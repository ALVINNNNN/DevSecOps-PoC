#!/usr/bin/env python3
"""
Read findings.json (from agentic_dast.py) and open one GitHub issue per finding
using the `gh` CLI. De-duplicates against existing issues by title so re-runs
don't spam duplicates. Creates the labels it needs first, and degrades
gracefully (files the issue with no label) if labelling still fails.

Usage:  python scripts/file_issues.py findings.json
Env:    GH_TOKEN must be set (the workflow provides secrets.GITHUB_TOKEN).
"""

import json
import subprocess
import sys

LABEL = "claude-dast"

# Labels we file issues under. `gh issue create` fails if a label does not
# exist, so we create them up front. name -> hex color.
LABELS = {
    "claude-dast": "5319e7",
    "security": "d73a4a",
    "severity:critical": "b60205",
    "severity:high": "d93f0b",
    "severity:medium": "fbca04",
    "severity:low": "0e8a16",
    "severity:info": "c5def5",
}


def ensure_labels():
    """Create (or update) every label we use. Idempotent thanks to --force."""
    for name, color in LABELS.items():
        subprocess.run(
            ["gh", "label", "create", name, "--color", color, "--force"],
            capture_output=True, text=True,
        )


def existing_titles():
    """Titles of issues we've already filed under our label (open or closed)."""
    try:
        out = subprocess.run(
            ["gh", "issue", "list", "--state", "all", "--label", LABEL,
             "--limit", "200", "--json", "title"],
            capture_output=True, text=True, check=True,
        ).stdout
        return {item["title"] for item in json.loads(out or "[]")}
    except subprocess.CalledProcessError:
        return set()


def build_body(f):
    return (
        f"**Detected by:** Claude Agentic DAST\n"
        f"**Type:** {f.get('vulnerability_type', '')}\n"
        f"**Severity:** {f.get('severity', 'info')}\n"
        f"**Confidence:** {f.get('confidence', 'suspected')}\n\n"
        f"## Description\n{f.get('description', '')}\n\n"
        f"## Evidence\n```\n{f.get('evidence', '(none)')}\n```\n\n"
        f"## Remediation\n{f.get('remediation', '')}\n\n"
        f"---\n_Filed automatically. Verify before acting; agentic findings can "
        f"include false positives._\n"
    )


def create_issue(title, body, severity):
    """Try with full labels, then base label, then no labels at all."""
    label_sets = [f"{LABEL},security,severity:{severity}", LABEL, None]
    for labels in label_sets:
        cmd = ["gh", "issue", "create", "--title", title, "--body", body]
        if labels:
            cmd += ["--label", labels]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0:
            return True
        print(f"  (retry) label set '{labels}' failed: {result.stderr.strip()}")
    print(f"  ERROR: could not create issue: {title}")
    return False


def main(path):
    with open(path, encoding="utf-8") as fh:
        findings = json.load(fh).get("findings", [])

    print(f"Agent reported {len(findings)} finding(s).")
    ensure_labels()
    already = existing_titles()

    for f in findings:
        title = f"[DAST] {f.get('title', 'Untitled finding')}"
        if title in already:
            print(f"Skipping existing issue: {title}")
            continue
        if create_issue(title, build_body(f), f.get("severity", "info")):
            print(f"Created issue: {title}")
            already.add(title)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "findings.json")
