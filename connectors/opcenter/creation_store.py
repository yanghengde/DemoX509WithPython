"""记录创建请求的身份和结果；重启或重复提交时不会自动重放业务命令。"""
import hashlib
import json
import sqlite3
import time
from pathlib import Path


class MaterialCreateError(ValueError):
    def __init__(self, status, code, message, **detail):
        super().__init__(message)
        self.status = status
        self.detail = {'code': code, 'message': message, **detail}


class CreationStore:
    def __init__(self, path):
        self.path = Path(path)

    def db(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute('''CREATE TABLE IF NOT EXISTS material_creations (
            request_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, base_url TEXT NOT NULL,
            command TEXT NOT NULL, state TEXT NOT NULL, result TEXT, updated_at REAL NOT NULL)''')
        db.commit()
        return db

    @staticmethod
    def fingerprint(base_url, command):
        return hashlib.sha256(json.dumps([base_url, command], ensure_ascii=False,
                                         sort_keys=True, separators=(',', ':')).encode()).hexdigest()

    def get(self, request_id):
        db = self.db()
        try:
            row = db.execute('SELECT * FROM material_creations WHERE request_id=?', (request_id,)).fetchone()
            if row is None:
                return None
            return {**dict(row), 'command': json.loads(row['command']),
                    'result': json.loads(row['result']) if row['result'] else None}
        finally:
            db.close()

    def reserve(self, request_id, base_url, command):
        db = self.db()
        try:
            with db:
                db.execute('INSERT INTO material_creations VALUES(?,?,?,?,?,?,?)', (
                    request_id, self.fingerprint(base_url, command), base_url,
                    json.dumps(command, ensure_ascii=False), 'pending', None, time.time()))
        except sqlite3.IntegrityError:
            raise MaterialCreateError(409, 'request_in_progress', '该创建请求已提交，请检查创建结果。', request_id=request_id) from None
        finally:
            db.close()

    def save(self, request_id, state, result):
        db = self.db()
        try:
            with db:
                db.execute('UPDATE material_creations SET state=?,result=?,updated_at=? WHERE request_id=?',
                           (state, json.dumps(result, ensure_ascii=False), time.time(), request_id))
        finally:
            db.close()
