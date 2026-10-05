"""Small durable single-user store. Private documents stay in ignored data/."""
import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
DB = Path(os.environ.get('COMMAND_CENTER_DB', ROOT / 'data' / 'command-center.db'))


def now():
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def connection():
    DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init():
    with connection() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS records(kind TEXT, id TEXT, value TEXT NOT NULL,
          PRIMARY KEY(kind,id));
        CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY AUTOINCREMENT,
          run_id TEXT, at TEXT, actor TEXT, type TEXT, message TEXT, payload TEXT);
        ''')
    if not get('settings', 'main'):
        put('settings', 'main', {
            'annual_min': 90000, 'hourly_min': 45, 'hourly_review_min': 40,
            'match_min': 60, 'review_min': 50, 'max_experience': 4,
            'location': 'Dallas, TX', 'country': 'US', 'relocate': True,
            'hourly_confirmed': False, 'provider': 'extractive',
        })


def put(kind, ident, value):
    with connection() as c:
        c.execute('INSERT OR REPLACE INTO records VALUES(?,?,?)', (kind, ident, json.dumps(value)))
    return value


def get(kind, ident, default=None):
    with connection() as c:
        row = c.execute('SELECT value FROM records WHERE kind=? AND id=?', (kind, ident)).fetchone()
    return json.loads(row['value']) if row else default


def all_records(kind):
    with connection() as c:
        rows = c.execute('SELECT value FROM records WHERE kind=? ORDER BY rowid DESC', (kind,)).fetchall()
    return [json.loads(r['value']) for r in rows]


def records_by(kind, field, value):
    """Records whose JSON field equals value (avoids loading a whole kind)."""
    with connection() as c:
        rows = c.execute(f"SELECT value FROM records WHERE kind=? AND json_extract(value, '$.{field}')=?", (kind, value)).fetchall()
    return [json.loads(r['value']) for r in rows]


def event(run_id, actor, event_type, message, **payload):
    with connection() as c:
        cur = c.execute('INSERT INTO events(run_id,at,actor,type,message,payload) VALUES(?,?,?,?,?,?)',
                        (run_id, now(), actor, event_type, message, json.dumps(payload)))
        return cur.lastrowid


def events(after=0, run_id=None, tail=None):
    with connection() as c:
        if tail:
            rows = c.execute('SELECT * FROM (SELECT * FROM events ORDER BY seq DESC LIMIT ?) ORDER BY seq', (tail,)).fetchall()
        else:
            rows = c.execute('SELECT * FROM events WHERE seq>? AND (? IS NULL OR run_id=?) ORDER BY seq LIMIT 1000',
                             (after, run_id, run_id)).fetchall()
    return [{**dict(r), 'payload': json.loads(r['payload'])} for r in rows]


def new_run(mode, task):
    ident = uuid4().hex
    value = {'id': ident, 'mode': mode, 'task': task, 'status': 'running', 'started_at': now()}
    put('runs', ident, value)
    return ident


def finish(run_id, status='completed', **details):
    value = get('runs', run_id)
    value.update(status=status, finished_at=now(), **details)
    put('runs', run_id, value)
