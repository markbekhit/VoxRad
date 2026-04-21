"""OAuth2 / OIDC authentication for VoxRad Web.

Supported providers: Google, Microsoft (Azure AD / Entra ID).

Configuration is stored in settings.ini [OAUTH] section and can be edited
through the web Settings page.  Environment variables override settings.ini
values when both are present (useful for Docker / 12-factor deployments).

  RedirectBaseURL        — public base URL, e.g. https://voxrad.example.com
  GoogleClientID / GoogleClientSecret
  MicrosoftClientID / MicrosoftClientSecret
  SessionSecretKey       — auto-generated on first run; never needs manual entry

If neither Google nor Microsoft credentials are present, oauth_enabled is False
and the server falls back to HTTP Basic Auth.
"""

from __future__ import annotations

import os
import secrets
import sqlite3
import urllib.parse
from typing import Optional

import httpx
from fastapi import HTTPException, Request
from config.config import config

# ---------------------------------------------------------------------------
# Lazy helpers — read from config at call time so values set via the Settings
# UI take effect without a server restart (for the next login only).
# ---------------------------------------------------------------------------


def _client_id(provider: str) -> str:
    return (config.google_client_id if provider == "google" else config.microsoft_client_id) or ""


def _client_secret(provider: str) -> str:
    return (config.google_client_secret if provider == "google" else config.microsoft_client_secret) or ""


def _redirect_base() -> str:
    return (config.oauth_redirect_base_url or "").rstrip("/") or "http://localhost:8765"


@property  # type: ignore[misc]
def google_enabled() -> bool:  # type: ignore[override]
    return bool(config.google_client_id and config.google_client_secret)


@property  # type: ignore[misc]
def microsoft_enabled() -> bool:  # type: ignore[override]
    return bool(config.microsoft_client_id and config.microsoft_client_secret)


@property  # type: ignore[misc]
def oauth_enabled() -> bool:  # type: ignore[override]
    return bool(
        (config.google_client_id and config.google_client_secret)
        or (config.microsoft_client_id and config.microsoft_client_secret)
    )


def _google_enabled() -> bool:
    return bool(config.google_client_id and config.google_client_secret)


def _microsoft_enabled() -> bool:
    return bool(config.microsoft_client_id and config.microsoft_client_secret)


def _oauth_enabled() -> bool:
    return _google_enabled() or _microsoft_enabled()


# Module-level aliases that app.py imports — these are callables that reflect
# the current config state rather than a value frozen at import time.
google_enabled    = _google_enabled
microsoft_enabled = _microsoft_enabled
oauth_enabled     = _oauth_enabled


def SESSION_SECRET_KEY() -> str:  # noqa: N802
    return config.session_secret_key or ""

# ---------------------------------------------------------------------------
# SQLite user database
# ---------------------------------------------------------------------------

_STYLE_DEFAULTS: dict = {
    "spelling":              "british",
    "numerals":              "roman",
    "measurement_unit":      "auto",
    "measurement_separator": "x",
    "decimal_precision":     1,
    "laterality":            "full",
    "impression_style":      "bulleted",
    "negation_phrasing":     "no_evidence_of",
    "date_format":           "dd_mm_yyyy",
    "fhir_export_enabled":   False,
}


def _db_path() -> str:
    if override := os.environ.get("VOXRAD_DB_PATH"):
        os.makedirs(os.path.dirname(override), exist_ok=True)
        return override
    if os.name == "nt":
        config_dir = os.path.join(os.environ["APPDATA"], "VOXRAD")
    else:
        config_dir = os.path.join(os.path.expanduser("~"), ".voxrad")
    os.makedirs(config_dir, exist_ok=True)
    return os.path.join(config_dir, "users.db")


def _conn() -> sqlite3.Connection:
    return sqlite3.connect(_db_path(), timeout=10, check_same_thread=False)


def init_db() -> None:
    """Create database tables idempotently."""
    with _conn() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id         INTEGER PRIMARY KEY,
                email      TEXT    UNIQUE NOT NULL,
                name       TEXT,
                provider   TEXT,
                created_at TEXT    DEFAULT CURRENT_TIMESTAMP,
                last_login TEXT    DEFAULT CURRENT_TIMESTAMP
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS user_settings (
                user_id                     INTEGER PRIMARY KEY REFERENCES users(id),
                style_spelling              TEXT    DEFAULT 'british',
                style_numerals              TEXT    DEFAULT 'roman',
                style_measurement_unit      TEXT    DEFAULT 'auto',
                style_measurement_separator TEXT    DEFAULT 'x',
                style_decimal_precision     INTEGER DEFAULT 1,
                style_laterality            TEXT    DEFAULT 'full',
                style_impression_style      TEXT    DEFAULT 'bulleted',
                style_negation_phrasing     TEXT    DEFAULT 'no_evidence_of',
                style_date_format           TEXT    DEFAULT 'dd_mm_yyyy',
                fhir_export_enabled         INTEGER DEFAULT 0
            )
        """)
        db.commit()


def get_or_create_user(email: str, name: str, provider: str) -> dict:
    """Upsert a user by e-mail and return their basic record."""
    email = email.lower().strip()
    with _conn() as db:
        db.execute("""
            INSERT INTO users (email, name, provider, last_login)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(email) DO UPDATE SET
                name       = excluded.name,
                last_login = CURRENT_TIMESTAMP
        """, (email, name, provider))
        db.commit()
        row = db.execute(
            "SELECT id, email, name FROM users WHERE email = ?", (email,)
        ).fetchone()
    return {"id": row[0], "email": row[1], "name": row[2]}


def get_user_style(user_id: int) -> dict:
    """Return the user's style settings dict (with defaults for missing rows)."""
    with _conn() as db:
        row = db.execute(
            "SELECT style_spelling, style_numerals, style_measurement_unit, "
            "style_measurement_separator, style_decimal_precision, "
            "style_laterality, style_impression_style, style_negation_phrasing, "
            "style_date_format, fhir_export_enabled "
            "FROM user_settings WHERE user_id = ?",
            (user_id,)
        ).fetchone()
    if not row:
        return dict(_STYLE_DEFAULTS)
    return {
        "spelling":              row[0],
        "numerals":              row[1],
        "measurement_unit":      row[2],
        "measurement_separator": row[3],
        "decimal_precision":     row[4],
        "laterality":            row[5],
        "impression_style":      row[6],
        "negation_phrasing":     row[7],
        "date_format":           row[8],
        "fhir_export_enabled":   bool(row[9]),
    }


def save_user_style(user_id: int, style: dict) -> None:
    """Upsert a user's style settings row."""
    with _conn() as db:
        db.execute("""
            INSERT INTO user_settings (
                user_id, style_spelling, style_numerals, style_measurement_unit,
                style_measurement_separator, style_decimal_precision,
                style_laterality, style_impression_style, style_negation_phrasing,
                style_date_format, fhir_export_enabled
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                style_spelling              = excluded.style_spelling,
                style_numerals              = excluded.style_numerals,
                style_measurement_unit      = excluded.style_measurement_unit,
                style_measurement_separator = excluded.style_measurement_separator,
                style_decimal_precision     = excluded.style_decimal_precision,
                style_laterality            = excluded.style_laterality,
                style_impression_style      = excluded.style_impression_style,
                style_negation_phrasing     = excluded.style_negation_phrasing,
                style_date_format           = excluded.style_date_format,
                fhir_export_enabled         = excluded.fhir_export_enabled
        """, (
            user_id,
            style.get("spelling",              "british"),
            style.get("numerals",              "roman"),
            style.get("measurement_unit",      "auto"),
            style.get("measurement_separator", "x"),
            int(style.get("decimal_precision", 1)),
            style.get("laterality",            "full"),
            style.get("impression_style",      "bulleted"),
            style.get("negation_phrasing",     "no_evidence_of"),
            style.get("date_format",           "dd_mm_yyyy"),
            int(bool(style.get("fhir_export_enabled", False))),
        ))
        db.commit()


# ---------------------------------------------------------------------------
# Session helpers (Starlette SessionMiddleware stores data in request.session)
# ---------------------------------------------------------------------------

def set_session_user(request: Request, user: dict) -> None:
    request.session["user"] = {
        "id":    user["id"],
        "email": user["email"],
        "name":  user["name"],
    }


def get_session_user(request: Request) -> Optional[dict]:
    return request.session.get("user")


def clear_session(request: Request) -> None:
    request.session.clear()


# ---------------------------------------------------------------------------
# OAuth redirect URL helpers
# ---------------------------------------------------------------------------

def _google_redirect_uri() -> str:
    return f"{_redirect_base()}/auth/google/callback"


def _microsoft_redirect_uri() -> str:
    return f"{_redirect_base()}/auth/microsoft/callback"


def google_auth_url(state: str) -> str:
    return "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode({
        "client_id":     _client_id("google"),
        "redirect_uri":  _google_redirect_uri(),
        "response_type": "code",
        "scope":         "openid email profile",
        "state":         state,
        "access_type":   "online",
        "prompt":        "select_account",
    })


def microsoft_auth_url(state: str) -> str:
    return "https://login.microsoftonline.com/common/oauth2/v2.0/authorize?" + urllib.parse.urlencode({
        "client_id":       _client_id("microsoft"),
        "redirect_uri":    _microsoft_redirect_uri(),
        "response_type":   "code",
        "scope":           "openid email profile",
        "state":           state,
        "prompt":          "select_account",
        "response_mode":   "query",
    })


# ---------------------------------------------------------------------------
# OAuth code-exchange
# ---------------------------------------------------------------------------

def exchange_google_code(code: str) -> dict:
    """Exchange Google auth code → user dict {email, name}."""
    redirect_uri = _google_redirect_uri()
    with httpx.Client(timeout=15) as client:
        tok = client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "code":          code,
                "client_id":     _client_id("google"),
                "client_secret": _client_secret("google"),
                "redirect_uri":  redirect_uri,
                "grant_type":    "authorization_code",
            },
        )
        tok.raise_for_status()
        access_token = tok.json()["access_token"]

        info = client.get(
            "https://www.googleapis.com/oauth2/v3/userinfo",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        info.raise_for_status()
        data = info.json()

    return {
        "email": data.get("email", ""),
        "name":  data.get("name", data.get("email", "Unknown")),
    }


def exchange_microsoft_code(code: str) -> dict:
    """Exchange Microsoft auth code → user dict {email, name}."""
    redirect_uri = _microsoft_redirect_uri()
    with httpx.Client(timeout=15) as client:
        tok = client.post(
            "https://login.microsoftonline.com/common/oauth2/v2.0/token",
            data={
                "code":          code,
                "client_id":     _client_id("microsoft"),
                "client_secret": _client_secret("microsoft"),
                "redirect_uri":  redirect_uri,
                "grant_type":    "authorization_code",
                "scope":         "openid email profile",
            },
        )
        tok.raise_for_status()
        access_token = tok.json()["access_token"]

        me = client.get(
            "https://graph.microsoft.com/v1.0/me",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        me.raise_for_status()
        data = me.json()

    email = data.get("mail") or data.get("userPrincipalName", "")
    name  = data.get("displayName") or email
    return {"email": email, "name": name}


# ---------------------------------------------------------------------------
# FastAPI auth dependency
# ---------------------------------------------------------------------------

def require_oauth_user(request: Request) -> dict:
    """Return the current session user or redirect to /login."""
    user = get_session_user(request)
    if not user:
        raise HTTPException(
            status_code=307,
            headers={"Location": "/login"},
        )
    return user
