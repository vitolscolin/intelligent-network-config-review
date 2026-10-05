# Intelligent Network Configuration Review Platform

A local dashboard and command-line prototype for reviewing network configuration
changes. It compares a synthetic snapshot with a Git baseline, classifies drift
using deterministic rules, and saves findings and a draft baseline-update patch
for administrator review.

**Current scope:** synthetic `lab-router` demonstrations only. No equipment is
connected. The application does not approve findings, apply patches, overwrite
baselines, or execute device commands.

## Requirements

- Python **3.10 or later**, available in your terminal.
- Git, available in your terminal.
- A current web browser for the dashboard.

Running the application requires no third-party Python packages, Node.js build,
API keys, or device credentials. Playwright is optional and used only for browser
testing. Verification has been performed on Linux with Python 3.12 and Chrome;
the Windows and macOS setup instructions have not been independently tested.

## Set up and start the dashboard

Clone the repository into a directory of your choice. If you already have a
checkout, open a terminal in that repository instead of cloning again.

```bash
git clone https://github.com/vitolscolin/intelligent-network-config-review.git
cd intelligent-network-config-review
```

**Linux / macOS:**

```bash
python3 --version
git --version
python3 dashboard.py
```

**Windows PowerShell:**

```powershell
py -3 --version
git --version
py -3 dashboard.py
```

Open **http://127.0.0.1:8765**. Leave the terminal running while using the
dashboard. Stop it with **Ctrl+C**. Restart with the same command to reopen
saved history. Commands below use `python3`; on Windows, substitute `py -3`.
Run commands from the repository root unless stated otherwise.

The server binds only to `127.0.0.1`. If port 8765 is occupied:

```bash
python3 dashboard.py --port 8766
```

Then open **http://127.0.0.1:8766**. No configuration file is required.

## Try the review workflow

1. Select **Combined changes**, then **Run audit**.
2. Open the **HIGH / ACL configuration changed** finding for its explanation and
   supporting evidence.
3. Compare approved and observed configurations. Line order, including ACL
   ordering, is preserved. On narrow screens, the panels stack vertically.
4. Select **Review draft response** to inspect the baseline-update patch.
5. Try **No configuration change** and **Unsupported syntax** to see the other
   audit outcomes.
6. Reopen a run from **Audit history**, or select **Download JSON** to export the
   execution currently being reviewed.

The scenario menu includes no change, whitespace only, SNMP location, VLAN label,
static route, ACL rule, ACL ordering, combined changes, and five exception cases:
empty input, instruction-like banner, unsupported syntax, sensitive-line
rejection, and missing baseline.

| Status | What it means | Individual-audit CLI exit code |
| --- | --- | --- |
| `NO_DRIFT` | No differences in the supported configuration categories | 0 |
| `REVIEW_REQUIRED` | Supported drift; a draft awaits administrator review | 0 |
| `MANUAL_REVIEW` | Baseline or input validation failed; risk is not assessed | 2 |

Zero findings in a manual-review case do **not** mean the configuration is safe.
Raw configuration is withheld from those dashboard results. Overview always
shows the latest stored audit, even while an older run is open below it.
Timestamps display in the browser's local timezone. A new workspace starts empty;
counts are derived from stored executions.

**A baseline update and a device restoration are different actions.** Accepting
an observed configuration changes what is considered approved. Restoring an
approved configuration would change the device back through a separate authorized
procedure. This application performs neither action. Exit code 0 indicates a
completed audit, not administrator approval.

## Local storage

Dashboard data defaults to `runs/dashboard/`, which is excluded from Git:

| File or directory | Contents |
| --- | --- |
| `<request-id>/request.json` | Scenario, stable audit ID, start time, and execution state for recovery |
| `<request-id>/audit.json` | Unmodified output from the auditor |
| `<request-id>/record.json` | Downloadable envelope with the audit, scenario metadata, and validated configurations |
| `<request-id>/snapshot.cfg` | Synthetic input for that execution |
| `<request-id>/baseline_repo/` | Local synthetic baseline Git repository |
| `audits.sqlite` | The auditor's persisted audit payloads |

To use a separate workspace, choose a data directory from the terminal:

```bash
python3 dashboard.py --port 8766 --data-dir runs/dashboard-sandbox
```

Use one server per data directory. Restart with the same `--data-dir` to reopen
that workspace. To back up history, stop the server and copy the entire data
directory. Custom locations outside `runs/` are not automatically ignored by Git.

Each new execution writes a request journal before auditing. JSON publications
use a flushed temporary file and atomic rename. Failed or interrupted executions
remain in history with **FAILED** or **INTERRUPTED** status; their audit result is
`null`, risk is unavailable, and the UI shows dashes rather than zero findings.
These execution states are distinct from the auditor's `MANUAL_REVIEW` outcome.

On startup, the dashboard attempts to finish persistence from a saved audit JSON
or SQLite row. You can also open an incomplete run and select **Recover saved
output**. Recovery validates the payload, checks the request's stable audit ID,
and verifies configuration hashes before publishing a completed record. It never
reruns the auditor and never creates a second audit row. Conflicting saved copies
or changed input files stop recovery with an actionable error.

If no complete audit output exists, select **Prepare a new execution**, then
**Run audit**. The original interrupted execution and its files remain in place.
Incomplete records can also be downloaded as JSON for inspection. Older completed
dashboard records remain readable. Older incomplete directories without a request
journal appear as unknown interrupted executions and require local inspection.

SQLite and JSON remain separate writes, not a single transaction. Recovery cannot
repair a damaged database, recover missing input files, or guarantee durability
against every power failure. If the directory cannot be created at all, no record
can be stored; inspect the server terminal and filesystem permissions. Original
evidence under [`evidence/original_run/`](evidence/original_run/) remains unchanged.

Only allowlisted scenario IDs and UUID request IDs are accepted from the browser;
paths and commands are not accepted. The UI disables submission during a run,
the server rejects concurrent executions, and a completed request ID returns its
existing result. A pending ID is retained in the browser session after a network
failure. Host and Origin checks restrict browser requests to the local dashboard.

## Command-line demonstration

Run all 13 fixture scenarios in a **new** directory:

```bash
python3 demo.py --out runs/demo-1
```

Expected summary: `"passed": 13`, `"total": 13`, and `"sqlite_rows": 13`.
Existing output directories are rejected; choose `runs/demo-2` for another run.
Run Python without `-O`, because the demonstration uses assertions.

The output includes `summary.json`, individual JSON reports, the synthetic
snapshots, `audits.sqlite`, and `baseline_repo/`.

For an individual audit, copy `approved_commit` from `runs/demo-1/summary.json`
and replace `FULL_SHA` below with that full 40-character value:

```bash
python3 auditor.py --repo runs/demo-1/baseline_repo --commit FULL_SHA --snapshot runs/demo-1/combined.cfg --database runs/demo-1/review.sqlite --report runs/demo-1/review.json
```

Storage failures propagate as process errors. CLI runs do not automatically
appear in dashboard history.

## Tests

### Backend and fixture checks

```bash
python3 demo.py --out runs/demo-check-1
python3 -m unittest discover -s tests -v
```

The HTTP integration suite exercises all 13 scenarios, compares downloads with
JSON and SQLite output, checks history after a server restart, and verifies input
restrictions, cross-origin rejection, duplicate IDs, and concurrent-run rejection. Failure-injection tests cover interrupted publication,
SQLite-only output, conflicting records, damaged metadata, and hash mismatches.
Tests use temporary storage and do not alter existing dashboard runs.

### Optional browser checks

Create a test environment and install the pinned browser-test dependency and
Chromium. These dependencies are not needed to run the dashboard.

**Linux / macOS:**

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-test.txt
.venv/bin/python -m playwright install chromium
.venv/bin/python tests/run_browser_checks.py
```

**Windows PowerShell:**

```powershell
py -3 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements-test.txt
.venv\Scripts\python.exe -m playwright install chromium
.venv\Scripts\python.exe tests/run_browser_checks.py
```

The harness starts a temporary dashboard on an available localhost port, runs the
normal and recovery browser suites, and stops the server afterward. Every run uses
fresh temporary data. Screenshots and the server log are saved under ignored
`runs/ci-browser-screenshots/` and `runs/ci-browser.log`.

To use installed Chrome/Chromium instead of downloading Chromium, omit the browser
installation command and pass `--chrome` followed by its executable path to the
harness. Quote paths containing spaces. On Linux, Playwright may also require
system libraries; install the dependencies it reports before running the tests.

The suites check empty/loading/error states, combined/no-change/manual-review
flows, evidence and patch equality, JSON downloads, reopening history, mobile
layout, interrupted execution controls, recovery without rerunning, and preserving
old records when starting a new execution. They also verify that a failed history
refresh cannot mislabel an already-saved audit as unconfirmed.

### Continuous integration

[GitHub Actions](https://github.com/vitolscolin/intelligent-network-config-review/actions/workflows/ci.yml)
runs on pushes and pull requests, and can be started manually:

- Fixture and HTTP/recovery tests on Linux, Windows, and macOS using Python 3.10
  and 3.12.
- Chromium dashboard and recovery browser checks on Linux/Python 3.12.
- Synthetic browser screenshots and the server log retained for seven days.

The workflow uses read-only repository permissions, pinned action commits, and
no deployment or device credentials. The matrix tests execution on hosted runners;
it does not verify manual installation or browser behavior on every operating
system. See the linked run results for the status of a particular commit.

## Troubleshooting

| Symptom | What to check |
| --- | --- |
| Python or Git command not found | Install the missing program, ensure it is on PATH, and reopen the terminal. Some systems expose Python as `python` instead. |
| Browser cannot connect | Keep the server terminal open and use the exact printed URL and port. Do not open `web/index.html` directly. |
| Address already in use | Stop your previous instance or choose another `--port`. |
| Audit execution fails | Check the server terminal, Git availability, disk space, directory permissions, and any global Git signing or hook configuration. Refresh history before starting another audit. |
| Expected manual review | Exception scenarios deliberately stop validation; choose a supported scenario to see findings and a draft. |
| Demo directory already exists | Choose a new `--out` directory; preserve existing evidence. |
| History appears empty after restart | Confirm the same `--data-dir` is being used. CLI runs and original evidence are not dashboard history. |
| Browser test fails at the empty-state check | Restart the test server with a new data directory. |
| Playwright reports missing Linux system libraries | Install the system dependencies requested by Playwright, or use a supported installed browser with `--chrome`. |

## Project layout

| Path | Purpose |
| --- | --- |
| `auditor.py` | Parser, ordered comparison, risk rules, draft generation, and persistence |
| `demo.py` | Shared synthetic fixtures and the 13-scenario assertion harness |
| `dashboard.py` | Local HTTP server, allowlisted execution, and history |
| `web/` | HTML, CSS, and JavaScript frontend; no build step |
| `tests/` | HTTP recovery tests and optional Playwright browser checks |
| `.github/workflows/ci.yml` | Cross-platform backend and Linux browser CI |
| `requirements-test.txt` | Pinned optional browser-test dependency |
| `evidence/original_run/` | Original fixture evidence, execution log, and source hashes |
| `runs/` | Ignored local execution data and test artifacts |

## Validation and limitations

The original demonstration passed 13/13 cases: six drift scenarios produced
eight expected category findings, two no-drift cases produced no alerts, and five
exception cases stopped for manual review without a proposal. The harness checks
input and baseline preservation, database row counts, and exclusion of the fake
`DEMO_SECRET` marker from reports. Original CLI evidence covers exit codes 0 and 2.

These fixtures do not establish real-world accuracy or time savings. The targets
of 90% detection/classification and 50% less manual review time remain unmeasured
on representative lab cases.

Use synthetic inputs only. The parser handles a narrow lab grammar, preserves
order within categories rather than a full vendor syntax tree, and does not fully
validate addresses, VLAN ranges, or collection completeness. Unsupported lines
stop the audit; accepted free-text fields are not a comprehensive secret filter.
A supplied baseline commit is not proof of organizational approval.

This is a single-user local prototype without authentication, encrypted storage,
retention management, pagination, or a durable job queue. Progress indicates an
in-flight request and recorded stages, not live stage-by-stage telemetry. Do not
expose the server through a public proxy or tunnel. SSH collection, the GitHub
baseline API, Redis, LangGraph, model inference, and an execution agent are not
integrated. Hosting the source on GitHub does not connect the auditor to its API.

## Suggested next steps

1. **Extend validation coverage.** Exercise manual setup and browser behavior on
   Windows/macOS, and expand malformed-input and failure-injection tests.
2. **Manage growing history.** Add pagination, retention, and a backup/restore
   workflow; consider consolidating storage into one transaction.
3. **Expand parser validation and evaluation.** Add malformed addresses, VLAN
   boundaries, duplicate statements, incomplete snapshots, and more ACL-order
   cases; measure results against independently labeled lab configurations.
4. **Define baseline governance and review records.** Establish how a baseline
   becomes approved and how administrator decisions are recorded, without
   coupling review to device execution.
5. **Add one authorized read-only input adapter.** After parser and data-handling
   requirements are established, introduce a constrained lab collector with
   explicit provenance and credential handling. Keep device writes out of scope.

These are proposed follow-up milestones, not implemented capabilities.
