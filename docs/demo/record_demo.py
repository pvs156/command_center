"""Record the README demo from the fictional sample workspace.

Starts its own sample-only server (fresh temporary database, no API keys,
scheduler off), so none of your real jobs, resume or contacts can appear.
    python docs/demo/record_demo.py [--shots]
Needs: pip install playwright imageio-ffmpeg && python -m playwright install chromium
"""
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

import imageio_ffmpeg
from playwright.sync_api import sync_playwright

URL = 'http://127.0.0.1:8799/'
OUT = Path(__file__).resolve().parent
W, H = 1280, 800
ROOT = OUT.parents[1]
SHOTS = '--shots' in sys.argv


def shot(page, name):
    if SHOTS:
        page.screenshot(path=str(OUT / f'shot-{name}.png'))


def pause(page, seconds):
    page.wait_for_timeout(int(seconds * 1000))


def sample_server():
    db = Path(tempfile.mkdtemp()) / 'demo.db'
    env = {**os.environ, 'COMMAND_CENTER_DB': str(db), 'COMMAND_CENTER_SCHEDULER': '0'}
    for key in ('GEMINI_API_KEY', 'ANTHROPIC_API_KEY', 'GROQ_API_KEY', 'NVIDIA_API_KEY'):
        env[key] = ''  # extractive only: nothing leaves the machine
    server = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'app.main:app', '--port', '8799'], cwd=ROOT, env=env,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(60):
        try:
            urllib.request.urlopen(URL + 'api/runs/active', timeout=1)
            return server
        except OSError:
            time.sleep(0.5)
    server.kill()
    raise SystemExit('sample server did not start')


def go(page, route):
    page.evaluate(f"location.hash = '{route}'")  # no reload, so running animations continue


def show_floor(page):
    page.evaluate("document.querySelector('canvas').scrollIntoView({block: 'center'})")


def main():
    server = sample_server()
    try:
        record()
    finally:
        server.kill()
    encode()


def record():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={'width': W, 'height': H}, device_scale_factor=1,
                                  record_video_dir=str(OUT / 'raw'), record_video_size={'width': W, 'height': H})
        ctx.add_init_script("localStorage.setItem('cc-mode', 'demo')")
        page = ctx.new_page()
        page.goto(URL + '#office')
        page.wait_for_selector('canvas')
        pause(page, 2)
        shot(page, '1-office')

        go(page, '#jobs')
        page.wait_for_selector('.job-check')
        pause(page, 1)
        boxes = page.locator('.job-check:not([disabled])')
        for i in range(min(3, boxes.count())):
            boxes.nth(i).check()
            pause(page, 0.4)
        shot(page, '2-jobs')
        page.click('[data-action="prepare-selected"]')
        pause(page, 0.8)
        go(page, '#office')  # hash change, not a reload, so the live run keeps animating
        page.wait_for_selector('canvas')
        show_floor(page)
        pause(page, 4)
        shot(page, '3a-handoffs')  # Grace → Ana → Will ∥ Rosa → Remy
        pause(page, 7)
        shot(page, '4-delivered')
        page.locator('[data-action="inbox-open"]').first.click()
        pause(page, 2.5)
        shot(page, '5-card')
        dialog = page.locator('dialog[open]')
        for _ in range(4):
            dialog.hover()
            page.mouse.wheel(0, 260)
            pause(page, 0.9)
        shot(page, '6-card-scrolled')
        pause(page, 1)
        ctx.close()
        browser.close()


def encode():
    raw = max((OUT / 'raw').glob('*.webm'), key=lambda f: f.stat().st_mtime)
    ff = imageio_ffmpeg.get_ffmpeg_exe()
    # MP4 for GitHub (drag into an issue to get an inline player) and portfolio sites.
    subprocess.run([ff, '-y', '-loglevel', 'error', '-i', str(raw), '-vf', 'setpts=PTS/1.25',
                    '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-crf', '24', '-movflags', '+faststart', str(OUT / 'demo.mp4')], check=True)
    # GIF for the top of the README: 960px, 12 fps, sped up, palette-optimised.
    vf = 'setpts=PTS/1.6,fps=12,scale=960:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=128:stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=4:diff_mode=rectangle'
    subprocess.run([ff, '-y', '-loglevel', 'error', '-i', str(raw), '-vf', vf, str(OUT / 'demo.gif')], check=True)
    shutil.rmtree(OUT / 'raw', ignore_errors=True)
    for f in ('demo.gif', 'demo.mp4'):
        print(f, round((OUT / f).stat().st_size / 1e6, 1), 'MB')


if __name__ == '__main__':
    main()
