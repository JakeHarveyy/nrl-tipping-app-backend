# app/__init__.py
import os
from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_restful import Api  # <<< Import Api
from flask_cors import CORS
from flask_bcrypt import Bcrypt # Import Bcrypt
from flask_jwt_extended import JWTManager # Import JWTManager
from flask_apscheduler import APScheduler
from authlib.integrations.flask_client import OAuth # Import OAuth
from datetime import datetime, timezone # Need datetime/timezone
from app.config import config_by_name
from apscheduler.jobstores.base import JobLookupError
import random
from datetime import datetime, timezone, timedelta

import logging # Use logging
log = logging.getLogger(__name__)

db = SQLAlchemy()
migrate = Migrate()
api_restful = Api() # <<< Instantiate Api HERE, outside the factory
bcrypt = Bcrypt() # Instantiate Bcrypt
jwt = JWTManager() # Instantiate JWTManager
oauth = OAuth() # Instantiate OAuth
scheduler = APScheduler()

def check_and_process_rounds_job():
    """
    Checks for rounds that have started but are not yet 'Active'
    and triggers the bankroll update process for them.
    Also handles transitioning 'Active' rounds to 'Completed'.
    """
    app = scheduler.app # Get the app instance
    with app.app_context():
        print(f"--- Running Round Check Job at {datetime.now(timezone.utc)} ---")
        from app.models import Round # Import models inside context
        from app.services.round_service import process_round_start
        from app import db

        now = datetime.now(timezone.utc)

        # --- Process Rounds Starting Now ---
        rounds_to_start = Round.query.filter(
            Round.status == 'Upcoming',
            Round.start_date <= now
        ).order_by(Round.start_date).all()

        if rounds_to_start:
            print(f"Found {len(rounds_to_start)} round(s) to start processing.")
            for round_obj in rounds_to_start:
                print(f"Processing start for Round {round_obj.round_number} (Year: {round_obj.year})")
                # Call the service function to handle bankroll updates etc.
                processing_success = process_round_start(round_obj)

                if processing_success:
                     # Update round status ONLY if processing was generally successful
                     round_obj.status = 'Active'
                     db.session.add(round_obj)
                     print(f"Updated Round {round_obj.round_number} status to Active.")
                else:
                     print(f"WARNING: Processing failed for Round {round_obj.round_number}. Status not updated.")
                # Commit status change separately or within process_round_start
                try:
                    db.session.commit()
                except Exception as e:
                     db.session.rollback()
                     print(f"Error committing status change for Round {round_obj.round_number}: {e}")
        else:
             print("No upcoming rounds found that need to start.")


        # --- Process Rounds Finishing Now (Optional - could be separate job) ---
        rounds_to_complete = Round.query.filter(
             Round.status == 'Active',
             Round.end_date <= now
        ).order_by(Round.end_date).all()

        if rounds_to_complete:
             print(f"Found {len(rounds_to_complete)} round(s) to complete.")
             for round_obj in rounds_to_complete:
                 # Basic status update. Settlement should be triggered by match results, not round end.
                 round_obj.status = 'Completed'
                 db.session.add(round_obj)
                 print(f"Updated Round {round_obj.round_number} status to Completed.")
             try:
                 db.session.commit()
             except Exception as e:
                 db.session.rollback()
                 print(f"Error committing status change for completed rounds: {e}")
        else:
             print("No active rounds found that need to be completed.")


        print("--- Round Check Job Finished ---")

# --- High-Frequency Job for a Single Match ---
def scrape_specific_match_result_job(match_id_to_scrape):
    app = scheduler.app # Get app instance from scheduler
    with app.app_context():
        job_log_prefix = f"[Scrape Job MatchID:{match_id_to_scrape}]" # For clearer logs
        log.info(f"{job_log_prefix} Running.")
        from app.models import Match # Import inside context
        from app import db
        # Import service and settlement functions here too
        from app.services.results_scraper_service import fetch_match_result
        from app.api.settlement import settle_bets_for_match

        match = Match.query.get(match_id_to_scrape)
        if not match:
            log.warning(f"{job_log_prefix} Match not found in DB. Removing job.")
            try: scheduler.remove_job(f'scrape_match_{match_id_to_scrape}')
            except JobLookupError: pass
            return

        if match.status == 'Completed':
            log.info(f"{job_log_prefix} Match already completed. Removing job.")
            try: scheduler.remove_job(f'scrape_match_{match_id_to_scrape}')
            except JobLookupError: pass
            return

        try:
            # Prepare identifier dict for the service function
            match_identifier_details = {
                'round_number': match.round.round_number, # Access via relationship
                'year': match.round.year,
                'home_team': match.home_team,
                'away_team': match.away_team,
                'start_time': match.start_time,
                # Add external_id if you plan to store and use it for matching
                # 'external_id': match.external_match_id
            }

            # Call the results scraper service function
            status, home_score, away_score = fetch_match_result(match_identifier_details)
            log.info(f"{job_log_prefix} Scraped Status='{status}', Score={home_score}-{away_score}")

            if status == 'Error':
                log.error(f"{job_log_prefix} Scraper service returned error. Job will retry.")
                return # Let the scheduler retry later

            original_db_status = match.status # Store original status

            # Update DB status if changed and relevant (Live, Postponed, Cancelled)
            if status in ['Live', 'Postponed', 'Cancelled'] and match.status != status:
                 log.info(f"{job_log_prefix} DB Status changing from '{match.status}' to '{status}'.")
                 match.status = status
                 db.session.add(match)
                 db.session.commit() # Commit status change promptly

            # Check if finished and trigger settlement if needed
            if status == 'Finished' and original_db_status != 'Completed':
                log.info(f"{job_log_prefix} Match finished! Attempting settlement...")
                if home_score is not None and away_score is not None:
                     # Call settlement logic (settle_bets_for_match also updates match status/scores)
                     success, msg = settle_bets_for_match(match.match_id, home_score, away_score)
                     if success:
                          log.info(f"{job_log_prefix} Settlement successful. Removing job.")
                          try: scheduler.remove_job(f'scrape_match_{match_id_to_scrape}')
                          except JobLookupError: log.warning(f"{job_log_prefix} Job removal failed (already removed?).")
                     else:
                          log.error(f"{job_log_prefix} Settlement FAILED: {msg}. Job will retry.")
                else:
                     log.warning(f"{job_log_prefix} Match 'Finished' but scores invalid ({home_score}-{away_score}). Settlement skipped, job will retry.")

            # Handle removal for other terminal states if status was just updated
            if status in ['Postponed', 'Cancelled'] and original_db_status != status:
                  log.info(f"{job_log_prefix} Match is {status}. Removing job.")
                  try: scheduler.remove_job(f'scrape_match_{match_id_to_scrape}')
                  except JobLookupError: log.warning(f"{job_log_prefix} Job removal failed (already removed?).")

        except Exception as e:
            log.error(f"ERROR in {job_log_prefix}: {e}", exc_info=True)
            db.session.rollback()

# --- Primary Job to Check for Live Matches ---
def check_for_live_matches_job():
    app = scheduler.app
    with app.app_context():
        log.info(f"--- Running Live Match Check Job at {datetime.now(timezone.utc)} ---")
        from app.models import Match # Import inside context

        now = datetime.now(timezone.utc)

        # Define the window more carefully
        # Start checking slightly before kickoff, check for a few hours after
        start_window = now - timedelta(hours=3) # Check games started up to 3 hours ago
        end_window = now + timedelta(minutes=10) # Check games starting in the next 10 mins
        print(f"Current UTC time (now): {now}")
        print(f"Querying for matches between {start_window} and {end_window}")

        potential_live_matches = Match.query.filter(
            Match.start_time >= start_window,
            Match.start_time <= end_window,
            Match.status.notin_(['Completed', 'Cancelled', 'Postponed']) # Ignore terminal states
        ).all()

        log.info(f"Found {len(potential_live_matches)} potentially live/upcoming matches to check/schedule.")

        for match in potential_live_matches:
            job_id = f'scrape_match_{match.match_id}'
            try:
                existing_job = scheduler.get_job(job_id)
                if not existing_job:
                    log.info(f"  Scheduling high-frequency scrape job for Match ID: {match.match_id} ({match.home_team} vs {match.away_team})")
                    scheduler.add_job(
                        id=job_id,
                        func=scrape_specific_match_result_job,
                        args=[match.match_id],
                        trigger='interval',
                        minutes=1, # High frequency
                        next_run_time=datetime.now(timezone.utc) + timedelta(seconds=random.randint(3,10)) # Stagger start slightly
                    )
                # else: Job already exists, let it run its course
            except Exception as e_sched:
                 log.error(f"Error scheduling job for match {match.match_id}: {e_sched}", exc_info=True)

        log.info("--- Live Match Check Job Finished ---")




def create_app(config_name=None):
    if config_name is None:
        config_name = os.getenv('FLASK_ENV', 'development')

    app = Flask(__name__)
    app.config.from_object(config_by_name[config_name])
    print(f"--- Using database URI: {app.config.get('SQLALCHEMY_DATABASE_URI')} ---")

    # --- Initialize extensions with the app object ---
    db.init_app(app)
    migrate.init_app(app, db)
    bcrypt.init_app(app)
    jwt.init_app(app)
    oauth.init_app(app)
    scheduler.init_app(app) # Initialize scheduler first
    scheduler.start()
    CORS(app, resources={r"/api/*": {"origins": ["http://localhost:5173", "http://127.0.0.1:5173"]}})

    # Register Google OAuth client with Authlib
    oauth.register(
        name='google',
        client_id=app.config.get('GOOGLE_CLIENT_ID'),
        client_secret=app.config.get('GOOGLE_CLIENT_SECRET'),
        server_metadata_url='https://accounts.google.com/.well-known/openid-configuration', # Discovery URL
        client_kwargs={
            'scope': 'openid email profile' # Scopes determine what info you ask for
        }
    )

    api = Api(app)
    from app.api.routes import initialize_routes
    initialize_routes(api) # Add all your API resources
    app.logger.info("--- Flask-RESTful API Routes Initialized ---")

    # --- Schedule Jobs ---
    
    # Round Management Job
    job_id_rounds = 'round_management_job'
    if not scheduler.get_job(job_id_rounds):
            print(f"Scheduling job '{job_id_rounds}' (Bankroll Bonus).")
            scheduler.add_job(
                id=job_id_rounds, func=check_and_process_rounds_job,
                trigger='interval', minutes=1 # Or your desired interval
            )
    else:
            print(f"Job '{job_id_rounds}' already scheduled.")

    # --- Schedule Odds Scraper Job ---
    from app.services.odds_scraper_service import update_matches_from_odds_scraper

    odds_job_id = 'odds_update_job'
    if app.config.get("ENV") != "testing":
        if not scheduler.get_job(odds_job_id):
            print(f"Scheduling job '{odds_job_id}'.")
            scheduler.add_job(
                id=odds_job_id,
                func=lambda: app.app_context().push() or update_matches_from_odds_scraper(),
                trigger='interval', 
                minutes=1 # Or your desired interval
            )
        else:
            print(f"Job '{odds_job_id}' already scheduled.")

    # --- Schedule live match Check Job ---
    primary_job_id = 'live_match_check_job'
    if app.config.get("ENV") != "testing":
        if not scheduler.get_job(primary_job_id):
                print(f"Scheduling job '{primary_job_id}'.")
                scheduler.add_job(
                    id=primary_job_id, 
                    func=check_for_live_matches_job,
                    trigger='interval',
                    minutes=2 # Or your desired interval
                )
        else:
                print(f"Job '{primary_job_id}' already scheduled.")

    # Start the scheduler AFTER all jobs have been added
    if not scheduler.running:
        try:
            scheduler.start()
            app.logger.info("Scheduler started successfully.")
        except Exception as e:
            app.logger.error(f"Failed to start scheduler: {e}", exc_info=True)
    # --- End Job Scheduling ---

    if app.debug or os.environ.get("FLASK_ENV") == "development":
        app.logger.info("--- Final Registered Routes (app.url_map) ---")
        for rule in app.url_map.iter_rules():
            app.logger.info(f"Endpoint: {rule.endpoint}, Methods: {list(rule.methods)}, Path: {rule.rule}")
        app.logger.info("----------------------------------------------------------")

    app.logger.info("--- App Creation Complete ---")
    return app