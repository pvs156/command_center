"""Nightly backup of everything you'd hate to lose: the database, career profile and Word resume.

Copies go to OneDrive\\JobSearchCommandCenter-backup\\YYYY-MM-DD (or Documents when OneDrive
isn't set up). The newest 14 days are kept.
"""
import os
import shutil
import sqlite3
from datetime import date
from pathlib import Path

from . import store

KEEP = 14


def target_root():
    base = os.environ.get('OneDrive') or os.environ.get('OneDriveConsumer') or str(Path.home() / 'Documents')
    return Path(base) / 'JobSearchCommandCenter-backup'


def run(root=None):
    root = Path(root) if root else target_root()
    dest = root / date.today().isoformat()
    dest.mkdir(parents=True, exist_ok=True)
    # SQLite's online backup API gives a consistent copy even while the app is writing.
    src = sqlite3.connect(store.DB)
    out = sqlite3.connect(dest / 'command-center.db')
    with out:
        src.backup(out)
    src.close()
    out.close()
    data = Path(store.DB).parent
    for folder in ('career', 'uploads'):
        if (data / folder).exists():
            shutil.copytree(data / folder, dest / folder, dirs_exist_ok=True)
    days = sorted(p for p in root.iterdir() if p.is_dir() and len(p.name) == 10 and p.name[4] == '-')
    for old in days[:-KEEP]:
        shutil.rmtree(old, ignore_errors=True)
    state = store.get('scheduler', 'state', {})
    state['last_backup'] = date.today().isoformat()
    state['last_backup_path'] = str(dest)
    store.put('scheduler', 'state', state)
    return dest


def due():
    cfg = store.get('settings', 'main') or {}
    if cfg.get('backup', True) is False:
        return False
    return store.get('scheduler', 'state', {}).get('last_backup') != date.today().isoformat()
