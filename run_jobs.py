# run_jobs.py
from app import create_app
from app.services.odds_scraper_service import update_matches_from_odds_scraper

# (You'll need to import check_for_live_matches_job logic here or call it)
# from app.services.round_service import check_and_process_rounds_job # (If you move it to a service)

# Import job functions from __init__.py or services
# Need to re-import the actual job functions here
from app import check_and_process_rounds_job, check_for_live_matches_job # Assuming they are importable

app = create_app()
app.app_context().push() # Push context for the script

def run_odds_update():
    print("Heroku Scheduler: Running odds update job...")
    update_matches_from_odds_scraper()
    print("Heroku Scheduler: Odds update job finished.")

def run_round_management():
    print("Heroku Scheduler: Running round management job...")
    check_and_process_rounds_job() # Make sure this function is accessible
    print("Heroku Scheduler: Round management job finished.")

def run_live_match_check():
    print("Heroku Scheduler: Running live match check job...")
    check_for_live_matches_job() # Make sure this function is accessible
    print("Heroku Scheduler: Live match check job finished.")

if __name__ == '__main__':
    import sys
    if len(sys.argv) > 1:
        job_name = sys.argv[1]
        if job_name == 'odds':
            run_odds_update()
        elif job_name == 'rounds':
            run_round_management()
        elif job_name == 'live_check':
            run_live_match_check()
        else:
            print(f"Unknown job: {job_name}")
    else:
        print("No job specified. Usage: python run_jobs.py <odds|rounds|live_check>")