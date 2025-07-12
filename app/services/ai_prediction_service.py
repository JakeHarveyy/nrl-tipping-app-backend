# app/services/ai_prediction_service.py
"""
AI Prediction Service for NRL Tipping Application

Orchestrates the complete AI prediction pipeline: data preparation, feature engineering,
model inference, prediction storage, and automated betting. Integrates machine learning
predictions with the application's betting and user management systems.

Notebook for the unimplmented AI Workflow Available at: https://github.com/JakeHarveyy/NRLMatchPredictor
"""

# =============================================================================
# IMPORTS
# =============================================================================
import pandas as pd
import joblib
import os
import sys
from decimal import Decimal
import logging
import importlib.util

# =============================================================================
# PATH CONFIGURATION AND DYNAMIC IMPORTS
# =============================================================================
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from app import db
from app.models import Match, Round, User, AIPrediction
from app.services.betting_service import place_bet_for_user

prediction_path = os.path.join(project_root, 'app', 'ai_models', 'prediction')
if prediction_path not in sys.path:
    sys.path.append(prediction_path)

# Dynamic import of prediction pipeline modules
spec = importlib.util.spec_from_file_location("prediction_pipeline", os.path.join(prediction_path, "prediction_pipeline.py"))
prediction_pipeline_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prediction_pipeline_module)
NRLPredictionPipeline = prediction_pipeline_module.NRLPredictionPipeline

spec = importlib.util.spec_from_file_location("predict_upcoming_matches", os.path.join(prediction_path, "predict_upcoming_matches.py"))
predict_upcoming_matches_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(predict_upcoming_matches_module)
make_predictions = predict_upcoming_matches_module.make_predictions
get_model_features = predict_upcoming_matches_module.get_model_features

log = logging.getLogger(__name__)

# =============================================================================
# CONFIGURATION AND CONSTANTS
# =============================================================================
MODEL_PATH = os.path.join(project_root, 'app', 'ai_models', 'nrl_baseline_logistic_model.pkl')
SCALER_PATH = os.path.join(project_root, 'app', 'ai_models', 'nrl_feature_scaler.pkl')
HISTORICAL_DATA_PATH = os.path.join(project_root, 'app', 'ai_models', 'data', 'nrl_matches_final_model_ready.csv')
TEAM_STATS_PATH = os.path.join(project_root, 'app', 'ai_models', 'data', 'nrl_team_stats_final_complete.csv')
AI_BOT_USERNAME = 'LogisticsRegressionBot'
KELLY_FRACTION = Decimal('0.5')

# =============================================================================
# TEAM NAME MAPPING
# =============================================================================
TEAM_NAME_MAPPING = {
    'Sea Eagles': 'Manly Sea Eagles',
    'Rabbitohs': 'South Sydney Rabbitohs',
    'Roosters': 'Sydney Roosters',
    'Cowboys': 'North QLD Cowboys',
    'Storm': 'Melbourne Storm',
    'Raiders': 'Canberra Raiders',
    'Dragons': 'St George Dragons',
    'Bulldogs': 'Canterbury Bulldogs',
    'Titans': 'Gold Coast Titans',
    'Sharks': 'Cronulla Sharks',
    'Dolphins': 'Dolphins',
    'Panthers': 'Penrith Panthers',
    'Broncos': 'Brisbane Broncos',
    'Knights': 'Newcastle Knights',
    'Wests Tigers': 'Wests Tigers',
    'Eels': 'Parramatta Eels',
    'Warriors': 'New Zealand Warriors'
}

# =============================================================================
# UTILITY FUNCTIONS
# =============================================================================

def _map_team_name_for_model(db_team_name):
    """Map database team name to the full name used in model training."""
    mapped_name = TEAM_NAME_MAPPING.get(db_team_name, db_team_name)
    if mapped_name != db_team_name:
        log.debug(f"Mapped team name: '{db_team_name}' -> '{mapped_name}'")
    return mapped_name

def _map_team_name_from_model(model_team_name):
    """Map model team name back to database team name."""
    reverse_mapping = {v: k for k, v in TEAM_NAME_MAPPING.items()}
    mapped_name = reverse_mapping.get(model_team_name, model_team_name)
    if mapped_name != model_team_name:
        log.debug(f"Reverse mapped team name: '{model_team_name}' -> '{mapped_name}'")
    return mapped_name

def _load_model_and_scaler():
    """Load the trained model and scaler from .pkl files."""
    try:
        model = joblib.load(MODEL_PATH)
        scaler = joblib.load(SCALER_PATH)
        log.info("AI model and scaler loaded successfully.")
        return model, scaler
    except FileNotFoundError as e:
        log.error(f"AI model or scaler file not found: {e}")
        return None, None

# =============================================================================
# DATA PREPARATION FUNCTIONS
# =============================================================================

def _prepare_upcoming_matches_data(round_number, year):
    """
    Fetch matches from database and prepare data for the prediction pipeline.
    Converts database match format to the format expected by the AI model.
    """
    log.info(f"Preparing match data for Round {round_number}, Year {year}...")
    
    matches_for_round = Match.query.join(Round).filter(
        Round.round_number == round_number, 
        Round.year == year
    ).all()
    
    if not matches_for_round:
        log.warning(f"No matches found for Round {round_number}, Year {year}")
        return None
    
    match_data = []
    for match in matches_for_round:
        if not match.home_odds or not match.away_odds:
            log.info(f"Skipping match {match.home_team} vs {match.away_team} - missing odds (Home: {match.home_odds}, Away: {match.away_odds})")
            continue
            
        home_team_mapped = _map_team_name_for_model(match.home_team)
        away_team_mapped = _map_team_name_for_model(match.away_team)
        
        match_dict = {
            'Date': match.start_time.strftime('%d/%m/%Y'),
            'Home Team': home_team_mapped,
            'Away Team': away_team_mapped,
            'Venue': match.venue or 'TBD',
            'City': match.venue_city or 'TBD',
            'Home Odds': float(match.home_odds),
            'Away Odds': float(match.away_odds),
            'Home Score': '',
            'Away Score': '',
            'match_id_db': match.match_id,
            'db_home_team': match.home_team,
            'db_away_team': match.away_team
        }
        match_data.append(match_dict)
    
    import tempfile
    temp_csv_path = os.path.join(tempfile.gettempdir(), 'upcoming_matches_from_db.csv')
    df = pd.DataFrame(match_data)
    df.to_csv(temp_csv_path, index=False)
    
    log.info(f"Prepared {len(match_data)} matches for prediction pipeline")
    return temp_csv_path, matches_for_round

# =============================================================================
# PREDICTION PIPELINE FUNCTIONS
# =============================================================================

def _run_prediction_pipeline(upcoming_matches_path):
    """
    Run the complete NRL prediction pipeline to generate model-ready features.
    Integrates with the feature engineering and prediction modules.
    """
    try:
        log.info("Running NRL prediction pipeline...")
        
        pipeline = NRLPredictionPipeline(
            historical_data_path=HISTORICAL_DATA_PATH,
            team_stats_path=TEAM_STATS_PATH
        )
        
        prediction_df = pipeline.run_prediction_pipeline(
            upcoming_matches_path=upcoming_matches_path
        )
        
        if prediction_df is None or prediction_df.empty:
            log.error("Prediction pipeline returned empty results")
            return None
            
        log.info(f"Pipeline generated {len(prediction_df)} predictions with {len(prediction_df.columns)} features")
        return prediction_df
        
    except Exception as e:
        log.error(f"Error running prediction pipeline: {e}", exc_info=True)
        return None

# =============================================================================
# MAIN SERVICE FUNCTIONS
# =============================================================================

def run_ai_predictions_for_round(round_number, year):
    """
    Main service function integrating the complete prediction pipeline.
    Orchestrates data preparation, model inference, prediction storage, and automated betting.
    """
    log.info(f"Starting AI predictions for Round {round_number}, Year {year}")
    
    # --- INITIALISE COMPONENTS ---
    model, scaler = _load_model_and_scaler()
    if not model or not scaler:
        log.error("Cannot load AI model - aborting predictions")
        return False
    
    ai_bot = User.query.filter_by(username=AI_BOT_USERNAME).first()
    if not ai_bot:
        log.error(f"AI Bot user '{AI_BOT_USERNAME}' not found")
        return False
    
    # --- PREPARE MATCH DATA ---
    match_data_result = _prepare_upcoming_matches_data(round_number, year)
    if not match_data_result:
        log.warning("No match data available for predictions")
        return False
        
    temp_csv_path, db_matches = match_data_result
    log.info(f"Prepared {len(db_matches)} matches for prediction")
    
    # --- RUN PREDICTION PIPELINE ---
    prediction_df = _run_prediction_pipeline(temp_csv_path)
    if prediction_df is None:
        log.error("Prediction pipeline failed")
        return False
    
    # --- GENERATE MODEL PREDICTIONS ---
    try:
        predict_spec = importlib.util.spec_from_file_location("predict_upcoming_matches", 
                                                            os.path.join(prediction_path, "predict_upcoming_matches.py"))
        predict_module = importlib.util.module_from_spec(predict_spec)
        predict_spec.loader.exec_module(predict_module)
        
        results_df = predict_module.predict_upcoming_matches(
            prediction_df,
            model_path=MODEL_PATH,
            scaler_path=SCALER_PATH
        )
        
        if results_df is None or results_df.empty:
            log.error("Prediction service returned empty results")
            return False
        
        log.info(f"Generated {len(results_df)} AI predictions")
        log.info(f"Prediction columns: {list(results_df.columns)}")
        
        # --- PROCESS AND STORE PREDICTIONS ---
        predictions_stored = 0
        for index, row in results_df.iterrows():
            log.info(f"Processing prediction {index + 1}/{len(results_df)}: {row.get('Home Team', 'Unknown')} vs {row.get('Away Team', 'Unknown')}")
            
            # Convert mapped team names back to database team names for matching
            home_team_for_db = _map_team_name_from_model(row['Home Team'])
            away_team_for_db = _map_team_name_from_model(row['Away Team'])
            
            log.info(f"Mapped team names for DB matching: {row['Home Team']} -> {home_team_for_db}, {row['Away Team']} -> {away_team_for_db}")
            
            # Find corresponding database match using mapped-back team names
            db_match = None
            for match in db_matches:
                if (match.home_team == home_team_for_db and 
                    match.away_team == away_team_for_db):
                    db_match = match
                    break
            
            if not db_match:
                log.warning(f"Could not find DB match for {home_team_for_db} vs {away_team_for_db} (mapped from {row['Home Team']} vs {row['Away Team']})")
                log.info(f"Available matches in DB: {[(m.home_team, m.away_team) for m in db_matches]}")
                continue

            if not db_match.home_odds or not db_match.away_odds:
                log.warning(f"Skipping prediction for {db_match.home_team} vs {db_match.away_team} - missing odds in database")
                continue
            
            log.info(f"Found matching DB match: {db_match.home_team} vs {db_match.away_team} (ID: {db_match.match_id})")
            
            # Check if prediction already exists for this match
            existing_prediction = AIPrediction.query.filter_by(
                user_id=ai_bot.user_id,
                match_id=db_match.match_id
            ).first()
            
            if existing_prediction:
                log.info(f"AI prediction already exists for match {db_match.home_team} vs {db_match.away_team} (ID: {existing_prediction.prediction_id})")
                continue
            
            # --- STORE PREDICTION IN DATABASE ---
            try:
                kelly_stake_raw = row['kelly_criterion_stake']
                kelly_stake_actual = kelly_stake_raw * float(KELLY_FRACTION)
                
                prediction_entry = AIPrediction(
                    user_id=ai_bot.user_id,
                    match_id=db_match.match_id,
                    home_team=row['Home Team'],
                    away_team=row['Away Team'],
                    match_date=db_match.start_time,
                    home_win_probability=row['home_win_probability'],
                    away_win_probability=row['away_win_probability'],
                    predicted_winner=row['predicted_winner'],
                    model_confidence=row['model_confidence'],
                    betting_recommendation=row['betting_recommendation'],
                    recommended_team=row.get('recommended_team'),
                    confidence_level=row['confidence_level'],
                    kelly_criterion_stake=kelly_stake_actual
                )
                db.session.add(prediction_entry)
                predictions_stored += 1
                log.info(f"Stored AI prediction for {row['Home Team']} vs {row['Away Team']} (Winner: {row['predicted_winner']}, Confidence: {row['model_confidence']:.2f})")
                
                db.session.commit()
                log.info(f"Committed prediction {predictions_stored} to database successfully")
                
            except Exception as prediction_error:
                log.error(f"Failed to store/commit prediction: {prediction_error}", exc_info=True)
                db.session.rollback()
                continue
            
            # --- PLACE AUTOMATED BET IF RECOMMENDED ---
            if row['betting_recommendation'] != 'No Bet' and row['kelly_criterion_stake'] > 0:
                from app.models import Bet
                existing_bet = Bet.query.filter_by(
                    user_id=ai_bot.user_id,
                    match_id=db_match.match_id,
                    status='Pending'
                ).first()
                
                if existing_bet:
                    log.info(f"AI Bot already has a bet placed for match {db_match.home_team} vs {db_match.away_team} (Bet ID: {existing_bet.bet_id})")
                else:
                    # Calculate bet amount using Kelly criterion
                    db.session.refresh(ai_bot)
                    kelly_stake = float(row['kelly_criterion_stake'])
                    # Apply Kelly fraction (safety multiplier) to reduce bet size
                    bet_amount = Decimal(str(ai_bot.bankroll)) * Decimal(str(kelly_stake)) * KELLY_FRACTION
                    
                    # Cap bet amount
                    max_bet = Decimal(str(ai_bot.bankroll)) * Decimal('0.1')
                    bet_amount = min(bet_amount, max_bet)
                    bet_amount = bet_amount.quantize(Decimal('0.01'))
                    
                    if bet_amount > Decimal('0.01'):
                        # Map the recommended team back to database team name for betting
                        recommended_team_for_bet = row.get('recommended_team')
                        if recommended_team_for_bet:
                            # Convert mapped team name back to database team name
                            db_team_name = None
                            if recommended_team_for_bet == row['Home Team']:
                                db_team_name = db_match.home_team
                            elif recommended_team_for_bet == row['Away Team']:
                                db_team_name = db_match.away_team
                            
                            if db_team_name:
                                success, result_msg = place_bet_for_user(
                                    user=ai_bot,
                                    match=db_match,
                                    team_selected=db_team_name,
                                    bet_amount=bet_amount
                                )
                                
                                if success:
                                    log.info(f"AI Bot placed ${bet_amount} bet on {db_team_name} (mapped from {recommended_team_for_bet})")
                                else:
                                    log.error(f"Failed to place bet: {result_msg}")
                            else:
                                log.error(f"Could not map recommended team '{recommended_team_for_bet}' back to database team name")
        
        # --- FINALISE AND CLEANUP ---
        try:
            db.session.commit()
            if predictions_stored > 0:
                log.info(f"Successfully completed AI predictions for Round {round_number}, Year {year}. Stored {predictions_stored} new predictions.")
            else:
                log.info(f"AI predictions for Round {round_number}, Year {year} already exist. No new predictions stored.")
        except Exception as final_commit_error:
            log.error(f"Failed final commit: {final_commit_error}")
            db.session.rollback()
        
        if os.path.exists(temp_csv_path):
            os.remove(temp_csv_path)
            
        return len(results_df) > 0
        
    except Exception as e:
        db.session.rollback()
        log.error(f"Error processing predictions: {e}", exc_info=True)
        return False

# =============================================================================
# API ENDPOINT FUNCTIONS
# =============================================================================
def get_ai_predictions_for_round(round_number, year):
    """
    Return AI predictions for frontend display without placing bets.
    Used by API endpoints to fetch stored predictions for user interface.
    """
    ai_bot = User.query.filter_by(username=AI_BOT_USERNAME).first()
    if not ai_bot:
        log.warning(f"AI Bot user '{AI_BOT_USERNAME}' not found for predictions query")
        return []
    
    predictions = AIPrediction.query.join(Match).join(Round).filter(
        Round.round_number == round_number,
        Round.year == year,
        AIPrediction.user_id == ai_bot.user_id
    ).all()
    
    return [{
        'match_id': pred.match_id,
        'home_team': pred.home_team,
        'away_team': pred.away_team,
        'home_win_probability': float(pred.home_win_probability),
        'away_win_probability': float(pred.away_win_probability),
        'predicted_winner': pred.predicted_winner,
        'model_confidence': float(pred.model_confidence),
        'betting_recommendation': pred.betting_recommendation,
        'confidence_level': pred.confidence_level,
        'kelly_criterion_stake': float(pred.kelly_criterion_stake)
    } for pred in predictions]
