"""
API clients for Kalshi and Unabated.
"""

import time
import requests
from typing import Dict, Any, Optional, Tuple
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.backends import default_backend

from data_build import config


class UnabatedClient:
    """Client for Unabated API."""
    
    def __init__(self, api_key: str = None):
        self.api_key = api_key or config.UNABATED_API_KEY
        if not self.api_key:
            raise ValueError(
                "Unabated API key not configured. "
                "For local runs, set UNABATED_API_KEY in creds_local.txt/creds.txt (recommended), "
                "secrets_local.py, or environment variables."
            )
    
    def fetch_snapshot(self) -> Dict[str, Any]:
        """Fetch Unabated game odds snapshot."""
        url = f"{config.UNABATED_PROD_URL}?x-api-key={self.api_key}"
        
        try:
            resp = requests.get(url, timeout=20)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            raise Exception(f"Failed to fetch Unabated snapshot: {e}")


class KalshiClient:
    """Client for Kalshi API."""
    
    def __init__(self, api_key_id: str = None, private_key_pem: str = None):
        if api_key_id and private_key_pem:
            self.api_key_id = api_key_id
            self.private_key_pem = private_key_pem
        else:
            self.api_key_id, self.private_key_pem = self._load_creds()
    
    def _load_creds(self) -> Tuple[str, str]:
        """
        Load Kalshi API credentials from environment variables (Streamlit) or files (local).
        
        Priority:
        1. Environment variables (KALSHI_API_KEY_ID, KALSHI_PRIVATE_KEY_PEM) - for Streamlit Cloud
        2. Local files (kalshi_api_key_id.txt, kalshi_private_key.pem) - for local development
        """
        # Delegate to the shared loader (supports env vars, creds.txt, and local files)
        from utils.kalshi_api import load_creds
        return load_creds()
    
    def _sign_request(self, message: str) -> str:
        """Sign a message using RSA-PSS + SHA256."""
        private_key = serialization.load_pem_private_key(
            self.private_key_pem.encode() if isinstance(self.private_key_pem, str) else self.private_key_pem,
            password=None,
            backend=default_backend()
        )
        
        signature = private_key.sign(
            message.encode('utf-8'),
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.MAX_LENGTH
            ),
            hashes.SHA256()
        )
        
        import base64
        return base64.b64encode(signature).decode('utf-8')
    
    def make_request(
        self,
        method: str,
        path: str,
        body: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Make an authenticated Kalshi API request."""
        timestamp = str(int(time.time() * 1000))
        
        # Ensure path starts with /trade-api/v2
        sign_path = path if path.startswith("/trade-api/v2") else "/trade-api/v2" + path
        message = timestamp + method.upper() + sign_path
        
        signature = self._sign_request(message)
        
        headers = {
            "KALSHI-ACCESS-KEY": self.api_key_id,
            "KALSHI-ACCESS-TIMESTAMP": timestamp,
            "KALSHI-ACCESS-SIGNATURE": signature,
            "Content-Type": "application/json",
        }
        
        url = config.KALSHI_BASE_URL + path
        
        try:
            if method.upper() == "GET":
                resp = requests.get(url, headers=headers, params=body, timeout=20)
            elif method.upper() == "POST":
                resp = requests.post(url, headers=headers, json=body, timeout=20)
            elif method.upper() == "DELETE":
                resp = requests.delete(url, headers=headers, timeout=20)
            else:
                raise ValueError(f"Unsupported method: {method}")
            
            resp.raise_for_status()
            
            # Handle 204 No Content
            if resp.status_code == 204:
                return {}
            
            # Try to parse JSON, return empty dict if not JSON
            try:
                return resp.json()
            except ValueError:
                return {}
        except requests.exceptions.HTTPError as e:
            print(f"❌ Kalshi API error: {e}")
            if hasattr(e.response, 'text'):
                print(f"Response: {e.response.text}")
            raise
        except Exception as e:
            print(f"❌ Kalshi API request failed: {e}")
            raise