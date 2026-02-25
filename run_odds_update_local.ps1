# run_odds_update_local.ps1
#
# Fetches NRL.com odds from your local Australian IP and updates the production database.
# Run manually or schedule with Windows Task Scheduler (recommended: every 3 hours).
#
# SETUP (first time only):
#   1. Make sure Heroku CLI is installed and you're logged in (heroku auth:whoami)
#   2. Make sure the venv is built: python -m venv venv
#
# SCHEDULING with Task Scheduler:
#   - Program:   powershell.exe
#   - Arguments: -ExecutionPolicy Bypass -File "C:\Users\61429\PROJECTS\PERSONAL\Nrl_Tipping\nrl-tipping-app-backend\run_odds_update_local.ps1"
#   - Trigger:   Daily, repeat every 3 hours

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

Write-Host "=== NRL Odds Update (Local) ===" -ForegroundColor Cyan
Write-Host "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"

# --- Fetch production credentials from Heroku at runtime (nothing stored in plain text) ---
Write-Host "Fetching production config from Heroku..."
$env:DATABASE_URL    = (heroku config:get DATABASE_URL    --app nrl-tipping-app)
$env:SECRET_KEY      = (heroku config:get SECRET_KEY      --app nrl-tipping-app)
$env:JWT_SECRET_KEY  = (heroku config:get JWT_SECRET_KEY  --app nrl-tipping-app)
$env:FRONTEND_URL    = (heroku config:get FRONTEND_URL    --app nrl-tipping-app)
$env:FLASK_ENV       = "production"

if (-not $env:DATABASE_URL) {
    Write-Host "ERROR: Could not fetch DATABASE_URL from Heroku. Are you logged in?" -ForegroundColor Red
    exit 1
}

# Rewrite the DB URL to use pg8000 (pure Python driver — no C extension required on Windows)
$env:DATABASE_URL = $env:DATABASE_URL -replace "^postgres(ql)?://", "postgresql+pg8000://"
Write-Host "Using driver: pg8000"

# --- Activate virtual environment ---
$VenvActivate = Join-Path $ScriptDir "venv\Scripts\Activate.ps1"
if (-not (Test-Path $VenvActivate)) {
    Write-Host "ERROR: venv not found at $VenvActivate. Run: python -m venv venv && pip install -r requirements.txt" -ForegroundColor Red
    exit 1
}
. $VenvActivate

# --- Run the odds update ---
Write-Host "Running flask run-odds-update..." -ForegroundColor Green
Set-Location $ScriptDir
$Python = Join-Path $ScriptDir "venv\Scripts\python.exe"
& $Python -m flask run-odds-update

Write-Host "=== Done ===" -ForegroundColor Cyan
