"""Push/pull/clone protocol (v2): incremental, verified, owner-checked."""

from __future__ import annotations

import base64
import binascii
import hashlib
import os
import re
import json
import threading
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from api.auth import get_current_user, get_db, get_optional_user
from api.models import Repository, User
from api.storage import StorageError, ensure_local, lock_for, persist
from bytevcs.repository import (
    ByteVCSError,
    _valid_branch_name,
    _validate_snapshot_path,
    save_index,
)

router = APIRouter(prefix="/api/r")

ROOT = Path(__file__).resolve().parent.parent
DATA_ROOT = Path(os.environ.get("BYTEVCS_DATA_ROOT", str(ROOT / "repo-data"))).resolve()

HASH_RE = re.compile(r"[0-9a-f]{64}")
MAX_OBJECT_BYTES = 5 * 1024 * 1024
MAX_PUSH_BYTES = 25 * 1024 * 1024
MAX_PACK_BYTES = 50 * 1024 * 1024
MAX_PUSH_OBJECTS = 5000
MAX_HASHES = 100_000
MAX_COMMITS_WALK = 100_000

# One writer per repository (single-process; replaced by a DB lock when scaling out).
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _lock_for(key: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(key, threading.Lock())


# ---------- storage helpers ----------

def _root(repo: Repository) -> Path:
    try:
        ensure_local(repo)
    except StorageError:
        raise HTTPException(503, "Storage is temporarily unavailable. Please try again.")
    path = (DATA_ROOT / repo.storage_key).resolve()
    if not path.is_relative_to(DATA_ROOT) or not (path / ".bytevcs").is_dir():
        raise HTTPException(500, "Repository data is missing on the server.")
    return path


def _object_path(root: Path, h: str) -> Path:
    return root / ".bytevcs" / "objects" / h[:2] / h[2:]


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp-" + uuid.uuid4().hex[:8])
    tmp.write_bytes(data)
    os.replace(tmp, path)


class Store:
    """Objects on disk plus an in-memory quarantine of uploaded, unverified ones."""

    def __init__(self, root: Path, extra: dict[str, bytes] | None = None):
        self.root = root
        self.extra = extra or {}

    def has(self, h: str) -> bool:
        return bool(HASH_RE.fullmatch(h)) and (
            h in self.extra or _object_path(self.root, h).is_file()
        )

    def read(self, h: str) -> bytes:
        if h in self.extra:
            return self.extra[h]
        data = _object_path(self.root, h).read_bytes()
        if hashlib.sha256(data).hexdigest() != h:
            raise OSError("object failed its integrity check")
        return data


def _load_commit(store: Store, h: str) -> dict:
    if not HASH_RE.fullmatch(h):
        raise HTTPException(400, "Invalid commit hash.")
    try:
        data = json.loads(store.read(h).decode("utf-8"))
    except (OSError, ValueError):
        raise HTTPException(400, f"Commit {h[:8]} is missing or invalid.")
    if not isinstance(data, dict) or not isinstance(data.get("snapshot"), str):
        raise HTTPException(400, f"Commit {h[:8]} is invalid.")
    if not HASH_RE.fullmatch(data["snapshot"]):
        raise HTTPException(400, f"Commit {h[:8]} is invalid.")
    for key in ("parent", "merge_parent"):
        v = data.get(key)
        if v is not None and not (isinstance(v, str) and HASH_RE.fullmatch(v)):
            raise HTTPException(400, f"Commit {h[:8]} is invalid.")
    return data


def _load_snapshot(store: Store, h: str) -> dict[str, str]:
    try:
        snap = json.loads(store.read(h).decode("utf-8"))
    except (OSError, ValueError):
        raise HTTPException(400, "A snapshot is missing or invalid.")
    if not isinstance(snap, dict) or not all(
        isinstance(k, str) and isinstance(v, str) and HASH_RE.fullmatch(v)
        for k, v in snap.items()
    ):
        raise HTTPException(400, "A snapshot is invalid.")
    for rel in snap:
        try:
            _validate_snapshot_path(store.root, rel)
        except (ByteVCSError, ValueError, OSError):
            raise HTTPException(400, f"Unsafe file path in snapshot: {rel[:80]!r}")
    return snap


def _parents(commit: dict) -> list[str]:
    return [commit[k] for k in ("parent", "merge_parent") if commit.get(k)]


def _reachable(store: Store, tips: list[str]) -> set[str]:
    """Every object (commits, snapshots, blobs) reachable from the tip commits."""
    seen: set[str] = set()
    objects: set[str] = set()
    stack = list(tips)
    while stack:
        c = stack.pop()
        if c in seen:
            continue
        if len(seen) >= MAX_COMMITS_WALK:
            raise HTTPException(413, "Repository history is too large.")
        seen.add(c)
        commit = _load_commit(store, c)
        objects.add(c)
        objects.add(commit["snapshot"])
        objects.update(_load_snapshot(store, commit["snapshot"]).values())
        stack.extend(_parents(commit))
    return objects


def _verify_new_history(store: Store, tip: str) -> None:
    """Every uploaded commit must be complete: snapshot, blobs and parents present."""
    stack, seen = [tip], set()
    while stack:
        c = stack.pop()
        if c in seen:
            continue
        seen.add(c)
        if not store.has(c):
            raise HTTPException(400, f"Commit {c[:8]} is missing from the push.")
        if c not in store.extra:
            continue  # stored by an earlier, fully verified push
        commit = _load_commit(store, c)
        snap = _load_snapshot(store, commit["snapshot"])
        for blob in snap.values():
            if not store.has(blob):
                raise HTTPException(400, "The push is missing file contents.")
        stack.extend(_parents(commit))


def _is_ancestor(store: Store, ancestor: str, descendant: str) -> bool:
    stack, seen = [descendant], set()
    while stack:
        c = stack.pop()
        if c == ancestor:
            return True
        if c in seen:
            continue
        if len(seen) >= MAX_COMMITS_WALK:
            raise HTTPException(413, "Repository history is too large.")
        seen.add(c)
        stack.extend(_parents(_load_commit(store, c)))
    return False


def _read_refs(root: Path) -> dict[str, str]:
    heads = root / ".bytevcs" / "refs" / "heads"
    refs: dict[str, str] = {}
    if heads.is_dir():
        for p in sorted(heads.rglob("*")):
            if p.is_file():
                text = p.read_text(encoding="utf-8").strip()
                if HASH_RE.fullmatch(text):
                    refs[p.relative_to(heads).as_posix()] = text
    return refs


def _head_branch(root: Path) -> str:
    text = (root / ".bytevcs" / "HEAD").read_text(encoding="utf-8").strip()
    prefix = "ref: refs/heads/"
    return text[len(prefix):] if text.startswith(prefix) else "main"


def _mirror_worktree(store: Store, root: Path, old_tip: str | None, new_tip: str) -> None:
    """Keep the server's working copy equal to the pushed branch (for the dashboard)."""
    old = _load_snapshot(store, _load_commit(store, old_tip)["snapshot"]) if old_tip else {}
    new = _load_snapshot(store, _load_commit(store, new_tip)["snapshot"])
    for rel in set(old) - set(new):
        target = _validate_snapshot_path(root, rel)
        if target.is_file():
            target.unlink()
    for rel, h in new.items():
        target = _validate_snapshot_path(root, rel)
        _atomic_write(target, store.read(h))
    save_index(root, new)


# ---------- access control ----------

def _find_repo(db: Session, owner: str, name: str) -> Repository | None:
    return db.scalar(
        select(Repository)
        .join(User, Repository.owner_id == User.id)
        .where(User.username == owner.lower(), Repository.name == name)
    )


def get_read_repo(
    owner: str,
    name: str,
    viewer: User | None = Depends(get_optional_user),
    db: Session = Depends(get_db),
) -> Repository:
    repo = _find_repo(db, owner, name)
    if repo is None or (
        repo.is_private and (viewer is None or viewer.id != repo.owner_id)
    ):
        raise HTTPException(404, "Repository not found.")
    return repo


def get_write_repo(
    owner: str,
    name: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Repository:
    repo = _find_repo(db, owner, name)
    if repo is None or (repo.is_private and repo.owner_id != user.id):
        raise HTTPException(404, "Repository not found.")
    if repo.owner_id != user.id:
        raise HTTPException(403, "Only the owner can push to this repository.")
    return repo


# ---------- routes ----------

@router.get("/{owner}/{name}/refs")
def refs(repo: Repository = Depends(get_read_repo)):
    root = _root(repo)
    return {"refs": _read_refs(root), "head": _head_branch(root)}


class MissingRequest(BaseModel):
    hashes: list[str] = Field(max_length=MAX_HASHES)


@router.post("/{owner}/{name}/missing")
def missing(request: MissingRequest, repo: Repository = Depends(get_write_repo)):
    store = Store(_root(repo))
    return {"missing": [h for h in request.hashes if not store.has(h)]}


class PackRequest(BaseModel):
    want: list[str] = Field(min_length=1, max_length=50)
    have: list[str] = Field(default_factory=list, max_length=1000)


@router.post("/{owner}/{name}/pack")
def pack(request: PackRequest, repo: Repository = Depends(get_read_repo)):
    store = Store(_root(repo))
    for h in request.want:
        if not store.has(h):
            raise HTTPException(404, "Requested commit not found.")
    have = [h for h in request.have if store.has(h)]
    wanted = _reachable(store, request.want)
    if have:
        wanted -= _reachable(store, have)
    objects: dict[str, str] = {}
    total = 0
    for h in sorted(wanted):
        data = store.read(h)
        total += len(data)
        if total > MAX_PACK_BYTES:
            raise HTTPException(413, "Repository is too large to fetch in one request.")
        objects[h] = base64.b64encode(data).decode("ascii")
    return {"objects": objects}


class RefUpdate(BaseModel):
    branch: str = Field(min_length=1, max_length=200)
    old: str | None = None
    new: str


class PushRequest(BaseModel):
    updates: list[RefUpdate] = Field(min_length=1, max_length=50)
    head: str | None = None
    objects: dict[str, str] = Field(default_factory=dict)


@router.post("/{owner}/{name}/push")
def push(request: PushRequest, repo: Repository = Depends(get_write_repo)):
    root = _root(repo)
    if len(request.objects) > MAX_PUSH_OBJECTS:
        raise HTTPException(413, "Too many new objects in one push.")

    extra: dict[str, bytes] = {}
    total = 0
    for h, encoded in request.objects.items():
        if not HASH_RE.fullmatch(h):
            raise HTTPException(400, "Invalid object hash.")
        try:
            data = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError):
            raise HTTPException(400, "Invalid object encoding.")
        if len(data) > MAX_OBJECT_BYTES:
            raise HTTPException(413, "A file is larger than the 5 MB limit.")
        total += len(data)
        if total > MAX_PUSH_BYTES:
            raise HTTPException(413, "Push is larger than the 25 MB limit.")
        if hashlib.sha256(data).hexdigest() != h:
            raise HTTPException(400, "An object does not match its hash.")
        extra[h] = data

    with _lock_for(repo.storage_key):
        store = Store(root, extra)
        refs_now = _read_refs(root)
        head_branch = _head_branch(root)

        plan: list[tuple[str, str | None, str]] = []
        for u in request.updates:
            if not _valid_branch_name(u.branch):
                raise HTTPException(400, "Invalid branch name.")
            if not HASH_RE.fullmatch(u.new) or (u.old is not None and not HASH_RE.fullmatch(u.old)):
                raise HTTPException(400, "Invalid commit hash.")
            current = refs_now.get(u.branch)
            if u.old != current:
                raise HTTPException(409, "The server has changed since you last synced. Run: bytevcs pull")
            _verify_new_history(store, u.new)
            if current and current != u.new and not _is_ancestor(store, current, u.new):
                raise HTTPException(
                    409,
                    "Your history has diverged from the server. Run: bytevcs pull, merge, then push again.",
                )
            plan.append((u.branch, current, u.new))

        try:
            for h, data in extra.items():
                path = _object_path(root, h)
                if not path.exists():
                    _atomic_write(path, data)
            if not refs_now and request.head and _valid_branch_name(request.head):
                head_branch = request.head
                _atomic_write(
                    root / ".bytevcs" / "HEAD",
                    f"ref: refs/heads/{head_branch}\n".encode("utf-8"),
                )
            for branch, old, new in plan:
                if branch == head_branch and old != new:
                    _mirror_worktree(store, root, old, new)
            for branch, old, new in plan:
                _atomic_write(
                    root / ".bytevcs" / "refs" / "heads" / branch,
                    (new + "\n").encode("utf-8"),
                )
            persist(root)
        except (OSError, ByteVCSError, ValueError):
            raise HTTPException(500, "Could not store the push.")

    return {
        "message": f"Updated {len(plan)} branch(es) with {len(extra)} new object(s).",
        "updated": [b for b, _, _ in plan],
    }

_lock_for = lock_for  # one lock registry shared with storage restores
