# run.py
import os
from datetime import datetime, timedelta, timezone # <--- Ensure these are imported
from decimal import Decimal # <--- Ensure this is imported
from app import create_app, db # <--- Ensure db is imported
from app.models import Round, Match, Bet, BankrollHistory, AIPrediction # <--- Ensure models are imported
from app.services.results_scraper_service import populate_schedule_from_nrl_com
import click
from app.models import User


# Get config name from environment variable or default to 'dev'
config_name = os.getenv('FLASK_ENV', 'development') # Use 'development' as key now
app = create_app(config_name)

@app.cli.command("reset-db")
def reset_db():
    """Deletes all data from every table while keeping the schema intact."""
    with app.app_context():
        print("WARNING: This will permanently delete all data. Starting reset...")
        try:
            # Delete in FK-safe order (children first, parents last)
            deleted = {}
            deleted['ai_predictions']    = db.session.query(AIPrediction).delete()
            deleted['bankroll_history']  = db.session.query(BankrollHistory).delete()
            deleted['bets']              = db.session.query(Bet).delete()
            deleted['matches']           = db.session.query(Match).delete()
            deleted['rounds']            = db.session.query(Round).delete()
            deleted['users']             = db.session.query(User).delete()
            db.session.commit()
            for table, count in deleted.items():
                print(f"  Deleted {count} rows from {table}")
            print("Database reset complete. Schema preserved.")
        except Exception as e:
            db.session.rollback()
            print(f"ERROR during reset: {e}")
            import traceback
            traceback.print_exc()


@app.cli.command("populate_schedule")
@click.option('--start_round', default=1, type=int, help="First round to fetch.")
@click.option('--end_round', default=27, type=int, help="Last round to fetch.") # Assume 27 rounds
@click.option('--year', default=datetime.now().year, type=int, help="Season year.")
def populate_schedule(start_round, end_round, year):
    """
    Populates Rounds and Matches from NRL.com data.
    """
    print(f"--- Starting Full Schedule Population from NRL.com (Rounds {start_round}-{end_round}, Year {year}) ---")
    with app.app_context():
        try:
            populate_schedule_from_nrl_com(start_round, end_round, year) # <<< CALL NEW FUNCTION
            print("--- Finished Full Schedule Population ---")
        except Exception as e:
            print(f"ERROR during schedule population: {e}")
            import traceback
            traceback.print_exc()

@app.cli.command("create-bot")
@click.argument('username', default='LogisticsRegressionBot')
def create_bot(username):
    """Creates the AI Bot user if it doesn't exist."""
    with app.app_context():
        if User.find_by_username(username):
            print(f"Bot user '{username}' already exists.")
            return

        print(f"Creating AI Bot user: '{username}'...")
        bot_user = User(
            username=username,
            email=f"{username.lower()}@bot.local", # Use a fake local email
            is_bot=True, # Set the bot flag
            # Bots don't need passwords or Google IDs
            password_hash=None,
            is_email_verified=True, # Assume verified
            bankroll=1000.00 # Set initial bankroll
        )
        try:
            bot_user.save_to_db()
            # You might want to log the initial deposit in BankrollHistory here too
            # (similar to the UserRegister logic)
            print(f"Bot user '{username}' created successfully with ID {bot_user.user_id}.")
        except Exception as e:
            print(f"Error creating bot user: {e}")

@app.cli.command("run-ai-predictions")
@click.option('--round_number', default=None, type=int, help="Specific round number to predict.")
@click.option('--year', default=datetime.now().year, type=int, help="Season year.")
def run_ai_predictions(round_number, year):
    """Manually run AI predictions for a specific round or current round."""
    with app.app_context():
        try:
            from app.models import Round
            from app.services.ai_prediction_service import run_ai_predictions_for_round
            from datetime import datetime, timezone
            
            if round_number:
                # Run for specific round
                print(f"--- Running AI Predictions for Round {round_number}, Year {year} ---")
                success = run_ai_predictions_for_round(round_number, year)
                
                if success:
                    print(f"✅ AI predictions completed successfully for Round {round_number}")
                else:
                    print(f"❌ AI predictions failed for Round {round_number}")
            else:
                # Find current round automatically
                print("--- Finding current round for AI predictions ---")
                now = datetime.now(timezone.utc)
                
                # First try to find an active round
                current_round = Round.query.filter(
                    Round.status == 'Active',
                    Round.year == year
                ).first()
                
                # If no active round, find the next upcoming round
                if not current_round:
                    current_round = Round.query.filter(
                        Round.status == 'Upcoming',
                        Round.start_date >= now,
                        Round.year == year
                    ).order_by(Round.start_date).first()
                
                if current_round:
                    print(f"--- Running AI Predictions for Round {current_round.round_number}, Year {current_round.year} ---")
                    success = run_ai_predictions_for_round(current_round.round_number, current_round.year)
                    
                    if success:
                        print(f"✅ AI predictions completed successfully for Round {current_round.round_number}")
                        # Force commit to ensure data is saved
                        db.session.commit()
                    else:
                        print(f"❌ AI predictions failed for Round {current_round.round_number}")
                else:
                    print("⚠️  No suitable round found for AI predictions")
                    
        except Exception as e:
            print(f"❌ Error running AI predictions: {e}")
            import traceback
            traceback.print_exc()
            db.session.rollback()

# --- Main execution ---
if __name__ == '__main__':
    # Note: app.run() is generally used for development server.
    # For production, use a WSGI server like Gunicorn or Waitress.
    # The Flask CLI commands like 'flask seed_db' work independently.
    app.run(host='0.0.0.0', port=5000) # Or your preferred host/port