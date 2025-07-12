# app/api/settlement.py
from app.models import Match, Bet, User, BankrollHistory
from app import db
from decimal import Decimal
from datetime import datetime, timezone
from app.sse_events import announce_event
import logging

log = logging.getLogger(__name__)

def settle_bets_for_match(match_id, home_score, away_score):
    """
    Settles all pending bets for a given match after results are known.
    Updates match status, bet statuses, user bankrolls, and bankroll history.
    Returns a tuple: (success_boolean, message_string)
    """
    log.info(f"Attempting to settle match ID: {match_id} with score {home_score}-{away_score}")

    match = Match.query.get(match_id)
    if not match:
        return False, f"Match ID {match_id} not found."

    if match.status == 'Completed':
         return False, f"Match ID {match_id} has already been settled."

    winner = None
    if home_score > away_score:
        winner = match.home_team
    elif away_score > home_score:
        winner = match.away_team
    else:
        winner = 'Draw' # Explicitly handle draw
    log.info(f"Match {match_id}: Winner determined as '{winner}'")

    pending_bets = match.bets.filter(Bet.status == 'Pending').all()
    log.info(f"Found {len(pending_bets)} pending bets for match ID: {match_id}")

    affected_users_for_sse = {} 

    try:
        # --- Update Match Record ---
        match.result_home_score = home_score
        match.result_away_score = away_score
        match.winner = winner
        match.status = 'Completed'
        db.session.add(match) 

        # --- Process Each Bet ---
        for bet in pending_bets:
            user = bet.user 
            if not user:
                 print(f"WARNING: User not found for Bet ID {bet.bet_id}, skipping settlement for this bet.")
                 continue 

            previous_balance = user.bankroll
            new_bet_status = 'Lost' # Default to Lost
            payout_amount = Decimal('0.00')
            history_type = 'Bet Loss'

            # --- Determine Bet Outcome ---
            if winner == 'Draw':
                # Rule: Bets are void (push) on a draw
                new_bet_status = 'Void'
                payout_amount = bet.amount # Refund stake
                history_type = 'Bet Void'
                print(f"Bet ID {bet.bet_id}: Draw - Voiding bet, refunding {payout_amount}")
            elif bet.team_selected == winner:
                # Bet won
                new_bet_status = 'Won'
                payout_amount = bet.potential_payout # Payout includes stake
                history_type = 'Bet Win'
                print(f"Bet ID {bet.bet_id}: Won - Payout {payout_amount}")
            else:
                # Bet lost (already default)
                print(f"Bet ID {bet.bet_id}: Lost")


            # --- Update Bet Status ---
            bet.status = new_bet_status
            bet.settlement_time = datetime.now(timezone.utc)
            db.session.add(bet) 

            new_balance = previous_balance + payout_amount
            user.bankroll = new_balance 

            history_entry = BankrollHistory(
                user_id=user.user_id,
                round_number=match.round.round_number,
                change_type=history_type,
                related_bet_id=bet.bet_id,
                amount_change=payout_amount,
                previous_balance=previous_balance,
                new_balance=new_balance,
                timestamp=bet.settlement_time
            )
            db.session.add(history_entry)
            log.info(f"   User {user.username}: Bankroll {previous_balance} -> {new_balance} (+{payout_amount}). History logged.")

             # --- Store user for SSE if bankroll changed ---
            if payout_amount > 0 or history_type == 'Bet Loss': 
                affected_users_for_sse[user.user_id] = new_balance

            db.session.commit()
            log.info(f"Successfully settled match {match_id} and {len(pending_bets)} bets.")

            # --- Announce bankroll updates AFTER successful commit ---
            for user_id, final_bankroll in affected_users_for_sse.items():
                announce_event('bankroll_update', {
                    'user_id': user_id, 
                    'new_bankroll': float(final_bankroll), 
                    'reason': 'bet_settlement',
                    'match_id': match_id
                })
            
            return True, f"Match {match_id} settled. Winner: {winner}. {len(pending_bets)} bets processed."

    except Exception as e:
        db.session.rollback()
        log.error(f"ERROR during settlement for match ID {match_id}: {e}", exc_info=True)
        return False, f"An error occurred during settlement for match {match_id}."
