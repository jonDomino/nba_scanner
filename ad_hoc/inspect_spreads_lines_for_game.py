"""
Smoke-test / debug script:
Print all Pinnacle-proxy (Unabated ms7 "Sharp Book Price") spread alt lines for a single game.

Usage:
  python ad_hoc/inspect_spreads_lines_for_game.py --away CHA --home MEM
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

project_root = Path(__file__).resolve().parents[1]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from core.reusable_functions import fetch_unabated_snapshot
from data_build.unabated_callsheet import extract_nba_games_today
from data_build.slate import get_today_games_with_fairs_and_kalshi_tickers
from spreads.builder import _extract_pinnacle_spreads_alt_lines_ms7, _pair_pinnacle_spreads_by_overround, PINNACLE_SPREADS_OVERROUND


PINNY_SPREADS_MSID = 7  # Unabated alias "Sharp Book Price"
OVERROUND_TARGET = 1.034


def american_to_prob(american: int) -> Optional[float]:
    try:
        o = int(american)
    except Exception:
        return None
    if o == 0:
        return None
    if o < 0:
        return (-o) / ((-o) + 100.0)
    return 100.0 / (o + 100.0)


def prob_to_american(p: float) -> Optional[int]:
    try:
        p = float(p)
    except Exception:
        return None
    if p <= 0.0 or p >= 1.0:
        return None
    if p >= 0.5:
        odds = -(100.0 * p) / (1.0 - p)
    else:
        odds = (100.0 * (1.0 - p)) / p
    return int(round(odds))


def estimate_missing_juice_from_known(known_american: int, overround_target: float = OVERROUND_TARGET) -> Optional[int]:
    p_known = american_to_prob(known_american)
    if p_known is None:
        return None
    p_missing = overround_target - p_known
    p_missing = max(1e-6, min(1.0 - 1e-6, p_missing))
    return prob_to_american(p_missing)


def _find_unabated_event_for_game(
    today_events: List[Dict[str, Any]],
    event_start: str,
    away_team_id: Optional[int],
    home_team_id: Optional[int],
) -> Optional[Dict[str, Any]]:
    for ev in today_events:
        if ev.get("eventStart") != event_start:
            continue
        et = ev.get("eventTeams", {})
        if not isinstance(et, dict):
            continue
        ids = set()
        for _, ti in et.items():
            if isinstance(ti, dict) and ti.get("id") is not None:
                ids.add(ti.get("id"))
        if away_team_id in ids and home_team_id in ids:
            return ev
    return None


def extract_ms7_spread_lines_raw(event: Dict[str, Any]) -> Dict[int, List[Tuple[float, int]]]:
    """
    Returns: team_id -> list of (spread_line, american)
    """
    market_lines = event.get("gameOddsMarketSourcesLines", {})
    if not isinstance(market_lines, dict):
        return {}

    event_teams = event.get("eventTeams", {})
    if not isinstance(event_teams, dict):
        return {}

    ms_keys = [k for k in market_lines.keys() if isinstance(k, str) and f":ms{PINNY_SPREADS_MSID}:" in k]
    out: Dict[int, List[Tuple[float, int]]] = {}

    def add(team_id: int, s: Optional[float], a: Optional[int]) -> None:
        if team_id is None or s is None or a is None:
            return
        out.setdefault(team_id, [])
        out[team_id].append((float(s), int(a)))

    for k in ms_keys:
        block = market_lines.get(k)
        if not isinstance(block, dict):
            continue
        try:
            side_token = k.split(":")[0]  # si1
            if not (side_token.startswith("si") and len(side_token) > 2):
                continue
            side_idx = int(side_token[2:])
        except Exception:
            continue

        team_info = event_teams.get(str(side_idx), {})
        if not isinstance(team_info, dict):
            continue
        team_id = team_info.get("id")
        if team_id is None:
            continue

        bt2 = block.get("bt2")
        if not isinstance(bt2, dict):
            continue

        s_raw = bt2.get("line") or bt2.get("spread") or bt2.get("value") or bt2.get("points")
        a_raw = bt2.get("americanPrice") or bt2.get("price") or bt2.get("unabatedPrice") or bt2.get("juice")

        s = None
        if s_raw is not None:
            try:
                s = float(str(s_raw).strip())
            except Exception:
                s = None
        a = None
        if a_raw is not None:
            try:
                a = int(str(a_raw).strip())
            except Exception:
                a = None
        add(team_id, s, a)

        alts = bt2.get("alternateLines")
        if isinstance(alts, list):
            for alt in alts:
                if not isinstance(alt, dict):
                    continue
                s_raw = alt.get("line") or alt.get("spread") or alt.get("value") or alt.get("points")
                a_raw = alt.get("americanPrice") or alt.get("price") or alt.get("unabatedPrice") or alt.get("juice")
                s = None
                if s_raw is not None:
                    try:
                        s = float(str(s_raw).strip())
                    except Exception:
                        s = None
                a = None
                if a_raw is not None:
                    try:
                        a = int(str(a_raw).strip())
                    except Exception:
                        a = None
                add(team_id, s, a)

    return out


def pair_by_magnitude(
    home_team_id: int,
    away_team_id: int,
    raw: Dict[int, List[Tuple[float, int]]],
) -> Dict[float, Dict[str, Any]]:
    """
    Returns magnitude -> {home_american, away_american, home_prob, away_prob, estimated_other_side}
    """
    out: Dict[float, Dict[str, Any]] = {}

    def upsert(side: str, spread_line: float, american: int) -> None:
        mag = abs(float(spread_line))
        out.setdefault(mag, {"home_american": None, "away_american": None})
        key = f"{side}_american"
        if out[mag].get(key) is None:
            out[mag][key] = int(american)

    for s, a in raw.get(home_team_id, []):
        upsert("home", s, a)
    for s, a in raw.get(away_team_id, []):
        upsert("away", s, a)

    for mag, d in out.items():
        h_am = d.get("home_american")
        a_am = d.get("away_american")
        estimated = False
        if h_am is None and a_am is not None:
            h_am = estimate_missing_juice_from_known(a_am)
            estimated = True
        if a_am is None and h_am is not None:
            a_am = estimate_missing_juice_from_known(h_am)
            estimated = True
        d["home_american"] = h_am
        d["away_american"] = a_am
        d["home_prob"] = american_to_prob(h_am) if h_am is not None else None
        d["away_prob"] = american_to_prob(a_am) if a_am is not None else None
        d["estimated_other_side"] = estimated

    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--away", required=True, help="Away team Kalshi/CollegeSheet code (e.g., CHA)")
    ap.add_argument("--home", required=True, help="Home team Kalshi/CollegeSheet code (e.g., MEM)")
    args = ap.parse_args()

    away_code = args.away.strip().upper()
    home_code = args.home.strip().upper()

    games = get_today_games_with_fairs_and_kalshi_tickers()
    target = None
    for g in games:
        if (g.get("kalshi_away_code"), g.get("kalshi_home_code")) == (away_code, home_code):
            target = g
            break

    if not target:
        print(f"Could not find game in today's slate: {away_code}@{home_code}")
        return

    print(f"Game: {away_code}@{home_code}")
    print(f"  event_start: {target.get('event_start')}")
    print(f"  event_ticker: {target.get('event_ticker')}")
    print(f"  away_team_id: {target.get('away_team_id')}, home_team_id: {target.get('home_team_id')}")
    print(f"  away_name: {target.get('away_team_name')}")
    print(f"  home_name: {target.get('home_team_name')}")

    snap = fetch_unabated_snapshot()
    today_events = extract_nba_games_today(snap)
    ev = _find_unabated_event_for_game(today_events, target.get("event_start"), target.get("away_team_id"), target.get("home_team_id"))
    if not ev:
        print("Could not match Unabated event for this game.")
        return

    raw = extract_ms7_spread_lines_raw(ev)
    away_id = int(target.get("away_team_id"))
    home_id = int(target.get("home_team_id"))

    print("\nRaw ms7 bt2/alternateLines by team_id:")
    for tid in [away_id, home_id]:
        lines = raw.get(tid, [])
        print(f"  team_id={tid}: {len(lines)} line(s)")
        for s, a in sorted(lines, key=lambda x: (abs(x[0]), x[0], x[1])):
            p = american_to_prob(a)
            p_str = f"{p:.4f}" if p is not None else "N/A"
            print(f"    spread={s:+.1f}  american={a:+d}  prob={p_str}")

    # Use production pairing logic (overround-based orientation selection)
    raw_by_mag = _extract_pinnacle_spreads_alt_lines_ms7(ev, home_id, away_id)
    paired = _pair_pinnacle_spreads_by_overround(raw_by_mag, PINNACLE_SPREADS_OVERROUND)

    print("\nPaired by magnitude (orientation chosen by overround; home may be +X if underdog):")
    print("  MAG | HOME_LINE  HOME_AM  HOME_P   || AWAY_LINE  AWAY_AM  AWAY_P   | ORIENT")
    for mag in sorted(paired.keys()):
        d = paired[mag]
        h_line = float(d.get("home_line"))
        a_line = float(d.get("away_line"))
        h_am = d.get("home_american")
        a_am = d.get("away_american")
        h_p = d.get("home_prob")
        a_p = d.get("away_prob")
        orient = d.get("orientation_used") or ""
        h_p_str = f"{h_p:.4f}" if h_p is not None else "N/A"
        a_p_str = f"{a_p:.4f}" if a_p is not None else "N/A"
        h_am_str = f"{int(h_am):+d}" if h_am is not None else "N/A"
        a_am_str = f"{int(a_am):+d}" if a_am is not None else "N/A"
        print(f" {mag:4.1f} | {h_line:+7.1f}  {h_am_str:>7}  {h_p_str:>7}  || {a_line:+7.1f}  {a_am_str:>7}  {a_p_str:>7} | {orient}")


if __name__ == "__main__":
    main()

