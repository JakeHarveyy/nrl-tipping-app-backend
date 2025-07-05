# app/services/betting_service.py
from app.models import User, Match, Bet, BankrollHistory
from app import db
from decimal import Decimal
from datetime import datetime, timezone

def place_bet_for_user(user: User, match: Match, team_selected: str, bet_amount: Decimal):
    """
    Core service function to place a bet for a given user.
    Contains all validation and database transaction logic.

    Returns:
        (bool, str | Bet): A tuple of (success_boolean, message_or_bet_object).
    """
    # --- All validations from your PlaceBet API resource go here ---
    if not user or not match or not team_selected or not bet_amount:
        return False, "Invalid arguments provided to place_bet service."

    if match.start_time <= datetime.now(timezone.utc) or match.status != 'Scheduled':
        return False, f"Betting closed for this match (Status: {match.status})."

    selected_odds = None
    if team_selected == match.home_team: selected_odds = match.home_odds
    elif team_selected == match.away_team: selected_odds = match.away_odds
    else: return False, f"Invalid team selected: {team_selected}."

    if selected_odds is None: return False, "Odds not available for the selected team."
    if bet_amount <= 0: return False, "Bet amount must be positive."
    if user.bankroll < bet_amount: return False, f"Insufficient funds. Balance: ${user.bankroll:.2f}"
    # --- End Validations ---

    potential_payout = bet_amount * selected_odds

    try:
        previous_balance = user.bankroll
        user.bankroll -= bet_amount # Deduct from user

        new_bet = Bet(
            user_id=user.user_id, match_id=match.match_id, round_id=match.round_id,
            team_selected=team_selected, amount=bet_amount,
            odds_at_placement=selected_odds, potential_payout=potential_payout
        )
        db.session.add(new_bet)
        db.session.flush() # Get the new_bet.bet_id

        history_entry = BankrollHistory(
            user_id=user.user_id, round_number=match.round.round_number,
            change_type='Bet Placement', related_bet_id=new_bet.bet_id,
            amount_change=-bet_amount, previous_balance=previous_balance,
            new_balance=user.bankroll
        )
        db.session.add(history_entry)
        db.session.commit() # Commit all changes
        return True, new_bet # Return success and the created bet object
    except Exception as e:
        db.session.rollback()
        return False, f"Database error during bet placement: {e}"