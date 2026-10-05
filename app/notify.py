"""Windows notifications: Remy taps you on the shoulder when new work is ready.

Each package is announced once (remembered in the database, so restarts don't repeat it).
Clicking the notification opens your review desk.
"""
import asyncio
import logging

from . import store

APP_ID = 'Job Search Command Center'
REVIEW_URL = 'http://localhost:8765/#review'
ICON = store.ROOT / 'app' / 'static' / 'icon.png'
log = logging.getLogger('command-center')


def toast(title, message, url=REVIEW_URL):
    """Show a Windows toast. Returns False when not on Windows or the library is missing."""
    try:
        from winotify import Notification, audio
    except ImportError:
        return False
    try:
        n = Notification(app_id=APP_ID, title=title, msg=message, launch=url,
                         icon=str(ICON) if ICON.exists() else '')
        n.set_audio(audio.Default, loop=False)
        n.add_actions(label='Open review desk', launch=url)
        n.show()
        return True
    except Exception as exc:  # a notification must never take the app down
        log.warning('notification failed: %s', exc)
        return False


def new_items(items, seen):
    """Package cards and follow-ups not announced yet."""
    out = []
    for i in items:
        key = i.get('package_id') if i['kind'] == 'package_ready' else (i['id'] if i['kind'] == 'follow_up' else None)
        if key and key not in seen:
            out.append((key, i))
    return out


def message_for(fresh, names):
    remy = names.get('reviewer', 'Remy')
    jobs = [i for _, i in fresh if i['kind'] == 'package_ready']
    follow = [i for _, i in fresh if i['kind'] == 'follow_up']
    if jobs:
        top = jobs[0]
        title = f'{remy} brought {len(jobs)} new job{"s" if len(jobs) > 1 else ""} ready to apply'
        lines = [f'{i["company"]} · {i["title"]} ({i.get("match") or "?"}%)' for i in jobs[:3]]
        if len(jobs) > 3:
            lines.append(f'+{len(jobs) - 3} more')
        if follow:
            lines.append(f'Also {len(follow)} follow-up{"s" if len(follow) > 1 else ""} due')
        return title, '\n'.join(lines)
    return f'{len(follow)} follow-up{"s" if len(follow) > 1 else ""} due', '\n'.join(f'{i["company"]} · {i["title"]}' for i in follow[:3])


def check_once(get_inbox, names):
    cfg = store.get('settings', 'main') or {}
    if cfg.get('notifications', True) is False:
        return None
    seen = set(store.get('notify', 'seen', []))
    fresh = new_items(get_inbox(), seen)
    if not fresh:
        return None
    title, message = message_for(fresh, names)
    toast(title, message)
    store.put('notify', 'seen', sorted(seen | {k for k, _ in fresh})[-2000:])
    return title


async def loop(get_inbox, names_fn, interval=60):
    await asyncio.sleep(20)  # let the server and the first catch-up search start
    while True:
        try:
            await asyncio.to_thread(check_once, get_inbox, names_fn())
        except Exception as exc:
            log.warning('notifier tick failed: %s', exc)
        await asyncio.sleep(interval)
