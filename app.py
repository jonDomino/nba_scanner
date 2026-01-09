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
        # We need to trigger parsing carefully - check if secrets exist first
        secrets_dict = st.secrets
        # Access secrets using dictionary-style (standard Streamlit pattern)
        if 'KALSHI_API_KEY_ID' in secrets_dict:
            os.environ['KALSHI_API_KEY_ID'] = str(secrets_dict['KALSHI_API_KEY_ID'])
        if 'KALSHI_PRIVATE_KEY_PEM' in secrets_dict:
            os.environ['KALSHI_PRIVATE_KEY_PEM'] = str(secrets_dict['KALSHI_PRIVATE_KEY_PEM'])
        if 'UNABATED_API_KEY' in secrets_dict:
            os.environ['UNABATED_API_KEY'] = str(secrets_dict['UNABATED_API_KEY'])
    except (StreamlitSecretNotFoundError, AttributeError, KeyError, TypeError):
        # Secrets file doesn't exist or can't be read - this is OK for local testing
        # Will fall back to environment variables or local files
        pass
    except Exception:
        # Any other error - silently ignore and use fallback methods
        pass

from orchestrator_moneylines import build_moneylines_rows, build_dashboard_html_moneylines

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
        Tuple of (moneyline_rows, html_string, timestamp)
    """
    try:
        rows = build_moneylines_rows(debug=False)
        html = build_dashboard_html_moneylines(rows)
        timestamp = datetime.now()
        return rows, html, timestamp
    except Exception as e:
        st.error(f"Error building dashboard: {e}")
        st.stop()
        return None, None, None


def main():
    """Main Streamlit app function."""
    st.title("🏀 NBA Value Dashboard")
    
    # Note: Credentials can come from multiple sources:
    # 1. Streamlit Cloud secrets (st.secrets) - handled at top of file
    # 2. Environment variables
    # 3. Local files (secrets_local.py, .pem files, etc.) - handled by config.py
    # The app will attempt to load data, and if credentials are truly missing,
    # the error will surface from the data fetching functions with a clear message.
    
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
    rows, html, timestamp = get_cached_dashboard()
    
    if rows is None or html is None:
        st.error("Failed to load dashboard data.")
        return
    
    # Display last updated timestamp
    if timestamp:
        st.caption(f"Last updated: {timestamp.strftime('%Y-%m-%d %H:%M:%S')} PST")
    
    # Display game count
    st.info(f"Showing {len(rows)} game(s) for today")
    
    # Embed HTML dashboard
    # Use height=1200 for comfortable viewing, scrolling enabled
    st.components.v1.html(html, height=1200, scrolling=True)
    
    # Footer
    st.markdown("---")
    st.caption("Data source: Unabated (consensus odds) + Kalshi (orderbook prices)")


if __name__ == "__main__":
    main()
