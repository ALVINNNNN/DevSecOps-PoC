# DevSecOps PoC — Learning Guide (What We Built and Why)

This is the story of everything you built, in order, with an explanation of what
each piece does and the concept behind it. Read it top to bottom to understand
the whole system, or jump to a section.

---

## 1. The mental model

**DevSecOps** = automating security checks *inside* your normal development flow,
so problems are caught the moment code changes instead of in an audit months
later. The "engine" that runs the automation is **GitHub Actions** — a system
that runs your defined steps (a "workflow") every time something happens in the
repo (like a `git push`).

You built a pipeline with four capabilities:

| Capability | What it does | Built with |
|-----------|--------------|-----------|
| **SAST** | Reads your source code for insecure patterns | Semgrep |
| **SCA** | Checks your dependencies for known CVEs | Trivy |
| **DAST** | Attacks the running app from the outside | OWASP ZAP |
| **AI DAST** | Claude actively probes the app like a pentester | Anthropic API |
| **Auto-remediation** | Fixes vulnerable dependencies automatically | `npm audit fix` + PR |

The flow:

```
 git push ──► GitHub Actions ──► SAST + SCA ──► results in Security tab
                              └► DAST (ZAP)  ──► report artifact
 (manual)  ──► Claude Agentic DAST ──────────► one GitHub issue per finding
 (schedule)──► Auto-Remediate  ──► npm audit fix ──► Pull Request ──► merge
```

---

## 2. Everything you created (inventory)

```
devsecops-poc/
├── package.json                     # demo app with deliberately vulnerable deps
├── .github/
│   └── workflows/
│       ├── security-pipeline.yml    # SAST + SCA + DAST (runs on every push)
│       ├── auto-remediate.yml       # dependency auto-fix + PR + merge
│       └── claude-dast.yml          # Claude agentic DAST (manual / weekly)
├── scripts/
│   ├── agentic_dast.py              # Claude drives the scan via an HTTP tool
│   └── file_issues.py               # turns findings into GitHub issues
├── README.md                        # the original step-by-step setup guide
└── LEARNING-GUIDE.md                # this file
```

---

## 3. The scanning pipeline — `security-pipeline.yml`

This file defines **three jobs that run in parallel** every time you push to
`main`. Key ideas you learned here:

**Triggers (`on:`).** The workflow runs on `push`, `pull_request`, and
`workflow_dispatch` (a manual "Run workflow" button). Triggers are what connect
"something happened" to "run these steps".

**Permissions.** `security-events: write` is what lets the pipeline publish
findings into the **Security tab**. Without it you'd get "Resource not accessible
by integration". This is least-privilege in action: the job only gets the exact
rights it needs.

**The three jobs:**

1. **SAST — Semgrep.** Installs Semgrep (`pip install semgrep`), runs it against
   your code with community rule packs (`p/default`, `p/owasp-top-ten`), and
   writes results as **SARIF**. SARIF is a standard JSON format for security
   findings that GitHub can display natively.

2. **SCA — Trivy.** Installs Trivy and scans the filesystem, focusing on your
   `package.json`/lockfile. It matches your dependency versions against a
   database of known CVEs and reports the vulnerable ones as SARIF.

3. **DAST — OWASP ZAP.** This one is different: it needs a *running* app. So the
   job first `docker run`s the real OWASP Juice Shop, waits for it to boot, then
   runs a ZAP "baseline" scan against `http://localhost:3000`. The report is
   saved and uploaded as a downloadable **artifact**.

**The upload step (`github/codeql-action/upload-sarif`).** Each SAST/SCA job ends
by pushing its SARIF file to GitHub, which is why Semgrep and Trivy findings
appear in the Security tab but ZAP (which doesn't produce SARIF here) appears as
a file artifact instead.

---

## 4. Auto-remediation — `auto-remediate.yml`

This automates *fixing*, not just *finding*. It runs on a schedule (weekly) or on
demand. Steps:

1. Checkout code and set up Node.
2. Run `npm audit fix`, which upgrades vulnerable dependencies to safe versions.
3. If any files changed, open a **Pull Request** with the fix
   (`peter-evans/create-pull-request`).
4. **(PoC only)** merge that PR automatically.

**The big lesson here:** auto-remediation is only safe for **dependency (SCA)**
fixes, because "upgrade to a patched version" is mechanical. Logic bugs found by
SAST/DAST need a human. And auto-*merge* is dangerous — it lands code in `main`
with no review. It's fine on a throwaway PoC repo; on real code you'd delete the
merge step so a human approves the PR.

---

## 5. Claude agentic DAST — the AI layer

This is the part you added to make DAST smarter. Instead of a fixed rule-based
scanner, **Claude actively behaves like a penetration tester**.

### `scripts/agentic_dast.py`

The core idea is **tool use** (a.k.a. function calling). You give Claude two
tools and let it decide how to use them in a loop:

- **`http_request`** — Claude asks to send an HTTP request (method + path +
  headers + body); the script executes it against the target and hands back the
  response. Crucially, the script **locks all traffic to the one authorized host**
  — if Claude tries to reach anything else, it's blocked. This is a safety guard.
- **`record_finding`** — when Claude confirms a vulnerability, it calls this with
  a title, severity, evidence, and remediation. The script collects these.

The **agent loop** works like this:

```
1. Send Claude the goal + the two tools.
2. Claude replies asking to use a tool (e.g. "GET /rest/products/search?q=test").
3. Script runs it, sends the response back.
4. Repeat — Claude explores, tests payloads, confirms issues.
5. When Claude is done (or hits the step budget), write findings.json.
```

A **step budget** (`--max-steps`, default 40) caps how many tool calls it makes,
which controls both time and API cost. The model is configurable
(`CLAUDE_MODEL`, default `claude-sonnet-5`).

**What actually happened on your run:** Claude explored Juice Shop and found a
real critical bug — it submitted `admin@juice-sh.op' --` as the login email,
bypassed the password check via SQL injection, got back a valid admin JWT, and
**decoded that token to confirm** it had admin role. That's genuine autonomous
security testing, not a canned rule.

### `scripts/file_issues.py`

Takes `findings.json` and creates **one GitHub issue per finding**, with a clean
body (description, evidence, remediation). It:

- **Creates the labels first** (`claude-dast`, `security`, `severity:*`) — because
  `gh issue create` fails on a label that doesn't exist yet. *(This was the bug
  in your last run.)*
- **De-duplicates** by title, so re-running doesn't spam duplicate issues.
- **Degrades gracefully** — if labelling still fails, it files the issue with no
  label rather than crashing.

### `claude-dast.yml`

Wires it together: start the app in Docker → set up Python → run the agent → file
the issues → upload `findings.json` as an artifact. It needs one secret,
`ANTHROPIC_API_KEY`, and `issues: write` permission. It runs **manually or
weekly** (not on every push) because each run costs API tokens.

---

## 6. Infrastructure lessons you learned the hard way

These were the "aha" moments from the errors we hit — the most valuable part.

**Runners: self-hosted vs cloud.** A "runner" is the machine that executes a
workflow. You first registered your Windows PC as a **self-hosted** runner. It
worked (it picked up jobs), but then every scan failed with *"Container action is
only supported on Linux"*.

**Why:** the scanner actions (Semgrep, Trivy, ZAP) are packaged as **Docker
container actions**, and those only run on **Linux** runners — never Windows. The
fix was to switch `runs-on:` to **`ubuntu-latest`**, GitHub's free Linux cloud
runners. Lesson: match your runner OS to what your actions require, and cloud
runners remove a whole class of setup problems.

**Tokens: three different kinds.** You hit confusion here, which is common:
- **Personal Access Token (`github_pat_...`)** — for API access / bots.
- **Runner registration token (`A...`, ~1hr life)** — only for registering a
  runner. Using the wrong one gave you a `404 Not Found`.
- **`GITHUB_TOKEN`** — created automatically for every workflow run; this is what
  the pipeline actually uses. You never needed a PAT for the scanning at all.

**Public vs private repos.** Code scanning (the Security tab) is **free only on
public repos**. On private repos it needs paid GitHub Advanced Security. That's
why the PoC repo is public.

**Warnings vs errors.** The "Node.js 20 is deprecated" and "CodeQL v3 will be
deprecated in December 2026" lines are **warnings** — informational, safe to
ignore. The line that actually matters is `Process completed with exit code 1`.
Learn to read past the noise to the real failure.

---

## 7. How to run everything (cheat sheet)

```powershell
# See recent runs / watch the live one
gh run list
gh run watch

# Run the security pipeline manually
gh workflow run "Security Pipeline (SAST + SCA + DAST)"

# See SAST/SCA findings from the CLI
gh api repos/ALVINNNNN/DevSecOps-PoC/code-scanning/alerts `
  --jq '.[] | {tool: .tool.name, severity: .rule.severity, rule: .rule.id}'

# Download the ZAP (DAST) report
gh run download --name zap-dast-report

# Run the Claude agentic DAST (needs ANTHROPIC_API_KEY secret)
gh workflow run "Claude Agentic DAST"

# Run auto-remediation
gh workflow run "Auto-Remediate Dependencies"

# If a run fails, read only the failing step's log
gh run view <run-id> --log-failed
```

---

## 8. Where results live & how to triage

- **Semgrep + Trivy** → repo **Security** tab → **Code scanning**. Sort by
  severity, click a finding for the detail and suggested fix. Dismiss false
  positives with a reason. *Prioritising by severity is triage.*
- **ZAP** → the workflow run's **Artifacts** → download the HTML report.
- **Claude agentic DAST** → repo **Issues** tab, labelled `claude-dast`. Each is
  a finding to verify and fix or close.

---

## 9. Glossary

- **SAST / SCA / DAST** — static code scan / dependency scan / running-app scan.
- **SARIF** — standard JSON format GitHub reads to show findings in the Security tab.
- **Runner** — the machine that runs a workflow (self-hosted = yours, `ubuntu-latest` = GitHub's).
- **Artifact** — a file a workflow saves for you to download afterward.
- **Tool use / function calling** — giving an AI model tools it can invoke in a loop.
- **CVE** — a public ID for a known vulnerability.
- **Gate** — a check that blocks a merge/deploy when it fails.

---

## 10. Where to go next

1. **Turn it into a gate** — make the pipeline *fail* on High/Critical findings and
   add branch protection, so bad code can't merge.
2. **Cross-reference** — have Claude read the ZAP report and its own findings and
   de-duplicate them into one prioritized list.
3. **Scale the agent** — raise `--max-steps`, or add tools (a headless browser)
   so Claude can test client-side/stored XSS more deeply.
4. **Move on-prem** — if you truly need self-hosted, run a Linux runner inside
   WSL2 and flip `ubuntu-latest` back to `[self-hosted]`.

You built a working scan → review → triage → auto-remediate loop, with an AI
pentester bolted on. That's a real DevSecOps pipeline.
