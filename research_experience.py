"""Reviewed, versioned research methods. Historical cases are never current facts."""
import copy
import datetime as dt
import hashlib
import json
import re
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path


class ExperienceConflict(ValueError):
    pass


FIELDS = ('title', 'trigger', 'action', 'boundary', 'counterexample', 'sources')


class ExperienceStore:
    def __init__(self, root):
        self.path = Path(root) / 'runtime' / 'research-experience.sqlite3'
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS feedback(id TEXT PRIMARY KEY, report_id TEXT NOT NULL, note TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS experiences(id TEXT PRIMARY KEY, version INTEGER NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS experience_versions(id TEXT NOT NULL, version INTEGER NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(id,version));
            ''')

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def text(value, name, maximum=4000, required=True):
        if not isinstance(value, str) or len(value.strip()) > maximum or (required and not value.strip()):
            raise ValueError(name + '须为非空文本且不超过' + str(maximum) + '字')
        return value.strip()

    def feedback(self, report_id, body):
        note = self.text(body.get('note'), '反馈')
        record = {'id': 'feedback-' + uuid.uuid4().hex, 'report_id': report_id, 'note': note,
                  'created_at': dt.datetime.now(dt.timezone.utc).isoformat()}
        with self.db() as db:
            db.execute('INSERT INTO feedback VALUES (?,?,?,?)', tuple(record[k] for k in ('id', 'report_id', 'note', 'created_at')))
        return record

    def listing(self):
        with self.db() as db:
            return {'entries': [json.loads(r['payload']) for r in db.execute('SELECT payload FROM experiences ORDER BY id')],
                    'feedback': [dict(r) for r in db.execute('SELECT * FROM feedback ORDER BY created_at DESC LIMIT 200')],
                    'policy': '仅显式选择已采用的方法；历史案例须重新检索核验，不作为当前事实；每任务最多5条'}

    def get(self, entry_id):
        with self.db() as db:
            rows = db.execute('SELECT payload FROM experience_versions WHERE id=? ORDER BY version', (entry_id,)).fetchall()
        if not rows:
            raise KeyError(entry_id)
        return {'entry': json.loads(rows[-1]['payload']), 'versions': [json.loads(r['payload']) for r in rows]}

    def _publish(self, db, entry, expected=None):
        entry = copy.deepcopy(entry)
        entry['updated_at'] = dt.datetime.now(dt.timezone.utc).isoformat()
        entry.pop('content_hash', None)
        entry['content_hash'] = hashlib.sha256(json.dumps(entry, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        raw = json.dumps(entry, ensure_ascii=False)
        if expected is None:
            db.execute('INSERT INTO experiences VALUES (?,?,?)', (entry['id'], entry['version'], raw))
        elif db.execute('UPDATE experiences SET version=?,payload=? WHERE id=? AND version=?',
                        (entry['version'], raw, entry['id'], expected)).rowcount != 1:
            raise ExperienceConflict('经验已变更，请刷新后重试；未覆盖')
        db.execute('INSERT INTO experience_versions VALUES (?,?,?)', (entry['id'], entry['version'], raw))
        return entry

    def _fields(self, body):
        out = {k: self.text(body.get(k, ''), k, maximum=200 if k == 'title' else 4000, required=k != 'counterexample')
               for k in FIELDS if k != 'sources'}
        sources = body.get('sources') or []
        if not isinstance(sources, list) or len(sources) > 20 or any(not isinstance(s, str) or len(s) > 1000 or not re.match(r'^https?://[^\s]+$', s) for s in sources):
            raise ValueError('来源须为不超过20条的http(s) URL；不会自动访问')
        out['sources'] = sources
        return out

    def create(self, body):
        if not isinstance(body, dict):
            raise ValueError('请求体须为对象')
        feedback_id = body.get('feedback_id')
        if feedback_id is not None and (not isinstance(feedback_id, str) or not re.fullmatch(r'feedback-[a-f0-9]{32}', feedback_id)):
            raise ValueError('反馈引用格式无效')
        entry = dict(self._fields(body), id='exp-' + uuid.uuid4().hex, version=1, status='candidate',
                     feedback_id=feedback_id, report_id=None, review_note='', methods_only=True)
        with self.db() as db:
            if entry['feedback_id']:
                feedback = db.execute('SELECT report_id FROM feedback WHERE id=?', (entry['feedback_id'],)).fetchone()
                if not feedback:
                    raise ValueError('反馈不存在')
                entry['report_id'] = feedback['report_id']
            return self._publish(db, entry)

    def update(self, entry_id, body):
        if not isinstance(body, dict):
            raise ValueError('请求体须为对象')
        with self.db() as db:
            row = db.execute('SELECT payload FROM experiences WHERE id=?', (entry_id,)).fetchone()
            if not row:
                raise KeyError(entry_id)
            entry = json.loads(row['payload'])
            expected = body.get('expected_version')
            if type(expected) is not int or expected != entry['version']:
                raise ExperienceConflict('经验版本已变更，请刷新；未覆盖')
            operation = body.get('operation', 'edit')
            if operation == 'edit':
                entry.update(self._fields(body))
                entry.update(status='candidate', review_note='')
            elif operation == 'review':
                if entry['status'] not in ('candidate', 'reviewed'):
                    raise ExperienceConflict('请先修改为候选再审核')
                entry.update(status='reviewed', review_note=self.text(body.get('review_note'), '审核说明'))
            elif operation == 'adopt':
                if entry['status'] != 'reviewed' or body.get('acknowledge_methods_only') is not True:
                    raise ExperienceConflict('采用前须审核并确认只复用方法，历史事实仍须新检索')
                entry['status'] = 'adopted'
            elif operation == 'disable':
                entry.update(status='disabled', review_note=self.text(body.get('review_note'), '停用原因'))
            else:
                raise ValueError('不支持的操作')
            entry['version'] += 1
            return self._publish(db, entry, expected)

    def resolve(self, selected):
        if selected is None:
            return []
        if not isinstance(selected, list) or len(selected) > 5:
            raise ValueError('每次投研最多显式选择5条经验')
        out = []
        with self.db() as db:
            for ref in selected:
                if not isinstance(ref, dict) or any(x['id'] == ref.get('id') for x in out):
                    raise ValueError('经验引用无效或重复')
                if not isinstance(ref.get('id'), str) or not re.fullmatch(r'exp-[a-f0-9]{32}', ref['id']):
                    raise ValueError('经验引用格式无效')
                row = db.execute('SELECT payload FROM experiences WHERE id=?', (ref.get('id'),)).fetchone()
                if not row:
                    raise ExperienceConflict('经验不存在，请重新预览')
                entry = json.loads(row['payload'])
                if entry['status'] != 'adopted' or type(ref.get('version')) is not int or ref.get('version') != entry['version'] or ref.get('content_hash') != entry['content_hash']:
                    raise ExperienceConflict('经验已停用/修改或未采用，请刷新并重新预览')
                entry['selection_reason'] = self.text(ref.get('selection_reason'), '选择依据', 500)
                out.append(entry)
        return sorted(out, key=lambda e: e['id'])
