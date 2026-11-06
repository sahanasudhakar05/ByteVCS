"""Supabase Auth token verification for the ByteVCS hosted service."""

import os
import uuid
from functools import lru_cache

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import PyJWKClient
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from api.database import SessionLocal
from api.models import User

bearer_scheme = HTTPBearer(auto_error=False)
ALLOWED_ALGORITHMS = ["ES256", "RS256"]  # asymmetric only; never HS256 here


def _supabase_url() -> str:
    url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
    if not url:
        raise RuntimeError("SUPABASE_URL is not set in .env")
    return url


@lru_cache(maxsize=1)
def _jwks_client() -> PyJWKClient:
    # Fetches Supabase's public keys and caches them.
    return PyJWKClient(f"{_supabase_url()}/auth/v1/.well-known/jwks.json")


def get_db():
    if SessionLocal is None:
        raise HTTPException(503, "Database is not configured.")
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def verify_token(token: str) -> dict:
    """Verify signature, expiry, issuer and audience. Returns the claims."""
    try:
        signing_key = _jwks_client().get_signing_key_from_jwt(token)
        return jwt.decode(
            token,
            signing_key.key,
            algorithms=ALLOWED_ALGORITHMS,
            audience="authenticated",
            issuer=f"{_supabase_url()}/auth/v1",
            leeway=30,
            options={"require": ["sub", "exp", "iss", "aud"]},
        )
    except (jwt.PyJWTError, jwt.exceptions.PyJWKClientError) as exc:
        print(f"TOKEN REJECTED: {type(exc).__name__}: {exc}")
        raise HTTPException(401, "Invalid or expired token.")

def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    if credentials is None:
        raise HTTPException(401, "Authentication required.")

    claims = verify_token(credentials.credentials)
    try:
        user_id = uuid.UUID(claims["sub"])
    except ValueError:
        raise HTTPException(401, "Invalid token subject.")

    user = db.get(User, user_id)
    if user is None:
        # First request from this account: create the profile row.
        user = User(id=user_id, email=claims.get("email") or "")
        db.add(user)
        try:
            db.commit()
        except IntegrityError:  # two simultaneous first requests
            db.rollback()
            user = db.get(User, user_id)
    return user

def get_optional_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User | None:
    """Anonymous visitors get None; a bad token is still a 401."""
    if credentials is None:
        return None
    return get_current_user(credentials, db)