"""
Transforms upcoming matches into model-ready features using historical context.

Core Functions:
- NRLPredictionPipeline(): Main pipeline class for feature generation
- load_upcoming_matches(): Filters future matches requiring predictions
- calculate_features_for_new_matches(): Applies rolling stats, Elo ratings, rest/travel factors
- extract_prediction_features(): Creates difference features and market intelligence
- run_prediction_pipeline(): Complete end-to-end processing workflow

Process Flow:
1. Combine historical data with upcoming matches for temporal context
2. Calculate rolling features using complete team history (prevents data leakage)
3. Generate strength metrics (Elo ratings, form differences, contextual factors)
4. Output ML-ready DataFrame with 20+ prediction features

Backend Integration: Supports both CSV file input and DataFrame input from backend services.
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import sys
import os

# Add parent directory to path to import feature engineering functions
current_dir = os.path.dirname(os.path.abspath(__file__))
feature_engineering_path = os.path.join(current_dir, '..', '..', '..', 'FeatureEngineering')
if feature_engineering_path not in sys.path:
    sys.path.append(feature_engineering_path)

try:
    from feature_engineering import (
        calculate_rolling_features, calculate_elo_ratings, 
        calculate_rest_days, calculate_travel_distance
    )
except ImportError:
    # Fallback: try to import from the ai_models directory structure
    ai_models_path = os.path.join(current_dir, '..')
    if ai_models_path not in sys.path:
        sys.path.append(ai_models_path)
    
    from feature_engineering import (
        calculate_rolling_features, calculate_elo_ratings, 
        calculate_rest_days, calculate_travel_distance
    )

class NRLPredictionPipeline:
    """Pipeline for generating features for upcoming matches"""
    
    def __init__(self, historical_data_path=None, team_stats_path=None, 
                 historical_matches_df=None, team_stats_df=None):
        """
        Initialize the prediction pipeline
        
        Args:
            historical_data_path: Path to historical model-ready match data
            team_stats_path: Path to historical team stats with all features
            historical_matches_df: DataFrame of historical matches (backend mode)
            team_stats_df: DataFrame of team stats (backend mode)
        """
        # Backend mode: use provided DataFrames
        if historical_matches_df is not None and team_stats_df is not None:
            print("📖 Using backend-provided DataFrames...")
            self.historical_matches = historical_matches_df.copy()
            self.historical_team_stats = team_stats_df.copy()
            
            # Convert dates
            self.historical_matches['Date'] = pd.to_datetime(self.historical_matches['Date'])
            self.historical_team_stats['Date'] = pd.to_datetime(self.historical_team_stats['Date'])
            
            print(f"✅ Loaded {len(self.historical_matches)} historical matches from backend")
            print(f"✅ Loaded {len(self.historical_team_stats)} team performance records from backend")
            
            # Get the most recent date in historical data
            self.last_historical_date = self.historical_matches['Date'].max()
            print(f"📅 Latest historical match: {self.last_historical_date.strftime('%d/%m/%Y')}")
            return
        
        # File mode: use CSV files
        # Get absolute paths relative to the ai_models directory
        ai_models_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        
        if historical_data_path is None:
            self.historical_data_path = os.path.join(ai_models_dir, 'data', 'nrl_matches_final_model_ready.csv')
        else:
            self.historical_data_path = historical_data_path
            
        if team_stats_path is None:
            self.team_stats_path = os.path.join(ai_models_dir, 'data', 'nrl_team_stats_final_complete.csv')
        else:
            self.team_stats_path = team_stats_path
        
        # Load historical data
        print("📖 Loading historical datasets...")
        self.historical_matches = pd.read_csv(historical_data_path)
        self.historical_team_stats = pd.read_csv(team_stats_path)
        
        # Convert dates
        self.historical_matches['Date'] = pd.to_datetime(self.historical_matches['Date'])
        self.historical_team_stats['Date'] = pd.to_datetime(self.historical_team_stats['Date'])
        
        print(f"✅ Loaded {len(self.historical_matches)} historical matches")
        print(f"✅ Loaded {len(self.historical_team_stats)} team performance records")
        
        # Get the most recent date in historical data
        self.last_historical_date = self.historical_matches['Date'].max()
        print(f"📅 Latest historical match: {self.last_historical_date.strftime('%d/%m/%Y')}")

    def load_upcoming_matches(self, upcoming_matches_path=None):
        """
        Load upcoming matches that need predictions
        
        Args:
            upcoming_matches_path: Path to CSV with upcoming matches (including odds)
            
        Returns:
            pd.DataFrame: Upcoming matches filtered for dates after historical data
        """
        print("\n📅 Loading upcoming matches for prediction...")
        # Load the dataset with future matches
        upcoming_df = pd.read_csv(upcoming_matches_path)
        upcoming_df['Date'] = pd.to_datetime(upcoming_df['Date'], format='%d/%m/%Y')
        
        # Filter for matches after the latest historical date
        upcoming_matches = upcoming_df[upcoming_df['Date'] > self.last_historical_date].copy()
        
        # Filter out matches that already have scores (completed matches)
        upcoming_matches = upcoming_matches[
            (upcoming_matches['Home Score'].isna()) | 
            (upcoming_matches['Home Score'] == '') |
            (upcoming_matches['Home Score'] == 0)  # Assuming 0 means not played yet
        ].copy()
        
        print(f"🔮 Found {len(upcoming_matches)} upcoming matches requiring predictions")
        
        if len(upcoming_matches) > 0:
            print(f"📅 Next match date: {upcoming_matches['Date'].min().strftime('%d/%m/%Y')}")
            print(f"📅 Last match date: {upcoming_matches['Date'].max().strftime('%d/%m/%Y')}")
        
        return upcoming_matches

    def prepare_upcoming_matches_for_feature_engineering(self, upcoming_matches):
        """
        Prepare upcoming matches in the format needed for feature engineering
        
        Args:
            upcoming_matches: DataFrame with upcoming matches
            
        Returns:
            tuple: (combined_df, new_match_ids) - Combined historical + new data, and IDs of new matches
        """
        print("\n🔧 Preparing upcoming matches for feature engineering...")
        
        # Create match_ids for upcoming matches (continue from historical max)
        max_historical_id = self.historical_matches['match_id'].max()
        upcoming_matches = upcoming_matches.copy()
        upcoming_matches['match_id'] = range(max_historical_id + 1, max_historical_id + 1 + len(upcoming_matches))
        
        # Create required columns that might be missing
        required_columns = ['Home_Win', 'Home_Margin', 'Home Score', 'Away Score']
        for col in required_columns:
            if col not in upcoming_matches.columns:
                upcoming_matches[col] = np.nan  # Will be filled after prediction
        
        # Combine with historical data
        combined_df = pd.concat([self.historical_matches, upcoming_matches], ignore_index=True)
        combined_df = combined_df.sort_values('Date').reset_index(drop=True)
        
        new_match_ids = upcoming_matches['match_id'].tolist()
        
        print(f"✅ Combined dataset: {len(combined_df)} matches total")
        print(f"📊 New matches to predict: {len(new_match_ids)}")
        
        return combined_df, new_match_ids

    def create_team_records_for_new_matches(self, combined_df, new_match_ids):
        """
        Create team-level records for upcoming matches
        
        Args:
            combined_df: Combined historical + upcoming matches
            new_match_ids: List of match IDs for new matches
            
        Returns:
            pd.DataFrame: Team stats including new match records
        """
        print("\n👥 Creating team records for upcoming matches...")
        
        # Get only the new matches
        new_matches = combined_df[combined_df['match_id'].isin(new_match_ids)].copy()
        
        # Create team-level records (home and away) for new matches
        new_team_records = []
        
        for _, match in new_matches.iterrows():
            # Home team record
            home_record = {
                'match_id': match['match_id'],
                'Date': match['Date'],
                'team_name': match['Home Team'],
                'is_home': 1,
                'points_for': np.nan,  # Unknown until match is played
                'points_against': np.nan,
                'won': np.nan,
                'opponent': match['Away Team'],
                'Venue': match['Venue'],
                'City': match['City'],
                'margin': np.nan,
                'lost': np.nan
            }
            
            # Away team record
            away_record = {
                'match_id': match['match_id'],
                'Date': match['Date'],
                'team_name': match['Away Team'],
                'is_home': 0,
                'points_for': np.nan,
                'points_against': np.nan,
                'won': np.nan,
                'opponent': match['Home Team'],
                'Venue': match['Venue'],
                'City': match['City'],
                'margin': np.nan,
                'lost': np.nan
            }
            
            new_team_records.extend([home_record, away_record])
        
        # Convert to DataFrame
        new_team_df = pd.DataFrame(new_team_records)
        
        # Combine with historical team stats
        combined_team_stats = pd.concat([self.historical_team_stats, new_team_df], ignore_index=True)
        combined_team_stats = combined_team_stats.sort_values(['team_name', 'Date']).reset_index(drop=True)
        
        print(f"✅ Created {len(new_team_records)} team records for upcoming matches")
        
        return combined_team_stats

    def calculate_features_for_new_matches(self, combined_team_stats, new_match_ids):
        """
        Calculate all features using the combined dataset but only for new matches
        
        Args:
            combined_team_stats: Combined team stats with new matches
            new_match_ids: List of new match IDs
            
        Returns:
            pd.DataFrame: Team stats with features calculated for new matches
        """
        print("\n🧮 Calculating features for upcoming matches...")
        
        # The key insight: Rolling features will use all historical data up to each new match
        # This gives us the actual team state going into upcoming matches
        
        # Step 1: Calculate rolling features on the combined dataset
        print("  📊 Calculating rolling features...")
        team_stats_with_rolling = calculate_rolling_features(combined_team_stats)
        
        # Step 2: Calculate Elo ratings (these need the full sequence)
        print("  ⚡ Updating Elo ratings...")
        team_stats_with_elo = calculate_elo_ratings(team_stats_with_rolling)
        
        # Step 3: Calculate rest days
        print("  😴 Calculating rest days...")
        team_stats_with_rest = calculate_rest_days(team_stats_with_elo)
        
        # Step 4: Calculate travel distances
        print("  ✈️ Calculating travel distances...")
        team_stats_final = calculate_travel_distance(team_stats_with_rest)
        
        print("✅ All features calculated using complete historical context")
        
        return team_stats_final

    def extract_prediction_features(self, team_stats_final, combined_matches, new_match_ids):
        """
        Extract the final prediction-ready features for new matches
        
        Args:
            team_stats_final: Team stats with all features
            combined_matches: Combined match data
            new_match_ids: List of new match IDs
            
        Returns:
            pd.DataFrame: Model-ready features for upcoming matches
        """
        print("\n🎯 Extracting prediction-ready features...")
        
        # Filter for new matches only
        new_matches = combined_matches[combined_matches['match_id'].isin(new_match_ids)].copy()
        
        # Get team stats for new matches
        new_team_stats = team_stats_final[team_stats_final['match_id'].isin(new_match_ids)].copy()
        
        # Split into home and away
        home_stats = new_team_stats[new_team_stats['is_home'] == 1].copy()
        away_stats = new_team_stats[new_team_stats['is_home'] == 0].copy()
        
        # Feature columns to extract (excluding base columns)
        base_columns = ['match_id', 'Date', 'team_name', 'is_home', 'points_for', 
                       'points_against', 'won', 'opponent', 'Venue', 'City', 'margin', 'lost']
        feature_columns = [col for col in team_stats_final.columns if col not in base_columns]
        
        # Create home and away feature sets
        home_features = home_stats[['match_id'] + feature_columns].copy()
        away_features = away_stats[['match_id'] + feature_columns].copy()
        
        # Rename with prefixes
        home_rename = {col: f'home_{col}' for col in feature_columns}
        away_rename = {col: f'away_{col}' for col in feature_columns}
        
        home_features = home_features.rename(columns=home_rename)
        away_features = away_features.rename(columns=away_rename)
        
        # Merge with match data
        prediction_df = new_matches.copy()
        
        # Drop existing feature columns from new_matches to avoid _x, _y suffixes
        feature_cols_to_drop = []
        for col in prediction_df.columns:
            if col.startswith('home_') or col.startswith('away_'):
                if any(feat in col for feat in ['rolling_', 'elo', 'streak', 'wins', 'games_since', 'rest_days', 'travel_distance']):
                    feature_cols_to_drop.append(col)
            # Also drop any existing difference features
            if '_diff' in col or col in ['elo_diff', 'market_spread', 'home_implied_prob', 'away_implied_prob']:
                feature_cols_to_drop.append(col)
        
        prediction_df = prediction_df.drop(columns=feature_cols_to_drop, errors='ignore')
        
        # Now merge the fresh features
        prediction_df = prediction_df.merge(home_features, on='match_id', how='left')
        prediction_df = prediction_df.merge(away_features, on='match_id', how='left')
        
        # Calculate difference features (same as in original pipeline)
        print("  🔄 Calculating difference features...")
        
        # Form differences - Match the original model's expected column names
        form_features = [
            ('rolling_avg_margin_3', 'form_margin_diff_3'),
            ('rolling_avg_margin_5', 'form_margin_diff_5'), 
            ('rolling_avg_margin_8', 'form_margin_diff_8'),
            ('rolling_win_percentage_3', 'form_win_rate_diff_3'),
            ('rolling_win_percentage_5', 'form_win_rate_diff_5'),
            ('rolling_win_percentage_8', 'form_win_rate_diff_8'),
            ('rolling_avg_points_for_3', 'form_points_for_diff_3'),
            ('rolling_avg_points_for_5', 'form_points_for_diff_5'),
            ('rolling_avg_points_for_8', 'form_points_for_diff_8'),
            ('rolling_avg_points_against_3', 'form_points_against_diff_3'),
            ('rolling_avg_points_against_5', 'form_points_against_diff_5'),
            ('rolling_avg_points_against_8', 'form_points_against_diff_8')
        ]
        
        for feature_name, diff_col in form_features:
            home_col = f'home_{feature_name}'
            away_col = f'away_{feature_name}'
            
            if home_col in prediction_df.columns and away_col in prediction_df.columns:
                prediction_df[diff_col] = prediction_df[home_col] - prediction_df[away_col]
        
        # Elo difference
        if 'home_pre_match_elo' in prediction_df.columns and 'away_pre_match_elo' in prediction_df.columns:
            prediction_df['elo_diff'] = prediction_df['home_pre_match_elo'] - prediction_df['away_pre_match_elo']
        
        # Streak differences
        streak_features = ['winning_streak', 'losing_streak', 'games_since_win', 'games_since_loss', 'recent_wins_3']
        for feature in streak_features:
            home_col = f'home_{feature}'
            away_col = f'away_{feature}'
            diff_col = f'{feature}_diff'
            
            if home_col in prediction_df.columns and away_col in prediction_df.columns:
                prediction_df[diff_col] = prediction_df[home_col] - prediction_df[away_col]
        
        # Market features (if odds are available)
        if 'Home Odds' in prediction_df.columns and prediction_df['Home Odds'].notna().any():
            prediction_df['home_implied_prob'] = np.where(
                prediction_df['Home Odds'].notna() & (prediction_df['Home Odds'] > 0),
                1 / prediction_df['Home Odds'],
                np.nan
            )
            
        if 'Away Odds' in prediction_df.columns and prediction_df['Away Odds'].notna().any():
            prediction_df['away_implied_prob'] = np.where(
                prediction_df['Away Odds'].notna() & (prediction_df['Away Odds'] > 0),
                1 / prediction_df['Away Odds'],
                np.nan
            )
            
        if 'home_implied_prob' in prediction_df.columns and 'away_implied_prob' in prediction_df.columns:
            prediction_df['market_spread'] = prediction_df['home_implied_prob'] - prediction_df['away_implied_prob']
        
        print(f"✅ Generated {len(prediction_df)} match predictions with {len(prediction_df.columns)} features")
        
        return prediction_df

    def run_prediction_pipeline(self, upcoming_matches_path=None, output_path=None):
        """
        Run the complete prediction pipeline
        
        Args:
            upcoming_matches_path: Path to upcoming matches CSV
            output_path: Where to save prediction-ready features
            
        Returns:
            pd.DataFrame: Model-ready features for prediction
        """
        print("\n🚀 RUNNING NRL PREDICTION PIPELINE")
        print("=" * 50)
        
        # Default output path if none provided
        if output_path is None:
            ai_models_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            output_path = os.path.join(ai_models_dir, 'data', 'upcoming_matches_model_ready.csv')
        
        # Step 1: Load upcoming matches
        upcoming_matches = self.load_upcoming_matches(upcoming_matches_path)
        
        if len(upcoming_matches) == 0:
            print("❌ No upcoming matches found for prediction")
            return None
        
        # Step 2: Prepare for feature engineering
        combined_matches, new_match_ids = self.prepare_upcoming_matches_for_feature_engineering(upcoming_matches)
        
        # Step 3: Create team records
        combined_team_stats = self.create_team_records_for_new_matches(combined_matches, new_match_ids)
        
        # Step 4: Calculate features
        team_stats_final = self.calculate_features_for_new_matches(combined_team_stats, new_match_ids)
        
        # Step 5: Extract prediction features
        prediction_df = self.extract_prediction_features(team_stats_final, combined_matches, new_match_ids)
        
        # Step 6: Save results
        prediction_df.to_csv(output_path, index=False)
        print(f"\n💾 Prediction-ready features saved to: {output_path}")
        
        # Display summary
        print(f"\n📊 PREDICTION PIPELINE SUMMARY:")
        print(f"   • Upcoming matches processed: {len(prediction_df)}")
        print(f"   • Features generated: {len(prediction_df.columns)}")
        print(f"   • Date range: {prediction_df['Date'].min().strftime('%d/%m/%Y')} to {prediction_df['Date'].max().strftime('%d/%m/%Y')}")
        
        # Show upcoming matches
        print(f"\n🏈 UPCOMING MATCHES:")
        for _, match in prediction_df[['Date', 'Home Team', 'Away Team', 'Venue']].head(10).iterrows():
            print(f"   {match['Date'].strftime('%d/%m/%Y')}: {match['Home Team']} vs {match['Away Team']} at {match['Venue']}")
        
        print("\n✅ Ready for model prediction!")
        print("=" * 50)
        
        return prediction_df

def main():
    """Main function to run the prediction pipeline"""
    
    # Initialize pipeline
    pipeline = NRLPredictionPipeline()
    
    # Run prediction pipeline
    prediction_features = pipeline.run_prediction_pipeline()
    
    if prediction_features is not None:
        print("\n🎯 Next steps:")
        print("1. Load your trained model (nrl_baseline_logistic_model.pkl)")
        print("2. Load the feature scaler (nrl_feature_scaler.pkl)")
        print("3. Use the prediction features to generate probabilities")
        print("4. Apply your betting strategy")
    
if __name__ == "__main__":
    main()
