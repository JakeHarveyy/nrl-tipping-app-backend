# app/services/historical_data_updater.py
"""
Historical Data Updater Service for NRL Tipping Application

Updates historical datasets with completed match results to ensure the AI model
always has the most recent data for predictions. Handles data transformation,
feature regeneration, and automated updates after round completion.

DATA FLOW:
1. Loads existing nrl_matches_final_model_ready.csv (model-ready dataset)
2. Extracts base columns to maintain data consistency
3. Appends new completed match data from database
4. Regenerates all features using feature engineering pipeline
5. Saves updated datasets with automatic backup
6. AI predictions use the refreshed model-ready data for future rounds
"""

# =============================================================================
# IMPORTS
# =============================================================================
import pandas as pd
import os
import sys
from datetime import datetime
import logging

# =============================================================================
# PATH CONFIGURATION
# =============================================================================
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from app import db
from app.models import Match, Round

feature_engineering_path = os.path.join(project_root, 'app', 'ai_models', 'prediction')
if feature_engineering_path not in sys.path:
    sys.path.append(feature_engineering_path)

try:
    from feature_engineering import (
        create_team_level_stats, calculate_rolling_features,
        calculate_elo_ratings, calculate_rest_days, calculate_travel_distance,
        assemble_final_model_ready_dataframe
    )
except ImportError:
    logging.error("Could not import feature engineering functions")

log = logging.getLogger(__name__)

# =============================================================================
# FILE PATHS AND CONFIGURATION
# =============================================================================
HISTORICAL_DATA_PATH = os.path.join(project_root, 'app', 'ai_models', 'data', 'nrl_matches_final_model_ready.csv')
TEAM_STATS_PATH = os.path.join(project_root, 'app', 'ai_models', 'data', 'nrl_team_stats_final_complete.csv')

# =============================================================================
# TEAM NAME MAPPING
# =============================================================================
MODEL_TO_DB_MAPPING = {
    'Manly Sea Eagles': 'Sea Eagles',
    'South Sydney Rabbitohs': 'Rabbitohs',
    'Sydney Roosters': 'Roosters',
    'North QLD Cowboys': 'Cowboys',
    'Melbourne Storm': 'Storm',
    'Canberra Raiders': 'Raiders',
    'St George Dragons': 'Dragons',
    'Canterbury Bulldogs': 'Bulldogs',
    'Gold Coast Titans': 'Titans',
    'Cronulla Sharks': 'Sharks',
    'Dolphins': 'Dolphins',
    'Penrith Panthers': 'Panthers',
    'Brisbane Broncos': 'Broncos',
    'Newcastle Knights': 'Knights',
    'Wests Tigers': 'Tigers',
    'Parramatta Eels': 'Eels',
    'New Zealand Warriors': 'Warriors'
}

# =============================================================================
# UTILITY FUNCTIONS
# =============================================================================

def _map_db_to_model_team_name(db_team_name):
    """Map database team name to model training name"""
    db_to_model = {v: k for k, v in MODEL_TO_DB_MAPPING.items()}
    return db_to_model.get(db_team_name, db_team_name)

# =============================================================================
# MAIN DATA UPDATE FUNCTIONS
# =============================================================================

def update_historical_data_with_completed_round(round_number, year):
    """
    Update historical datasets with completed match results from a specific round.
    
    Args:
        round_number (int): The round number to process
        year (int): The year
        
    Returns:
        bool: Success status
    """
    log.info(f"Updating historical data with Round {round_number}, Year {year} results...")
    
    try:
        # --- GET COMPLETED MATCHES FROM DATABASE ---
        completed_matches = Match.query.join(Round).filter(
            Round.round_number == round_number,
            Round.year == year,
            Match.result_home_score.isnot(None),
            Match.result_away_score.isnot(None),
            Match.status == 'Completed'
        ).all()
        
        if not completed_matches:
            log.warning(f"No completed matches found for Round {round_number}, Year {year}")
            return False
        
        log.info(f"Found {len(completed_matches)} completed matches to add to historical data")
        
        # --- LOAD EXISTING HISTORICAL DATA ---
        if os.path.exists(HISTORICAL_DATA_PATH):
            historical_df = pd.read_csv(HISTORICAL_DATA_PATH)
            log.info(f"Loading existing model-ready historical data from {HISTORICAL_DATA_PATH}")
            # Extract base columns for new data consistency
            base_columns = ['Date', 'Kick-off (local)', 'Home Team', 'Away Team', 'Venue', 'City', 
                          'Home Score', 'Away Score', 'Play Off Game?', 'Over Time?', 'Home Odds', 
                          'Draw Odds', 'Away Odds', 'Winner Team', 'Winner ', 'latitude', 'longitude', 
                          'Home_Win', 'Home_Margin', 'match_id']
            available_base_columns = [col for col in base_columns if col in historical_df.columns]
            base_df = historical_df[available_base_columns].copy()
            log.info(f"Extracted base columns for new data: {len(available_base_columns)} columns")
        else:
            log.error(f"Historical data file not found: {HISTORICAL_DATA_PATH}")
            return False
        
        base_df['Date'] = pd.to_datetime(base_df['Date'])
        
        # --- CONVERT COMPLETED MATCHES TO HISTORICAL FORMAT ---
        new_match_records = []
        
        for match in completed_matches:
            home_team_model = _map_db_to_model_team_name(match.home_team)
            away_team_model = _map_db_to_model_team_name(match.away_team)
            
            home_win = 1 if match.result_home_score > match.result_away_score else 0
            winner_team = home_team_model if home_win else away_team_model
            
            new_record = {
                'Date': match.start_time.strftime('%Y-%m-%d'),
                'Kick-off (local)': match.start_time.strftime('%H:%M'),
                'Home Team': home_team_model,
                'Away Team': away_team_model,
                'Venue': match.venue or 'TBD',
                'City': match.venue_city or 'TBD',
                'Home Score': match.result_home_score,
                'Away Score': match.result_away_score,
                'Play Off Game?': '',
                'Over Time?': '',
                'Home Odds': float(match.home_odds) if match.home_odds else None,
                'Draw Odds': None,
                'Away Odds': float(match.away_odds) if match.away_odds else None,
                'Winner Team': winner_team,
                'Winner ': 'Home' if home_win else 'Away',
                'latitude': 0,
                'longitude': 0,
                'Home_Win': home_win,
                'Home_Margin': match.result_home_score - match.result_away_score,
                'match_id': len(base_df) + len(new_match_records)
            }
            new_match_records.append(new_record)
        
        # --- MERGE NEW MATCHES WITH HISTORICAL DATA ---
        new_matches_df = pd.DataFrame(new_match_records)
        new_matches_df['Date'] = pd.to_datetime(new_matches_df['Date'])
        
        updated_historical_df = pd.concat([base_df, new_matches_df], ignore_index=True)
        updated_historical_df = updated_historical_df.sort_values('Date').reset_index(drop=True)
        
        log.info(f"Added {len(new_match_records)} new matches to historical dataset")
        
        # --- REGENERATE ALL FEATURES WITH UPDATED DATA ---
        log.info("Regenerating team-level stats and features...")
        
        team_stats_df = create_team_level_stats(updated_historical_df)
        
        team_stats_enhanced = calculate_rolling_features(team_stats_df)
        team_stats_with_elo = calculate_elo_ratings(team_stats_enhanced)
        team_stats_with_rest = calculate_rest_days(team_stats_with_elo)
        team_stats_final = calculate_travel_distance(team_stats_with_rest)
        
        final_match_df, core_features = assemble_final_model_ready_dataframe(
            updated_historical_df, team_stats_final
        )
        
        # --- SAVE UPDATED DATASETS WITH BACKUP ---
        backup_suffix = datetime.now().strftime("_%Y%m%d_%H%M%S_backup")
        
        historical_backup = HISTORICAL_DATA_PATH.replace('.csv', f'{backup_suffix}.csv')
        team_stats_backup = TEAM_STATS_PATH.replace('.csv', f'{backup_suffix}.csv')
        
        try:
            if os.path.exists(HISTORICAL_DATA_PATH):
                os.rename(HISTORICAL_DATA_PATH, historical_backup)
            if os.path.exists(TEAM_STATS_PATH):
                os.rename(TEAM_STATS_PATH, team_stats_backup)
            log.info(f"📁 Backups created successfully")
        except PermissionError as e:
            log.warning(f"Could not create backup files (file may be in use): {e}")
            historical_backup = "No backup created"
            team_stats_backup = "No backup created"
        
        final_match_df.to_csv(HISTORICAL_DATA_PATH, index=False)
        team_stats_final.to_csv(TEAM_STATS_PATH, index=False)
        
        log.info(f"✅ Successfully updated historical datasets")
        log.info(f"📁 Backups saved: {historical_backup}, {team_stats_backup}")
        log.info(f"📊 Updated dataset now contains {len(final_match_df)} matches")
        
        return True
        
    except Exception as e:
        log.error(f"Error updating historical data: {e}", exc_info=True)
        return False

# =============================================================================
# AUTOMATED UPDATE FUNCTIONS
# =============================================================================

def auto_update_after_round_completion():
    """
    Automatically detect completed rounds and update historical data.
    Called by scheduler after each round completes.
    """
    log.info("Checking for completed rounds to update historical data...")
    
    try:
        completed_rounds = Round.query.filter(
            Round.status == 'Completed',
            Round.year == 2025
        ).order_by(Round.round_number.desc()).limit(3).all()
        
        if not completed_rounds:
            log.info("No completed rounds found")
            return
        
        latest_round = completed_rounds[0]
        
        success = update_historical_data_with_completed_round(
            latest_round.round_number, 
            latest_round.year
        )
        
        if success:
            log.info(f"Historical data updated with Round {latest_round.round_number} results")
        else:
            log.error(f"Failed to update historical data for Round {latest_round.round_number}")
            
    except Exception as e:
        log.error(f"Error in auto-update process: {e}", exc_info=True)

# =============================================================================
# TESTING AND MAIN EXECUTION
# =============================================================================

if __name__ == "__main__":
    from app import create_app
    app = create_app()
    with app.app_context():
        print("🧪 Testing historical data update with Round 17...")
        success = update_historical_data_with_completed_round(17, 2025)
        print(f"✅ Update result: {success}")
