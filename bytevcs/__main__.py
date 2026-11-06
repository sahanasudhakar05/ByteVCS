
"""Command-line interface for ByteVCS."""

import argparse
from pathlib import Path

from bytevcs.object_store import store_object
from bytevcs.remotes import add_remote, list_remotes, remove_remote
from bytevcs.remote_sync import clone as clone_repository, pull as pull_repository, push as push_repository
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
        (metadata_path / "HEAD").write_text(
            "ref: refs/heads/main\n", encoding="utf-8"
        )
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
        prog="bytevcs",
        description="ByteVCS - A Git-inspired version control system",
    )
    subparsers = parser.add_subparsers(dest="command")

    init_parser = subparsers.add_parser(
        "init", help="Initialize a repository"
    )
    init_parser.add_argument("path", nargs="?", default=".")

    hash_parser = subparsers.add_parser(
        "hash-object", help="Store a file by content hash"
    )
    hash_parser.add_argument("file")
    hash_parser.add_argument("--repo", required=True)

    add_parser = subparsers.add_parser("add", help="Stage files or folders (use . for everything)")
    add_parser.add_argument("file", nargs="+")

    rm_parser = subparsers.add_parser(
        "rm", help="Stage deletion of a tracked file"
    )
    rm_parser.add_argument("file")

    subparsers.add_parser("status", help="Show repository status")

    commit_parser = subparsers.add_parser(
        "commit", help="Save staged changes as a commit"
    )
    commit_parser.add_argument("-m", "--message", required=True)

    subparsers.add_parser("log", help="Show commit history")

    diff_parser = subparsers.add_parser(
        "diff", help="Show file differences"
    )
    diff_parser.add_argument(
        "--staged", action="store_true", help="Compare index with HEAD"
    )
    diff_parser.add_argument(
        "file", nargs="?", help="Limit output to one file"
    )

    branch_parser = subparsers.add_parser(
        "branch", help="List branches or create one"
    )
    branch_parser.add_argument("name", nargs="?", help="Name of new branch")

    checkout_parser = subparsers.add_parser(
        "checkout", help="Switch to an existing branch"
    )
    checkout_parser.add_argument("branch")

    merge_parser = subparsers.add_parser(
        "merge", help="Merge a branch into the current branch"
    )
    merge_parser.add_argument("branch")

    push_parser = subparsers.add_parser("push", help="Push repository to a configured remote")
    push_parser.add_argument("remote", nargs="?", default="origin")
    pull_parser = subparsers.add_parser("pull", help="Pull repository from a configured remote")
    pull_parser.add_argument("remote", nargs="?", default="origin")
    clone_parser = subparsers.add_parser("clone", help="Clone a remote ByteVCS repository")
    clone_parser.add_argument("url")
    clone_parser.add_argument("path", nargs="?", default=None)

    # Remote configuration
    remote_parser = subparsers.add_parser(
        "remote", help="Manage repository remotes"
    )
    remote_parser.add_argument(
        "-v", "--verbose", action="store_true",
        help="Show remote URLs",
    )
    remote_parser.add_argument(
        "action", nargs="?", choices=["add", "remove"]
    )
    remote_parser.add_argument("name", nargs="?")
    remote_parser.add_argument("url", nargs="?")

    args = parser.parse_args()

    try:
        if args.command == "init":
            init_repository(args.path)
            return

        if args.command == "hash-object":
            hash_file(args.repo, args.file)
            return

        if not args.command:
            parser.print_help()
            return

        if args.command == "clone":
            destination = args.path or args.url.rstrip("/").split("/")[-1]
            result = clone_repository(args.url, destination)
            print(f"Cloned repository into {result}")
            return

        repo = find_repository(".")

        if args.command == "add":
                from bytevcs.folder_add import add_path
                for item in args.file:
                      result = add_path(repo, item)
                      print(f"Staged {result['staged']} file(s) from '{item}' "
                            f"({result['ignored']} ignored)")
                for big in result["too_large"]:
                      print(f"  Skipped (over 5 MB): {big}")
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
        elif args.command == "push":
            print(push_repository(repo, args.remote))
        elif args.command == "pull":
            print(pull_repository(repo, args.remote))
        elif args.command == "remote":
            if args.action == "add":
                if not args.name or not args.url:
                    parser.error(
                        "Usage: bytevcs remote add <name> <url>"
                    )
                add_remote(repo, args.name, args.url)
                print(f"Added remote '{args.name}'")
            elif args.action == "remove":
                if not args.name:
                    parser.error("Usage: bytevcs remote remove <name>")
                remove_remote(repo, args.name)
                print(f"Removed remote '{args.name}'")
            else:
                remotes = list_remotes(repo)
                for name, url in remotes.items():
                    print(f"{name}\t{url}" if args.verbose else name)

    except (ByteVCSError, OSError, ValueError, KeyError) as error:
        parser.exit(1, f"Error: {error}\n")


if __name__ == "__main__":
    main()
