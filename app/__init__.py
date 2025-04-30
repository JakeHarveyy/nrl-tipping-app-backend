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

from app.config import config_by_name

db = SQLAlchemy()
migrate = Migrate()
api_restful = Api() # <<< Instantiate Api HERE, outside the factory
bcrypt = Bcrypt() # Instantiate Bcrypt
jwt = JWTManager() # Instantiate JWTManager
oauth = OAuth() # Instantiate OAuth
scheduler = APScheduler()

def weekly_bankroll_update_job():
    """Adds $1000 to every active user's bankroll."""
    # This job runs outside the normal request context, so we need app context
    app = scheduler.app # Get the app instance the scheduler is bound to
    with app.app_context():
        print("--- Running Weekly Bankroll Update Job ---")
        from app.models import User, BankrollHistory, Round # Import models inside context
        from app import db
        from decimal import Decimal
        from datetime import datetime, timezone

        # TODO: Determine the current/starting round number logic more accurately later
        # For now, let's just assume we need a placeholder round number or skip it.
        # Maybe find the latest 'Active' or 'Upcoming' round?
        current_round_number = 1 # Placeholder - Needs real logic

        users = User.query.filter_by(active=True).all()
        print(f"Found {len(users)} active users to update.")
        added_amount = Decimal('1000.00')

        for user in users:
            try:
                previous_balance = user.bankroll
                new_balance = previous_balance + added_amount

                # Update user's bankroll
                user.bankroll = new_balance

                # Create history entry
                history_entry = BankrollHistory(
                    user_id=user.user_id,
                    round_number=current_round_number, # Use the determined round number
                    change_type='Weekly Addition',
                    related_bet_id=None,
                    amount_change=added_amount,
                    previous_balance=previous_balance,
                    new_balance=new_balance,
                    timestamp=datetime.now(timezone.utc)
                )
                db.session.add(history_entry)
                # Commit after each user OR commit all at the end (safer to commit per user)
                db.session.commit()
                print(f"Updated bankroll for user {user.username} (ID: {user.user_id}). New balance: {new_balance}")

            except Exception as e:
                db.session.rollback()
                print(f"ERROR updating bankroll for user {user.username} (ID: {user.user_id}): {e}")

        print("--- Weekly Bankroll Update Job Finished ---")

def create_app(config_name=None):
    """Application Factory Pattern"""
    if config_name is None:
        config_name = os.getenv('FLASK_ENV', 'development')

    app = Flask(__name__)
    app.config.from_object(config_by_name[config_name])

    print(f"--- Using database URI: {app.config.get('SQLALCHEMY_DATABASE_URI')} ---") # Keep this for debugging

    # Initialize extensions that need the app object directly
    db.init_app(app)
    migrate.init_app(app, db)
    bcrypt.init_app(app) # Initialize Bcrypt with app
    jwt.init_app(app) # Initialize JWTManager with app
    oauth.init_app(app) # Initialize OAuth with app
    scheduler.init_app(app)
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

    job_id = 'weekly_bankroll_update'
    if not scheduler.get_job(job_id):
         scheduler.add_job(
             id=job_id,
             func=weekly_bankroll_update_job,
             #trigger='cron', # Use 'interval' for testing, 'cron' for production schedule
             # day_of_week='mon', # Example: Run every Monday
             # hour=0,
             # minute=5
             # For testing every 2 minutes:
             trigger='interval',
             minutes=2
         )
         print(f"Scheduled job '{job_id}'")
    else:
         print(f"Job '{job_id}' already scheduled.")

    # Import models AFTER db is initialized IF they rely on db instance directly at import time
    # (Generally safer to import them here or inside routes where needed)
    from app import models

    # --- Initialise API Routes ---
    # Import the function that initializes routes
    from app.api.routes import initialize_routes
    api_restful.init_app(app)
    initialize_routes(api_restful)
    print("--- API Routes Initialized ---")
    # ---------------------

    # Example basic route (can be removed later)
    @app.route('/health')
    def health_check():
        # Check DB connection maybe?
        try:
             db.session.execute(db.text('SELECT 1'))
             return "OK", 200
        except Exception as e:
             print(f"Health check DB connection failed: {e}")
             return "DB Error", 500
    
        from app.api.routes import initialize_routes
    api_restful.init_app(app)
    initialize_routes(api_restful)

    # --- Add Temporary Test Route ---
    @app.route('/api/test')
    def api_test_route():
        return "Flask test route OK", 200
    
    print("--- Registered Routes ---")
    for rule in app.url_map.iter_rules():
        print(f"Endpoint: {rule.endpoint}, Methods: {list(rule.methods)}, Path: {rule.rule}")
    print("-----------------------")

    return app