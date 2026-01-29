"""
Poll Unabated snapshot for ms7 ("Sharp Book Price") TOTALS alternate lines for a given game.

Default use-case: check whether Unabated ms7 totals alts are updating over time.

Run (example):
  python ad_hoc/poll_unabated_totals_alts.py --away HOU --home ATL --interval 10

Stop with Ctrl+C.
"""

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Ensure project root is importable when running from ad_hoc/
project_root = Path(__file__).resolve().parents[1]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from core.reusable_functions import fetch_unabated_snapshot  # noqa: E402
from data_build.unabated_callsheet import extract_nba_games_today, get_team_name  # noqa: E402
from totals.builder import _extract_pinnacle_totals_alt_lines_ms7  # noqa: E402


def _load_code_to_unabated_name(xref_csv_path: Path) -> Dict[str, str]:
    """
    Load `team_xref_nba.csv` mapping Kalshi/CollegeSheet code -> Unabated name.
    """
    import csv

    out: Dict[str, str] = {}
    if not xref_csv_path.exists():
        return out
    with xref_csv_path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                league = (row.get("league") or "").strip()
                if league and league.upper() != "NBA":
                    continue
                code = (row.get("kalshi_code") or "").strip().upper()
                unab = (row.get("unabated_name") or "").strip()
                if code and unab and code not in out:
                    out[code] = unab
            except Exception:
                continue
    return out


def _event_team_names(ev: Dict[str, Any], teams_dict: Dict[str, Any]) -> List[str]:
    et = ev.get("eventTeams", {})
    names: List[str] = []
    if isinstance(et, dict):
        for _, ti in et.items():
            if isinstance(ti, dict) and ti.get("id") is not None:
                nm = get_team_name(ti.get("id"), teams_dict) or ""
                if nm:
                    names.append(nm)
    return names


def _find_event(today_events: List[Dict[str, Any]], teams_dict: Dict[str, Any], away_name: str, home_name: str) -> Optional[Dict[str, Any]]:
    for ev in today_events:
        names = _event_team_names(ev, teams_dict)
        if away_name in names and home_name in names:
            return ev
    return None


def _format_lines(lines: Dict[float, Dict[str, Any]]) -> List[str]:
    out = []
    for pts in sorted(lines.keys()):
        d = lines[pts] or {}
        oa = d.get("over_american")
        ua = d.get("under_american")
        est = " EST" if d.get("estimated_other_side") else ""
        out.append(f"{pts:6.1f}  OVER {str(oa):>5}   UNDER {str(ua):>5}{est}")
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--away", required=True, help="Away team code (e.g., HOU)")
    ap.add_argument("--home", required=True, help="Home team code (e.g., ATL)")
    ap.add_argument("--interval", type=float, default=10.0, help="Poll interval seconds (default 10)")
    args = ap.parse_args()

    away_code = args.away.strip().upper()
    home_code = args.home.strip().upper()
    interval = float(args.interval)

    xref_path = project_root / "team_xref_nba.csv"
    code_to_unab = _load_code_to_unabated_name(xref_path)
    away_name = code_to_unab.get(away_code)
    home_name = code_to_unab.get(home_code)
    if not away_name or not home_name:
        print(f"Could not map codes to Unabated names using {xref_path.name}.", flush=True)
        print(f"  away_code={away_code} -> {away_name}", flush=True)
        print(f"  home_code={home_code} -> {home_name}", flush=True)
        sys.exit(2)

    print(
        f"Polling Unabated ms7 totals alts for {away_code}@{home_code} ({away_name} @ {home_name}) every {interval:.1f}s",
        flush=True,
    )
    print("Press Ctrl+C to stop.\n", flush=True)

    while True:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            snap = fetch_unabated_snapshot()
            teams_dict = snap.get("teams", {})
            events = extract_nba_games_today(snap)
            ev = _find_event(events, teams_dict, away_name, home_name)
            if not ev:
                print(f"[{ts}] NOT FOUND in snapshot (game not in today's events?)", flush=True)
            else:
                event_start = ev.get("eventStart")
                lines = _extract_pinnacle_totals_alt_lines_ms7(ev)
                print(f"[{ts}] eventStart={event_start} ms7_points={len(lines)}", flush=True)
                for line in _format_lines(lines):
                    print("  " + line, flush=True)
        except KeyboardInterrupt:
            raise
        except Exception as e:
            print(f"[{ts}] ERROR: {type(e).__name__}: {e}", flush=True)

        try:
            time.sleep(interval)
        except KeyboardInterrupt:
            print("\nStopped.", flush=True)
            return


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped.", flush=True)

