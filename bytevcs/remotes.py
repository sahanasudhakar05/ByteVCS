
"""Remote configuration for ByteVCS repositories."""

import json
from pathlib import Path
from urllib.parse import urlparse

from bytevcs.repository import ByteVCSError, _metadata


def _config_path(repo_path: str | Path) -> Path:
    return _metadata(Path(repo_path)) / "config"


def list_remotes(repo_path: str | Path) -> dict[str, str]:
    """Return configured remote names and URLs."""
    config_path = _config_path(repo_path)

    if not config_path.exists():
        return {}

    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ByteVCSError(f"Invalid remote configuration: {exc}") from exc

    remotes = config.get("remotes", {})
    if not isinstance(remotes, dict):
        raise ByteVCSError("Invalid remote configuration.")

    return remotes


def add_remote(repo_path: str | Path, name: str, url: str) -> None:
    """Add a named remote URL."""
    if not name or any(c.isspace() for c in name) or "/" in name:
        raise ByteVCSError("Invalid remote name.")

    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ByteVCSError("Remote URL must be an HTTP or HTTPS URL.")

    remotes = list_remotes(repo_path)
    if name in remotes:
        raise ByteVCSError(f"Remote already exists: {name}")

    remotes[name] = url.rstrip("/")
    _config_path(repo_path).write_text(
        json.dumps({"remotes": remotes}, indent=2) + "\n",
        encoding="utf-8",
    )


def remove_remote(repo_path: str | Path, name: str) -> None:
    """Remove a named remote."""
    remotes = list_remotes(repo_path)

    if name not in remotes:
        raise ByteVCSError(f"Remote does not exist: {name}")

    del remotes[name]
    _config_path(repo_path).write_text(
        json.dumps({"remotes": remotes}, indent=2) + "\n",
        encoding="utf-8",
    )
