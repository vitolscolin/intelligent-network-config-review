"""Local baseline and review ledger; records decisions but never executes changes."""
from contextlib import closing, contextmanager
from datetime import datetime, timezone
import hashlib
import json
import re
import sqlite3

from auditor import digest, parse

UUID = re.compile(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}')
REVIEW_ACTIONS = {
    'REVIEW_REQUIRED': ('ACCEPT_OBSERVED', 'REJECT_CHANGE', 'INVESTIGATE', 'DEFER'),
    'NO_DRIFT': ('ACKNOWLEDGE', 'INVESTIGATE', 'DEFER'),
    'MANUAL_REVIEW': ('INVESTIGATE', 'DEFER'),
}


class GovernanceError(ValueError):
    def __init__(self, message, status=409):
        super().__init__(message)
        self.status = status


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()).hexdigest()


def identity(data):
    if not isinstance(data.get('request_id'), str) or not UUID.fullmatch(data['request_id']):
        raise GovernanceError('A UUID request ID is required.', 400)
    for key, limit in (('actor', 100), ('reason', 2000)):
        value = data.get(key)
        if not isinstance(value, str) or not value.strip() or len(value) > limit:
            raise GovernanceError(f'{key.title()} is required (maximum {limit} characters).', 400)
        if any(ord(char) < 32 and char not in '\n\t' for char in value):
            raise GovernanceError('Control characters are not allowed.', 400)
    return data['actor'].strip(), data['reason'].strip()


def revision(data):
    value = data.get('expected_revision')
    if type(value) is not int or value < 0:
        raise GovernanceError('A nonnegative expected revision is required.', 400)
    return value


class Governance:
    def __init__(self, root):
        self.database = root / 'governance.sqlite'
        with self.connection() as connection:
            connection.execute('CREATE TABLE IF NOT EXISTS baselines (id TEXT PRIMARY KEY, payload TEXT NOT NULL)')
            connection.execute('''CREATE TABLE IF NOT EXISTS events (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT UNIQUE NOT NULL,
                subject TEXT NOT NULL, request_hash TEXT NOT NULL, payload TEXT NOT NULL)''')
            connection.execute('CREATE INDEX IF NOT EXISTS events_subject ON events(subject, sequence)')
            for table in ('baselines', 'events'):
                for operation in ('UPDATE', 'DELETE'):
                    connection.execute(f'''CREATE TRIGGER IF NOT EXISTS {table}_no_{operation.lower()}
                        BEFORE {operation} ON {table} BEGIN
                        SELECT RAISE(ABORT, 'Governance records are append-only'); END''')

    @contextmanager
    def connection(self):
        with closing(sqlite3.connect(self.database, timeout=10)) as connection, connection:
            connection.execute('BEGIN IMMEDIATE')
            yield connection

    def events(self, connection, subject):
        return [json.loads(row[0]) for row in connection.execute(
            'SELECT payload FROM events WHERE subject=? ORDER BY sequence', (subject,))]

    def baseline(self, connection, key):
        row = connection.execute('SELECT payload FROM baselines WHERE id=?', (key,)).fetchone()
        if not row:
            raise GovernanceError('Baseline candidate not found.', 404)
        candidate = json.loads(row[0])
        events = self.events(connection, 'baseline:' + key)
        if digest(candidate['configuration']) != candidate['sha256']:
            raise GovernanceError('Stored baseline hash mismatch; inspect the local ledger.')
        parse(candidate['configuration'])
        state = {'PROPOSE': 'PENDING', 'APPROVE': 'APPROVED', 'REJECT': 'REJECTED', 'RETIRE': 'RETIRED'}[events[-1]['action']]
        return {**candidate, 'status': state, 'revision': len(events) - 1, 'events': events}

    def list_baselines(self):
        with self.connection() as connection:
            keys = [row[0] for row in connection.execute('SELECT id FROM baselines ORDER BY rowid DESC')]
            return [self.baseline(connection, key) for key in keys]

    def get_baseline(self, key):
        with self.connection() as connection:
            return self.baseline(connection, key)

    def replay(self, connection, request):
        row = connection.execute('SELECT request_hash, payload FROM events WHERE event_id=?', (request['request_id'],)).fetchone()
        if row:
            if row[0] != fingerprint(request):
                raise GovernanceError('This request ID was already used for a different decision.')
            return json.loads(row[1])
        return None

    def append(self, connection, subject, request, payload):
        event = {**payload, 'event_id': request['request_id'],
                 'recorded_at': datetime.now(timezone.utc).isoformat(),
                 'identity_assurance': 'SELF_DECLARED_LOCAL_USER'}
        connection.execute('INSERT INTO events(event_id, subject, request_hash, payload) VALUES (?, ?, ?, ?)',
                           (event['event_id'], subject, fingerprint(request), json.dumps(event)))
        return event

    def require_acceptance(self, connection, source):
        if source['kind'] == 'OBSERVED_AUDIT':
            events = self.events(connection, 'audit:' + source['audit_id'])
            if (not events or events[-1]['action'] != 'ACCEPT_OBSERVED'
                    or events[-1]['event_id'] != source['review_event_id']
                    or events[-1]['audit_sha256'] != source['audit_sha256']):
                raise GovernanceError('The source acceptance decision changed. Review the audit and propose a new candidate.')

    def propose(self, data, configuration, source):
        actor, reason = identity(data)
        parse(configuration)
        # Derive immutable provenance and content on the server, never from a browser path.
        request = {'kind': 'PROPOSE', **data, 'source': source, 'sha256': digest(configuration)}
        key = data['request_id']
        with self.connection() as connection:
            if self.replay(connection, request):
                return self.baseline(connection, key)
            self.require_acceptance(connection, source)
            candidate = {'baseline_id': key, 'device': 'lab-router', 'mode': 'local_fixture',
                         'configuration': configuration, 'sha256': digest(configuration), 'source': source}
            connection.execute('INSERT INTO baselines VALUES (?, ?)', (key, json.dumps(candidate)))
            self.append(connection, 'baseline:' + key, request,
                        {'action': 'PROPOSE', 'actor': actor, 'reason': reason, 'revision': 0,
                         'baseline_id': key, 'sha256': candidate['sha256']})
            return self.baseline(connection, key)

    def decide_baseline(self, key, data):
        actor, reason = identity(data)
        expected = revision(data)
        if data.get('action') not in ('APPROVE', 'REJECT', 'RETIRE'):
            raise GovernanceError('Choose approve, reject, or retire.', 400)
        request = {'kind': 'BASELINE_DECISION', 'baseline_id': key, **data}
        with self.connection() as connection:
            replay = self.replay(connection, request)
            if replay:
                return replay
            candidate = self.baseline(connection, key)
            if candidate['revision'] != expected:
                raise GovernanceError('This baseline changed since it was opened. Refresh and review its latest history.')
            allowed = {'PENDING': ('APPROVE', 'REJECT'), 'APPROVED': ('RETIRE',)}
            if data['action'] not in allowed.get(candidate['status'], ()):
                raise GovernanceError('This decision is not valid for the current baseline state.')
            if data.get('expected_sha256') != candidate['sha256']:
                raise GovernanceError('Baseline content does not match the version you reviewed. Refresh before deciding.')
            if data['action'] == 'APPROVE':
                self.require_acceptance(connection, candidate['source'])
            return self.append(connection, 'baseline:' + key, request,
                               {'action': data['action'], 'actor': actor, 'reason': reason,
                                'revision': expected + 1, 'baseline_id': key, 'sha256': candidate['sha256'],
                                'previous_event_id': candidate['events'][-1]['event_id']})

    def approved(self, key):
        with self.connection() as connection:
            candidate = self.baseline(connection, key)
            if candidate['status'] != 'APPROVED':
                raise GovernanceError('Only currently approved baselines can be selected for a new audit.')
            return candidate['configuration'], {
                'mode': 'GOVERNED', 'baseline_id': key, 'sha256': candidate['sha256'],
                'approval_event': candidate['events'][-1],
                'selected_at': datetime.now(timezone.utc).isoformat(),
                'identity_assurance': 'SELF_DECLARED_LOCAL_USER'}

    def review_history(self, record):
        audit = record.get('audit')
        if not audit:
            raise GovernanceError('Recover a complete audit before recording administrator decisions.')
        with self.connection() as connection:
            events = self.events(connection, 'audit:' + audit['audit_id'])
        current_hash = fingerprint(audit)
        return {'audit_id': audit['audit_id'], 'audit_sha256': current_hash,
                'revision': len(events), 'events': events,
                'evidence_matches': all(event['audit_sha256'] == current_hash for event in events),
                'allowed_actions': list(REVIEW_ACTIONS[audit['status']]),
                'baseline_governance': record.get('baseline_governance', {'mode': 'LEGACY_UNVERIFIED'}),
                'scope': 'ENTIRE_AUDIT', 'identity_assurance': 'SELF_DECLARED_LOCAL_USER'}

    def review(self, record, data):
        actor, reason = identity(data)
        expected = revision(data)
        audit = record.get('audit')
        if not audit:
            raise GovernanceError('Incomplete executions cannot be reviewed.')
        if data.get('action') not in REVIEW_ACTIONS[audit['status']]:
            raise GovernanceError('This decision is not valid for the audit outcome.', 400)
        audit_hash = fingerprint(audit)
        if data.get('expected_audit_sha256') != audit_hash:
            raise GovernanceError('The audit evidence differs from the version you reviewed. Reopen the audit.')
        request = {'kind': 'AUDIT_REVIEW', 'audit_id': audit['audit_id'], **data}
        with self.connection() as connection:
            replay = self.replay(connection, request)
            if replay:
                return replay
            events = self.events(connection, 'audit:' + audit['audit_id'])
            if len(events) != expected:
                raise GovernanceError('Another decision was recorded. Refresh the decision history before adding a new one.')
            if any(event['audit_sha256'] != audit_hash for event in events):
                raise GovernanceError('Saved review history refers to different evidence. Inspect locally before proceeding.')
            return self.append(connection, 'audit:' + audit['audit_id'], request,
                               {'action': data['action'], 'actor': actor, 'reason': reason,
                                'revision': expected + 1, 'scope': 'ENTIRE_AUDIT',
                                'audit_id': audit['audit_id'], 'audit_sha256': audit_hash,
                                'audit_status': audit['status'], 'baseline_commit': audit.get('baseline_commit'),
                                'baseline_sha256': audit.get('baseline_sha256'),
                                'snapshot_sha256': audit.get('snapshot_sha256'),
                                'baseline_governance': record.get('baseline_governance', {'mode': 'LEGACY_UNVERIFIED'}),
                                'supersedes_event_id': events[-1]['event_id'] if events else None})
