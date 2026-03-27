"""
topic_pool.py — Maintains the pool of 30 junior DevOps project topics
and picks one not yet in history.json.
"""

import json
import random
from pathlib import Path

TOPIC_POOL = [
    {"slug": "nginx-static-site",        "description": "Dockerized Nginx serving a static HTML page with custom config",          "category": "docker"},
    {"slug": "python-flask-ci",           "description": "Flask hello-world with pytest unit tests and GitHub Actions CI",           "category": "python"},
    {"slug": "node-express-docker",       "description": "Express.js app containerized with Dockerfile and docker-compose",          "category": "docker"},
    {"slug": "terraform-s3-bucket",       "description": "Terraform config to provision an S3 bucket using LocalStack",              "category": "terraform"},
    {"slug": "ansible-webserver",         "description": "Ansible playbook to install and configure an Nginx web server",            "category": "ansible"},
    {"slug": "docker-compose-db",         "description": "Multi-container app: Flask + PostgreSQL connected via docker-compose",      "category": "docker"},
    {"slug": "k8s-deployment-yaml",       "description": "Kubernetes Deployment and Service manifests for a simple web app",         "category": "k8s"},
    {"slug": "bash-backup-script",        "description": "Cron-ready backup script with rotation and bats unit tests",               "category": "bash"},
    {"slug": "prometheus-node-exporter",  "description": "Docker Compose monitoring stack with Prometheus and Node Exporter",        "category": "docker"},
    {"slug": "github-actions-lint",       "description": "Python project with flake8 and black linting enforced in CI",             "category": "python"},
    {"slug": "jenkins-pipeline-hello",    "description": "Declarative Jenkinsfile with build, test, and deploy stages",             "category": "docker"},
    {"slug": "vagrant-ubuntu-box",        "description": "Vagrantfile that provisions Ubuntu with shell script provisioner",         "category": "bash"},
    {"slug": "helm-chart-nginx",          "description": "Basic Helm chart packaging an Nginx deployment for Kubernetes",           "category": "helm"},
    {"slug": "docker-multistage-go",      "description": "Go web app using a multi-stage Docker build to minimise image size",      "category": "docker"},
    {"slug": "systemd-service-unit",      "description": "Custom systemd unit file with install and enable script",                 "category": "bash"},
    {"slug": "log-rotation-setup",        "description": "Logrotate configuration with validation and CI integration",              "category": "bash"},
    {"slug": "ssl-cert-checker",          "description": "Python script that checks SSL certificate expiry and alerts in CI",       "category": "python"},
    {"slug": "redis-docker-setup",        "description": "Redis and Python client wired together via Docker Compose",               "category": "docker"},
    {"slug": "makefile-project",          "description": "C project with Makefile build targets, tests, and GitHub Actions CI",     "category": "bash"},
    {"slug": "cloudformation-vpc",        "description": "Basic AWS CloudFormation template defining a VPC (validated only)",       "category": "bash"},
    {"slug": "grafana-dashboard",         "description": "Grafana JSON dashboard provisioned automatically via Docker Compose",     "category": "docker"},
    {"slug": "pre-commit-hooks",          "description": "Python repo with pre-commit hooks config and CI enforcement",             "category": "python"},
    {"slug": "cron-health-check",         "description": "Health-check shell script run on a GitHub Actions schedule",             "category": "bash"},
    {"slug": "docker-network-demo",       "description": "Multi-container networking demo using Docker bridge and overlay networks", "category": "docker"},
    {"slug": "env-config-manager",        "description": "Dotenv-based configuration manager with validation tests",                "category": "python"},
    {"slug": "git-hooks-setup",           "description": "Custom Git hooks (pre-commit, pre-push) with an automated installer",    "category": "bash"},
    {"slug": "nginx-reverse-proxy",       "description": "Nginx reverse proxy routing traffic to a backend app via Compose",       "category": "docker"},
    {"slug": "python-cli-tool",           "description": "Click-based CLI tool with unit tests and a full CI pipeline",            "category": "python"},
    {"slug": "shellcheck-scripts",        "description": "Collection of bash scripts linted with ShellCheck in GitHub Actions",   "category": "bash"},
    {"slug": "docker-healthcheck",        "description": "Dockerfile with HEALTHCHECK instruction and compose restart policy",     "category": "docker"},
]


def load_history(history_path: str = "history.json") -> list[str]:
    p = Path(history_path)
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return []


def save_history(done: list[str], history_path: str = "history.json") -> None:
    Path(history_path).write_text(json.dumps(done, indent=2), encoding="utf-8")


def pick_topic(history_path: str = "history.json", pool: list = TOPIC_POOL) -> dict:
    """Pick a random topic that has not been generated yet."""
    done = load_history(history_path)
    remaining = [t for t in pool if t["slug"] not in done]

    if not remaining:
        raise SystemExit(
            "All 30 topics exhausted. Add more topics to TOPIC_POOL or delete history.json to reset."
        )

    choice = random.choice(remaining)
    done.append(choice["slug"])
    save_history(done, history_path)
    return choice
