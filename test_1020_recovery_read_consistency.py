import itertools,json,sqlite3,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import unified_workbench as workbench
from test_unified_workbench import UnifiedEvidence

class RecoveryReading(unittest.TestCase):
    def fixture(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        root=Path(temp.name);UnifiedEvidence().fixture(root)
        return root
    def test_acquisition_progress_during_load_does_not_hide_market_rows(self):
        root=self.fixture();original=workbench.fingerprint;seq=itertools.count(1)
        def changing(root):return dict(original(root),recovery_commit_seq=str(next(seq)))
        with patch.object(workbench,'fingerprint',side_effect=changing):
            first=workbench.load_payload(root);second=workbench.load_payload(root)
        self.assertEqual(first['contract_count'],1)
        self.assertEqual(first['generation'],second['generation'])
        self.assertEqual(first['auxiliary_consistency'],'recovery_status_independent_snapshot')
    def test_actual_strategy_generation_race_is_still_rejected(self):
        root=self.fixture();original=workbench.fingerprint;seq=itertools.count(1)
        def changing(root):return dict(original(root),tracking_commit_seq=str(next(seq)))
        with patch.object(workbench,'fingerprint',side_effect=changing):
            with self.assertRaisesRegex(OSError,'generation'):workbench.load_payload(root)
    def test_status_before_first_projection_does_not_raise(self):
        from collection_status import status
        root=self.fixture();path=root/'runtime/signal-tracking.sqlite3'
        db=sqlite3.connect(path);db.execute('CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT)');db.commit();db.close()
        self.assertIsNone(status(root)['effective'])

if __name__=='__main__':unittest.main()
