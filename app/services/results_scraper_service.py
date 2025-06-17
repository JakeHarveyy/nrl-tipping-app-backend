# app/services/results_scraper_service.py
import requests
import json
from bs4 import BeautifulSoup
from datetime import datetime, timezone, timedelta
import logging
from app.models import Match, Round
from app import db
from app.utils.text_utils import normalize_team_name

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

        # --- TEMPORARY TEST OVERRIDE ---
        # Check if this is our specific test match (Wests Tigers vs Raiders in Round 16, 2025)
        # You might need to adjust based on the exact canonical names after normalization
        is_test_match = (db_home_team == "Wests Tigers" and db_away_team == "Raiders" and
                         db_round == 16 and db_year == 2025)

        if is_test_match:
            log.info(f"TESTING OVERRIDE: Forcing live data for {db_home_team} vs {db_away_team}")
            # Create a fake fixture entry that matches your DB's manually set start_time
            # and has the desired live status/scores.
            found_match_data = {
                "type": "Match",
                "matchMode": "Live", # Simulate LIVE
                "matchState": "Live",# Simulate LIVE
                "homeTeam": {"nickName": db_home_team, "score": 12}, # Simulate scores
                "awayTeam": {"nickName": db_away_team, "score": 0},
                "clock": {"kickOffTimeLong": db_start_time.isoformat().replace('+00:00', 'Z')} # Use DB start time
            }
            break # Found our forced match
        # --- END TEMPORARY TEST OVERRIDE ---

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

## populate db
def populate_schedule_from_nrl_com(start_round, end_round, year, competition='111'):
    """
    Populates Rounds and Matches from NRL.com data for a given range.
    If matches are in the past, it also attempts to populate their results.
    """
    log.info(f"--- Starting Schedule Population from NRL.com: Rounds {start_round}-{end_round}, Year {year} ---")
    rounds_created = 0
    matches_created = 0
    matches_updated = 0
    results_populated = 0 # New counter

    current_time_utc = datetime.now(timezone.utc) # Get current time once for comparison

    for round_num_to_fetch in range(start_round, end_round + 1):
        log.info(f"Processing Round {round_num_to_fetch} for Year {year} from NRL.com")
        # Fetch fixtures for the entire round ONCE
        round_fixtures_data = _fetch_nrl_round_data_from_web(round_num=round_num_to_fetch, year=year, competition=competition)

        if not round_fixtures_data:
            log.warning(f"No fixtures found for Round {round_num_to_fetch}, Year {year} on NRL.com. Skipping.")
            continue

        # --- Find or Create Round from NRL.com data ---
        actual_round_title = round_fixtures_data[0].get('roundTitle', '')
        try:
            parsed_round_number = int(actual_round_title.replace('Round ', ''))
            if parsed_round_number != round_num_to_fetch:
                 log.warning(f"Fetched round title '{actual_round_title}' does not match expected round_num {round_num_to_fetch}. Using parsed: {parsed_round_number}")
        except (ValueError, AttributeError):
            log.error(f"Could not parse round number from title '{actual_round_title}' for fetched Round {round_num_to_fetch}. Skipping this round's fixtures.")
            continue

        round_obj = Round.query.filter_by(round_number=parsed_round_number, year=year).first()
        round_match_kickoffs = []

        for fixture_item in round_fixtures_data: # Iterate over the already fetched round_fixtures_data
            if fixture_item.get("type") != "Match":
                continue
            kickoff_str = fixture_item.get('clock', {}).get('kickOffTimeLong')
            if kickoff_str:
                try:
                    kickoff_dt = datetime.fromisoformat(kickoff_str.replace('Z', '+00:00'))
                    round_match_kickoffs.append(kickoff_dt)
                except ValueError:
                    log.warning(f"Invalid kickoff time format '{kickoff_str}' for a match in Round {parsed_round_number}. Skipping for round date estimation.")

        if not round_match_kickoffs:
            log.warning(f"No valid kickoff times found for Round {parsed_round_number}, Year {year} to estimate round dates. Skipping round creation/update.")
            # continue # If you want to skip round creation. Or proceed if round might already exist.
        else: # Only try to create/update round if kickoffs were found
            min_kickoff = min(round_match_kickoffs)
            max_kickoff = max(round_match_kickoffs)

            if not round_obj:
                log.info(f"Creating Round {parsed_round_number} ({year}) from NRL.com data.")
                round_obj = Round(
                    round_number=parsed_round_number,
                    year=year,
                    start_date=min_kickoff.replace(hour=0, minute=0, second=0) - timedelta(days=3),
                    end_date=max_kickoff.replace(hour=23, minute=59, second=59) + timedelta(days=1),
                    status='Upcoming' # Default, will be updated by round management job
                )
                db.session.add(round_obj)
                try:
                    db.session.flush()
                    rounds_created += 1
                except Exception as e_flush:
                    log.error(f"Error flushing new Round {parsed_round_number} ({year}): {e_flush}", exc_info=True)
                    db.session.rollback()
                    continue # Skip this round's matches if round creation fails
            else:
                 log.info(f"Round {parsed_round_number} ({year}) already exists with ID {round_obj.round_id}.")
        
        if not round_obj: # If round creation failed or was skipped due to no kickoffs
            log.error(f"Cannot process matches for Round {parsed_round_number} as round_obj is not available.")
            continue


        # --- Process Matches for this Round from NRL.com ---
        for fixture in round_fixtures_data: # Iterate over the already fetched round_fixtures_data
            if fixture.get("type") != "Match":
                continue

            home_team_name = fixture.get('homeTeam', {}).get('nickName', '').strip()
            away_team_name = fixture.get('awayTeam', {}).get('nickName', '').strip()
            kickoff_str = fixture.get('clock', {}).get('kickOffTimeLong')

            if not all([home_team_name, away_team_name, kickoff_str]):
                log.warning(f"Skipping fixture due to missing team names or kickoff time: {fixture.get('matchCentreUrl', 'N/A')}")
                continue

            try:
                start_time_dt = datetime.fromisoformat(kickoff_str.replace('Z', '+00:00'))
            except ValueError:
                log.warning(f"Invalid kickoff time format '{kickoff_str}' for {home_team_name} vs {away_team_name}. Skipping match.")
                continue

            db_match = Match.query.filter_by(
                round_id=round_obj.round_id,
                home_team=home_team_name,
                away_team=away_team_name
            ).first()

            # Initial status from the main schedule scrape
            current_schedule_status = parse_match_status(fixture.get('matchMode'), fixture.get('matchState'))

            if not db_match:
                log.info(f"Creating new match from NRL.com: {home_team_name} vs {away_team_name} for Round {parsed_round_number}")
                db_match = Match(
                    round_id=round_obj.round_id,
                    home_team=home_team_name,
                    away_team=away_team_name,
                    start_time=start_time_dt,
                    status=current_schedule_status if current_schedule_status != 'Unknown' else 'Scheduled',
                )
                db.session.add(db_match)
                matches_created += 1
                try:
                    db.session.flush() # Ensure db_match.match_id is available if needed immediately
                except Exception as e_flush_match:
                    log.error(f"Error flushing new Match {home_team_name} vs {away_team_name}: {e_flush_match}", exc_info=True)
                    db.session.rollback() # Rollback this specific match add
                    continue # Skip to next fixture
            else:
                update_this_match = False
                if db_match.start_time != start_time_dt:
                    log.info(f"Updating start_time for {home_team_name} vs {away_team_name} from {db_match.start_time} to {start_time_dt}")
                    db_match.start_time = start_time_dt
                    update_this_match = True
                
                # Update status from schedule if it's a non-terminal, relevant update
                # and our DB status isn't already 'Completed'
                if current_schedule_status in ['Live', 'Postponed', 'Cancelled'] and \
                   db_match.status != current_schedule_status and \
                   db_match.status != 'Completed':
                    log.info(f"Updating status for {home_team_name} vs {away_team_name} from {db_match.status} to {current_schedule_status} (from schedule)")
                    db_match.status = current_schedule_status
                    update_this_match = True
                
                if update_this_match:
                    db.session.add(db_match) # Add to session for potential commit
                    matches_updated +=1

            # --- NEW: Check if match is in the past and not yet 'Completed', then fetch results ---
            if db_match and start_time_dt < current_time_utc and db_match.status != 'Completed':
                log.info(f"Match {db_match.home_team} vs {db_match.away_team} (ID: {db_match.match_id}) is in the past ({start_time_dt}) and not 'Completed'. Attempting to fetch results.")
                
                match_identifier = {
                    'round_number': round_obj.round_number,
                    'year': round_obj.year,
                    'home_team': db_match.home_team,
                    'away_team': db_match.away_team,
                    'start_time': db_match.start_time # Use start_time from DB match object
                }
                
                # fetch_match_result internally calls _fetch_nrl_round_data_from_web again.
                # This is okay but less efficient if you could reuse the `fixture` data.
                # However, fetch_match_result is designed to be self-contained.
                result_status, home_score, away_score = fetch_match_result(match_identifier)

                if result_status == 'Finished' and home_score is not None and away_score is not None:
                    log.info(f"Populating results for past match {db_match.home_team} vs {db_match.away_team}: {home_score}-{away_score}, Status: Completed")
                    db_match.result_home_score = home_score
                    db_match.result_away_score = away_score
                    db_match.status = 'Completed' # Directly set to Completed
                    # Determine winner for the match record
                    if home_score > away_score:
                        db_match.winner = db_match.home_team
                    elif away_score > home_score:
                        db_match.winner = db_match.away_team
                    else:
                        db_match.winner = 'Draw'
                    db.session.add(db_match) # Add to session for commit
                    results_populated += 1
                elif result_status != 'Error' and result_status != 'Unknown':
                    # If the result scraper found a status like 'Live', 'Postponed', etc.,
                    # and it's different from current DB status (and not 'Completed')
                    if db_match.status != result_status and result_status not in ['Scheduled', 'Finished']:
                        log.info(f"Updating status for past match {db_match.home_team} vs {db_match.away_team} to '{result_status}' based on result scraper.")
                        db_match.status = result_status
                        db.session.add(db_match)
                # Else: Error or Unknown, or not Finished. Leave match as is or as updated by schedule.
    try:
        db.session.commit()
    except Exception as e_commit:
        log.error(f"DB Commit failed for NRL.com schedule population: {e_commit}", exc_info=True)
        db.session.rollback()

    log.info(f"--- Finished NRL.com Schedule Population. Rounds Created: {rounds_created}, Matches Created: {matches_created}, Matches Updated: {matches_updated}, Results Populated for Past Matches: {results_populated} ---")
