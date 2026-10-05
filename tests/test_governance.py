"""Baseline lifecycle and review records through the real HTTP API."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
import sqlite3
import uuid
from unittest.mock import patch

from governance import fingerprint
from test_dashboard import DashboardTestCase


class GovernanceTests(DashboardTestCase):
    def proposal(self, source='fixture', **changes):
        data = {'source': source, 'request_id': str(uuid.uuid4()), 'actor': 'Lab proposer', 'reason': 'Synthetic change reference GOV-1'}
        data.update(changes)
        return data

    def propose(self, source='fixture'):
        status, result = self.request('/api/baselines', self.proposal(source))
        self.assertEqual(status, 200, result)
        return result

    def baseline_data(self, baseline, action='APPROVE', **changes):
        data = {'request_id': str(uuid.uuid4()), 'actor': 'Lab administrator', 'reason': 'Reviewed exact synthetic configuration',
                'action': action, 'expected_revision': baseline['revision'], 'expected_sha256': baseline['sha256']}
        data.update(changes)
        return data

    def decide(self, baseline, action='APPROVE', **changes):
        return self.request('/api/baselines/' + baseline['baseline_id'] + '/decisions', self.baseline_data(baseline, action, **changes))

    def review_data(self, record, action='ACCEPT_OBSERVED', **changes):
        status, history = self.request('/api/runs/' + record['id'] + '/reviews')
        self.assertEqual(status, 200)
        data = {'request_id': str(uuid.uuid4()), 'actor': 'Lab reviewer', 'reason': 'Reviewed all findings and exact observation',
                'action': action, 'expected_revision': history['revision'], 'expected_audit_sha256': history['audit_sha256']}
        data.update(changes)
        return data

    def review(self, record, action='ACCEPT_OBSERVED'):
        return self.request('/api/runs/' + record['id'] + '/reviews', self.review_data(record, action))

    def governed_run(self, baseline, scenario='combined', key=None):
        return self.request('/api/runs', {'scenario': scenario, 'request_id': key or str(uuid.uuid4()), 'baseline_id': baseline['baseline_id']})

    def test_explicit_baseline_lifecycle_and_historical_binding(self):
        self.assertEqual(self.request('/api/baselines'), (200, []))
        _, fixture = self.run_case('no_drift')
        self.assertEqual(fixture['baseline_governance']['mode'], 'FIXTURE_REFERENCE')
        self.assertEqual(self.request('/api/baselines')[1], [])
        baseline = self.propose()
        self.assertEqual(baseline['status'], 'PENDING')
        self.assertEqual(self.governed_run(baseline)[0], 409)
        self.assertEqual(self.decide(baseline, expected_sha256='0' * 64)[0], 409)
        status, approval = self.decide(baseline)
        self.assertEqual(status, 200)
        status, record = self.governed_run(baseline)
        self.assertEqual(status, 201)
        self.assertEqual(record['audit']['status'], 'REVIEW_REQUIRED')
        self.assertEqual(record['baseline_governance']['approval_event'], approval)
        self.assertEqual(record['audit']['baseline_sha256'], baseline['sha256'])
        approved = self.request('/api/baselines')[1][0]
        self.assertEqual(approved['status'], 'APPROVED')
        self.assertEqual(self.decide(approved, 'REJECT')[0], 409)
        self.assertEqual(self.decide(approved, 'RETIRE')[0], 200)
        self.assertEqual(self.governed_run(baseline)[0], 409)
        self.assertEqual(self.request('/api/runs/' + record['id'])[1], record)
        # Recovery binds the historical approval, even after retirement.
        (self.root / record['id'] / 'record.json').unlink()
        self.assertEqual(self.request('/api/runs/' + record['id'] + '/recover', {}), (200, record))
        self.stop()
        self.start()
        self.assertEqual(self.request('/api/baselines')[1][0]['status'], 'RETIRED')
        self.assertEqual(self.request('/api/runs/' + record['id'])[1], record)

    def test_review_acceptance_proposes_new_version_without_changing_evidence(self):
        _, record = self.run_case('combined')
        folder = self.root / record['id']
        originals = {name: (folder / name).read_bytes() for name in ('audit.json', 'record.json', 'snapshot.cfg', 'baseline_repo/baseline.cfg')}
        self.assertEqual(self.request('/api/baselines', self.proposal(record['id']))[0], 409)
        status, acceptance = self.review(record)
        self.assertEqual(status, 200)
        candidate = self.propose(record['id'])
        self.assertEqual(candidate['configuration'], record['configurations']['observed'])
        self.assertEqual(candidate['source']['review_event_id'], acceptance['event_id'])
        self.assertEqual(self.decide(candidate)[0], 200)
        status, governed = self.governed_run(candidate)
        self.assertEqual(status, 201)
        self.assertEqual(governed['audit']['status'], 'NO_DRIFT')
        self.assertEqual(self.run_case('combined')[1]['audit']['status'], 'REVIEW_REQUIRED')
        for name, content in originals.items():
            self.assertEqual((folder / name).read_bytes(), content, name)
        history = self.request('/api/runs/' + record['id'] + '/reviews/download')[1]
        self.assertEqual(history['events'], [acceptance])
        self.assertEqual(history['audit_sha256'], fingerprint(record['audit']))
        self.assertEqual(self.request('/api/runs/' + record['id'] + '/download')[1], record)

    def test_supersession_stale_review_and_baseline_proposals(self):
        _, record = self.run_case('combined')
        stale = self.review_data(record)
        self.assertEqual(self.review(record)[0], 200)
        candidate = self.propose(record['id'])
        status, rejection = self.review(record, 'REJECT_CHANGE')
        self.assertEqual(status, 200)
        events = self.request('/api/runs/' + record['id'] + '/reviews')[1]['events']
        self.assertEqual(len(events), 2)
        self.assertEqual(rejection['supersedes_event_id'], events[0]['event_id'])
        self.assertEqual(self.request('/api/runs/' + record['id'] + '/reviews', stale)[0], 409)
        self.assertEqual(self.decide(candidate)[0], 409)
        self.assertEqual(self.request('/api/baselines', self.proposal(record['id']))[0], 409)
        self.assertEqual(self.request('/api/runs/' + record['id'])[1]['audit'], record['audit'])

    def test_idempotency_and_simultaneous_decisions(self):
        proposal = self.proposal()
        status, baseline = self.request('/api/baselines', proposal)
        self.assertEqual(status, 200)
        self.assertEqual(self.request('/api/baselines', proposal), (200, baseline))
        self.assertEqual(self.request('/api/baselines', {**proposal, 'reason': 'Changed request'})[0], 409)
        body = self.baseline_data(baseline)
        url = '/api/baselines/' + baseline['baseline_id'] + '/decisions'
        status, approved = self.request(url, body)
        self.assertEqual(status, 200)
        self.assertEqual(self.request(url, body), (200, approved))
        self.assertEqual(self.decide(baseline, 'REJECT')[0], 409)
        _, record = self.run_case('combined')
        url = '/api/runs/' + record['id'] + '/reviews'
        body = self.review_data(record)
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(lambda payload: self.request(url, payload), [body, {**body, 'request_id': str(uuid.uuid4()), 'action': 'DEFER'}]))
        self.assertEqual(sorted(status for status, _ in responses), [200, 409])
        winner = next(result for status, result in responses if status == 200)
        submitted = body if winner['event_id'] == body['request_id'] else {**body, 'request_id': winner['event_id'], 'action': 'DEFER'}
        self.assertEqual(self.request(url, submitted), (200, winner))
        self.assertEqual(len(self.request(url)[1]['events']), 1)

    def test_invalid_inputs_origins_outcomes_and_incomplete_records(self):
        for data in [self.proposal(actor=' '), self.proposal(reason=''), self.proposal(source='../auditor.py'), {**self.proposal(), 'configuration':'arbitrary'}]:
            self.assertEqual(self.request('/api/baselines', data)[0], 400)
        self.assertEqual(self.request('/api/baselines', self.proposal(), {'Origin':'https://untrusted.invalid'})[0], 403)
        _, manual = self.run_case('unsupported')
        self.assertEqual(self.review(manual)[0], 400)
        self.assertEqual(self.review(manual, 'INVESTIGATE')[0], 200)
        self.assertEqual(self.request('/api/baselines', self.proposal(manual['id']))[0], 409)
        _, clean = self.run_case('no_drift')
        self.assertEqual(self.review(clean, 'ACKNOWLEDGE')[0], 200)
        self.assertEqual(self.review(clean, 'REJECT_CHANGE')[0], 400)
        key = str(uuid.uuid4())
        with patch('dashboard.git', side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.server.store.run('combined', key)
        self.assertEqual(self.request('/api/runs/' + key + '/reviews')[0], 409)
        self.assertEqual(self.request('/api/runs/' + key + '/reviews', self.review_data(clean, 'ACKNOWLEDGE'))[0], 409)

    def test_reviews_are_append_only_persistent_and_bound_to_evidence(self):
        _, record = self.run_case('combined')
        self.assertEqual(self.review(record)[0], 200)
        original = self.request('/api/runs/' + record['id'] + '/reviews')[1]
        with closing(sqlite3.connect(self.root / 'governance.sqlite')) as connection, connection:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute('DELETE FROM events')
        self.stop()
        self.start()
        self.assertEqual(self.request('/api/runs/' + record['id'] + '/reviews')[1], original)
        damaged = json.loads(json.dumps(record))
        damaged['audit']['findings'][0]['explanation'] = 'Changed after review'
        (self.root / record['id'] / 'record.json').write_text(json.dumps(damaged))
        changed = self.request('/api/runs/' + record['id'] + '/reviews')[1]
        self.assertFalse(changed['evidence_matches'])
        self.assertEqual(self.review(damaged, 'DEFER')[0], 409)

    def test_rejected_candidate_is_terminal_and_run_id_binds_baseline(self):
        rejected = self.propose()
        self.assertEqual(self.decide(rejected, 'REJECT')[0], 200)
        current = self.request('/api/baselines')[1][0]
        self.assertEqual(current['status'], 'REJECTED')
        self.assertEqual(self.decide(current)[0], 409)
        self.assertEqual(self.governed_run(rejected)[0], 409)
        approved = self.propose()
        self.assertEqual(self.decide(approved)[0], 200)
        key = str(uuid.uuid4())
        self.assertEqual(self.governed_run(approved, key=key)[0], 201)
        self.assertEqual(self.run_case('combined', key)[0], 409)
        self.assertEqual(self.governed_run(approved, key=key)[0], 200)
        self.assertEqual(len(self.request('/api/baselines/download')[1]), 2)

    def test_ledger_failure_rolls_back_and_same_request_can_retry(self):
        payload = self.proposal()
        with patch.object(self.server.store.governance, 'append', side_effect=sqlite3.OperationalError('Simulated disk failure')):
            self.assertEqual(self.request('/api/baselines', payload)[0], 500)
        self.assertEqual(self.request('/api/baselines')[1], [])
        self.assertEqual(self.request('/api/baselines', payload)[0], 200)
        self.assertEqual(len(self.request('/api/baselines')[1]), 1)
        baseline = self.request('/api/baselines')[1][0]
        with closing(sqlite3.connect(self.root / 'governance.sqlite')) as connection, connection:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute('UPDATE baselines SET payload=? WHERE id=?', ('{}', baseline['baseline_id']))
        self.assertEqual(self.decide(baseline, expected_revision=True)[0], 400)
