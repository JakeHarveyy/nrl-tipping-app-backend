# app/utils/text_utils.py
import logging

log = logging.getLogger(__name__)

# --- TEAM NAME MAPPING ---
# This map should aim to convert various scraper names to a SINGLE canonical name
# that you will store in your database (preferably the official NRL.com nickname).
# Keys should be lowercase versions of names found in scrapers.
# Values should be the canonical name you want to use.

TEAM_NAME_MAP = {
    # NRL.com Nicknames (these are good canonical names)
    "sharks": "Sharks",
    "eels": "Eels",
    "roosters": "Roosters",
    "dolphins": "Dolphins", # NRL.com uses "Dolphins"
    "rabbitohs": "Rabbitohs",
    "knights": "Knights",
    "warriors": "Warriors",
    "cowboys": "Cowboys",
    "wests tigers": "Wests Tigers",
    "dragons": "Dragons",
    "titans": "Titans",
    "bulldogs": "Bulldogs",
    "panthers": "Panthers",
    "broncos": "Broncos",
    "storm": "Storm",
    "raiders": "Raiders",
    "sea eagles": "Sea Eagles", # Manly

    # Variations from Pinnacle (or other scrapers) - map them to the canonical names above
    "cronulla sharks": "Sharks",
    "parramatta eels": "Eels",
    "sydney roosters": "Roosters",
    "redcliffe dolphins": "Dolphins", # Pinnacle uses "Redcliffe Dolphins"
    "the dolphins": "Dolphins",
    "south sydney rabbitohs": "Rabbitohs",
    "newcastle knights": "Knights",
    "new zealand warriors": "Warriors",
    "north queensland cowboys": "Cowboys",
    # "wests tigers" is usually consistent
    "st george illawarra dragons": "Dragons",
    "gold coast titans": "Titans",
    "canterbury bulldogs": "Bulldogs",
    "canterbury-bankstown bulldogs": "Bulldogs",
    "penrith panthers": "Panthers",
    "brisbane broncos": "Broncos",
    "melbourne storm": "Storm", # Usually consistent
    "canberra raiders": "Raiders", # Usually consistent
    "manly warringah sea eagles": "Sea Eagles",
    "manly-warringah sea eagles": "Sea Eagles",
    "manly sea eagles": "Sea Eagles",

    # Add any other variations you encounter from different scrapers
    # Example: If a scraper just says "Tigers"
    "tigers": "Wests Tigers",
}

def normalize_team_name(name: str) -> str:
    """
    Standardizes team names using the TEAM_NAME_MAP.
    Converts to lowercase, strips whitespace, and looks up in the map.
    If not found in map, returns the original name (after stripping and title casing).
    """
    if not name or not isinstance(name, str):
        log.warning(f"Invalid team name received for normalization: {name}")
        return "Unknown Team" # Or raise an error

    processed_name = name.lower().strip()
    canonical_name = TEAM_NAME_MAP.get(processed_name)

    if canonical_name:
        return canonical_name
    else:
        # If not in map, maybe it's already a canonical name or a new variation
        # Return the original name, but consistently cased (e.g., Title Case)
        # This helps if a new team appears or a variation isn't mapped yet.
        log.warning(f"Team name '{name}' (processed: '{processed_name}') not found in TEAM_NAME_MAP. Returning title-cased original.")
        return name.strip().title() # Example: "new team" -> "New Team"