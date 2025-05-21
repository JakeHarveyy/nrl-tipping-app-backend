# run.py
import os
from datetime import datetime, timedelta, timezone # <--- Ensure these are imported
from decimal import Decimal # <--- Ensure this is imported
from app import create_app, db # <--- Ensure db is imported
from app.models import Round, Match # <--- Ensure models are imported
from app.services.results_scraper_service import populate_schedule_from_nrl_com
import click


# Get config name from environment variable or default to 'dev'
config_name = os.getenv('FLASK_ENV', 'development') # Use 'development' as key now
app = create_app(config_name)

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


# --- Main execution ---
if __name__ == '__main__':
    # Note: app.run() is generally used for development server.
    # For production, use a WSGI server like Gunicorn or Waitress.
    # The Flask CLI commands like 'flask seed_db' work independently.
    app.run(host='0.0.0.0', port=5000) # Or your preferred host/port