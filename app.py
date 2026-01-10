"""
Streamlit app for NBA Moneylines Dashboard.

This app embeds the existing HTML dashboard into Streamlit using st.components.v1.html.
"""

import os
import streamlit as st
from datetime import datetime

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

from orchestrator import build_all_rows, build_dashboard_html_all

# Configure Streamlit page
st.set_page_config(
    page_title="NBA Value Dashboard",
    page_icon="🏀",
    layout="wide",
    initial_sidebar_state="collapsed"
)

# Cache data with TTL to reduce API calls
@st.cache_data(ttl=30)  # Cache for 30 seconds
def get_cached_dashboard():
    """
    Build and cache the dashboard HTML.
    
    Returns:
        Tuple of (moneyline_rows, spread_rows, totals_rows, html_string, timestamp)
    """
    try:
        moneyline_rows, spread_rows, totals_rows = build_all_rows(debug=False)
        html = build_dashboard_html_all(moneyline_rows, spread_rows, totals_rows)
        timestamp = datetime.now()
        return moneyline_rows, spread_rows, totals_rows, html, timestamp
    except Exception as e:
        st.error(f"Error building dashboard: {e}")
        st.stop()
        return None, None, None, None, None


def main():
    """Main Streamlit app function."""
    st.title("🏀 NBA Value Dashboard")
    
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
        except Exception as e:
            # Ignore debug errors
            pass
    
    # Sidebar with refresh controls
    with st.sidebar:
        st.header("Controls")
        
        if st.button("🔄 Refresh Now", type="primary"):
            # Clear cache and rebuild
            get_cached_dashboard.clear()
            st.rerun()
        
        st.markdown("---")
        st.caption("Dashboard refreshes automatically every 30 seconds.")
        st.caption("Click 'Refresh Now' to force immediate refresh.")
    
    # Get cached data
    moneyline_rows, spread_rows, totals_rows, html, timestamp = get_cached_dashboard()
    
    if moneyline_rows is None or html is None:
        st.error("Failed to load dashboard data.")
        return
    
    # Display last updated timestamp
    if timestamp:
        st.caption(f"Last updated: {timestamp.strftime('%Y-%m-%d %H:%M:%S')} PST")
    
    # Display game count
    st.info(f"Showing {len(moneyline_rows)} moneyline game(s), {len(spread_rows) if spread_rows else 0} spread row(s), {len(totals_rows) if totals_rows else 0} totals row(s)")
    
    # Embed HTML dashboard
    # Use height=1200 for comfortable viewing, scrolling enabled
    st.components.v1.html(html, height=1200, scrolling=True)
    
    # Footer
    st.markdown("---")
    st.caption("Data source: Unabated (consensus odds) + Kalshi (orderbook prices)")


if __name__ == "__main__":
    main()
