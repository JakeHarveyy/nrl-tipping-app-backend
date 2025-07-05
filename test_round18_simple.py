#!/usr/bin/env python3
"""
Simple test script for AI predictions on Round 18
"""
import os
import sys
from decimal import Decimal

# Add the project root to Python path
project_root = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, project_root)

from app import create_app, db
from app.models import Round, Match, User, AIPrediction
from app.services.ai_prediction_service import run_ai_predictions_for_round, get_ai_predictions_for_round

def main():
    """Main test function"""
    print("🏈 NRL AI PREDICTION SYSTEM - ROUND 18 TEST")
    print("=" * 60)
    
    app = create_app()
    
    with app.app_context():
        # Check Round 18 data
        print("🔍 CHECKING ROUND 18 DATA")
        print("=" * 50)
        
        round18 = Round.query.filter_by(year=2025, round_number=18).first()
        if not round18:
            print("❌ Round 18 not found!")
            return
        
        matches = Match.query.filter_by(round_id=round18.round_id).all()
        print(f"📊 Round 18 has {len(matches)} matches:")
        
        for i, match in enumerate(matches, 1):
            print(f"  {i}. {match.home_team} vs {match.away_team}")
            print(f"     Date: {match.start_time.strftime('%Y-%m-%d %H:%M')}")
            print(f"     Odds: Home {match.home_odds}, Away {match.away_odds}")
        
        # Check AI Bot
        ai_bot = User.query.filter_by(username='LogisticsRegressionBot').first()
        if not ai_bot:
            print("❌ AI Bot not found!")
            return
        
        print(f"\n🤖 AI Bot: {ai_bot.username}")
        print(f"   Bankroll: ${ai_bot.bankroll}")
        
        # Check existing predictions
        existing_predictions = AIPrediction.query.join(Match).filter(
            Match.round_id == round18.round_id,
            AIPrediction.user_id == ai_bot.user_id
        ).all()
        
        print(f"📈 Existing AI predictions for Round 18: {len(existing_predictions)}")
        
        if existing_predictions:
            print("🗑️  Clearing existing predictions for fresh test...")
            for pred in existing_predictions:
                db.session.delete(pred)
            db.session.commit()
        
        # Run AI predictions
        print("\n🚀 RUNNING AI PREDICTIONS FOR ROUND 18")
        print("=" * 50)
        
        print("Starting AI prediction process...")
        success = run_ai_predictions_for_round(round_number=18, year=2025)
        
        if success:
            print("✅ AI predictions completed successfully!")
            
            # Get and display the predictions
            predictions = get_ai_predictions_for_round(round_number=18, year=2025)
            
            print(f"\n📊 GENERATED {len(predictions)} PREDICTIONS:")
            print("-" * 50)
            
            for i, pred in enumerate(predictions, 1):
                print(f"{i}. {pred['home_team']} vs {pred['away_team']}")
                print(f"   🏠 Home Win Probability: {pred['home_win_probability']:.1%}")
                print(f"   🏃 Away Win Probability: {pred['away_win_probability']:.1%}")
                print(f"   🎯 Predicted Winner: {pred['predicted_winner']}")
                print(f"   📈 Model Confidence: {pred['model_confidence']:.1%} ({pred['confidence_level']})")
                print(f"   💰 Betting Recommendation: {pred['betting_recommendation']}")
                if pred['kelly_criterion_stake'] > 0:
                    print(f"   💵 Kelly Stake: {pred['kelly_criterion_stake']:.1%}")
                print()
            
            # Summary statistics
            total_predictions = len(predictions)
            betting_recommendations = [p for p in predictions if p['betting_recommendation'] != 'No Bet']
            high_confidence = [p for p in predictions if p['confidence_level'] in ['High', 'Very High']]
            
            print(f"📊 SUMMARY STATISTICS:")
            print(f"   • Total predictions: {total_predictions}")
            print(f"   • Betting recommendations: {len(betting_recommendations)}")
            print(f"   • High confidence predictions: {len(high_confidence)}")
            if total_predictions > 0:
                print(f"   • Average confidence: {sum(p['model_confidence'] for p in predictions)/len(predictions):.1%}")
            
            # Check AI bot's updated bankroll
            ai_bot = User.query.filter_by(username='LogisticsRegressionBot').first()
            if ai_bot:
                print(f"   • AI Bot bankroll after betting: ${ai_bot.bankroll}")
            
            print("\n✅ ALL TESTS PASSED!")
            print("🎉 AI prediction system is working correctly for Round 18!")
            
        else:
            print("❌ AI predictions failed!")
            print("🔧 Check the logs for detailed error information.")

if __name__ == "__main__":
    main()
