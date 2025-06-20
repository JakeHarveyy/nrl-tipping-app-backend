from app.models import User, BankrollHistory, Round
from app import db
from decimal import Decimal
from datetime import datetime, timezone
from app.sse_events import announce_event
import logging

log = logging.getLogger(__name__)

def process_round_start(round_obj: Round):
    """
    Processes the start of a given round:
    - Adds $1000 bonus to active users if not already applied for this round.
    - Logs the addition in BankrollHistory.
    - Returns True if successful, False otherwise.
    """
    if not round_obj:
        log.info("ERROR: process_round_start called with None round_obj")
        return False

    round_number = round_obj.round_number
    log.info(f"--- Processing Start of Round {round_number} ---")

    users = User.query.filter_by(active=True).all()
    

    if not users:
        print(f"No active users found for Round {round_number} start.")
        return True # Not an error if no users exist

    print(f"Found {len(users)} active users for Round {round_number} update.")
    added_amount = Decimal('1000.00')
    success_count = 0
    already_processed_count = 0

    for user in users:
        # --- Idempotency Check ---
        existing_bonus = BankrollHistory.query.filter_by(
            user_id=user.user_id,
            round_number=round_number,
            change_type='Weekly Addition'
        ).first()

        if existing_bonus:
            # print(f"User {user.username} (ID: {user.user_id}) already received bonus for Round {round_number}.")
            already_processed_count += 1
            continue # Skip if bonus already applied for this round

        # --- Apply Bonus ---
        try:
            previous_balance = user.bankroll
            new_balance = previous_balance + added_amount

            # Update user's bankroll
            user.bankroll = new_balance

            # Create history entry
            history_entry = BankrollHistory(
                user_id=user.user_id,
                round_number=round_number, # Use the actual round number
                change_type='Weekly Addition',
                related_bet_id=None,
                amount_change=added_amount,
                previous_balance=previous_balance,
                new_balance=new_balance,
                timestamp=datetime.now(timezone.utc) # Timestamp of bonus application
            )
            db.session.add(history_entry)
            # Commit per user to isolate failures, though less performant for many users
            db.session.commit()
            log.info(f"Applied bonus for user {user.username} (ID: {user.user_id}). New balance: {new_balance}")
            success_count += 1

            # --- Announce bankroll update AFTER successful commit ---
            announce_event('bankroll_update', {
                'user_id': user.user_id,
                'new_bankroll': float(new_balance),
                'reason': 'weekly_bonus',
                'round_number': round_number
            })

        except Exception as e:
            db.session.rollback()
            print(f"ERROR applying bonus for user {user.username} (ID: {user.user_id}) for Round {round_number}: {e}")
            # Optionally: Log this error more formally

    print(f"--- Finished Processing Round {round_number}. Applied: {success_count}, Already Processed: {already_processed_count} ---")
    return True # Indicate processing completed (even if some users failed)