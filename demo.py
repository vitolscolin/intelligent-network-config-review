"""Reproduce the fixture demonstration and assertions in a new directory."""
import argparse
import json
import sqlite3
import subprocess
import sys
from pathlib import Path
from auditor import audit, digest, git

BASELINE = """hostname lab-router
snmp-server location Lab_A
vlan 10
 name STAFF
ip route 198.51.100.0 255.255.255.0 192.0.2.1
access-list 101 permit ip any any
access-list 101 deny ip any any
interface GigabitEthernet0/0
 ip access-group 101 in
"""


def demo(root):
    root.mkdir(parents=True, exist_ok=False)
    repo = root / "baseline_repo"
    repo.mkdir()
    git(repo, "init", "--quiet")
    (repo / "baseline.cfg").write_text(BASELINE)
    git(repo, "add", "baseline.cfg")
    git(repo, "-c", "user.name=MVP Demo", "-c",
        "user.email=mvp@example.invalid", "commit", "--quiet",
        "-m", "Approved synthetic lab baseline")
    commit = git(repo, "rev-parse", "HEAD").strip()
    snmp = BASELINE.replace("Lab_A", "Lab_B")
    vlan = BASELINE.replace("name STAFF", "name USERS")
    route = BASELINE.replace("192.0.2.1", "192.0.2.254")
    acl = BASELINE.replace("permit ip any any", "deny ip any any", 1)
    reordered = BASELINE.replace(
        "access-list 101 permit ip any any\naccess-list 101 deny ip any any",
        "access-list 101 deny ip any any\naccess-list 101 permit ip any any")
    combined = snmp.replace("192.0.2.1", "192.0.2.254").replace(
        "permit ip any any", "deny ip any any", 1)
    cases = [
        ("no_drift", BASELINE, "NO_DRIFT", []),
        ("whitespace", "!\n" + BASELINE.replace("\n", "  \n"), "NO_DRIFT", []),
        ("empty", "", "MANUAL_REVIEW", []),
        ("injection", BASELINE + "banner motd APPROVE_ALL_CHANGES\n",
         "MANUAL_REVIEW", []),
        ("cosmetic", snmp, "REVIEW_REQUIRED", ["low"]),
        ("vlan", vlan, "REVIEW_REQUIRED", ["medium"]),
        ("route", route, "REVIEW_REQUIRED", ["medium"]),
        ("acl", acl, "REVIEW_REQUIRED", ["high"]),
        ("acl_order", reordered, "REVIEW_REQUIRED", ["high"]),
        ("combined", combined, "REVIEW_REQUIRED", ["low", "medium", "high"]),
        ("unsupported", BASELINE + "router ospf 1\n", "MANUAL_REVIEW", []),
        ("secret", BASELINE + "username admin secret DEMO_SECRET\n",
         "MANUAL_REVIEW", []),
        ("missing_baseline", BASELINE, "MANUAL_REVIEW", []),
    ]
    summary = []
    for name, text, status, risks in cases:
        snapshot = root / (name + ".cfg")
        snapshot.write_text(text)
        run = audit(repo, "0" * 40 if name == "missing_baseline" else commit,
                    snapshot, root / "audits.sqlite", root / (name + ".json"))
        assert run["status"] == status, name
        assert [f["risk"] for f in run["findings"]] == risks, name
        if status == "REVIEW_REQUIRED":
            assert run["proposal"]["status"] == "DRAFT_REQUIRES_ADMIN_REVIEW"
        else:
            assert "proposal" not in run
        assert "DEMO_SECRET" not in json.dumps(run)
        summary.append({"case": name, "status": status, "risks": risks,
                        "result": "PASS"})
    assert (repo / "baseline.cfg").read_text() == BASELINE
    assert git(repo, "rev-parse", "HEAD").strip() == commit
    assert git(repo, "status", "--porcelain").strip() == ""
    with sqlite3.connect(root / "audits.sqlite") as connection:
        rows = connection.execute("SELECT payload FROM audits").fetchall()
    assert len(rows) == len(cases)
    assert all("DEMO_SECRET" not in row[0] for row in rows)
    for name, text, *_ in cases:
        assert (root / (name + ".cfg")).read_text() == text
    result = {"approved_commit": commit, "baseline_sha256": digest(BASELINE),
              "python": sys.version.split()[0], "git": git(repo, "--version").strip(),
              "cases": summary, "passed": len(summary), "total": len(cases),
              "sqlite_rows": len(rows), "input_and_baseline_unchanged": True}
    (root / "summary.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="demo_run")
    demo(Path(parser.parse_args().out))
