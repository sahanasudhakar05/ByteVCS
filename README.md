\# ByteVCS



\*\*A Git-inspired version control system built from scratch in Python.\*\*



ByteVCS is a lightweight, local version control system that tracks file changes, stores content-addressed objects, maintains commit history, and supports branching and merging.



\## Features



\- Initialize repositories

\- Store file contents using SHA-256 hashes

\- Stage file additions and deletions

\- Track staged, unstaged, and untracked changes

\- Commit snapshots with messages and timestamps

\- View commit history

\- Compare staged and unstaged changes using diffs

\- Create and switch between branches

\- Merge branches with basic conflict detection

\- Installable command-line interface



\## Tech Stack



\- Python

\- SHA-256 hashing

\- `pathlib` for filesystem operations

\- `unittest` for automated testing



\## Installation



Clone the repository and enter the project directory:



```bash

git clone <YOUR\_GITHUB\_REPOSITORY\_URL>

cd ByteVCS

```



Create and activate a virtual environment on Windows:



```cmd

python -m venv .venv

.venv\\Scripts\\activate

```



Install ByteVCS:



```cmd

python -m pip install -e .

```



\## Usage



Initialize a repository:



```cmd

bytevcs init .

```



Stage a file:



```cmd

bytevcs add hello.txt

```



Create a commit:



```cmd

bytevcs commit -m "Initial commit"

```



Check repository status:



```cmd

bytevcs status

```



View commit history:



```cmd

bytevcs log

```



Create and switch branches:



```cmd

bytevcs branch feature

bytevcs checkout feature

```



View available commands:



```cmd

bytevcs --help

```



\## Testing



Run the automated test suite from the project root:



```cmd

python -m unittest discover -s tests -v

```



\## Project Status



ByteVCS is an educational project under active development. It currently supports local version control operations. Remote repositories, network-based push/pull, authentication, and a web interface are planned future enhancements.



\## Disclaimer



ByteVCS is inspired by Git but is not Git-compatible. It is intended for learning and experimentation, not as a replacement for production version control systems.




## HTTP remotes: push, pull, and clone

ByteVCS includes an initial HTTP remote transport. A remote URL points to a ByteVCS API server endpoint such as `http://127.0.0.1:8000/api/remote/repos/demo`.

Start the server (the server stores remote bundles under `remote-repositories/` by default):

```powershell
python -m pip install -r api\requirements.txt
python -m uvicorn api.main:app --reload
```

In a local ByteVCS repository, configure the remote endpoint and push:

```powershell
python -m bytevcs remote add origin http://127.0.0.1:8000/api/remote/repos/demo
python -m bytevcs push origin
```

On another machine, clone it:

```powershell
python -m bytevcs clone http://SERVER-HOST:8000/api/remote/repos/demo demo-copy
```

Then make a change/commit and use `python -m bytevcs pull origin` to retrieve the latest remote state. `push` uploads the repository metadata/object store and the committed working-tree snapshot; `pull` replaces local `.bytevcs` metadata and materializes tracked files. This first transport is intentionally simple: it transfers a complete repository bundle, is limited to 25 MB per server upload, and does not implement concurrent-push protection, incremental packfiles, conflict-aware merge-on-pull, or production-grade multi-user authorization. Set `BYTEVCS_REMOTE_TOKEN` to the same strong secret on the server and client to require a bearer token. Use HTTPS when exposing the server outside localhost. Do not expose an unauthenticated development server to the public internet.
