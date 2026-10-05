import asyncio
import time

import pytest
from fastapi.testclient import TestClient

from app import intake, store
from app.main import app

JD = """Applied AI Engineer
Company: Northwind Labs

About the role
You will build LLM-powered agents for our customers.

Requirements
- 2+ years of experience with Python and FastAPI
- Experience building AI agents with LangGraph and MCP
- Retrieval-augmented generation (RAG) and evaluation
Benefits
- Health insurance, 401k
"""

PAGE = """<html><head><title>Applied AI Engineer | Northwind</title>
<meta property="og:site_name" content="Northwind Careers">
<script type="application/ld+json">{"@context":"https://schema.org","@graph":[{"@type":"Organization","name":"x"},
{"@type":"JobPosting","title":"Applied AI Engineer","datePosted":"2026-10-02",
"description":"<p>Build agents.</p><ul><li>2+ years of experience with Python</li><li>LangGraph and MCP</li><li>RAG pipelines and evals for production LLM systems at scale.</li></ul><p>We value curiosity and ownership across a fast moving AI team building production systems.</p>",
"hiringOrganization":{"@type":"Organization","name":"Northwind Labs"},"employmentType":"FULL_TIME",
"jobLocation":{"@type":"Place","address":{"addressLocality":"Dallas","addressRegion":"TX","addressCountry":"US"}},
"baseSalary":{"@type":"MonetaryAmount","currency":"USD","value":{"@type":"QuantitativeValue","minValue":120000,"maxValue":150000,"unitText":"YEAR"}}}]}
</script></head><body><nav>menu</nav><h1>Applied AI Engineer</h1></body></html>"""


def test_jsonld_job_posting_is_extracted():
    node, parser = intake.job_posting(PAGE)
    assert node['title'] == 'Applied AI Engineer'
    assert intake._location(node) == 'Dallas, TX' and intake._country(node) == 'US'
    assert intake._salary(node)['min'] == 120000 and intake._employment(node) == 'Full-time'


def test_known_board_links_are_recognised():
    assert intake._posting_parts('https://job-boards.greenhouse.io/anthropic/jobs/4567890')['posting'] == '4567890'
    lever = intake._posting_parts('https://jobs.lever.co/acme/1234abcd-1111-2222-3333-444455556666')
    assert lever['provider'] == 'lever' and lever['posting'].startswith('1234abcd')
    wd = intake._posting_parts('https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite/job/US-CA-Santa-Clara/AI-Engineer_JR123')
    assert wd['provider'] == 'workday' and wd['posting'].startswith('/job/')
    assert intake._posting_parts('https://example.com/careers/123') is None


def test_pasted_text_becomes_a_manual_job():
    job = asyncio.run(intake.intake(text=JD))
    assert job['manual'] and job['title'] == 'Applied AI Engineer' and job['company'] == 'Northwind Labs'
    assert {'Python', 'LangGraph', 'MCP'} <= set(job['requirements'])
    with pytest.raises(intake.IntakeError):
        asyncio.run(intake.intake(text='too short'))


def test_login_walls_get_a_helpful_message():
    with pytest.raises(intake.IntakeError, match='login'):
        asyncio.run(intake.intake(url='https://www.linkedin.com/jobs/view/123'))


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'DB', tmp_path / 'test.db')
    monkeypatch.setenv('COMMAND_CENTER_SCHEDULER', '0')
    with TestClient(app) as c:
        c.get('/api/state?mode=demo')
        yield c


def wait_run(client, run_id):
    for _ in range(80):
        r = client.get(f'/api/runs/{run_id}').json()
        if r['status'] != 'running':
            return r
        time.sleep(0.05)
    return r


def test_pasted_jd_goes_from_ivy_to_grace_to_your_review_desk(client):
    r = client.post('/api/intake', json={'text': JD, 'mode': 'demo', 'apply_url': 'https://example.com/apply'}).json()
    run = wait_run(client, r['run_id'])
    assert run['status'] == 'completed', run
    actors = [e['actor'] for e in client.get(f'/api/events?run_id={r["run_id"]}').json()]
    assert actors[0] == 'intake' and 'eligibility' in actors and 'writer' in actors
    item = next(i for i in client.get('/api/state?mode=demo').json()['inbox'] if i['kind'] == 'package_ready' and i.get('manual'))
    assert item['title'] == 'Applied AI Engineer' and item['apply_url'] == 'https://example.com/apply'
    freshness = next(c for c in client.get(f'/api/jobs/{item["job_id"]}').json()['assessment']['checks'] if c['key'] == 'freshness')
    assert freshness['status'] == 'pass' and freshness['label'].startswith('Added by you')


def test_hard_exclusions_are_flags_not_blocks_for_jobs_you_add(client):
    jd = JD + '\nWe cannot provide visa sponsorship now or in the future.\nRequired: 7+ years of experience.\n'
    run = wait_run(client, client.post('/api/intake', json={'text': jd, 'mode': 'demo'}).json()['run_id'])
    assert run['status'] == 'completed', run
    item = next(i for i in client.get('/api/state?mode=demo').json()['inbox'] if i.get('manual'))
    exclusions = [f for f in item['flags'] if f.get('severity') == 'exclusion']
    assert {'sponsorship', 'experience'} <= {f['key'] for f in exclusions}
    assert client.post(f'/api/packages/{item["package_id"]}/applied', json={}).status_code == 400
    keys = [f['key'] for f in item['flags'] if f['ack']]
    assert client.post(f'/api/packages/{item["package_id"]}/applied', json={'acknowledged_flags': keys}).status_code == 200


def test_link_pasted_in_any_chat_is_handed_to_ivy(client, monkeypatch):
    async def fake(url=None, text=None, demo=False, **kw):
        return intake.make_job({'title': 'AI Engineer', 'company': 'Acme', 'description': JD, 'url': url, 'via': 'test'}, demo)
    monkeypatch.setattr(intake, 'intake', fake)
    r = client.post('/api/agents/scout/chat', json={'message': 'can you check https://acme.example/jobs/42', 'mode': 'demo'}).json()
    assert r['action']['type'] == 'intake' and 'Ivy' in r['reply'] and r['result'] == 'reading the posting'
    time.sleep(0.5)
    assert any(j.get('manual') and j['company'] == 'Acme' for j in client.get('/api/state?mode=demo').json()['jobs'])


def test_agents_have_names_you_can_change(client):
    assert client.get('/api/agents/scout/chat?mode=demo').json()['name'] == 'Sam'
    cfg = client.get('/api/state?mode=demo').json()['settings']
    client.post('/api/settings', json={'hourly_min': cfg['hourly_min'], 'provider': 'extractive', 'agent_names': {'scout': 'Scotty'}})
    assert client.get('/api/agents/scout/chat?mode=demo').json()['name'] == 'Scotty'
    assert client.get('/api/state?mode=demo').json()['agent_names']['intake'] == 'Ivy'


def test_sams_search_never_closes_a_job_you_brought_in(client):
    from app.collectors import _persist
    job = intake.make_job({'title': 'AI Engineer', 'company': 'Acme', 'description': JD, 'url': 'https://x.example/j', 'via': 'test'})
    job['source_id'] = 'greenhouse:acme'  # even if it shares a board with Sam's sources
    store.put('jobs', job['id'], job)
    _persist({'id': 'greenhouse:acme', 'provider': 'greenhouse', 'board': 'acme', 'company': 'Acme'}, [], [])
    assert not store.get('jobs', job['id'])['closed']
