"""Run by the Windows scheduled task that wakes the PC (plugged in only) every 2 hours.

Makes sure the app is running, starts a search with auto-tailoring, and keeps the PC awake
until the whole pipeline (Sam → Grace → Ana → Will + Rosa → Remy) has finished. Then Windows
can go back to sleep on its own.
"""
import ctypes
import subprocess
import sys
import time
from pathlib import Path

import httpx

from app.tray import ROOT, URL, port_open

ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
LIMIT_SECONDS = 20 * 60


def keep_awake(on):
    try:
        ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | (ES_SYSTEM_REQUIRED if on else 0))
    except Exception:
        pass


def main():
    keep_awake(True)
    try:
        if not port_open():  # you quit the tray app: start it quietly
            pythonw = Path(sys.executable).with_name('pythonw.exe')
            subprocess.Popen([str(pythonw if pythonw.exists() else sys.executable), '-m', 'app.tray', '--no-browser'],
                             cwd=ROOT, creationflags=getattr(subprocess, 'DETACHED_PROCESS', 0))
            for _ in range(120):
                if port_open():
                    break
                time.sleep(1)
        time.sleep(5)  # give the app's own catch-up search a moment to start
        try:
            httpx.post(URL + '/api/collect', json={'auto_prepare': True}, timeout=15)  # 409 = already searching: fine
        except Exception:
            pass
        started, idle = time.time(), 0
        while time.time() - started < LIMIT_SECONDS:
            time.sleep(10)
            try:
                running = httpx.get(URL + '/api/runs/active', timeout=15).json()['running']
            except Exception:
                running = []
            idle = 0 if running else idle + 1
            if idle >= 3:  # 30s with nothing running: search, tailoring and notifications are done
                break
    finally:
        keep_awake(False)


if __name__ == '__main__':
    main()
