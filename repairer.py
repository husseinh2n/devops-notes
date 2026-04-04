"""
repairer.py — Feeds CI error logs back to Gemini, applies the patch,
and re-validates. Runs for up to max_retries iterations (default 3).

IaC-aware: injects tool-specific syntax hints for Terraform, K8s, Ansible, Helm.
Per spec: IaC projects routinely consume all 3 repair attempts with Flash-Lite.
"""

import json
import time
from pathlib import Path

from generator import call_gemini, parse_files, API_CALL_DELAY
from git_ops import commit_and_push_patch
from validator import wait_for_workflow, get_error_logs

# Load config
_cfg_path = Path(__file__).parent / "config.json"
_cfg = json.loads(_cfg_path.read_text()) if _cfg_path.exists() else {}

MAX_RETRIES = _cfg.get("max_repair_attempts", 3)


# ── IaC syntax hints (injected into repair prompts for known-tricky tools) ─────

IAC_SYNTAX_HINTS: dict[str, str] = {
    "terraform": """HCL syntax rules — follow these EXACTLY:
- Blocks:  resource "type" "name" { ... }
- Strings must be quoted. No trailing commas.
- Variables: variable "name" { default = "value" }
- References: var.name, resource.type.name.attr
- provider block must precede resource blocks""",

    "k8s": """Kubernetes YAML rules — follow these EXACTLY:
- Top-level keys: apiVersion, kind, metadata, spec (all required)
- Indentation: 2 spaces, NO tabs
- containerPort is an integer, not a string
- image pull policy values: Always | IfNotPresent | Never
- selector.matchLabels must match template.metadata.labels exactly""",

    "ansible": """Ansible playbook rules — follow these EXACTLY:
- Top level is a YAML list of plays: starts with  - hosts: all
- Tasks use module names as dictionary keys: apt:  copy:  service:
- Indentation: 2 spaces
- become: yes must be at play level or per task, not both
- Module args go on the line after the module key or as sub-keys""",

    "helm": """Helm chart rules — follow these EXACTLY:
- Chart.yaml required fields: apiVersion, name, version
- Templates must be in templates/ directory
- Use {{ .Values.key }} syntax for value injection
- Indent template output with | nindent N
- Named templates start with {{- define "chart.name" -}}""",

    "docker-compose": """Docker Compose v3 rules — follow these EXACTLY:
- Top-level keys: version, services, volumes, networks
- version: '3.8' (quoted string)
- Each service: image or build (not both required, but one must exist)
- ports format: "host:container" (both quoted)
- depends_on is a list of service names, not a dict
- environment can be a list (KEY=value) or dict (KEY: value)""",
}


def get_syntax_hint(topic_slug: str) -> str:
    """Return the IaC syntax hint for this topic, or empty string if not applicable."""
    slug_lower = topic_slug.lower().replace("-", " ")
    for key, hint in IAC_SYNTAX_HINTS.items():
        if key.replace("-", " ") in slug_lower or key in topic_slug:
            return hint
    return ""


def format_files_for_prompt(files: dict[str, str]) -> str:
    """Format all project files into a prompt-friendly block."""
    parts = []
    for path, content in sorted(files.items()):
        parts.append(f"```filename:{path}\n{content}\n```")
    return "\n\n".join(parts)


# ── Repair loop ────────────────────────────────────────────────────────────────

def repair_loop(
    repo_name: str,
    owner: str,
    files: dict[str, str],
    topic: dict,
    work_dir: str,
    max_retries: int = MAX_RETRIES,
) -> bool:
    for attempt in range(1, max_retries + 1):
        print(f"\n  🔄 Repair attempt {attempt}/{max_retries}...")

        try:
            run = wait_for_workflow(owner, repo_name)
        except TimeoutError as e:
            print(f"  ⚠ {e}")
            run = {"conclusion": "failure", "id": None}

        if run["conclusion"] == "success":
            print(f"  ✅ Pipeline passed on attempt {attempt}!")
            return True

        run_id = run.get("id")
        if run_id:
            error_log = get_error_logs(owner, repo_name, run_id)
        else:
            error_log = "Could not retrieve logs — run ID unavailable."

        print(f"  📋 Error log snippet:\n{error_log[:800]}\n  ...")

        syntax_hint = get_syntax_hint(topic["slug"])
        hint_block = (
            f"\n\nIMPORTANT — follow these syntax rules exactly:\n{syntax_hint}"
            if syntax_hint else ""
        )

        repair_prompt = f"""The GitHub Actions CI pipeline for this project failed.

Project: {topic['slug']}
Description: {topic['description']}

CI error logs:
{error_log}

Current project files:
{format_files_for_prompt(files)}
{hint_block}

Diagnose the root cause and provide ONLY the corrected files that need to change.

At the very top of your response, before any files, write one line in this exact format:
FIX: <short description of what you are fixing, max 50 chars>

Then wrap each corrected file in this exact format:
```filename:path/to/file.ext
<corrected content>
```
"""

        print(f"  → Asking Gemini for a fix...", flush=True)
        raw_patch = call_gemini(repair_prompt)

        # Extract the commit message from the FIX: line
        commit_message = f"fix: repair attempt {attempt}"
        for line in raw_patch.splitlines():
            if line.startswith("FIX:"):
                description = line[4:].strip()[:50].lower()
                commit_message = f"fix: {description}"
                break

        patched_files = parse_files(raw_patch)

        if not patched_files:
            print("  ⚠ Gemini returned no parseable files — skipping.")
            Path(f"debug_repair_{attempt}.txt").write_text(raw_patch, encoding="utf-8")
            time.sleep(API_CALL_DELAY)
            continue

        print(f"  → Applying patch: {list(patched_files.keys())}")
        print(f"  → Commit message: {commit_message}")
        files.update(patched_files)

        commit_and_push_patch(
            repo_name=repo_name,
            patched_files=patched_files,
            commit_message=commit_message,
            work_dir=work_dir,
        )

        time.sleep(API_CALL_DELAY)

    print(f"\n  ❌ Pipeline still broken after {max_retries} repair attempts.")
    print(f"     Repo left in current state: https://github.com/{owner}/{repo_name}")
    return False

    # All attempts exhausted
    print(f"\n  ❌ Pipeline still broken after {max_retries} repair attempts.")
    print(f"     Repo left in current state: https://github.com/{owner}/{repo_name}")
    return False
