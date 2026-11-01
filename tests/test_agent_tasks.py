import json
import tempfile
import unittest
from pathlib import Path
from pcv.store import Store
from pcv.agent_tasks import initialize, prepare, accept


class AgentTasksTests(unittest.TestCase):
    def test_handoff_validation_idempotency_and_revocation(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / 'test.db')
            try:
                store.init()
                initialize(store)
                source = store.add_source(name='test', kind='browser', access_scope='test', legal_basis='test')
                evidence = store.add_evidence(source_id=source, title='test', content='Synthetic user statement')
                job = prepare(store, evidence, Path(tmp) / 'tasks')
                task = json.loads(Path(job['path']).read_text(encoding='utf-8'))
                result = {'task_id': job['task_id'], 'evidence_hash': 'wrong', 'summary': 'Summary',
                          'limitations': ['Text only'], 'candidate_inferences': ['Candidate']}
                with self.assertRaisesRegex(ValueError, 'hash mismatch'):
                    accept(store, result)
                self.assertEqual(len(store.list_evidence()), 1)
                result['evidence_hash'] = task['evidence_hash']
                accepted = accept(store, result)
                self.assertEqual(accepted['pending_candidates'], 1)
                self.assertTrue(accept(store, result)['already_complete'])
                self.assertEqual(len(store.list_evidence()), 2)
                self.assertEqual(store.export_profile()['claims'], [])
                self.assertEqual(store.list_claims()[0]['review_status'], 'pending')
                another = store.add_evidence(source_id=source, title='second', content='other')
                pending = prepare(store, another, Path(tmp) / 'tasks')
                other = json.loads(Path(pending['path']).read_text(encoding='utf-8'))
                result.update(task_id=pending['task_id'], evidence_hash=other['evidence_hash'])
                store.revoke_source(source)
                with self.assertRaisesRegex(ValueError, 'inactive'):
                    accept(store, result)
                self.assertEqual(len(store.list_evidence()), 3)
            finally:
                store.close()
