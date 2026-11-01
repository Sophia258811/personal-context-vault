import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

from pcv.companion import create_server
from pcv.connectors import sync_connector
from pcv.store import Store


class CompanionTests(unittest.TestCase):
    def test_http_auth_validation_and_evidence_pipeline(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inbox = root / 'inbox'
            server = create_server(inbox, 'test-only-token', 0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            def post(value, token='test-only-token'):
                connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=3)
                try:
                    connection.request('POST', '/events', json.dumps(value),
                                       {'Authorization': 'Bearer ' + token})
                    response = connection.getresponse()
                    response.read()
                    return response.status
                finally:
                    connection.close()
            try:
                event = {'url': 'https://example.org/read?secret=hidden#fragment',
                         'text': '合成浏览内容', 'title': 'Synthetic'}
                self.assertEqual(post(event, 'wrong'), 401)
                for invalid in (None, [], {'url': 5}, {'url': 'file:///private', 'text': 'x'}):
                    self.assertEqual(post(invalid), 400)
                self.assertEqual(list(inbox.glob('*.json')), [])
                self.assertEqual(post(event), 204)
                saved = json.loads(next(inbox.glob('*.json')).read_text(encoding='utf-8'))
                self.assertEqual(saved['url'], 'https://example.org/read')
                self.assertEqual(saved['actor_role'], 'third_party')
                store = Store(root / 'test.db')
                try:
                    store.init()
                    source = store.add_source(name='synthetic', kind='browser',
                                              access_scope='test', legal_basis='test')
                    connector = store.add_connector(source_id=source, name='browser',
                        platform='browser', format_name='json', input_path=inbox)
                    self.assertEqual(sync_connector(store, connector)['imported'], 1)
                    self.assertEqual(sync_connector(store, connector)['skipped'], 1)
                    self.assertEqual(store.export_profile()['claims'], [])
                    store.revoke_source(source)
                    with self.assertRaises(ValueError):
                        sync_connector(store, connector)
                finally:
                    store.close()
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)
