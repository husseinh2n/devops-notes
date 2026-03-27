"""
git_ops.py — Creates a GitHub repo via the REST API, writes files locally,
and pushes them in staggered commit batches to simulate realistic dev history.
"""

import os
import json
import random
import subprocess
import tempfile
import time
from pathlib import Path


# Load config
_cfg_path = Path(__file__).parent / "config.json"
_cfg = json.loads(_cfg_path.read_text()) if _cfg_path.exists() else {}

COMMIT_DELAY_MIN, COMMIT_DELAY_MAX = _cfg.get("commit_delay_range", [60, 150])
GITHUB_API = "https://api.github.com"


# ── GitHub REST API helpers ────────────────────────────────────────────────────

def _gh_headers() -> dict:
    token = os.environ["GITHUB_TOKEN"]
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _gh_user() -> str:
    return os.environ["GITHUB_USERNAME"]


def create_github_repo(repo_name: str, description: str) -> str:
    """
    Create a new public GitHub repo. Returns the clone URL.
    Raises on failure.
    """
    import requests

    url = f"{GITHUB_API}/user/repos"
    payload = {
        "name": repo_name,
        "description": description,
        "private": False,
        "auto_init": False,
        "has_issues": True,
        "has_projects": False,
        "has_wiki": False,
    }
    resp = requests.post(url, headers=_gh_headers(), json=payload, timeout=30)

    if resp.status_code == 422:
        # Repo already exists — reuse it
        print(f"  ⚠ Repo '{repo_name}' already exists, reusing.")
        clone_url = f"https://github.com/{_gh_user()}/{repo_name}.git"
        return clone_url

    resp.raise_for_status()
    clone_url = resp.json()["clone_url"]
    print(f"  ✔ Created repo: {clone_url}")
    return clone_url


def delete_github_repo(repo_name: str) -> None:
    """Delete a repo — used during dry-run cleanup or testing."""
    import requests
    url = f"{GITHUB_API}/repos/{_gh_user()}/{repo_name}"
    resp = requests.delete(url, headers=_gh_headers(), timeout=30)
    if resp.status_code not in (204, 404):
        resp.raise_for_status()
    print(f"  ✔ Deleted repo: {repo_name}")


# ── Local git helpers ──────────────────────────────────────────────────────────

def _run(cmd: list[str], cwd: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, check=check, capture_output=True, text=True)


def _write_files(base_dir: str, files: dict[str, str]) -> None:
    """Write all files to disk, creating parent directories as needed."""
    for rel_path, content in files.items():
        dest = Path(base_dir) / rel_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")


def _group_into_batches(files: dict[str, str]) -> list[tuple[str, list[str]]]:
    """
    Group files into 4-6 logical commit batches with human-readable commit messages.
    Returns a list of (commit_message, [list_of_file_paths]).
    """
    ci_files = []
    readme_files = []
    test_files = []
    doc_files = []
    config_files = []
    code_files = []

    for path in files:
        p = path.lower()
        if ".github/" in p or "workflow" in p:
            ci_files.append(path)
        elif p.endswith("readme.md") or p.endswith(".md"):
            readme_files.append(path)
        elif "test" in p or "spec" in p or p.endswith(".bats"):
            test_files.append(path)
        elif p in (".env.example", "config.json", ".gitignore", "requirements.txt",
                   "package.json", "go.mod", "go.sum"):
            config_files.append(path)
        elif p.endswith(".md") or p.endswith(".rst") or p.endswith(".txt") and "require" not in p:
            doc_files.append(path)
        else:
            code_files.append(path)

    batches = []

    # Always start with project skeleton
    skeleton = config_files or (code_files[:1] if code_files else [])
    if skeleton:
        batches.append(("init project structure", skeleton))
        code_files = [f for f in code_files if f not in skeleton]

    if code_files:
        batches.append(("add core implementation", code_files))

    if test_files:
        batches.append(("add tests", test_files))

    if ci_files:
        batches.append(("add ci workflow", ci_files))

    if readme_files or doc_files:
        batches.append(("add readme and docs", readme_files + doc_files))

    # Anything that didn't fit
    all_batched = {f for _, fs in batches for f in fs}
    leftover = [f for f in files if f not in all_batched]
    if leftover:
        batches.append(("add remaining files", leftover))

    return batches


# ── Main entry point ───────────────────────────────────────────────────────────

def staggered_commit_and_push(
    repo_name: str,
    files: dict[str, str],
    work_dir: str | None = None,
) -> str:
    """
    1. Writes all files to a temp directory (or work_dir).
    2. Groups them into logical commit batches.
    3. Commits each batch with a human delay between them.
    4. Pushes to origin main.

    Returns the path to the working directory (caller can clean up).
    """
    if work_dir is None:
        work_dir = tempfile.mkdtemp(prefix=f"{repo_name}-")

    print(f"  → Working directory: {work_dir}")

    # Write all files
    _write_files(work_dir, files)

    # Ensure a .gitignore exists
    gi_path = Path(work_dir) / ".gitignore"
    if not gi_path.exists():
        gi_path.write_text(
            ".env\n__pycache__/\n*.pyc\n.DS_Store\n*.egg-info/\ndist/\nbuild/\n",
            encoding="utf-8",
        )

    # Git identity (use env vars if set, fall back to bot identity)
    git_name  = os.environ.get("GIT_AUTHOR_NAME",  "DevOps Bot")
    git_email = os.environ.get("GIT_AUTHOR_EMAIL", "devops-bot@noreply.github.com")

    # Prepare clone URL with embedded token for authenticated push
    token    = os.environ["GITHUB_TOKEN"]
    username = _gh_user()
    auth_url = f"https://{username}:{token}@github.com/{username}/{repo_name}.git"

    # Init git repo
    _run(["git", "init", "-b", "main"], cwd=work_dir)
    _run(["git", "config", "user.name",  git_name],  cwd=work_dir)
    _run(["git", "config", "user.email", git_email], cwd=work_dir)
    _run(["git", "remote", "add", "origin", auth_url], cwd=work_dir)

    # Commit in batches
    batches = _group_into_batches(files)
    # Always include .gitignore in first commit
    first_batch_files = batches[0][1] if batches else []
    if ".gitignore" not in first_batch_files and not files.get(".gitignore"):
        first_batch_files.append(".gitignore")

    print(f"  → Committing in {len(batches)} batch(es):")

    for i, (message, batch_files) in enumerate(batches):
        # Stage only files in this batch
        for f in batch_files:
            rel = Path(f).as_posix()
            _run(["git", "add", rel], cwd=work_dir, check=False)

        result = _run(
            ["git", "commit", "-m", message],
            cwd=work_dir,
            check=False,
        )
        if result.returncode != 0:
            print(f"     ⚠ Commit '{message}' produced no changes (skipping).")
            continue

        print(f"     ✔ [{i+1}/{len(batches)}] {message}")

        # Stagger commits unless it's the last one
        if i < len(batches) - 1:
            delay = random.randint(COMMIT_DELAY_MIN, COMMIT_DELAY_MAX)
            print(f"     ⏱ Waiting {delay}s before next commit...", flush=True)
            time.sleep(delay)

    # Push
    print("  → Pushing to GitHub...", flush=True)
    _run(["git", "push", "-u", "origin", "main"], cwd=work_dir)
    print(f"  ✔ Pushed to https://github.com/{username}/{repo_name}")

    return work_dir


def commit_and_push_patch(
    repo_name: str,
    patched_files: dict[str, str],
    commit_message: str,
    work_dir: str,
) -> None:
    """
    Write patched files into an existing work_dir and push a fix commit.
    Used by the repair loop.
    """
    _write_files(work_dir, patched_files)

    for rel_path in patched_files:
        _run(["git", "add", rel_path], cwd=work_dir, check=False)

    result = _run(
        ["git", "commit", "-m", commit_message],
        cwd=work_dir,
        check=False,
    )
    if result.returncode != 0:
        print("     ⚠ Patch produced no file changes — skipping commit.")
        return

    _run(["git", "push"], cwd=work_dir)
    print(f"  ✔ Pushed repair commit: {commit_message}")
