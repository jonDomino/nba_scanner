"""
Streamlit app for NBA Moneylines Dashboard.

This app embeds the existing HTML dashboard into Streamlit using st.components.v1.html.
"""

import streamlit as st
from datetime import datetime
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
