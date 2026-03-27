"""
validator.py — Polls GitHub Actions until the CI workflow completes.

Key design decisions from the spec:
- 30-minute timeout (not 10) — free-tier runners queue for a while
- Explicit handling of 'queued' and 'in_progress' states
- Extracts error logs from the failed step for use by repairer.py
"""

import os
import json
import time
from pathlib import Path
import requests

# Load config
_cfg_path = Path(__file__).parent / "config.json"
_cfg = json.loads(_cfg_path.read_text()) if _cfg_path.exists() else {}

POLL_INTERVAL = _cfg.get("ci_poll_interval", 30)   # seconds between polls
POLL_TIMEOUT  = _cfg.get("ci_poll_timeout", 1800)  # 30 minutes total

GITHUB_API = "https://api.github.com"


# ── GitHub API helpers ─────────────────────────────────────────────────────────

def _gh_headers() -> dict:
    return {
        "Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _gh_user() -> str:
    return os.environ["GITHUB_USERNAME"]


# ── Core polling ───────────────────────────────────────────────────────────────

def get_latest_run(owner: str, repo: str) -> dict | None:
    """
    Fetch the most recent Actions workflow run.
    Returns None if no runs exist yet (CI hasn't triggered).
    """
    url = f"{GITHUB_API}/repos/{owner}/{repo}/actions/runs?per_page=1"
    resp = requests.get(url, headers=_gh_headers(), timeout=30)
    resp.raise_for_status()
    runs = resp.json().get("workflow_runs", [])
    return runs[0] if runs else None


def wait_for_workflow(
    owner: str,
    repo: str,
    poll_interval: int = POLL_INTERVAL,
    timeout: int = POLL_TIMEOUT,
) -> dict:
    """
    Poll until the latest workflow run reaches 'completed'.
    Returns the run object (which contains 'conclusion': 'success' | 'failure').
    Raises TimeoutError if the run doesn't complete within `timeout` seconds.
    """
    elapsed = 0
    dots = 0

    print(f"  → Polling CI for {owner}/{repo} (timeout: {timeout}s)...")

    while elapsed < timeout:
        run = get_latest_run(owner, repo)

        if run is None:
            # Workflow hasn't appeared yet — push may still be propagating
            print(f"     [{elapsed}s] Waiting for workflow to appear...", flush=True)
            time.sleep(poll_interval)
            elapsed += poll_interval
            continue

        status     = run["status"]        # queued | in_progress | completed
        conclusion = run.get("conclusion")  # success | failure | cancelled | None

        dots += 1
        marker = "." if dots % 4 != 0 else f" [{elapsed}s]"
        print(marker, end="", flush=True)

        if status == "completed":
            print()  # newline after the dots
            icon = "✅" if conclusion == "success" else "❌"
            print(f"  {icon} Workflow completed: {conclusion}")
            return run

        # Still queued or in_progress — keep waiting
        time.sleep(poll_interval)
        elapsed += poll_interval

    print()
    raise TimeoutError(
        f"Workflow for {owner}/{repo} did not complete within {timeout}s"
    )


# ── Error log extraction ───────────────────────────────────────────────────────

def get_error_logs(owner: str, repo: str, run_id: int, max_chars: int = 6000) -> str:
    """
    Retrieve the raw log from the first failed step in a failed run.
    Truncates to max_chars to stay within LLM context limits.
    """
    # 1. Get jobs for this run
    jobs_url = f"{GITHUB_API}/repos/{owner}/{repo}/actions/runs/{run_id}/jobs"
    jobs_resp = requests.get(jobs_url, headers=_gh_headers(), timeout=30)
    jobs_resp.raise_for_status()
    jobs = jobs_resp.json().get("jobs", [])

    failed_job = next(
        (j for j in jobs if j.get("conclusion") == "failure"),
        jobs[0] if jobs else None,
    )

    if not failed_job:
        return "No failed job found — could not retrieve error logs."

    job_id = failed_job["id"]

    # 2. Get full log for that job
    log_url = f"{GITHUB_API}/repos/{owner}/{repo}/actions/jobs/{job_id}/logs"
    log_resp = requests.get(
        log_url, headers=_gh_headers(), timeout=60, allow_redirects=True
    )

    if log_resp.status_code == 404:
        return "Log not available (404 — job may have been deleted or logs expired)."

    log_resp.raise_for_status()
    log_text = log_resp.text

    # Extract the most relevant portion — failed step lines
    failed_lines = []
    capture = False
    for line in log_text.splitlines():
        if "##[error]" in line or "Error:" in line or "FAILED" in line or capture:
            capture = True
            failed_lines.append(line)

    focused = "\n".join(failed_lines) if failed_lines else log_text

    # Truncate to stay under LLM context limits
    if len(focused) > max_chars:
        half = max_chars // 2
        focused = (
            focused[:half]
            + f"\n\n... [truncated {len(focused) - max_chars} chars] ...\n\n"
            + focused[-half:]
        )

    return focused
