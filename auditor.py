"""Local network review MVP. Python 3.10+ and Git; no device writes."""
import argparse
import difflib
import hashlib
import json
import re
import sqlite3
from contextlib import closing
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path

PARSER = "lab-subset-1.0"
RULES = {
    "snmp": ("low", "Location metadata changed; no forwarding rule changed."),
    "vlan": ("medium", "VLAN membership availability or its label may change."),
    "route": ("medium", "A static forwarding path changed; check reachability."),
    "acl": ("high", "An ACL rule or binding changed; traffic access may change."),
}


def git(repo, *args):
    return subprocess.check_output(
        ["git", "-C", str(repo), *args], text=True,
        stderr=subprocess.PIPE, timeout=10)


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


def parse(text):
    # A strict fixture grammar, not a general Cisco configuration parser.
    lines = [line.strip() for line in text.splitlines()
             if line.strip() and line.strip() != "!"]
    if not lines or lines[0] != "hostname lab-router":
        raise ValueError("Expected lab-router snapshot header")
    if lines.count("hostname lab-router") != 1:
        raise ValueError("Ambiguous device identity")
    groups = {key: [] for key in RULES}
    context = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line == "!":
            continue
        if re.fullmatch(r"hostname lab-router", line):
            context = None
        elif re.fullmatch(r"snmp-server location [A-Za-z0-9 _-]+", line):
            groups["snmp"].append(line)
            context = None
        elif re.fullmatch(r"vlan [0-9]{1,4}", line):
            groups["vlan"].append(line)
            context = "vlan"
        elif context == "vlan" and re.fullmatch(
                r"name [A-Za-z0-9_-]+", line):
            groups["vlan"].append(line)
        elif re.fullmatch(r"interface GigabitEthernet0/[0-9]+", line):
            groups["acl"].append(line)
            context = "interface"
        elif context == "interface" and re.fullmatch(
                r"ip access-group [0-9]+ in", line):
            groups["acl"].append(line)
        elif re.fullmatch(
                r"ip route (?:[0-9.]+ ){2}[0-9.]+", line):
            groups["route"].append(line)
            context = None
        elif re.fullmatch(
                r"access-list [0-9]+ (?:permit|deny) ip any any", line):
            groups["acl"].append(line)
            context = None
        else:
            # Do not echo unknown lines: they may contain secrets or banners.
            raise ValueError("Unsupported input; raw line withheld")
    if len(groups["snmp"]) > 1:
        raise ValueError("Ambiguous repeated location setting")
    return groups


def diff(before, after):
    return list(difflib.unified_diff(
        before, after, fromfile="approved", tofile="observed", lineterm=""))


def audit(repo, commit, snapshot, database, report, *, audit_id=None):
    run = {"audit_id": audit_id or str(uuid.uuid4()),
           "collected_at": datetime.now(timezone.utc).isoformat(),
           "parser": PARSER, "device": "lab-router",
           "mode": "local_fixture", "stages": [], "findings": []}
    def stage(name):
        run["stages"].append(name)
    try:
        stage("ingestion")
        if not re.fullmatch(r"[0-9a-f]{40}", commit):
            raise ValueError("A full approved commit SHA is required")
        resolved = git(repo, "rev-parse", commit + "^{commit}").strip()
        baseline = git(repo, "show", resolved + ":baseline.cfg")
        current = Path(snapshot).read_text()
        run.update(baseline_commit=resolved,
                   baseline_sha256=digest(baseline),
                   snapshot_sha256=digest(current))
        before, after = parse(baseline), parse(current)
        stage("drift_analysis")
        changed = [key for key in RULES if before[key] != after[key]]
        if changed:
            stage("impact_assessment")
            for key in changed:
                risk, reason = RULES[key]
                run["findings"].append({"category": key, "risk": risk,
                    "explanation": reason,
                    "evidence": diff(before[key], after[key])})
            stage("remediation_planning")
            run["proposal"] = {
                "type": "baseline_update_patch",
                "status": "DRAFT_REQUIRES_ADMIN_REVIEW",
                "patch": diff(baseline.splitlines(), current.splitlines()),
                "instruction": "Approve only if observed state is intended. "
                    "Otherwise restore approved settings through a separate "
                    "authorized procedure. No change is executed here."}
            run["status"] = "REVIEW_REQUIRED"
        else:
            run["status"] = "NO_DRIFT"
        stage("supervisor_review")
        assert all(f["evidence"] for f in run["findings"])
        run["highest_risk"] = next((r for r in ("high", "medium", "low")
            if any(f["risk"] == r for f in run["findings"])), "none")
    except (ValueError, OSError, subprocess.SubprocessError):
        run["status"] = "MANUAL_REVIEW"
        run["reason"] = "Baseline, input, or parser validation failed; " \
                        "raw content withheld. Inspect locally."
        run.pop("proposal", None)
    stage("audit_persistence")
    # Only supported demo syntax reaches storage; no raw config is persisted.
    payload = json.dumps(run, indent=2)
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute("CREATE TABLE IF NOT EXISTS audits "
                           "(id TEXT PRIMARY KEY, payload TEXT NOT NULL)")
        connection.execute("INSERT INTO audits VALUES (?, ?)",
                           (run["audit_id"], payload))
    Path(report).write_text(payload + "\n")
    print(json.dumps({"audit_id": run["audit_id"], "status": run["status"],
                      "findings": len(run["findings"])}))
    return run


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    for name in ("repo", "commit", "snapshot", "database", "report"):
        cli.add_argument("--" + name, required=True)
    args = cli.parse_args()
    result = audit(args.repo, args.commit, args.snapshot,
                   args.database, args.report)
    raise SystemExit(2 if result["status"] == "MANUAL_REVIEW" else 0)
