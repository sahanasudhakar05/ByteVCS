"""Persistent repository storage in a private Supabase Storage bucket.

Layout inside the bucket, per repository (<key> is the repository's storage_key):
    <key>/objects/<sha256>   immutable, content-addressed objects
    <key>/refs.json          {"head": "main", "refs": {"main": "<commit hash>"}}

The server's disk (repo-data/) is only a cache. If it is wiped, a repository is rebuilt
from Storage the next time it is used. Without SUPABASE_SECRET_KEY, storage is disabled
and everything stays on local disk (handy for development).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import quote

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

from bytevcs.object_store import read_object
from bytevcs.repo_init import create_repository
from bytevcs.repository import (
    ByteVCSError,
    _read_snapshot,
    _valid_branch_name,
    _validate_snapshot_path,
    save_index,
)

DATA_ROOT = Path(os.environ.get("BYTEVCS_DATA_ROOT", str(ROOT / "repo-data"))).resolve()
HASH_RE = re.compile(r"[0-9a-f]{64}")
WORKERS = 8
PAGE = 1000


class StorageError(OSError):
    """Storage is unreachable or returned an error. Messages never contain secrets."""


_locks: dict[str, threading.Lock] = {}
_guard = threading.Lock()
_client: httpx.Client | None = None


def lock_for(key: str) -> threading.Lock:
    with _guard:
        return _locks.setdefault(key, threading.Lock())


def enabled() -> bool:
    if os.environ.get("SUPABASE_SECRET_KEY", "").strip() and os.environ.get("SUPABASE_URL", "").strip():
        return True
    if os.environ.get("BYTEVCS_REQUIRE_STORAGE", "").strip() == "1":
        raise StorageError("Storage is required but is not configured.")
    return False


# ---------- HTTP layer ----------

def _base() -> str:
    return os.environ["SUPABASE_URL"].strip().rstrip("/") + "/storage/v1"


def _bucket() -> str:
    return os.environ.get("SUPABASE_STORAGE_BUCKET", "repositories").strip() or "repositories"


def _http() -> httpx.Client:
    global _client
    if _client is None:
        _client = httpx.Client(timeout=httpx.Timeout(60.0, connect=10.0))
    return _client


def _object_url(path: str) -> str:
    return f"{_base()}/object/{quote(_bucket(), safe='')}/{quote(path, safe='/')}"


def _send(method: str, url: str, what: str, headers: dict | None = None, **kwargs) -> httpx.Response:
    merged = {"apikey": os.environ["SUPABASE_SECRET_KEY"].strip()}
    if headers:
        merged.update(headers)
    last: StorageError | None = None
    for attempt in range(3):
        try:
            response = _http().request(method, url, headers=merged, **kwargs)
        except httpx.HTTPError:
            last = StorageError(f"Could not reach storage ({what}).")
        else:
            if response.status_code < 500 and response.status_code != 429:
                return response
            last = StorageError(f"Storage is busy ({what}, status {response.status_code}).")
        time.sleep(0.5 * (attempt + 1))
    raise last or StorageError(f"Storage request failed ({what}).")


def _is_missing(response: httpx.Response) -> bool:
    if response.status_code == 404:
        return True
    if response.status_code == 400:
        try:
            body = response.json()
        except ValueError:
            return False
        return isinstance(body, dict) and str(body.get("statusCode")) == "404"
    return False


def _put(path: str, data: bytes, content_type: str = "application/octet-stream") -> None:
    response = _send(
        "POST", _object_url(path), "upload",
        headers={"Content-Type": content_type, "x-upsert": "true"}, content=data,
    )
    if response.status_code >= 400:
        raise StorageError(f"Upload failed (status {response.status_code}). Check the bucket name and key.")


def _get(path: str) -> bytes | None:
    response = _send("GET", _object_url(path), "download")
    if _is_missing(response):
        return None
    if response.status_code >= 400:
        raise StorageError(f"Download failed (status {response.status_code}).")
    return response.content


def _delete(paths: list[str]) -> None:
    url = f"{_base()}/object/{quote(_bucket(), safe='')}"
    response = _send("DELETE", url, "delete", json={"prefixes": paths})
    if response.status_code >= 400:
        raise StorageError(f"Delete failed (status {response.status_code}).")


def _list_objects(key: str) -> list[str]:
    names: list[str] = []
    offset = 0
    url = f"{_base()}/object/list/{quote(_bucket(), safe='')}"
    while True:
        response = _send(
            "POST", url, "list",
            json={"prefix": f"{key}/objects", "limit": PAGE, "offset": offset,
                  "sortBy": {"column": "name", "order": "asc"}},
        )
        if response.status_code >= 400:
            raise StorageError(f"Listing failed (status {response.status_code}).")
        items = response.json()
        if not isinstance(items, list):
            raise StorageError("Unexpected response from storage.")
        for item in items:
            name = item.get("name") if isinstance(item, dict) else None
            if isinstance(name, str) and HASH_RE.fullmatch(name):
                names.append(name)
        if len(items) < PAGE:
            return names
        offset += PAGE


# ---------- local cache helpers ----------

def _manifest(meta: Path) -> dict:
    head_text = (meta / "HEAD").read_text(encoding="utf-8").strip()
    prefix = "ref: refs/heads/"
    head = head_text[len(prefix):] if head_text.startswith(prefix) else "main"
    refs: dict[str, str] = {}
    heads = meta / "refs" / "heads"
    if heads.is_dir():
        for p in sorted(heads.rglob("*")):
            if p.is_file():
                text = p.read_text(encoding="utf-8").strip()
                if HASH_RE.fullmatch(text):
                    refs[p.relative_to(heads).as_posix()] = text
    return {"head": head, "refs": refs}


def _local_hashes(meta: Path) -> set[str]:
    found: set[str] = set()
    objects = meta / "objects"
    if objects.is_dir():
        for folder in objects.iterdir():
            if folder.is_dir() and re.fullmatch(r"[0-9a-f]{2}", folder.name):
                for f in folder.iterdir():
                    h = folder.name + f.name
                    if HASH_RE.fullmatch(h):
                        found.add(h)
    return found


def _load_synced(meta: Path) -> set[str]:
    try:
        data = json.loads((meta / "synced.json").read_text(encoding="utf-8"))
        return {h for h in data if isinstance(h, str) and HASH_RE.fullmatch(h)}
    except (OSError, ValueError, TypeError):
        return set()


def _save_synced(meta: Path, hashes: set[str]) -> None:
    (meta / "synced.json").write_text(json.dumps(sorted(hashes)), encoding="utf-8")


def _cache_dir(key: str) -> Path:
    path = (DATA_ROOT / key).resolve()
    if path.parent != DATA_ROOT:
        raise StorageError("Invalid repository location.")
    return path


# ---------- upload (persist) ----------

def _sync_up(root: Path) -> None:
    key = root.name
    meta = root / ".bytevcs"
    try:
        local = _local_hashes(meta)
        new = sorted(local - _load_synced(meta))
        objects = meta / "objects"

        def upload(h: str) -> None:
            data = (objects / h[:2] / h[2:]).read_bytes()
            if hashlib.sha256(data).hexdigest() != h:
                raise StorageError("A local object is corrupt and was not uploaded.")
            _put(f"{key}/objects/{h}", data)

        if new:
            with ThreadPoolExecutor(WORKERS) as pool:
                list(pool.map(upload, new))
        _put(f"{key}/refs.json", json.dumps(_manifest(meta), sort_keys=True).encode("utf-8"), "application/json")
        _save_synced(meta, local)
        try:
            (meta / "dirty").unlink()
        except FileNotFoundError:
            pass
    except StorageError:
        raise
    except (OSError, ValueError) as exc:
        raise StorageError("Could not save the repository to storage.") from exc


def persist(root: Path) -> None:
    """Upload new objects and the branch pointers. Call after any change."""
    if not enabled():
        return
    root = Path(root).resolve()
    if root.parent != DATA_ROOT or not (root / ".bytevcs").is_dir():
        raise StorageError("Invalid repository location.")
    try:
        _sync_up(root)
    except StorageError:
        try:
            (root / ".bytevcs" / "dirty").write_text("1", encoding="utf-8")
        except OSError:
            pass
        raise


# ---------- restore (hydrate) ----------

def _hydrate(root: Path, key: str) -> None:
    raw = _get(f"{key}/refs.json")
    if raw is None:
        raise StorageError("Repository data was not found in storage.")
    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    tmp = DATA_ROOT / f".restore-{key}-{uuid.uuid4().hex[:8]}"
    try:
        manifest = json.loads(raw.decode("utf-8"))
        refs = manifest["refs"]
        head = manifest.get("head") or "main"
        if not isinstance(refs, dict) or not _valid_branch_name(head):
            raise StorageError("Stored repository data is invalid.")

        create_repository(tmp)
        meta = tmp / ".bytevcs"
        names = _list_objects(key)

        def download(h: str) -> None:
            data = _get(f"{key}/objects/{h}")
            if data is None or hashlib.sha256(data).hexdigest() != h:
                raise StorageError("A stored object is missing or corrupt.")
            target = meta / "objects" / h[:2] / h[2:]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)

        if names:
            with ThreadPoolExecutor(WORKERS) as pool:
                list(pool.map(download, names))

        (meta / "HEAD").write_text(f"ref: refs/heads/{head}\n", encoding="utf-8")
        heads = (meta / "refs" / "heads").resolve()
        for branch, commit in refs.items():
            if not _valid_branch_name(branch) or not isinstance(commit, str) or not HASH_RE.fullmatch(commit):
                raise StorageError("Stored repository data is invalid.")
            path = (heads / branch).resolve()
            if not path.is_relative_to(heads):
                raise StorageError("Stored repository data is invalid.")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(commit + "\n", encoding="utf-8")

        tip = refs.get(head)
        if tip:
            snapshot = _read_snapshot(tmp, tip)
            for rel, h in snapshot.items():
                target = _validate_snapshot_path(tmp, rel)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(read_object(str(tmp), h))
            save_index(tmp, snapshot)
        _save_synced(meta, set(names))

        if root.exists():
            shutil.rmtree(root, ignore_errors=True)
        os.replace(tmp, root)
    except StorageError:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    except (ByteVCSError, OSError, ValueError, KeyError, TypeError) as exc:
        shutil.rmtree(tmp, ignore_errors=True)
        raise StorageError("Could not restore the repository from storage.") from exc


def ensure_local(repo) -> None:
    """Make sure the repository exists on local disk (restoring it if needed)."""
    if not enabled():
        return
    key = repo.storage_key
    root = _cache_dir(key)
    meta = root / ".bytevcs"
    if (meta / "HEAD").is_file():
        if (meta / "dirty").exists():  # an earlier upload failed: retry before serving
            with lock_for(key):
                if (meta / "dirty").exists():
                    _sync_up(root)
        return
    with lock_for(key):
        if (meta / "HEAD").is_file():
            return
        _hydrate(root, key)


def ping() -> bool:
    """True if storage is disabled, or configured and reachable. Raises StorageError otherwise."""
    if not enabled():
        return True
    _list_objects("_health")
    return True

# ---------- command line: selftest / migrate ----------

def selftest() -> None:
    if not enabled():
        print("Storage is NOT configured: set SUPABASE_URL and SUPABASE_SECRET_KEY in .env")
        sys.exit(1)
    path = f"_selftest/{uuid.uuid4().hex}.txt"
    payload = b"bytevcs storage self-test"
    _put(path, payload, "text/plain")
    if _get(path) != payload:
        raise StorageError("Downloaded data did not match what was uploaded.")
    try:
        _delete([path])
    except StorageError:
        print("(could not delete the test file; you can remove the _selftest folder in the dashboard)")
    print("storage ok")


def migrate_all() -> None:
    if not enabled():
        print("Storage is NOT configured: set SUPABASE_URL and SUPABASE_SECRET_KEY in .env")
        sys.exit(1)
    done = failed = 0
    if DATA_ROOT.is_dir():
        for folder in sorted(DATA_ROOT.iterdir()):
            if folder.is_dir() and not folder.name.startswith(".") and (folder / ".bytevcs").is_dir():
                try:
                    persist(folder)
                    print(f"uploaded {folder.name}")
                    done += 1
                except StorageError as exc:
                    print(f"FAILED {folder.name}: {exc}")
                    failed += 1
    print(f"{done} repositories are in storage, {failed} failed.")


if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    if command == "selftest":
        selftest()
    elif command == "migrate":
        migrate_all()
    else:
        print("Usage: python -m api.storage selftest | migrate")