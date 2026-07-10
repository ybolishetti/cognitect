"""
Supabase JWT verification for FastAPI.

Backend only verifies JWTs — Google OAuth is handled entirely by Supabase Auth
and the frontend. Anonymous requests are identified by a client-generated
`X-Device-Id` header (a UUID persisted in the frontend's localStorage), not by
anything issued server-side.

Supabase JWT signing keys migration
-----------------------------------
Supabase migrated new projects (roughly mid-2026) to asymmetric ES256 JWTs by
default, phasing out the legacy symmetric HS256 flow that used a shared
`SUPABASE_JWT_SECRET`. This module handles both:

1. If the token header advertises an asymmetric alg (ES256 / RS256 / EdDSA),
   we fetch the project's JWKS from `<SUPABASE_URL>/auth/v1/.well-known/jwks.json`,
   look up the key by `kid`, and verify.
2. If it advertises HS256, we fall back to the legacy `SUPABASE_JWT_SECRET`
   shared secret (still works while the legacy key is retained in the
   project's signing-key rotation).

JWKS is cached in-process for 10 minutes. On cache miss / stale, we refetch.
This keeps request latency low without needing a signing-key rotation to
force a redeploy.
"""
from __future__ import annotations

import logging
import os
import threading
import time
import uuid
from typing import Any, Optional

import httpx
import jwt
from fastapi import Header, HTTPException, status
from jwt.algorithms import ECAlgorithm, RSAAlgorithm
from pydantic import BaseModel

logger = logging.getLogger(__name__)

# Legacy HS256 shared secret. Still required as a fallback while any users
# might hold HS256-signed tokens (or if the project is rolled back). Kept as
# a hard requirement because the boot-time crash surface is what we want if
# it's missing — silent-fallback-to-no-auth would be worse.
_JWT_SECRET = os.environ["SUPABASE_JWT_SECRET"]

# Supabase project URL — needed to locate JWKS. Falls back to SUPABASE_URL
# (already set for the supabase-py client) if a dedicated env isn't provided.
_SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
_JWKS_URL = f"{_SUPABASE_URL}/auth/v1/.well-known/jwks.json" if _SUPABASE_URL else None

_ASYMMETRIC_ALGS = {"ES256", "RS256", "EdDSA"}
_JWKS_TTL_SECONDS = 600  # 10 min

# In-process JWKS cache. Fine for a single Cloud Run instance; each instance
# fetches independently on cold start, which is <100ms so no coordination
# needed.
_jwks_lock = threading.Lock()
_jwks_cache: dict[str, Any] = {"fetched_at": 0.0, "keys_by_kid": {}}


class AuthedUser(BaseModel):
    id: str
    email: str


def _fetch_jwks() -> dict[str, Any]:
    """Fetch the project JWKS. Raises on network / parse failure."""
    if not _JWKS_URL:
        raise RuntimeError("SUPABASE_URL not set; cannot fetch JWKS")
    resp = httpx.get(_JWKS_URL, timeout=5.0)
    resp.raise_for_status()
    data = resp.json()
    keys_by_kid: dict[str, Any] = {}
    for jwk in data.get("keys", []):
        kid = jwk.get("kid")
        if not kid:
            continue
        kty = jwk.get("kty")
        if kty == "EC":
            keys_by_kid[kid] = ECAlgorithm.from_jwk(jwk)
        elif kty == "RSA":
            keys_by_kid[kid] = RSAAlgorithm.from_jwk(jwk)
        # Ed25519 (OKP) not handled — PyJWT supports it via EdDSAAlgorithm,
        # but Supabase currently only issues EC/RSA.
    return keys_by_kid


def _get_signing_key(kid: str) -> Any:
    """Return a verified signing key for the given kid, refreshing JWKS if needed."""
    now = time.time()
    with _jwks_lock:
        fresh = (now - _jwks_cache["fetched_at"]) < _JWKS_TTL_SECONDS
        keys = _jwks_cache["keys_by_kid"]
        if fresh and kid in keys:
            return keys[kid]

    # Refetch outside the lock — network I/O shouldn't block other requests.
    try:
        new_keys = _fetch_jwks()
    except Exception as e:
        logger.warning("JWKS fetch failed: %s", e)
        # Fall back to whatever we already had cached — better than 500ing
        # every request during a transient JWKS blip.
        with _jwks_lock:
            keys = _jwks_cache["keys_by_kid"]
        if kid in keys:
            return keys[kid]
        raise

    with _jwks_lock:
        _jwks_cache["fetched_at"] = time.time()
        _jwks_cache["keys_by_kid"] = new_keys

    if kid not in new_keys:
        raise KeyError(f"Unknown kid {kid!r} in JWKS")
    return new_keys[kid]


def _decode(token: str) -> Optional[AuthedUser]:
    """Verify + decode a bearer token. Returns None on any failure."""
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError:
        return None

    alg = header.get("alg")
    try:
        if alg in _ASYMMETRIC_ALGS:
            kid = header.get("kid")
            if not kid:
                return None
            key = _get_signing_key(kid)
            payload = jwt.decode(
                token, key, algorithms=[alg], options={"verify_aud": False}
            )
        elif alg == "HS256":
            payload = jwt.decode(
                token,
                _JWT_SECRET,
                algorithms=["HS256"],
                options={"verify_aud": False},
            )
        else:
            logger.info("Rejecting token with unsupported alg=%r", alg)
            return None
    except jwt.PyJWTError as e:
        logger.info("Token verify failed: %s", e)
        return None
    except Exception as e:
        # JWKS fetch fail, unknown kid, etc — treat as auth failure, not 500.
        logger.warning("Token verify errored: %s", e)
        return None

    sub = payload.get("sub")
    if not sub:
        # Anon key / service-role tokens have valid signatures but no user
        # identity — reject cleanly instead of crashing on KeyError.
        return None
    return AuthedUser(id=sub, email=payload.get("email", ""))


def validate_device_id(x_device_id: Optional[str]) -> Optional[str]:
    if x_device_id is None:
        return None
    try:
        uuid.UUID(x_device_id)
    except ValueError:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, f"Invalid X-Device-Id: {x_device_id!r}"
        )
    return x_device_id


async def require_user(authorization: str = Header(...)) -> AuthedUser:
    """Dependency for routes that require an authenticated user."""
    if not authorization.startswith("Bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing bearer token")
    user = _decode(authorization[7:])
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token")
    return user


async def optional_user(
    authorization: Optional[str] = Header(None),
    x_device_id: Optional[str] = Header(None),
) -> tuple[Optional[AuthedUser], Optional[str]]:
    """
    Dependency for routes that accept either an authenticated user or an
    anonymous device_id. Returns (user, device_id) — exactly one populated,
    or both None if neither was supplied (route decides how to handle that).
    """
    user = None
    if authorization and authorization.startswith("Bearer "):
        user = _decode(authorization[7:])
    device_id = validate_device_id(x_device_id) if not user else None
    return user, device_id
