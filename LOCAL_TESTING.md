# Local Streamlit Testing Guide

## Quick Start

To launch the Streamlit app locally for testing:

```bash
streamlit run app.py
```

The app will automatically open in your browser at `http://localhost:8501`

## Prerequisites

### 1. Install Streamlit (if not already installed)

```bash
pip install streamlit>=1.28.0
```

Or install all requirements:

```bash
pip install -r requirements.txt
```

### 2. Ensure Credentials are Available

For local testing, you need credentials. Options:

**Option A: Use local files (easiest for testing)**
- `kalshi_api_key_id.txt` - Your Kalshi API key ID
- `kalshi_private_key.pem` - Your Kalshi private key (PEM format)
- Create `secrets_local.py` with `UNABATED_API_KEY = "your_key"`

**Option B: Use environment variables**
```bash
# PowerShell (Windows)
$env:KALSHI_API_KEY_ID = "your_key_id"
$env:KALSHI_PRIVATE_KEY_PEM = "your_private_key_pem_content"
$env:UNABATED_API_KEY = "your_unabated_key"

# Then run Streamlit
streamlit run app.py
```

```bash
# Bash (Linux/Mac)
export KALSHI_API_KEY_ID="your_key_id"
export KALSHI_PRIVATE_KEY_PEM="your_private_key_pem_content"
export UNABATED_API_KEY="your_unabated_key"

# Then run Streamlit
streamlit run app.py
```

## Running the App

### Basic Command
```bash
streamlit run app.py
```

### With Custom Port
```bash
streamlit run app.py --server.port 8502
```

### With Auto-Reload Disabled (for debugging)
```bash
streamlit run app.py --server.runOnSave false
```

## Expected Behavior

1. **Browser opens automatically** to `http://localhost:8501`
2. **Dashboard loads** with today's NBA games
3. **Sidebar appears** with "Refresh Now" button
4. **Timestamp shows** last update time
5. **Game count** displayed above dashboard

## Testing Features

### Test Refresh Button
- Click "🔄 Refresh Now" in sidebar
- Should clear cache and fetch fresh data
- Timestamp should update

### Test Caching
- Refresh button should show cached data (no new API calls)
- Wait 30+ seconds, data should auto-refresh
- Click "Refresh Now" to force immediate refresh

### Test Error Handling
- Temporarily remove credentials to see error messages
- Remove API keys to test graceful failure

## Troubleshooting

### "ModuleNotFoundError: No module named 'streamlit'"
```bash
pip install streamlit
```

### "Missing Kalshi credentials"
- Ensure credential files exist or environment variables are set
- Check `.gitignore` - credential files should NOT be committed

### "Failed to load dashboard data"
- Check your API credentials are valid
- Verify internet connection
- Check Streamlit terminal output for detailed error messages

### Port Already in Use
```bash
# Use different port
streamlit run app.py --server.port 8502

# Or kill existing process
# Windows: Find process using port 8501 and kill it
# Linux/Mac: lsof -ti:8501 | xargs kill
```

### Browser Doesn't Open Automatically
- Manually navigate to `http://localhost:8501`
- Or use: `streamlit run app.py --server.headless true` and open manually

## Comparing Local vs Streamlit Cloud

| Feature | Local (`streamlit run app.py`) | Streamlit Cloud |
|---------|-------------------------------|-----------------|
| Credentials | Local files or env vars | Streamlit Secrets |
| URL | `localhost:8501` | `your-app.streamlit.app` |
| Sharing | Local network only | Public URL |
| Updates | Manual restart | Auto-deploys on git push |

## Development Tips

1. **Auto-reload**: Streamlit auto-reloads on file save (default)
2. **Clear cache**: Use "R" key in Streamlit UI or click "Clear cache" in menu
3. **View logs**: Check terminal output for API calls and errors
4. **Debug mode**: Add `st.write()` statements for debugging

## Next Steps

Once local testing works:
1. Push code to GitHub
2. Deploy to Streamlit Cloud
3. Set up Streamlit Secrets (see `STREAMLIT_DEPLOY.md`)
4. Share public URL with colleagues
