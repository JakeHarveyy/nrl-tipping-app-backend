# run.py
import os
from datetime import datetime, timedelta, timezone # <--- Ensure these are imported
from decimal import Decimal # <--- Ensure this is imported
from app import create_app, db # <--- Ensure db is imported
from app.models import Round, Match # <--- Ensure models are imported

# Get config name from environment variable or default to 'dev'
config_name = os.getenv('FLASK_ENV', 'development') # Use 'development' as key now
app = create_app(config_name)

# --- Add Seed Command ---
@app.cli.command("seed_db")
def seed_db():
    """Seeds the database with initial sample data."""
    print("Seeding database...")

    # --- Clean up existing data (optional, careful in production!) ---
    # Uncomment these lines if you want to clear data before seeding during development
    # try:
    #     num_matches = db.session.query(Match).delete()
    #     num_rounds = db.session.query(Round).delete()
    #     db.session.commit()
    #     print(f"Cleared {num_matches} matches and {num_rounds} rounds.")
    # except Exception as e:
    #     db.session.rollback()
    #     print(f"Error clearing data: {e}")
    #     return # Stop if clearing failed


    try:
        # --- Create Sample Rounds (Check if they exist first) ---
        round1 = db.session.query(Round).filter_by(round_number=1, year=2024).first()
        if not round1:
            round1 = Round(
                round_number=1,
                year=2024,
                # Example Dates - adjust as needed
                start_date=datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1),
                end_date=datetime.now(timezone.utc).replace(hour=23, minute=59, second=59, microsecond=0) + timedelta(days=4),
                status='Active' # Or 'Upcoming'
            )
            db.session.add(round1)
            print("Adding Round 1 (2024)")
        else:
            print("Round 1 (2024) already exists.")

        round2 = db.session.query(Round).filter_by(round_number=2, year=2024).first()
        if not round2:
            round2 = Round(
                round_number=2,
                year=2024,
                # Example Dates
                start_date=datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=8),
                end_date=datetime.now(timezone.utc).replace(hour=23, minute=59, second=59, microsecond=0) + timedelta(days=11),
                status='Upcoming'
            )
            db.session.add(round2)
            print("Adding Round 2 (2024)")
        else:
            print("Round 2 (2024) already exists.")

        # Commit rounds to get their IDs before adding matches
        db.session.commit()
        print("Rounds committed/checked.")

        # --- Create Sample Matches for Round 1 (Check if they exist first) ---
        if round1: # Re-fetch round1 in case it existed before to ensure we have the ID
            round1 = db.session.query(Round).filter_by(round_number=1, year=2024).first()
            if round1:
                match1_r1 = db.session.query(Match).filter_by(home_team='Broncos', away_team='Cowboys', round_id=round1.round_id).first()
                if not match1_r1:
                    match1_r1 = Match(
                        round_id=round1.round_id,
                        home_team='Broncos',
                        away_team='Cowboys',
                        start_time=round1.start_date + timedelta(hours=19, minutes=50), # Example time
                        home_odds=Decimal('1.90'),
                        away_odds=Decimal('1.90'),
                        status='Scheduled'
                    )
                    db.session.add(match1_r1)
                    print("Adding Match: Broncos vs Cowboys (R1)")

                match2_r1 = db.session.query(Match).filter_by(home_team='Roosters', away_team='Rabbitohs', round_id=round1.round_id).first()
                if not match2_r1:
                    match2_r1 = Match(
                        round_id=round1.round_id,
                        home_team='Roosters',
                        away_team='Rabbitohs',
                        start_time=round1.start_date + timedelta(days=1, hours=20, minutes=5), # Example time
                        home_odds=Decimal('1.75'),
                        away_odds=Decimal('2.10'),
                        status='Scheduled'
                    )
                    db.session.add(match2_r1)
                    print("Adding Match: Roosters vs Rabbitohs (R1)")
            else:
                 print("Error: Could not find Round 1 after commit to add matches.")


        # --- Create Sample Matches for Round 2 (Check if they exist first) ---
        if round2: # Re-fetch round2
            round2 = db.session.query(Round).filter_by(round_number=2, year=2024).first()
            if round2:
                match1_r2 = db.session.query(Match).filter_by(home_team='Storm', away_team='Panthers', round_id=round2.round_id).first()
                if not match1_r2:
                    match1_r2 = Match(
                        round_id=round2.round_id,
                        home_team='Storm',
                        away_team='Panthers',
                        start_time=round2.start_date + timedelta(hours=19, minutes=50), # Example time
                        home_odds=Decimal('2.00'),
                        away_odds=Decimal('1.80'),
                        status='Scheduled'
                    )
                    db.session.add(match1_r2)
                    print("Adding Match: Storm vs Panthers (R2)")
            else:
                 print("Error: Could not find Round 2 after commit to add matches.")


        # --- Final Commit ---
        db.session.commit()
        print("Database seeding completed!")

    except Exception as e:
        db.session.rollback() # Rollback in case of any error during the process
        print(f"Error seeding database: {e}")
        import traceback
        traceback.print_exc() # Print detailed traceback


# --- Main execution ---
if __name__ == '__main__':
    # Note: app.run() is generally used for development server.
    # For production, use a WSGI server like Gunicorn or Waitress.
    # The Flask CLI commands like 'flask seed_db' work independently.
    app.run(host='0.0.0.0', port=5000) # Or your preferred host/port