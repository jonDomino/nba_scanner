"""
Streamlit app for NBA Moneylines Dashboard.

This app embeds the existing HTML dashboard into Streamlit using st.components.v1.html.
"""

import os
import streamlit as st
from datetime import datetime
from typing import Optional, List, Dict, Any

# Import the specific exception type for secrets
try:
    from streamlit.errors import StreamlitSecretNotFoundError
except ImportError:
    # Older versions of Streamlit might not have this specific exception
    StreamlitSecretNotFoundError = Exception

# Export Streamlit secrets as environment variables (before importing other modules)
# This makes them available to os.getenv() calls in the rest of the codebase
# In Streamlit Cloud, secrets are available via st.secrets (works like a dict)
# For local testing, secrets may not exist - that's OK, we'll use env vars or local files
if hasattr(st, 'secrets'):
    try:
        # Try to access secrets - this may raise StreamlitSecretNotFoundError if no secrets file exists
        secrets_dict = st.secrets
        
        # Try dictionary access first, then attribute access as fallback
        # Streamlit secrets can be accessed both ways
        for key in ['KALSHI_API_KEY_ID', 'KALSHI_PRIVATE_KEY_PEM', 'UNABATED_API_KEY']:
            try:
                # Try dictionary-style access
                value = secrets_dict[key]
                if value:
                    os.environ[key] = str(value)
            except (KeyError, TypeError):
                try:
                    # Try attribute-style access
                    value = getattr(secrets_dict, key)
                    if value:
                        os.environ[key] = str(value)
                except (AttributeError, TypeError):
                    # Key not found - that's OK, will use fallback
                    pass
                    
    except (StreamlitSecretNotFoundError, AttributeError, KeyError, TypeError) as e:
        # Secrets file doesn't exist or can't be read - this is OK for local testing
        # Will fall back to environment variables or local files
        pass
    except Exception as e:
        # Any other error - log for debugging but don't fail
        import sys
        print(f"Note: Error reading Streamlit secrets: {type(e).__name__}: {e}", file=sys.stderr)

# Local fallback: load creds from gitignored creds_local.txt/creds.txt into env
try:
    from utils.creds_loader import apply_creds_to_environ
    apply_creds_to_environ(keys=["KALSHI_API_KEY_ID", "KALSHI_PRIVATE_KEY_PEM", "UNABATED_API_KEY"])
except Exception:
    # Never block the app on local creds loading
    pass

from orchestrator import build_all_rows, build_dashboard_html_all
from cbb.orchestrator_cbb import build_all_rows_cbb

# Configure Streamlit page
st.set_page_config(
    page_title="Kalshi Value Dashboard",
    page_icon="🏀",
    layout="wide",
    initial_sidebar_state="collapsed"
)

# Cache data with TTL to reduce API calls
@st.cache_data(ttl=30)  # Cache for 30 seconds
def get_cached_rows_nba():
    """
    Build and cache the raw dashboard rows (HTML is built after applying UI filters).
    
    Returns:
        Tuple of (moneyline_rows, spread_rows, totals_rows, timestamp)
    """
    try:
        moneyline_rows, spread_rows, totals_rows = build_all_rows(debug=False)
        timestamp = datetime.now()
        return moneyline_rows, spread_rows, totals_rows, timestamp
    except Exception as e:
        st.error(f"Error building dashboard: {e}")
        st.stop()
        return None, None, None, None


@st.cache_data(ttl=30)  # Cache for 30 seconds
def get_cached_rows_cbb():
    """
    Build and cache the raw CBB dashboard rows (HTML is built after applying UI filters).
    Kept separate from NBA cache to avoid any coupling.
    """
    try:
        moneyline_rows, spread_rows, totals_rows = build_all_rows_cbb(debug=False)
        timestamp = datetime.now()
        return moneyline_rows, spread_rows, totals_rows, timestamp
    except Exception as e:
        # Never break the NBA page due to CBB build issues
        return [], [], [], datetime.now()


def _calc_dollar_liq(row: Dict[str, Any]) -> Optional[float]:
    """Dollar liquidity at TOB: (price_cents/100) * contracts."""
    try:
        price_cents = row.get("kalshi_price_cents")
        contracts = row.get("kalshi_liq")
        if price_cents is None or contracts is None:
            return None
        return (float(price_cents) / 100.0) * float(contracts)
    except Exception:
        return None


def _filter_rows_by_liq(rows: Optional[List[Dict[str, Any]]], min_dollars: float) -> List[Dict[str, Any]]:
    """Keep only rows with dollar liquidity >= min_dollars."""
    if not rows:
        return []
    out: List[Dict[str, Any]] = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        d = _calc_dollar_liq(r)
        if d is None:
            continue
        if d >= float(min_dollars):
            out.append(r)
    return out


def main():
    """Main Streamlit app function."""
    st.title("🏀 Kalshi Value Dashboard")
    
    # Debug: Show secrets status (only in Streamlit Cloud for debugging)
    if hasattr(st, 'secrets'):
        try:
            # Check what secrets are available
            secrets_available = []
            try:
                # Try to get list of keys
                if hasattr(st.secrets, 'keys'):
                    secrets_available = list(st.secrets.keys())
                elif hasattr(st.secrets, '__dict__'):
                    secrets_available = list(st.secrets.__dict__.keys())
            except:
                pass
            
            # Check environment variables
            env_vars_set = []
            if os.getenv('KALSHI_API_KEY_ID'):
                env_vars_set.append('KALSHI_API_KEY_ID')
            if os.getenv('KALSHI_PRIVATE_KEY_PEM'):
                env_vars_set.append('KALSHI_PRIVATE_KEY_PEM')
            if os.getenv('UNABATED_API_KEY'):
                env_vars_set.append('UNABATED_API_KEY')
            
            # Show debug info if secrets or env vars are missing
            if not os.getenv('UNABATED_API_KEY'):
                with st.expander("🔍 Debug: Secrets Status", expanded=False):
                    st.write(f"**Available secrets keys:** {secrets_available}")
                    st.write(f"**Environment variables set:** {env_vars_set}")
                    st.write(f"**UNABATED_API_KEY in env:** {bool(os.getenv('UNABATED_API_KEY'))}")
                    st.write(f"**UNABATED_API_KEY in secrets:** {'UNABATED_API_KEY' in (secrets_available if secrets_available else [])}")

                    # Local creds file status (does not print secrets)
                    try:
                        from pathlib import Path
                        from utils.creds_loader import load_creds_file

                        root = Path(__file__).resolve().parent
                        creds_local = root / "creds_local.txt"
                        creds_txt = root / "creds.txt"
                        creds = load_creds_file()

                        st.write("**Local creds files**:")
                        st.write(f"- creds_local.txt exists: {creds_local.exists()}")
                        st.write(f"- creds.txt exists: {creds_txt.exists()}")
                        st.write(f"**Keys found in creds file:** {sorted(list(creds.keys())) if creds else []}")
                        st.write("**Expected keys for live local:** ['UNABATED_API_KEY', 'KALSHI_API_KEY_ID', 'KALSHI_PRIVATE_KEY_PEM']")
                    except Exception as _e:
                        st.write(f"**Local creds debug failed:** {type(_e).__name__}: {_e}")
        except Exception as e:
            # Ignore debug errors
            pass
    
    # Sidebar with refresh controls
    with st.sidebar:
        st.header("Controls")
        
        if st.button("🔄 Refresh Now", type="primary"):
            # Clear cache and rebuild
            get_cached_rows_nba.clear()
            get_cached_rows_cbb.clear()
            st.rerun()
        
        st.markdown("---")
        st.caption("Dashboard refreshes automatically every 30 seconds.")
        st.caption("Click 'Refresh Now' to force immediate refresh.")
    
    # Controls (top of page)
    liq_min = st.number_input(
        "Min Liq ($)",
        min_value=0,
        value=5000,
        step=500,
        help="Filter rows by TOB dollar liquidity: (price_cents/100) * contracts",
    )
    
    tabs = st.tabs(["NBA", "CBB"])

    def _render_tab(league: str):
        if league == "NBA":
            moneyline_rows, spread_rows, totals_rows, timestamp = get_cached_rows_nba()
        else:
            moneyline_rows, spread_rows, totals_rows, timestamp = get_cached_rows_cbb()

        # Apply liquidity filter to all markets
        moneyline_rows_f = _filter_rows_by_liq(moneyline_rows, liq_min)
        spread_rows_f = _filter_rows_by_liq(spread_rows, liq_min) if spread_rows else []
        totals_rows_f = _filter_rows_by_liq(totals_rows, liq_min) if totals_rows else []

        html = build_dashboard_html_all(moneyline_rows_f, spread_rows_f, totals_rows_f)

        # Display last updated timestamp
        if timestamp:
            st.caption(f"{league} last updated: {timestamp.strftime('%Y-%m-%d %H:%M:%S')} PST")

        # Display counts (consolidated)
        consolidated_rows = (moneyline_rows_f or []) + (spread_rows_f or []) + (totals_rows_f or [])
        consolidated_games = len({r.get("event_start") for r in consolidated_rows if isinstance(r, dict) and r.get("event_start")}) if consolidated_rows else 0
        st.info(f"Showing {len(consolidated_rows)} row(s) across {consolidated_games} game(s) (liq ≥ ${int(liq_min):,})")

        iframe_height = int(500 + (len(consolidated_rows) * 48))
        iframe_height = max(900, min(30000, iframe_height))
        st.components.v1.html(html, height=iframe_height, scrolling=False)

    with tabs[0]:
        _render_tab("NBA")
    with tabs[1]:
        _render_tab("CBB")
    
    # Footer
    st.markdown("---")
    st.caption("Data source: Unabated snapshot (prefers Pinnacle ms70; falls back ms58/ms7/ms49) + Kalshi (orderbook prices)")


if __name__ == "__main__":
    main()
