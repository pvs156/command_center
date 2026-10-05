import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import store, scheduler
from app.main import app
from app.policy import assess, sponsorship
from app.scoring import requirements, base_match, evidence_map
from app.tailor import tailor, validate_bullet
from app.resume import parse
from app.collectors import board_from_url, normalize, title_matches
from app.outreach import drafts, contact_plan
from app.demo import SAMPLE_RESUME


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'DB', tmp_path / 'test.db')
    monkeypatch.delenv('ANTHROPIC_API_KEY', raising=False)
    monkeypatch.delenv('GROQ_API_KEY', raising=False)
    monkeypatch.setenv('COMMAND_CENTER_SCHEDULER', '0')
    with TestClient(app) as c:
        yield c


JD = """About the role
Build LLM features for customers.
Requirements
- 2+ years of experience with Python and FastAPI
- Experience with RAG and vector databases
- Kubernetes in production
Nice to have
- LangGraph, Terraform
Benefits
- Python lunch club"""

SETTINGS = {'annual_min': 90000, 'hourly_min': 45, 'hourly_review_min': 40, 'match_min': 60, 'review_min': 50, 'max_experience': 4}


def test_requirements_split_required_preferred_and_ignore_benefits():
    r = requirements(JD)
    assert r['Python']['kind'] == 'required' and r['Kubernetes']['kind'] == 'required'
    assert r['LangGraph']['kind'] == 'preferred' and r['Terraform']['weight'] == 1
    assert r['Python']['weight'] == 2


def test_base_match_ignores_user_confirmed_skills():
    profile = parse(SAMPLE_RESUME)
    reqs = requirements(JD)
    plain = base_match(reqs, evidence_map(reqs, profile))
    boosted = base_match(reqs, evidence_map(reqs, profile, ['Kubernetes', 'Terraform']))
    assert plain == boosted  # confirmations never move the gate


@pytest.mark.parametrize('text', [
    'U.S. citizenship is required for this role.',
    'Must be a US citizen.',
    'Active Secret security clearance required.',
    'No H1B candidates please.',
    'Candidates requiring sponsorship will not be considered.',
])
def test_more_explicit_restrictions(text):
    assert sponsorship(text)['status'] == 'excluded'


def test_opt_condition_needs_review():
    assert sponsorship('We do not accept OPT candidates for this role.')['status'] == 'review'


def run_tailor(job_text, profile, confirmed=()):
    profile = {**profile, 'confirmed_skills': list(confirmed)}
    job = {'title': 'Senior AI Engineer', 'company': 'Acme', 'description': job_text, 'location': 'Dallas, TX'}
    a = assess(job, profile, SETTINGS)
    return asyncio.run(tailor(job, profile, a, 'extractive')), a


def test_tailoring_adds_only_supported_keywords():
    profile = parse(SAMPLE_RESUME, 'Sample')
    resume, _ = run_tailor(JD, profile)
    assert 'Kubernetes' in resume['gaps'] and 'Terraform' in resume['gaps']
    assert 'Kubernetes' not in resume['text']
    assert 'FastAPI' in resume['added_to_skills']  # shown in experience, missing from Skills
    original = next(f['text'] for f in profile['facts'] if f['section'] == 'summary')
    assert resume['summary'] == original and 'Hands-on' not in resume['summary']  # no tacked-on keyword lists (rule 13)
    resume2, _ = run_tailor(JD, profile, confirmed=['Kubernetes'])
    assert 'Kubernetes' in resume2['added_to_skills']
    assert resume2['ats']['score'] >= resume['ats']['score']


def test_validator_blocks_invented_numbers_and_skills():
    old = 'Built Python services for 40 users.'
    assert validate_bullet(old, 'Built Python services for 400 users.', ['Python'])
    assert validate_bullet(old, 'Built Python and Kubernetes services for 40 users.', ['Python'])
    assert validate_bullet(old, 'Built Python and FastAPI services for 40 users.', ['Python', 'FastAPI']) is None


def test_model_rewrites_are_validated(monkeypatch):
    from app import llm
    profile = parse(SAMPLE_RESUME, 'Sample')
    bullet = next(f for f in profile['facts'] if f['kind'] == 'bullet')

    async def fake(provider, system, prompt, schema, run_id=None, actor='writer'):
        return {'summary': 'AI engineer with 99 years using Kubernetes.',
                'roles': [{'role': 'r1', 'bullets': [bullet['text'] + ' Used Kubernetes at 10x scale.']}], 'notes': []}
    monkeypatch.setattr(llm, 'complete_json', fake)
    job = {'title': 'AI Engineer', 'company': 'Acme', 'description': JD, 'location': 'Dallas, TX'}
    r = asyncio.run(tailor(job, profile, assess(job, profile, SETTINGS), 'claude'))
    assert 'Kubernetes' not in r['text'] and '99' not in r['summary']
    assert len([v for v in r['validation'] if v['status'] == 'rejected']) == 2


def test_contact_plan_and_drafts_never_invent_people():
    profile = parse(SAMPLE_RESUME, 'Sample Candidate')
    job = {'title': 'AI Engineer', 'company': 'Acme', 'url': 'https://example.com/job'}
    plan = contact_plan(job, profile)
    assert {p['persona'] for p in plan} >= {'recruiter', 'manager', 'peer', 'alumni'}
    assert all(p['linkedin'].startswith('https://www.linkedin.com/search/') for p in plan)
    resume, a = run_tailor(JD, profile)
    d = drafts(job, profile, resume, a['requirements'])
    assert '[first name]' in d['recruiter'] and len(d['connection']) <= 300
    assert 'sponsorship' in d['recruiter']


def test_board_url_detection():
    assert board_from_url('https://job-boards.greenhouse.io/anthropic/jobs/123') == {'provider': 'greenhouse', 'board': 'anthropic'}
    assert board_from_url('https://jobs.ashbyhq.com/openai')['board'] == 'openai'
    wd = board_from_url('https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite/job/x')
    assert wd == {'provider': 'workday', 'board': 'nvidia', 'host': 'nvidia.wd5.myworkdayjobs.com', 'site': 'NVIDIAExternalCareerSite'}


def test_title_filter():
    assert title_matches('Applied AI Engineer')
    assert title_matches('Forward Deployed Engineer')
    assert not title_matches('Staff AI Engineer')
    assert not title_matches('Account Executive')


def test_lever_created_recently_counts_as_fresh_and_workday_days():
    now = datetime.now(timezone.utc)
    lever = normalize({'id': 'a', 'text': 'AI Engineer', 'createdAt': (now - timedelta(hours=3)).timestamp() * 1000,
                       'hostedUrl': 'https://x'}, {'id': 'lever:t', 'provider': 'lever', 'board': 't', 'company': 'T'})
    fresh = lambda j: next(c for c in assess(j, None, SETTINGS)['checks'] if c['key'] == 'freshness')['status']
    assert fresh(lever) == 'pass' and lever['published_at'] is None
    src = {'id': 'workday:t', 'provider': 'workday', 'board': 't', 'company': 'T', 'host': 't.wd5.myworkdayjobs.com', 'site': 'Ext'}
    today = normalize({'title': 'AI Engineer', 'externalPath': '/job/x_R1', 'postedOn': 'Posted Today'}, src)
    old = normalize({'title': 'AI Engineer', 'externalPath': '/job/y_R2', 'postedOn': 'Posted 5 Days Ago'}, src)
    assert fresh(today) == 'pass' and fresh(old) == 'excluded'


def test_greenhouse_stale_updated_at_excludes_without_detail():
    old = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
    j = normalize({'id': 1, 'title': 'AI Engineer', 'updated_at': old, 'location': {'name': 'Dallas, TX'}},
                  {'id': 'greenhouse:t', 'provider': 'greenhouse', 'board': 't', 'company': 'T'})
    assert next(c for c in assess(j, None, SETTINGS)['checks'] if c['key'] == 'freshness')['status'] == 'excluded'


def test_skill_confirmation_flows_into_profile(client):
    client.get('/api/state?mode=demo')
    assert client.post('/api/skills', json={'skill': 'Kubernetes', 'mode': 'demo'}).status_code == 200
    profile = client.get('/api/state?mode=demo').json()['profile']
    assert 'Kubernetes' in profile['confirmed_skills']
    assert client.post('/api/skills', json={'skill': 'Made-up', 'mode': 'demo'}).status_code == 400


def test_package_has_scores_drafts_and_editable_resume(client):
    client.get('/api/state?mode=demo')
    assert client.post('/api/prepare', json={'job_ids': ['demo:1']}).status_code == 200
    p = next(p for p in client.get('/api/state?mode=demo').json()['packages'] if p['job_id'] == 'demo:1')
    assert p['scores']['base'] >= 60 and p['scores']['ats_tailored'] >= p['scores']['ats_base']
    assert set(p['drafts']) == {'connection', 'recruiter', 'manager', 'referral', 'followup'}
    edited = client.put(f'/api/packages/{p["id"]}/resume', json={'summary': 'Edited summary.'}).json()
    assert edited['resume']['summary'] == 'Edited summary.'
    assert client.put(f'/api/packages/{p["id"]}/drafts', json={'key': 'connection', 'text': 'Hi!'}).json()['connection'] == 'Hi!'
    log = client.post(f'/api/packages/{p["id"]}/outreach', json={'contact': 'Pat', 'channel': 'linkedin', 'status': 'sent'}).json()
    assert log[0]['recorded_by'] == 'user'
    assert client.get(f'/api/packages/{p["id"]}/export?format=txt').status_code == 200


def test_insights_report_and_snapshot(client):
    client.get('/api/state?mode=demo')
    data = client.get('/api/insights?mode=demo').json()
    assert data['weekly']['roles_in_window'] >= 4
    assert any(g['skill'] == 'Kubernetes' for g in data['weekly']['gaps'])
    assert data['weekly']['projects'] and data['weekly']['projects'][0]['cites']
    assert data['outcomes']['funnel'][0]['stage'] == 'collected'
    ident = client.post('/api/insights/snapshot?mode=demo').json()['id']
    assert client.get(f'/api/insights/snapshots/{ident}').json()['mode'] == 'demo'


def test_sources_seed_and_url_add_validation(client):
    r = client.post('/api/sources/seed', json={'categories': ['dallas-area']}).json()
    assert r['added'] >= 5
    assert client.post('/api/sources', json={'company': 'X', 'careers_url': 'https://example.com/careers'}).status_code == 400


def test_scheduler_slot_fires_once_and_skips_stale(client):
    from zoneinfo import ZoneInfo
    tz = ZoneInfo('America/Chicago')
    now = datetime(2026, 10, 5, 8, 30, tzinfo=tz)
    assert scheduler.due(now, '08:00', 'collect:08:00')
    scheduler.mark('collect:08:00', now)
    assert not scheduler.due(now, '08:00', 'collect:08:00')
    assert not scheduler.due(datetime(2026, 10, 6, 12, 30, tzinfo=tz), '08:00', 'collect:08:00')


def test_package_stays_approvable_after_posting_ages(client):
    client.get('/api/state?mode=demo')
    client.post('/api/prepare', json={'job_ids': ['demo:1']})
    p = next(p for p in client.get('/api/state?mode=demo').json()['packages'] if p['job_id'] == 'demo:1')
    job = store.get('jobs', 'demo:1')
    job['published_at'] = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
    store.put('jobs', 'demo:1', job)  # content_hash unchanged: same posting, just older
    listed = client.get('/api/state?mode=demo').json()['jobs']
    assert any(j['id'] == 'demo:1' for j in listed)  # still visible on the review desk
    assert client.post('/api/batches', json={'package_ids': [p['id']], 'acknowledged': True}).status_code == 200


def test_catch_up_window_after_laptop_was_off():
    from app.policy import freshness
    now = datetime.now(timezone.utc)
    job = {'published_at': (now - timedelta(hours=40)).isoformat(), 'publication_kind': 'first_published',
           'first_seen_at': now.isoformat()}
    assert freshness(job)[0] == 'excluded'  # normal 24h rule
    job['catch_up_hours'] = 48  # Sam's first search after 47h away
    status, label, _ = freshness(job)
    assert status == 'pass' and 'while you were away' in label
    job['first_seen_at'] = (now - timedelta(hours=25)).isoformat()  # the catch-up credit lasts one day
    assert freshness(job)[0] == 'excluded'
    lever = {'created_at': (now - timedelta(hours=30)).isoformat(), 'first_seen_at': now.isoformat(), 'catch_up_hours': 36}
    assert freshness(lever)[0] == 'pass'


def test_priority_blends_match_and_recency():
    from app.policy import priority
    now = datetime.now(timezone.utc)
    new = {'published_at': (now - timedelta(hours=2)).isoformat()}
    old = {'published_at': (now - timedelta(hours=23)).isoformat()}
    assert priority(new, 70) > priority(old, 75)  # a fresh 70% outranks a day-old 75%
    assert priority(old, 90) > priority(new, 70)  # but a much better match still wins
