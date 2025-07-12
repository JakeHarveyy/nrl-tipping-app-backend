"""
Generates AI-powered betting recommendations for upcoming NRL matches.

Core Functions:
- predict_upcoming_matches(): Main prediction pipeline for backend integration
- load_trained_model(): Loads pre-trained ML model and feature scaler
- make_predictions(): Generates win probabilities and betting recommendations
- calculate_kelly_criterion(): Optimal stake sizing using Kelly Criterion formula
- display_betting_opportunities(): Formatted output of high-confidence bets

Features:
- Model confidence thresholds (52%+ for betting recommendations)
- Kelly Criterion position sizing for bankroll management
- Risk categorisation (Very High, High, Medium, Low confidence levels)
- Backend-compatible DataFrame input/output for seamless API integration

Output: Structured betting recommendations with probabilities, stakes, and confidence levels.
"""

import pandas as pd
import numpy as np
import joblib
from datetime import datetime
import sys
import os

def load_trained_model(model_path=None, scaler_path=None):
    """Load the trained model and scaler with backend-compatible paths"""
    try:
        if model_path is None:
            # Use absolute path for backend compatibility
            project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            model_path = os.path.join(project_root, 'app', 'ai_models', 'nrl_baseline_logistic_model.pkl')
        
        if scaler_path is None:
            # Use absolute path for backend compatibility
            project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            scaler_path = os.path.join(project_root, 'app', 'ai_models', 'nrl_feature_scaler.pkl')
        
        model = joblib.load(model_path)
        scaler = joblib.load(scaler_path)
        print("✅ Loaded trained model and scaler")
        return model, scaler
    except FileNotFoundError as e:
        print(f"❌ Could not load model files: {e}")
        return None, None

def get_model_features():
    """Get the feature list used by the model"""
    features = [
        'elo_diff',
        'form_margin_diff_3', 'form_margin_diff_5', 'form_margin_diff_8',
        'form_win_rate_diff_3', 'form_win_rate_diff_5', 'form_win_rate_diff_8',
        'form_points_for_diff_3', 'form_points_for_diff_5', 'form_points_for_diff_8',
        'form_points_against_diff_3', 'form_points_against_diff_5', 'form_points_against_diff_8',
        'winning_streak_diff', 'losing_streak_diff', 'games_since_win_diff', 
        'games_since_loss_diff', 'recent_wins_3_diff',
        'home_rest_days', 'away_rest_days', 'away_travel_distance_km',
        'home_implied_prob', 'away_implied_prob', 'market_spread'
    ]
    return features

def make_predictions(prediction_df, model, scaler, model_features):
    """
    Make predictions for upcoming matches
    
    Args:
        prediction_df: DataFrame with prediction features
        model: Trained model
        scaler: Feature scaler
        model_features: List of features used by model
        
    Returns:
        pd.DataFrame: Predictions with betting recommendations
    """
    print("\n🤖 Making AI predictions...")
    
    # Prepare features for model
    missing_features = set(model_features) - set(prediction_df.columns)
    if missing_features:
        print(f"⚠️  Missing features: {missing_features}")
        # Fill missing features with 0 (or could use median/mean)
        for feature in missing_features:
            prediction_df[feature] = 0
    
    # Select and order features
    X = prediction_df[model_features].copy()
    
    # Handle any remaining NaN values
    X = X.fillna(0)
    
    # Scale features
    X_scaled = scaler.transform(X)
    
    # Make predictions
    home_win_probs = model.predict_proba(X_scaled)[:, 1]
    predictions = model.predict(X_scaled)
    
    # Add predictions to dataframe
    results_df = prediction_df[['Date', 'Home Team', 'Away Team', 'Venue', 'Home Odds', 'Away Odds']].copy()
    results_df['home_win_probability'] = home_win_probs
    results_df['away_win_probability'] = 1 - home_win_probs
    results_df['predicted_winner'] = np.where(predictions == 1, results_df['Home Team'], results_df['Away Team'])
    results_df['model_confidence'] = np.maximum(home_win_probs, 1 - home_win_probs)
    
    # Betting recommendations
    betting_threshold = 0.52
    results_df['betting_recommendation'] = 'No Bet'
    results_df['recommended_team'] = ''
    results_df['confidence_level'] = ''
    
    # Home betting opportunities
    home_bets = home_win_probs > betting_threshold
    results_df.loc[home_bets, 'betting_recommendation'] = 'Bet Home'
    results_df.loc[home_bets, 'recommended_team'] = results_df.loc[home_bets, 'Home Team']
    
    # Away betting opportunities  
    away_bets = (1 - home_win_probs) > betting_threshold
    results_df.loc[away_bets, 'betting_recommendation'] = 'Bet Away'
    results_df.loc[away_bets, 'recommended_team'] = results_df.loc[away_bets, 'Away Team']
    
    # Confidence levels
    results_df.loc[results_df['model_confidence'] >= 0.70, 'confidence_level'] = 'Very High'
    results_df.loc[(results_df['model_confidence'] >= 0.60) & (results_df['model_confidence'] < 0.70), 'confidence_level'] = 'High'
    results_df.loc[(results_df['model_confidence'] >= 0.55) & (results_df['model_confidence'] < 0.60), 'confidence_level'] = 'Medium'
    results_df.loc[results_df['model_confidence'] < 0.55, 'confidence_level'] = 'Low'
    
    # Kelly Criterion calculation
    def calculate_kelly_criterion(p, odds):
        """
        Calculate Kelly Criterion stake
        p = probability of winning
        odds = decimal odds
        """
        if pd.isna(odds) or odds <= 1:
            return 0.0
        
        b = odds - 1  # Net odds
        q = 1 - p     # Probability of losing
        
        kelly = (b * p - q) / b
        
        # Cap Kelly at 25% of bankroll for safety
        kelly = max(0, min(kelly, 0.25))
        
        return kelly
    
    # Calculate Kelly Criterion for each match
    results_df['kelly_criterion_home'] = 0.0
    results_df['kelly_criterion_away'] = 0.0
    results_df['kelly_criterion_stake'] = 0.0
    
    # Calculate Kelly for home bets
    home_kelly = results_df.apply(lambda row: calculate_kelly_criterion(row['home_win_probability'], row['Home Odds']), axis=1)
    results_df['kelly_criterion_home'] = home_kelly
    
    # Calculate Kelly for away bets
    away_kelly = results_df.apply(lambda row: calculate_kelly_criterion(row['away_win_probability'], row['Away Odds']), axis=1)
    results_df['kelly_criterion_away'] = away_kelly
    
    # Set the recommended Kelly stake based on betting recommendation
    results_df.loc[results_df['betting_recommendation'] == 'Bet Home', 'kelly_criterion_stake'] = results_df.loc[results_df['betting_recommendation'] == 'Bet Home', 'kelly_criterion_home']
    results_df.loc[results_df['betting_recommendation'] == 'Bet Away', 'kelly_criterion_stake'] = results_df.loc[results_df['betting_recommendation'] == 'Bet Away', 'kelly_criterion_away']
    
    # Drop intermediate columns
    results_df = results_df.drop(['kelly_criterion_home', 'kelly_criterion_away'], axis=1)
    
    print(f"✅ Generated predictions for {len(results_df)} matches")
    
    return results_df

def display_betting_opportunities(results_df):
    """Display betting opportunities in a formatted way"""
    
    betting_opportunities = results_df[results_df['betting_recommendation'] != 'No Bet'].copy()
    
    print(f"\n🎲 BETTING OPPORTUNITIES FOUND: {len(betting_opportunities)}")
    print("=" * 80)
    
    if len(betting_opportunities) == 0:
        print("No betting opportunities meet the confidence threshold (52%)")
        return
    
    for _, match in betting_opportunities.iterrows():
        print(f"\n📅 {match['Date'].strftime('%A, %d %B %Y')}")
        print(f"🏈 {match['Home Team']} vs {match['Away Team']}")
        print(f"🏟️  {match['Venue']}")
        print(f"💰 {match['betting_recommendation']}: {match['recommended_team']}")
        print(f"🎯 Model Confidence: {match['model_confidence']:.1%} ({match['confidence_level']})")
        print(f"📊 Home Win Probability: {match['home_win_probability']:.1%}")
        print(f"📊 Away Win Probability: {match['away_win_probability']:.1%}")
        
        if not pd.isna(match['Home Odds']):
            print(f"💵 Odds - Home: {match['Home Odds']:.2f}, Away: {match['Away Odds']:.2f}")
        
        if match['kelly_criterion_stake'] > 0:
            print(f"💰 Kelly Criterion Stake: {match['kelly_criterion_stake']:.1%} of bankroll")
        
        print("-" * 50)

def save_predictions(results_df, output_path='nrl_predictions.csv'):
    """Save predictions to CSV"""
    results_df.to_csv(output_path, index=False)
    print(f"\n💾 Predictions saved to: {output_path}")

def predict_upcoming_matches(prediction_df, model_path=None, scaler_path=None):
    """
    Main function to generate predictions for upcoming matches.
    
    Args:
        prediction_df: DataFrame with prediction features (from backend service)
        model_path: Optional path to trained model
        scaler_path: Optional path to feature scaler
        
    Returns:
        pd.DataFrame: Predictions with betting recommendations
    """
    print("🏈 NRL AI BETTING PREDICTIONS")
    print("=" * 50)
    
    if prediction_df is None or prediction_df.empty:
        print("❌ No upcoming matches to predict")
        return None
    
    # Load trained model
    print("Loading trained AI model...")
    model, scaler = load_trained_model(model_path, scaler_path)
    
    if model is None:
        print("❌ Could not load trained model")
        return None
    
    # Make predictions
    print("Generating predictions...")
    model_features = get_model_features()
    results_df = make_predictions(prediction_df, model, scaler, model_features)
    
    # Display results
    print("Analyzing betting opportunities...")
    display_betting_opportunities(results_df)
    
    # Summary statistics
    total_matches = len(results_df)
    betting_matches = len(results_df[results_df['betting_recommendation'] != 'No Bet'])
    high_confidence = len(results_df[results_df['confidence_level'].isin(['High', 'Very High'])])
    
    print(f"\n📊 PREDICTION SUMMARY:")
    print(f"   • Total upcoming matches: {total_matches}")
    print(f"   • Betting opportunities: {betting_matches} ({betting_matches/total_matches*100:.1f}%)")
    print(f"   • High confidence predictions: {high_confidence}")
    print(f"   • Average model confidence: {results_df['model_confidence'].mean():.1%}")
    
    # Kelly Criterion summary
    positive_kelly_count = len(results_df[results_df['kelly_criterion_stake'] > 0])
    max_kelly = results_df['kelly_criterion_stake'].max()
    avg_kelly = results_df[results_df['kelly_criterion_stake'] > 0]['kelly_criterion_stake'].mean()
    
    print(f"\n💰 KELLY CRITERION SUMMARY:")
    print(f"   • Matches with positive Kelly stake: {positive_kelly_count}")
    print(f"   • Maximum Kelly stake: {max_kelly:.1%}")
    if positive_kelly_count > 0:
        print(f"   • Average Kelly stake (positive only): {avg_kelly:.1%}")
    
    print("\n✅ Prediction pipeline complete!")
    
    return results_df

def main():
    """Main prediction workflow - for standalone testing only"""
    
    # This is for standalone testing - import here to avoid circular dependencies
    try:
        # Add prediction directory to path
        sys.path.append(os.path.dirname(os.path.abspath(__file__)))
        from prediction_pipeline import NRLPredictionPipeline
        
        print("🏈 NRL AI BETTING PREDICTIONS")
        print("=" * 50)
        
        # Step 1: Generate prediction features
        print("Step 1: Processing upcoming matches...")
        pipeline = NRLPredictionPipeline()
        prediction_df = pipeline.run_prediction_pipeline()
        
        if prediction_df is None:
            print("❌ No upcoming matches to predict")
            return
        
        # Step 2: Use the new backend-compatible function
        results_df = predict_upcoming_matches(prediction_df)
        
        if results_df is not None:
            # Step 3: Save results
            save_predictions(results_df)
            print("🚀 Ready for betting integration!")
            
    except ImportError as e:
        print(f"❌ Could not import prediction pipeline: {e}")
        print("This main function is for standalone testing only.")
        print("For backend integration, use predict_upcoming_matches() directly with DataFrame input.")

if __name__ == "__main__":
    main()
