import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from b_accumulation_store import BAccumulationStore
from b_event_sqlite import SQLiteBStore
from migrate_b_events import migrate

class Ledger(unittest.TestCase):
    def test_full_equivalence_revision_and_transaction_failure(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);old=BAccumulationStore(root);old.ensure_observation_start('2026-10-17T00:00:00+00:00')
            params=dict(market='USDⓈ-M',symbol='TEST',trigger_open_time=1792224000000)
            payload=dict(schema_version=2,input_fingerprint='a',technical_event='pass',technical_confirmation='pending',bg4h=dict(input_rows=[[1,2,3]],ema7=2,ema25=1),bg4h_h1=None,baseline=[dict(open_time=1,o=2)])
            old.upsert_event(**params,payload=payload)
            old.upsert_event(**params,payload={**payload,'input_fingerprint':'b','technical_confirmation':'pass'})
            before=old._load();raw=old.path.read_bytes();migrate(root)
            store=BAccumulationStore(root);self.assertIsInstance(store,SQLiteBStore)
            after=store._load()
            for key in ('events','cursors','rounds','outbox','observation_started_at'):self.assertEqual(after[key],before[key])
            self.assertEqual(old.path.read_bytes(),raw)
            with store.connection() as db:self.assertEqual(db.execute('SELECT count(*) FROM evidence_blobs').fetchone()[0],2)
            snap=store._load()
            with patch.object(store,'_generation',side_effect=OSError('disk full')):
                with self.assertRaises(OSError):store.commit_symbol(params['market'],params['symbol'],[(params['trigger_open_time'],{**payload,'input_fingerprint':'c'})],dict(last_processed_boundary=1),{})
            self.assertEqual(store._load(),snap)
            event=store.list_events()[0]
            store.upsert_event(**params,payload={**payload,'input_fingerprint':'b','technical_confirmation':'pass'})
            self.assertEqual(store.list_events()[0],event)
            with store.connection(write=True) as db:db.execute("UPDATE evidence_blobs SET data=x'00'")
            with self.assertRaisesRegex(ValueError,'hash'):store.list_events()

if __name__=='__main__':unittest.main()
