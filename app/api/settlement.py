# app/api/settlement.py
from app.models import Match, Bet, User, BankrollHistory
from app import db
from decimal import Decimal
from datetime import datetime, timezone

def settle_bets_for_match(match_id, home_score, away_score):
    """
    Settles all pending bets for a given match after results are known.
    Updates match status, bet statuses, user bankrolls, and bankroll history.
    Returns a tuple: (success_boolean, message_string)
    """
    print(f"Attempting to settle match ID: {match_id} with score {home_score}-{away_score}")

    match = Match.query.get(match_id)
    if not match:
        return False, f"Match ID {match_id} not found."

    if match.status == 'Completed':
         return False, f"Match ID {match_id} has already been settled."

    # Determine winner
    winner = None
    if home_score > away_score:
        winner = match.home_team
    elif away_score > home_score:
        winner = match.away_team
    else:
        winner = 'Draw' # Explicitly handle draw

    print(f"Match {match_id}: Winner determined as '{winner}'")

    # Fetch pending bets for this match
    # Use with_for_update() to lock rows during transaction if high concurrency expected (maybe overkill here)
    pending_bets = match.bets.filter(Bet.status == 'Pending').all() # Using relationship query
    # Or: Bet.query.filter_by(match_id=match_id, status='Pending').all()

    print(f"Found {len(pending_bets)} pending bets for match ID: {match_id}")

    try:
        # --- Update Match Record ---
        match.result_home_score = home_score
        match.result_away_score = away_score
        match.winner = winner
        match.status = 'Completed'
        db.session.add(match) # Add updated match to session

        # --- Process Each Bet ---
        for bet in pending_bets:
            user = bet.user # Get user via relationship
            if not user:
                 print(f"WARNING: User not found for Bet ID {bet.bet_id}, skipping settlement for this bet.")
                 continue # Skip this bet if user somehow doesn't exist

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
            db.session.add(bet) # Add updated bet to session

            # --- Update Bankroll & History (if payout > 0 OR if logging losses) ---
            if payout_amount > 0 or history_type == 'Bet Loss': # Log even if $0 change for losses
                amount_change = payout_amount - bet.amount if new_bet_status != 'Void' else payout_amount # Net change (Win adds profit, Void adds stake back)
                # For 'Bet Loss', amount_change should be 0 based on payout_amount = 0
                # For 'Bet Void', payout_amount is stake, so amount_change is stake (refund)
                # For 'Bet Win', payout_amount is (stake * odds), so amount_change is profit (stake * (odds-1))
                # Let's simplify: amount_change will be the actual *addition* to the bankroll.
                # If Win: payout_amount = total return. Bankroll increases by payout_amount. Change = payout_amount.
                # If Void: payout_amount = stake. Bankroll increases by stake. Change = stake.
                # If Loss: payout_amount = 0. Bankroll increases by 0. Change = 0.

                bankroll_change = payout_amount # The actual amount to add back/pay out
                new_balance = previous_balance + bankroll_change

                # Check if this is correct - a WIN should only increase by the PROFIT, not the full payout,
                # because the stake was already removed.
                # Let's recalculate bankroll change based on *profit*
                # Profit = Payout - Stake amount
                # If Win: profit = potential_payout - bet.amount
                # If Void: profit = bet.amount - bet.amount = 0 (but we add back the stake)
                # If Loss: profit = 0 - bet.amount = -bet.amount (no change needed as stake already gone)

                # Revised logic for bankroll update:
                if new_bet_status == 'Won':
                    bankroll_addition = bet.potential_payout # Add full payout (includes original stake back + profit)
                elif new_bet_status == 'Void':
                    bankroll_addition = bet.amount # Add stake back
                else: # Lost
                    bankroll_addition = Decimal('0.00') # No change, stake already gone

                new_balance = previous_balance + bankroll_addition
                user.bankroll = new_balance # Update user object

                # Create history entry
                history_entry = BankrollHistory(
                    user_id=user.user_id,
                    round_number=match.round.round_number,
                    change_type=history_type,
                    related_bet_id=bet.bet_id,
                    amount_change=bankroll_addition, # Log the actual amount added to bankroll
                    previous_balance=previous_balance,
                    new_balance=new_balance,
                    timestamp=bet.settlement_time
                )
                db.session.add(history_entry)
                print(f"   User {user.username}: Bankroll {previous_balance} -> {new_balance} (+{bankroll_addition}). History logged.")

        # --- Commit Transaction ---
        db.session.commit()
        print(f"Successfully settled match {match_id} and {len(pending_bets)} bets.")
        return True, f"Match {match_id} settled successfully. Winner: {winner}. {len(pending_bets)} bets processed."

    except Exception as e:
        db.session.rollback() # Rollback ALL changes if any error occurs
        print(f"ERROR during settlement for match ID {match_id}: {e}")
        import traceback
        traceback.print_exc()
        return False, f"An error occurred during settlement for match {match_id}."