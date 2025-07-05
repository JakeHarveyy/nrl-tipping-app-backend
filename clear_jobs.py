from app import create_app
from app import scheduler

app = create_app()
with app.app_context():
    # List all jobs
    jobs = scheduler.get_jobs()
    print(f'Current jobs: {[job.id for job in jobs]}')
    
    # Remove the AI prediction job if it exists
    try:
        scheduler.remove_job('ai_prediction_job')
        print('Removed ai_prediction_job')
    except Exception as e:
        print(f'No ai_prediction_job to remove: {e}')
    
    # List jobs again
    jobs = scheduler.get_jobs()
    print(f'Jobs after cleanup: {[job.id for job in jobs]}')
