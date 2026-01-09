"""
Team xref loading and mapping helpers.
"""

import csv
from typing import Dict, Optional

from data_build import config


def load_team_xref(path: str = None) -> Dict[str, str]:
    """
    Load NBA team xref CSV mapping Unabated team names to Kalshi codes.
    
    CSV format: league,unabated_name,kalshi_code
    
    Returns:
        Dict mapping normalized Unabated team name -> Kalshi code
    """
    if path is None:
        path = config.NBA_XREF_FILE
    
    xref = {}
    try:
        with open(path, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            
            for row in reader:
                # Handle BOM and header variants
                league = (
                    row.get("league") or 
                    row.get("\ufeffleague") or 
                    row.get("League") or 
                    ""
                ).strip()
                
                if league.upper() != "NBA":
                    continue
                
                unabated_name = (
                    row.get("unabated_name") or 
                    row.get("unabatedName") or 
                    row.get("Unabated_Name") or
                    row.get("\ufeffunabated_name") or
                    ""
                ).strip()
                
                kalshi_code = (
                    row.get("kalshi_code") or 
                    row.get("kalshiCode") or 
                    row.get("Kalshi_Code") or
                    row.get("\ufeffkalshi_code") or
                    ""
                ).strip().upper()
                
                if unabated_name and kalshi_code:
                    # Normalize name for matching (lowercase, strip)
                    normalized = unabated_name.lower().strip()
                    xref[normalized] = kalshi_code
    except FileNotFoundError:
        print(f"Warning: Team xref file not found: {path}")
    
    return xref


def map_unabated_to_kalshi_code(team_name: str, team_id: Optional[int], xref: Dict[str, str]) -> Optional[str]:
    """
    Map Unabated team name/ID to Kalshi code using xref CSV.
    
    Args:
        team_name: Unabated team display name
        team_id: Unabated team ID (currently not used in CSV)
        xref: Team xref dict (normalized name -> kalshi_code)
    
    Returns:
        Kalshi code (3-letter) or None if not found
    """
    if not team_name:
        return None
    
    # Normalize team name for matching
    normalized = team_name.lower().strip()
    
    # Direct lookup
    kalshi_code = xref.get(normalized)
    if kalshi_code:
        return kalshi_code
    
    # Try partial match (in case names differ slightly)
    for unabated_name, code in xref.items():
        if normalized in unabated_name or unabated_name in normalized:
            return code
    
    return None