"""Legacy smoke reports must not become unreadable default selections."""
import json
import tempfile
import unittest
from pathlib import Path

from ai_research import AiResearch


class ReportHistoryIdentityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.research = AiResearch(self.root, None)

    def write_report(self, dirname, report_id):
        path = self.research.dir / dirname / 'report.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({'report_id': report_id, 'source': 'local',
                                    'content': 'saved report', 'contracts': []}), encoding='utf-8')
        return path

    def test_legacy_smoke_is_preserved_but_does_not_consume_list_limit(self):
        legacy = self.write_report('report-r2b-ui-smoke-retrieval', 'report-r2b-ui-smoke-retrieval')
        original = legacy.read_bytes()
        valid_id = 'report-' + 'a' * 32
        self.write_report(valid_id, valid_id)
        reports = self.research.history(limit=1)
        self.assertEqual([r['report_id'] for r in reports], [valid_id])
        self.assertEqual(self.research.get_report(valid_id)['content'], 'saved report')
        self.assertEqual(legacy.read_bytes(), original)

    def test_directory_and_payload_identity_must_match(self):
        mismatch = self.write_report('report-' + 'b' * 32, 'report-' + 'c' * 32)
        self.assertEqual(self.research.history(), [])
        self.assertTrue(mismatch.exists())


if __name__ == '__main__':
    unittest.main()
