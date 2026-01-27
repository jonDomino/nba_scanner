"""
Local credentials loader.

Supports a simple gitignored `creds.txt` file in the project root for local runs.

Format (one per line):
  KEY=VALUE

Rules:
- Blank lines and lines starting with '#' are ignored
- Values may be quoted with single or double quotes
- If VALUE starts with '@', it is treated as a file path and the file contents are loaded
- Common escape sequences in quoted values are unescaped (e.g., '\\n' -> newline)

Looked-up file names (in project root):
- creds_local.txt (preferred)
- creds.txt
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, Optional


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _strip_quotes(s: str) -> str:
    s = s.strip()
    if len(s) >= 2 and ((s[0] == s[-1] == '"') or (s[0] == s[-1] == "'")):
        return s[1:-1]
    return s


def _unescape(s: str) -> str:
    # Only unescape common sequences; keep it predictable
    return (
        s.replace("\\n", "\n")
        .replace("\\r", "\r")
        .replace("\\t", "\t")
    )


def _load_value(raw: str, base_dir: Path) -> str:
    raw = raw.strip()
    raw = _strip_quotes(raw)
    raw = _unescape(raw)

    if raw.startswith("@"):
        path_raw = raw[1:].strip()
        path = Path(path_raw)
        if not path.is_absolute():
            path = base_dir / path_raw
        try:
            return path.read_text(encoding="utf-8")
        except FileNotFoundError:
            # Treat missing referenced files as "unset" rather than crashing at import time.
            # Callers can decide whether the missing secret is required.
            return ""

    return raw


def load_creds_file() -> Dict[str, str]:
    """
    Load credentials from creds_local.txt or creds.txt in project root.
    Returns {} if no creds file exists.
    """
    root = _project_root()
    candidates = [root / "creds_local.txt", root / "creds.txt"]
    creds_path: Optional[Path] = next((p for p in candidates if p.exists()), None)
    if not creds_path:
        return {}

    out: Dict[str, str] = {}
    for line in creds_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        key = k.strip()
        if not key:
            continue
        out[key] = _load_value(v, creds_path.parent)
    return out


def apply_creds_to_environ(keys: Optional[list[str]] = None) -> None:
    """
    Load creds file and set os.environ for provided keys (or all keys found),
    without overwriting existing environment variables.
    """
    creds = load_creds_file()
    if not creds:
        return

    if keys is None:
        keys = list(creds.keys())

    for k in keys:
        if k in creds and not os.getenv(k):
            os.environ[k] = str(creds[k])

