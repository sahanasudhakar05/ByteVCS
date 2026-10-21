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



