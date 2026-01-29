import csv
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Dict, Optional, Tuple, Any


_WS_RE = re.compile(r"\s+")
_PUNCT_RE = re.compile(r"[^a-z0-9 ]+")


def normalize_team_name(name: str) -> str:
    """
    Lightweight normalizer for fuzzy matching across Kalshi/Unabated naming differences.
    Keep it conservative to avoid collisions in CBB.
    """
    if not name:
        return ""
    s = name.lower().strip()
    s = s.replace("&", "and")
    s = s.replace(".", "")
    s = _PUNCT_RE.sub(" ", s)
    s = _WS_RE.sub(" ", s).strip()
    # Common expansions
    s = s.replace("st ", "state ")
    s = s.replace(" st", " state")
    return s


def similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def load_cbb_overrides(path: Path) -> Dict[str, str]:
    """
    Load overrides mapping: kalshi_code -> unabated_name
    """
    out: Dict[str, str] = {}
    if not path.exists():
        return out
    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if not row:
                continue
            code = (row.get("kalshi_code") or "").strip().upper()
            unab = (row.get("unabated_name") or "").strip()
            if code and unab:
                out[code] = unab
    return out


def parse_kalshi_matchup_title(title: str) -> Optional[Tuple[str, str]]:
    """
    Parse Kalshi event titles like:
      "Samford at Furman"
    into (away_name, home_name).
    """
    if not title:
        return None
    t = title.strip()
    # Kalshi seems consistent on " at "
    if " at " in t:
        a, h = t.split(" at ", 1)
        return (a.strip(), h.strip())
    if " vs " in t:
        a, h = t.split(" vs ", 1)
        return (a.strip(), h.strip())
    return None


@dataclass
class UnabatedTeamMatch:
    team_id: int
    unabated_name: str
    score: float


def best_match_unabated_team(
    kalshi_team_name: str,
    teams_dict: Dict[str, Any],
    override_unabated_name: Optional[str] = None,
    min_score: float = 0.86,
) -> Optional[UnabatedTeamMatch]:
    """
    Find the best matching Unabated team for a Kalshi team display name.

    This is "best effort" and intentionally conservative (high min_score).
    For ambiguous cases, we rely on overrides.
    """
    if override_unabated_name:
        # Exact override: find that unabated name in teams_dict
        for tid, tinfo in (teams_dict or {}).items():
            if not isinstance(tinfo, dict):
                continue
            nm = (tinfo.get("name") or "").strip()
            if nm == override_unabated_name:
                try:
                    return UnabatedTeamMatch(team_id=int(tid), unabated_name=nm, score=1.0)
                except Exception:
                    return None

    kn = normalize_team_name(kalshi_team_name)
    if not kn:
        return None

    best: Optional[UnabatedTeamMatch] = None
    for tid, tinfo in (teams_dict or {}).items():
        if not isinstance(tinfo, dict):
            continue
        nm = (tinfo.get("name") or "").strip()
        if not nm:
            continue
        un = normalize_team_name(nm)
        sc = similarity(kn, un)
        if best is None or sc > best.score:
            try:
                best = UnabatedTeamMatch(team_id=int(tid), unabated_name=nm, score=sc)
            except Exception:
                continue

    if best and best.score >= min_score:
        return best
    return None

