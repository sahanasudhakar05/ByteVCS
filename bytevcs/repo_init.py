"""Create an empty ByteVCS repository (same layout as `bytevcs init`)."""

from pathlib import Path


def create_repository(path: Path) -> Path:
    repo_path = Path(path).resolve()
    metadata = repo_path / ".bytevcs"
    if metadata.exists():
        raise FileExistsError("Repository already exists.")
    metadata.mkdir(parents=True)
    (metadata / "objects").mkdir()
    (metadata / "refs" / "heads").mkdir(parents=True)
    (metadata / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (metadata / "index").write_text("{}\n", encoding="utf-8")
    return repo_path