import json
import tempfile
import unittest
from pathlib import Path
from pcv.observation import structured_fields
from pcv.store import Store
from pcv.connectors import sync_connector
from pcv.integration import from_database


class ObservationTests(unittest.TestCase):
    def event(self):
        return dict(schema_version=2, platform='douyin', platform_content_id='12345',
                    identity_method='page_url', observed_at='2026-11-02T00:00:00+08:00',
                    sections=dict(body='Title', comments='Third party comment', navigation='Home', unclassified=''),
                    media=dict(position_seconds=10, duration_seconds=60, paused=False, playback_rate=1))

    def test_validation_and_old_client(self):
        self.assertEqual(structured_fields({}, 'www.douyin.com'), {})
        validated = structured_fields(self.event(), 'www.douyin.com')
        self.assertEqual(validated['available_modalities'], ['text'])
        with self.assertRaises(ValueError):
            structured_fields(self.event(), 'example.org')
        invalid = self.event()
        invalid['media']['position_seconds'] = float('nan')
        with self.assertRaises(ValueError):
            structured_fields(invalid, 'www.douyin.com')

    def test_file_import_grouping_and_source_revocation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inbox = root / 'inbox'
            inbox.mkdir()
            store = Store(root / 'vault.db')
            try:
                store.init()
                source = store.add_source(name='test', kind='browser', access_scope='test', legal_basis='test')
                connector = store.add_connector(source_id=source, name='test', platform='browser',
                    format_name='json', input_path=inbox)
                for i in range(2):
                    event = self.event()
                    event['media']['position_seconds'] += i * 15
                    record = dict(structured_fields(event, 'www.douyin.com'), id=str(i),
                                  event_type='page_observed', text='Title', title='Synthetic')
                    (inbox / f'{i}.json').write_text(json.dumps(record), encoding='utf-8')
                self.assertEqual(sync_connector(store, connector)['imported'], 2)
                groups = from_database(store.path, source)
                self.assertEqual(len(groups), 1)
                self.assertEqual(len(groups[0]['observations']), 2)
                self.assertEqual(groups[0]['observations'][1]['media']['position_seconds'], 25)
                store.revoke_source(source)
                self.assertEqual(from_database(store.path, source), [])
            finally:
                store.close()
