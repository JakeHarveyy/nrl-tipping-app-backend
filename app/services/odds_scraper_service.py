# app/services/odds_scraper_service.py
"""
NRL.com Odds Scraper Service for NRL Tipping Application

Fetches decimal odds embedded in NRL.com draw page fixtures for the active or
next upcoming round. Reuses the existing NRL.com fetch/parse logic from
results_scraper_service and updates Match records with current odds, broadcasting
SSE events on change.
"""

# =============================================================================
# IMPORTS
# =============================================================================
from decimal import Decimal, InvalidOperation
from datetime import datetime, timezone, timedelta
import logging

from app.models import Match, Round
from app import db
from app.sse_events import announce_event
from app.services.results_scraper_service import _fetch_nrl_round_data_from_web

log = logging.getLogger(__name__)

# =============================================================================
# NRL.COM ODDS FETCHING
# =============================================================================

def fetch_nrl_odds_for_round(round_number, year):
    """
    Fetches decimal odds from the NRL.com draw page for a specific round.

    Args:
        round_number (int): The round number to fetch.
        year (int): The season year.

    Returns:
        list[dict] | None: List of dicts with keys:
            'home_team', 'away_team', 'home_odds', 'away_odds', 'start_time_dt'
        Returns None on fetch failure, empty list if no fixtures with odds found.
    """
    log.info(f"Fetching NRL.com odds for Round {round_number}, Year {year}")
    fixtures = _fetch_nrl_round_data_from_web(round_number, year)

    if fixtures is None:
        log.error(f"Failed to fetch NRL.com fixture data for Round {round_number}, Year {year}.")
        return None

    results = []
    for fixture in fixtures:
        if fixture.get("type") != "Match":
            continue

        home_team = fixture.get("homeTeam", {}).get("nickName", "").strip()
        away_team = fixture.get("awayTeam", {}).get("nickName", "").strip()
        kickoff_str = fixture.get("clock", {}).get("kickOffTimeLong")

        if not home_team or not away_team or not kickoff_str:
            log.warning(f"Skipping fixture missing team names or kickoff time: {fixture.get('matchCentreUrl', 'N/A')}")
            continue

        try:
            start_time_dt = datetime.fromisoformat(kickoff_str.replace("Z", "+00:00"))
        except ValueError:
            log.warning(f"Could not parse kickoff time '{kickoff_str}' for {home_team} vs {away_team}. Skipping.")
            continue

        home_odds = None
        away_odds = None
        try:
            raw_home = fixture.get("homeTeam", {}).get("odds")
            if raw_home:
                home_odds = Decimal(str(raw_home))
        except (InvalidOperation, TypeError):
            log.warning(f"Could not parse home odds '{fixture.get('homeTeam', {}).get('odds')}' for {home_team} vs {away_team}.")

        try:
            raw_away = fixture.get("awayTeam", {}).get("odds")
            if raw_away:
                away_odds = Decimal(str(raw_away))
        except (InvalidOperation, TypeError):
            log.warning(f"Could not parse away odds '{fixture.get('awayTeam', {}).get('odds')}' for {home_team} vs {away_team}.")

        results.append({
            "home_team": home_team,
            "away_team": away_team,
            "home_odds": home_odds,
            "away_odds": away_odds,
            "start_time_dt": start_time_dt,
        })

    log.info(f"Parsed {len(results)} fixtures with odds data for Round {round_number}, Year {year}.")
    return results

# =============================================================================
# DATABASE UPDATE FUNCTIONS
# =============================================================================

def update_matches_from_odds_scraper():
    """
    Main function to update database matches with current odds from NRL.com.
    Targets the Active round for the current year, falling back to the earliest
    Upcoming round. Broadcasts SSE events for any changed odds.
    """
    log.info("--- Starting Odds Update from NRL.com ---")

    current_year = datetime.now(timezone.utc).year

    # --- DETERMINE TARGET ROUND ---
    target_round = Round.query.filter_by(status="Active", year=current_year).first()
    if not target_round:
        target_round = (
            Round.query.filter_by(status="Upcoming", year=current_year)
            .order_by(Round.round_number.asc())
            .first()
        )

    if not target_round:
        log.info(f"No Active or Upcoming round found for year {current_year}. Skipping odds update.")
        return

    log.info(f"Targeting Round {target_round.round_number} ({target_round.year}) for odds update.")

    # --- SKIP IF ANY MATCH IN THE ROUND IS CURRENTLY LIVE ---
    live_matches = Match.query.filter(
        Match.round_id == target_round.round_id,
        Match.status == "Live",
    ).count()
    if live_matches > 0:
        log.info(f"Skipping odds update — {live_matches} match(es) currently Live in Round {target_round.round_number}.")
        return

    # --- FETCH NRL.COM ODDS ---
    fetched_fixtures = fetch_nrl_odds_for_round(target_round.round_number, target_round.year)

    if fetched_fixtures is None:
        log.error("Failed to fetch odds data from NRL.com. Aborting odds update.")
        return
    if not fetched_fixtures:
        log.info("No fixtures with odds returned from NRL.com for this round.")
        return

    matches_odds_updated_count = 0
    matches_not_found_in_db = 0
    updated_match_details_for_sse = []

    # --- PROCESS EACH FETCHED FIXTURE ---
    for fixture_data in fetched_fixtures:
        try:
            f_home_team = fixture_data["home_team"]
            f_away_team = fixture_data["away_team"]
            f_start_time_dt = fixture_data["start_time_dt"]
            f_home_odds = fixture_data["home_odds"]
            f_away_odds = fixture_data["away_odds"]

            # Find match in DB by team names (case-insensitive) within a ±2 h time window
            time_window_start = f_start_time_dt - timedelta(hours=2)
            time_window_end = f_start_time_dt + timedelta(hours=2)

            candidate_matches = Match.query.filter(
                Match.start_time >= time_window_start,
                Match.start_time <= time_window_end,
            ).all()

            found_db_match = None
            for m in candidate_matches:
                if m.home_team.lower() == f_home_team.lower() and \
                   m.away_team.lower() == f_away_team.lower():
                    found_db_match = m
                    break

            if not found_db_match:
                log.warning(f"Could not find DB match for: {f_home_team} vs {f_away_team} around {f_start_time_dt}")
                matches_not_found_in_db += 1
                continue

            # --- UPDATE ODDS IF CHANGED ---
            update_needed = False

            if (f_home_odds is not None and found_db_match.home_odds != f_home_odds) or \
               (f_home_odds is None and found_db_match.home_odds is not None):
                found_db_match.home_odds = f_home_odds
                update_needed = True

            if (f_away_odds is not None and found_db_match.away_odds != f_away_odds) or \
               (f_away_odds is None and found_db_match.away_odds is not None):
                found_db_match.away_odds = f_away_odds
                update_needed = True

            if update_needed:
                found_db_match.last_odds_update = datetime.now(timezone.utc)
                db.session.add(found_db_match)
                matches_odds_updated_count += 1
                log.info(
                    f"Updated odds for DB Match ID {found_db_match.match_id} "
                    f"({found_db_match.home_team} vs {found_db_match.away_team}): "
                    f"home={f_home_odds}, away={f_away_odds}"
                )
                updated_match_details_for_sse.append({
                    "match_id": found_db_match.match_id,
                    "home_odds": float(found_db_match.home_odds) if found_db_match.home_odds else None,
                    "away_odds": float(found_db_match.away_odds) if found_db_match.away_odds else None,
                })

        except Exception as e:
            log.error(f"Error processing fixture data {fixture_data}: {e}", exc_info=True)
            db.session.rollback()
            continue

    # --- COMMIT AND BROADCAST SSE EVENTS ---
    try:
        if matches_odds_updated_count > 0:
            db.session.commit()
            log.info(f"DB commit successful. {matches_odds_updated_count} match(es) odds updated.")
            for detail in updated_match_details_for_sse:
                announce_event("odds_update", detail)
        else:
            log.info("No odds changes detected; no database commit required.")
    except Exception as e:
        db.session.rollback()
        log.error(f"DB commit failed for odds update: {e}", exc_info=True)

    log.info(
        f"--- Finished Odds Update from NRL.com. "
        f"Updated: {matches_odds_updated_count}, Not Found: {matches_not_found_in_db} ---"
    )
