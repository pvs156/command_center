"""Run the Command Center in the background with a tray icon (no console window).

    pythonw -m app.tray               start (or, if already running, just open the app)
    pythonw -m app.tray --no-browser  start quietly (used at Windows login)

The server, hourly search, notifications and nightly backup all run inside this process.
Logs: data/logs/server.log
"""
import logging
import os
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
URL = 'http://localhost:8765'
LOGS = ROOT / 'data' / 'logs'


def port_open():
    try:
        with socket.create_connection(('127.0.0.1', 8765), timeout=0.5):
            return True
    except OSError:
        return False


def _setup_logging():
    LOGS.mkdir(parents=True, exist_ok=True)
    log_file = open(LOGS / 'server.log', 'a', buffering=1, encoding='utf-8')
    if sys.stdout is None or sys.stderr is None:  # pythonw has no console
        sys.stdout = sys.stderr = log_file
    logging.basicConfig(stream=log_file, level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s: %(message)s')


def main():
    os.chdir(ROOT)
    quiet = '--no-browser' in sys.argv
    if port_open():  # already running: behave like a shortcut to the app
        if not quiet:
            webbrowser.open(URL)
        return
    _setup_logging()
    import httpx
    import pystray
    import uvicorn
    from PIL import Image

    from app.main import app

    server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=8765, log_config=None, log_level='info'))
    threading.Thread(target=server.run, daemon=True, name='server').start()
    for _ in range(120):
        if port_open():
            break
        time.sleep(0.25)
    if not quiet:
        webbrowser.open(URL)

    waiting = {'n': 0}

    def refresh_count(icon):
        while not server.should_exit:
            try:
                items = httpx.get(URL + '/api/inbox', timeout=30).json()
                waiting['n'] = sum(1 for i in items if i['kind'] == 'package_ready')
                icon.title = f'Job Search Command Center · {waiting["n"]} ready to apply'
                icon.update_menu()
            except Exception:
                pass
            time.sleep(120)

    def search_now(icon, item):
        try:
            httpx.post(URL + '/api/collect', json={'auto_prepare': True}, timeout=10)
        except Exception:
            pass

    def quit_app(icon, item):
        server.should_exit = True
        icon.stop()

    image_path = ROOT / 'app' / 'static' / 'icon.png'
    if not image_path.exists():
        from app.make_icon import main as make_icon
        make_icon()
    menu = pystray.Menu(
        pystray.MenuItem('Open Command Center', lambda i, _: webbrowser.open(URL), default=True),
        pystray.MenuItem(lambda _: f'Review desk ({waiting["n"]} ready)', lambda i, _: webbrowser.open(URL + '/#review')),
        pystray.MenuItem('Search now', search_now),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem('Quit (stops searching)', quit_app),
    )
    icon = pystray.Icon('job-search-command-center', Image.open(image_path), 'Job Search Command Center', menu)
    threading.Thread(target=refresh_count, args=(icon,), daemon=True, name='tray-count').start()
    icon.run()
    server.should_exit = True


if __name__ == '__main__':
    main()
