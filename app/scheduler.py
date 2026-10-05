"""In-process schedule: Scout searches every N minutes (default hourly), the pipeline
auto-tailors what qualifies, and a weekly report is saved.

Runs only while the local server is running. After sleep, one catch-up search runs
(not one per missed hour).
"""
import asyncio
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from . import store

TZ = ZoneInfo('America/Chicago')
DAYS = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun']
DEFAULT = {'enabled': True, 'interval_minutes': 60, 'auto_prepare': True,
           'weekly_day': 'fri', 'weekly_time': '18:00'}


def config():
    return {**DEFAULT, **(store.get('settings', 'main') or {}).get('schedule', {})}


def due(now, hhmm, key):
    h, m = map(int, hhmm.split(':'))
    slot = now.replace(hour=h, minute=m, second=0, microsecond=0)
    if not (slot <= now < slot + timedelta(hours=3)):
        return False
    state = store.get('scheduler', 'state', {})
    return state.get(key) != slot.date().isoformat()


def mark(key, now):
    state = store.get('scheduler', 'state', {})
    state[key] = now.date().isoformat()
    state['last_' + key.split(':')[0]] = now.isoformat()
    store.put('scheduler', 'state', state)


def last_collect():
    value = store.get('scheduler', 'state', {}).get('last_collect')
    return datetime.fromisoformat(value) if value else None


def collect_due(now, cfg):
    last = last_collect()
    return last is None or now - last >= timedelta(minutes=cfg['interval_minutes'])


def next_runs(now=None):
    now = now or datetime.now(TZ)
    cfg = config()
    if not cfg['enabled']:
        return {'collect': None, 'weekly': None}
    last = last_collect()
    nxt = max(now, last + timedelta(minutes=cfg['interval_minutes'])) if last else now
    wd = DAYS.index(cfg['weekly_day'])
    h, m = map(int, cfg['weekly_time'].split(':'))
    weekly = (now + timedelta(days=(wd - now.weekday()) % 7)).replace(hour=h, minute=m, second=0, microsecond=0)
    if weekly <= now:
        weekly += timedelta(days=7)
    return {'collect': nxt.isoformat(), 'weekly': weekly.isoformat(), 'last_collect': last.isoformat() if last else None}


async def loop(start_collection, make_snapshot):
    while True:
        try:
            cfg = config()
            now = datetime.now(TZ)
            if cfg['enabled']:
                if collect_due(now, cfg):
                    ident = await start_collection(auto_prepare=cfg['auto_prepare'], trigger=f'hourly scout' if cfg['interval_minutes'] == 60 else f'every {cfg["interval_minutes"]} min')
                    if ident:
                        state = store.get('scheduler', 'state', {})
                        state['last_collect'] = now.isoformat()
                        store.put('scheduler', 'state', state)
                if DAYS[now.weekday()] == cfg['weekly_day'] and due(now, cfg['weekly_time'], 'weekly'):
                    mark('weekly', now)
                    make_snapshot()
        except Exception as exc:  # the loop must survive a bad tick
            store.event('scheduler', 'lead', 'error', f'Scheduler tick failed: {type(exc).__name__}')
        await asyncio.sleep(60)
