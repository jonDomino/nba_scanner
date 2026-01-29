"""
CBB orchestrator.

IMPORTANT: This module is isolated from the existing NBA orchestrator (`orchestrator.py`).
It should not modify or depend on NBA-specific parsing assumptions.
"""

from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional, Tuple

from utils.kalshi_api import load_creds

from core.reusable_functions import fetch_unabated_snapshot
from data_build.unabated_callsheet import extract_nba_games_today  # reuse helper; snapshot format is the same

from spreads.builder import _fetch_orderbook_with_cache, get_spread_orderbook_data, american_to_prob
from totals.builder import _extract_pinnacle_totals_alt_lines_ms7
from spreads.builder import _extract_pinnacle_spreads_alt_lines_ms7

from cbb.team_mapping import load_cbb_overrides, parse_kalshi_matchup_title, best_match_unabated_team
from cbb.kalshi_series import (
    fetch_cbb_game_events,
    cbb_game_to_totals_event_ticker,
    cbb_game_to_spreads_event_ticker,
    fetch_markets_for_event,
    parse_cbb_totals_strike,
    parse_cbb_spread_market,
)

from spreads.builder import _pair_pinnacle_spreads_by_overround  # reuse generic pairing helper


def _extract_ms7_moneyline_probs(event: Dict[str, Any]) -> Dict[int, float]:
    """
    Extract moneyline implied probs from ms7 bt1 for each team_id in the event.
    Returns team_id -> prob.
    """
    market_lines = event.get("gameOddsMarketSourcesLines", {})
    if not isinstance(market_lines, dict):
        return {}
    event_teams = event.get("eventTeams", {})
    if not isinstance(event_teams, dict):
        return {}
    ms_keys = [k for k in market_lines.keys() if isinstance(k, str) and ":ms7:" in k]
    out: Dict[int, float] = {}

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
        bt1 = block.get("bt1")
        if not isinstance(bt1, dict):
            continue
        price_raw = bt1.get("americanPrice") or bt1.get("price") or bt1.get("unabatedPrice")
        if price_raw is None:
            continue
        try:
            american = int(str(price_raw).strip())
        except Exception:
            continue
        p = american_to_prob(american)
        if p is None:
            continue
        out[int(team_id)] = float(p)
    return out


def _find_unabated_cbb_event(
    snapshot: Dict[str, Any],
    away_name: str,
    home_name: str,
    overrides_by_code: Dict[str, str],
    away_code: str,
    home_code: str,
) -> Optional[Dict[str, Any]]:
    """
    Best-effort: find an Unabated CBB event matching the Kalshi matchup.
    """
    # CBB pregame events live here (lg4)
    ge = snapshot.get("gameOddsEvents", {})
    events = ge.get("lg4:pt1:pregame", []) if isinstance(ge, dict) else []
    if not isinstance(events, list) or not events:
        return None

    teams_dict = snapshot.get("teams", {})

    away_override = overrides_by_code.get(away_code)
    home_override = overrides_by_code.get(home_code)

    away_match = best_match_unabated_team(away_name, teams_dict, override_unabated_name=away_override)
    home_match = best_match_unabated_team(home_name, teams_dict, override_unabated_name=home_override)
    if not away_match or not home_match:
        return None

    wanted_ids = {away_match.team_id, home_match.team_id}
    for ev in events:
        if not isinstance(ev, dict):
            continue
        et = ev.get("eventTeams", {})
        if not isinstance(et, dict):
            continue
        ids = set()
        for _, ti in et.items():
            if isinstance(ti, dict) and ti.get("id") is not None:
                ids.add(int(ti.get("id")))
        if wanted_ids.issubset(ids):
            return ev
    return None


def build_all_rows_cbb(debug: bool = False) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Returns (moneyline_rows, spread_rows, totals_rows) for CBB.
    """
    api_key_id, private_key_pem = load_creds()
    snapshot = fetch_unabated_snapshot()

    overrides_path = __import__("pathlib").Path(__file__).resolve().parents[1] / "team_xref_cbb_overrides.csv"
    overrides = load_cbb_overrides(overrides_path)

    # Kalshi game events (CBB)
    game_events = fetch_cbb_game_events(api_key_id, private_key_pem)
    if not game_events:
        return [], [], []

    moneyline_rows: List[Dict[str, Any]] = []
    totals_rows: List[Dict[str, Any]] = []
    spread_rows: List[Dict[str, Any]] = []

    def _fetch_orderbooks_many(tickers: List[str]) -> Dict[str, Dict[str, Any]]:
        """
        Fetch many Kalshi orderbooks concurrently, with per-ticker caching.
        Returns ticker -> orderbook dict.
        """
        out: Dict[str, Dict[str, Any]] = {}
        uniq: List[str] = []
        for t in tickers:
            if t and t not in out and t not in uniq:
                uniq.append(t)

        if not uniq:
            return out

        # Keep this conservative to avoid rate limits for CBB volume.
        max_workers = 8
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            futs = {ex.submit(_fetch_orderbook_with_cache, t, api_key_id, private_key_pem): t for t in uniq}
            for fut, t in futs.items():
                try:
                    out[t] = fut.result() or {}
                except Exception:
                    out[t] = {}
        return out

    # Limit to a reasonable number to avoid huge API load during early iteration
    for ev in game_events[:120]:
        event_ticker = ev.get("event_ticker")
        title = ev.get("title") or ""
        parsed = parse_kalshi_matchup_title(title)
        if not event_ticker or not parsed:
            continue
        away_name, home_name = parsed

        away_code = ev.get("away_code") or ""
        home_code = ev.get("home_code") or ""
        away_market_ticker = ev.get("away_market_ticker")
        home_market_ticker = ev.get("home_market_ticker")
        if not away_code or not home_code or not away_market_ticker or not home_market_ticker:
            continue

        game_code = f"{away_code}@{home_code}"

        unab_ev = _find_unabated_cbb_event(snapshot, away_name, home_name, overrides, away_code, home_code)
        if not unab_ev:
            continue

        # -----------------
        # CBB Moneylines
        # -----------------
        # Kalshi orderbooks for the two winner markets (concurrent + cached)
        orderbooks = _fetch_orderbooks_many([away_market_ticker, home_market_ticker])

        away_ob = get_spread_orderbook_data(away_market_ticker, "YES", orderbook=orderbooks[away_market_ticker])
        home_ob = get_spread_orderbook_data(home_market_ticker, "YES", orderbook=orderbooks[home_market_ticker])

        # Pinnacle proxy (ms7 bt1)
        pin_ml = _extract_ms7_moneyline_probs(unab_ev)
        # Map by team_id match (best-effort); if missing, skip ML rows
        # Derive team_ids by matching Unabated eventTeams against matched names
        et = unab_ev.get("eventTeams", {})
        team_ids = []
        if isinstance(et, dict):
            for _, ti in et.items():
                if isinstance(ti, dict) and ti.get("id") is not None:
                    team_ids.append(int(ti.get("id")))
        # We don't know which is away/home in Unabated; use name matching from overrides
        teams_dict = snapshot.get("teams", {})
        away_match = best_match_unabated_team(away_name, teams_dict, override_unabated_name=overrides.get(away_code))
        home_match = best_match_unabated_team(home_name, teams_dict, override_unabated_name=overrides.get(home_code))
        away_id = away_match.team_id if away_match else None
        home_id = home_match.team_id if home_match else None
        away_pin = pin_ml.get(away_id) if away_id is not None else None
        home_pin = pin_ml.get(home_id) if home_id is not None else None

        # Invert opponent POV (same philosophy as NBA)
        away_pinnacle = (1.0 - home_pin) if home_pin is not None else away_pin
        home_pinnacle = (1.0 - away_pin) if away_pin is not None else home_pin

        base_ml = {
            "game_date": None,
            "event_start": unab_ev.get("eventStart"),
            "away_roto": None,
            "home_roto": None,
            "roto": None,
            "game": game_code,
            "market": "ML",
            "line": None,
        }

        moneyline_rows.append({
            **base_ml,
            "side": away_code,
            "kalshi_prob": away_ob.get("tob_effective_prob"),
            "kalshi_liq": away_ob.get("tob_liq"),
            "kalshi_price_cents": away_ob.get("tob_bid_cents"),
            "pinnacle_prob": away_pinnacle,
            "ev": (away_pinnacle - away_ob.get("tob_effective_prob")) * 100.0 if (away_pinnacle is not None and away_ob.get("tob_effective_prob") is not None) else None,
            "market_ticker": away_market_ticker,
        })

        moneyline_rows.append({
            **base_ml,
            "side": home_code,
            "kalshi_prob": home_ob.get("tob_effective_prob"),
            "kalshi_liq": home_ob.get("tob_liq"),
            "kalshi_price_cents": home_ob.get("tob_bid_cents"),
            "pinnacle_prob": home_pinnacle,
            "ev": (home_pinnacle - home_ob.get("tob_effective_prob")) * 100.0 if (home_pinnacle is not None and home_ob.get("tob_effective_prob") is not None) else None,
            "market_ticker": home_market_ticker,
        })

        # -----------------
        # CBB Totals
        # -----------------
        # Speedup: derive the strike universe from Unabated first, then only fetch orderbooks
        # for Kalshi markets that match those strikes (instead of iterating all Kalshi strikes).
        pin_tot = _extract_pinnacle_totals_alt_lines_ms7(unab_ev)

        totals_event_ticker = cbb_game_to_totals_event_ticker(event_ticker)
        totals_markets = fetch_markets_for_event(api_key_id, private_key_pem, totals_event_ticker)
        by_strike: Dict[float, Dict[str, Any]] = {}
        for m in totals_markets:
            strike = parse_cbb_totals_strike(m)
            t = m.get("ticker")
            if strike is None or not t:
                continue
            by_strike[float(strike)] = m

        if by_strike and pin_tot:
            # Inner join Kalshi strikes to Unabated strikes within a tolerance, but *drive the loop*
            # from Unabated to avoid touching extraneous Kalshi strikes/orderbooks.
            matched: List[Tuple[float, Dict[str, Any]]] = []
            for p_pts in pin_tot.keys():
                best_k = None
                best_d = None
                for k_strike in by_strike.keys():
                    d = abs(float(k_strike) - float(p_pts))
                    if d <= 0.26 and (best_d is None or d < best_d):
                        best_k = float(k_strike)
                        best_d = d
                if best_k is None:
                    continue
                matched.append((float(p_pts), by_strike[best_k]))

            tickers_to_fetch = []
            for _, m in matched:
                t = m.get("ticker")
                if t:
                    tickers_to_fetch.append(str(t))
            obs = _fetch_orderbooks_many(tickers_to_fetch)

            for p_pts, m in matched:
                ticker = m.get("ticker")
                if not ticker:
                    continue
                ob = obs.get(str(ticker), {}) or {}
                yes_ob = get_spread_orderbook_data(str(ticker), "YES", orderbook=ob)
                no_ob = get_spread_orderbook_data(str(ticker), "NO", orderbook=ob)

                pin = pin_tot.get(p_pts) or {}
                over_pin = pin.get("over_prob")
                under_pin = pin.get("under_prob")
                over_pinnacle = (1.0 - under_pin) if under_pin is not None else over_pin
                under_pinnacle = (1.0 - over_pin) if over_pin is not None else under_pin

                base_t = {
                    "game_date": None,
                    "event_start": unab_ev.get("eventStart"),
                    "away_roto": None,
                    "home_roto": None,
                    "roto": None,
                    "game": game_code,
                    "market": "TOTALS",
                    "line": float(p_pts),
                    "market_ticker": str(ticker),
                }

                totals_rows.append({
                    **base_t,
                    "side": "OVER",
                    "kalshi_prob": yes_ob.get("tob_effective_prob"),
                    "kalshi_liq": yes_ob.get("tob_liq"),
                    "kalshi_price_cents": yes_ob.get("tob_bid_cents"),
                    "pinnacle_prob": over_pinnacle,
                    "ev": (over_pinnacle - yes_ob.get("tob_effective_prob")) * 100.0 if (over_pinnacle is not None and yes_ob.get("tob_effective_prob") is not None) else None,
                })
                totals_rows.append({
                    **base_t,
                    "side": "UNDER",
                    "kalshi_prob": no_ob.get("tob_effective_prob"),
                    "kalshi_liq": no_ob.get("tob_liq"),
                    "kalshi_price_cents": no_ob.get("tob_bid_cents"),
                    "pinnacle_prob": under_pinnacle,
                    "ev": (under_pinnacle - no_ob.get("tob_effective_prob")) * 100.0 if (under_pinnacle is not None and no_ob.get("tob_effective_prob") is not None) else None,
                })

        # -----------------
        # CBB Spreads
        # -----------------
        # Speedup: same concept as totals — drive matching from Unabated magnitudes so we only
        # pull orderbooks for strikes Unabated actually provides.
        spreads_event_ticker = cbb_game_to_spreads_event_ticker(event_ticker)
        spread_markets = fetch_markets_for_event(api_key_id, private_key_pem, spreads_event_ticker)
        candidates_by_strike: Dict[float, List[Dict[str, Any]]] = {}
        for m in spread_markets:
            parsed = parse_cbb_spread_market(m)
            if not parsed:
                continue
            strike, team_code = parsed
            t = m.get("ticker")
            if not t:
                continue
            candidates_by_strike.setdefault(float(strike), []).append(m)

        pin_sp_raw = _extract_pinnacle_spreads_alt_lines_ms7(unab_ev, home_id, away_id)
        pin_sp = _pair_pinnacle_spreads_by_overround(pin_sp_raw)
        if candidates_by_strike and pin_sp:
            # Match each Unabated magnitude to the closest Kalshi strike and choose the correct
            # "wins by over X" market based on which team is -X in Pinnacle.
            chosen_markets: List[Tuple[float, Dict[str, Any], Dict[str, Any]]] = []
            for p_mag, pinp in pin_sp.items():
                if not pinp:
                    continue
                best_k = None
                best_d = None
                for k_strike in candidates_by_strike.keys():
                    d = abs(float(k_strike) - float(p_mag))
                    if d <= 0.26 and (best_d is None or d < best_d):
                        best_k = float(k_strike)
                        best_d = d
                if best_k is None:
                    continue

                candidates = candidates_by_strike.get(best_k, [])
                if not candidates:
                    continue

                # Determine which team is -X in Pinnacle for this magnitude.
                home_line = float(pinp.get("home_line", 0.0))
                away_line = float(pinp.get("away_line", 0.0))
                favored_code = None
                if abs(home_line + float(best_k)) <= 0.26:
                    favored_code = home_code
                elif abs(away_line + float(best_k)) <= 0.26:
                    favored_code = away_code

                chosen = None
                for c in candidates:
                    tc = (c.get("market_team_code") or "").strip()
                    if favored_code and tc != favored_code:
                        continue
                    chosen = c
                    break
                if not chosen:
                    continue

                chosen_markets.append((float(best_k), chosen, pinp))

            tickers_to_fetch = []
            for _, m, _ in chosen_markets:
                t = m.get("ticker")
                if t:
                    tickers_to_fetch.append(str(t))
            obs = _fetch_orderbooks_many(tickers_to_fetch)

            for k_strike, chosen, pinp in chosen_markets:
                ticker = chosen.get("ticker")
                market_team = (chosen.get("market_team_code") or "").strip()
                if not ticker or market_team not in [away_code, home_code]:
                    continue
                opp = away_code if market_team == home_code else home_code

                ob = obs.get(str(ticker), {}) or {}
                yes_ob = get_spread_orderbook_data(str(ticker), "YES", orderbook=ob)
                no_ob = get_spread_orderbook_data(str(ticker), "NO", orderbook=ob)

                prob_by_code = {home_code: pinp.get("home_prob"), away_code: pinp.get("away_prob")}
                fav_prob = prob_by_code.get(market_team)
                dog_prob = prob_by_code.get(opp)

                fav_pinnacle = (1.0 - dog_prob) if dog_prob is not None else fav_prob
                dog_pinnacle = (1.0 - fav_prob) if fav_prob is not None else dog_prob

                base_s = {
                    "game_date": None,
                    "event_start": unab_ev.get("eventStart"),
                    "away_roto": None,
                    "home_roto": None,
                    "roto": None,
                    "game": game_code,
                    "market": "SPREADS",
                    "market_ticker": str(ticker),
                }
                spread_rows.append({
                    **base_s,
                    "side": market_team,
                    "line": -float(k_strike),
                    "kalshi_prob": yes_ob.get("tob_effective_prob"),
                    "kalshi_liq": yes_ob.get("tob_liq"),
                    "kalshi_price_cents": yes_ob.get("tob_bid_cents"),
                    "pinnacle_prob": fav_pinnacle,
                    "ev": (fav_pinnacle - yes_ob.get("tob_effective_prob")) * 100.0 if (fav_pinnacle is not None and yes_ob.get("tob_effective_prob") is not None) else None,
                })
                spread_rows.append({
                    **base_s,
                    "side": opp,
                    "line": float(k_strike),
                    "kalshi_prob": no_ob.get("tob_effective_prob"),
                    "kalshi_liq": no_ob.get("tob_liq"),
                    "kalshi_price_cents": no_ob.get("tob_bid_cents"),
                    "pinnacle_prob": dog_pinnacle,
                    "ev": (dog_pinnacle - no_ob.get("tob_effective_prob")) * 100.0 if (dog_pinnacle is not None and no_ob.get("tob_effective_prob") is not None) else None,
                })

    return moneyline_rows, spread_rows, totals_rows

