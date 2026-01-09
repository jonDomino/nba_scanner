# Streamlit Cloud Deployment Guide

This guide explains how to deploy the NBA Moneylines Dashboard to Streamlit Community Cloud.

## Local Development

To run the Streamlit app locally:

```bash
streamlit run app.py
```

The app will open in your browser at `http://localhost:8501`.

## Streamlit Cloud Deployment

### 1. Prerequisites

- GitHub repository containing this codebase
- Streamlit Cloud account (free at [share.streamlit.io](https://share.streamlit.io))

### 2. Required Secrets

Before deploying, you must set the following secrets in Streamlit Cloud:

**Streamlit Cloud Dashboard → Settings → Secrets → Add secret**

Add the following secrets:

```toml
KALSHI_API_KEY_ID = "your_kalshi_api_key_id"
KALSHI_PRIVATE_KEY_PEM = """-----BEGIN PRIVATE KEY-----
your_private_key_content_here
-----END PRIVATE KEY-----"""
UNABATED_API_KEY = "your_unabated_api_key"
```

**Important**: Paste the secrets WITHOUT any section header (no `[secrets]` line). Streamlit Cloud expects root-level keys.

**Important Notes:**
- The `KALSHI_PRIVATE_KEY_PEM` should include the full PEM-formatted key including the `-----BEGIN PRIVATE KEY-----` and `-----END PRIVATE KEY-----` lines
- Keep the triple quotes (`"""`) around the private key value
- These secrets are encrypted and only accessible to your Streamlit app

### 3. Deploy Steps

1. **Push code to GitHub** (if not already done)
   ```bash
   git add .
   git commit -m "Add Streamlit app"
   git push origin main
   ```

2. **Connect to Streamlit Cloud**
   - Go to [share.streamlit.io](https://share.streamlit.io)
   - Click "New app"
   - Select your GitHub repository
   - Set:
     - **Main file path**: `app.py`
     - **Branch**: `main` (or your default branch)
   - Click "Deploy"

3. **Configure Secrets**
   - In the app settings, go to "Secrets"
   - Add the three secrets listed above
   - Save

4. **Wait for Deployment**
   - Streamlit will automatically build and deploy your app
   - Check the logs if deployment fails

### 4. Expected Behavior

Once deployed, the app will:

- **Display the dashboard** with today's NBA games
- **Cache data for 30 seconds** to reduce API calls
- **Auto-refresh** when cache expires (every 30 seconds)
- **Manual refresh** via "Refresh Now" button in sidebar
- **Show timestamp** of last update
- **Display game count** for today

### 5. Troubleshooting

**Error: Missing credentials**
- Ensure all three secrets are set in Streamlit Cloud
- Check that secret names match exactly: `KALSHI_API_KEY_ID`, `KALSHI_PRIVATE_KEY_PEM`, `UNABATED_API_KEY`

**Error: Failed to load dashboard data**
- Check the app logs in Streamlit Cloud
- Verify API credentials are valid
- Ensure network access (Streamlit Cloud should have internet access)

**Dashboard not displaying**
- Check browser console for JavaScript errors
- Verify HTML generation is working (check logs)
- Try the "Refresh Now" button

### 6. Local vs. Streamlit Behavior

**Local CLI (`python orchestrator_moneylines.py`):**
- Opens HTML dashboard in default browser
- Prints console version of table
- Uses local credential files (if available)

**Streamlit (`streamlit run app.py`):**
- Embeds HTML dashboard in Streamlit interface
- Uses environment variables for credentials
- No browser opening or file I/O
- Caching enabled for performance

### 7. API Rate Limits

The app uses caching (30-second TTL) to minimize API calls. However, be aware of:

- **Unabated API**: Check their rate limits
- **Kalshi API**: Check their rate limits

If you hit rate limits, increase the cache TTL in `app.py`:

```python
@st.cache_data(ttl=60)  # Increase to 60 seconds or more
```

### 8. Updating the App

To update the deployed app:

1. Make changes to code
2. Commit and push to GitHub
3. Streamlit Cloud will automatically redeploy

The app will rebuild on each push to the connected branch.
