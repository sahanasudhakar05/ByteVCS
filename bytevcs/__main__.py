"""Command-line interface for ByteVCS."""

import argparse
from pathlib import Path

from bytevcs.object_store import store_object
from bytevcs.repository import (
    ByteVCSError,
    add_file,
    checkout_branch,
    create_branch,
    create_commit,
    find_repository,
    list_branches,
    merge_branch,
    remove_file,
    show_diff,
    show_log,
    show_status,
)


def init_repository(path: str) -> None:
    repo_path = Path(path).resolve()
    metadata_path = repo_path / ".bytevcs"
    if metadata_path.exists():
        print("Error: This directory is already a ByteVCS repository.")
        return
    try:
        metadata_path.mkdir(parents=True)
        (metadata_path / "objects").mkdir()
        (metadata_path / "refs" / "heads").mkdir(parents=True)
        (metadata_path / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
        (metadata_path / "index").write_text("{}\n", encoding="utf-8")
    except OSError as error:
        print(f"Error initializing repository: {error}")
        return
    print(f"Initialized empty ByteVCS repository in {metadata_path}")


def hash_file(repo_path: str, file_path: str) -> None:
    try:
        content = Path(file_path).read_bytes()
        print(store_object(repo_path, content))
    except (OSError, ValueError) as error:
        print(f"Error: {error}")


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="bytevcs", description="ByteVCS - A Git-inspired version control system"
    )
    subparsers = parser.add_subparsers(dest="command")

    init_parser = subparsers.add_parser("init", help="Initialize a repository")
    init_parser.add_argument("path", nargs="?", default=".")

    hash_parser = subparsers.add_parser("hash-object", help="Store a file by content hash")
    hash_parser.add_argument("file")
    hash_parser.add_argument("--repo", required=True)

    add_parser = subparsers.add_parser("add", help="Stage a file")
    add_parser.add_argument("file")

    rm_parser = subparsers.add_parser("rm", help="Stage deletion of a tracked file")
    rm_parser.add_argument("file")

    subparsers.add_parser("status", help="Show repository status")

    commit_parser = subparsers.add_parser("commit", help="Save staged changes as a commit")
    commit_parser.add_argument("-m", "--message", required=True)

    subparsers.add_parser("log", help="Show commit history")

    diff_parser = subparsers.add_parser("diff", help="Show file differences")
    diff_parser.add_argument("--staged", action="store_true", help="Compare index with HEAD")
    diff_parser.add_argument("file", nargs="?", help="Limit output to one file")

    branch_parser = subparsers.add_parser("branch", help="List branches or create one")
    branch_parser.add_argument("name", nargs="?", help="Name of new branch")

    checkout_parser = subparsers.add_parser("checkout", help="Switch to an existing branch")
    checkout_parser.add_argument("branch")

    merge_parser = subparsers.add_parser("merge", help="Merge a branch into the current branch")
    merge_parser.add_argument("branch")

    args = parser.parse_args()
    try:
        if args.command == "init":
            init_repository(args.path)
        elif args.command == "hash-object":
            hash_file(args.repo, args.file)
        elif args.command:
            repo = find_repository(".")
            if args.command == "add":
                add_file(repo, args.file)
            elif args.command == "rm":
                remove_file(repo, args.file)
            elif args.command == "status":
                show_status(repo)
            elif args.command == "commit":
                create_commit(repo, args.message)
            elif args.command == "log":
                show_log(repo)
            elif args.command == "diff":
                show_diff(repo, staged=args.staged, file_path=args.file)
            elif args.command == "branch":
                create_branch(repo, args.name) if args.name else list_branches(repo)
            elif args.command == "checkout":
                checkout_branch(repo, args.branch)
            elif args.command == "merge":
                merge_branch(repo, args.branch)
        else:
            parser.print_help()
    except (OSError, ValueError, KeyError) as error:
        print(f"Error: {error}")


if __name__ == "__main__":
    main()
