# DevSecOps PoC — Beginner's Step-by-Step Guide
ee
**Goal:** Build a self-hosted pipeline on GitHub that automatically **scans** code for
security issues (SAST, DAST, SCA), **reviews/triages** them in GitHub's Security tab, and
**auto-remediates** vulnerable dependencies with a self-merging pull request.

**Target app:** [OWASP Juice Shop](https://owasp.org/www-project-juice-shop/) — a deliberately
insecure Node.js app, so your scans will actually find things.

**Your choices (locked in):** demo vulnerable app · GitHub.com repo + self-hosted runner ·
free/open-source tools (Semgrep, Trivy, OWASP ZAP) · full auto-remediate + merge.

---

## The big picture (read this first)

Three kinds of scanning, in plain English:

| Type | What it looks at | Tool we use | Analogy |
|------|------------------|-------------|---------|
| **SAST** | Your source code, without running it | **Semgrep** | Proofreading the recipe |
| **SCA** | Third-party libraries you depend on | **Trivy** | Checking the ingredients aren't recalled |
| **DAST** | The **running** app, attacking it from outside | **OWASP ZAP** | Taste-testing the cooked dish |

The flow you're building:

```
  git push
     │
     ▼
 GitHub Actions (on your self-hosted runner)
     ├── SAST  (Semgrep) ─┐
     ├── SCA   (Trivy)   ─┼──►  results uploaded as SARIF
     └── DAST  (ZAP)     ─┘         │
                                    ▼
                        GitHub "Security" tab  ← you review & triage here
                                    │
                                    ▼
                   Auto-remediate workflow: npm audit fix
                                    │
                                    ▼
                     Pull Request → auto-merge (PoC only)
```

> **What is SARIF?** A standard JSON file format for security findings. When a tool outputs
> SARIF, GitHub can display those findings natively in the **Security → Code scanning** tab.
> That single tab becomes your triage dashboard for all three tools.

---

## Answering your original question: token permissions

You were on the "create a fine-grained personal access token" screen. Good news for a PoC:

**For the scanning pipeline you do NOT need a personal access token at all.** GitHub Actions
gives every workflow a built-in `GITHUB_TOKEN` automatically. You just declare the permissions
inside the workflow file (already done for you):

```yaml
permissions:
  contents: read
  security-events: write   # lets the pipeline upload findings to the Security tab
  pull-requests: write     # (auto-remediate workflow) open + merge the fix PR
```

**When you WOULD create a fine-grained PAT** (e.g. for a bot account, or to trigger other
workflows): on that screen, select these repository permissions —

| Permission | Access | Why |
|------------|--------|-----|
| **Metadata** | Read | Required, auto-selected |
| **Contents** | Read and write | Check out code, commit auto-fixes |
| **Pull requests** | Read and write | Open and merge the remediation PR |
| **Code scanning alerts** | Read and write | Upload SARIF, dismiss/triage alerts |
| **Actions** | Read | Read workflow run status |
| **Administration** | ❌ none | You do **not** need it — uncheck it |

Leave everything else (Secrets, Variables, Webhooks, Workflows, etc.) **unchecked**. Least
privilege is the whole point of DevSecOps. Only add **Workflows: Read/write** if the token
itself needs to edit files under `.github/workflows/`.

---

## What you need before starting

- A **GitHub account** (free is fine) and basic git installed on your machine.
- A machine to be the **self-hosted runner**: Linux or macOS, or Windows with WSL2. It needs
  **Docker** installed (the DAST job runs the app in a container).
- ~1 hour. No prior security-tooling experience assumed.

---

## Step 1 — Create the PoC repository

1. Go to GitHub → **New repository**. Name it `devsecops-poc`. Make it **Private** (safer while
   you experiment). Tick "Add a README". Click **Create repository**.
2. Clone it to your machine:
   ```bash
   git clone https://github.com/<your-username>/devsecops-poc.git
   cd devsecops-poc
   ```

> **Why a fresh, isolated repo?** Because you enabled **auto-merge**. You never want a
> self-merging robot pointed at real code. This throwaway repo is its sandbox.

---

## Step 2 — Add the demo vulnerable app

We won't copy Juice Shop's whole source. For scanning we only need something with a
**vulnerable dependency list** for SAST/SCA, and we run the real app in Docker for DAST.

Create a minimal `package.json` so Trivy/Semgrep have something to chew on:

```bash
cat > package.json <<'EOF'
{
  "name": "devsecops-poc-demo",
  "version": "1.0.0",
  "description": "Deliberately outdated deps for scanning practice",
  "dependencies": {
    "lodash": "4.17.4",
    "express": "4.16.0",
    "minimist": "1.2.0",
    "jsonwebtoken": "8.3.0"
  }
}
EOF

npm install --package-lock-only   # generates package-lock.json without downloading
git add package.json package-lock.json
git commit -m "Add demo app with vulnerable dependencies"
```

Those pinned old versions have known CVEs — perfect fodder for Trivy (SCA) and later for
auto-remediation.

---

## Step 3 — Set up the self-hosted runner

A "self-hosted runner" is just your own computer, registered with GitHub, so your workflows
run on **your** hardware instead of GitHub's cloud.

1. In your repo: **Settings → Actions → Runners → New self-hosted runner**.
2. Pick your OS. GitHub shows you exact copy-paste commands. They look like:
   ```bash
   mkdir actions-runner && cd actions-runner
   curl -o actions-runner.tar.gz -L https://github.com/actions/runner/releases/download/<ver>/<file>.tar.gz
   tar xzf actions-runner.tar.gz
   ./config.sh --url https://github.com/<you>/devsecops-poc --token <TOKEN_SHOWN_ON_PAGE>
   ./run.sh
   ```
3. Leave `./run.sh` running in that terminal. When it says **"Listening for Jobs"**, the runner
   is live. In the Runners page it shows a green **Idle** dot.
4. Make sure **Docker** works on this machine: `docker run hello-world` should succeed.

> The token on that page is a **short-lived runner-registration token** — different from the
> PAT you asked about. Don't confuse the two.

---

## Step 4 — Add the pipeline workflow files

Copy the two files from this bundle into your repo, keeping the folder structure:

```
devsecops-poc/
└── .github/
    └── workflows/
        ├── security-pipeline.yml   ← SAST + SCA + DAST
        └── auto-remediate.yml      ← dependency auto-fix + PR + merge
```

Then commit and push:

```bash
mkdir -p .github/workflows
# copy the two .yml files into .github/workflows/ ...
git add .github/workflows/
git commit -m "Add security pipeline and auto-remediation workflows"
git push
```

The push to `main` immediately triggers `security-pipeline.yml`.

---

## Step 5 — Run the scans and watch them

1. Go to the **Actions** tab. You'll see "Security Pipeline" running, with three parallel jobs:
   **SAST - Semgrep**, **SCA - Trivy**, **DAST - OWASP ZAP**.
2. Click any job to watch its live logs. The DAST job pulls the Juice Shop Docker image, waits
   for it to boot, then ZAP attacks `http://localhost:3000`.
3. First run takes a few minutes (Docker image download). Later runs are faster.

If a job fails, open its log and read the last red lines — usually Docker not installed, or the
runner offline. Fix and re-run from the **Actions** tab ("Re-run jobs").

---

## Step 6 — Review & triage in the Security tab

This is the "review/triage" part of your goal.

1. Go to the **Security** tab → **Code scanning**. After the pipeline finishes you'll see
   findings tagged by tool category (`semgrep`, `trivy`).
2. Click a finding to see: the file/line or the vulnerable package, the severity, and a
   description with a suggested fix.
3. **Triage** = decide what to do with each one. On each alert you can:
   - **Keep it open** (you'll fix it), or
   - **Dismiss** it with a reason: *False positive*, *Used in tests*, or *Won't fix*.
4. Sort by severity and start with **Critical/High**. That prioritisation *is* triage.

> DAST (ZAP) findings appear as a downloadable report artifact on the workflow run and as a
> ZAP HTML report, rather than in the Code scanning list. Open the ZAP job → **Artifacts** to
> download it. (You can later convert ZAP output to SARIF to unify everything, but that's an
> optional enhancement.)

---

## Step 7 — Auto-remediate

You chose **full auto-remediate + merge**. Here's exactly what happens and its limits.

**What can be auto-fixed:** dependency (SCA) issues — the fix is "upgrade to a patched
version", which a machine can do safely. `auto-remediate.yml` runs `npm audit fix`, and if any
package versions changed, it opens a PR and (PoC only) merges it automatically.

**What can NOT be reliably auto-fixed:** SAST/DAST findings that are logic bugs (e.g. SQL
injection in *your* code). These need a human to rewrite code. Don't auto-merge those.

Run it:

1. **Actions** tab → **Auto-Remediate Dependencies** → **Run workflow** (manual trigger). It
   also runs every Monday on a schedule.
2. It creates branch `auto/dependency-fixes`, opens a PR titled *"Automated dependency security
   fixes"*, and — because auto-merge is on — squash-merges it once checks pass.
3. Re-run the Security Pipeline; the previously-High dependency alerts should now be resolved.

> ### ⚠️ Safety — the most important paragraph in this guide
> Auto-merge lands changes in `main` with **no human review**. This is acceptable **only**
> because this is a disposable PoC repo. On any real project:
> - **Delete** the "Enable auto-merge (PoC only)" step so a human approves the PR, and/or
> - protect `main` with a branch rule requiring review + passing checks.
>
> Auto-remediation is powerful and dangerous. Treat "full auto-merge" as a demo of what's
> *possible*, not as a default you'd ship.

---

## Step 8 — (Optional) make it a proper gate

Once it works, turn the pipeline into an actual quality gate:

- In `security-pipeline.yml`, change ZAP's `fail_action: false` to `true`, and remove
  `|| true` / `ignore-unfixed` softeners, so the build **fails** when High/Critical issues
  appear. Now a bad PR can't merge.
- Add **branch protection** (Settings → Branches) requiring the "Security Pipeline" check to
  pass before merge. That's the "Sec" being enforced in DevSecOps.

---

## Troubleshooting cheat-sheet

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| Workflow stuck "Queued" | Runner offline | Restart `./run.sh`; check green Idle dot |
| DAST job fails immediately | Docker not installed/running | `docker run hello-world` to verify |
| "Resource not accessible by integration" | Missing `permissions:` block | Keep the `permissions:` in each workflow |
| No alerts in Security tab | SARIF upload step skipped | Confirm the `upload-sarif` steps ran (they use `if: always()`) |
| Auto-merge PR didn't merge | Branch protection / checks failing | Check the PR's checks, or merge manually |

---

## Glossary

- **CVE** — a public ID for a known vulnerability (e.g. CVE-2021-23337 in lodash).
- **SARIF** — standard JSON format GitHub reads to show findings in the Security tab.
- **Runner** — the machine that executes your workflow (here, self-hosted = your computer).
- **GITHUB_TOKEN** — automatic per-run credential; no PAT needed for basic scanning.
- **Gate** — a check that blocks a merge/deploy when it fails.

---

## What to do next

1. Get the three scans green (Step 5).
2. Practise triage on 3–4 real findings (Step 6).
3. Run auto-remediation once and confirm a dependency alert disappears (Step 7).
4. Then harden it into a real gate (Step 8).

You now have the full loop: **scan → review → triage → auto-remediate**, self-hosted, with
free tools.
