"""
Authentication helpers for SpendSense.

Verifies Supabase-issued JWTs on every protected request.
Usage in FastAPI routes:
    current_user: UserClaims = Depends(get_current_user)
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

import httpx
from dotenv import load_dotenv
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY", "")

# Dev mode: when SUPABASE_URL is not configured, skip auth entirely.
# All requests are treated as a single local user. Never use in production.
DEV_MODE: bool = not SUPABASE_URL

_DEV_USER = None   # populated lazily below
_bearer = HTTPBearer(auto_error=False)   # auto_error=False so dev mode can skip the header


@dataclass
class UserClaims:
    id: str           # Supabase user UUID (or "local-dev" in dev mode)
    email: str
    is_paid: bool     # True if active Pro subscription


def _dev_user() -> UserClaims:
    return UserClaims(id="local-dev", email="local@dev", is_paid=True)


def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
) -> UserClaims:
    """
    FastAPI dependency.
    - Dev mode (no SUPABASE_URL): returns a hardcoded local user, no token needed.
    - Production: validates Bearer JWT with Supabase and returns user claims.
    """
    if DEV_MODE:
        return _dev_user()

    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Bearer token.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = credentials.credentials

    try:
        resp = httpx.get(
            f"{SUPABASE_URL}/auth/v1/user",
            headers={
                "Authorization": f"Bearer {token}",
                "apikey": SUPABASE_ANON_KEY,
            },
            timeout=10,
        )
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Auth service unreachable.",
        ) from exc

    if resp.status_code != 200:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    data = resp.json()
    user_id: str = data.get("id", "")
    email: str = data.get("email", "")
    if not user_id:
        raise HTTPException(status_code=401, detail="Token missing user ID.")

    meta = data.get("user_metadata", {})
    is_paid: bool = meta.get("is_paid", False) is True

    return UserClaims(id=user_id, email=email, is_paid=is_paid)


def require_pro(current_user: UserClaims = Depends(get_current_user)) -> UserClaims:
    """
    FastAPI dependency.
    Same as get_current_user but also enforces an active Pro subscription.
    Use on endpoints that are Pro-only.
    """
    if not current_user.is_paid:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail="Upgrade to Pro to use this feature.",
        )
    return current_user
