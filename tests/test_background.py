import sqlite3

import pytest

from app import backup, notify, store


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'DB', tmp_path / 'data' / 'test.db')
    store.init()
    return tmp_path


ITEMS = [
    {'kind': 'package_ready', 'package_id': 'p1', 'id': 'pkg:p1', 'company': 'Acme', 'title': 'AI Engineer', 'match': 82},
    {'kind': 'package_ready', 'package_id': 'p2', 'id': 'pkg:p2', 'company': 'Beta', 'title': 'LLM Engineer', 'match': 74},
    {'kind': 'fit_review', 'id': 'fit:x', 'company': 'C', 'title': 'T'},
]


def test_each_job_is_announced_once(db, monkeypatch):
    shown = []
    monkeypatch.setattr(notify, 'toast', lambda title, msg, url=notify.REVIEW_URL: shown.append((title, msg)) or True)
    title = notify.check_once(lambda: ITEMS, {'reviewer': 'Remy'})
    assert title == 'Remy brought 2 new jobs ready to apply'
    assert 'Acme · AI Engineer (82%)' in shown[0][1]
    assert notify.check_once(lambda: ITEMS, {'reviewer': 'Remy'}) is None  # nothing new: no repeat
    more = ITEMS + [{'kind': 'package_ready', 'package_id': 'p3', 'id': 'pkg:p3', 'company': 'Gamma', 'title': 'AI Dev', 'match': 70}]
    assert notify.check_once(lambda: more, {'reviewer': 'Remy'}) == 'Remy brought 1 new job ready to apply'
    assert len(shown) == 2


def test_notifications_can_be_turned_off(db, monkeypatch):
    monkeypatch.setattr(notify, 'toast', lambda *a, **k: pytest.fail('should not notify'))
    cfg = store.get('settings', 'main')
    cfg['notifications'] = False
    store.put('settings', 'main', cfg)
    assert notify.check_once(lambda: ITEMS, {}) is None


def test_backup_is_consistent_and_keeps_14_days(db):
    store.put('jobs', 'j1', {'id': 'j1', 'title': 'AI Engineer'})
    career = store.DB.parent / 'career'
    career.mkdir(parents=True)
    (career / 'career.md').write_text('# Me', encoding='utf-8')
    root = db / 'backups'
    for d in range(1, 20):
        (root / f'2026-01-{d:02d}').mkdir(parents=True)
    dest = backup.run(root)
    copy = sqlite3.connect(dest / 'command-center.db')
    assert copy.execute("select count(*) from records where kind='jobs'").fetchone()[0] == 1
    copy.close()
    assert (dest / 'career' / 'career.md').exists()
    assert len([p for p in root.iterdir() if p.is_dir()]) == 14
    assert not backup.due()  # done for today
