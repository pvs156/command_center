import asyncio
import copy
import hashlib
import shutil
import threading
import csv
import io
import json
import os
import re
import time
import zipfile
from collections import Counter
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse
from uuid import uuid4
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import career as career_mod
from . import intake as intake_mod
from . import backup, chat, demo, insights, llm, notify, pipeline, scheduler, store
from . import docx_edit
from .agents import context_signature, prepare, public_page
from .collectors import UA, board_from_url, collect
from .outreach import drafts as make_drafts
from .policy import assess, digest, freshness, priority
from .resume import parse as parse_resume, parseability
from .scoring import ats_keyword
from .tailor import render_docx, render_pdf, render_text
from .taxonomy import TAXONOMY

LOCK = asyncio.Lock()
STATIC = Path(__file__).parent / 'static'
SEED = Path(__file__).parent / 'registry_seed.json'
UPLOADS = store.ROOT / 'data' / 'uploads'
WORD_LOCK = threading.Lock()
llm.load_env()


@asynccontextmanager
async def lifespan(app):
    store.init()
    demo.seed()
    with store.connection() as c:  # packages from the pre-tailoring format can't be rendered or approved
        c.execute("DELETE FROM records WHERE kind='packages' AND json_extract(value,'$.resume') IS NULL")
    _retire_off_target()
    for run in store.all_records('runs'):
        if run['status'] == 'running':
            store.finish(run['id'], 'interrupted', error='Server restarted; completed artifacts are preserved. Start a new run to continue.')
    asyncio.get_running_loop().run_in_executor(None, _warm)
    tasks = []
    if os.getenv('COMMAND_CENTER_SCHEDULER', '1') != '0':
        tasks = [asyncio.create_task(scheduler.loop(start_collection, snapshot)),
                 asyncio.create_task(notify.loop(lambda: state('live')['inbox'], chat.names)),
                 asyncio.create_task(_daily_backup())]
    yield
    for task in tasks:
        task.cancel()


async def _daily_backup():
    await asyncio.sleep(120)
    while True:
        try:
            if backup.due():
                await asyncio.to_thread(backup.run)
        except Exception:
            pass  # a failed backup retries on the next tick
        await asyncio.sleep(1800)


def _retire_off_target():
    """Close live jobs that no longer pass the title filter, unless they are already in the pipeline."""
    from .collectors import title_matches
    keep = {p['job_id'] for p in store.all_records('packages')} | {o.get('job_id') for o in store.all_records('outcomes')}
    for j in store.all_records('jobs'):
        if not j['demo'] and not j.get('closed') and j['id'] not in keep and not title_matches(j.get('title', '')):
            j.update(closed=True, closed_reason='off-target title')
            store.put('jobs', j['id'], j)


def _warm():
    try:
        profile, cfg = profile_for(False), settings()
        for j in store.all_records('jobs'):
            if not j['demo'] and not j.get('closed'):
                checked(j, profile, cfg)
    except Exception:
        pass


app = FastAPI(title='Job Search Command Center', lifespan=lifespan)
app.add_middleware(GZipMiddleware, minimum_size=2000)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=['localhost', '127.0.0.1', 'testserver'])


@app.middleware('http')
async def local_origin(request: Request, call_next):
    if request.method not in ('GET', 'HEAD', 'OPTIONS'):
        origin = request.headers.get('origin')
        if origin and urlparse(origin).netloc != request.headers.get('host'):
            return JSONResponse({'detail': 'Cross-origin writes are not allowed'}, status_code=403)
    response = await call_next(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['Cache-Control'] = 'no-store'
    return response


def need(kind, ident):
    item = store.get(kind, ident)
    if item is None:
        raise HTTPException(404, f'{kind} item not found')
    return item


def profile_for(demo_mode):
    profile = store.get('profiles', 'demo' if demo_mode else 'real')
    if profile:
        profile['confirmed_skills'] = sorted(store.get('skills', 'demo' if demo_mode else 'real', {}).keys())
    return profile


def settings():
    s = store.get('settings', 'main')
    s.setdefault('schedule', scheduler.DEFAULT)
    return s


_ASSESS_CACHE = {}


_UNSET = object()


def checked(job, profile=_UNSET, cfg=None):
    profile = profile_for(job['demo']) if profile is _UNSET else profile
    cfg = cfg or settings()
    key = digest([job.get('content_hash'), job.get('closed'), job.get('resolutions'), job.get('company_evidence'), job.get('source_trust'),
                  (profile or {}).get('version'), (profile or {}).get('confirmed_skills'),
                  {k: cfg.get(k) for k in ('annual_min', 'hourly_min', 'hourly_review_min', 'match_min', 'review_min', 'max_experience')}])
    cached = _ASSESS_CACHE.get(key)
    if cached is None:
        cached = assess(job, profile, cfg)
        if len(_ASSESS_CACHE) > 20000:
            _ASSESS_CACHE.clear()
        _ASSESS_CACHE[key] = cached
    assessment = {**cached, 'checks': [dict(c) for c in cached['checks']]}
    # Freshness depends on the clock: recompute it on every read.
    status, label, evidence = freshness(job)
    for c in assessment['checks']:
        if c['key'] == 'freshness':
            c.update(status=status, label=label, evidence=evidence or '')
    for check in assessment['checks']:
        resolution = job.get('resolutions', {}).get(check['key'])
        if check['status'] == 'review' and resolution and check['key'] not in ('fit', 'profile', 'company'):
            check.update(status='pass', resolution=resolution, label=check['label'] + ' · reviewed')
    assessment['status'] = 'excluded' if any(c['status'] == 'excluded' for c in assessment['checks']) else 'review' if any(c['status'] == 'review' for c in assessment['checks']) else 'qualified'
    return assessment


def current_packages(demo_mode, profile, cfg):
    """Packages for this mode, each marked stale when its context signature no longer matches."""
    out = []
    for p in store.all_records('packages'):
        if p['demo'] != demo_mode:
            continue
        job = store.get('jobs', p['job_id'])
        p['stale'] = not job or not profile or p['signature'] != context_signature(job, profile, cfg)
        out.append(p)
    return out


def fresh_key(job):
    return next((c['status'] for c in job['assessment']['checks'] if c['key'] == 'freshness'), 'review')


def slim(job, profile, cfg, outcomes, full=False):
    a = checked(job, profile, cfg)
    if not full:  # the list view fetches requirements and evidence per job on demand
        a.pop('evidence', None)
        a['requirements'] = {}
        a['checks'] = [{**c, 'evidence': (c.get('evidence') or '')[:160]} for c in a['checks']]
        if a.get('pay'):
            a['pay'] = {k: v for k, v in a['pay'].items() if k != 'evidence'}
        if a.get('ats'):
            a['ats'] = {'score': a['ats'].get('score')}
    out = {k: v for k, v in job.items() if k not in ('description', 'requirements', 'content_hash', 'requirements_hash')}
    out['assessment'] = a
    out['outcome'] = outcomes.get(job['id'], {'stage': 'discovered'})
    return out


def visible(job, outcomes):
    """Hide stale excluded roles that never entered the pipeline (keeps the board fast)."""
    if job['id'] in outcomes:
        return True
    seen = job.get('last_checked_at') or job.get('first_seen_at') or ''
    return seen >= (datetime.now(timezone.utc) - timedelta(days=21)).isoformat()


@app.get('/api/state')
def state(mode: Literal['live', 'demo'] = 'live', full: bool = False, excluded: bool = False):
    demo_mode = mode == 'demo'
    profile, cfg = profile_for(demo_mode), settings()
    outcomes = {o['job_id']: o for o in store.all_records('outcomes') if 'job_id' in o}
    jobs = [slim(j, profile, cfg, outcomes, full) for j in store.all_records('jobs')
            if j['demo'] == demo_mode and not j.get('closed') and visible(j, outcomes)]
    order = {'qualified': 2, 'review': 1, 'excluded': 0}
    hidden = Counter()
    packaged = {p['job_id'] for p in store.all_records('packages') if p['demo'] == demo_mode}
    if not (full or excluded):  # excluded roles stay out of the payload unless asked for
        keep = []
        for j in jobs:
            if j['assessment']['status'] == 'excluded' and j['outcome']['stage'] == 'discovered' and j['id'] not in packaged and not j.get('manual'):
                hidden[next(c['label'] for c in j['assessment']['checks'] if c['status'] == 'excluded')] += 1
            else:
                keep.append(j)
        jobs = keep
    for j in jobs:
        j['priority'] = priority(j, j['assessment']['score'])
    jobs.sort(key=lambda j: (order[j['assessment']['status']], j['priority']), reverse=True)
    sources = store.all_records('sources')
    packages = current_packages(demo_mode, profile, cfg)
    pending = pipeline.inbox(jobs, packages, sources, profile, cfg, demo_mode)
    return {'jobs': jobs, 'inbox': pending, 'profile': profile, 'settings': cfg,
            'parse': parseability(profile) if profile and profile.get('header') else None,
            'sources': sources,
            'packages': packages,
            'batches': [b for b in store.all_records('batches') if b['demo'] == demo_mode],
            'runs': [r for r in store.all_records('runs') if r['mode'] == mode][:25],
            'models': {**llm.available(), 'claude_model': llm.claude_model(),
                       'groq_configured': llm.available()['groq'],
                       'labels': {k: llm.label(k) for k in ('gemini', 'nvidia', 'groq', 'claude')}},
            'schedule': {'config': scheduler.config(), 'next': scheduler.next_runs(), 'state': store.get('scheduler', 'state', {})},
            'agent_names': chat.names(),
            'skills_catalog': sorted(TAXONOMY), 'seed_available': SEED.exists(),
            'excluded_count': sum(hidden.values()), 'excluded_reasons': hidden.most_common(8), 'includes_excluded': bool(full or excluded),
            'mode': mode}


@app.get('/api/jobs/{job_id}')
def job_detail(job_id: str):
    job = need('jobs', job_id)
    return {**job, 'assessment': checked(job), 'outcome': store.get('outcomes', job_id, {'stage': 'discovered'}),
            'history': [h for h in store.all_records('history') if h['job_id'] == job_id]}


@app.get('/api/runs/active')
def active_runs():
    runs = store.all_records('runs')[:10]
    return {'running': [r for r in runs if r['status'] == 'running'], 'latest': runs[:3]}


@app.get('/api/events')
def events(after: int = 0, run_id: str | None = None, tail: int | None = None):
    return store.events(max(0, after), run_id, min(tail, 1000) if tail else None)


# ---------------------------------------------------------------- sources
class SourceIn(BaseModel):
    company: str = Field(min_length=1, max_length=120)
    company_url: str = Field(default='', max_length=500)
    provider: Literal['greenhouse', 'ashby', 'lever', 'smartrecruiters', 'workday'] | None = None
    board: str = Field(default='', pattern=r'^[a-zA-Z0-9_.-]{0,100}$')
    careers_url: str = Field(default='', max_length=500)
    host: str = Field(default='', pattern=r'^([\w-]+\.wd\d+\.myworkdayjobs\.com)?$')
    site: str = Field(default='', pattern=r'^[\w-]{0,100}$')


@app.post('/api/sources')
async def add_source(body: SourceIn):
    item = body.model_dump(exclude={'careers_url'})
    if body.careers_url:
        found = board_from_url(body.careers_url)
        if not found:
            raise HTTPException(400, 'Could not recognise a Greenhouse, Ashby, Lever, SmartRecruiters or Workday careers URL')
        item.update(found)
    if not item.get('provider') or not item.get('board'):
        raise HTTPException(400, 'Provide a careers URL, or a provider and board name')
    if item['provider'] == 'workday' and not (item.get('host') and item.get('site')):
        raise HTTPException(400, 'Workday needs the full careers URL (host and site)')
    item.update(id=f'{item["provider"]}:{item["board"]}', status='not_checked', enabled=True, category='custom')
    if store.get('sources', item['id']):
        raise HTTPException(409, 'That board already exists')
    async with httpx.AsyncClient(timeout=20, headers=UA) as client:
        try:
            from .collectors import _list
            raws = await _list(client, item)
        except Exception as exc:
            raise HTTPException(400, f'Board check failed ({type(exc).__name__}). Check the board name or URL.')
    item['total_open'] = len(raws)
    store.put('sources', item['id'], item)
    return item


class SeedIn(BaseModel):
    categories: list[str] = Field(default_factory=list)


@app.post('/api/sources/seed')
async def seed_sources(body: SeedIn):
    if not SEED.exists():
        raise HTTPException(404, 'No starter registry is bundled')
    added = 0
    for entry in json.loads(SEED.read_text(encoding='utf-8')):
        if body.categories and entry.get('category') not in body.categories:
            continue
        ident = f'{entry["provider"]}:{entry["board"]}'
        if store.get('sources', ident):
            continue
        store.put('sources', ident, {**entry, 'id': ident, 'status': 'not_checked', 'enabled': True})
        added += 1
    return {'added': added, 'total': len(store.all_records('sources'))}


class ToggleIn(BaseModel):
    enabled: bool


@app.post('/api/sources/{source_id}/toggle')
async def toggle_source(source_id: str, body: ToggleIn):
    s = need('sources', source_id)
    s['enabled'] = body.enabled
    store.put('sources', source_id, s)
    return s


@app.delete('/api/sources/{source_id}')
async def delete_source(source_id: str):
    need('sources', source_id)
    with store.connection() as c:
        c.execute('DELETE FROM records WHERE kind=? AND id=?', ('sources', source_id))
    return {'deleted': source_id}


# ---------------------------------------------------------------- collection
async def collect_run(run_id, auto_prepare=False):
    failures, total = [], 0
    async with LOCK:
        sources = [s for s in store.all_records('sources') if s.get('enabled', True)]
        sched = store.get('scheduler', 'state', {})
        run_started = store.now()
        # Catch-up: if the last successful search was long ago (laptop off/asleep), look back that far (24-72h).
        prev = sched.get('last_success') or sched.get('last_collect')
        gap = (datetime.now(scheduler.TZ) - datetime.fromisoformat(prev)).total_seconds() / 3600 if prev else 0
        catch_up = int(min(72, max(24, gap + 1))) if gap > 23 else 0
        sched['last_collect'] = datetime.now(scheduler.TZ).isoformat()
        store.put('scheduler', 'state', sched)
        if catch_up:
            store.event(run_id, 'scout', 'working', f'Catching up: my last search was {gap:.0f}h ago, so I am looking back {catch_up}h across {len(sources)} boards')
        else:
            store.event(run_id, 'scout', 'working', f'Checking {len(sources)} company boards for roles posted in the last 24h')
        sem = asyncio.Semaphore(6)
        async with httpx.AsyncClient(timeout=25, headers=UA) as client:
            async def one(source):
                nonlocal total
                async with sem:
                    try:
                        result = await collect(source, client)
                        total += result['count']
                        if result['count']:
                            store.event(run_id, 'scout', 'done', f'{source["company"]}: {result["count"]} matching roles', source=source['id'], **result)
                    except Exception as exc:
                        message = f'{source["company"]}: {type(exc).__name__}; check the board or try later'
                        source.update(status='error', error=message, last_checked_at=store.now())
                        store.put('sources', source['id'], source)
                        failures.append(source['company'])
                        store.event(run_id, 'scout', 'error', message, source=source['id'])
            await asyncio.gather(*(one(s) for s in sources))
        if catch_up:
            await asyncio.to_thread(_mark_catch_up, run_started, catch_up)
        if len(failures) < max(1, len(sources)) * 0.5:
            sched = store.get('scheduler', 'state', {})
            sched['last_success'] = datetime.now(scheduler.TZ).isoformat()
            store.put('scheduler', 'state', sched)
        profile, cfg = profile_for(False), settings()
        summary = await asyncio.to_thread(_summarise_live, profile, cfg)
        store.event(run_id, 'eligibility', 'done',
                    f'{total} matching roles · {summary["fresh"]} fresh in 24h · {summary["eligible"]} fully eligible',
                    failed_sources=failures[:20])
        store.finish(run_id, 'partial' if failures else 'completed', failed_sources=failures, matched=total, fresh=summary['fresh'])
    if auto_prepare and profile:
        await advance(False)


def _mark_catch_up(since, hours):
    """Jobs first seen in a catch-up run get the longer look-back for their first day."""
    for j in store.all_records('jobs'):
        if not j['demo'] and not j.get('manual') and (j.get('first_seen_at') or '') >= since and not j.get('catch_up_hours'):
            j['catch_up_hours'] = hours
            store.put('jobs', j['id'], j)


def _summarise_live(profile, cfg, demo_mode=False):
    fresh = eligible = 0
    preparable = []
    pkgs = current_packages(demo_mode, profile, cfg)
    packaged = {p['job_id'] for p in pkgs if not p['stale']}
    stale = {p['job_id'] for p in pkgs if p['stale']} - packaged
    for j in store.all_records('jobs'):
        if j['demo'] != demo_mode or j.get('closed'):
            continue
        a = checked(j, profile, cfg)
        is_fresh = next((c['status'] for c in a['checks'] if c['key'] == 'freshness'), '') == 'pass'
        fresh += is_fresh
        eligible += a['status'] == 'qualified'
        if pipeline.preparable(j, a, cfg, j['id'] in packaged, refresh=j['id'] in stale):
            preparable.append((priority(j, a['score']), j['id']))
    preparable = [i for _, i in sorted(preparable, reverse=True)]  # best match + newest first
    return {'fresh': fresh, 'eligible': eligible, 'preparable': preparable}


async def advance(demo_mode):
    """Move every qualifying role to the next stage so nothing waits silently."""
    profile = profile_for(demo_mode)
    if not profile or LOCK.locked():
        return None
    todo = (await asyncio.to_thread(_summarise_live, profile, settings(), demo_mode))['preparable']
    if not todo:
        return None
    ident = store.new_run('demo' if demo_mode else 'live', f'Tailor {len(todo)} qualifying role{"s" if len(todo) != 1 else ""} (auto)')
    store.event(ident, 'writer', 'working', f'{len(todo)} roles meet your criteria; tailoring them now')
    await prepare_run(ident, todo)
    return ident


async def start_collection(auto_prepare=False, trigger='manual'):
    if LOCK.locked():
        return None
    if not any(s.get('enabled', True) for s in store.all_records('sources')):
        return None
    ident = store.new_run('live', f'Collect company boards ({trigger})')
    asyncio.create_task(collect_run(ident, auto_prepare))
    return ident


class CollectIn(BaseModel):
    auto_prepare: bool = False


@app.post('/api/collect')
async def start_collect(background: BackgroundTasks, body: CollectIn | None = None):
    if not any(s.get('enabled', True) for s in store.all_records('sources')):
        raise HTTPException(400, 'Add or enable a company board in Sources first')
    if LOCK.locked():
        raise HTTPException(409, 'A collection or preparation run is already active')
    ident = store.new_run('live', 'Collect company boards (manual)')
    background.add_task(collect_run, ident, bool(body and body.auto_prepare))
    return {'run_id': ident}


# ---------------------------------------------------------------- profile
class ProfileIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    text: str = Field(min_length=20, max_length=80000)
    confirmed: bool
    upload_id: str | None = Field(default=None, pattern=r'^[0-9a-f]{16}$')
    filename: str | None = Field(default=None, max_length=200)


@app.post('/api/profile/preview')
async def preview(file: UploadFile):
    data = await file.read(2_000_001)
    if len(data) > 2_000_000:
        raise HTTPException(413, 'Resume is larger than 2 MB')
    filename = (file.filename or '').lower()
    upload_id = None
    try:
        if filename.endswith('.docx'):
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                if sum(x.file_size for x in z.infolist()) > 10_000_000:
                    raise ValueError('Document expands beyond limit')
            from docx import Document
            doc = Document(io.BytesIO(data))
            lines = []
            for p in doc.paragraphs:
                if not p.text.strip():
                    continue
                style = (p.style.name or '').lower() if p.style is not None else ''
                is_list = 'list' in style or p._p.pPr is not None and p._p.pPr.numPr is not None
                lines.append(('- ' if is_list and not p.text.lstrip().startswith(('-', '•')) else '') + p.text.strip())
            for table in doc.tables:
                for row in table.rows:
                    lines.append(' | '.join(c.text for c in row.cells))
            text = '\n'.join(lines)
            UPLOADS.mkdir(parents=True, exist_ok=True)
            upload_id = hashlib.sha256(data).hexdigest()[:16]
            (UPLOADS / f'pending-{upload_id}.docx').write_bytes(data)
        elif filename.endswith('.txt') or filename.endswith('.md'):
            text = data.decode('utf-8-sig')
        elif filename.endswith('.pdf'):
            try:
                from pypdf import PdfReader
            except ImportError:
                raise ValueError('PDF import needs pypdf; use DOCX or TXT')
            text = '\n'.join(page.extract_text() or '' for page in PdfReader(io.BytesIO(data)).pages)
        else:
            raise ValueError('Use DOCX, TXT or PDF')
    except Exception as exc:
        raise HTTPException(400, f'Could not parse resume: {exc if isinstance(exc, ValueError) else type(exc).__name__}')
    parsed = parse_resume(text)
    return {'text': text, 'upload_id': upload_id, 'filename': file.filename,
            'notice': ('Your Word file is kept as the base: tailored resumes will be edits of this exact document. ' if upload_id else
                       'Tip: upload your DOCX so tailored resumes keep your exact formatting. ') + 'Check the text, then confirm.',
            'preview': {'roles': [r['header'] for r in parsed['roles']], 'years': parsed['years_experience'],
                        'skills': parsed['resume_skills'], 'parse': parseability(parsed)}}


@app.post('/api/profile')
async def save_profile(body: ProfileIn):
    if not body.confirmed:
        raise HTTPException(400, 'Confirm the extracted resume facts before scoring')
    profile = parse_resume(body.text, body.name)
    if not profile['facts']:
        raise HTTPException(400, 'No resume lines were found')
    profile.update(confirmed=True, demo=False, source_text=body.text, updated_at=store.now(),
                   version=digest({'name': profile['name'], 'facts': profile['facts']})[:16])
    old = store.get('profiles', 'real') or {}
    pending = UPLOADS / f'pending-{body.upload_id}.docx' if body.upload_id else None
    if pending and pending.exists():
        base = UPLOADS / f'base-{profile["version"]}.docx'
        shutil.copyfile(pending, base)
        profile.update(docx=base.name, docx_name=body.filename or 'resume.docx')
    elif old.get('docx') and (UPLOADS / old['docx']).exists():
        profile.update(docx=old['docx'], docx_name=old.get('docx_name'))  # same Word file, text re-confirmed
    if profile.get('docx'):
        from docx import Document
        mapped = docx_edit.map_facts(list(docx_edit.all_paragraphs(Document(UPLOADS / profile['docx']))), profile)
        profile['docx_coverage'] = round(100 * len(mapped) / max(1, len(profile['facts'])))
    store.put('profiles', 'real', profile)
    asyncio.create_task(advance(False))
    return profile


class SkillIn(BaseModel):
    skill: str = Field(min_length=1, max_length=60)
    note: str = Field(default='', max_length=500)
    mode: Literal['live', 'demo'] = 'live'


@app.post('/api/skills')
async def confirm_skill(body: SkillIn):
    if body.skill not in TAXONOMY:
        raise HTTPException(400, 'Unknown skill')
    key = 'demo' if body.mode == 'demo' else 'real'
    skills = store.get('skills', key, {})
    skills[body.skill] = {'note': body.note, 'confirmed_at': store.now()}
    store.put('skills', key, skills)
    asyncio.create_task(advance(body.mode == 'demo'))
    return {'confirmed_skills': sorted(skills)}


@app.delete('/api/skills/{skill}')
async def remove_skill(skill: str, mode: Literal['live', 'demo'] = 'live'):
    key = 'demo' if mode == 'demo' else 'real'
    skills = store.get('skills', key, {})
    skills.pop(skill, None)
    store.put('skills', key, skills)
    asyncio.create_task(advance(mode == 'demo'))
    return {'confirmed_skills': sorted(skills)}


# ---------------------------------------------------------------- settings
class ScheduleIn(BaseModel):
    enabled: bool
    interval_minutes: int = Field(default=60, ge=15, le=1440)
    auto_prepare: bool
    weekly_day: Literal['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun']
    weekly_time: str = Field(pattern=r'^([01]\d|2[0-3]):[0-5]\d$')


class SettingsIn(BaseModel):
    hourly_min: float = Field(ge=40, le=500)
    provider: Literal['extractive', 'claude', 'groq', 'gemini', 'nvidia']
    match_min: int | None = Field(default=None, ge=40, le=95)
    review_min: int | None = Field(default=None, ge=30, le=90)
    schedule: ScheduleIn | None = None
    agent_names: dict[str, str] | None = None
    notifications: bool | None = None
    backup: bool | None = None


@app.post('/api/settings')
async def save_settings(body: SettingsIn):
    if body.provider != 'extractive' and not llm.available()[body.provider]:
        env = {'claude': 'ANTHROPIC_API_KEY', 'groq': 'GROQ_API_KEY', 'gemini': 'GEMINI_API_KEY', 'nvidia': 'NVIDIA_API_KEY'}[body.provider]
        raise HTTPException(400, f'Set {env} in .env first')
    cfg = settings()
    cfg.update(hourly_min=body.hourly_min, provider=body.provider, hourly_confirmed=True)
    if body.match_min is not None:
        cfg['match_min'] = body.match_min
    if body.review_min is not None:
        cfg['review_min'] = min(body.review_min, cfg['match_min'])
    if body.schedule:
        cfg['schedule'] = body.schedule.model_dump()
    for key in ('notifications', 'backup'):
        if getattr(body, key) is not None:
            cfg[key] = getattr(body, key)
    if body.agent_names is not None:
        clean = {k: v.strip()[:20] for k, v in body.agent_names.items() if k in chat.DEFAULT_NAMES and k != 'lead' and v.strip()}
        cfg['agent_names'] = clean
    store.put('settings', 'main', cfg)
    asyncio.create_task(advance(False))
    return cfg


# ---------------------------------------------------------------- preparation
class PrepareIn(BaseModel):
    job_ids: list[str] = Field(min_length=1)


async def prepare_run(run_id, ids):
    async with LOCK:
        errors = []
        for ident in dict.fromkeys(ids):
            job = store.get('jobs', ident)
            try:
                if not job:
                    raise ValueError('Job not found')
                profile = profile_for(job['demo'])
                had = any(p['job_id'] == ident for p in store.all_records('packages'))  # refreshing a stale package
                await prepare(job, profile, settings(), checked(job, profile), run_id, refresh=had)
            except Exception as exc:
                message = str(exc) if isinstance(exc, ValueError) else f'{type(exc).__name__}: preparation failed'
                errors.append({'job_id': ident, 'error': message})
                store.event(run_id, 'reviewer', 'error', message, job_id=ident)
        store.finish(run_id, 'partial' if errors else 'completed', errors=errors)


@app.post('/api/prepare')
async def start_prepare(body: PrepareIn, background: BackgroundTasks):
    jobs = [need('jobs', j) for j in body.job_ids]
    if len({j['demo'] for j in jobs}) != 1:
        raise HTTPException(400, 'Sample and live jobs cannot be mixed')
    if LOCK.locked():
        raise HTTPException(409, 'A run is already active')
    mode = 'demo' if jobs[0]['demo'] else 'live'
    ident = store.new_run(mode, f'Prepare {len(set(body.job_ids))} application packages')
    background.add_task(prepare_run, ident, body.job_ids)
    return {'run_id': ident}


class ResolutionIn(BaseModel):
    key: Literal['country', 'experience', 'employment', 'pay', 'freshness', 'sponsorship']
    note: str = Field(min_length=15, max_length=1500)
    source_url: str = Field(min_length=8, max_length=1000)


@app.post('/api/jobs/{job_id}/resolve')
async def resolve(job_id: str, body: ResolutionIn):
    job = need('jobs', job_id)
    check = next((c for c in checked(job)['checks'] if c['key'] == body.key), None)
    if not check or check['status'] != 'review':
        raise HTTPException(400, 'Only unresolved review checks can be documented; exclusions cannot be overridden')
    if urlparse(body.source_url).scheme != 'https':
        raise HTTPException(400, 'Provide an HTTPS evidence URL')
    job.setdefault('resolutions', {})[body.key] = {**body.model_dump(), 'reviewed_at': store.now()}
    store.put('jobs', job_id, job)
    return {'assessment': checked(job)}


class CompanyIn(BaseModel):
    official_url: str = Field(min_length=10, max_length=1000)
    people_url: str = Field(min_length=10, max_length=1000)
    note: str = Field(min_length=30, max_length=3000)
    confirmed: bool


@app.post('/api/jobs/{job_id}/company')
async def company_review(job_id: str, body: CompanyIn):
    job = need('jobs', job_id)
    if not body.confirmed or any(urlparse(u).scheme != 'https' for u in [body.official_url, body.people_url]):
        raise HTTPException(400, 'Confirm that you checked the company, actual people, and original posting; use HTTPS evidence URLs')
    job['company_evidence'] = {**body.model_dump(exclude={'confirmed'}), 'reviewed_at': store.now(), 'method': 'User-reviewed evidence'}
    store.put('jobs', job_id, job)
    return {'assessment': checked(job)}


class ResearchIn(BaseModel):
    url: str = Field(max_length=1000)


@app.post('/api/research/page')
async def research_page(body: ResearchIn):
    try:
        return await public_page(body.url)
    except Exception as exc:
        raise HTTPException(400, f'Page could not be read ({type(exc).__name__}); use the original source in your browser')


class ContactIn(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    role: str = Field(min_length=2, max_length=150)
    url: str = Field(min_length=10, max_length=1000)
    relevance: str = Field(min_length=10, max_length=1000)
    persona: str = Field(default='recruiter', max_length=30)


@app.post('/api/jobs/{job_id}/contacts')
async def contact(job_id: str, body: ContactIn):
    if urlparse(body.url).scheme != 'https':
        raise HTTPException(400, 'Use a public professional source URL')
    job = need('jobs', job_id)
    contacts = job.setdefault('contacts', [])
    contacts.append({**body.model_dump(), 'added_at': store.now(), 'method': 'User-supplied professional source'})
    store.put('jobs', job_id, job)
    for p in store.all_records('packages'):  # contacts don't change the resume; refresh drafts in place
        if p['job_id'] == job_id:
            p['contacts'] = contacts
            store.put('packages', p['id'], p)
    return {'contacts': contacts}


# ---------------------------------------------------------------- packages
class DraftIn(BaseModel):
    key: Literal['connection', 'recruiter', 'manager', 'referral', 'followup']
    text: str = Field(min_length=1, max_length=5000)


@app.put('/api/packages/{package_id}/drafts')
async def save_draft(package_id: str, body: DraftIn):
    p = need('packages', package_id)
    p['drafts'][body.key] = body.text
    store.put('packages', package_id, p)
    return p['drafts']


class PersonalizeIn(BaseModel):
    contact_index: int = Field(ge=0)


@app.post('/api/packages/{package_id}/personalize')
async def personalize(package_id: str, body: PersonalizeIn):
    p = need('packages', package_id)
    job = need('jobs', p['job_id'])
    contacts = job.get('contacts', [])
    if body.contact_index >= len(contacts):
        raise HTTPException(400, 'Add that contact first')
    profile = profile_for(job['demo'])
    provider = 'extractive' if job['demo'] else settings().get('provider', 'extractive')
    if provider != 'extractive':
        from .career import load as load_career
        from .outreach import llm_drafts
        p['drafts'], p['drafts_rejected'] = await llm_drafts(provider, job, profile, checked(job, profile), load_career(), contacts[body.contact_index])
    else:
        p['drafts'] = make_drafts(job, profile, p['resume'], checked(job, profile)['requirements'], contacts[body.contact_index])
    p['drafts_for'] = contacts[body.contact_index]['name']
    store.put('packages', package_id, p)
    return p['drafts']


class ResumeEditIn(BaseModel):
    summary: str | None = Field(default=None, max_length=1500)
    bullets: dict[str, str] = Field(default_factory=dict)


@app.put('/api/packages/{package_id}/resume')
async def edit_resume(package_id: str, body: ResumeEditIn):
    p = need('packages', package_id)
    if any(p['id'] in b['package_ids'] for b in store.all_records('batches')):
        raise HTTPException(409, 'This package is already approved; prepare it again to edit')
    r = p['resume']
    if body.summary is not None:
        r['summary'] = body.summary.strip()
    for s in r['sections']:
        for role in s['roles']:
            for b in role['bullets']:
                if b['id'] in body.bullets and body.bullets[b['id']].strip():
                    b['text'] = body.bullets[b['id']].strip()
                    b['changed'] = b['text'] != b['original']
                    b['edited_by_user'] = True
    job = need('jobs', p['job_id'])
    a = checked(job)
    r['text'] = render_text(r)
    r['ats'] = ats_keyword(a['requirements'], r['text'], job['title'], (a.get('ats') or {}).get('parse') or 70)
    p['scores']['ats_tailored'] = r['ats']['score']
    store.put('packages', package_id, p)
    return p


class OutreachLogIn(BaseModel):
    contact: str = Field(min_length=1, max_length=120)
    channel: Literal['linkedin', 'email', 'referral', 'other']
    status: Literal['sent', 'replied', 'no_response', 'referred']
    note: str = Field(default='', max_length=1000)


@app.post('/api/packages/{package_id}/outreach')
async def log_outreach(package_id: str, body: OutreachLogIn):
    p = need('packages', package_id)
    p.setdefault('outreach_log', []).append({**body.model_dump(), 'at': store.now(), 'recorded_by': 'user'})
    store.put('packages', package_id, p)
    return p['outreach_log']


class BatchIn(BaseModel):
    package_ids: list[str] = Field(min_length=1)
    acknowledged: bool


def valid_package(p):
    job = need('jobs', p['job_id'])
    profile = profile_for(job['demo'])
    if not profile or p['signature'] != context_signature(job, profile, settings()):
        raise HTTPException(409, 'Source, resume, settings or evidence changed. Prepare this package again.')
    a = checked(job, profile)
    # Freshness gates discovery and preparation; a package prepared in time stays approvable.
    if any(c['status'] != 'pass' for c in a['checks'] if c['key'] != 'freshness'):
        raise HTTPException(409, 'Every eligibility check must pass or have documented review before approval')
    return job


@app.post('/api/batches')
async def approve_batch(body: BatchIn):
    if not body.acknowledged:
        raise HTTPException(400, 'Confirm review of the exact packages and any unstated sponsorship')
    async with LOCK:
        packages = [need('packages', p) for p in dict.fromkeys(body.package_ids)]
        if len({p['demo'] for p in packages}) != 1:
            raise HTTPException(400, 'Cannot combine sample and live packages')
        for p in packages:
            valid_package(p)
        ident = digest(sorted(p['id'] for p in packages))[:20]
        existing = store.get('batches', ident)
        if existing:
            return existing
        batch = {'id': ident, 'demo': packages[0]['demo'], 'package_ids': [p['id'] for p in packages],
                 'approved_at': store.now(), 'status': 'approved', 'submitted_count': 0,
                 'notice': 'Approved for use. No application or message has been sent.'}
        store.put('batches', ident, batch)
        for p in packages:
            old = store.get('outcomes', p['job_id'])
            if not old or old['stage'] in ('discovered', 'ready'):
                store.put('outcomes', p['job_id'], {'job_id': p['job_id'], 'stage': 'ready', 'at': store.now(), 'package_id': p['id'], 'batch_id': ident})
        store.event('batch:' + ident, 'lead', 'done', f'Approved {len(packages)} packages; none submitted', batch_id=ident)
        return batch


class OutcomeIn(BaseModel):
    stage: Literal['applied', 'replied', 'screening', 'interviewing', 'offer', 'rejected', 'withdrawn']
    note: str = Field(default='', max_length=2000)
    follow_up: str | None = Field(default=None, pattern=r'^\d{4}-\d{2}-\d{2}$')


@app.post('/api/jobs/{job_id}/outcome')
async def outcome(job_id: str, body: OutcomeIn):
    need('jobs', job_id)
    old = store.get('outcomes', job_id)
    if not old or old['stage'] == 'discovered':
        raise HTTPException(409, 'Approve a package before recording an application outcome')
    value = {**old, **body.model_dump(exclude_none=True), 'job_id': job_id, 'at': store.now()}
    if body.stage == 'applied' and not old.get('applied_at'):
        value['applied_at'] = store.now()
    store.put('outcomes', job_id, value)
    store.put('history', uuid4().hex, {'job_id': job_id, **value, 'recorded_by': 'user'})
    return value


def base_docx(p):
    """Your uploaded Word file, if this package was tailored from the same resume version."""
    profile = profile_for(p['demo'])
    if profile and profile.get('docx') and profile.get('version') == p.get('profile_version'):
        path = UPLOADS / profile['docx']
        if path.exists():
            return path, profile
    return None, profile


def package_docx(p, highlight=False):
    path, profile = base_docx(p)
    if path:
        data, report = docx_edit.build(path, profile, p['resume'], highlight)
        return data, {**report, 'source': 'your Word file', 'docx_name': profile.get('docx_name')}
    return render_docx(p['resume'], p['demo'], highlight), {'source': 'generated', 'edits': [], 'failed': []}


def word_to_pdf(docx_bytes):
    """Convert with Microsoft Word itself so the PDF matches your document exactly."""
    import tempfile
    import pythoncom
    import win32com.client
    with WORD_LOCK, tempfile.TemporaryDirectory() as tmp:
        src, dst = Path(tmp) / 'resume.docx', Path(tmp) / 'resume.pdf'
        src.write_bytes(docx_bytes)
        pythoncom.CoInitialize()
        word = None
        try:
            word = win32com.client.DispatchEx('Word.Application')
            word.Visible = False
            word.DisplayAlerts = 0
            doc = word.Documents.Open(str(src), ReadOnly=True)
            doc.SaveAs2(str(dst), FileFormat=17)
            doc.Close(False)
        finally:
            if word is not None:
                try:
                    word.Quit(0)
                except Exception:
                    pass  # Word sometimes exits on its own once the document is closed
            pythoncom.CoUninitialize()
        return dst.read_bytes()


@app.get('/api/packages/{package_id}/export')
async def export_package(package_id: str, format: Literal['docx', 'pdf', 'json', 'txt'] = 'docx', highlight: bool = False):
    p = need('packages', package_id)
    company = re.sub(r'[^\w-]+', '-', (store.get('jobs', p['job_id']) or {}).get('company', 'company')).strip('-')
    name = re.sub(r'[^\w-]+', '-', p['profile_name']).strip('-')
    if format == 'json':
        return Response(json.dumps(p, indent=2), media_type='application/json', headers={'Content-Disposition': f'attachment; filename="{name}-{company}-package.json"'})
    if format == 'txt':
        body = '\n\n'.join(f'== {k.upper()} ==\n{v}' for k, v in p['drafts'].items())
        return Response(body, media_type='text/plain', headers={'Content-Disposition': f'attachment; filename="{name}-{company}-outreach.txt"'})
    data, report = await asyncio.to_thread(package_docx, p, highlight)
    suffix = '-HIGHLIGHTED-review-copy' if highlight else ''
    if format == 'docx':
        return Response(data, media_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                        headers={'Content-Disposition': f'attachment; filename="{name}-{company}-resume{suffix}.docx"'})
    try:
        pdf = await asyncio.to_thread(word_to_pdf, data)
    except Exception:
        pdf = render_pdf(p['resume'], p['demo'])  # Word unavailable: simple layout, same content and order
    return Response(pdf, media_type='application/pdf', headers={'Content-Disposition': f'attachment; filename="{name}-{company}-resume{suffix}.pdf"'})


@app.get('/api/packages/{package_id}/preview')
async def package_preview(package_id: str):
    p = need('packages', package_id)
    data, report = await asyncio.to_thread(package_docx, p, True)
    return {'paragraphs': docx_edit.preview(data), 'report': report}


# ---------------------------------------------------------------- insights
def insight_data(mode):
    data = state(mode, full=True)
    weekly = insights.weekly(data['jobs'], data['profile'])
    history = [h for h in store.all_records('history') if (store.get('jobs', h['job_id']) or {}).get('demo') == (mode == 'demo')]
    return weekly, insights.outcomes(data['jobs'], data['packages'], history)


@app.get('/api/insights')
def get_insights(mode: Literal['live', 'demo'] = 'live'):
    weekly, outs = insight_data(mode)
    snaps = sorted((s for s in store.all_records('snapshots') if s['mode'] == mode), key=lambda s: s['generated_at'], reverse=True)
    return {'weekly': weekly, 'outcomes': outs, 'snapshots': [{'id': s['id'], 'generated_at': s['generated_at']} for s in snaps[:12]]}


def snapshot(mode='live'):
    weekly, outs = insight_data(mode)
    ident = uuid4().hex[:12]
    store.put('snapshots', ident, {'id': ident, 'mode': mode, 'generated_at': store.now(), 'weekly': weekly, 'outcomes': outs})
    store.event('report:' + ident, 'lead', 'waiting', 'Weekend report ready: market demand, gaps and project ideas', snapshot=ident)
    return ident


@app.post('/api/insights/snapshot')
def make_snapshot(mode: Literal['live', 'demo'] = 'live'):
    return {'id': snapshot(mode)}


@app.get('/api/insights/snapshots/{ident}')
def get_snapshot(ident: str):
    return need('snapshots', ident)


# Kept for older clients; the full report lives at /api/insights.
@app.get('/api/report')
def report(mode: Literal['live', 'demo'] = 'live'):
    weekly, outs = insight_data(mode)
    return {**weekly, 'outcomes': outs}


@app.get('/api/tracker.csv')
def tracker(mode: Literal['live', 'demo'] = 'live'):
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(['company', 'title', 'location', 'url', 'stage', 'base_match', 'ats_keywords', 'sponsorship', 'source', 'applied_at', 'follow_up', 'note', 'updated_at'])
    for j in state(mode, full=True)['jobs']:
        if j['outcome']['stage'] == 'discovered':
            continue
        o, a = j['outcome'], j['assessment']
        cells = [j['company'], j['title'], j['location'], j['url'], o['stage'], a['score'], (a.get('ats') or {}).get('score'),
                 a['sponsorship']['label'], j.get('provider'), o.get('applied_at', ''), o.get('follow_up', ''), o.get('note', ''), o.get('at', '')]
        writer.writerow(["'" + str(v) if str(v).startswith(('=', '+', '-', '@')) else v for v in cells])
    return Response(out.getvalue(), media_type='text/csv', headers={'Content-Disposition': 'attachment; filename="job-tracker.csv"'})


# ---------------------------------------------------------------- career profile
@app.get('/api/career')
def get_career():
    data = career_mod.load()
    return {'markdown': career_mod.markdown(), 'summary': career_mod.summarize(data), 'career': data,
            'path': str(career_mod.MD)}


class CareerIn(BaseModel):
    markdown: str = Field(min_length=20, max_length=400000)


@app.put('/api/career')
async def save_career(body: CareerIn):
    result = career_mod.save_markdown(body.markdown)
    asyncio.create_task(advance(False))  # packages built on the old profile are re-tailored
    return {**result, 'markdown': career_mod.markdown()}


@app.post('/api/career/import')
async def import_career(file: UploadFile, replace: bool = True):
    data = await file.read(5_000_001)
    if len(data) > 5_000_000:
        raise HTTPException(413, 'File is larger than 5 MB')
    name = (file.filename or '').lower()
    try:
        if name.endswith('.docx'):
            md = career_mod.docx_to_markdown(io.BytesIO(data))
        elif name.endswith(('.md', '.txt')):
            md = data.decode('utf-8-sig')
        elif name.endswith('.json'):
            md = '```json\n' + data.decode('utf-8-sig') + '\n```\n'
        else:
            raise ValueError('Use .docx, .md, .txt or .json')
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:
        raise HTTPException(400, f'Could not read the file ({type(exc).__name__})')
    if not replace and career_mod.markdown():
        md = career_mod.markdown().rstrip() + '\n\n' + md
    result = career_mod.save_markdown(md)
    asyncio.create_task(advance(False))
    return {**result, 'markdown': career_mod.markdown()}


# ---------------------------------------------------------------- Ivy: bring in a job yourself
class IntakeIn(BaseModel):
    url: str | None = Field(default=None, max_length=2000)
    text: str | None = Field(default=None, max_length=60000)
    title: str = Field(default='', max_length=200)
    company: str = Field(default='', max_length=200)
    location: str = Field(default='', max_length=200)
    apply_url: str = Field(default='', max_length=2000)
    mode: Literal['live', 'demo'] = 'live'


async def intake_run(run_id, body):
    """Ivy reads the posting, Grace checks it, then the usual team tailors it onto your review desk."""
    demo_mode = body.mode == 'demo'
    what = body.url or 'the pasted job description'
    store.event(run_id, 'intake', 'working', f'Reading {what[:90]}')
    try:
        job = await intake_mod.intake(url=body.url, text=body.text, title=body.title, company=body.company,
                                      location=body.location, apply_url=body.apply_url, demo=demo_mode)
    except intake_mod.IntakeError as exc:
        store.event(run_id, 'intake', 'error', str(exc))
        store.finish(run_id, 'failed', error=str(exc))
        return
    except Exception as exc:
        store.event(run_id, 'intake', 'error', f"Couldn't read that posting ({type(exc).__name__}). Paste the job description text instead.")
        store.finish(run_id, 'failed', error=type(exc).__name__)
        return
    old = store.get('jobs', job['id'])
    if old:
        for key in ('company_evidence', 'resolutions', 'contacts', 'first_seen_at'):
            if key in old:
                job[key] = old[key]
    job.pop('skipped', None)
    store.put('jobs', job['id'], job)
    reqs = len(job.get('requirements') or {})
    store.event(run_id, 'intake', 'done', f'Got it: {job["title"]} at {job["company"]} ({reqs} skills recognised, via {job.get("via")}). Handing to Grace.',
                job_id=job['id'], via=job.get('via'))
    profile = profile_for(demo_mode)
    if not profile:
        store.event(run_id, 'analyst', 'waiting', 'I need your base resume before I can score and tailor this job', job_id=job['id'])
        store.finish(run_id, 'partial', job_id=job['id'], error='No base resume yet')
        return
    if LOCK.locked():
        store.event(run_id, 'eligibility', 'waiting', f'Queued behind {chat.name_of("scout")}\'s current search; I\'ll pick this up the moment it finishes (usually under 2 minutes).', job_id=job['id'])
    async with LOCK:
        try:
            a = checked(job, profile)
            hard = [c['label'] for c in a['checks'] if c['status'] == 'excluded']
            if hard:
                store.event(run_id, 'eligibility', 'waiting', f'Outside your rules: {"; ".join(hard)}. Tailoring anyway since you chose it; flagged on the card.', job_id=job['id'])
            await prepare(job, profile, settings(), a, run_id, refresh=True)
            store.finish(run_id, 'completed', job_id=job['id'])
        except Exception as exc:
            message = str(exc) if isinstance(exc, ValueError) else f'{type(exc).__name__}: tailoring failed'
            store.event(run_id, 'reviewer', 'error', message, job_id=job['id'])
            store.finish(run_id, 'partial', job_id=job['id'], error=message)


@app.post('/api/intake')
async def start_intake(body: IntakeIn, background: BackgroundTasks):
    if not (body.url or (body.text and body.text.strip())):
        raise HTTPException(400, 'Paste a job link or the job description text')
    if body.url and not re.match(r'https?://', body.url.strip()):
        raise HTTPException(400, 'Links must start with https://')
    ident = store.new_run(body.mode, f'Intake: {(body.url or body.title or "pasted job description")[:80]}')
    background.add_task(intake_run, ident, body)
    return {'run_id': ident}


@app.get('/api/jobs/{job_id}/jd')
def download_jd(job_id: str):
    job = need('jobs', job_id)
    a = checked(job)
    head = [f'{job["title"]}', f'{job["company"]} · {job.get("location") or "Location not stated"} · {job.get("employment_type") or ""}'.strip(' ·'),
            f'Posted: {job.get("published_at") or "not published"}', f'Link: {job.get("url") or job.get("apply_url") or "pasted text"}',
            f'Base-resume match: {a["score"] if a["score"] is not None else "n/a"}%', '', 'JOB DESCRIPTION', '']
    name = re.sub(r'[^\w-]+', '-', f'{job["company"]}-{job["title"]}').strip('-')[:80]
    return Response('\n'.join(head) + (job.get('description') or ''), media_type='text/plain; charset=utf-8',
                    headers={'Content-Disposition': f'attachment; filename="{name}-JD.txt"'})


@app.post('/api/notify/test')
def test_notification():
    ok = notify.toast(f'{chat.name_of("reviewer")} says hi', 'Notifications are working. You will hear from me when new jobs are ready.')
    return {'shown': ok}


@app.post('/api/backup')
async def backup_now():
    dest = await asyncio.to_thread(backup.run)
    return {'path': str(dest)}


@app.get('/api/runs/{run_id}')
def get_run(run_id: str):
    return need('runs', run_id)


# ---------------------------------------------------------------- inbox decisions
@app.get('/api/inbox')
def get_inbox(mode: Literal['live', 'demo'] = 'live'):
    return state(mode)['inbox']


class AppliedIn(BaseModel):
    acknowledged_flags: list[str] = Field(default_factory=list)
    note: str = Field(default='', max_length=2000)
    follow_up: str | None = Field(default=None, pattern=r'^\d{4}-\d{2}-\d{2}$')


@app.post('/api/packages/{package_id}/applied')
async def mark_applied(package_id: str, body: AppliedIn):
    """You applied on the employer's site with this package. Records approval + application."""
    p = store.get('packages', package_id)
    if p is None:
        raise HTTPException(409, 'This package was just re-tailored. The fresh version is on your review desk.')
    job = need('jobs', p['job_id'])
    profile = profile_for(job['demo'])
    if not profile or p['signature'] != context_signature(job, profile, settings()):
        asyncio.create_task(advance(job['demo']))
        raise HTTPException(409, 'Your resume, confirmed skills or the posting changed since this was tailored. The Writer is re-tailoring it now; it will be back on your desk shortly.')
    a = checked(job, profile)
    if not job.get('manual') and any(c['status'] == 'excluded' and c['key'] != 'freshness' for c in a['checks']):
        raise HTTPException(409, 'This role is now excluded by your rules.')
    needs = [c['key'] for c in a['checks'] if c['status'] == 'review' or (job.get('manual') and c['status'] == 'excluded')]
    unacked = [k for k in needs if k not in body.acknowledged_flags]
    if unacked:
        raise HTTPException(400, 'Tick each flag to confirm you checked it: ' + ', '.join(unacked))
    ident = digest([p['id']])[:20]
    if not store.get('batches', ident):
        store.put('batches', ident, {'id': ident, 'demo': p['demo'], 'package_ids': [p['id']], 'approved_at': store.now(),
                                     'status': 'approved', 'submitted_count': 0, 'notice': 'Approved by applying from the inbox.'})
    old = store.get('outcomes', job['id']) or {}
    follow = body.follow_up or (datetime.now(timezone.utc) + timedelta(days=6)).date().isoformat()
    value = {**old, 'job_id': job['id'], 'stage': 'applied', 'at': store.now(), 'package_id': p['id'], 'batch_id': ident,
             'applied_at': old.get('applied_at') or store.now(), 'flags_acknowledged': body.acknowledged_flags,
             'note': body.note, 'follow_up': follow}
    store.put('outcomes', job['id'], value)
    store.put('history', uuid4().hex, {**value, 'recorded_by': 'user'})
    store.event('batch:' + ident, 'lead', 'done', f'You applied: {job["company"]} · {job["title"]}. Follow-up set for {follow}.', job_id=job['id'])
    return value


class SkipIn(BaseModel):
    reason: str = Field(default='Not interested', max_length=300)


@app.post('/api/jobs/{job_id}/skip')
async def skip_job(job_id: str, body: SkipIn):
    job = need('jobs', job_id)
    job['skipped'] = {'at': store.now(), 'reason': body.reason}
    store.put('jobs', job_id, job)
    store.event('batch:skip', 'reviewer', 'done', f'Skipped {job["company"]} · {job["title"]}: {body.reason}', job_id=job_id)
    return {'skipped': job_id}


@app.post('/api/jobs/{job_id}/unskip')
async def unskip_job(job_id: str):
    job = need('jobs', job_id)
    job.pop('skipped', None)
    store.put('jobs', job_id, job)
    return {'unskipped': job_id}


@app.post('/api/jobs/{job_id}/tailor-anyway')
async def tailor_anyway(job_id: str, background: BackgroundTasks):
    job = need('jobs', job_id)
    a = checked(job)
    cfg = settings()
    if a['score'] is None or a['score'] < cfg['review_min']:
        raise HTTPException(400, f'Below {cfg["review_min"]}% is skipped by your rules')
    if any(c['status'] == 'excluded' for c in a['checks'] if c['key'] != 'fit'):
        raise HTTPException(400, 'This role is excluded by a hard rule')
    job['fit_override'] = {'at': store.now(), 'score': a['score']}
    store.put('jobs', job_id, job)
    if LOCK.locked():
        return {'queued': True, 'notice': 'Another run is active; the next pipeline pass will tailor it.'}
    ident = store.new_run('demo' if job['demo'] else 'live', f'Tailor {job["company"]} (your call)')
    background.add_task(prepare_run, ident, [job_id])
    return {'run_id': ident}


@app.post('/api/advance')
async def run_advance(mode: Literal['live', 'demo'] = 'live'):
    if LOCK.locked():
        return {'queued': True}
    ident = store.new_run(mode, 'Pipeline pass')
    store.finish(ident, 'completed')
    asyncio.create_task(advance(mode == 'demo'))
    return {'started': True}


@app.post('/api/sources/pause-failing')
async def pause_failing():
    paused = []
    for s in store.all_records('sources'):
        if s.get('status') == 'error' and s.get('enabled', True):
            s['enabled'] = False
            store.put('sources', s['id'], s)
            paused.append(s['company'])
    return {'paused': paused}


@app.post('/api/jobs/{job_id}/followed-up')
async def followed_up(job_id: str):
    o = store.get('outcomes', job_id)
    if not o:
        raise HTTPException(404, 'No application recorded')
    o.update(follow_up=None, followed_up_at=store.now(), at=store.now())
    store.put('outcomes', job_id, o)
    store.put('history', uuid4().hex, {**o, 'job_id': job_id, 'recorded_by': 'user', 'event': 'followed_up'})
    return o


# ---------------------------------------------------------------- talk to an agent
class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=1000)
    mode: Literal['live', 'demo'] = 'live'


def chat_context(agent, mode):
    data = state(mode, full=True)
    jobs = data['jobs']
    weekly = insights.weekly(jobs, data['profile'])
    sched = data['schedule']
    recent = [e['message'] for e in store.events(tail=300) if e['actor'] == agent][-15:]
    excluded = [j for j in jobs if j['assessment']['status'] == 'excluded']
    reasons = Counter(next(c['label'] for c in j['assessment']['checks'] if c['status'] == 'excluded') for j in excluded)
    cfg = data['settings']
    return {'jobs': jobs, 'packages': data['packages'], 'pending': [i for i in data['inbox'] if agent == 'reviewer' or i.get('from_agent') == agent],
            'schedule': {'last': sched['next'].get('last_collect'), 'next': sched['next'].get('collect'), 'interval': sched['config']['interval_minutes']},
            'sources_enabled': sum(1 for s in data['sources'] if s.get('enabled', True)),
            'fresh': sum(1 for j in jobs if fresh_key(j) == 'pass'), 'eligible': sum(1 for j in jobs if j['assessment']['status'] == 'qualified'),
            'excluded': len(excluded), 'excluded_reasons': ', '.join(f'{r} {n}' for r, n in reasons.most_common(4)),
            'scored': sum(1 for j in jobs if j['assessment']['score'] is not None),
            'above': sum(1 for j in jobs if (j['assessment']['score'] or 0) >= cfg['match_min']), 'match_min': cfg['match_min'],
            'has_profile': bool(data['profile']), 'gaps': weekly['gaps'], 'recent': recent}


@app.get('/api/agents/{agent}/chat')
def get_chat(agent: str, mode: Literal['live', 'demo'] = 'live'):
    if agent not in chat.PERSONAS:
        raise HTTPException(404, 'Unknown agent')
    title, role, examples = chat.PERSONAS[agent]
    return {'agent': agent, 'name': chat.name_of(agent), 'title': title, 'role': role, 'examples': examples,
            'messages': store.get('chats', f'{mode}:{agent}', [])}


@app.post('/api/agents/{agent}/chat')
async def post_chat(agent: str, body: ChatIn, background: BackgroundTasks):
    if agent not in chat.PERSONAS:
        raise HTTPException(404, 'Unknown agent')
    key = f'{body.mode}:{agent}'
    history = store.get('chats', key, [])
    ctx = await asyncio.to_thread(chat_context, agent, body.mode)
    reply, action = await chat.respond(agent, body.message, ctx, settings().get('provider', 'extractive'), history)
    name = chat.name_of(agent)
    done = None
    if action:
        kind = action['type']
        try:
            if kind == 'run_search':
                if body.mode == 'demo':
                    done, reply = 'skipped', reply + '\n(Sample workspace: searching runs in Live mode.)'
                else:
                    done = await start_collection(auto_prepare=True, trigger=f'asked {name}') or 'busy'
                    if done == 'busy':
                        reply += "\n(A run is already going; I'll pick this up right after.)"
            elif kind == 'intake':
                ident = store.new_run(body.mode, f'Intake: {action["url"][:80]}')
                background.add_task(intake_run, ident, IntakeIn(url=action['url'], mode=body.mode))
                done = 'reading the posting'
            elif kind == 'set_interval':
                cfg = settings()
                cfg['schedule'] = {**scheduler.config(), 'interval_minutes': max(15, min(1440, int(action['minutes'])))}
                store.put('settings', 'main', cfg)
                done = 'interval set'
            elif kind == 'skip':
                await skip_job(action['job_id'], SkipIn(reason=f'Asked {name} to skip'))
                done = 'skipped'
            elif kind == 'tailor':
                job = need('jobs', action['job_id'])
                if action.get('override'):
                    await tailor_anyway(job['id'], background)
                    done = 'tailoring'
                elif LOCK.locked():
                    done, reply = 'busy', reply + "\n(Another run is active; I'll get to it next.)"
                else:
                    ident = store.new_run(body.mode, f'Tailor {job["company"]} (asked {name})')
                    background.add_task(prepare_run, ident, [job['id']])
                    done = 'tailoring'
        except HTTPException as exc:
            reply, done = f"I couldn't do that: {exc.detail}", 'refused'
    history += [{'role': 'user', 'text': body.message, 'at': store.now()},
                {'role': 'agent', 'text': reply, 'at': store.now(), 'action': action and action['type'], 'result': done}]
    store.put('chats', key, history[-60:])
    return {'reply': reply, 'action': action, 'result': done, 'messages': history[-60:]}


@app.get('/')
def home():
    return FileResponse(STATIC / 'index.html')


app.mount('/static', StaticFiles(directory=STATIC, check_dir=False), name='static')
