#working 1/05/2025
#pinnacle scraper
#later intergrate live odd scraping/backup odd scraper

import requests
from decimal import Decimal, InvalidOperation
from datetime import datetime, timezone, timedelta
from app.models import Match, Round
from app import db

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

    except Exception as e:
        print(f"An unexpected error occurred during Pinnacle data fetch: {e}")
        import traceback
        traceback.print_exc()
        return None # Indicate a general failure

    print(f"--- Finished Fetching Pinnacle Data. Processed {len(processed_matches)} matches. ---")
    return processed_matches



#edit to populate whole season 
def update_matches_from_odds_scraper():
    """
    Fetches latest odds data and updates the database.
    Finds or creates Rounds and Matches. Updates odds and start times.
    """
    print("--- Starting Match/Odds Update from Scraper ---")
    scraped_matches = fetch_pinnacle_nrl_data()

    if scraped_matches is None:
        print("ERROR: Failed to fetch data from scraper. Aborting update.")
        return # Stop if fetching failed critically

    if not scraped_matches:
        print("No matches returned from scraper.")
        return # Stop if no matches were processed

    # Counters for summary
    matches_created = 0
    matches_updated = 0
    rounds_created = 0
    skipped_count = 0

    # Process matches one by one
    for match_data in scraped_matches:
        try:
            # --- 1. Find or Create Round ---
            round_number = match_data.get('round_number')
            start_time_dt = match_data.get('start_time_dt')

            if not round_number or not start_time_dt:
                print(f"Skipping match due to missing round number or start time. Data: {match_data}")
                skipped_count += 1
                continue

            # Determine year from start time
            year = start_time_dt.year

            # Look for existing round
            round_obj = Round.query.filter_by(round_number=round_number, year=year).first()

            if not round_obj:
                print(f"Round {round_number} ({year}) not found, creating...")
                # Estimate start/end dates for the round if needed (can be basic)
                # A more robust approach might involve a separate process or manual entry for round dates
                round_start_est = start_time_dt.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1) # Estimate
                round_end_est = round_start_est + timedelta(days=6) # Estimate week end

                round_obj = Round(
                    round_number=round_number,
                    year=year,
                    start_date=round_start_est, # Use scraped time or estimate
                    end_date=round_end_est,     # Use scraped time or estimate
                    status='Upcoming' # Default new rounds to upcoming
                )
                db.session.add(round_obj)
                # Flush to get the ID without committing the whole transaction yet
                try:
                    db.session.flush()
                    print(f"Created Round {round_number} ({year}) with ID {round_obj.round_id}")
                    rounds_created += 1
                except Exception as flush_err:
                    print(f"ERROR: Failed to flush new round {round_number} ({year}): {flush_err}")
                    db.session.rollback() # Rollback this specific attempt
                    skipped_count += 1
                    continue # Skip this match if round creation fails
            # else:
                # print(f"Found existing Round {round_number} ({year}) with ID {round_obj.round_id}")


            # --- 2. Find or Create Match ---
            external_id = match_data.get('external_id')
            home_team = match_data.get('home_team')
            away_team = match_data.get('away_team')
            home_odds = match_data.get('home_odds') # Already Decimal or None
            away_odds = match_data.get('away_odds') # Already Decimal or None

            if not home_team or not away_team:
                 print(f"Skipping match due to missing team names. Data: {match_data}")
                 skipped_count += 1
                 continue

            # Try finding by external_id first (most reliable if consistent)
            match_obj = None
            if external_id:
                match_obj = Match.query.filter_by(external_match_id=external_id).first()

            # Fallback: Try finding by teams and round (less reliable due to name variations)
            # Be cautious with this fallback, might lead to duplicates if names change slightly
            # if not match_obj:
            #     print(f"Match with external_id {external_id} not found. Trying fallback lookup...")
            #     match_obj = Match.query.filter_by(
            #         round_id=round_obj.round_id,
            #         home_team=home_team,
            #         away_team=away_team
            #     ).first() # This assumes team names are EXACT matches

            if match_obj:
                # --- 3a. Update Existing Match ---
                # print(f"Found existing Match ID: {match_obj.match_id} (Ext: {external_id})")
                update_needed = False
                if match_obj.start_time != start_time_dt:
                    print(f"  Updating start time for Match {match_obj.match_id} from {match_obj.start_time} to {start_time_dt}")
                    match_obj.start_time = start_time_dt
                    update_needed = True
                # Compare odds, handling None values and potential precision differences
                if (home_odds is not None and match_obj.home_odds != home_odds) or \
                   (home_odds is None and match_obj.home_odds is not None):
                    print(f"  Updating home odds for Match {match_obj.match_id} from {match_obj.home_odds} to {home_odds}")
                    match_obj.home_odds = home_odds
                    update_needed = True
                if (away_odds is not None and match_obj.away_odds != away_odds) or \
                   (away_odds is None and match_obj.away_odds is not None):
                    print(f"  Updating away odds for Match {match_obj.match_id} from {match_obj.away_odds} to {away_odds}")
                    match_obj.away_odds = away_odds
                    update_needed = True

                if update_needed:
                    match_obj.last_odds_update = datetime.now(timezone.utc)
                    db.session.add(match_obj) # Add updated object to session
                    matches_updated += 1
                # else:
                    # print(f"  No updates needed for Match {match_obj.match_id}.")

            else:
                # --- 3b. Create New Match ---
                print(f"Match not found for Ext ID: {external_id} / Teams: {home_team} vs {away_team}. Creating...")
                match_obj = Match(
                    external_match_id=external_id,
                    round_id=round_obj.round_id,
                    home_team=home_team,
                    away_team=away_team,
                    start_time=start_time_dt,
                    home_odds=home_odds,
                    away_odds=away_odds,
                    status='Scheduled', # Default new matches to Scheduled
                    last_odds_update = datetime.now(timezone.utc) if home_odds or away_odds else None
                )
                db.session.add(match_obj) # Add new object to session
                matches_created += 1

        except Exception as e:
            print(f"ERROR processing scraped match data: {match_data}. Error: {e}")
            import traceback
            traceback.print_exc()
            db.session.rollback() # Rollback any partial changes for this match
            skipped_count += 1
            # Continue to the next match instead of stopping the whole process
            continue

    # --- 4. Commit All Changes ---
    try:
        db.session.commit()
        print("--- Database commit successful ---")
    except Exception as e:
        db.session.rollback()
        print(f"ERROR: Database commit failed after processing all matches: {e}")

    print(f"--- Finished Match/Odds Update ---")
    print(f"Summary: Rounds Created: {rounds_created}, Matches Created: {matches_created}, Matches Updated: {matches_updated}, Skipped/Errors: {skipped_count}")
