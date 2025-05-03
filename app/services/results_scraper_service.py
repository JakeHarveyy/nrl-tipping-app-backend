# app/services/results_scraper_service.py
import requests
import json
from bs4 import BeautifulSoup
from datetime import datetime, timezone, timedelta
import logging

# Configure basic logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - [%(name)s] %(message)s')
log = logging.getLogger(__name__) # Create a logger specific to this module

def _fetch_nrl_round_data_from_web(round_num, year, competition='111'):
    """Fetches and parses fixture data for a specific round from NRL.com."""
    url = f"https://www.nrl.com/draw/?competition={competition}&round={round_num}&season={year}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
    }
    log.info(f"Fetching fixture data from: {url}")
    try:
        response = requests.get(url, headers=headers, timeout=20)
        response.raise_for_status() # Check for HTTP errors

        soup = BeautifulSoup(response.text, "html.parser")
        script_tag = soup.find("div", {"id": "vue-draw"})

        if not script_tag:
            log.error(f"Could not find fixture data container ('vue-draw' div) on page for Round {round_num}, Year {year}.")
            return None

        raw_json = script_tag.get("q-data")
        if not raw_json:
            log.error(f"Could not find 'q-data' attribute in 'vue-draw' div for Round {round_num}, Year {year}.")
            return None

        data = json.loads(raw_json)
        fixtures = data.get("fixtures", []) # Get the list of fixtures/matches
        log.info(f"Successfully fetched and parsed {len(fixtures)} fixture items for Round {round_num}, Year {year}.")
        return fixtures # Return the raw list of fixtures

    except requests.exceptions.RequestException as e:
        log.error(f"HTTP Error fetching NRL fixture data for Round {round_num}, Year {year}: {e}")
        return None
    except json.JSONDecodeError as e:
        log.error(f"JSON Decode Error parsing fixture data for Round {round_num}, Year {year}: {e}")
        return None
    except Exception as e:
        log.error(f"Unexpected error fetching NRL fixture data for Round {round_num}, Year {year}: {e}", exc_info=True) # Log traceback
        return None
    

# test when nrl match live to find values for match/state 
def parse_match_status(match_mode, match_state):
    """Translates NRL API status fields to our application's status."""
    mode = str(match_mode).lower() if match_mode is not None else ''
    state = str(match_state).lower() if match_state is not None else ''

    if mode == 'post' or state == 'fulltime':
        return 'Finished'
    elif mode == 'live' or state == 'live':
        return 'Live'
    elif mode == 'pre' and state == 'upcoming':
        return 'Scheduled' # Matches our default status
    elif state == 'postponed':
        return 'Postponed'
    elif state == 'cancelled' or state == 'abandoned':
        return 'Cancelled'
    else:
        log.warning(f"Unmapped match status - Mode='{match_mode}', State='{match_state}'")
        return 'Unknown'
    

    
def fetch_match_result(match_identifier_details: dict):
    """
    Fetches data for the round the match is in, finds the specific match,
    and returns its status and score.

    Args:
        match_identifier_details (dict): Dictionary containing details from the DB match object,
                                         e.g., {'home_team': 'Sharks', 'away_team': 'Eels',
                                                'round_number': 9, 'year': 2025,
                                                'start_time': datetime_object}.

    Returns:
        tuple: (status_string, home_score_int, away_score_int) or ('Error', None, None)
    """
    db_round = match_identifier_details.get('round_number')
    db_year = match_identifier_details.get('year')
    db_home_team = match_identifier_details.get('home_team')
    db_away_team = match_identifier_details.get('away_team')
    db_start_time = match_identifier_details.get('start_time') # Timezone-aware datetime

    if not all([db_round, db_year, db_home_team, db_away_team, db_start_time]):
        log.error(f"fetch_match_result: Missing required details in identifier: {match_identifier_details}")
        return 'Error', None, None

    log.info(f"Fetching result data for R{db_round}/{db_year}: {db_home_team} vs {db_away_team}")
    round_fixture_data = _fetch_nrl_round_data_from_web(round_num=db_round, year=db_year)

    if round_fixture_data is None:
        log.warning(f"Failed to fetch fixture data for Round {db_round}, Year {db_year}.")
        return 'Error', None, None # Indicate fetch failure

    found_match_data = None
    for fixture in round_fixture_data:
        if fixture.get("type") != "Match":
            continue

        # Extract details safely using .get()
        scraped_home = fixture.get('homeTeam', {}).get('nickName', '').strip()
        scraped_away = fixture.get('awayTeam', {}).get('nickName', '').strip()
        scraped_kickoff_str = fixture.get('clock', {}).get('kickOffTimeLong')

        # --- Matching Logic ---
        # Compare case-insensitively for robustness
        if db_home_team.lower() == scraped_home.lower() and db_away_team.lower() == scraped_away.lower():
            # Check start time proximity
            if scraped_kickoff_str:
                try:
                    scraped_start_time = datetime.fromisoformat(scraped_kickoff_str.replace('Z', '+00:00'))
                    time_diff = abs(db_start_time - scraped_start_time)
                    if time_diff < timedelta(hours=12): # Allow generous window
                        found_match_data = fixture
                        log.info(f"Found matching fixture for {db_home_team} vs {db_away_team} based on teams and time.")
                        break
                    else:
                        log.warning(f"Team names match for {db_home_team} vs {db_away_team}, but kickoff times differ significantly ({db_start_time} vs {scraped_start_time}).")
                except ValueError:
                    log.warning(f"Could not parse scraped kickoff time '{scraped_kickoff_str}' for comparison. Matching based on teams only.")
                    found_match_data = fixture # Fallback to team match if time parse fails
                    break
            else:
                log.warning(f"No kickoff time in scraped data for {scraped_home} vs {scraped_away}. Matching based on teams only.")
                found_match_data = fixture
                break
        # --- End Matching Logic ---

    if not found_match_data:
        log.warning(f"Could not find matching fixture data in fetched list for R{db_round}: {db_home_team} vs {db_away_team}")
        # Could be the data isn't live yet, or matching failed. Return 'Unknown' not 'Error'.
        return 'Unknown', None, None

    # --- Extract results from the matched fixture ---
    status = parse_match_status(
        found_match_data.get('matchMode'),
        found_match_data.get('matchState')
    )
    home_score_raw = found_match_data.get('homeTeam', {}).get('score')
    away_score_raw = found_match_data.get('awayTeam', {}).get('score')

    home_score, away_score = None, None
    try:
        home_score = int(home_score_raw) if home_score_raw is not None else None
    except (ValueError, TypeError):
        log.warning(f"Could not parse home score '{home_score_raw}' as integer.")
    try:
        away_score = int(away_score_raw) if away_score_raw is not None else None
    except (ValueError, TypeError):
        log.warning(f"Could not parse away score '{away_score_raw}' as integer.")

    if status == 'Finished' and (home_score is None or away_score is None):
        log.error(f"Match status is 'Finished' but scores are invalid for {db_home_team} vs {db_away_team} ({home_score}-{away_score}). Treating status as 'Unknown'.")
        return 'Unknown', None, None # Don't settle with bad scores

    log.info(f"Parsed result for {db_home_team} vs {db_away_team}: Status='{status}', Score={home_score}-{away_score}")
    return status, home_score, away_score