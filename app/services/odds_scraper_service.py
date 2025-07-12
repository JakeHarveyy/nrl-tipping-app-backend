# app/services/odds_scraper_service.py
"""
Pinnacle Odds Scraper Service for NRL Tipping Application

Scrapes live betting odds from Pinnacle API for NRL matches and updates database
records with current odds data. Handles API authentication, data processing,
team name normalization, and real-time odds updates with SSE event broadcasting.
"""

# =============================================================================
# IMPORTS
# =============================================================================
import requests
from decimal import Decimal, InvalidOperation
from datetime import datetime, timezone, timedelta
import logging

from app.models import Match, Round
from app import db
from app.utils.text_utils import normalize_team_name
from app.sse_events import announce_event

log = logging.getLogger(__name__)

# =============================================================================
# API CONFIGURATION
# =============================================================================
PINNACLE_API_URL = "https://www.pinnacle.com/config/app.json"
PINNACLE_MATCHUPS_URL = "https://guest.api.arcadia.pinnacle.com/0.1/leagues/1654/matchups?brandId=0"

# =============================================================================
# API AUTHENTICATION AND HEADERS
# =============================================================================

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

# =============================================================================
# DATA FETCHING FUNCTIONS
# =============================================================================

def fetch_matchups(headers):
    try:
        response = requests.get(PINNACLE_MATCHUPS_URL, headers=headers, timeout=15)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"Error fetching matchups: {e}")
        return None

def fetch_game_odds(league_id, headers):
    URL = f"https://guest.api.arcadia.pinnacle.com/0.1/leagues/{league_id}/markets/straight"
    try:
        response = requests.get(URL, headers=headers, timeout=15)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"Error fetching game odds for league {league_id}: {e}")
        return None

# =============================================================================
# DATA FILTERING AND PROCESSING FUNCTIONS
# =============================================================================

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

# =============================================================================
# ODDS CONVERSION UTILITIES
# =============================================================================

def american_to_decimal(odd):
    try:
        odd = int(odd) # Ensure it's an integer first
        if odd > 0:
            decimal_odds = (Decimal(odd) / 100) + 1
        else:
            decimal_odds = (100 / Decimal(abs(odd))) + 1
        return decimal_odds.quantize(Decimal("0.001"))
    except (ValueError, TypeError, InvalidOperation):
        print(f"Could not convert American odd '{odd}' to Decimal.")
        return None

# =============================================================================
# MAIN PINNACLE DATA FETCHING
# =============================================================================

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
            return []

        # --- GET LEAGUE ID AND FETCH ODDS ---
        try:
             league_id = next(iter(matchups_dict.values()))["league_id"]
        except StopIteration:
             print("Could not determine league ID (empty matchups dict).")
             return []

        game_odds_raw = fetch_game_odds(league_id, headers)
        if game_odds_raw is None:
            print("Failed to fetch game odds. Proceeding without odds.")
            game_odds_dict = {}
        else:
            game_odds_dict = process_game_odds(game_odds_raw, list(matchups_dict.keys()))

        # --- PROCESS MATCHUPS WITH ODDS ---
        print(f"Processing {len(matchups_dict)} potential NRL matchups...")
        for external_id, matchup_data in matchups_dict.items():
            match_info = {
                'external_id': str(external_id),
                'home_team': matchup_data['home_team'].strip(),
                'away_team': matchup_data['away_team'].strip(),
                'start_time_str': matchup_data['start_time'],
                'round_number': matchup_data['round'],
                'home_odds': None,
                'away_odds': None
            }

            # --- PARSE START TIME ---
            try:
                match_info['start_time_dt'] = datetime.fromisoformat(matchup_data['start_time'].replace('Z', '+00:00'))
            except ValueError:
                print(f"Error parsing start time '{matchup_data['start_time']}' for match {external_id}. Skipping match.")
                continue

            # --- ADD ODDS IF AVAILABLE ---
            if external_id in game_odds_dict:
                odds_data = game_odds_dict[external_id].get("prices")
                if odds_data and len(odds_data) >= 2:
                    home_american = odds_data[0].get("price")
                    away_american = odds_data[1].get("price")
                    match_info['home_odds'] = american_to_decimal(home_american) if home_american else None
                    match_info['away_odds'] = american_to_decimal(away_american) if away_american else None

            processed_matches.append(match_info)

    except Exception as e:
        print(f"An unexpected error occurred during Pinnacle data fetch: {e}")
        import traceback
        traceback.print_exc()
        return None

    print(f"--- Finished Fetching Pinnacle Data. Processed {len(processed_matches)} matches. ---")
    return processed_matches

# =============================================================================
# DATABASE UPDATE FUNCTIONS
# =============================================================================

def update_matches_from_odds_scraper():
    """
    Main function to update database matches with current odds from Pinnacle API.
    Handles team name normalization, match identification, and SSE event broadcasting.
    """
    print("--- Starting Odds Update from Pinnacle ---")
    scraped_pinnacle_matches = fetch_pinnacle_nrl_data()

    if scraped_pinnacle_matches is None:
        print("Failed to fetch data from Pinnacle. Aborting odds update.")
        return
    if not scraped_pinnacle_matches:
        print("No matches returned from Pinnacle scraper for odds update.")
        return

    matches_odds_updated_count = 0
    matches_not_found_in_db = 0
    updated_match_details_for_sse = []

    # --- PROCESS EACH PINNACLE MATCH ---

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

            # --- NORMALIZE TEAM NAMES AND FIND MATCH ---
            norm_p_home = normalize_team_name(p_home_team)
            norm_p_away = normalize_team_name(p_away_team)

            # Find match in DB based on normalized team names and start time window
            time_window_start = p_start_time_dt - timedelta(hours=2)
            time_window_end = p_start_time_dt + timedelta(hours=2)

            db_match = Match.query.filter(
                Match.start_time >= time_window_start,
                Match.start_time <= time_window_end
            ).all()

            found_db_match = None
            for m in db_match:
                if normalize_team_name(m.home_team) == norm_p_home and \
                   normalize_team_name(m.away_team) == norm_p_away:
                    found_db_match = m
                    break

            # --- UPDATE ODDS IF MATCH FOUND ---
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
                    found_db_match.external_match_id = pinnacle_match_data.get('external_id')
                    db.session.add(found_db_match)
                    matches_odds_updated_count += 1
                    print(f"Updated odds for DB Match ID {found_db_match.match_id} ({found_db_match.home_team} vs {found_db_match.away_team}) using Pinnacle data.")

                    # --- COLLECT DETAILS FOR SSE EVENT ---
                    updated_match_details_for_sse.append({
                        'match_id': found_db_match.match_id,
                        'home_odds': float(found_db_match.home_odds) if found_db_match.home_odds else None,
                        'away_odds': float(found_db_match.away_odds) if found_db_match.away_odds else None,
                    })
            else:
                print(f"Could not find DB match for Pinnacle: {norm_p_home} vs {norm_p_away} around {p_start_time_dt}")
                matches_not_found_in_db +=1

        except Exception as e:
            print(f"Error processing Pinnacle match data: {pinnacle_match_data}. Error: {e}", exc_info=True)
            db.session.rollback()
            continue
    
    # --- COMMIT CHANGES AND SEND SSE EVENTS ---
    try:
        if matches_odds_updated_count > 0 or updated_match_details_for_sse:
            db.session.commit()
            log.info(f"DB Commit successful for odds update. {matches_odds_updated_count} matches affected.")
            
            # --- ANNOUNCE SSE EVENTS AFTER SUCCESSFUL COMMIT ---
            for detail in updated_match_details_for_sse:
                announce_event('odds_update', detail)
        else:
            log.info("No odds changes detected that required a database commit.")

    except Exception as e:
        db.session.rollback()
        log.error(f"DB Commit failed for odds update: {e}", exc_info=True)

    log.info(f"--- Finished Odds Update from Pinnacle. Odds Updated: {matches_odds_updated_count}, Matches Not Found: {matches_not_found_in_db} ---")
