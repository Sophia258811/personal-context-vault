import os
import tempfile
import unittest
from pathlib import Path
from pcv.desktop import configure, pairing_token
from pcv.store import Store


class DesktopTests(unittest.TestCase):
    @unittest.skipUnless(os.name == 'nt', 'Windows DPAPI')
    def test_pairing_survives_restart_and_is_protected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            token = pairing_token(root)
            self.assertEqual(pairing_token(root), token)
            self.assertNotIn(token.encode(), (root / 'pairing.bin').read_bytes())

    def test_setup_is_idempotent_and_does_not_restore_revoked_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            connector_id = configure(root)
            self.assertEqual(configure(root), connector_id)
            store = Store(root / 'vault.db')
            try:
                connectors = store.list_connectors()
                self.assertEqual(len(connectors), 1)
                store.revoke_source(connectors[0]['source_id'])
            finally:
                store.close()
            with self.assertRaises(ValueError):
                configure(root)
