#!/usr/bin/env python3
"""
Test script for AI predictions on Round 18
"""
import os
import sys
from decimal import Decimal

# Add the project root to Python path
project_root = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, project_root)

from app import create_app, db
from app.models import Round, Match, User, AIPrediction, Bet
from app.services.ai_prediction_service import run_ai_predictions_for_round, get_ai_predictions_for_round

def test_round18_data(app):
    """Check what data we have for Round 18"""
    with app.app_context():
        print("🔍 CHECKING ROUND 18 DATA")
        print("=" * 50)
        
        # Check if Round 18 exists
        round18 = Round.query.filter_by(year=2025, round_number=18).first()
        if not round18:
            print("❌ Round 18 not found!")
            return False
        
        print(f"✅ Round 18 found: ID {round18.round_id}")
        
        # Check matches for Round 18
        matches = Match.query.filter_by(round_id=round18.round_id).all()
        print(f"📊 Round 18 has {len(matches)} matches:")
        
        for i, match in enumerate(matches, 1):
            print(f"  {i}. {match.home_team} vs {match.away_team}")
            print(f"     Venue: {match.venue} ({match.venue_city})")
            print(f"     Date: {match.start_time.strftime('%Y-%m-%d %H:%M')}")
            print(f"     Status: {match.status}")
            print(f"     Odds: Home {match.home_odds}, Away {match.away_odds}")
            print()
        
        # Check AI Bot user
        ai_bot = User.query.filter_by(username='LogisticsRegressionBot').first()
        if ai_bot:
            print(f"🤖 AI Bot found: {ai_bot.username}")
            print(f"   Bankroll: ${ai_bot.bankroll}")
            print(f"   Is Bot: {ai_bot.is_bot}")
        else:
            print("❌ AI Bot not found!")
            return False
        
        # Check for existing predictions
        existing_predictions = AIPrediction.query.join(Match).filter(
            Match.round_id == round18.round_id,
            AIPrediction.user_id == ai_bot.user_id
        ).all()
        
        print(f"📈 Existing AI predictions for Round 18: {len(existing_predictions)}")
        
        return True

def test_ai_bot_bets(app):
    """Test and display the actual bets placed by the AI bot for Round 18"""
    with app.app_context():
        print("\n💰 TESTING AI BOT BETTING BEHAVIOR")
        print("=" * 50)
        
        # Get AI bot and Round 18
        ai_bot = User.query.filter_by(username='LogisticsRegressionBot').first()
        round18 = Round.query.filter_by(year=2025, round_number=18).first()
        
        if not ai_bot or not round18:
            print("❌ AI Bot or Round 18 not found!")
            return False
        
        # Get all bets placed by AI bot for Round 18
        ai_bets = Bet.query.filter_by(
            user_id=ai_bot.user_id,
            round_id=round18.round_id
        ).all()
        
        print(f"💰 AI Bot has placed {len(ai_bets)} bets for Round 18:")
        print("-" * 30)
        
        if not ai_bets:
            print("⚠️  No bets found for AI Bot in Round 18")
            return True
        
        total_bet_amount = Decimal('0.00')
        total_potential_payout = Decimal('0.00')
        
        for i, bet in enumerate(ai_bets, 1):
            match = Match.query.get(bet.match_id)
            print(f"{i}. Bet #{bet.bet_id}")
            print(f"     Match: {match.home_team} vs {match.away_team}")
            print(f"     Team Selected: {bet.team_selected}")
            print(f"     Amount: ${bet.amount}")
            print(f"     Odds: {bet.odds_at_placement}")
            print(f"     Potential Payout: ${bet.potential_payout}")
            print(f"     Status: {bet.status}")
            print(f"     Placed At: {bet.placement_time.strftime('%Y-%m-%d %H:%M:%S')}")
            print()
            
            total_bet_amount += bet.amount
            total_potential_payout += bet.potential_payout
        
        print(f"📊 BETTING SUMMARY:")
        print(f"   • Total Amount Bet: ${total_bet_amount}")
        print(f"   • Total Potential Payout: ${total_potential_payout}")
        print(f"   • Average Bet Size: ${total_bet_amount / len(ai_bets):.2f}")
        print(f"   • AI Bot Current Bankroll: ${ai_bot.bankroll}")
        print(f"   • Money at Risk: {(total_bet_amount / ai_bot.bankroll) * 100:.1f}% of bankroll")
        
        return True

def test_ai_predictions(app):
    """Test running AI predictions for Round 18"""
    with app.app_context():
        print("\n🚀 TESTING AI PREDICTIONS FOR ROUND 18")
        print("=" * 50)
        
        # Run AI predictions
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
            print(f"   • Average confidence: {sum(p['model_confidence'] for p in predictions)/len(predictions):.1%}")
            
            # Check AI bot's updated bankroll
            ai_bot = User.query.filter_by(username='LogisticsRegressionBot').first()
            if ai_bot:
                print(f"   • AI Bot bankroll after betting: ${ai_bot.bankroll}")
        
        else:
            print("❌ AI predictions failed!")
            return False
        
        return True

def main():
    """Main test function"""
    print("🏈 NRL AI PREDICTION SYSTEM - ROUND 18 TEST")
    print("=" * 60)
    
    # Create app once
    app = create_app()
    
    # Test 1: Check Round 18 data
    if not test_round18_data(app):
        print("❌ Data check failed - cannot proceed with AI predictions")
        return
    
    # Test 2: Run AI predictions
    if test_ai_predictions(app):
        # Test 3: Check AI bot bets
        if test_ai_bot_bets(app):
            print("\n✅ ALL TESTS PASSED!")
            print("🎉 AI prediction system is working correctly for Round 18!")
        else:
            print("\n⚠️  PREDICTION TESTS PASSED BUT BETTING VERIFICATION INCOMPLETE")
            print("🔧 Check the betting logic for potential issues.")
    else:
        print("\n❌ TESTS FAILED!")
        print("🔧 Check the logs for detailed error information.")

if __name__ == "__main__":
    main()
