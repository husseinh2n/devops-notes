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

# Force stdout/stderr to use UTF-8 to prevent encoding errors in Windows terminal
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from topic_pool import pick_topic, TOPIC_POOL, load_history
from generator import generate_all_assets
from git_ops import create_github_repo, staggered_commit_and_push
from validator import wait_for_workflow, get_latest_run
from repairer import repair_loop


# ── Setup ──────────────────────────────────────────────────────────────────────

load_dotenv()

LOG_FILE = Path("run_log.jsonl")

# Load config values
_cfg_path = Path("config.json")
_cfg = json.loads(_cfg_path.read_text()) if _cfg_path.exists() else {}
PROJECTS_PER_WEEK = _cfg.get("projects_per_week", 2)
FIXES_PER_WEEK = _cfg.get("fixes_per_week", 5)


def _already_run_today() -> bool:
    """Return True if an action has already been logged today (UTC)."""
    if not LOG_FILE.exists():
        return False
    try:
        with LOG_FILE.open("r", encoding="utf-8") as f:
            lines = f.readlines()
            if not lines:
                return False
            last_line = lines[-1].strip()
            if not last_line:
                return False
            last_entry = json.loads(last_line)
            timestamp_str = last_entry.get("timestamp")
            if timestamp_str:
                last_date = datetime.fromisoformat(timestamp_str).date()
                current_date = datetime.now(timezone.utc).date()
                return last_date == current_date
    except Exception:
        pass
    return False


def load_files_from_dir(directory: Path) -> dict[str, str]:
    """Recursively load all files from a directory into a dict (path -> content)."""
    files = {}
    for path in directory.rglob("*"):
        if path.is_file():
            rel_parts = path.relative_to(directory).parts
            if ".git" in rel_parts or "__pycache__" in rel_parts:
                continue
            try:
                content = path.read_text(encoding="utf-8")
                files[Path(*rel_parts).as_posix()] = content
            except Exception:
                continue
    return files


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

def run(dry_run: bool = False, force_topic: str | None = None, mode: str = "auto") -> None:
    start = datetime.now(timezone.utc)
    owner = os.environ.get("GITHUB_USERNAME", "")

    # Decide the actual mode to run
    if force_topic:
        actual_mode = "new"
    elif mode == "auto":
        # Decided based on the current weekday: 0=Monday, 1=Tuesday, etc.
        weekday = datetime.now().weekday()
        if weekday < PROJECTS_PER_WEEK:
            actual_mode = "new"
        elif weekday < (PROJECTS_PER_WEEK + FIXES_PER_WEEK):
            actual_mode = "fix"
        else:
            print("=" * 60)
            print("  DevOps Portfolio Generator")
            print(f"  {start.strftime('%Y-%m-%d %H:%M UTC')}")
            print("  Mode: IDLE (No scheduled new project or repair run for today)")
            print("=" * 60)
            return
    else:
        actual_mode = mode

    # Check if we already run today (unless explicitly overridden by mode/topic)
    is_explicit = force_topic is not None or mode in ("new", "fix")
    if not dry_run and not is_explicit:
        if _already_run_today():
            print("=" * 60)
            print("  DevOps Portfolio Generator")
            print(f"  {start.strftime('%Y-%m-%d %H:%M UTC')}")
            print("  Info: A project generation or fix has already run today.")
            print("  To override this check and run manually, use --mode new or --mode fix.")
            print("=" * 60)
            return

    print("=" * 60)
    print("  DevOps Portfolio Generator")
    print(f"  {start.strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"  Mode: {'DRY RUN' if dry_run else 'LIVE'} ({actual_mode.upper()} run)")
    print("=" * 60)

    if actual_mode == "new":
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
            
        # Review and complete the README before pushing
        from generator import review_readme
        files = review_readme(files, topic)

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
            "mode": "new"
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

    elif actual_mode == "fix":
        # ── 1. Load history ────────────────────────────────────────────────────────
        print("\n[1/5] Checking previous projects...")
        done_slugs = load_history()
        if not done_slugs:
            print("  → No previously generated projects found in history.json to fix.")
            return

        # ── Validate env before hitting GitHub ────────────────────────────────────
        _check_env()

        # ── 2. Find the first failing project ──────────────────────────────────────
        print(f"  → Found {len(done_slugs)} projects in history. Checking CI status on GitHub...")
        failed_slug = None
        for slug in done_slugs:
            print(f"     Checking {slug}...", end="", flush=True)
            try:
                run_status = get_latest_run(owner, slug)
                if run_status is None:
                    print(" [No CI runs found, needs fix]")
                    failed_slug = slug
                    break
                conclusion = run_status.get("conclusion")
                if conclusion != "success":
                    print(f" [CI status is '{conclusion}', needs fix]")
                    failed_slug = slug
                    break
                else:
                    print(" [CI status is 'success', OK]")
            except Exception as e:
                print(f" [Error checking status: {e}, treating as needs fix]")
                failed_slug = slug
                break

        if not failed_slug:
            print("\n  ✔ All previously generated projects have a successful CI status! No fixes needed.")
            return

        topic = next((t for t in TOPIC_POOL if t["slug"] == failed_slug), None)
        if not topic:
            topic = {
                "slug": failed_slug,
                "description": f"DevOps project for {failed_slug}",
                "category": "unknown"
            }
        
        print(f"\n  → Selected project for repair: {failed_slug}")

        # ── 3. Clone repo locally ──────────────────────────────────────────────────
        print("\n[2/5] Cloning repository...")
        token = os.environ["GITHUB_TOKEN"]
        auth_url = f"https://{owner}:{token}@github.com/{owner}/{failed_slug}.git"
        
        work_dir = tempfile.mkdtemp(prefix=f"repair-{failed_slug}-")
        print(f"  → Temp directory: {work_dir}")
        success = False
        try:
            import subprocess
            try:
                subprocess.run(["git", "clone", auth_url, work_dir], check=True, capture_output=True)
                print("  ✔ Clone successful.")
            except subprocess.CalledProcessError as clone_err:
                err_msg = clone_err.stderr.decode(errors="replace").strip() if clone_err.stderr else str(clone_err)
                print(f"  ❌ Error cloning repository: {err_msg}")
                print(f"  Skipping repair for {failed_slug}.")
                return
            
            # Load files
            files = load_files_from_dir(Path(work_dir))
            print(f"  ✔ Loaded {len(files)} files from workspace.")

            if dry_run:
                print("\n[DRY RUN] Repair would be executed on cloned files, but no push would occur.")
                return

            # ── 4. Run repair loop ──────────────────────────────────────────────────
            print("\n[3/5] Starting self-repair loop on GitHub...")
            success = repair_loop(
                repo_name=failed_slug,
                owner=owner,
                files=files,
                topic=topic,
                work_dir=work_dir,
            )

        finally:
            try:
                shutil.rmtree(work_dir)
            except Exception:
                pass

        # ── 5. Log and output results ──────────────────────────────────────────────
        end = datetime.now(timezone.utc)
        duration = (end - start).seconds

        entry = {
            "timestamp": end.isoformat(),
            "topic": failed_slug,
            "description": topic["description"],
            "files_generated": len(files),
            "success": success,
            "duration_seconds": duration,
            "repo_url": f"https://github.com/{owner}/{failed_slug}",
            "mode": "fix"
        }
        _log_run(entry)

        print("\n" + "=" * 60)
        if success:
            print(f"  ✅ SUCCESS REPAIR — {failed_slug}")
            print(f"     https://github.com/{owner}/{failed_slug}")
        else:
            print(f"  ⚠  FAILED REPAIR — {failed_slug} still failing after repair attempts")
            print(f"     https://github.com/{owner}/{failed_slug}")
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
    parser.add_argument(
        "--mode",
        type=str,
        choices=["auto", "new", "fix"],
        default="auto",
        help="Run mode: 'new' to generate a project, 'fix' to repair existing failing ones, or 'auto' to decide based on weekday.",
    )
    args = parser.parse_args()

    run(dry_run=args.dry_run, force_topic=args.topic, mode=args.mode)
