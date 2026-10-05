"""Integration checks against a real local HTTP server and the existing auditor."""
import json
from pathlib import Path
import sqlite3
from contextlib import closing
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import uuid
from unittest.mock import patch

import dashboard

from dashboard import Handler, Store, ThreadingHTTPServer
from demo import scenarios


class DashboardTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.start()

    def start(self):
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.store = Store(self.root)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f'http://127.0.0.1:{self.server.server_port}'

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def tearDown(self):
        self.stop()
        self.tmp.cleanup()

    def request(self, path, payload=None, headers=None):
        headers = headers or {}
        if payload is not None:
            headers['Content-Type'] = 'application/json'
        req = Request(self.url + path, data=None if payload is None else json.dumps(payload).encode(), headers=headers)
        try:
            response = urlopen(req)
        except HTTPError as exc:
            response = exc
        with response:
            return response.status, json.loads(response.read())

    def run_case(self, name, key=None):
        return self.request('/api/runs', {'scenario': name, 'request_id': key or str(uuid.uuid4())})


class DashboardTests(DashboardTestCase):
    def test_fixtures_download_and_restart(self):
        self.assertEqual(self.request('/api/runs'), (200, []))
        self.assertEqual(len(self.request('/api/scenarios')[1]), 13)
        for name, _, status, risks in scenarios():
            code, record = self.run_case(name)
            self.assertEqual(code, 201)
            run = record['audit']
            self.assertEqual(run['status'], status)
            self.assertEqual([f['risk'] for f in run['findings']], risks)
            stored = json.loads((self.root / record['id'] / 'audit.json').read_text())
            self.assertEqual(run, stored)
            self.assertEqual(self.request('/api/runs/' + record['id'] + '/download')[1], record)
            self.assertNotIn('DEMO_SECRET', json.dumps(record))
            if status == 'MANUAL_REVIEW':
                self.assertNotIn('configurations', record)
                self.assertNotIn('proposal', run)
            if name == 'acl_order':
                self.assertLess(record['configurations']['observed'].index('deny'), record['configurations']['observed'].index('permit'))
        before = self.request('/api/runs')[1]
        with closing(sqlite3.connect(self.root / 'audits.sqlite')) as conn, conn:
            saved = {row[0]: json.loads(row[1]) for row in conn.execute('SELECT id,payload FROM audits')}
        self.assertEqual(len(saved), 13)
        for record in before:
            self.assertEqual(record['audit'], saved[record['audit']['audit_id']])
        self.stop()
        self.start()
        self.assertEqual(self.request('/api/runs')[1], before)

    def test_allowlist_origin_duplicate_and_busy(self):
        self.assertEqual(self.run_case('../auditor.py')[0], 400)
        self.assertEqual(self.request('/api/runs', {'scenario':'combined','request_id':'../../tmp'})[0], 400)
        self.assertEqual(self.request('/api/runs', {'scenario':'combined','request_id':str(uuid.uuid4()),'path':'/tmp'})[0], 400)
        self.assertEqual(self.request('/api/runs', {'scenario':'combined','request_id':str(uuid.uuid4())}, {'Origin':'https://untrusted.invalid'})[0], 403)
        self.assertEqual(self.request('/api/runs', headers={'Host':'untrusted.invalid'})[0], 403)
        key = str(uuid.uuid4())
        first = self.run_case('combined', key)
        second = self.run_case('combined', key)
        self.assertEqual(first[0], 201)
        self.assertEqual(second, (200, first[1]))
        self.assertEqual(self.run_case('no_drift', key)[0], 409)
        self.assertEqual(len(self.request('/api/runs')[1]), 1)
        self.server.store.lock.acquire()
        try:
            self.assertEqual(self.run_case('combined')[0], 409)
        finally:
            self.server.store.lock.release()
        self.assertEqual(self.request('/api/runs/' + str(uuid.uuid4()))[0], 404)

    def test_failed_record_publication_recovers_without_duplicate(self):
        key = str(uuid.uuid4())
        original = dashboard.atomic_json

        def fail_record(path, value):
            if path.name == 'record.json':
                raise OSError('Simulated full disk')
            return original(path, value)

        with patch('dashboard.atomic_json', side_effect=fail_record):
            self.assertEqual(self.run_case('combined', key)[0], 500)
        incomplete = self.request('/api/runs')[1][0]
        self.assertEqual(incomplete['execution_status'], 'FAILED')
        self.assertIsNone(incomplete['audit'])
        self.assertEqual(self.request('/api/runs/' + key + '/download')[1], incomplete)
        with patch('dashboard.audit', side_effect=AssertionError('Recovery must not rerun')):
            code, recovered = self.request('/api/runs/' + key + '/recover', {})
        self.assertEqual(code, 200)
        self.assertEqual(recovered['audit']['audit_id'], key)
        self.assertEqual(recovered['audit']['status'], 'REVIEW_REQUIRED')
        self.assertEqual(self.run_case('combined', key), (200, recovered))
        with closing(sqlite3.connect(self.root / 'audits.sqlite')) as conn, conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM audits').fetchone()[0], 1)

    def test_sqlite_only_output_recovers_after_restart(self):
        key = str(uuid.uuid4())
        original = Path.write_text

        def fail_report(path, text, *args, **kwargs):
            if path.name == 'audit.json':
                raise OSError('Simulated interruption after SQLite commit')
            return original(path, text, *args, **kwargs)

        with patch.object(Path, 'write_text', fail_report):
            self.assertEqual(self.run_case('combined', key)[0], 500)
        self.assertFalse((self.root / key / 'audit.json').exists())
        self.stop()
        with patch('dashboard.audit', side_effect=AssertionError('No audit rerun')):
            self.start()
        record = self.request('/api/runs')[1][0]
        self.assertEqual(record['audit']['audit_id'], key)
        self.assertEqual(load_report := json.loads((self.root / key / 'audit.json').read_text()), record['audit'])
        with closing(sqlite3.connect(self.root / 'audits.sqlite')) as conn, conn:
            self.assertEqual(json.loads(conn.execute('SELECT payload FROM audits').fetchone()[0]), load_report)

    def test_interruption_before_audit_is_visible_and_requires_new_execution(self):
        key = str(uuid.uuid4())
        with patch('dashboard.git', side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.server.store.run('combined', key)
        self.stop()
        self.start()
        record = self.request('/api/runs')[1][0]
        self.assertEqual(record['execution_status'], 'INTERRUPTED')
        self.assertIsNone(record['audit'])
        self.assertEqual(self.request('/api/runs/' + key + '/recover', {})[0], 409)
        self.assertEqual(self.run_case('combined', key)[0], 409)
        self.assertEqual(self.run_case('combined')[0], 201)
        self.assertEqual(len(self.request('/api/runs')[1]), 2)

    def test_corrupt_record_does_not_break_history_and_hash_mismatch_blocks_recovery(self):
        _, record = self.run_case('combined')
        key = record['id']
        folder = self.root / key
        (folder / 'record.json').write_text('{broken json')
        broken = self.request('/api/runs')[1][0]
        self.assertIsNone(broken['audit'])
        (folder / 'snapshot.cfg').write_text('hostname lab-router\n')
        self.assertEqual(self.request('/api/runs/' + key + '/recover', {})[0], 409)
        self.assertEqual((folder / 'record.json').read_text(), '{broken json')
        self.assertEqual(self.run_case('no_drift')[0], 201)
        self.assertEqual(len(self.request('/api/runs')[1]), 2)

    def test_recovery_from_json_and_legacy_compatibility(self):
        _, record = self.run_case('no_drift')
        folder = self.root / record['id']
        (folder / 'record.json').unlink()
        with closing(sqlite3.connect(self.root / 'audits.sqlite')) as conn, conn:
            conn.execute('DELETE FROM audits')
        self.assertEqual(self.request('/api/runs/' + record['id'] + '/recover', {}), (200, record))
        (folder / 'request.json').unlink()
        self.assertEqual(self.request('/api/runs/' + record['id']), (200, record))

    def test_conflicting_copies_and_invalid_metadata_remain_visible(self):
        _, record = self.run_case('combined')
        folder = self.root / record['id']
        (folder / 'record.json').unlink()
        conflicting = dict(record['audit'], parser='different-parser')
        (folder / 'audit.json').write_text(json.dumps(conflicting))
        self.assertEqual(self.request('/api/runs/' + record['id'] + '/recover', {})[0], 409)
        (folder / 'request.json').write_text('[]')
        self.assertEqual(self.request('/api/runs')[0], 200)
        self.assertEqual(self.request('/api/runs/' + record['id'] + '/recover', {})[0], 409)
        self.assertEqual(self.request('/api/runs/' + record['id'] + '/recover', {}, {'Origin':'https://untrusted.invalid'})[0], 403)

    def test_connections_close_after_success_failure_and_recovery(self):
        # Keep references alive so garbage collection cannot hide leaked handles.
        connect = sqlite3.connect
        connections = []

        def tracked_connect(*args, **kwargs):
            connection = connect(*args, **kwargs, check_same_thread=False)
            connections.append(connection)
            return connection

        original = dashboard.atomic_json

        def fail_record(path, value):
            if path.name == 'record.json':
                raise OSError('Simulated publication failure')
            return original(path, value)

        try:
            with patch('dashboard.sqlite3.connect', side_effect=tracked_connect):
                self.assertEqual(self.run_case('no_drift')[0], 201)
                key = str(uuid.uuid4())
                with patch('dashboard.atomic_json', side_effect=fail_record):
                    self.assertEqual(self.run_case('combined', key)[0], 500)
                self.assertEqual(self.request('/api/runs/' + key + '/recover', {})[0], 200)
            self.assertTrue(connections)
            for connection in connections:
                with self.assertRaisesRegex(sqlite3.ProgrammingError, 'closed database'):
                    connection.execute('SELECT 1')
        finally:
            for connection in connections:
                connection.close()


if __name__ == '__main__':
    unittest.main()
