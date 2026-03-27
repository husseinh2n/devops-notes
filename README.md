# DevOps Portfolio Project Generator

Automated tool that generates junior-level DevOps projects, pushes each one to a **new GitHub repo** with realistic commit history, validates the CI pipeline, and self-repairs failures — all within free-tier limits.

---

## Architecture

```
┌─────────────┐    ┌──────────────┐    ┌───────────────┐    ┌────────────┐
│  Topic Pool  │───▶│ LLM Generate │───▶│ Commit & Push │───▶│  Validate  │
│  (30 topics) │    │ (Gemini API) │    │ (git + GitHub │    │  (Actions) │
└─────────────┘    └──────────────┘    │   REST API)   │    └─────┬──────┘
                                       └───────────────┘          │
                                              ▲                   ▼
                                              │           ┌──────────────┐
                                              └───────────│ Self-Repair  │
                                                          │ (max 3 tries)│
                                                          └──────────────┘
```

Each run creates a separate GitHub repo, pushes 4-6 staggered commits, and ensures CI is green before moving on.

---

## Free-tier budget

| Service | Plan | Key limits |
|---------|------|------------|
| Gemini API | Free (2.5 Flash-Lite) | 15 RPM, 1,000 RPD, 250k TPM |
| GitHub | Free | Unlimited public repos |
| GitHub Actions | Free (public repos) | Unlimited minutes |

At 2 projects/week, ~12–24 Gemini calls/week — well within 1,000 RPD.

---

## Setup

### 1. Fork or clone this repo (keep it public)

### 2. Get credentials

| Credential | Where to get it |
|-----------|----------------|
| `GEMINI_API_KEY` | [aistudio.google.com](https://aistudio.google.com/app/apikey) — free |
| `GH_PAT` | [github.com/settings/tokens](https://github.com/settings/tokens) — scopes: `repo`, `workflow` |
| `GITHUB_USERNAME` | Your GitHub username |

### 3. Add GitHub Actions secrets

Go to your repo → **Settings → Secrets and variables → Actions** and add:
- `GEMINI_API_KEY`
- `GH_PAT`
- `GITHUB_USERNAME`

### 4. Test with a dry run

Actions → **Generate DevOps Project** → **Run workflow** → set `dry_run = true`

This generates files locally in the runner without creating any GitHub repos. Check the logs.

### 5. Run live

Remove the `dry_run` flag — or just let the cron fire on Tuesdays and Thursdays at 14:00 UTC.

---

## Running locally

```bash
git clone https://github.com/YOUR_USERNAME/devops-portfolio-generator
cd devops-portfolio-generator
pip install -r requirements.txt
cp .env.example .env
# Fill in .env

# Dry run (no GitHub interaction)
python main.py --dry-run

# Force a specific topic
python main.py --topic nginx-static-site

# Full live run
python main.py
```

---

## File structure

```
.
├── main.py              # Orchestrator
├── topic_pool.py        # 30 topics + random selection
├── generator.py         # Gemini API calls (4 per project)
├── git_ops.py           # Repo creation + staggered commits
├── validator.py         # CI polling (30-min timeout)
├── repairer.py          # Self-repair loop (max 3 retries, IaC-aware)
├── config.json          # Tuneable parameters
├── history.json         # Tracks completed slugs (auto-updated)
├── requirements.txt
├── .env.example
└── .github/
    └── workflows/
        └── generate.yml # Cron scheduler (Tues/Thurs 14:00 UTC)
```

---

## Customisation

**Change schedule:** Edit the cron in `.github/workflows/generate.yml`

**Add topics:** Add entries to `TOPIC_POOL` in `topic_pool.py`

**Reset history:** Delete or empty `history.json` to start over

**Tune repair attempts:** Change `max_repair_attempts` in `config.json`

---

## Notes on IaC topics

Terraform, Kubernetes, Ansible, and Helm configs are the hardest for Flash-Lite to generate correctly on the first attempt. The repairer injects tool-specific syntax rules into the fix prompt. Expect these to use all 3 repair attempts. If they still fail, the repo is left as-is with a failing CI badge — you can fix it manually as a learning exercise.
