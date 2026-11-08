"""Authentication for the ByteVCS hosted service.

Two kinds of bearer credential:
  - Supabase session tokens (website login), verified with Supabase's public keys
  - ByteVCS personal access tokens ("bvcs_...") used by the CLI
"""

import hashlib
import os
import uuid
from datetime import datetime, timedelta, timezone
from functools import lru_cache

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import PyJWKClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from api.database import SessionLocal
from api.models import ApiToken, User

bearer_scheme = HTTPBearer(auto_error=False)
ALLOWED_ALGORITHMS = ["ES256", "RS256"]  # asymmetric only; never HS256 here
PAT_PREFIX = "bvcs_"


def _supabase_url() -> str:
    url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
    if not url:
        raise RuntimeError("SUPABASE_URL is not set in .env")
    return url


@lru_cache(maxsize=1)
def _jwks_client() -> PyJWKClient:
    return PyJWKClient(f"{_supabase_url()}/auth/v1/.well-known/jwks.json")


def get_db():
    if SessionLocal is None:
        raise HTTPException(503, "Database is not configured.")
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def hash_token(token: str) -> str:
    # Tokens are long and random, so a fast hash is appropriate here.
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


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


def _jwt_user(token: str, db: Session) -> User:
    claims = verify_token(token)
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


def _pat_user(token: str, db: Session) -> User:
    row = db.scalar(select(ApiToken).where(ApiToken.token_hash == hash_token(token)))
    if row is None:
        raise HTTPException(401, "Invalid or revoked token.")
    now = datetime.now(timezone.utc)
    if row.last_used_at is None or now - row.last_used_at > timedelta(minutes=5):
        row.last_used_at = now
        db.commit()
    user = db.get(User, row.user_id)
    if user is None:
        raise HTTPException(401, "Invalid or revoked token.")
    return user


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    """Website session token OR CLI personal access token."""
    if credentials is None:
        raise HTTPException(401, "Authentication required.")
    token = credentials.credentials
    if token.startswith(PAT_PREFIX):
        return _pat_user(token, db)
    return _jwt_user(token, db)


def get_session_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    """Website login only. Used for managing tokens themselves."""
    if credentials is None:
        raise HTTPException(401, "Authentication required.")
    if credentials.credentials.startswith(PAT_PREFIX):
        raise HTTPException(401, "Log in on the website to manage tokens.")
    return _jwt_user(credentials.credentials, db)


def get_optional_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User | None:
    """Anonymous visitors get None; a bad token is still a 401."""
    if credentials is None:
        return None
    return get_current_user(credentials, db)