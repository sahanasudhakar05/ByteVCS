"""Push, pull and clone against a ByteVCS server (protocol v2).

Objects are content-addressed, so only missing objects travel. Every object is
checked against its hash on both ends, and the login token is only ever sent to
the server it was issued by.
"""

from __future__ import annotations

import base64
import hashlib
import re
import shutil
from pathlib import Path
from urllib.parse import urlparse

import httpx

from bytevcs.credentials import load_credentials, whoami
from bytevcs.object_store import read_object, store_object
from bytevcs.remotes import add_remote, list_remotes
from bytevcs.repository import (
    ByteVCSError,
    _current_commit_hash,
    _metadata,
    _read_commit,
    _read_snapshot,
    _status_data,
    _valid_branch_name,
    _validate_snapshot_path,
    find_repository,
    get_current_branch,
    save_index,
)

HASH_RE = re.compile(r"[0-9a-f]{64}")
MAX_PUSH_BYTES = 25 * 1024 * 1024
MAX_PUSH_OBJECTS = 5000
MAX_HASHES = 100_000


class RemoteError(ByteVCSError):
    def __init__(self, message: str, status: int = 0):
        super().__init__(message)
        self.status = status


def _parse_url(url: str) -> tuple[str, str, str]:
    parsed = urlparse(url.rstrip("/"))
    parts = [p for p in parsed.path.split("/") if p]
    if parsed.scheme not in ("http", "https") or not parsed.netloc or len(parts) < 2:
        raise ByteVCSError("Remote URL must look like https://host/username/repository")
    prefix = "/" + "/".join(parts[:-2]) if len(parts) > 2 else ""
    return f"{parsed.scheme}://{parsed.netloc}{prefix}", parts[-2], parts[-1]


class _Remote:
    def __init__(self, url: str):
        self.url = url.rstrip("/")
        self.base, self.owner, self.name = _parse_url(url)
        creds = load_credentials()
        # Never send the token to a server other than the one it was issued for.
        self.token = creds["token"] if creds["server"] == self.base else ""

    def headers(self) -> dict:
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}

    def _request(self, method: str, path: str, **kwargs) -> dict:
        try:
            response = httpx.request(
                method,
                f"{self.base}/api/r/{self.owner}/{self.name}{path}",
                headers=self.headers(),
                timeout=120,
                **kwargs,
            )
        except httpx.HTTPError as exc:
            raise RemoteError(f"Could not reach {self.base}: {exc}") from exc
        if response.status_code >= 400:
            try:
                detail = str(response.json().get("detail", ""))
            except ValueError:
                detail = ""
            if response.status_code == 401:
                detail = "You are not logged in, or your token was revoked. Run: bytevcs login"
            elif response.status_code == 404 and not self.token:
                detail = "Repository not found. If it is private, run: bytevcs login"
            raise RemoteError(detail or f"Server error ({response.status_code}).", response.status_code)
        return response.json()

    def refs(self) -> dict:
        return self._request("GET", "/refs")

    def missing(self, hashes: list[str]) -> list[str]:
        return self._request("POST", "/missing", json={"hashes": hashes})["missing"]

    def push(self, body: dict) -> dict:
        return self._request("POST", "/push", json=body)

    def pack(self, want: list[str], have: list[str]) -> dict:
        return self._request("POST", "/pack", json={"want": want, "have": have})["objects"]


# ---------- local repository helpers ----------

def _heads_dir(repo: Path) -> Path:
    return _metadata(repo) / "refs" / "heads"


def _branch_name(repo: Path) -> str:
    return get_current_branch(repo).relative_to(_heads_dir(repo)).as_posix()


def _write_ref(repo: Path, branch: str, commit: str) -> None:
    if not _valid_branch_name(branch) or not HASH_RE.fullmatch(commit):
        raise ByteVCSError("The server sent an invalid branch or commit.")
    heads = _heads_dir(repo).resolve()
    path = (heads / branch).resolve()
    if not path.is_relative_to(heads):
        raise ByteVCSError("The server sent an invalid branch name.")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(commit + "\n", encoding="utf-8")


def _parents(commit: dict) -> list[str]:
    return [commit[k] for k in ("parent", "merge_parent") if commit.get(k)]


def _local_reachable(repo: Path, tips: list[str]) -> set[str]:
    seen: set[str] = set()
    objects: set[str] = set()
    stack = list(tips)
    while stack:
        c = stack.pop()
        if c in seen:
            continue
        seen.add(c)
        commit = _read_commit(repo, c)
        objects.add(c)
        objects.add(commit["snapshot"])
        objects.update(_read_snapshot(repo, c).values())
        stack.extend(_parents(commit))
    return objects


def _is_ancestor(repo: Path, ancestor: str, descendant: str) -> bool:
    stack, seen = [descendant], set()
    while stack:
        c = stack.pop()
        if c == ancestor:
            return True
        if c in seen:
            continue
        seen.add(c)
        stack.extend(_parents(_read_commit(repo, c)))
    return False


def _require_clean(repo: Path) -> None:
    data = _status_data(repo)
    if any(v for k, v in data.items() if k != "untracked"):
        raise ByteVCSError("You have uncommitted changes. Commit them before pulling.")


def _apply_snapshot(repo: Path, old: dict[str, str], new: dict[str, str]) -> None:
    targets = {rel: _validate_snapshot_path(repo, rel) for rel in new}
    for rel, target in targets.items():
        if rel not in old and target.exists():
            raise ByteVCSError(f"Pull would overwrite an untracked file: {rel}")
    for rel in set(old) - set(new):
        target = _validate_snapshot_path(repo, rel)
        if target.is_file():
            target.unlink()
    for rel, h in new.items():
        target = targets[rel]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(read_object(str(repo), h))
    save_index(repo, new)


def _fetch(remote: _Remote, repo: Path, want: list[str], have: list[str]) -> int:
    objects = remote.pack(want, have)
    for h, encoded in objects.items():
        try:
            data = base64.b64decode(encoded, validate=True)
        except ValueError as exc:
            raise ByteVCSError("The server sent a corrupt object.") from exc
        if hashlib.sha256(data).hexdigest() != h:
            raise ByteVCSError("The server sent an object that does not match its hash.")
        store_object(str(repo), data)
    return len(objects)


def _remote_url(repo: Path, name: str) -> str:
    remotes = list_remotes(repo)
    if name not in remotes:
        raise ByteVCSError(f"Remote does not exist: {name}")
    return remotes[name].rstrip("/")


def _default_name(repo: Path, override: str | None) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", override or repo.name).strip("-._")
    if not cleaned or len(cleaned) > 100:
        raise ByteVCSError("Use --name to choose a repository name (letters, digits, '.', '_' or '-').")
    return cleaned


def _auto_remote(repo: Path, name: str | None) -> str:
    creds = load_credentials()
    if not creds["token"]:
        raise ByteVCSError("No remote is configured and you are not logged in. Run: bytevcs login")
    info = whoami()
    url = f"{creds['server']}/{info['username']}/{_default_name(repo, name)}"
    add_remote(repo, "origin", url)
    return url


def _create_remote_repo(remote: _Remote, public: bool) -> None:
    if not remote.token:
        raise RemoteError("Repository not found. Log in with: bytevcs login (to create it).", 404)
    info = whoami(remote.base, remote.token)
    if (info.get("username") or "").lower() != remote.owner.lower():
        raise ByteVCSError(
            "Repository not found, and you can only create repositories under "
            f"your own username ('{info.get('username')}')."
        )
    try:
        response = httpx.post(
            f"{remote.base}/api/repos",
            json={"name": remote.name, "is_private": not public},
            headers=remote.headers(),
            timeout=30,
        )
    except httpx.HTTPError as exc:
        raise RemoteError(f"Could not reach {remote.base}: {exc}") from exc
    if response.status_code not in (200, 201):
        try:
            detail = str(response.json().get("detail", ""))
        except ValueError:
            detail = ""
        raise RemoteError(detail or f"Could not create the repository ({response.status_code}).", response.status_code)


# ---------- commands ----------

def push(repo_path: str | Path, remote: str = "origin", *, public: bool = False, name: str | None = None) -> str:
    repo = find_repository(repo_path)
    tip = _current_commit_hash(repo)
    if not tip:
        raise ByteVCSError("Nothing to push: make a commit first.")
    branch = _branch_name(repo)

    remotes = list_remotes(repo)
    if remote in remotes:
        url = remotes[remote].rstrip("/")
    elif remote == "origin":
        url = _auto_remote(repo, name)
    else:
        raise ByteVCSError(f"Remote does not exist: {remote}")

    server = _Remote(url)
    prefix = ""
    try:
        info = server.refs()
    except RemoteError as exc:
        if exc.status != 404:
            raise
        _create_remote_repo(server, public)
        prefix = f"Created {'public' if public else 'private'} repository {server.owner}/{server.name}\n"
        info = {"refs": {}, "head": None}

    old = info["refs"].get(branch)
    if old == tip:
        return prefix + "Everything up to date."

    hashes = sorted(_local_reachable(repo, [tip]))
    if len(hashes) > MAX_HASHES:
        raise ByteVCSError("This repository is too large for this version of ByteVCS.")
    needed = server.missing(hashes)
    if len(needed) > MAX_PUSH_OBJECTS:
        raise ByteVCSError(f"Too many new objects for one push ({len(needed)}; limit {MAX_PUSH_OBJECTS}).")

    objects: dict[str, str] = {}
    total = 0
    for h in needed:
        data = read_object(str(repo), h)
        total += len(data)
        if total > MAX_PUSH_BYTES:
            raise ByteVCSError("Push is larger than the 25 MB limit.")
        objects[h] = base64.b64encode(data).decode("ascii")

    result = server.push({
        "updates": [{"branch": branch, "old": old, "new": tip}],
        "head": branch,
        "objects": objects,
    })
    return prefix + result.get("message", "Push completed.") + f"\n{server.url}"


def pull(repo_path: str | Path, remote: str = "origin") -> str:
    repo = find_repository(repo_path)
    server = _Remote(_remote_url(repo, remote))
    info = server.refs()
    branch = _branch_name(repo)
    remote_tip = info["refs"].get(branch)
    if remote_tip is None:
        raise ByteVCSError(f"The remote has no branch '{branch}'.")
    local_tip = _current_commit_hash(repo)
    if local_tip == remote_tip:
        return "Already up to date."

    _fetch(server, repo, [remote_tip], [local_tip] if local_tip else [])

    if local_tip and _is_ancestor(repo, remote_tip, local_tip):
        return "Already up to date (your branch is ahead of the server; use push)."
    if local_tip and not _is_ancestor(repo, local_tip, remote_tip):
        track = f"{remote}/{branch}"
        _write_ref(repo, track, remote_tip)
        return (
            "Your history and the server's have diverged. The server's version "
            f"was saved as branch '{track}'. Run: bytevcs merge {track}"
        )

    _require_clean(repo)
    old = _read_snapshot(repo, local_tip) if local_tip else {}
    _apply_snapshot(repo, old, _read_snapshot(repo, remote_tip))
    _write_ref(repo, branch, remote_tip)
    return f"Updated {branch} to {remote_tip[:8]}."


def clone(url: str, destination: str | Path) -> Path:
    from bytevcs.repo_init import create_repository

    server = _Remote(url)
    dest = Path(destination).resolve()
    if dest.exists() and any(dest.iterdir()):
        raise ByteVCSError(f"Destination is not empty: {dest}")
    info = server.refs()
    refs = info["refs"]
    if not refs:
        raise ByteVCSError("The remote repository is empty.")
    head = info.get("head")
    if head not in refs:
        head = sorted(refs)[0]

    created_dir = not dest.exists()
    try:
        dest.mkdir(parents=True, exist_ok=True)
        create_repository(dest)
        _fetch(server, dest, sorted(set(refs.values())), [])
        for branch, commit in refs.items():
            _write_ref(dest, branch, commit)
        (_metadata(dest) / "HEAD").write_text(f"ref: refs/heads/{head}\n", encoding="utf-8")
        _apply_snapshot(dest, {}, _read_snapshot(dest, refs[head]))
        add_remote(dest, "origin", server.url)
    except BaseException:
        for child in list(dest.iterdir()):
            shutil.rmtree(child) if child.is_dir() else child.unlink()
        if created_dir:
            dest.rmdir()
        raise
    return dest
