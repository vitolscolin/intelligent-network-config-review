"""Local-only synthetic audit dashboard. Python standard library + Git."""
import argparse
import json
import os
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from auditor import audit, digest, git, parse
from demo import BASELINE, scenarios

WEB = Path(__file__).parent / 'web'
LABELS = [
    ('No configuration change', 'Approved and observed configurations match.'),
    ('Whitespace only', 'Formatting changes with no supported configuration drift.'),
    ('Empty snapshot', 'Stop safely when the snapshot is empty.'),
    ('Instruction-like banner', 'Reject unsupported banner content; do not interpret instructions.'),
    ('SNMP location', 'Change the location label from Lab_A to Lab_B.'),
    ('VLAN label', 'Rename VLAN 10 from STAFF to USERS.'),
    ('Static route', 'Change the next hop of the static route.'),
    ('ACL rule', 'Replace a permit rule with a deny rule.'),
    ('ACL ordering', 'Reverse permit and deny statements; ordering matters.'),
    ('Combined changes', 'Review SNMP, static route, and high-risk ACL changes together.'),
    ('Unsupported syntax', 'Stop safely on an unsupported routing configuration.'),
    ('Sensitive-line rejection', 'Reject a synthetic credential line and withhold raw content.'),
    ('Missing baseline', 'Stop safely when the baseline commit cannot be resolved.'),
]
CASES = {case[0]: (case[1], *label) for case, label in zip(scenarios(), LABELS)}
UUID = re.compile(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}')


def atomic_json(path, value):
    temporary = path.with_suffix('.tmp')
    with temporary.open('w', encoding='utf-8') as stream:
        json.dump(value, stream, indent=2)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def load_json(path):
    return json.loads(path.read_text(encoding='utf-8'))


def validate_audit(result):
    """Reject malformed stored payloads before returning them to the frontend."""
    if not isinstance(result, dict):
        raise ValueError('Invalid audit')
    for field in ('audit_id', 'collected_at', 'parser', 'device', 'mode', 'status'):
        if not isinstance(result.get(field), str):
            raise ValueError('Missing audit field')
    if not UUID.fullmatch(result['audit_id']):
        raise ValueError('Invalid audit ID')
    datetime.fromisoformat(result['collected_at'])
    if result['device'] != 'lab-router' or result['mode'] != 'local_fixture':
        raise ValueError('Invalid fixture identity')
    if result['status'] not in ('NO_DRIFT', 'REVIEW_REQUIRED', 'MANUAL_REVIEW'):
        raise ValueError('Invalid audit status')
    if not isinstance(result.get('stages'), list) or not all(isinstance(s, str) for s in result['stages']):
        raise ValueError('Invalid stages')
    findings = result.get('findings')
    if not isinstance(findings, list):
        raise ValueError('Invalid findings')
    for finding in findings:
        if not isinstance(finding, dict) or finding.get('category') not in ('snmp', 'vlan', 'route', 'acl'):
            raise ValueError('Invalid finding')
        if finding.get('risk') not in ('low', 'medium', 'high') or not isinstance(finding.get('explanation'), str):
            raise ValueError('Invalid finding')
        if not isinstance(finding.get('evidence'), list) or not finding['evidence'] or not all(isinstance(line, str) for line in finding['evidence']):
            raise ValueError('Invalid evidence')
    if result['status'] == 'REVIEW_REQUIRED':
        proposal = result.get('proposal')
        if not findings or not isinstance(proposal, dict) or proposal.get('status') != 'DRAFT_REQUIRES_ADMIN_REVIEW':
            raise ValueError('Invalid proposal')
        if not isinstance(proposal.get('patch'), list) or not all(isinstance(line, str) for line in proposal['patch']):
            raise ValueError('Invalid patch')
    elif findings or 'proposal' in result:
        raise ValueError('Unexpected findings or proposal')
    if result['status'] == 'MANUAL_REVIEW':
        if not isinstance(result.get('reason'), str):
            raise ValueError('Missing validation reason')
    else:
        highest = next((r for r in ('high', 'medium', 'low') if any(f['risk'] == r for f in findings)), 'none')
        if result.get('highest_risk') != highest:
            raise ValueError('Invalid highest risk')


class Store:
    def __init__(self, root):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.active = None
        # Reconcile saved auditor output, never rerun an audit during startup.
        for folder in self.folders():
            try:
                if not self.read(folder.name).get('audit'):
                    self.recover(folder.name)
            except (OSError, ValueError, sqlite3.Error):
                pass  # The incomplete execution remains visible for inspection.

    def folders(self):
        return [p for p in self.root.iterdir() if p.is_dir() and UUID.fullmatch(p.name)]

    def metadata(self, key):
        folder = self.root / key
        if not folder.is_dir():
            raise FileNotFoundError(key)
        try:
            meta = load_json(folder / 'request.json')
            if not isinstance(meta, dict) or meta.get('scenario') not in CASES or meta.get('audit_id') != key:
                raise ValueError('Invalid request metadata')
            datetime.fromisoformat(meta['created_at'])
            return meta
        except (OSError, ValueError, KeyError, TypeError):
            return {'scenario': None, 'created_at': datetime.fromtimestamp(folder.stat().st_mtime, timezone.utc).isoformat(), 'state': 'INTERRUPTED'}

    def configurations(self, folder, result):
        # Verify exact persisted input hashes; do not reconstruct from today's fixtures.
        approved = (folder / 'baseline_repo' / 'baseline.cfg').read_text()
        observed = (folder / 'snapshot.cfg').read_text()
        if digest(approved) != result.get('baseline_sha256') or digest(observed) != result.get('snapshot_sha256'):
            raise ValueError('Configuration hashes do not match')
        parse(approved)
        parse(observed)
        return {'approved': approved, 'observed': observed}

    def read(self, key):
        folder = self.root / key
        meta = self.metadata(key)
        try:
            record = load_json(folder / 'record.json')
            if not isinstance(record, dict) or record.get('id') != key or record.get('scenario') not in CASES:
                raise ValueError('Invalid execution record')
            validate_audit(record.get('audit'))
            if not isinstance(record.get('scenario_name'), str):
                raise ValueError('Invalid scenario name')
            if record['audit']['status'] != 'MANUAL_REVIEW':
                configs = record.get('configurations')
                if not isinstance(configs, dict):
                    raise ValueError('Missing configurations')
                for side, hash_name in (('approved', 'baseline_sha256'), ('observed', 'snapshot_sha256')):
                    if not isinstance(configs.get(side), str) or digest(configs[side]) != record['audit'].get(hash_name):
                        raise ValueError('Configuration hash mismatch')
                    parse(configs[side])
            elif 'configurations' in record:
                raise ValueError('Raw input in manual review record')
            return record  # Legacy completed records remain compatible.
        except (OSError, ValueError, KeyError, TypeError):
            scenario = meta['scenario']
            state = 'RUNNING' if self.active == key else ('FAILED' if meta.get('state') == 'FAILED' else 'INTERRUPTED')
            return {'id': key, 'scenario': scenario,
                    'scenario_name': CASES[scenario][1] if scenario else 'Unknown interrupted execution',
                    'created_at': meta['created_at'], 'execution_status': state, 'audit': None,
                    'reason': 'No validated execution record is available. Recover saved output, or start a new execution. Existing evidence is preserved.'}

    def history(self):
        records = [self.read(folder.name) for folder in self.folders()]
        return sorted(records, key=lambda r: (r.get('audit') or {}).get('collected_at', r.get('created_at', '')), reverse=True)

    def publish(self, key, result):
        folder = self.root / key
        meta = self.metadata(key)
        validate_audit(result)
        if meta['scenario'] not in CASES or result['audit_id'] != meta.get('audit_id'):
            raise ValueError('Audit does not match journal')
        record = {'id': key, 'scenario': meta['scenario'], 'scenario_name': CASES[meta['scenario']][1],
                  'execution_status': 'COMPLETED', 'created_at': meta['created_at'], 'audit': result}
        if result['status'] != 'MANUAL_REVIEW':
            record['configurations'] = self.configurations(folder, result)
        # Repair missing persistence copies without inserting a second audit.
        with closing(sqlite3.connect(self.root / 'audits.sqlite')) as connection, connection:
            connection.execute('CREATE TABLE IF NOT EXISTS audits (id TEXT PRIMARY KEY, payload TEXT NOT NULL)')
            existing = connection.execute('SELECT payload FROM audits WHERE id=?', (result['audit_id'],)).fetchone()
            if existing and json.loads(existing[0]) != result:
                raise ValueError('Conflicting persisted audit copies')
            connection.execute('INSERT OR IGNORE INTO audits VALUES (?, ?)', (result['audit_id'], json.dumps(result, indent=2)))
        atomic_json(folder / 'audit.json', result)
        atomic_json(folder / 'record.json', record)
        return record

    def recover(self, key):
        if not self.lock.acquire(blocking=False):
            return 409, {'error': 'An audit is running. Wait before recovering saved output.'}
        try:
            record = self.read(key)
            if record.get('audit'):
                return 200, record
            folder = self.root / key
            meta = self.metadata(key)
            if meta['scenario'] is None:
                return 409, {'error': 'Request metadata is missing or damaged. Inspect the local files; start a new scenario without deleting this execution.'}
            try:
                result = load_json(folder / 'audit.json')
                validate_audit(result)
            except (OSError, ValueError, TypeError, KeyError):
                result = None
            database = self.root / 'audits.sqlite'
            if database.exists():
                with closing(sqlite3.connect(database)) as connection, connection:
                    row = connection.execute('SELECT payload FROM audits WHERE id=?', (meta['audit_id'],)).fetchone()
                if row:
                    saved = json.loads(row[0])
                    validate_audit(saved)
                    if result is not None and result != saved:
                        raise ValueError('Conflicting persisted output')
                    result = saved
            if result is None:
                return 409, {'error': 'No complete auditor output was saved. Start a new execution; the interrupted record and its files will remain available.'}
            return 200, self.publish(key, result)
        except (OSError, ValueError, sqlite3.Error, KeyError, TypeError):
            return 409, {'error': 'Saved output could not be safely recovered. Inspect local file permissions, database health, and configuration hashes. Existing evidence was preserved.'}
        finally:
            self.lock.release()

    def run(self, scenario, key):
        if not self.lock.acquire(blocking=False):
            return 409, {'error': 'An audit is already running. Wait for it to finish, then refresh history.'}
        folder = self.root / key
        try:
            if folder.exists():
                record = self.read(key)
                if record['scenario'] != scenario:
                    return 409, {'error': 'This request ID belongs to a different or unknown scenario. Start a new audit.'}
                if record.get('audit'):
                    return 200, record
                return 409, {'error': 'This execution is incomplete. Open it in history to recover saved output or start a new execution.'}
            folder.mkdir()
            self.active = key
            meta = {'scenario': scenario, 'audit_id': key,
                    'created_at': datetime.now(timezone.utc).isoformat(), 'state': 'RUNNING'}
            atomic_json(folder / 'request.json', meta)
            repo = folder / 'baseline_repo'
            repo.mkdir()
            git(repo, 'init', '--quiet')
            (repo / 'baseline.cfg').write_text(BASELINE)
            git(repo, 'add', 'baseline.cfg')
            git(repo, '-c', 'user.name=Dashboard Demo', '-c', 'user.email=demo@example.invalid',
                'commit', '--quiet', '-m', 'Synthetic approved baseline')
            commit = git(repo, 'rev-parse', 'HEAD').strip()
            snapshot = folder / 'snapshot.cfg'
            current = CASES[scenario][0]
            snapshot.write_text(current)
            result = audit(repo, '0' * 40 if scenario == 'missing_baseline' else commit,
                           snapshot, self.root / 'audits.sqlite', folder / 'audit.json', audit_id=key)
            return 201, self.publish(key, result)
        except Exception:
            if self.active == key:
                try:
                    meta['state'] = 'FAILED'
                    atomic_json(folder / 'request.json', meta)
                except OSError:
                    pass
            raise
        finally:
            self.active = None
            self.lock.release()


class Handler(BaseHTTPRequestHandler):
    def respond(self, status, data, content_type='application/json', download=False):
        body = json.dumps(data).encode() if content_type == 'application/json' else data
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'self'; style-src 'self'; script-src 'self'; frame-ancestors 'none'")
        if download:
            self.send_header('Content-Disposition', 'attachment; filename="audit-execution.json"')
        self.end_headers()
        self.wfile.write(body)

    def local_request(self):
        allowed = {f'localhost:{self.server.server_port}', f'127.0.0.1:{self.server.server_port}'}
        host = self.headers.get('Host')
        origin = self.headers.get('Origin')
        return host in allowed and (origin is None or origin == 'http://' + host)

    def do_GET(self):
        if not self.local_request():
            return self.respond(403, {'error': 'Use the localhost dashboard URL.'})
        path = urlsplit(self.path).path
        try:
            if path == '/api/scenarios':
                return self.respond(200, [{'id': k, 'name': v[1], 'description': v[2]} for k, v in CASES.items()])
            if path == '/api/runs':
                return self.respond(200, self.server.store.history())
            match = re.fullmatch(r'/api/runs/([^/]+)(/download)?', path)
            if match and UUID.fullmatch(match[1]):
                return self.respond(200, self.server.store.read(match[1]), download=bool(match[2]))
            assets = {'/': ('index.html', 'text/html; charset=utf-8'),
                      '/app.js': ('app.js', 'text/javascript; charset=utf-8'),
                      '/style.css': ('style.css', 'text/css; charset=utf-8')}
            if path in assets:
                name, mime = assets[path]
                return self.respond(200, (WEB / name).read_bytes(), mime)
            self.respond(404, {'error': 'Not found.'})
        except FileNotFoundError:
            self.respond(404, {'error': 'Execution record not found. Refresh history.'})
        except (OSError, ValueError):
            self.respond(500, {'error': 'Cannot read local records. Check the dashboard data directory and server terminal.'})

    def do_POST(self):
        if not self.local_request():
            return self.respond(403, {'error': 'Cross-origin requests are not allowed.'})
        recovery = re.fullmatch(r'/api/runs/([^/]+)/recover', self.path)
        if recovery and UUID.fullmatch(recovery[1]):
            return self.respond(*self.server.store.recover(recovery[1]))
        if self.path != '/api/runs':
            return self.respond(404, {'error': 'Not found.'})
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 1024 or self.headers.get('Content-Type') != 'application/json':
                raise ValueError()
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict) or set(data) != {'scenario', 'request_id'}:
                raise ValueError()
            if not isinstance(data['scenario'], str) or data['scenario'] not in CASES:
                raise ValueError()
            if not isinstance(data['request_id'], str) or not UUID.fullmatch(data['request_id']):
                raise ValueError()
        except (ValueError, TypeError):
            return self.respond(400, {'error': 'Choose an allowlisted synthetic scenario and a valid request ID.'})
        try:
            status, record = self.server.store.run(data['scenario'], data['request_id'])
            self.respond(status, record)
        except Exception:
            # Keep local paths and raw input out of browser errors.
            import traceback
            traceback.print_exc()
            self.respond(500, {'error': 'Execution failed. Check Git installation, free disk space, and directory permissions in the server terminal. Refresh history before starting a new audit.'})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--data-dir', type=Path, default=Path(__file__).parent / 'runs' / 'dashboard')
    args = parser.parse_args()
    server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    server.store = Store(args.data_dir.resolve())
    print(f'Dashboard: http://127.0.0.1:{server.server_port} (synthetic fixtures only)', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
