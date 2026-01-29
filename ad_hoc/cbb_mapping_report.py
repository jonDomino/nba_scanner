"""
Ad-hoc: CBB team mapping report (Kalshi -> Unabated).

Goal:
- Show which Kalshi CBB teams are being matched to Unabated teams (and with what score)
- Flag teams that need manual overrides in `team_xref_cbb_overrides.csv`

Usage:
  python ad_hoc/cbb_mapping_report.py
  python ad_hoc/cbb_mapping_report.py --limit 200 --min-score 0.86 --warn-score 0.90
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Make project imports work when executed from ad_hoc/
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from core.reusable_functions import fetch_unabated_snapshot  # noqa: E402
from utils.kalshi_api import load_creds  # noqa: E402
from cbb.kalshi_series import fetch_cbb_game_events  # noqa: E402
from cbb.team_mapping import load_cbb_overrides, parse_kalshi_matchup_title, best_match_unabated_team  # noqa: E402


def _unique_in_order(items: List[str]) -> List[str]:
    out: List[str] = []
    for x in items:
        if x and x not in out:
            out.append(x)
    return out


def _collect_kalshi_teams(game_events: List[Dict[str, Any]], limit: int) -> List[Tuple[str, str]]:
    """
    Return list of (kalshi_code, kalshi_display_name) pairs.
    """
    pairs: List[Tuple[str, str]] = []
    for ev in (game_events or [])[: max(0, int(limit))]:
        title = ev.get("title") or ""
        parsed = parse_kalshi_matchup_title(title)
        if not parsed:
            continue
        away_name, home_name = parsed
        away_code = (ev.get("away_code") or "").strip().upper()
        home_code = (ev.get("home_code") or "").strip().upper()
        if away_code and away_name:
            pairs.append((away_code, away_name))
        if home_code and home_name:
            pairs.append((home_code, home_name))

    # Deduplicate by code (keep first seen name)
    by_code: Dict[str, str] = {}
    for code, name in pairs:
        if code and code not in by_code:
            by_code[code] = name
    return [(c, by_code[c]) for c in sorted(by_code.keys())]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=250, help="How many Kalshi game events to scan.")
    ap.add_argument(
        "--min-score",
        type=float,
        default=0.86,
        help="Min score required for automatic fuzzy match (same default as code).",
    )
    ap.add_argument(
        "--warn-score",
        type=float,
        default=0.92,
        help="Warn if match score is below this (even if above min-score).",
    )
    args = ap.parse_args()

    api_key_id, private_key_pem = load_creds()
    snapshot = fetch_unabated_snapshot()
    teams_dict = snapshot.get("teams", {}) if isinstance(snapshot, dict) else {}

    overrides_path = PROJECT_ROOT / "team_xref_cbb_overrides.csv"
    overrides_by_code = load_cbb_overrides(overrides_path)

    game_events = fetch_cbb_game_events(api_key_id, private_key_pem) or []
    kalshi_teams = _collect_kalshi_teams(game_events, args.limit)

    # Report header
    print("CBB TEAM MAPPING REPORT")
    print(f"- kalshi events scanned: {min(len(game_events), args.limit)} (of {len(game_events)})")
    print(f"- unique kalshi teams found: {len(kalshi_teams)}")
    print(f"- overrides file: {overrides_path}")
    print(f"- overrides loaded: {len(overrides_by_code)}")
    print("")

    # Grouped rollups
    unmatched: List[Tuple[str, str]] = []
    low_conf: List[Tuple[str, str, str, float, bool]] = []
    matched: List[Tuple[str, str, str, float, bool]] = []

    for code, kalshi_name in kalshi_teams:
        override_name = overrides_by_code.get(code)
        m = best_match_unabated_team(
            kalshi_name,
            teams_dict,
            override_unabated_name=override_name,
            min_score=float(args.min_score),
        )
        if not m:
            unmatched.append((code, kalshi_name))
            continue
        used_override = override_name is not None
        rec = (code, kalshi_name, m.unabated_name, float(m.score), used_override)
        matched.append(rec)
        if (not used_override) and float(m.score) < float(args.warn_score):
            low_conf.append(rec)

    # Print main table (sorted by score asc, so the sketchy ones float to top)
    matched_sorted = sorted(matched, key=lambda r: (r[3], r[0]))
    print("MATCHES (lowest score first)")
    print("kalshi_code\tkalshi_name\tunabated_name\tscore\toverride_used")
    for code, kname, uname, score, used_override in matched_sorted:
        print(f"{code}\t{kname}\t{uname}\t{score:.3f}\t{int(used_override)}")
    print("")

    # Warnings / action items
    if low_conf:
        print("LOW CONFIDENCE (consider adding override)")
        print("kalshi_code\tkalshi_name\tunabated_name\tscore")
        for code, kname, uname, score, _ in sorted(low_conf, key=lambda r: (r[3], r[0])):
            print(f"{code}\t{kname}\t{uname}\t{score:.3f}")
        print("")

    if unmatched:
        print("UNMATCHED (override REQUIRED)")
        print("kalshi_code\tkalshi_name")
        for code, kname in unmatched:
            print(f"{code}\t{kname}")
        print("")

    # Helpful: template rows to paste into CSV
    if low_conf or unmatched:
        print("CSV TEMPLATE ROWS (paste into team_xref_cbb_overrides.csv and fill unabated_name)")
        print("kalshi_code,kalshi_name,unabated_name,notes")
        codes = _unique_in_order([c for c, _ in unmatched] + [c for c, _, _, _, _ in low_conf])
        kalshi_name_by_code = dict(kalshi_teams)
        for code in codes:
            nm = kalshi_name_by_code.get(code, "")
            print(f"{code},{nm},,todo")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

