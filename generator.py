"""
generator.py — Calls Gemini API to generate 4 assets per project.

Rate limit: Gemini 2.5 Flash-Lite = 15 RPM.
All API calls are separated by API_CALL_DELAY seconds to stay safely under the cap.
"""

import os
import re
import time
import json
import requests
from pathlib import Path


# Load config or fall back to sensible defaults
_cfg_path = Path(__file__).parent / "config.json"
_cfg = json.loads(_cfg_path.read_text()) if _cfg_path.exists() else {}

MODEL = _cfg.get("model", "gemini-2.5-flash-lite-preview-06-17")
API_CALL_DELAY = _cfg.get("api_call_delay", 5)  # seconds between LLM calls


# ── Low-level API call ─────────────────────────────────────────────────────────

def call_gemini(prompt: str) -> str:
    """
    Send a single prompt to Gemini and return the text response.
    Raises on HTTP errors so the caller can handle retries.
    """
    api_key = os.environ["GEMINI_API_KEY"]
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{MODEL}:generateContent?key={api_key}"
    )
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.3,
            "maxOutputTokens": 8192,
        },
    }
    resp = requests.post(url, json=payload, timeout=120)
    resp.raise_for_status()
    data = resp.json()
    try:
        return data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError) as e:
        raise ValueError(f"Unexpected Gemini response structure: {data}") from e


# ── File block parser ──────────────────────────────────────────────────────────

def parse_files(raw: str) -> dict[str, str]:
    """
    Extracts file blocks from LLM output.

    Expected format:
        ```filename:path/to/file.ext
        <content>
        ```

    Returns a dict of {relative_path: content}.
    Falls back to treating the entire response as a single file if no blocks found.
    """
    pattern = r"```(?:filename|file)?:?\s*([^\s`\n]+)\n(.*?)```"
    matches = re.findall(pattern, raw, re.DOTALL)
    if matches:
        return {path.strip(): content.strip() for path, content in matches}

    # Fallback: also try ===FILE: path=== ... ===END=== format
    pattern2 = r"===FILE:\s*(.+?)===\n(.*?)===END==="
    matches2 = re.findall(pattern2, raw, re.DOTALL)
    if matches2:
        return {path.strip(): content.strip() for path, content in matches2}

    return {}


# ── Prompt builders ────────────────────────────────────────────────────────────

def _prompt_code(topic: dict) -> str:
    return f"""You are an expert DevOps engineer writing beginner-friendly project code.

Project: {topic['slug']}
Description: {topic['description']}

Generate ALL source code and configuration files needed to make this project work.
Requirements:
- Keep it minimal but complete — something a junior engineer can run on their laptop
- Add a comment on every non-obvious line explaining what it does
- Use current, stable tool versions (2024/2025)
- Include a Dockerfile if the project involves containers
- Include docker-compose.yml if multiple services are involved

Wrap EVERY file in this exact format — no other text:
```filename:path/to/file.ext
<full file content>
```

Generate all necessary files now.
"""


def _prompt_tests(topic: dict, code_files: dict) -> str:
    file_summary = "\n".join(
        f"  - {path}" for path in code_files
    )
    framework = {
        "python": "pytest",
        "bash": "bats (Bash Automated Testing System)",
        "docker": "pytest or shell assert statements",
        "k8s": "kubeval or shell assert statements",
        "terraform": "terraform validate + terratest (basic)",
        "ansible": "ansible-lint + molecule (basic)",
        "helm": "helm lint + helm unittest",
    }.get(topic.get("category", "python"), "pytest")

    return f"""You are writing tests for a DevOps project.

Project: {topic['slug']}
Description: {topic['description']}
Existing files:
{file_summary}
Test framework: {framework}

Generate a complete test suite. Requirements:
- Cover the most important behaviours (happy path + at least one failure case)
- Include setup/teardown if containers or files need to be started/stopped
- Add comments explaining what each test verifies
- Keep tests beginner-readable

Wrap EVERY file in this exact format — no other text:
```filename:path/to/file.ext
<full file content>
```
"""


def _prompt_ci(topic: dict) -> str:
    return f"""You are writing a GitHub Actions CI workflow for a DevOps project.

Project: {topic['slug']}
Description: {topic['description']}
Category: {topic.get('category', 'general')}

Generate a file at `.github/workflows/ci.yml` that:
- Runs on every push and pull_request to main
- Uses ubuntu-latest
- Installs all dependencies
- Runs the project's tests
- Lints code if appropriate (flake8 for Python, shellcheck for Bash, helm lint for Helm, etc.)
- Fails loudly on errors — no `|| true` hacks
- Caches dependencies where it makes sense (pip, npm, go modules)

If the project needs Docker, use Docker Compose in the workflow.
If the project needs a database, use GitHub Actions service containers.

Wrap the file in this exact format — no other text:
```filename:.github/workflows/ci.yml
<full file content>
```
"""


def _prompt_readme(topic: dict, all_files: dict) -> str:
    file_list = "\n".join(f"  {path}" for path in sorted(all_files))
    return f"""You are writing a README.md for a beginner DevOps learning project.

Project: {topic['slug']}
Description: {topic['description']}
Files in this project:
{file_list}

Write a clear, friendly README.md that includes:
1. ## What this project does  (2-3 sentences)
2. ## What you will learn  (3-5 bullet points)
3. ## Prerequisites  (tools + install commands for macOS/Linux)
4. ## Project structure  (file tree with one-line description per file)
5. ## How to run it  (numbered steps with exact commands and expected output)
6. ## How it works  (explain each major piece; use ASCII diagrams where helpful)
7. ## Verify it's working  (concrete commands the reader can run to confirm)
8. ## Common issues  (markdown table: Issue | Cause | Fix)
9. ## Next steps  (3 ideas to extend the project)

Tone: friendly, educational, assume the reader is comfortable with a terminal but new to DevOps.

Wrap in this exact format — no other text:
```filename:README.md
<full README content>
```
"""


# ── Main asset generator ───────────────────────────────────────────────────────

def generate_all_assets(topic: dict) -> dict[str, str]:
    """
    Call Gemini 4 times (code → tests → CI → README) with rate-limit delays.
    Returns a merged dict of {filepath: content}.
    """
    all_files: dict[str, str] = {}

    steps = [
        ("code",   lambda: call_gemini(_prompt_code(topic))),
        ("tests",  lambda: call_gemini(_prompt_tests(topic, all_files))),
        ("ci",     lambda: call_gemini(_prompt_ci(topic))),
        ("readme", lambda: call_gemini(_prompt_readme(topic, all_files))),
    ]

    for label, fn in steps:
        print(f"  → Generating {label}...", flush=True)
        raw = fn()
        parsed = parse_files(raw)
        if parsed:
            all_files.update(parsed)
            print(f"     ✔ {len(parsed)} file(s) parsed for '{label}'")
        else:
            print(f"     ⚠ No files parsed for '{label}' — raw response saved to debug_{label}.txt")
            Path(f"debug_{label}.txt").write_text(raw, encoding="utf-8")

        # Throttle: 15 RPM cap = max 1 call every 4 seconds. 5s gives comfortable headroom.
        print(f"     ⏱ Waiting {API_CALL_DELAY}s before next API call...", flush=True)
        time.sleep(API_CALL_DELAY)

    return all_files
