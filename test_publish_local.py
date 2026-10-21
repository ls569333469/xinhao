"""Current publisher: no migrations, no partial writes on malformed anchors."""
import contextlib
import hashlib
import importlib.util
import io
import json
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parent


class LocalPublisher(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for name in ['index.html', 'strategy-registry.json', 'scan.json',
                     'cex-reserves.json', 'chain-flows.json', 'design/prototype.html',
                     'design/market-evidence.js', 'design/strategy-registry.template.js',
                     'design/prompt-templates.json',
                     'design/unified-workbench-fragment.html',
                     'design/evidence-runtime.js', 'design/reserves-runtime.js',
                     'design/pro-table-runtime.js']:
            target = self.root/name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(SOURCE/name, target)
        spec = importlib.util.spec_from_file_location('isolated_publish', SOURCE/'design/publish_local.py')
        self.publisher = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.publisher)
        self.publisher.ROOT = self.root
        self.publisher.DESIGN = self.root/'design'

    def hashes(self):
        return {p.relative_to(self.root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in self.root.rglob('*') if p.is_file()}

    def main(self, check=False):
        with patch.object(sys, 'argv', ['publisher'] + (['--check'] if check else [])), contextlib.redirect_stdout(io.StringIO()):
            return self.publisher.main()

    def test_idempotent_and_no_dead_outputs(self):
        self.assertEqual(self.main(), 0)
        baseline = self.hashes()
        self.assertEqual(self.main(), 0)
        self.assertEqual(self.main(check=True), 0)
        self.assertEqual(self.hashes(), baseline)
        self.assertFalse(list(self.root.rglob('*runtime-check.js')))
        self.assertIn('#indicators input[value]:checked', (self.root/'design/prototype.html').read_text(encoding='utf-8'))

    def test_check_is_read_only_even_when_stale(self):
        self.main()
        p = self.root/'scan.json'
        data = json.loads(p.read_text(encoding='utf-8'))
        data['finishedAt'] = '2030-01-01T00:00:00Z'
        p.write_text(json.dumps(data), encoding='utf-8')
        baseline = self.hashes()
        self.assertEqual(self.main(check=True), 1)
        self.assertEqual(self.hashes(), baseline)

    def test_bad_anchor_fails_before_any_write(self):
        p = self.root/'index.html'
        p.write_text(p.read_text(encoding='utf-8').replace('id="shared-market-evidence"', 'id="missing-evidence"'), encoding='utf-8')
        baseline = self.hashes()
        with self.assertRaisesRegex(ValueError, 'Expected one script'):
            self.main()
        self.assertEqual(self.hashes(), baseline)

    def test_changed_prompt_source_is_published_with_its_version(self):
        self.main()
        p = self.root/'design/prompt-templates.json'
        prompts = json.loads(p.read_text(encoding='utf-8'))
        prompts['version'] = 'publisher-test-next-version'
        prompts['profiles']['standard']['instruction'] += '\nNew reviewed requirement.'
        p.write_text(json.dumps(prompts, ensure_ascii=False), encoding='utf-8')
        baseline = self.hashes()
        self.assertEqual(self.main(check=True), 1)
        self.assertEqual(self.hashes(), baseline)
        self.assertEqual(self.main(), 0)
        page = (self.root/'design/prototype.html').read_text(encoding='utf-8')
        embedded = re.search(r'<script id="prompt-data"[^>]*>(.*?)</script>', page, re.S)
        self.assertIsNotNone(embedded)
        self.assertEqual(json.loads(embedded[1]), prompts)
        self.assertEqual(self.main(check=True), 0)

    def test_json_cannot_end_script(self):
        encoded = self.publisher.encode({'text': '</script><script>bad()</script>'})
        self.assertNotIn('</script>', encoded)
        self.assertEqual(json.loads(encoded)['text'], '</script><script>bad()</script>')

    def test_fragment_single_source_and_nested_host(self):
        self.main()
        p = self.root/'design/unified-workbench-fragment.html'
        p.write_text('<div><div>canonical new scope controls</div></div>', encoding='utf-8')
        self.assertEqual(self.main(check=True), 1)
        self.main()
        page = (self.root/'design/prototype.html').read_text(encoding='utf-8')
        self.assertIn(p.read_text(encoding='utf-8'), page)
        self.assertIn('snapshot-data', page)
        self.assertEqual(self.main(check=True), 0)


if __name__ == '__main__':
    unittest.main()
