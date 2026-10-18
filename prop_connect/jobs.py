import hashlib
import json
import sqlite3
import threading
import uuid
from datetime import datetime,timezone
from concurrent.futures import ThreadPoolExecutor


def now():return datetime.now(timezone.utc).isoformat()


class Jobs:
    def __init__(self,path,executor):
        self.path=path;self.executor=executor;self.pool=ThreadPoolExecutor(max_workers=1)
        with self.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, request_key TEXT UNIQUE, request_hash TEXT, operation TEXT, payload TEXT, status TEXT, result TEXT, error TEXT, created_at TEXT, updated_at TEXT)')
            db.execute("UPDATE jobs SET status='interrupted',error='Processus interrompu ; aucune relance automatique',updated_at=? WHERE status IN ('queued','running')",(now(),))

    def db(self):return sqlite3.connect(self.path,timeout=15)

    def submit(self,operation,payload,key):
        if not key or len(key)>160:raise ValueError('Clé de requête stable requise, maximum 160 caractères')
        body=json.dumps(payload,sort_keys=True,allow_nan=False)
        digest=hashlib.sha256((operation+body).encode()).hexdigest()
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            old=db.execute('SELECT id,request_hash FROM jobs WHERE request_key=?',(key,)).fetchone()
            if old:
                if old[1]!=digest:raise ValueError('Clé déjà utilisée avec une autre requête')
                return self.get(old[0])
            if db.execute("SELECT COUNT(*) FROM jobs WHERE status IN ('queued','running')").fetchone()[0]>=8:
                raise ValueError('File de recherche pleine ; attendre la fin des tâches')
            jid=uuid.uuid4().hex
            db.execute('INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?,?,?)',(jid,key,digest,operation,body,'queued',None,None,now(),now()))
        self.pool.submit(self.run,jid)
        return self.get(jid)

    def run(self,jid):
        j=self.get(jid)
        with self.db() as db:db.execute("UPDATE jobs SET status='running',updated_at=? WHERE id=?",(now(),jid))
        try:
            result=self.executor(j['operation'],j['payload'])
            result=json.dumps(result,ensure_ascii=False,allow_nan=False)
            if len(result)>2_000_000:raise ValueError('Résultat trop grand pour le journal de recherche')
            with self.db() as db:db.execute("UPDATE jobs SET status='succeeded',result=?,updated_at=? WHERE id=?",(result,now(),jid))
        except Exception as exc:
            # Never persist upstream exception strings: they can contain secrets or headers.
            with self.db() as db:db.execute("UPDATE jobs SET status='failed',error=?,updated_at=? WHERE id=?",(type(exc).__name__+': échec ; vérifier configuration et journaux expurgés',now(),jid))

    def get(self,jid):
        with self.db() as db:
            db.row_factory=sqlite3.Row
            row=db.execute('SELECT * FROM jobs WHERE id=?',(jid,)).fetchone()
        if not row:return None
        d=dict(row)
        for k in ['payload','result']:
            d[k]=json.loads(d[k]) if d[k] else None
        return d

    def list(self):
        with self.db() as db:ids=[x[0] for x in db.execute('SELECT id FROM jobs ORDER BY created_at DESC LIMIT 50')]
        return [self.get(x) for x in ids]

    def close(self):self.pool.shutdown(wait=True)
