#working 1/05/2025
#pinnacle scraper
#later intergrate live odd scraping/backup odd scraper

import requests
from decimal import Decimal, InvalidOperation
from datetime import datetime, timezone, timedelta
from app.models import Match, Round
from app import db
from app.utils.text_utils import normalize_team_name
from app.sse_events import announce_event
import logging

log = logging.getLogger(__name__)

PINNACLE_API_URL = "https://www.pinnacle.com/config/app.json"
PINNACLE_MATCHUPS_URL = "https://guest.api.arcadia.pinnacle.com/0.1/leagues/1654/matchups?brandId=0"

def get_api_key():
    response = requests.get(PINNACLE_API_URL)
    return response.json()["api"]["haywire"]["apiKey"]

def get_headers(api_key):
    return {
        "accept": "application/json",
        "content-type": "application/json",
        "referer": "https://www.pinnacle.com/en/rugby-league/super-league/matchups/",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36",
        "sec-ch-ua": '"Not(A:Brand";v="99", "Google Chrome";v="133", "Chromium";v="133"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
        "x-api-key": api_key,
    }

def fetch_matchups(headers):
    try:
        response = requests.get(PINNACLE_MATCHUPS_URL, headers=headers, timeout=15) # Add timeout
        response.raise_for_status() # Raise HTTPError for bad responses (4xx or 5xx)
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"Error fetching matchups: {e}")
        return None # Return None on error

def filter_NRL_matchups(matchups):
    return {
        matchup["id"]: {
            "home_team": matchup["participants"][0]["name"],
            "away_team": matchup["participants"][1]["name"],
            "start_time": matchup["startTime"],
            "league_id": matchup["league"]["id"],
            "round": matchup["league"]["matchupCount"]
        }
        for matchup in matchups
        if matchup["league"]["name"] == "NRL" and matchup["type"] == "matchup" 
    }

def fetch_game_odds(league_id, headers):
    URL = f"https://guest.api.arcadia.pinnacle.com/0.1/leagues/{league_id}/markets/straight"
    try:
        response = requests.get(URL, headers=headers, timeout=15) # Add timeout
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"Error fetching game odds for league {league_id}: {e}")
        return None # Return None on error

def process_game_odds(game_odds, target_matchup_ids):
    """
    Process game odds and organize them by matchup ID
    
    Args:
        game_odds: The odds data from the API
        target_matchup_ids: List of matchup IDs we're interested in
        
    Returns:
        Dictionary of matchup IDs mapped to their moneyline odds
    """
    result = {}
    
    for odds in game_odds:
        # Check if this is a moneyline bet and if the matchup is one we're tracking
        if odds["type"] == "moneyline" and odds["matchupId"] in target_matchup_ids:
            matchup_id = odds["matchupId"]
            
            # Initialize the dictionary for this matchup if it doesn't exist
            if matchup_id not in result:
                result[matchup_id] = {}
                
            # Add the moneyline odds
            if odds["key"] == "s;0;m":
                result[matchup_id] = {
                    "prices": odds["prices"],
                    "type": odds["type"]
                }
    
    return result

def american_to_decimal(odd):
    try:
        odd = int(odd) # Ensure it's an integer first
        if odd > 0:
            decimal_odds = (Decimal(odd) / 100) + 1
        else:
            decimal_odds = (100 / Decimal(abs(odd))) + 1
        # Round to 3 decimal places as per your DB schema Numeric(6, 3)
        return decimal_odds.quantize(Decimal("0.001"))
    except (ValueError, TypeError, InvalidOperation):
        print(f"Could not convert American odd '{odd}' to Decimal.")
        return None # Return None if conversion fails

def fetch_pinnacle_nrl_data():
    """
    Fetches NRL matchups and odds from Pinnacle API.

    Returns:
        list: A list of dictionaries, each representing a match, or None if fetching fails.
              Each dictionary contains: 'external_id', 'home_team', 'away_team',
              'start_time_dt', 'round_number', 'home_odds', 'away_odds'.
    """

    print("--- Fetching Pinnacle NRL Data ---")
    processed_matches = []

    try:
        api_key = get_api_key()
        if not api_key:
            print("Failed to get API key.")
            return None
        headers = get_headers(api_key)

        # Get and filter matchups
        matchups_raw = fetch_matchups(headers)
        if matchups_raw is None:
            print("Failed to fetch matchups.")
            return None
        matchups_dict = filter_NRL_matchups(matchups_raw)

        if not matchups_dict:
            print("No NRL matchups found in fetched data.")
            return [] # Return empty list, not None

        # Get league ID and fetch odds
        # Use a try-except in case matchups_dict is empty after filtering
        try:
             league_id = next(iter(matchups_dict.values()))["league_id"]
        except StopIteration:
             print("Could not determine league ID (empty matchups dict).")
             return []

        game_odds_raw = fetch_game_odds(league_id, headers)
        if game_odds_raw is None:
            print("Failed to fetch game odds. Proceeding without odds.")
            game_odds_dict = {} # Process matches without odds
        else:
            game_odds_dict = process_game_odds(game_odds_raw, list(matchups_dict.keys()))

        print(f"Processing {len(matchups_dict)} potential NRL matchups...")
        # Combine matchup data with odds data
        for external_id, matchup_data in matchups_dict.items():
            match_info = {
                'external_id': str(external_id), # Ensure external ID is a string
                'home_team': matchup_data['home_team'].strip(), # Strip whitespace
                'away_team': matchup_data['away_team'].strip(),
                'start_time_str': matchup_data['start_time'],
                'round_number': matchup_data['round'], # Assuming this is the correct round number
                'home_odds': None,
                'away_odds': None
            }

            # Parse start time string into timezone-aware datetime object (UTC)
            try:
                # Pinnacle uses ISO 8601 format with 'Z' for UTC
                match_info['start_time_dt'] = datetime.fromisoformat(matchup_data['start_time'].replace('Z', '+00:00'))
            except ValueError:
                print(f"Error parsing start time '{matchup_data['start_time']}' for match {external_id}. Skipping match.")
                continue # Skip match if time can't be parsed

            # Add odds if available
            if external_id in game_odds_dict:
                odds_data = game_odds_dict[external_id].get("prices")
                if odds_data and len(odds_data) >= 2:
                    # Assuming index 0 is home, index 1 is away based on your example
                    # Convert American odds from price field to Decimal
                    home_american = odds_data[0].get("price")
                    away_american = odds_data[1].get("price")
                    match_info['home_odds'] = american_to_decimal(home_american) if home_american else None
                    match_info['away_odds'] = american_to_decimal(away_american) if away_american else None

            processed_matches.append(match_info)
            # print(f"Processed Match: {match_info}") # Debugging

            # # # --- TEMPORARY OVERRIDE FOR TESTING ODDS UPDATE ---
            # # # Define the match you want to change and the new odds
            # test_match_home_team_canonical = "New Zealand Warriors" # Use the canonical name stored in your DB
            # test_match_away_team_canonical = "Penrith Panthers"
            # new_test_home_odds = Decimal("1.99") # New odds
            # new_test_away_odds = Decimal("10.00") # Optional: change away odds too

            # found_and_overridden = False
            # for match in processed_matches:
            #     # Compare against the already normalized names in 'match'
            #     if match['home_team'] == test_match_home_team_canonical and \
            #     match['away_team'] == test_match_away_team_canonical:
            #         log.warning(f"TESTING OVERRIDE: Modifying odds for {match['home_team']} vs {match['away_team']}.")
            #         log.warning(f"  Old odds: H={match['home_odds']}, A={match['away_odds']}")
            #         match['home_odds'] = new_test_home_odds
            #         match['away_odds'] = new_test_away_odds # If changing away too
            #         log.warning(f"  New odds: H={match['home_odds']}, A={match['away_odds']}")
            #         found_and_overridden = True
            #         break # Found and modified, no need to continue loop for override

            # if not found_and_overridden:
            #     log.warning(f"TESTING OVERRIDE: Did not find {test_match_home_team_canonical} vs {test_match_away_team_canonical} in Pinnacle data to override odds.")
            # # # --- END TEMPORARY OVERRIDE ---



    except Exception as e:
        print(f"An unexpected error occurred during Pinnacle data fetch: {e}")
        import traceback
        traceback.print_exc()
        return None # Indicate a general failure

    print(f"--- Finished Fetching Pinnacle Data. Processed {len(processed_matches)} matches. ---")
    return processed_matches



def update_matches_from_odds_scraper():
    print("--- Starting Odds Update from Pinnacle ---")
    scraped_pinnacle_matches = fetch_pinnacle_nrl_data() # This gets Pinnacle data

    if scraped_pinnacle_matches is None:
        print("Failed to fetch data from Pinnacle. Aborting odds update.")
        return
    if not scraped_pinnacle_matches:
        print("No matches returned from Pinnacle scraper for odds update.")
        return

    matches_odds_updated_count = 0
    matches_not_found_in_db = 0
    updated_match_details_for_sse = [] # Collect details of matches whose odds changed

    for pinnacle_match_data in scraped_pinnacle_matches:
        try:
            p_home_team = pinnacle_match_data.get('home_team')
            p_away_team = pinnacle_match_data.get('away_team')
            p_start_time_dt = pinnacle_match_data.get('start_time_dt') # Already UTC
            p_home_odds = pinnacle_match_data.get('home_odds')
            p_away_odds = pinnacle_match_data.get('away_odds')

            if not all([p_home_team, p_away_team, p_start_time_dt]):
                print(f"Pinnacle data missing key fields: {pinnacle_match_data}")
                continue

            # Normalize Pinnacle team names
            norm_p_home = normalize_team_name(p_home_team)
            norm_p_away = normalize_team_name(p_away_team)

            # Find match in DB (created by NRL.com scraper)
            # Match based on normalized team names and start time (within a window)
            time_window_start = p_start_time_dt - timedelta(hours=2)
            time_window_end = p_start_time_dt + timedelta(hours=2)

            # This query might need to be more sophisticated if team names are still tricky
            db_match = Match.query.filter(
                Match.start_time >= time_window_start,
                Match.start_time <= time_window_end
            ).all() # Get all matches in window, then filter by normalized names

            found_db_match = None
            for m in db_match:
                if normalize_team_name(m.home_team) == norm_p_home and \
                   normalize_team_name(m.away_team) == norm_p_away:
                    found_db_match = m
                    break

            if found_db_match:
                original_home_odds = found_db_match.home_odds
                original_away_odds = found_db_match.away_odds
                update_needed = False
                
                if (p_home_odds is not None and found_db_match.home_odds != p_home_odds) or \
                   (p_home_odds is None and found_db_match.home_odds is not None):
                    found_db_match.home_odds = p_home_odds
                    update_needed = True
                if (p_away_odds is not None and found_db_match.away_odds != p_away_odds) or \
                   (p_away_odds is None and found_db_match.away_odds is not None):
                    found_db_match.away_odds = p_away_odds
                    update_needed = True

                if update_needed:
                    found_db_match.last_odds_update = datetime.now(timezone.utc)
                    # Store Pinnacle's external_id if you want
                    found_db_match.external_match_id = pinnacle_match_data.get('external_id')
                    db.session.add(found_db_match)
                    matches_odds_updated_count += 1
                    print(f"Updated odds for DB Match ID {found_db_match.match_id} ({found_db_match.home_team} vs {found_db_match.away_team}) using Pinnacle data.")

                    # --- ADDED: Collect details for SSE event ---
                    updated_match_details_for_sse.append({
                        'match_id': found_db_match.match_id,
                        'home_odds': float(found_db_match.home_odds) if found_db_match.home_odds else None,
                        'away_odds': float(found_db_match.away_odds) if found_db_match.away_odds else None,
                        # Optionally include other identifiers if useful for frontend
                        # 'home_team': found_db_match.home_team,
                        # 'away_team': found_db_match.away_team
                    })
            else:
                print(f"Could not find DB match for Pinnacle: {norm_p_home} vs {norm_p_away} around {p_start_time_dt}")
                matches_not_found_in_db +=1

        except Exception as e:
            print(f"Error processing Pinnacle match data: {pinnacle_match_data}. Error: {e}", exc_info=True)
            db.session.rollback()
            continue
    try:
        if matches_odds_updated_count > 0 or updated_match_details_for_sse: # Check if any actual DB adds were made
            db.session.commit()
            log.info(f"DB Commit successful for odds update. {matches_odds_updated_count} matches affected.")
            # --- ADDED: Announce SSE events AFTER successful commit ---
            for detail in updated_match_details_for_sse:
                announce_event('odds_update', detail)
            # -------------------------------------------------------
        else:
            log.info("No odds changes detected that required a database commit.")

    except Exception as e:
        db.session.rollback()
        log.error(f"DB Commit failed for odds update: {e}", exc_info=True)

    log.info(f"--- Finished Odds Update from Pinnacle. Odds Updated: {matches_odds_updated_count}, Matches Not Found: {matches_not_found_in_db} ---")
