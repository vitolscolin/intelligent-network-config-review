# Original execution evidence

These files were captured from the local synthetic demonstration on
2026-10-05 UTC (October 4 in America/Kentucky/Monticello).

- `summary.json`: 13/13 passing scenarios and baseline provenance.
- `execution.log`: captured demonstration output.
- `combined.json`: three findings and the exact proposed baseline patch.
- `cli_checks.json`: separately captured CLI exit codes and output.
- Other JSON files: individual reviews; `cli_` reports belong to the CLI checks.
- CFG files: synthetic inputs and the original baseline text.
- `source_sha256.json`: hashes of the exact source files used for this run.

The baseline commit `dccbd1138abd164d3e28d346ca6beb4486339419` belongs to the
original local fixture repository, not this GitHub source repository. Its text
is preserved in `baseline.cfg`; its SHA-256 matches `summary.json`. The original
nested Git repository and SQLite databases are deliberately not tracked here.
Run the demonstration to regenerate the baseline repository and audit database.
New audit IDs, timestamps, and baseline commit hashes can differ on each run.
Only synthetic test data is included. `DEMO_SECRET` is a fake rejection-test marker.
