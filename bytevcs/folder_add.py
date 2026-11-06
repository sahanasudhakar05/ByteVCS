"""Stage whole folders: `bytevcs add .` and `bytevcs add <folder>`."""

from __future__ import annotations

import fnmatch
import os
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from bytevcs.repository import ByteVCSError, add_file

MAX_FILE_BYTES = 5 * 1024 * 1024

# Never staged by default. Extend per project with a .bytevcsignore file.
DEFAULT_IGNORE = [
    ".bytevcs", ".git", ".hg", ".svn",
    "node_modules", "__pycache__", ".venv", "venv", ".idea", ".vscode",
    ".DS_Store", "Thumbs.db",
    "*.pyc", "*.pyo",
    # likely secrets
    ".env", ".env.*", "*.pem", "*.key", "*.p12", "*.pfx",
    "id_rsa", "id_rsa.*", "id_ed25519", "id_ed25519.*",
]


def load_ignore(repo: Path) -> list[str]:
    patterns = list(DEFAULT_IGNORE)
    extra = repo / ".bytevcsignore"
    if extra.is_file():
        for line in extra.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                patterns.append(line)
    return patterns


def is_ignored(rel_posix: str, patterns: list[str]) -> bool:
    parts = rel_posix.split("/")
    for pattern in patterns:
        pattern = pattern.rstrip("/")
        if "/" in pattern:
            if fnmatch.fnmatch(rel_posix, pattern) or rel_posix.startswith(pattern + "/"):
                return True
        elif any(fnmatch.fnmatch(part, pattern) for part in parts):
            return True
    return False


def add_path(repo: Path, path_arg: str) -> dict:
    """Stage a file or a whole folder. Relative paths resolve against the
    current directory (like git). Returns a summary of what happened."""
    repo = Path(repo).resolve()
    target = Path(path_arg)
    if not target.is_absolute():
        target = Path.cwd() / target
    target = target.resolve()
    if target != repo and not target.is_relative_to(repo):
        raise ByteVCSError("Path is outside the repository.")
    if not target.exists():
        raise ByteVCSError(f"Path not found: {path_arg}")

    patterns = load_ignore(repo)
    summary = {"staged": 0, "ignored": 0, "too_large": [], "symlinks": 0}

    def stage(file_path: Path) -> None:
        rel = file_path.relative_to(repo).as_posix()
        if file_path.is_symlink():
            summary["symlinks"] += 1
            return
        if is_ignored(rel, patterns):
            summary["ignored"] += 1
            return
        if file_path.stat().st_size > MAX_FILE_BYTES:
            summary["too_large"].append(rel)
            return
        with redirect_stdout(StringIO()):
            add_file(repo, rel)
        summary["staged"] += 1

    if target.is_file():
        stage(target)
        if summary["ignored"]:
            raise ByteVCSError(
                f"'{path_arg}' is ignored by default or by .bytevcsignore."
            )
        return summary

    for current, dirs, files in os.walk(target, followlinks=False):
        current_path = Path(current)
        kept = []
        for d in sorted(dirs):
            rel_dir = (current_path / d).relative_to(repo).as_posix()
            if is_ignored(rel_dir, patterns):
                summary["ignored"] += 1
            elif (current_path / d).is_symlink():
                summary["symlinks"] += 1
            else:
                kept.append(d)
        dirs[:] = kept
        for name in sorted(files):
            stage(current_path / name)
    return summary