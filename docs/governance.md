# Local baseline governance and review policy

## Authority and scope

This is a synthetic, single-user demonstration of a governance workflow. The
operator enters a name and reason for each action. Those names are **self-declared**:
there is no authentication, role verification, signature, enforced separation of
duties, or proof of organizational authority. One operator can fill both proposer
and decision-maker fields. Do not treat this ledger as a production approval system.

All decisions concern `lab-router` fixture data. The application can record
approval of a local reference version and use it for a later comparison. It cannot
apply a patch, overwrite previous baseline files, restore a device, create an
execution job, or connect to network equipment. A Git commit existing is not proof
that anyone approved it.

## Baseline lifecycle

| Current state | Action | Result |
| --- | --- | --- |
| No candidate | Propose | Immutable candidate in `PENDING` |
| `PENDING` | Approve | `APPROVED`, eligible for explicit selection in new audits |
| `PENDING` | Reject | Terminal `REJECTED` candidate, retained for history |
| `APPROVED` | Retire | Terminal `RETIRED` version, unavailable to new audits |
| `REJECTED` or `RETIRED` | Any further lifecycle decision | Rejected; propose a new version instead |

A candidate has a UUID, device identity, exact configuration text, SHA-256 digest,
source provenance, and a proposal event. Its content never changes. Approval
requires a nonblank decision-maker name and reason, the exact candidate digest,
and the current revision. The UI shows the full configuration and history before
the decision form. Version IDs distinguish candidates even when their content is
identical; there is no implicit "latest approved" or automatically active version.

There are two candidate sources:

- **Built-in fixture:** the synthetic reference defined in `demo.py`.
- **Accepted observation:** the exact observed configuration in a completed,
  validated audit whose latest review is `ACCEPT_OBSERVED`. The candidate captures
  that audit's ID and digest, the acceptance event ID, and the audit's source
  baseline commit. The source baseline commit does not represent a Git commit of
  the observed candidate text; the candidate's own content is bound by SHA-256.

An observation's captured acceptance must still be the latest decision when its
candidate is approved. If another decision supersedes it, record the appropriate
review and propose a new candidate. After a candidate is approved, later audit
reviews do not automatically retire it; retirement is a separate explicit action.

## Applying governance to an audit

The baseline selector defaults to **Fixture reference · not governed**. This
preserves the original 13 demonstration scenarios without inventing an approval.
Older executions are labeled as having no established governance approval.
The auditor's legacy `approved` diff label alone never establishes approval.

For a governed audit, choose a currently approved registry version. The backend
checks its state and freezes its approval event, version ID, configuration digest,
and selection timestamp into the execution journal. The auditor compares the
scenario's observed fixture with that exact version, saved in a new per-run Git
repository. Consequently, a scenario's result may differ from its original demo
expectation. For example, combined changes become `NO_DRIFT` when compared with an
approved version containing that same observed configuration.

Retirement prevents future selections. It does not cancel a run that already
captured its approval or rewrite historical results. Recovery uses the original
journal snapshot, not the registry's current state. Request IDs also bind the
chosen baseline so retrying an audit cannot silently substitute another version.
The UI retains an unavailable selection until the operator explicitly chooses a
replacement; it does not silently fall back to an ungoverned reference.

## Audit review decisions

Review decisions cover the **entire audit**, not individual findings. They are
stored separately from auditor output; risk labels, `NO_DRIFT` / `REVIEW_REQUIRED`
/ `MANUAL_REVIEW` statuses, and draft patches remain unchanged.

| Decision | Allowed audit status | Meaning |
| --- | --- | --- |
| `ACCEPT_OBSERVED` | `REVIEW_REQUIRED` | Observation is intended; makes it eligible for a separate baseline proposal |
| `REJECT_CHANGE` | `REVIEW_REQUIRED` | Observation is unwanted; any restoration requires a separate authorized procedure |
| `ACKNOWLEDGE` | `NO_DRIFT` | Acknowledges the supported-category comparison, not overall device security |
| `INVESTIGATE` | Any completed audit | More evidence or investigation is needed |
| `DEFER` | Any completed audit | Decision deferred with a recorded reason |

An incomplete execution cannot receive a review. A `MANUAL_REVIEW` result can only
be investigated or deferred; it cannot be accepted as a baseline candidate.

Each review event captures the decision, self-declared actor, reason, server UTC
time, event ID, revision, audit ID and canonical JSON digest, baseline and snapshot
digests where available, baseline Git commit, and the approval snapshot where
available. Subsequent decisions explicitly reference the event they supersede.
Earlier events remain available; there is no edit or deletion endpoint. A changed
audit digest blocks further reviews rather than silently reattaching an old decision
to new evidence.

## Persistence and concurrency

`governance.sqlite`, inside the dashboard's ignored data directory, contains
immutable candidate records and an ordered event ledger. Candidate creation and
its proposal event commit in one transaction. Database triggers reject updates
and deletes to these tables. The original `audit.json`, `record.json`, input
snapshots, per-run Git baseline, and auditor database are not changed by review or
approval actions.

Writes are serialized with SQLite transactions. Every decision includes an expected
revision; stale and conflicting decisions return HTTP 409. Request IDs prevent
duplicate writes, including a response lost after commit. Reusing an ID for a
different command is rejected. Retrying a proposal also rechecks source eligibility;
a later superseding review may require a fresh proposal rather than a replay.
A failed ledger write rolls back rather than leaving a candidate with no event.

These measures protect ordinary application operations. They are **not tamper-proof**:
a person controlling the machine can replace the database, remove triggers, or
edit files. There is no signed audit trail, encryption, retention enforcement,
external identity provider, or organizational approval policy engine.

## Exports and API

- **Download JSON** exports the unchanged execution envelope.
- **Download review record** exports the audit digest, scope, approval snapshot,
  allowed actions, evidence-match flag, and ordered administrator decisions.
- **Download registry** exports all immutable versions, current derived states,
  and lifecycle events, including rejected and retired versions.

All data stays local. Back up the entire data directory with the server stopped,
including `governance.sqlite`. Notes can contain sensitive information typed by an
operator; use synthetic identities and references for demonstrations and keep the
runtime directory out of Git.

| Method and path | Purpose |
| --- | --- |
| `GET /api/baselines` | List candidate versions and lifecycle history |
| `POST /api/baselines` | Propose the fixture or an accepted saved observation |
| `POST /api/baselines/{id}/decisions` | Approve, reject, or retire with revision and digest checks |
| `GET /api/baselines/download` | Export the registry |
| `POST /api/runs` | Optional `baseline_id` selects an approved version; omission/null preserves fixture mode |
| `GET /api/runs/{id}/reviews` | Read the review context and ordered decision history |
| `POST /api/runs/{id}/reviews` | Append a decision with revision and evidence-digest checks |
| `GET /api/runs/{id}/reviews/download` | Export the review record |

Governance POST bodies accept only the documented fields used by the UI. All
require `request_id`, `actor`, and `reason`. Proposals add `source` (`fixture` or a
saved run UUID); baseline decisions add `action`, `expected_revision`, and
`expected_sha256`; audit reviews add `action`, `expected_revision`, and
`expected_audit_sha256`. The server derives configurations and provenance. Browser
clients cannot supply configuration text, timestamps, file paths, or shell commands.
The existing loopback binding and Host/Origin checks apply to all these routes.
