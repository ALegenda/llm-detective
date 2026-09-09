"""SQLite WAL, short transactions, durable jobs and fenced leases."""
import contextlib
import hashlib
import json
import sqlite3
import time
import uuid
from . import config


def uid():
    return uuid.uuid4().hex


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def digest(value):
    return hashlib.sha256(encode(value).encode()).hexdigest()


def connect():
    con = sqlite3.connect(config.DB_PATH, timeout=15, isolation_level=None)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA foreign_keys=ON')
    con.execute('PRAGMA busy_timeout=15000')
    return con


@contextlib.contextmanager
def transaction():
    con = connect()
    try:
        con.execute('BEGIN IMMEDIATE')
        yield con
        con.commit()
    except BaseException:
        con.rollback()
        raise
    finally:
        con.close()


def one(sql, args=()):
    with contextlib.closing(connect()) as con:
        row = con.execute(sql, args).fetchone()
        return dict(row) if row else None


def all_rows(sql, args=()):
    with contextlib.closing(connect()) as con:
        return [dict(row) for row in con.execute(sql, args).fetchall()]


def init():
    config.DATA.mkdir(parents=True, exist_ok=True)
    (config.DATA / 'assets').mkdir(exist_ok=True)
    with contextlib.closing(connect()) as con:
        con.execute('PRAGMA journal_mode=WAL')
        version = con.execute('PRAGMA user_version').fetchone()[0]
        if version > config.SCHEMA_VERSION:
            raise RuntimeError('Database is newer than this application')
        con.executescript('''
        CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY,email TEXT UNIQUE NOT NULL,password TEXT NOT NULL,created REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS telegram_users(telegram_id TEXT PRIMARY KEY,subject TEXT UNIQUE NOT NULL,user_id TEXT UNIQUE NOT NULL REFERENCES users(id),name TEXT NOT NULL,username TEXT NOT NULL,created REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY,user_id TEXT NOT NULL REFERENCES users(id),expires REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS cases(id TEXT PRIMARY KEY,user_id TEXT NOT NULL REFERENCES users(id),request_key TEXT NOT NULL,request_hash TEXT NOT NULL,settings TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'queued',blueprint TEXT,review TEXT,created REAL NOT NULL,updated REAL NOT NULL,UNIQUE(user_id,request_key));
        CREATE TABLE IF NOT EXISTS attempts(id TEXT PRIMARY KEY,case_id TEXT NOT NULL REFERENCES cases(id),user_id TEXT NOT NULL REFERENCES users(id),state TEXT NOT NULL,initial_state TEXT NOT NULL,version INTEGER NOT NULL DEFAULT 0,status TEXT NOT NULL DEFAULT 'active',verdict TEXT,created REAL NOT NULL,updated REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS commands(id TEXT PRIMARY KEY,attempt_id TEXT NOT NULL REFERENCES attempts(id),user_id TEXT NOT NULL,request_key TEXT NOT NULL,request_hash TEXT NOT NULL,payload TEXT NOT NULL,expected_version INTEGER NOT NULL,status TEXT NOT NULL DEFAULT 'queued',result TEXT,created REAL NOT NULL,UNIQUE(attempt_id,request_key));
        CREATE UNIQUE INDEX IF NOT EXISTS one_active_command ON commands(attempt_id) WHERE status IN ('queued','running');
        CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,case_id TEXT NOT NULL REFERENCES cases(id),command_id TEXT REFERENCES commands(id),kind TEXT NOT NULL,payload TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'queued',priority INTEGER NOT NULL DEFAULT 10,attempts INTEGER NOT NULL DEFAULT 0,repair_count INTEGER NOT NULL DEFAULT 0,available REAL NOT NULL,lease_until REAL,lease_token TEXT,checkpoint TEXT,error_code TEXT,diagnostic TEXT,created REAL NOT NULL,updated REAL NOT NULL,dedupe TEXT UNIQUE NOT NULL);
        CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT,attempt_id TEXT NOT NULL REFERENCES attempts(id),command_id TEXT,version INTEGER NOT NULL,minute INTEGER NOT NULL,kind TEXT NOT NULL,public TEXT NOT NULL,private TEXT NOT NULL,created REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS assets(id TEXT PRIMARY KEY,case_id TEXT NOT NULL REFERENCES cases(id),kind TEXT NOT NULL,entity_id TEXT NOT NULL,variant TEXT NOT NULL,path TEXT NOT NULL,base_id TEXT,provenance TEXT NOT NULL,created REAL NOT NULL,UNIQUE(case_id,kind,entity_id,variant));
        CREATE TABLE IF NOT EXISTS operations(id TEXT PRIMARY KEY,case_id TEXT NOT NULL,user_id TEXT NOT NULL,job_id TEXT,category TEXT NOT NULL,status TEXT NOT NULL,model TEXT NOT NULL,request_id TEXT,input_tokens INTEGER DEFAULT 0,output_tokens INTEGER DEFAULT 0,elapsed REAL,error_code TEXT,created REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY,user_id TEXT NOT NULL,action TEXT NOT NULL,target TEXT NOT NULL,created REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS rate_limits(key TEXT PRIMARY KEY,count INTEGER NOT NULL,expires REAL NOT NULL);
        ''')
        # Version 2 adds a durable, job-scoped structured-response cache.
        columns={row[1] for row in con.execute('PRAGMA table_info(operations)')}
        if 'cache_key' not in columns:
            con.execute('ALTER TABLE operations ADD COLUMN cache_key TEXT')
            con.execute('ALTER TABLE operations ADD COLUMN response TEXT')
        # Version 3 adds Telegram identities without changing legacy email accounts.
        # Version 4 stores the OIDC subject separately from Telegram's numeric user ID.
        identity_columns={row[1] for row in con.execute('PRAGMA table_info(telegram_users)')}
        if 'subject' not in identity_columns:
            con.execute("ALTER TABLE telegram_users ADD COLUMN subject TEXT NOT NULL DEFAULT ''")
            con.execute('UPDATE telegram_users SET subject=telegram_id')
        con.execute('CREATE UNIQUE INDEX IF NOT EXISTS telegram_subject_unique ON telegram_users(subject)')
        con.execute('PRAGMA user_version=4')


def enqueue(con, case_id, kind, payload, dedupe, priority=10, command_id=None):
    now = time.time()
    con.execute('INSERT OR IGNORE INTO jobs(id,case_id,command_id,kind,payload,priority,available,created,updated,dedupe) VALUES(?,?,?,?,?,?,?,?,?,?)',
                (uid(), case_id, command_id, kind, encode(payload), priority, now, now, now, dedupe))


def claim(lane=None):
    now = time.time()
    with transaction() as con:
        # A stalled worker's fencing token is replaced before another worker runs.
        clause = " AND kind='asset'" if lane=='assets' else " AND kind!='asset'" if lane=='interactive' else ''
        row = con.execute("SELECT * FROM jobs WHERE ((status IN ('queued','retry') AND available<=?) OR (status='running' AND lease_until<?))"+clause+" ORDER BY priority,created LIMIT 1", (now, now)).fetchone()
        if not row:
            return None
        token = uid()
        con.execute("UPDATE jobs SET status='running',lease_token=?,lease_until=?,attempts=attempts+1,updated=? WHERE id=?", (token, now + 300, now, row['id']))
        out = dict(row)
        out.update(lease_token=token, attempts=row['attempts'] + 1, status='running')
        return out


def fenced(con, job):
    row = con.execute('SELECT status,lease_token,lease_until FROM jobs WHERE id=?', (job['id'],)).fetchone()
    return row and row['status'] == 'running' and row['lease_token'] == job['lease_token'] and row['lease_until'] > time.time()


def save_checkpoint(job, data):
    with transaction() as con:
        if not fenced(con, job):
            raise RuntimeError('lease_lost')
        con.execute('UPDATE jobs SET checkpoint=?,updated=? WHERE id=?', (encode(data), time.time(), job['id']))


def rate_limit(key, limit, window):
    with transaction() as con:
        now = time.time()
        row = con.execute('SELECT * FROM rate_limits WHERE key=?', (key,)).fetchone()
        if row and row['expires'] > now:
            if row['count'] >= limit:
                return False
            con.execute('UPDATE rate_limits SET count=count+1 WHERE key=?', (key,))
        else:
            con.execute('INSERT OR REPLACE INTO rate_limits VALUES(?,1,?)', (key, now + window))
        return True
