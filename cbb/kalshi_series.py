import re
from typing import Any, Dict, List, Optional, Tuple

from core.reusable_functions import fetch_kalshi_events, fetch_kalshi_markets_for_event, parse_kalshi_event_ticker


CBB_GAME_SERIES = "KXNCAAMBGAME"
CBB_TOTALS_SERIES = "KXNCAAMBTOTAL"
CBB_SPREADS_SERIES = "KXNCAAMBSPREAD"


def fetch_cbb_game_events(api_key_id: str, private_key_pem: str) -> List[Dict[str, Any]]:
    """
    Fetch open CBB game events and attach:
      - away_code/home_code
      - away_market_ticker/home_market_ticker
    """
    events = fetch_kalshi_events(api_key_id, private_key_pem, CBB_GAME_SERIES) or []
    out: List[Dict[str, Any]] = []
    for ev in events:
        if not isinstance(ev, dict):
            continue
        et = ev.get("event_ticker") or ev.get("eventTicker")
        title = ev.get("title") or ""
        markets = ev.get("markets") or []
        if not et or not isinstance(markets, list):
            continue
        # Collect two team markets
        team_markets = [m for m in markets if isinstance(m, dict) and (m.get("ticker") or "").startswith(et + "-")]
        tickers = [m.get("ticker") for m in team_markets if m.get("ticker")]
        if len(tickers) < 2:
            continue
        # Deduplicate and take first two
        uniq = []
        for t in tickers:
            if t not in uniq:
                uniq.append(t)
        if len(uniq) < 2:
            continue
        t1, t2 = uniq[0], uniq[1]
        c1 = t1.split("-")[-1].upper()
        c2 = t2.split("-")[-1].upper()

        parsed = parse_kalshi_event_ticker(et)
        rest = parsed[1] if parsed else ""
        away_code = None
        home_code = None
        away_ticker = None
        home_ticker = None
        if rest == (c1 + c2):
            away_code, home_code = c1, c2
            away_ticker, home_ticker = t1, t2
        elif rest == (c2 + c1):
            away_code, home_code = c2, c1
            away_ticker, home_ticker = t2, t1
        else:
            # Fallback: keep stable ordering
            away_code, home_code = c1, c2
            away_ticker, home_ticker = t1, t2

        out.append({
            "event_ticker": et,
            "title": title,
            "away_code": away_code,
            "home_code": home_code,
            "away_market_ticker": away_ticker,
            "home_market_ticker": home_ticker,
        })
    return out


def cbb_game_to_totals_event_ticker(game_event_ticker: str) -> str:
    return game_event_ticker.replace(CBB_GAME_SERIES + "-", CBB_TOTALS_SERIES + "-", 1)


def cbb_game_to_spreads_event_ticker(game_event_ticker: str) -> str:
    return game_event_ticker.replace(CBB_GAME_SERIES + "-", CBB_SPREADS_SERIES + "-", 1)


def fetch_markets_for_event(api_key_id: str, private_key_pem: str, event_ticker: str) -> List[Dict[str, Any]]:
    return fetch_kalshi_markets_for_event(api_key_id, private_key_pem, event_ticker) or []


def parse_cbb_totals_strike(market: Dict[str, Any]) -> Optional[float]:
    """
    Prefer `custom_strike` when present. Fallback to ticker suffix.
    """
    if not isinstance(market, dict):
        return None
    cs = market.get("custom_strike")
    if cs is not None:
        try:
            return float(cs)
        except Exception:
            pass
    t = market.get("ticker") or ""
    if not t:
        return None
    try:
        suf = t.split("-")[-1]
        return float(suf)
    except Exception:
        return None


_SPREAD_TITLE_RE = re.compile(r"wins\s+by\s+over\s+([\d.]+)\s+points?", re.IGNORECASE)
_SPREAD_TICKER_RE = re.compile(r"^([A-Z]{2,5})(\d+)$")


def parse_cbb_spread_market(market: Dict[str, Any]) -> Optional[Tuple[float, str]]:
    """
    Parse (strike, market_team_code) from a CBB spread market dict.
    """
    if not isinstance(market, dict):
        return None
    t = market.get("ticker") or ""
    title = market.get("title") or ""
    if not t or not title:
        return None
    # strike from title
    m = _SPREAD_TITLE_RE.search(title)
    if not m:
        return None
    try:
        strike = float(m.group(1))
    except Exception:
        return None
    # team code from ticker suffix (e.g., MSU2)
    suf = t.split("-")[-1].upper()
    m2 = _SPREAD_TICKER_RE.match(suf)
    if not m2:
        return None
    team_code = m2.group(1)
    market["market_team_code"] = team_code
    return (strike, team_code)

