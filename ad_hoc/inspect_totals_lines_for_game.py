"""
Smoke test: print all totals lines (main + alternates) for a specific game from Unabated snapshot.

Focus: Pinnacle market sources if present:
- ms70: Pinnacle - 3838
- ms58: Pinnacle - Delayed

This prints, per total points:
- line (points)
- over_juice (American odds)
- under_juice (American odds)

Notes:
- Unabated snapshot stores totals (bt3) under event["gameOddsMarketSourcesLines"] keys like "si0:ms49:an0".
- Totals often appear in BOTH "si0" and "si1" blocks; we pair them by points to get (over, under).
- If the payload doesn't label over/under explicitly, we use a heuristic:
  - For alt points > main points, OVER should be the cheaper side (less negative), UNDER more expensive.
  - For alt points < main points, OVER should be the more expensive side (more negative).

Usage:
  python ad_hoc/inspect_totals_lines_for_game.py
"""

from __future__ import annotations

import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

# Add project root to Python path (so `core/`, `utils/`, etc. import cleanly)
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from core.reusable_functions import fetch_unabated_snapshot  # noqa: E402


PINNACLE_KEYWORDS = ["pinnacle"]
DEFAULT_OVERROUND = 1.034  # sum of implied probs per (over, under) pair
REFERENCE_MSIDS = [7, 49]  # Sharp Book Price + Unabated (useful baseline)


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def _iter_events(snapshot: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
    goe = snapshot.get("gameOddsEvents", {})
    if not isinstance(goe, dict):
        return
    for _, events in goe.items():
        if isinstance(events, list):
            for ev in events:
                if isinstance(ev, dict):
                    yield ev


def _team_names_for_event(event: Dict[str, Any], teams_dict: Dict[str, Any]) -> List[str]:
    out: List[str] = []
    event_teams = event.get("eventTeams", {})
    if not isinstance(event_teams, dict):
        return out
    for _, info in event_teams.items():
        if not isinstance(info, dict):
            continue
        tid = info.get("id")
        if tid is None:
            continue
        tinfo = teams_dict.get(str(tid)) or teams_dict.get(tid) or {}
        if isinstance(tinfo, dict):
            name = tinfo.get("name") or tinfo.get("teamName")
            if isinstance(name, str) and name.strip():
                out.append(name.strip())
    return out


def _find_game_event(
    snapshot: Dict[str, Any],
    team_a: str,
    team_b: str,
) -> Optional[Dict[str, Any]]:
    teams_dict = snapshot.get("teams", {}) or {}
    na = _norm(team_a)
    nb = _norm(team_b)
    for ev in _iter_events(snapshot):
        names = _team_names_for_event(ev, teams_dict)
        nset = {_norm(x) for x in names}
        if na in nset and nb in nset:
            return ev
    return None


def _extract_points_and_price(line: Dict[str, Any]) -> Tuple[Optional[float], Optional[int]]:
    # points / total / line / value (varies)
    pts_raw = (
        line.get("points")
        or line.get("total")
        or line.get("line")
        or line.get("value")
        or line.get("overUnder")
    )
    pts: Optional[float] = None
    if pts_raw is not None:
        try:
            pts = float(str(pts_raw).strip())
        except Exception:
            pts = None

    price_raw = line.get("americanPrice") or line.get("price") or line.get("unabatedPrice")
    price: Optional[int] = None
    if price_raw is not None:
        try:
            price = int(str(price_raw).strip())
        except Exception:
            price = None

    return pts, price


def _alt_lines(line: Dict[str, Any]) -> List[Dict[str, Any]]:
    alts = line.get("alternateLines")
    return alts if isinstance(alts, list) else []


def _ms_keys(market_lines: Dict[str, Any], msid: int) -> List[str]:
    token = f":ms{msid}:"
    return [k for k in market_lines.keys() if isinstance(k, str) and token in k]


def _american_to_prob(odds: int) -> Optional[float]:
    try:
        o = int(odds)
    except Exception:
        return None
    if o == 0:
        return None
    if o < 0:
        return (-o) / ((-o) + 100.0)
    return 100.0 / (o + 100.0)


def _prob_to_american(p: float) -> Optional[int]:
    try:
        p = float(p)
    except Exception:
        return None
    if p <= 0.0 or p >= 1.0:
        return None
    if p >= 0.5:
        odds = - (100.0 * p) / (1.0 - p)
    else:
        odds = (100.0 * (1.0 - p)) / p
    return int(round(odds))


def _pair_over_under(
    pts: float,
    main_pts: Optional[float],
    prices: List[int],
) -> Tuple[Optional[int], Optional[int], bool]:
    """
    Given two prices for the same pts, decide (over, under).
    If we can't confidently infer, return (min_price, max_price) as a stable ordering.
    """
    if len(prices) < 2:
        # If only one side is present, estimate the other side using the overround assumption.
        if not prices:
            return (None, None, False)
        one = prices[0]
        p_one = _american_to_prob(one)
        if p_one is None:
            return (one, None, False)
        p_other = DEFAULT_OVERROUND - p_one
        other = _prob_to_american(p_other) if 0.0 < p_other < 1.0 else None
        return (one, other, True)

    a, b = prices[0], prices[1]
    # "Cheaper" in American odds: closer to 0 (e.g., -103 is cheaper than -117).
    cheaper = a if abs(a) < abs(b) else b
    expensive = b if cheaper == a else a

    if main_pts is None:
        # Unknown direction; just return cheaper as over (arbitrary but stable)
        return cheaper, expensive, False

    if pts > main_pts:
        # Higher total => OVER less likely => OVER should be cheaper
        return cheaper, expensive, False
    if pts < main_pts:
        # Lower total => OVER more likely => OVER should be more expensive
        return expensive, cheaper, False

    # At-the-main: can't infer, keep stable ordering
    return cheaper, expensive, False


def print_totals_for_event(snapshot: Dict[str, Any], event: Dict[str, Any]) -> None:
    teams_dict = snapshot.get("teams", {}) or {}
    names = _team_names_for_event(event, teams_dict)
    print("=" * 100)
    print("Totals lines for game:")
    print("  " + " @ ".join(names) if len(names) == 2 else "  " + ", ".join(names))
    print(f"  eventStart: {event.get('eventStart')}")
    print("=" * 100)

    market_lines = event.get("gameOddsMarketSourcesLines", {})
    if not isinstance(market_lines, dict):
        print("No gameOddsMarketSourcesLines in event.")
        return

    msids_present = sorted({int(m.split(":ms")[1].split(":")[0]) for m in market_lines.keys() if isinstance(m, str) and ":ms" in m})
    print(f"MarketSourceIds present in this event payload: {msids_present}")
    print()

    # marketSources is typically a list of {id, name, ...}
    ms_list = snapshot.get("marketSources", []) or []
    ms_name_by_id = {int(x.get("id")): x.get("name") for x in ms_list if isinstance(x, dict) and x.get("id") is not None}

    # Determine all Pinnacle msids from marketSources (and show totals for each)
    pinny_msids = sorted([
        msid for msid, name in ms_name_by_id.items()
        if isinstance(name, str) and any(k in name.lower() for k in PINNACLE_KEYWORDS)
    ])
    msids_to_check = [*pinny_msids, *REFERENCE_MSIDS]

    printed_any = False
    for msid in msids_to_check:
        keys = _ms_keys(market_lines, msid)
        if not keys:
            continue
        printed_any = True
        ms_name = ms_name_by_id.get(msid)
        label = f"ms{msid}" + (f" ({ms_name})" if ms_name else "")
        print(f"--- {label} ---")

        # Collect totals lines per points -> list of prices
        prices_by_pts: Dict[float, List[int]] = defaultdict(list)

        # Determine "main" points (first bt3 points we see)
        main_pts: Optional[float] = None

        for k in keys:
            block = market_lines.get(k)
            if not isinstance(block, dict):
                continue
            bt3 = block.get("bt3")
            if not isinstance(bt3, dict):
                continue

            pts, price = _extract_points_and_price(bt3)
            if pts is not None and price is not None:
                prices_by_pts[pts].append(price)
                if main_pts is None:
                    main_pts = pts

            # Alternate lines
            for alt in _alt_lines(bt3):
                if not isinstance(alt, dict):
                    continue
                a_pts, a_price = _extract_points_and_price(alt)
                if a_pts is not None and a_price is not None:
                    prices_by_pts[a_pts].append(a_price)

        if not prices_by_pts:
            print("  No bt3 totals found.")
            print()
            continue

        print(f"  main_points (heuristic): {main_pts}")
        print(f"{'LINE':>7}  {'OVER':>9}  {'UNDER':>9}")
        for pts in sorted(prices_by_pts.keys()):
            prices = prices_by_pts[pts]
            # de-dupe while preserving order
            dedup: List[int] = []
            for p in prices:
                if p not in dedup:
                    dedup.append(p)
            over, under, estimated = _pair_over_under(pts, main_pts, dedup[:2])
            over_s = f"{over:+d}" if isinstance(over, int) else ""
            under_s = f"{under:+d}" if isinstance(under, int) else ""
            if estimated:
                if over_s:
                    over_s += "*"
                if under_s:
                    under_s += "*"
            print(f"{pts:7.1f}  {over_s:>9}  {under_s:>9}")
        print()

    if not printed_any:
        print("No totals found for the requested msids in this event payload.")

    # If no Pinnacle keys, print a quick hint
    if not any(_ms_keys(market_lines, msid) for msid in pinny_msids):
        print("NOTE: No Pinnacle msids were present in this event payload's gameOddsMarketSourcesLines.")
        if pinny_msids:
            print(f"      Pinnacle msids known from marketSources: {pinny_msids}")
        print("      Output above shows the best available sources Unabated included for this game (e.g., ms7 Sharp Book Price, ms49 Unabated).")


def main() -> None:
    snapshot = fetch_unabated_snapshot()
    teams_dict = snapshot.get("teams", {}) or {}

    # The user asked for Lakers @ Cavaliers specifically
    event = _find_game_event(snapshot, "Los Angeles Lakers", "Cleveland Cavaliers")
    if not event:
        # Try shorter names just in case
        event = _find_game_event(snapshot, "Lakers", "Cavaliers")

    if not event:
        print("Could not find Lakers/Cavaliers game in the current snapshot.")
        return

    # Print totals for that event
    print_totals_for_event(snapshot, event)


if __name__ == "__main__":
    main()

