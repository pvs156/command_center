import asyncio

import httpx
import pytest

from app import llm, rules, store
from app.outreach import llm_drafts
from app.policy import assess
from app.resume import parse
from app.demo import SAMPLE_RESUME
from tests.test_tailoring import JD, SETTINGS


@pytest.fixture(autouse=True)
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'DB', tmp_path / 'test.db')


class FakeResponse:
    def __init__(self, status, data=None):
        self.status_code, self._data = status, data or {}
        self.is_success = 200 <= status < 300

    def json(self):
        return self._data


def test_gemini_falls_back_to_next_model_when_busy(monkeypatch):
    monkeypatch.setenv('GEMINI_API_KEY', 'test')
    monkeypatch.delenv('GEMINI_MODEL', raising=False)
    calls = []
    ok = {'candidates': [{'content': {'parts': [{'text': 'thinking', 'thought': True}, {'text': '{"a": 1}'}]}}], 'usageMetadata': {}}

    async def post(self, url, json=None):
        calls.append(url.split('/models/')[1].split(':')[0])
        return FakeResponse({0: 503, 1: 503, 2: 429}.get(len(calls) - 1, 200), ok)

    async def nap(_):
        return None
    monkeypatch.setattr(httpx.AsyncClient, 'post', post)
    monkeypatch.setattr(llm.asyncio, 'sleep', nap)
    assert asyncio.run(llm._gemini('s', 'p', {'type': 'object'}, None, 'writer')) == {'a': 1}
    assert calls == [llm.GEMINI_MODELS[0], llm.GEMINI_MODELS[0], llm.GEMINI_MODELS[1], llm.GEMINI_MODELS[2]]


def test_contact_details_are_redacted_and_restored(monkeypatch):
    monkeypatch.setattr(llm, '_identity', lambda: ({'name': 'Jane Q Doe', 'email': 'jane@x.io', 'phone': '555-1234'},
                                                   ['linkedin.com/in/janedoe']))
    text, restore = llm.redact('Jane Q Doe (jane@x.io, 555-1234, linkedin.com/in/janedoe). Jane builds agents.')
    assert 'Jane' not in text and 'jane@x.io' not in text and '555' not in text and 'janedoe' not in text
    assert llm.unredact({'msg': ['Thanks, [MY_FIRST_NAME]']}, restore) == {'msg': ['Thanks, Jane']}


def test_inflation_gate_blocks_new_qualifiers():
    base = 'Built a Python service handling 200 requests per day.'
    assert rules.hard_gate('Built a high-performance, real-time Python service handling 200 requests per day.', {'200'}, ['Python'], base)
    assert rules.hard_gate('Built a Python service that handles 200 requests a day.', {'200'}, ['Python'], base) is None


def test_model_drafts_are_checked_and_fall_back_per_draft(monkeypatch):
    profile = parse(SAMPLE_RESUME, 'Sample Candidate')
    job = {'id': 'j1', 'title': 'AI Engineer', 'company': 'Acme', 'description': JD, 'location': 'Dallas, TX', 'url': 'https://example.com/j'}
    a = assess(job, profile, SETTINGS)
    good = 'Hi [first name], your team is building LLM features with RAG. Would you be open to a short chat? [MY_FIRST_NAME]'

    async def fake(provider, system, prompt, schema, run_id=None, actor='writer'):
        assert 'JOB DESCRIPTION' in prompt and 'EVIDENCE' in prompt
        return {'connection': good,
                'recruiter': 'Subject: AI Engineer\nI ran Kubernetes in production for years.',  # claims a gap
                'manager': 'Subject: hi\nI built a real-time system serving 9000 users.',          # invented number + qualifier
                'referral': 'I hope this message finds you well. Could you refer me?',              # template opener
                'followup': 'Following up with one more detail: my RAG work is on my resume. [MY_FIRST_NAME]',
                'hook': 'LLM features'}
    monkeypatch.setattr(llm, 'complete_json', fake)
    d, rejected = asyncio.run(llm_drafts('gemini', job, profile, a))
    assert d['connection'] == good and d['followup'].startswith('Following up')
    assert {r['draft'] for r in rejected} == {'recruiter', 'manager', 'referral'}
    assert 'sponsorship' in d['recruiter']  # template kept


def test_model_failure_keeps_all_template_drafts(monkeypatch):
    profile = parse(SAMPLE_RESUME, 'Sample Candidate')
    job = {'id': 'j1', 'title': 'AI Engineer', 'company': 'Acme', 'description': JD, 'location': 'Dallas, TX'}

    async def boom(*a, **k):
        raise ValueError('Gemini unavailable')
    monkeypatch.setattr(llm, 'complete_json', boom)
    d, rejected = asyncio.run(llm_drafts('gemini', job, profile, assess(job, profile, SETTINGS)))
    assert rejected == [] and 'sponsorship' in d['recruiter']


def test_foreign_locations_are_excluded_but_us_namesakes_are_not():
    from app.policy import country
    assert [country(x) for x in ('Turkey', 'Istanbul', 'Toronto, ON', 'Dublin, OH', 'Paris, TX', 'Remote - US')] == \
        ['other', 'other', 'other', 'US', 'US', 'US']


def test_percentages_from_evidence_are_allowed_in_drafts(monkeypatch):
    profile = parse(SAMPLE_RESUME, 'Sample Candidate')
    job = {'id': 'j1', 'title': 'AI Engineer', 'company': 'Acme', 'description': JD, 'location': 'Dallas, TX'}
    fact = next(f['text'] for f in profile['facts'] if f['kind'] == 'bullet' and rules.NUMBER.search(f['text']))
    pct = rules.NUMBER.search(fact)[0].rstrip('.').replace(',', '')

    async def fake(*a, **k):
        return {k: f'Your RAG work caught my eye; I cut a metric by {pct}%. [MY_FIRST_NAME]' for k in
                ('connection', 'recruiter', 'manager', 'referral', 'followup', 'hook')}
    monkeypatch.setattr(llm, 'complete_json', fake)
    d, rejected = asyncio.run(llm_drafts('gemini', job, profile, assess(job, profile, SETTINGS)))
    assert rejected == [], rejected


def test_reselected_bullet_never_appears_twice(monkeypatch):
    from app.tailor import tailor
    profile = parse(SAMPLE_RESUME, 'Sample Candidate')
    job = {'title': 'AI Engineer', 'company': 'Acme', 'description': JD, 'location': 'Dallas, TX'}
    a = assess(job, profile, SETTINGS)
    seen = {}

    async def fake(provider, system, prompt, schema, run_id=None, actor='writer'):
        first = prompt.split('CURRENT: ', 1)[1].split('\n', 1)[0].split(' | ')
        seen['first'] = first
        # slot 1 takes slot 2's bullet; slot 2's rewrite fails (claims Kubernetes), so slot 2 keeps its original
        return {'summary': '', 'notes': [], 'roles': [{'role': 'r1', 'bullets': [first[1], first[1] + ' Ran it on Kubernetes.'] + first[2:]}]}
    monkeypatch.setattr(llm, 'complete_json', fake)
    r = asyncio.run(tailor(job, profile, a, 'gemini'))
    texts = [b['text'] for s in r['sections'] for role in s['roles'] for b in role['bullets']]
    assert texts.count(seen['first'][1]) == 1
    assert any(v.get('reason') == 'duplicates another bullet' for v in r['validation'])
