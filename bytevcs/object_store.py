
import hashlib
from pathlib import Path


def store_object(repo_path: str, content: bytes) -> str:
    """Store content by SHA-256 hash and return its hash."""
    repo = Path(repo_path).resolve()
    metadata = repo / ".bytevcs"

    if not metadata.is_dir():
        raise ValueError(f"Not a ByteVCS repository: {repo}")

    object_hash = hashlib.sha256(content).hexdigest()

    object_path = (
        metadata
        / "objects"
        / object_hash[:2]
        / object_hash[2:]
    )

    object_path.parent.mkdir(parents=True, exist_ok=True)

    if not object_path.exists():
        object_path.write_bytes(content)

    return object_hash


def read_object(repo_path: str, object_hash: str) -> bytes:
    """Read an object and verify its content hash."""
    if (
        len(object_hash) != 64
        or any(c not in "0123456789abcdef" for c in object_hash)
    ):
        raise ValueError("Invalid SHA-256 object hash.")

    repo = Path(repo_path).resolve()

    object_path = (
        repo / ".bytevcs" / "objects"
        / object_hash[:2] / object_hash[2:]
    )

    if not object_path.is_file():
        raise FileNotFoundError(
            f"Object not found: {object_hash}"
        )

    content = object_path.read_bytes()

    if hashlib.sha256(content).hexdigest() != object_hash:
        raise ValueError("Object integrity check failed.")

    return content
