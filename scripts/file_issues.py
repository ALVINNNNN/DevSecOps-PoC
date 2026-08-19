#!/usr/bin/env python3
"""
Read findings.json (from agentic_dast.py) and open one GitHub issue per finding
using the `gh` CLI. De-duplicates against existing issues by title so re-runs
don't spam duplicates.

Usage:  python scripts/file_issues.py findings.json
Env:    GH_TOKEN must be set (the workflow provides secrets.GITHUB_TOKEN).
"""

import json
import subprocess
import sys

LABEL = "claude-dast"


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
        # Label may not exist yet on a brand-new repo -> nothing filed yet.
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
    labels = f"{LABEL},security,severity:{severity}"
    try:
        subprocess.run(
            ["gh", "issue", "create", "--title", title, "--body", body,
             "--label", labels],
            check=True,
        )
    except subprocess.CalledProcessError:
        # A severity:* label may not exist; retry with just our base label.
        subprocess.run(
            ["gh", "issue", "create", "--title", title, "--body", body,
             "--label", LABEL],
            check=True,
        )


def main(path):
    with open(path, encoding="utf-8") as fh:
        findings = json.load(fh).get("findings", [])

    print(f"Agent reported {len(findings)} finding(s).")
    already = existing_titles()

    for f in findings:
        title = f"[DAST] {f.get('title', 'Untitled finding')}"
        if title in already:
            print(f"Skipping existing issue: {title}")
            continue
        create_issue(title, build_body(f), f.get("severity", "info"))
        already.add(title)
        print(f"Created issue: {title}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "findings.json")
