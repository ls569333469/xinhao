import json
from test_task_service import HttpTests

class CountdownHttpTests(HttpTests):
    def test_countdown_api_persists_without_submitting_on_get(self):
        before=json.loads(self.request('/api/settings')[1])
        self.assertIsNone(before['test_countdown'])
        code,raw=self.request('/api/schedule/test-countdown','POST',{'minutes':15})
        self.assertEqual(code,200);armed=json.loads(raw)
        self.assertEqual(armed['state'],'armed');self.assertEqual(self.service.tasks(),[])
        self.assertEqual(json.loads(self.request('/api/settings')[1])['test_countdown']['id'],armed['id'])
        self.assertEqual(self.request('/api/schedule/test-countdown','POST',{'minutes':1})[0],400)
        self.assertEqual(self.request('/api/schedule/test-countdown','POST',{'minutes':15,'enabled':True})[0],400)
        self.assertEqual(self.request('/api/schedule/test-countdown','POST',{}, {'Origin':'https://evil.test'})[0],403)
        code,raw=self.request('/api/schedule/test-countdown/cancel','POST',{})
        self.assertEqual(code,200);self.assertEqual(json.loads(raw)['state'],'cancelled')
        after=json.loads(self.request('/api/settings')[1])
        for key in ('scan','observe','version','next_due','observe_next_due'):self.assertEqual(before[key],after[key])
