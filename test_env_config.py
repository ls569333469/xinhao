import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from env_config import read_env, env_get, write_env_value
from ai_credentials import AiCredentialStore, CredentialError


class EnvConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_literal_special_characters_and_quotes(self):
        (self.root / '.env').write_text('A=pk_abc#def$ghi\nB="pk_$x#y" # note\nC=\'literal ${X}\'\n', encoding='utf-8')
        self.assertEqual(read_env(self.root), {'A': 'pk_abc#def$ghi', 'B': 'pk_$x#y', 'C': 'literal ${X}'})

    def test_empty_blocks_stale_environment(self):
        with patch.dict(os.environ, {'CLAWBY_API_KEY': 'stale'}):
            self.assertEqual(env_get('CLAWBY_API_KEY', root=self.root), 'stale')
            write_env_value('CLAWBY_API_KEY', '', root=self.root)
            self.assertEqual(env_get('CLAWBY_API_KEY', root=self.root), '')

    def test_atomic_updates_preserve_other_provider_and_comments(self):
        (self.root / '.env').write_text('# retain\nCLAWBY_API_KEY=other\nAI_API_KEY=old\n', encoding='utf-8')
        write_env_value('AI_API_KEY', 'new#"$secret', root=self.root)
        self.assertEqual(read_env(self.root)['CLAWBY_API_KEY'], 'other')
        self.assertEqual(read_env(self.root)['AI_API_KEY'], 'new#"$secret')
        self.assertIn('# retain', (self.root / '.env').read_text())
        self.assertFalse(list(self.root.glob('.env.write-*')))

    def test_malformed_file_does_not_expose_secret_or_get_overwritten(self):
        path = self.root / '.env'
        for text in ['API_KEY="private-secret', 'API_KEY=a\nAPI_KEY=private-secret\n']:
            path.write_text(text, encoding='utf-8')
            with self.assertRaises(ValueError) as ctx:
                write_env_value('AI_API_KEY', 'new', root=self.root)
            self.assertNotIn('private-secret', str(ctx.exception))
            self.assertEqual(path.read_text(), text)

    def test_ai_save_read_replace_clear_and_no_legacy_fallback(self):
        (self.root / '.env').write_text('AI_API_KEY=old\nCLAWBY_API_KEY=other\n', encoding='utf-8')
        store = AiCredentialStore(self.root)
        store.key_path.write_bytes(b'legacy encrypted bytes')
        self.assertEqual(store.read_key(), 'old')
        store.write_key('new')
        self.assertEqual(store.read_key(), 'new')
        store.clear_key()
        self.assertFalse(store.has_key())
        self.assertFalse(store.key_path.exists())
        self.assertEqual(read_env(self.root)['CLAWBY_API_KEY'], 'other')
        with self.assertRaises(CredentialError):
            store.read_key()

    def test_no_line_injection(self):
        with self.assertRaises(ValueError):
            write_env_value('AI_API_KEY', 'x\nCLAWBY_API_KEY=y', root=self.root)

    def test_ui_config_does_not_return_plaintext_env_key(self):
        from ai_service import AiService, DEFAULT_AI
        write_env_value('AI_API_KEY', 'local-only-secret', root=self.root)
        ai = AiService(self.root)
        pub = ai.public_config()
        self.assertTrue(pub['keyConfigured'])
        self.assertNotIn('local-only-secret', json.dumps(pub))
        ai.save_config({'version': pub['version'], 'config': DEFAULT_AI, 'keyAction': 'replace', 'apiKey': 'replacement'})
        self.assertEqual(read_env(self.root)['AI_API_KEY'], 'replacement')


if __name__ == '__main__':
    unittest.main()
