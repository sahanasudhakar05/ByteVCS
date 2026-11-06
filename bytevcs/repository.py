"""Repository operations for the educational ByteVCS version-control system."""

import difflib
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from bytevcs.object_store import read_object, store_object


class ByteVCSError(ValueError):
    """Expected repository or command error."""


def find_repository(start_path: str | Path) -> Path:
    """Find the repository root by walking upward from start_path."""
    current = Path(start_path).resolve()
    if current.is_file():
        current = current.parent
    for candidate in (current, *current.parents):
        if (candidate / ".bytevcs").is_dir():
            return candidate
    raise ByteVCSError("Not inside a ByteVCS repository.")


def _metadata(repo_path: Path) -> Path:
    repo_path = repo_path.resolve()
    metadata = repo_path / ".bytevcs"
    if not metadata.is_dir():
        raise ByteVCSError(f"Not a ByteVCS repository: {repo_path}")
    return metadata


def _safe_relative_path(repo_path: Path, file_path: str | Path) -> tuple[str, Path]:
    """Return a normalized repository-relative path and safe absolute path."""
    repo_path = repo_path.resolve()
    supplied = Path(file_path)
    target = (repo_path / supplied).resolve() if not supplied.is_absolute() else supplied.resolve()
    if not target.is_relative_to(repo_path):
        raise ByteVCSError("File must be inside the repository.")
    relative = target.relative_to(repo_path)
    if not relative.parts or ".bytevcs" in relative.parts:
        raise ByteVCSError("Cannot operate on ByteVCS internal metadata.")
    return relative.as_posix(), target


def _validate_snapshot_path(repo_path: Path, relative_path: str) -> Path:
    """Validate a path read from repository metadata before writing to it."""
    return _safe_relative_path(repo_path, relative_path)[1]


def load_index(repo_path: Path) -> dict[str, str]:
    index_path = _metadata(repo_path) / "index"
    if not index_path.exists() or not index_path.read_text(encoding="utf-8").strip():
        return {}
    data = json.loads(index_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in data.items()
    ):
        raise ByteVCSError("The staging index is invalid.")
    return data


def save_index(repo_path: Path, index: dict[str, str]) -> None:
    index_path = _metadata(repo_path) / "index"
    index_path.write_text(json.dumps(index, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def get_current_branch(repo_path: Path) -> Path:
    """Resolve HEAD to a branch ref. Detached HEAD is not supported."""
    metadata = _metadata(repo_path)
    head_text = (metadata / "HEAD").read_text(encoding="utf-8").strip()
    if not head_text.startswith("ref: "):
        raise ByteVCSError("Detached HEAD is not supported.")
    ref_name = head_text[5:]
    ref_path = (metadata / ref_name).resolve()
    if not ref_path.is_relative_to(metadata.resolve()):
        raise ByteVCSError("Invalid branch reference.")
    return ref_path


def _current_commit_hash(repo_path: Path) -> str | None:
    branch_path = get_current_branch(repo_path)
    if not branch_path.is_file():
        return None
    value = branch_path.read_text(encoding="utf-8").strip()
    return value or None


def _read_commit(repo_path: Path, commit_hash: str) -> dict:
    try:
        data = json.loads(read_object(str(repo_path), commit_hash).decode("utf-8"))
    except (OSError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ByteVCSError(f"Could not read commit {commit_hash}: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("snapshot"), str):
        raise ByteVCSError(f"Invalid commit object: {commit_hash}")
    return data


def _read_snapshot(repo_path: Path, commit_hash: str | None) -> dict[str, str]:
    if not commit_hash:
        return {}
    commit = _read_commit(repo_path, commit_hash)
    try:
        snapshot = json.loads(read_object(str(repo_path), commit["snapshot"]).decode("utf-8"))
    except (OSError, ValueError, UnicodeDecodeError, json.JSONDecodeError, KeyError) as exc:
        raise ByteVCSError(f"Could not read snapshot for {commit_hash}: {exc}") from exc
    if not isinstance(snapshot, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in snapshot.items()
    ):
        raise ByteVCSError("Commit snapshot is invalid.")
    return snapshot


def add_file(repo_path: Path, file_path: str) -> None:
    repo_path = repo_path.resolve()
    relative, target = _safe_relative_path(repo_path, file_path)
    if not target.is_file():
        raise ByteVCSError(f"File not found: {file_path}")
    object_hash = store_object(str(repo_path), target.read_bytes())
    index = load_index(repo_path)
    index[relative] = object_hash
    save_index(repo_path, index)
    print(f"Staged: {relative}")


def remove_file(repo_path: Path, file_path: str) -> None:
    """Stage a deletion and remove the working-tree file."""
    repo_path = repo_path.resolve()
    relative, target = _safe_relative_path(repo_path, file_path)
    index = load_index(repo_path)
    committed = _read_snapshot(repo_path, _current_commit_hash(repo_path))
    if relative not in index and relative not in committed:
        raise ByteVCSError(f"File is not tracked: {relative}")
    index.pop(relative, None)
    save_index(repo_path, index)
    if target.is_file():
        target.unlink()
    print(f"Staged deletion: {relative}")


def _working_hash(target: Path) -> str:
    return hashlib.sha256(target.read_bytes()).hexdigest()


def _status_data(repo_path: Path) -> dict[str, list[str]]:
    repo_path = repo_path.resolve()
    index = load_index(repo_path)
    head = _read_snapshot(repo_path, _current_commit_hash(repo_path))

    result = {
        "staged_added": sorted(set(index) - set(head)),
        "staged_modified": sorted(p for p in set(index) & set(head) if index[p] != head[p]),
        "staged_deleted": sorted(set(head) - set(index)),
        "unstaged_modified": [],
        "unstaged_deleted": [],
        "untracked": [],
    }
    for relative, staged_hash in index.items():
        target = _validate_snapshot_path(repo_path, relative)
        if not target.is_file():
            result["unstaged_deleted"].append(relative)
        elif _working_hash(target) != staged_hash:
            result["unstaged_modified"].append(relative)

    from bytevcs.folder_add import is_ignored, load_ignore  # local import avoids a cycle
    ignore_patterns = load_ignore(repo_path)

    for target in repo_path.rglob("*"):
        if not target.is_file():
            continue
        relative = target.relative_to(repo_path).as_posix()
        if ".bytevcs" in Path(relative).parts:
            continue
        if is_ignored(relative, ignore_patterns):
            continue
        if relative not in index and relative not in head:
            result["untracked"].append(relative)

    for key in result:
        result[key] = sorted(set(result[key]))
    return result


def show_status(repo_path: Path) -> None:
    branch_path = get_current_branch(repo_path)
    data = _status_data(repo_path)
    print("On branch:", branch_path.name)
    if not any(data.values()):
        print("Nothing to commit, working tree clean")
        return
    groups = [
        ("Changes to be committed:", [
            ("new file", data["staged_added"]),
            ("modified", data["staged_modified"]),
            ("deleted", data["staged_deleted"]),
        ]),
        ("Changes not staged for commit:", [
            ("modified", data["unstaged_modified"]),
            ("deleted", data["unstaged_deleted"]),
        ]),
        ("Untracked files:", [("untracked", data["untracked"])]),
    ]
    for heading, items in groups:
        populated = [(label, paths) for label, paths in items if paths]
        if not populated:
            continue
        print(f"\n{heading}")
        for label, paths in populated:
            for path in paths:
                print(f"  {label}: {path}")


def _make_commit(repo_path: Path, snapshot: dict[str, str], message: str,
                 parent: str | None, merge_parent: str | None = None) -> str:
    snapshot_bytes = json.dumps(snapshot, sort_keys=True).encode("utf-8")
    snapshot_hash = store_object(str(repo_path), snapshot_bytes)
    commit_data = {
        "message": message.strip(),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "parent": parent,
        "snapshot": snapshot_hash,
    }
    if merge_parent:
        commit_data["merge_parent"] = merge_parent
    commit_hash = store_object(
        str(repo_path), json.dumps(commit_data, sort_keys=True).encode("utf-8")
    )
    branch_path = get_current_branch(repo_path)
    branch_path.parent.mkdir(parents=True, exist_ok=True)
    branch_path.write_text(commit_hash + "\n", encoding="utf-8")
    save_index(repo_path, snapshot)
    return commit_hash


def create_commit(repo_path: Path, message: str) -> None:
    repo_path = repo_path.resolve()
    if not message.strip():
        raise ByteVCSError("Commit message cannot be empty.")
    index = load_index(repo_path)
    if not index and not _read_snapshot(repo_path, _current_commit_hash(repo_path)):
        raise ByteVCSError("Nothing staged to commit.")
    parent = _current_commit_hash(repo_path)
    if index == _read_snapshot(repo_path, parent):
        raise ByteVCSError("Nothing to commit; no staged changes.")
    commit_hash = _make_commit(repo_path, index, message, parent)
    print(f"[{get_current_branch(repo_path).name} {commit_hash[:8]}] {message.strip()}")


def show_log(repo_path: Path) -> None:
    commit_hash = _current_commit_hash(repo_path)
    if not commit_hash:
        print("No commits yet.")
        return
    seen = set()
    while commit_hash and commit_hash not in seen:
        seen.add(commit_hash)
        try:
            commit = _read_commit(repo_path, commit_hash)
        except ByteVCSError as exc:
            print(f"Error: {exc}")
            return
        print(f"commit {commit_hash}")
        if commit.get("merge_parent"):
            print(f"Merge: {commit['parent'][:8]} {commit['merge_parent'][:8]}")
        print(f"Date:   {commit.get('timestamp', 'unknown')}")
        print(f"\n    {commit.get('message', '(no message)')}\n")
        commit_hash = commit.get("parent")


def _file_text(repo_path: Path, object_hash: str | None) -> str:
    if object_hash is None:
        return ""
    content = read_object(str(repo_path), object_hash)
    return content.decode("utf-8", errors="replace")


def _show_file_diff(path: str, old_hash: str | None, new_hash: str | None,
                    old_label: str, new_label: str, repo_path: Path) -> None:
    if old_hash == new_hash:
        return
    old_lines = _file_text(repo_path, old_hash).splitlines(keepends=True)
    new_lines = _file_text(repo_path, new_hash).splitlines(keepends=True)
    diff_lines = difflib.unified_diff(
        old_lines, new_lines,
        fromfile=f"{old_label}/{path}" if old_hash is not None else "/dev/null",
        tofile=f"{new_label}/{path}" if new_hash is not None else "/dev/null",
    )
    print("".join(diff_lines), end="")


def show_diff(repo_path: Path, staged: bool = False, file_path: str | None = None) -> None:
    repo_path = repo_path.resolve()
    index = load_index(repo_path)
    if staged:
        old = _read_snapshot(repo_path, _current_commit_hash(repo_path))
        new = index
        old_label, new_label = "HEAD", "INDEX"
    else:
        old = index
        new = {}
        for relative in index:
            target = _validate_snapshot_path(repo_path, relative)
            if target.is_file():
                new[relative] = store_object(str(repo_path), target.read_bytes())
        old_label, new_label = "INDEX", "WORKTREE"
    paths = sorted(set(old) | set(new))
    if file_path:
        relative, _ = _safe_relative_path(repo_path, file_path)
        paths = [p for p in paths if p == relative]
    printed = False
    for path in paths:
        if old.get(path) != new.get(path):
            _show_file_diff(path, old.get(path), new.get(path), old_label, new_label, repo_path)
            printed = True
    if not printed:
        print("No differences found.")


def _valid_branch_name(name: str) -> bool:
    return bool(name and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]*", name)
                and ".." not in name and not name.endswith("/")
                and not name.endswith(".lock") and ".bytevcs" not in name)


def _branch_ref(repo_path: Path, name: str) -> Path:
    if not _valid_branch_name(name):
        raise ByteVCSError("Invalid branch name.")
    metadata = _metadata(repo_path)
    ref = (metadata / "refs" / "heads" / name).resolve()
    if not ref.is_relative_to((metadata / "refs" / "heads").resolve()):
        raise ByteVCSError("Invalid branch name.")
    return ref


def list_branches(repo_path: Path) -> None:
    current = get_current_branch(repo_path).resolve()
    refs_root = _metadata(repo_path) / "refs" / "heads"
    branches = sorted(p for p in refs_root.rglob("*") if p.is_file())
    if not branches:
        print("No branches yet.")
        return
    for branch in branches:
        name = branch.relative_to(refs_root).as_posix()
        marker = "* " if branch.resolve() == current else "  "
        print(marker + name)


def create_branch(repo_path: Path, name: str) -> None:
    ref = _branch_ref(repo_path, name)
    if ref.exists():
        raise ByteVCSError(f"Branch already exists: {name}")
    current = _current_commit_hash(repo_path)
    if not current:
        raise ByteVCSError("Create the first commit before creating a branch.")
    ref.parent.mkdir(parents=True, exist_ok=True)
    ref.write_text(current + "\n", encoding="utf-8")
    print(f"Created branch {name}")


def _write_snapshot(repo_path: Path, snapshot: dict[str, str]) -> None:
    """Write a snapshot into the working tree, deleting previously tracked absent files."""
    current_index = load_index(repo_path)
    current_head = _read_snapshot(repo_path, _current_commit_hash(repo_path))
    for relative in set(current_index) | set(current_head):
        if relative not in snapshot:
            target = _validate_snapshot_path(repo_path, relative)
            if target.is_file():
                target.unlink()
    for relative, object_hash in snapshot.items():
        target = _validate_snapshot_path(repo_path, relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(read_object(str(repo_path), object_hash))
    save_index(repo_path, snapshot)


def checkout_branch(repo_path: Path, name: str) -> None:
    repo_path = repo_path.resolve()
    target_ref = _branch_ref(repo_path, name)
    if not target_ref.is_file():
        raise ByteVCSError(f"Branch does not exist: {name}")
    if target_ref.resolve() == get_current_branch(repo_path).resolve():
        print(f"Already on branch {name}")
        return
    status = _status_data(repo_path)
    if any(status.values()):
        raise ByteVCSError("Working tree has changes. Commit or clean them before checkout.")
    commit_hash = target_ref.read_text(encoding="utf-8").strip()
    snapshot = _read_snapshot(repo_path, commit_hash)
    _write_snapshot(repo_path, snapshot)
    relative_ref = target_ref.relative_to(_metadata(repo_path)).as_posix()
    (_metadata(repo_path) / "HEAD").write_text(f"ref: {relative_ref}\n", encoding="utf-8")
    print(f"Switched to branch {name}")


def _parents(repo_path: Path, commit_hash: str) -> list[str]:
    commit = _read_commit(repo_path, commit_hash)
    return [p for p in (commit.get("parent"), commit.get("merge_parent")) if p]


def _ancestors(repo_path: Path, commit_hash: str | None) -> list[str]:
    if not commit_hash:
        return []
    result, stack, seen = [], [commit_hash], set()
    while stack:
        current = stack.pop(0)
        if current in seen:
            continue
        seen.add(current)
        result.append(current)
        stack.extend(_parents(repo_path, current))
    return result


def merge_branch(repo_path: Path, name: str) -> None:
    """Merge a branch with fast-forward or a simple per-file three-way merge."""
    repo_path = repo_path.resolve()
    target_ref = _branch_ref(repo_path, name)
    if not target_ref.is_file():
        raise ByteVCSError(f"Branch does not exist: {name}")
    current_hash = _current_commit_hash(repo_path)
    target_hash = target_ref.read_text(encoding="utf-8").strip()
    if not current_hash:
        raise ByteVCSError("Commit on the current branch before merging.")
    if current_hash == target_hash:
        print("Already up to date.")
        return
    status = _status_data(repo_path)
    if any(status.values()):
        raise ByteVCSError("Working tree has changes. Commit or clean them before merging.")

    current_ancestors = _ancestors(repo_path, current_hash)
    target_ancestors = _ancestors(repo_path, target_hash)
    if current_hash in target_ancestors:
        _write_snapshot(repo_path, _read_snapshot(repo_path, target_hash))
        get_current_branch(repo_path).write_text(target_hash + "\n", encoding="utf-8")
        print(f"Fast-forwarded to {name} ({target_hash[:8]})")
        return
    if target_hash in current_ancestors:
        print("Already up to date.")
        return

    target_set = set(target_ancestors)
    base_hash = next((h for h in current_ancestors if h in target_set), None)
    if base_hash is None:
        raise ByteVCSError("Branches have no common ancestor; merge is not supported.")

    base = _read_snapshot(repo_path, base_hash)
    ours = _read_snapshot(repo_path, current_hash)
    theirs = _read_snapshot(repo_path, target_hash)
    merged: dict[str, str] = {}
    conflicts = []
    for path in sorted(set(base) | set(ours) | set(theirs)):
        b, o, t = base.get(path), ours.get(path), theirs.get(path)
        if o == t:
            chosen = o
        elif o == b:
            chosen = t
        elif t == b:
            chosen = o
        else:
            conflicts.append(path)
            continue
        if chosen is not None:
            merged[path] = chosen
    if conflicts:
        print("Merge conflicts (same file changed on both branches):")
        for path in conflicts:
            print(f"  {path}")
        print("No files were changed. Resolve the conflicts manually, then stage and commit.")
        return

    _write_snapshot(repo_path, merged)
    commit_hash = _make_commit(
        repo_path, merged, f"Merge branch '{name}'", current_hash, target_hash
    )
    print(f"Merged {name} into {get_current_branch(repo_path).name} ({commit_hash[:8]})")
