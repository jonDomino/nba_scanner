"""
Ad-hoc helper: enumerate Unabated "books" (market sources) and their msid.

What this script does:
- Fetches the Unabated snapshot (same endpoint the app uses)
- Extracts all msids observed in event `gameOddsMarketSourcesLines` keys (pattern ":ms{ID}:")
- Attempts to find a snapshot mapping of market source ids -> names (e.g., "marketSources")
- Prints any entries whose name contains "pinnacle"

Usage (PowerShell):
  $env:UNABATED_API_KEY="..."           # required
  python ad_hoc/list_unabated_books_and_msids.py
"""

from __future__ import annotations

import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


# Add project root to Python path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))


from core.reusable_functions import fetch_unabated_snapshot  # noqa: E402
from utils import config  # noqa: E402


MS_RE = re.compile(r":ms(\d+):")


def _iter_events_from_snapshot(snapshot: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
    """
    Yield all event dicts from snapshot["gameOddsEvents"][...].
    Snapshot structure in this repo is typically:
      gameOddsEvents: { "lg3:...": [event, event, ...], ... }
    """
    goe = snapshot.get("gameOddsEvents", {})
    if not isinstance(goe, dict):
        return
    for _, maybe_events in goe.items():
        if isinstance(maybe_events, list):
            for ev in maybe_events:
                if isinstance(ev, dict):
                    yield ev


def _extract_msids_from_event(event: Dict[str, Any]) -> List[int]:
    market_lines = event.get("gameOddsMarketSourcesLines", {})
    if not isinstance(market_lines, dict):
        return []
    msids: List[int] = []
    for k in market_lines.keys():
        if not isinstance(k, str):
            continue
        m = MS_RE.search(k)
        if m:
            try:
                msids.append(int(m.group(1)))
            except Exception:
                pass
    return msids


def _find_marketsource_mappings(snapshot: Dict[str, Any]) -> List[Tuple[str, Dict[int, str]]]:
    """
    Heuristically look for a "market sources" mapping in the snapshot.
    Returns list of (top_level_key, mapping{id->name}).
    """
    results: List[Tuple[str, Dict[int, str]]] = []

    for top_key, v in snapshot.items():
        mapping: Dict[int, str] = {}

        # Case 1: dict keyed by numeric ids (or numeric strings) -> dict containing a name
        if isinstance(v, dict):
            # dict of dicts
            if all(isinstance(k, (int, str)) for k in v.keys()):
                for k2, v2 in v.items():
                    if not isinstance(v2, dict):
                        continue
                    name = v2.get("name") or v2.get("displayName") or v2.get("marketSourceName")
                    if not isinstance(name, str) or not name.strip():
                        continue
                    try:
                        msid = int(k2)
                    except Exception:
                        continue
                    mapping[msid] = name.strip()

        # Case 2: list of dicts with {id, name}
        if not mapping and isinstance(v, list):
            for item in v:
                if not isinstance(item, dict):
                    continue
                _id = item.get("id") or item.get("marketSourceId") or item.get("msid")
                name = item.get("name") or item.get("displayName") or item.get("marketSourceName")
                if _id is None or not isinstance(name, str) or not name.strip():
                    continue
                try:
                    msid = int(_id)
                except Exception:
                    continue
                mapping[msid] = name.strip()

        if mapping:
            results.append((str(top_key), mapping))

    return results


def main() -> None:
    print("=" * 100)
    print("UNABATED MARKET SOURCES (BOOKS) — msid enumeration")
    print("=" * 100)
    print(f"Configured UNABATED_MARKET_SOURCE_ID (used by extractors): {getattr(config, 'UNABATED_MARKET_SOURCE_ID', 49)}")
    print()

    snapshot = fetch_unabated_snapshot()
    if not isinstance(snapshot, dict):
        raise RuntimeError("Unabated snapshot did not return a JSON object")

    print(f"Top-level snapshot keys sample: {list(snapshot.keys())[:25]}")
    print()

    # 1) msid enumeration from event line keys
    msid_counts: Counter[int] = Counter()
    events_seen = 0

    for ev in _iter_events_from_snapshot(snapshot):
        events_seen += 1
        for msid in _extract_msids_from_event(ev):
            msid_counts[msid] += 1

    print(f"Events scanned: {events_seen}")
    print("Observed msids (from ':ms{ID}:' keys) with counts:")
    if not msid_counts:
        print("  (none found) — snapshot format may have changed, or no gameOddsMarketSourcesLines present.")
    else:
        for msid, cnt in sorted(msid_counts.items(), key=lambda x: (-x[1], x[0])):
            print(f"  ms{msid}: {cnt}")
    print()

    # 2) Try to find an id->name mapping in snapshot itself
    mappings = _find_marketsource_mappings(snapshot)
    if not mappings:
        print("No obvious market-source id->name mapping found at the snapshot top level.")
        print("If Unabated exposes a separate endpoint for market sources, we may need to call that instead.")
        print()
    else:
        print("Found candidate market-source mappings in snapshot:")
        for top_key, mapping in mappings:
            print(f"- {top_key}: {len(mapping)} entries")
            # Print sorted first 40 entries for readability
            for msid, name in sorted(mapping.items(), key=lambda x: x[0])[:40]:
                print(f"    {msid}: {name}")
            if len(mapping) > 40:
                print("    ... (truncated)")
        print()

        # 3) Look for Pinnacle
        pinnacle_hits: List[Tuple[str, int, str]] = []
        for top_key, mapping in mappings:
            for msid, name in mapping.items():
                if "pinnacle" in name.lower():
                    pinnacle_hits.append((top_key, msid, name))

        if pinnacle_hits:
            print("Pinnacle hits:")
            for top_key, msid, name in sorted(pinnacle_hits, key=lambda x: (x[1], x[0])):
                # Use ASCII-safe arrow for Windows consoles
                print(f"  {top_key}: ms{msid} -> {name}")
        else:
            print("No 'Pinnacle' string found in discovered name mappings.")

        # Also print Unabated mapping (useful sanity check when switching from ms49)
        unabated_hits: List[Tuple[str, int, str]] = []
        for top_key, mapping in mappings:
            for msid, name in mapping.items():
                if "unabated" in name.lower():
                    unabated_hits.append((top_key, msid, name))
        if unabated_hits:
            print("\nUnabated hits:")
            for top_key, msid, name in sorted(unabated_hits, key=lambda x: (x[1], x[0])):
                print(f"  {top_key}: ms{msid} -> {name}")

    print()
    print("Next step:")
    print("  - Once you identify Pinnacle's msid, update:")
    print("      utils/config.py::UNABATED_MARKET_SOURCE_ID = <PINNACLE_MSID>")
    print("    and the app's Unabated extracts will switch to that book.")


if __name__ == "__main__":
    main()

