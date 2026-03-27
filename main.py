"""
main.py — DevOps Portfolio Project Generator
Orchestrates the full pipeline: pick → generate → commit → validate → repair.

Usage:
    python main.py              # Full run
    python main.py --dry-run    # Generate files locally, no GitHub interaction
    python main.py --topic nginx-static-site   # Force a specific topic
"""

import argparse
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from topic_pool import pick_topic, TOPIC_POOL
from generator import generate_all_assets
from git_ops import create_github_repo, staggered_commit_and_push
from validator import wait_for_workflow
from repairer import repair_loop


# ── Setup ──────────────────────────────────────────────────────────────────────

load_dotenv()

LOG_FILE = Path("run_log.jsonl")


def _check_env() -> None:
    required = ["GEMINI_API_KEY", "GITHUB_TOKEN", "GITHUB_USERNAME"]
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        print(f"ERROR: Missing environment variables: {', '.join(missing)}")
        print("Copy .env.example to .env and fill in your credentials.")
        sys.exit(1)


def _log_run(entry: dict) -> None:
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def _get_topic_by_slug(slug: str) -> dict:
    match = next((t for t in TOPIC_POOL if t["slug"] == slug), None)
    if not match:
        print(f"ERROR: Unknown topic slug '{slug}'.")
        print("Available slugs:")
        for t in TOPIC_POOL:
            print(f"  {t['slug']}")
        sys.exit(1)
    return match


# ── Main pipeline ──────────────────────────────────────────────────────────────

def run(dry_run: bool = False, force_topic: str | None = None) -> None:
    start = datetime.now(timezone.utc)
    owner = os.environ.get("GITHUB_USERNAME", "")

    print("=" * 60)
    print("  DevOps Portfolio Generator")
    print(f"  {start.strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"  Mode: {'DRY RUN' if dry_run else 'LIVE'}")
    print("=" * 60)

    # ── 1. Pick topic ──────────────────────────────────────────────────────────
    print("\n[1/5] Picking topic...")
    if force_topic:
        topic = _get_topic_by_slug(force_topic)
        print(f"  → Forced: {topic['slug']}")
    else:
        topic = pick_topic()
        print(f"  → Selected: {topic['slug']}")
    print(f"     {topic['description']}")

    # ── 2. Generate assets ─────────────────────────────────────────────────────
    print("\n[2/5] Generating project files via Gemini...")
    files = generate_all_assets(topic)

    if not files:
        print("ERROR: No files generated. Check debug_*.txt files for raw output.")
        sys.exit(1)

    print(f"  ✔ {len(files)} file(s) generated:")
    for f in sorted(files):
        print(f"     {f}")

    # ── Dry-run: write locally and stop ───────────────────────────────────────
    if dry_run:
        out_dir = Path(f"dry-run-{topic['slug']}")
        out_dir.mkdir(exist_ok=True)
        for rel, content in files.items():
            dest = out_dir / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(content, encoding="utf-8")
        print(f"\n[DRY RUN] Files written to: {out_dir.resolve()}")
        print("No GitHub repos were created or modified.")
        return

    # ── Validate env before hitting GitHub ────────────────────────────────────
    _check_env()

    # ── 3. Create GitHub repo + push ──────────────────────────────────────────
    print("\n[3/5] Creating GitHub repo and pushing commits...")
    repo_name = topic["slug"]

    create_github_repo(repo_name, topic["description"])

    work_dir = staggered_commit_and_push(repo_name, files)

    # ── 4. Validate CI ────────────────────────────────────────────────────────
    print("\n[4/5] Waiting for CI pipeline...")

    # Initial CI check — if it passes immediately, we're done
    try:
        run_result = wait_for_workflow(owner, repo_name)
        initial_pass = run_result["conclusion"] == "success"
    except TimeoutError:
        print("  ⚠ Initial CI wait timed out. Proceeding to repair loop.")
        initial_pass = False
        run_result = {"conclusion": "failure", "id": None}

    # ── 5. Self-repair if needed ───────────────────────────────────────────────
    if initial_pass:
        print("\n[5/5] CI passed on first attempt — no repairs needed! 🎉")
        success = True
    else:
        print("\n[5/5] CI failed — starting self-repair loop...")
        success = repair_loop(
            repo_name=repo_name,
            owner=owner,
            files=files,
            topic=topic,
            work_dir=work_dir,
        )

    # ── Cleanup ───────────────────────────────────────────────────────────────
    try:
        shutil.rmtree(work_dir)
    except Exception:
        pass  # Non-fatal

    # ── Log result ────────────────────────────────────────────────────────────
    end = datetime.now(timezone.utc)
    duration = (end - start).seconds

    entry = {
        "timestamp": end.isoformat(),
        "topic": topic["slug"],
        "description": topic["description"],
        "files_generated": len(files),
        "success": success,
        "duration_seconds": duration,
        "repo_url": f"https://github.com/{owner}/{repo_name}",
    }
    _log_run(entry)

    # ── Summary ───────────────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    if success:
        print(f"  ✅ SUCCESS — {repo_name}")
        print(f"     https://github.com/{owner}/{repo_name}")
    else:
        print(f"  ⚠  PARTIAL — {repo_name} has failing CI after all repair attempts")
        print(f"     https://github.com/{owner}/{repo_name}")
    print(f"  Duration: {duration}s")
    print("=" * 60)


# ── CLI entry point ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Generate a junior DevOps portfolio project and push it to GitHub."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Generate files locally without touching GitHub.",
    )
    parser.add_argument(
        "--topic",
        type=str,
        default=None,
        metavar="SLUG",
        help="Force a specific topic slug (bypasses random selection and history).",
    )
    args = parser.parse_args()

    run(dry_run=args.dry_run, force_topic=args.topic)
