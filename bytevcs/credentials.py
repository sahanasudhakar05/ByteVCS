"""Stored CLI login (server URL + personal access token)."""

from __future__ import annotations

import json
import os
from pathlib import Path

import httpx
from urllib.parse import urlparse

from bytevcs.repository import ByteVCSError

DEFAULT_SERVER = "http://127.0.0.1:8000"


def _origin(url: str) -> tuple[str, str, int]:
    parsed = urlparse(url if "//" in url else "//" + url)
    host = (parsed.hostname or "").lower()
    if host in ("localhost", "127.0.0.1", "::1"):
        host = "localhost"
    scheme = (parsed.scheme or "http").lower()
    port = parsed.port or (443 if scheme == "https" else 80)
    return scheme, host, port


def is_local(url: str) -> bool:
    return _origin(url)[1] == "localhost"


def same_server(a: str, b: str) -> bool:
    """localhost and 127.0.0.1 count as the same machine."""
    return _origin(a) == _origin(b)


def check_secure(server: str) -> None:
    """Never send a token over an unencrypted connection (except to this computer)."""
    if _origin(server)[0] != "https" and not is_local(server):
        raise ByteVCSError(
            "Refusing to send your token over an unencrypted connection. "
            "Use an https:// server address."
        )


def _config_dir() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "bytevcs"


def _path() -> Path:
    return _config_dir() / "credentials.json"


def load_credentials() -> dict:
    """Return {'server': ..., 'token': ...}. Environment variables win."""
    data: dict = {}
    path = _path()
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
    server = os.environ.get("BYTEVCS_SERVER") or data.get("server") or DEFAULT_SERVER
    token = os.environ.get("BYTEVCS_TOKEN") or data.get("token") or ""
    return {"server": server.rstrip("/"), "token": token}


def save_credentials(server: str, token: str) -> None:
    folder = _config_dir()
    folder.mkdir(parents=True, exist_ok=True)
    path = _path()
    path.write_text(
        json.dumps({"server": server.rstrip("/"), "token": token}) + "\n",
        encoding="utf-8",
    )
    try:
        path.chmod(0o600)  # owner-only where the OS supports it
    except OSError:
        pass


def clear_credentials() -> bool:
    path = _path()
    if path.is_file():
        path.unlink()
        return True
    return False


def auth_headers() -> dict:
    token = load_credentials()["token"]
    return {"Authorization": f"Bearer {token}"} if token else {}


def whoami(server: str | None = None, token: str | None = None) -> dict:
    creds = load_credentials()
    server = (server or creds["server"]).rstrip("/")
    token = token or creds["token"]
    if token:
        check_secure(server)
    if not token:
        raise ByteVCSError("Not logged in. Run: bytevcs login")
    try:
        response = httpx.get(
            f"{server}/api/me",
            headers={"Authorization": f"Bearer {token}"},
            timeout=20,
        )
    except httpx.HTTPError as exc:
        raise ByteVCSError(f"Could not reach {server}: {exc}") from exc
    if response.status_code == 401:
        raise ByteVCSError("That token is invalid or has been revoked.")
    if response.status_code != 200:
        raise ByteVCSError(f"Server error ({response.status_code}).")
    return response.json()


def login(server: str, token: str) -> str:
    token = token.strip()
    if not token.startswith("bvcs_"):
        raise ByteVCSError("That doesn't look like a ByteVCS token (it starts with bvcs_).")
    info = whoami(server, token)
    if not info.get("username"):
        raise ByteVCSError("Your account has no username yet. Choose one on the website first.")
    save_credentials(server, token)
    return f"Logged in as {info['username']} on {server.rstrip('/')}"
