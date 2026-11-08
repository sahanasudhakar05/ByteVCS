"""ByteVCS hosted API: authenticated, owner-isolated repositories."""
import secrets
import json
import os
import re
import shutil
import uuid
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from api.auth import (
    PAT_PREFIX,
    get_current_user,
    get_db,
    get_optional_user,
    get_session_user,
    hash_token,
)
from api.models import ApiToken, Repository, User
from bytevcs.repo_init import create_repository
from api.storage import StorageError, ensure_local, persist
from bytevcs.object_store import read_object
from bytevcs.repository import (
    ByteVCSError,
    _current_commit_hash,
    _metadata,
    _read_commit,
    _read_snapshot,
    _status_data,
    add_file,
    checkout_branch,
    create_branch,
    create_commit,
    find_repository,
    get_current_branch,
    load_index,
    merge_branch,
    remove_file,
    save_index,
    show_diff,
)

ROOT = Path(__file__).resolve().parent.parent
# Local disk for now. Phase 3 moves this to Supabase Storage.
DATA_ROOT = Path(os.environ.get("BYTEVCS_DATA_ROOT", str(ROOT / "repo-data"))).resolve()
REMOTE_ROOT = Path(
    os.environ.get("BYTEVCS_REMOTE_ROOT", str(ROOT / "remote-repositories"))
).resolve()
NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}")
MAX_FILE_BYTES = 1_000_000
MAX_BUNDLE_BYTES = 25 * 1024 * 1024

app = FastAPI(title="ByteVCS API", version="0.2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT","DELETE"],
    allow_headers=["Content-Type", "Authorization"],
)


# ---------- request models ----------

class CreateRepoRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    is_private: bool = True
    description: str = Field(default="", max_length=300)


class FileRequest(BaseModel):
    path: str = Field(min_length=1, max_length=500)


class WriteFileRequest(BaseModel):
    path: str = Field(min_length=1, max_length=500)
    content: str = Field(max_length=MAX_FILE_BYTES)


class CommitRequest(BaseModel):
    message: str = Field(min_length=1, max_length=200)


class BranchRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)


# ---------- ownership + path helpers ----------

def get_owned_repo(
    repo_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Repository:
    """The only way routes reach a repository: it must belong to the caller.

    Missing and not-yours give the same 404, so IDs can't be probed.
    """
    repo = db.scalar(
        select(Repository).where(
            Repository.id == repo_id, Repository.owner_id == user.id
        )
    )
    if repo is None:
        raise HTTPException(404, "Repository not found.")
    return repo


def repo_dir(repo: Repository) -> Path:
    path = (DATA_ROOT / repo.storage_key).resolve()
    if not path.is_relative_to(DATA_ROOT):
        raise HTTPException(500, "Invalid repository location.")
    return path


def repo_path(repo: Repository) -> Path:
    try:
        ensure_local(repo)
    except StorageError:
        raise HTTPException(503, "Storage is temporarily unavailable. Please try again.")
    path = repo_dir(repo)
    try:
        found = find_repository(path)
    except (ByteVCSError, OSError) as exc:
        raise HTTPException(500, "Repository data is missing on the server.") from exc
    if found != path:
        raise HTTPException(500, "Repository data is missing on the server.")
    return found


def safe_file(root: Path, relative: str) -> Path:
    if not relative or "\x00" in relative:
        raise HTTPException(400, "Invalid file path.")
    candidate = (root / relative).resolve()
    if (
        candidate == root
        or not candidate.is_relative_to(root)
        or ".bytevcs" in candidate.relative_to(root).parts
    ):
        raise HTTPException(400, "Invalid file path.")
    return candidate


def clean(message: str) -> str:
    """Never leak server filesystem paths in error messages."""
    return message.replace(str(DATA_ROOT), "<data>")


def capture(action, *args, **kwargs) -> str:
    output = StringIO()
    try:
        with redirect_stdout(output):
            action(*args, **kwargs)
    except (ByteVCSError, OSError, ValueError, KeyError) as exc:
        raise HTTPException(400, clean(str(exc))) from exc
    if args and isinstance(args[0], Path):
        try:
            persist(args[0])
        except StorageError:
            raise HTTPException(503, "Storage is temporarily unavailable. Please try again.")
    return output.getvalue().strip()


def current_branch_name(root: Path) -> str:
    return get_current_branch(root).relative_to(
        _metadata(root) / "refs" / "heads"
    ).as_posix()


def branch_names(root: Path) -> list[str]:
    refs = _metadata(root) / "refs" / "heads"
    return sorted(
        p.relative_to(refs).as_posix() for p in refs.rglob("*") if p.is_file()
    )


def repo_json(repo: Repository) -> dict:
    return {
        "id": str(repo.id),
        "owner": repo.owner.username,
        "name": repo.name,
        "description": repo.description,
        "is_private": repo.is_private,
        "created_at": repo.created_at.isoformat(),
    }


# ---------- account + repository management ----------

@app.get("/api/health")
def health():
    return {"ok": True, "service": "ByteVCS API"}


@app.get("/api/me")
def me(user: User = Depends(get_current_user)):
    return {"id": str(user.id), "email": user.email, "username": user.username}


@app.get("/api/repos")
def list_repos(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.scalars(
        select(Repository)
        .where(Repository.owner_id == user.id)
        .order_by(Repository.created_at.desc())
    ).all()
    return {"repos": [repo_json(r) for r in rows]}


@app.post("/api/repos", status_code=201)
def create_repo(
    request: CreateRepoRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if user.username is None:
        raise HTTPException(409, "Choose a username first.")
    name = request.name.strip()
    if not NAME_RE.fullmatch(name):
        raise HTTPException(
            400, "Name must be 1-100 characters: letters, digits, '.', '_' or '-'."
        )
    repo = Repository(
        owner_id=user.id,
        name=name,
        is_private=request.is_private,
        description=request.description.strip(),
    )
    db.add(repo)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "You already have a repository with that name.")

    path = repo_dir(repo)
    try:
        DATA_ROOT.mkdir(parents=True, exist_ok=True)
        create_repository(path)
        persist(path)
        db.commit()
    except Exception as exc:
        db.rollback()
        shutil.rmtree(path, ignore_errors=True)
        raise HTTPException(500, "Could not create repository.") from exc
    return repo_json(repo)


# ---------- per-repository routes (all owner-checked) ----------

@app.get("/api/repos/{repo_id}")
def repository_info(repo: Repository = Depends(get_owned_repo)):
    root = repo_path(repo)
    status = _status_data(root)
    return {
        **repo_json(repo),
        "branch": current_branch_name(root),
        "head": _current_commit_hash(root),
        "clean": not any(status.values()),
        "status": status,
    }


@app.get("/api/repos/{repo_id}/status")
def status(repo: Repository = Depends(get_owned_repo)):
    root = repo_path(repo)
    return {"branch": current_branch_name(root), "changes": _status_data(root)}


@app.get("/api/repos/{repo_id}/branches")
def branches(repo: Repository = Depends(get_owned_repo)):
    root = repo_path(repo)
    current = get_current_branch(root).resolve()
    refs = _metadata(root) / "refs" / "heads"
    return {
        "current": current_branch_name(root),
        "branches": [
            {"name": n, "current": (refs / n).resolve() == current}
            for n in branch_names(root)
        ],
    }


@app.get("/api/repos/{repo_id}/commits")
def commits(limit: int = 50, repo: Repository = Depends(get_owned_repo)):
    root = repo_path(repo)
    if not 1 <= limit <= 200:
        raise HTTPException(400, "Limit must be between 1 and 200.")
    current = _current_commit_hash(root)
    result, seen = [], set()
    while current and current not in seen and len(result) < limit:
        seen.add(current)
        try:
            commit = _read_commit(root, current)
        except ByteVCSError as exc:
            raise HTTPException(400, clean(str(exc))) from exc
        result.append({
            "hash": current,
            "short_hash": current[:8],
            "message": commit.get("message", ""),
            "timestamp": commit.get("timestamp"),
            "parent": commit.get("parent"),
            "merge_parent": commit.get("merge_parent"),
        })
        current = commit.get("parent")
    return {"commits": result}


@app.get("/api/repos/{repo_id}/files")
def tracked_files(repo: Repository = Depends(get_owned_repo)):
    root = repo_path(repo)
    snapshot = _read_snapshot(root, _current_commit_hash(root))
    index = load_index(root)
    data = _status_data(root)
    changed = {
        p
        for key in ("staged_added", "staged_modified", "staged_deleted",
                    "unstaged_modified", "unstaged_deleted", "untracked")
        for p in data[key]
    }
    paths = sorted(set(snapshot) | set(index) | changed)

    def state(p: str) -> str:
        for key, label in (
            ("untracked", "untracked"), ("staged_added", "staged_added"),
            ("staged_modified", "staged_modified"), ("staged_deleted", "staged_deleted"),
            ("unstaged_modified", "modified"), ("unstaged_deleted", "deleted"),
        ):
            if p in data[key]:
                return label
        return "clean"

    return {
        "files": [
            {"path": p, "tracked": p in snapshot or p in index,
             "staged": p in index, "status": state(p)}
            for p in paths
        ]
    }


@app.get("/api/repos/{repo_id}/diff")
def diff(staged: bool = False, path: str | None = None,
         repo: Repository = Depends(get_owned_repo)):
    root = repo_path(repo)
    output = StringIO()
    try:
        with redirect_stdout(output):
            show_diff(root, staged=staged, file_path=path)
    except (ByteVCSError, OSError, ValueError) as exc:
        raise HTTPException(400, clean(str(exc))) from exc
    return {"diff": output.getvalue(), "staged": staged, "path": path}


@app.get("/api/repos/{repo_id}/file")
def read_file(path: str, repo: Repository = Depends(get_owned_repo)):
    root = repo_path(repo)
    candidate = safe_file(root, path)
    if not candidate.is_file():
        raise HTTPException(404, "File not found.")
    try:
        return {"path": path, "content": candidate.read_text(encoding="utf-8")}
    except UnicodeDecodeError:
        raise HTTPException(415, "This file is not UTF-8 text.")


@app.put("/api/repos/{repo_id}/file")
def write_file(request: WriteFileRequest, repo: Repository = Depends(get_owned_repo)):
    root = repo_path(repo)
    data = request.content.encode("utf-8")
    if len(data) > MAX_FILE_BYTES:
        raise HTTPException(413, "File exceeds the 1 MB limit.")
    candidate = safe_file(root, request.path)
    if candidate.is_dir():
        raise HTTPException(400, "Path is a directory.")
    try:
        candidate.parent.mkdir(parents=True, exist_ok=True)
        candidate.write_bytes(data)
    except OSError as exc:
        raise HTTPException(400, "Could not write file.") from exc
    return {"message": f"Saved {request.path}", "bytes": len(data)}


@app.post("/api/repos/{repo_id}/stage")
def stage_file(request: FileRequest, repo: Repository = Depends(get_owned_repo)):
    return {"message": capture(add_file, repo_path(repo), request.path)}


@app.post("/api/repos/{repo_id}/unstage")
def unstage_file(request: FileRequest, repo: Repository = Depends(get_owned_repo)):
    root = repo_path(repo)
    index = load_index(root)
    if request.path not in index:
        raise HTTPException(400, "File is not staged.")
    index.pop(request.path)
    save_index(root, index)
    return {"message": f"Unstaged {request.path}"}


@app.post("/api/repos/{repo_id}/remove")
def remove_tracked_file(request: FileRequest, repo: Repository = Depends(get_owned_repo)):
    return {"message": capture(remove_file, repo_path(repo), request.path)}


@app.post("/api/repos/{repo_id}/commits")
def make_commit(request: CommitRequest, repo: Repository = Depends(get_owned_repo)):
    message = request.message.strip()
    if not message:
        raise HTTPException(400, "Commit message cannot be empty.")
    return {"message": capture(create_commit, repo_path(repo), message)}


@app.post("/api/repos/{repo_id}/branches")
def make_branch(request: BranchRequest, repo: Repository = Depends(get_owned_repo)):
    return {"message": capture(create_branch, repo_path(repo), request.name)}


@app.post("/api/repos/{repo_id}/checkout")
def checkout(request: BranchRequest, repo: Repository = Depends(get_owned_repo)):
    return {"message": capture(checkout_branch, repo_path(repo), request.name)}


@app.post("/api/repos/{repo_id}/merge")
def merge(request: BranchRequest, repo: Repository = Depends(get_owned_repo)):
    return {"message": capture(merge_branch, repo_path(repo), request.name)}


# ---------- remote push/pull transport (owner-checked) ----------

def bundle_file(repo: Repository) -> Path:
    return REMOTE_ROOT / f"{repo.storage_key}.json"


@app.get("/api/remote/repos/{repo_id}")
def download_remote(repo: Repository = Depends(get_owned_repo)):
    path = bundle_file(repo)
    if not path.is_file():
        raise HTTPException(404, "Nothing pushed to this repository yet.")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise HTTPException(500, "Remote repository data is invalid.") from exc


@app.put("/api/remote/repos/{repo_id}")
def upload_remote(bundle: dict[str, Any], repo: Repository = Depends(get_owned_repo)):
    if (
        bundle.get("format") != 1
        or not isinstance(bundle.get("metadata"), dict)
        or not isinstance(bundle.get("worktree"), dict)
    ):
        raise HTTPException(400, "Invalid ByteVCS bundle.")
    encoded = json.dumps(bundle)
    if len(encoded) > MAX_BUNDLE_BYTES:
        raise HTTPException(413, "Repository bundle exceeds the 25 MB limit.")
    path = bundle_file(repo)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(encoded, encoding="utf-8")
    return {"message": f"Pushed '{repo.name}' successfully.", "bytes": len(encoded)}

# ---------- usernames, visibility, public read access ----------

USERNAME_RE = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,37}[a-z0-9])?")
RESERVED_USERNAMES = {
    "api", "admin", "login", "logout", "signup", "settings", "new",
    "explore", "help", "support", "about", "u", "static", "assets",
}


class UsernameRequest(BaseModel):
    username: str = Field(min_length=3, max_length=39)


class UpdateRepoRequest(BaseModel):
    is_private: bool | None = None
    description: str | None = Field(default=None, max_length=300)


@app.put("/api/me/username")
def set_username(
    request: UsernameRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if user.username:
        raise HTTPException(409, "Username is already set.")
    name = request.username.strip().lower()
    if not USERNAME_RE.fullmatch(name) or name in RESERVED_USERNAMES:
        raise HTTPException(
            400, "Use 3-39 lowercase letters, digits or hyphens (not at the ends)."
        )
    user.username = name
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "That username is taken.")
    return {"username": user.username}


@app.patch("/api/repos/{repo_id}")
def update_repo(
    request: UpdateRepoRequest,
    repo: Repository = Depends(get_owned_repo),
    db: Session = Depends(get_db),
):
    if request.is_private is not None:
        repo.is_private = request.is_private
    if request.description is not None:
        repo.description = request.description.strip()
    db.commit()
    return repo_json(repo)


def get_readable_repo(
    owner: str,
    name: str,
    viewer: User | None = Depends(get_optional_user),
    db: Session = Depends(get_db),
) -> Repository:
    """Public repos: anyone. Private repos: the owner only. Else 404."""
    repo = db.scalar(
        select(Repository)
        .join(User, Repository.owner_id == User.id)
        .where(User.username == owner.lower(), Repository.name == name)
    )
    if repo is None or (
        repo.is_private and (viewer is None or viewer.id != repo.owner_id)
    ):
        raise HTTPException(404, "Repository not found.")
    return repo


@app.get("/api/u/{owner}")
def user_public_repos(owner: str, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.username == owner.lower()))
    if user is None:
        raise HTTPException(404, "User not found.")
    rows = db.scalars(
        select(Repository)
        .where(Repository.owner_id == user.id, Repository.is_private.is_(False))
        .order_by(Repository.created_at.desc())
    ).all()
    return {"owner": user.username, "repos": [repo_json(r) for r in rows]}


@app.get("/api/u/{owner}/{name}")
def public_repo_info(repo: Repository = Depends(get_readable_repo)):
    root = repo_path(repo)
    return {
        **repo_json(repo),
        "branch": current_branch_name(root),
        "head": _current_commit_hash(root),
    }


@app.get("/api/u/{owner}/{name}/commits")
def public_commits(limit: int = 50, repo: Repository = Depends(get_readable_repo)):
    return commits(limit=limit, repo=repo)


@app.get("/api/u/{owner}/{name}/branches")
def public_branches(repo: Repository = Depends(get_readable_repo)):
    return branches(repo=repo)


@app.get("/api/u/{owner}/{name}/files")
def public_files(repo: Repository = Depends(get_readable_repo)):
    root = repo_path(repo)
    snapshot = _read_snapshot(root, _current_commit_hash(root))
    return {"files": [{"path": p} for p in sorted(snapshot)]}


@app.get("/api/u/{owner}/{name}/file")
def public_file(path: str, repo: Repository = Depends(get_readable_repo)):
    root = repo_path(repo)
    snapshot = _read_snapshot(root, _current_commit_hash(root))
    if path not in snapshot:
        raise HTTPException(404, "File not found.")
    try:
        content = read_object(str(root), snapshot[path]).decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(415, "This file is not UTF-8 text.")
    except (ByteVCSError, OSError, ValueError):
        raise HTTPException(404, "File not found.")
    return {"path": path, "content": content}

# ---------- personal access tokens (website login required) ----------

MAX_TOKENS_PER_USER = 20


class TokenRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)


@app.post("/api/tokens", status_code=201)
def create_token(
    request: TokenRequest,
    user: User = Depends(get_session_user),
    db: Session = Depends(get_db),
):
    count = db.scalar(
        select(func.count()).select_from(ApiToken).where(ApiToken.user_id == user.id)
    )
    if count >= MAX_TOKENS_PER_USER:
        raise HTTPException(400, "Token limit reached. Revoke an old token first.")
    raw = PAT_PREFIX + secrets.token_urlsafe(32)
    row = ApiToken(
        user_id=user.id,
        name=request.name.strip(),
        token_hash=hash_token(raw),
        prefix=raw[:10],
    )
    db.add(row)
    db.commit()
    return {
        "id": str(row.id),
        "name": row.name,
        "token": raw,
        "note": "Copy this token now. It will not be shown again.",
    }


@app.get("/api/tokens")
def list_tokens(
    user: User = Depends(get_session_user), db: Session = Depends(get_db)
):
    rows = db.scalars(
        select(ApiToken)
        .where(ApiToken.user_id == user.id)
        .order_by(ApiToken.created_at.desc())
    ).all()
    return {
        "tokens": [
            {
                "id": str(t.id),
                "name": t.name,
                "prefix": t.prefix,
                "created_at": t.created_at.isoformat(),
                "last_used_at": t.last_used_at.isoformat() if t.last_used_at else None,
            }
            for t in rows
        ]
    }


@app.delete("/api/tokens/{token_id}")
def revoke_token(
    token_id: uuid.UUID,
    user: User = Depends(get_session_user),
    db: Session = Depends(get_db),
):
    row = db.scalar(
        select(ApiToken).where(ApiToken.id == token_id, ApiToken.user_id == user.id)
    )
    if row is None:
        raise HTTPException(404, "Token not found.")
    db.delete(row)
    db.commit()
    return {"message": "Token revoked."}
from api.sync import router as sync_router
app.include_router(sync_router)


# ---------- public search and explore ----------

from sqlalchemy import or_


def _like(term: str) -> str:
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


@app.get("/api/search")
def search_repos(
    q: str = "", limit: int = 20, offset: int = 0, db: Session = Depends(get_db)
):
    term = q.strip()[:100]
    if not term:
        raise HTTPException(400, "Enter something to search for.")
    limit = max(1, min(limit, 50))
    offset = max(0, min(offset, 1000))
    pattern = _like(term)
    rows = db.scalars(
        select(Repository)
        .join(User, Repository.owner_id == User.id)
        .where(
            Repository.is_private.is_(False),
            User.username.is_not(None),
            or_(
                Repository.name.ilike(pattern, escape="\\"),
                Repository.description.ilike(pattern, escape="\\"),
                User.username.ilike(pattern, escape="\\"),
            ),
        )
        .order_by(Repository.created_at.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    return {"query": term, "repos": [repo_json(r) for r in rows]}


@app.get("/api/explore")
def explore(limit: int = 20, db: Session = Depends(get_db)):
    limit = max(1, min(limit, 50))
    rows = db.scalars(
        select(Repository)
        .join(User, Repository.owner_id == User.id)
        .where(Repository.is_private.is_(False), User.username.is_not(None))
        .order_by(Repository.created_at.desc())
        .limit(limit)
    ).all()
    return {"repos": [repo_json(r) for r in rows]}
