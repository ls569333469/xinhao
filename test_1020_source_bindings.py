import sqlite3,unittest
from tracking.recovery_effects import input_jobs_conn

class IdentityGenerations(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:');self.db.row_factory=sqlite3.Row
        self.addCleanup(self.db.close)
        self.db.executescript('''
          CREATE TABLE tracking_recovery(job_key TEXT,source TEXT,epoch INTEGER,quality_version TEXT,contract TEXT,provider TEXT);
          CREATE TABLE recovery_replay_contexts(dependency_id TEXT,epoch INTEGER,input_sha TEXT);
          CREATE TABLE recovery_dependencies(dependency_id TEXT,input_epoch INTEGER,job_key TEXT,consumer TEXT,rule_version TEXT,event_id TEXT,start_ms INTEGER,target_ms INTEGER);
        ''')
        self.d=dict(job_key='new-oi',dependency_id='dep-new-oi',input_epoch=0,consumer='C_strategy',rule_version='c-1015-v1.1',event_id=None,contract='USDT-M|TESTUSDT',next_ms=10)
    def add(self,key,source,quality,sha='frozen',contract='USDT-M|TESTUSDT'):
        self.db.execute('INSERT INTO tracking_recovery VALUES(?,?,0,?,?,?)',(key,source,quality,contract,'fixture'))
        self.db.execute('INSERT INTO recovery_replay_contexts VALUES(?,0,?)',('dep-'+key,sha))
        self.db.execute('INSERT INTO recovery_dependencies VALUES(?,0,?,?,?,NULL,0,20)',('dep-'+key,key,'C_strategy','c-1015-v1.1'))
    def keys(self):return {r['job_key'] for r in input_jobs_conn(self.db,self.d)}
    def test_mapping_change_keeps_same_quality_sources_without_mixing_old_generation(self):
        for quality in ('old','new'):
            for source in ('oi','lsr','cap'):self.add(quality+'-'+source,source,quality)
        self.assertEqual(self.keys(),{'new-oi','new-lsr','new-cap'})
        self.d.update(job_key='old-oi',dependency_id='dep-old-oi')
        self.assertEqual(self.keys(),{'old-oi','old-lsr','old-cap'})
    def test_missing_corrected_source_does_not_borrow_old_mapping(self):
        self.add('new-oi','oi','new');self.add('old-cap','cap','old')
        self.assertEqual(self.keys(),{'new-oi'})
    def test_same_quality_ambiguous_role_still_rejected(self):
        self.add('new-oi','oi','new');self.add('new-lsr','lsr','new');self.add('other-lsr','lsr','new')
        with self.assertRaisesRegex(ValueError,'ambiguous_source_bindings'):self.keys()
    def test_other_frozen_context_or_contract_cannot_join(self):
        self.add('new-oi','oi','new');self.add('other-cap','cap','new',sha='different');self.add('other-lsr','lsr','new',contract='USDT-M|OTHERUSDT')
        self.assertEqual(self.keys(),{'new-oi'})
    def test_original_null_quality_contexts_remain_supported(self):
        self.add('new-oi','oi',None);self.add('cap','cap',None)
        self.assertEqual(self.keys(),{'new-oi','cap'})

class ArchivedInputGenerations(unittest.TestCase):
    def test_b_replay_reads_only_anchored_quality_with_actual_archives(self):
        from test_1020_recovery_closure import LocalEvidence
        from tracking.recovery_bridge import enqueue_verified
        from tracking.recovery import recovered_inputs
        fixture=LocalEvidence();fixture.setUp();self.addCleanup(fixture.tearDown)
        meta=fixture.synced();meta['USDT-M|TESTUSDT']['cap']['id']='old-asset'
        old=enqueue_verified(fixture.recovery,meta,contract='USDT-M|TESTUSDT',source='oi',start_ms=fixture.at,target_ms=fixture.at,clock_ms=fixture.at)
        new=fixture.job('oi')
        for job,value in [(old,1),(new,2)]:
            fixture.recovery.import_local(job,[dict(effective_at_ms=fixture.at,available_at_ms=fixture.at,value=value,unit='USD',local_provenance={'path':'fixture'})],clock_ms=fixture.at)
        kwargs=dict(market='USDT-M',symbol='TESTUSDT',start_ms=fixture.at,target_ms=fixture.at)
        with self.assertRaisesRegex(ValueError,'ambiguous_acquisition'):recovered_inputs(fixture.ledger,**kwargs)
        self.assertEqual(recovered_inputs(fixture.ledger,**kwargs,acquisition_job_key=new)['oi'][0]['value'],2)
        self.assertEqual(recovered_inputs(fixture.ledger,**kwargs,acquisition_job_key=old)['oi'][0]['value'],1)
        with self.assertRaisesRegex(ValueError,'anchor_contract'):recovered_inputs(fixture.ledger,**dict(kwargs,symbol='OTHER'),acquisition_job_key=new)

if __name__=='__main__':unittest.main()
