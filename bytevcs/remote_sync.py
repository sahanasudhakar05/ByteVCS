"""HTTP push, pull and clone for ByteVCS (educational single-server protocol)."""
from __future__ import annotations
import base64, hashlib, json, os, shutil
from pathlib import Path, PurePosixPath
import httpx
from bytevcs.repository import ByteVCSError, _current_commit_hash, _read_snapshot, find_repository, get_current_branch, _metadata


def _bundle(repo: Path) -> dict:
    meta = _metadata(repo)
    files = {}
    for p in meta.rglob('*'):
        if p.is_file():
            rel = p.relative_to(meta).as_posix()
            files[rel] = base64.b64encode(p.read_bytes()).decode('ascii')
    head = _current_commit_hash(repo)
    worktree = {}
    if head:
        for rel, obj_hash in _read_snapshot(repo, head).items():
            from bytevcs.object_store import read_object
            content = read_object(str(repo), obj_hash)
            worktree[rel] = base64.b64encode(content).decode('ascii')
    return {'format': 1, 'metadata': files, 'worktree': worktree}


def _safe_write(root: Path, rel: str, data: bytes):
    posix = PurePosixPath(rel)
    if posix.is_absolute() or '..' in posix.parts or not posix.parts:
        raise ByteVCSError('Remote bundle contains an unsafe path.')
    target = (root / Path(*posix.parts)).resolve()
    if not target.is_relative_to(root.resolve()):
        raise ByteVCSError('Remote bundle contains an unsafe path.')
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)


def _restore(repo: Path, bundle: dict, *, replace: bool = True):
    if bundle.get('format') != 1 or not isinstance(bundle.get('metadata'), dict) or not isinstance(bundle.get('worktree'), dict):
        raise ByteVCSError('Unsupported or invalid remote bundle.')
    meta = _metadata(repo)
    if replace:
        shutil.rmtree(meta)
        meta.mkdir(parents=True)
    for rel, encoded in bundle['metadata'].items():
        _safe_write(meta, rel, base64.b64decode(encoded, validate=True))
    # Materialize tracked files. Existing untracked files are left untouched.
    for rel, encoded in bundle['worktree'].items():
        _safe_write(repo, rel, base64.b64decode(encoded, validate=True))


def _remote_url(repo: Path, name: str) -> str:
    from bytevcs.remotes import list_remotes
    remotes = list_remotes(repo)
    if name not in remotes:
        raise ByteVCSError(f"Remote does not exist: {name}")
    return remotes[name].rstrip('/')


def push(repo_path: str | Path, remote: str = 'origin') -> str:
    repo = find_repository(repo_path)
    url = _remote_url(repo, remote)
    bundle = _bundle(repo)
    headers = _headers()
    try:
        response = httpx.put(url, json=bundle, headers=headers, timeout=60)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise ByteVCSError(f'Push failed: {exc}') from exc
    return response.json().get('message', 'Push completed.')


def pull(repo_path: str | Path, remote: str = 'origin') -> str:
    repo = find_repository(repo_path)
    url = _remote_url(repo, remote)
    try:
        response = httpx.get(url, headers=_headers(), timeout=60)
        response.raise_for_status()
        bundle = response.json()
        _restore(repo, bundle)
    except (httpx.HTTPError, ValueError, OSError) as exc:
        raise ByteVCSError(f'Pull failed: {exc}') from exc
    return 'Pull completed. Local repository metadata and tracked files were updated.'


def clone(url: str, destination: str | Path) -> Path:
    dest = Path(destination).resolve()
    if dest.exists() and any(dest.iterdir()):
        raise ByteVCSError(f'Destination is not empty: {dest}')
    dest.mkdir(parents=True, exist_ok=True)
    try:
        response = httpx.get(url.rstrip('/'), headers=_headers(), timeout=60)
        response.raise_for_status()
        bundle = response.json()
        (dest / '.bytevcs').mkdir(exist_ok=True)
        _restore(dest, bundle)
        # A clone tracks the source as origin.
        from bytevcs.remotes import add_remote, list_remotes
        if 'origin' not in list_remotes(dest):
            add_remote(dest, 'origin', url.rstrip('/'))
    except httpx.HTTPError as exc:
        shutil.rmtree(dest, ignore_errors=True)
        raise ByteVCSError(f'Clone failed: {exc}') from exc
    except Exception:
        shutil.rmtree(dest, ignore_errors=True)
        raise
    return dest


def _headers():
    token = os.environ.get('BYTEVCS_REMOTE_TOKEN', '').strip()
    return {'Authorization': f'Bearer {token}'} if token else {}
