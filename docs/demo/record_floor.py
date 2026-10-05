"""Record the working floor only: how the agents hand work to each other.

Same isolated sample server as record_demo.py (fictional data, no keys).
    python docs/demo/record_floor.py
Writes docs/demo/floor.gif and docs/demo/floor.mp4.
"""
import shutil
import subprocess
import time

import imageio_ffmpeg
from playwright.sync_api import sync_playwright

from record_demo import OUT, URL, go, pause, sample_server

W, H = 1280, 900


def record():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={'width': W, 'height': H}, device_scale_factor=1,
                                  record_video_dir=str(OUT / 'raw-floor'), record_video_size={'width': W, 'height': H})
        ctx.add_init_script("localStorage.setItem('cc-mode', 'demo')")
        page = ctx.new_page()
        started = time.monotonic()
        page.goto(URL + '#office')
        page.wait_for_selector('#office-canvas')
        pause(page, 1.5)
        page.evaluate("document.querySelector('.scene-wrap').scrollIntoView({block: 'center'})")
        pause(page, 0.8)
        box = page.locator('.scene-wrap').bounding_box()
        ids = page.evaluate("fetch('/api/state?mode=demo').then(r => r.json()).then(d => d.jobs.filter(j => j.assessment && j.assessment.status !== 'excluded').map(j => j.id))")
        go(page, '#jobs')
        page.wait_for_selector('.job-check')
        for job_id in ids[:2]:  # two complete journeys fit a short loop
            box_el = page.locator(f'.job-check[value="{job_id}"]:not([disabled])')
            if box_el.count():
                box_el.check()
        page.click('[data-action="prepare-selected"]')
        page.wait_for_function("document.querySelector('#review-count') && +document.querySelector('#review-count').textContent > 0", timeout=60000)
        pause(page, 2)
        go(page, '#office')
        page.wait_for_selector('#office-canvas')
        page.evaluate("document.querySelector('.scene-wrap').scrollIntoView({block: 'center'})")
        box = page.locator('.scene-wrap').bounding_box()
        pause(page, 1.5)
        begin = time.monotonic() - started - 0.8  # a beat of the quiet floor first
        # Sample prep finishes in under a second, so film the replay: the same event log at a watchable pace.
        page.click('[data-action="replay"]')
        # Grace → Ana → Will ∥ Rosa → Remy → you. Stop on the last delivery, before the "Replay complete" idle.
        page.wait_for_function("/Replay complete/.test(document.querySelector('#scene-caption').textContent)", timeout=90000, polling=100)
        end = time.monotonic() - started - 0.3
        ctx.close()
        browser.close()
    return box, begin, end


def encode(box, begin, end):
    raw = max((OUT / 'raw-floor').glob('*.webm'), key=lambda f: f.stat().st_mtime)
    x, y = int(box['x']) // 2 * 2, int(box['y']) // 2 * 2
    w, h = int(box['width']) // 2 * 2, int(box['height']) // 2 * 2
    crop = f'crop={w}:{h}:{x}:{y}'
    ff = imageio_ffmpeg.get_ffmpeg_exe()
    trim = ['-ss', f'{begin:.2f}', '-to', f'{end:.2f}']
    subprocess.run([ff, '-y', '-loglevel', 'error', *trim, '-i', str(raw), '-vf', f'{crop},scale=-2:720:flags=neighbor',
                    '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-crf', '20', '-movflags', '+faststart', str(OUT / 'floor.mp4')], check=True)
    vf = (f'{crop},setpts=PTS/1.2,fps=14,scale=900:-2:flags=neighbor,split[a][b];'
          '[a]palettegen=max_colors=96:stats_mode=diff[p];[b][p]paletteuse=dither=none:diff_mode=rectangle')
    subprocess.run([ff, '-y', '-loglevel', 'error', *trim, '-i', str(raw), '-vf', vf, str(OUT / 'floor.gif')], check=True)
    shutil.rmtree(OUT / 'raw-floor', ignore_errors=True)
    for f in ('floor.gif', 'floor.mp4'):
        print(f, round((OUT / f).stat().st_size / 1e6, 1), 'MB')


if __name__ == '__main__':
    server = sample_server()
    try:
        result = record()
    finally:
        server.kill()
    encode(*result)
