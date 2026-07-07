"""
Supabase JWT verification for FastAPI.

Backend only verifies JWTs — Google OAuth is handled entirely by Supabase Auth
and the frontend. Anonymous requests are identified by a client-generated
`X-Device-Id` header (a UUID persisted in the frontend's localStorage), not by
anything issued server-side.
"""
from __future__ import annotations

import os
import uuid
from typing import Optional

import jwt
from fastapi import Header, HTTPException, status
from pydantic import BaseModel

_JWT_SECRET = os.environ["SUPABASE_JWT_SECRET"]
_JWT_ALGO = "HS256"


class AuthedUser(BaseModel):
    id: str
    email: str


def _decode(token: str) -> Optional[AuthedUser]:
    try:
        payload = jwt.decode(
            token, _JWT_SECRET, algorithms=[_JWT_ALGO], options={"verify_aud": False}
        )
        return AuthedUser(id=payload["sub"], email=payload.get("email", ""))
    except jwt.PyJWTError:
        return None


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
