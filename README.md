# Intelligent Network Configuration Review Platform

A local proof of concept that compares a synthetic network configuration with a
Git baseline, classifies changes, prepares a draft baseline update, and stores
an administrator review in JSON and SQLite.

**Verified:** 13/13 fixture scenarios passed, including ACL statement reordering
and safe stops for unsupported input. The original execution evidence is in
[`evidence/original_run`](evidence/original_run). This is a deterministic local
MVP; live SSH, the GitHub baseline API, Redis, LangGraph, and local model inference
are not integrated yet. Hosting the source on GitHub does not connect the auditor
to the GitHub API.

## Quick start

Install Python 3.10 or later and Git. No third-party Python packages, API keys,
or device credentials are required. Tested locally on Python 3.12.3 and Git 2.43.0.

```bash
git clone https://github.com/vitolscolin/intelligent-network-config-review.git
cd intelligent-network-config-review
python3 demo.py --out runs/demo-1
```

On Windows, use `py -3` instead of `python3` if needed. Run Python normally,
without `-O`, because the demonstration uses assertions to verify outcomes.
Choose a new output directory on each run; existing directories are rejected.
A successful run ends with `"passed": 13`, `"total": 13`, and `"sqlite_rows": 13`.

## Core workflow

1. **Ingest:** read a local snapshot and a baseline at a full Git commit SHA.
2. **Compare:** retain ordering inside each supported configuration category.
3. **Assess:** assign low, medium, or high risk using fixed rules.
4. **Plan:** produce a draft patch describing a possible baseline update.
5. **Review and record:** return findings for administrator review and save the audit.

No device command, baseline overwrite, GitHub pull request, or approval action is
executed. A baseline update draft is only suitable for acceptance if the observed
configuration is intended. An unintended change requires a separate authorized
restoration procedure.

## Files

| Path | Purpose |
| --- | --- |
| `auditor.py` | Parser, ordered comparison, risk rules, supervisor, and persistence |
| `demo.py` | Synthetic fixtures and assertions for the 13 scenarios |
| `evidence/original_run/` | Original synthetic inputs, JSON reports, log, and source hashes |
| `runs/` | Ignored directory for new demonstrations |

The generated `runs/demo-1/combined.json` contains the multi-change review.
`summary.json` contains the case results, and `audits.sqlite` contains one audit
per scenario. `baseline_repo/` is the local Git repository created by the demo.

## Run an individual audit

Read `approved_commit` in `runs/demo-1/summary.json`. Replace `FULL_SHA` below with
that 40-character value, then enter this as one command:

```bash
python3 auditor.py --repo runs/demo-1/baseline_repo --commit FULL_SHA --snapshot runs/demo-1/combined.cfg --database runs/demo-1/review.sqlite --report runs/demo-1/review.json
```

| Status | Meaning | Exit code |
| --- | --- | --- |
| `NO_DRIFT` | No difference in supported categories | 0 |
| `REVIEW_REQUIRED` | Supported drift; draft awaiting administrator review | 0 |
| `MANUAL_REVIEW` | Baseline or input validation failed | 2 |

Storage failures propagate as process errors. Exit 0 does not mean changes were
approved or applied.

## Validation scope

The six drift scenarios contain eight expected category findings; all eight
were detected and given the expected labels. Two no-drift cases produced zero
false alerts. Five exception cases returned manual review without a proposal.
The harness checks that inputs and the baseline are unchanged, that SQLite has
13 audit rows, and that the synthetic credential marker is absent from reports.
Two separately recorded CLI checks cover exit codes 0 and 2.

These hand-built fixtures do not establish real-world accuracy or time savings.
The project targets of 90% detection/classification and 50% lower manual review
time remain to be evaluated on representative lab cases.

## Limits and next iteration

Use synthetic inputs only. The parser supports a narrow lab grammar and does not
fully validate addresses, VLAN ranges, device syntax, or collection completeness.
It preserves category order, not a full vendor syntax tree. Unknown lines stop
the whole audit, but accepted free-text fields are not a comprehensive secret
filter. The demonstration contains an intentionally fake `DEMO_SECRET` marker.

Next work includes an authorized read-only SSH collector, an approved GitHub
baseline adapter, reviewed external rule files, constrained model explanations,
queue retries and deduplication, encrypted storage, retention, authenticated
approvals, and an isolated executor. The current program does not prove that a
supplied Git commit has organizational approval. Git reads have a 10-second
timeout; retry handling and scheduling are not implemented.
