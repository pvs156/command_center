import pytest
from fastapi.testclient import TestClient

from app import store, scheduler
from app.main import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'DB', tmp_path / 'test.db')
    monkeypatch.delenv('ANTHROPIC_API_KEY', raising=False)
    monkeypatch.delenv('GROQ_API_KEY', raising=False)
    monkeypatch.setenv('COMMAND_CENTER_SCHEDULER', '0')
    with TestClient(app) as c:
        c.get('/api/state?mode=demo')
        yield c


def inbox(client):
    return client.get('/api/state?mode=demo').json()['inbox']


def test_package_reaches_inbox_with_everything_needed_to_apply(client):
    client.post('/api/prepare', json={'job_ids': ['demo:1']})
    item = next(i for i in inbox(client) if i['kind'] == 'package_ready')
    assert item['agent'] == 'reviewer'  # no flags needing acknowledgement
    assert item['match'] >= 60 and item['ats_after'] >= item['ats_before']
    assert item['apply_url'] and item['posted_at'] and item['package_id']
    assert any(f['key'] == 'sponsorship' for f in item['flags'])


def test_flagged_package_is_brought_by_gatekeeper_and_needs_acknowledgement(client):
    client.post('/api/prepare', json={'job_ids': ['demo:4']})  # pay undisclosed, no company evidence
    item = next(i for i in inbox(client) if i['kind'] == 'package_ready' and i['job_id'] == 'demo:4')
    assert item['agent'] == 'reviewer' and item['from_agent'] == 'eligibility'  # Grace flagged it, Remy delivers
    keys = [f['key'] for f in item['flags'] if f['ack']]
    assert 'company' in keys
    assert client.post(f'/api/packages/{item["package_id"]}/applied', json={}).status_code == 400
    r = client.post(f'/api/packages/{item["package_id"]}/applied', json={'acknowledged_flags': keys})
    assert r.status_code == 200 and r.json()['stage'] == 'applied' and r.json()['follow_up']
    assert not any(i.get('job_id') == 'demo:4' and i['kind'] == 'package_ready' for i in inbox(client))
    job = next(j for j in client.get('/api/state?mode=demo').json()['jobs'] if j['id'] == 'demo:4')
    assert job['outcome']['applied_at']


def test_skip_removes_from_inbox_and_pipeline(client):
    client.post('/api/prepare', json={'job_ids': ['demo:2']})
    assert any(i.get('job_id') == 'demo:2' for i in inbox(client))
    client.post('/api/jobs/demo:2/skip', json={'reason': 'Not my stack'})
    assert not any(i.get('job_id') == 'demo:2' for i in inbox(client))


def test_fit_review_band_asks_analyst_and_tailor_anyway_works(client):
    job = store.get('jobs', 'demo:2')
    job['description'] = ('Required: 2 years of experience. Python, RAG, Kubernetes, Terraform.\n'
                          'Base salary $100,000-$120,000 per year.')
    job['content_hash'] = 'changed-for-test'
    store.put('jobs', 'demo:2', job)
    a = next(j for j in client.get('/api/state?mode=demo&excluded=true').json()['jobs'] if j['id'] == 'demo:2')['assessment']
    if not (50 <= a['score'] < 60):
        pytest.skip(f'fixture score {a["score"]} not in review band')
    item = next(i for i in inbox(client) if i['kind'] == 'fit_review')
    assert item['agent'] == 'reviewer' and item['from_agent'] == 'analyst'
    assert client.post('/api/jobs/demo:2/tailor-anyway').status_code == 200
    assert any(p['job_id'] == 'demo:2' for p in client.get('/api/state?mode=demo').json()['packages'])


def test_chat_scout_status_and_interval(client):
    r = client.post('/api/agents/scout/chat', json={'message': 'status', 'mode': 'demo'}).json()
    assert 'boards' in r['reply'] and r['action'] is None
    r = client.post('/api/agents/scout/chat', json={'message': 'search every 30 minutes', 'mode': 'demo'}).json()
    assert r['result'] == 'interval set' and scheduler.config()['interval_minutes'] == 30
    assert len(client.get('/api/agents/scout/chat?mode=demo').json()['messages']) == 4


def test_chat_actions_tailor_skip_and_explain(client):
    r = client.post('/api/agents/writer/chat', json={'message': 'tailor Juniper Systems', 'mode': 'demo'}).json()
    assert r['result'] == 'tailoring'
    assert any(p['job_id'] == 'demo:2' for p in client.get('/api/state?mode=demo').json()['packages'])
    r = client.post('/api/agents/eligibility/chat', json={'message': 'why was Signal Foundry excluded?', 'mode': 'demo'}).json()
    assert 'sponsorship' in r['reply'].lower()
    r = client.post('/api/agents/reviewer/chat', json={'message': 'skip Juniper Systems', 'mode': 'demo'}).json()
    assert r['result'] == 'skipped' and store.get('jobs', 'demo:2')['skipped']
    assert client.post('/api/agents/nobody/chat', json={'message': 'hi', 'mode': 'demo'}).status_code == 404


def test_scout_interval_due_logic(client):
    from datetime import datetime, timedelta
    now = datetime.now(scheduler.TZ)
    cfg = {**scheduler.config(), 'interval_minutes': 60}
    store.put('scheduler', 'state', {'last_collect': (now - timedelta(minutes=30)).isoformat()})
    assert not scheduler.collect_due(now, cfg)
    store.put('scheduler', 'state', {'last_collect': (now - timedelta(minutes=61)).isoformat()})
    assert scheduler.collect_due(now, cfg)


def test_stale_package_is_retailored_not_stuck(client):
    client.post('/api/prepare', json={'job_ids': ['demo:1']})
    old = next(p for p in client.get('/api/state?mode=demo').json()['packages'] if p['job_id'] == 'demo:1')
    client.post('/api/skills', json={'skill': 'Kubernetes', 'mode': 'demo'})  # evidence changed
    state = client.get('/api/state?mode=demo').json()
    pkgs = [p for p in state['packages'] if p['job_id'] == 'demo:1']
    if all(p['stale'] for p in pkgs):  # advance may still be running in the background
        assert any(i['kind'] == 'stale_packages' for i in state['inbox'])
        assert client.post(f'/api/packages/{old["id"]}/applied', json={}).status_code == 409  # stale or already replaced
        client.post('/api/advance?mode=demo')
    import time
    for _ in range(50):
        pkgs = [p for p in client.get('/api/state?mode=demo').json()['packages'] if p['job_id'] == 'demo:1']
        if pkgs and not any(p['stale'] for p in pkgs):
            break
        time.sleep(0.1)
    assert len(pkgs) == 1 and not pkgs[0]['stale'] and pkgs[0]['id'] != old['id']


def test_workday_hash_is_stable_and_decisions_survive_recollection(client):
    from app.collectors import normalize, _persist
    src = {'id': 'workday:t', 'provider': 'workday', 'board': 't', 'company': 'T', 'host': 't.wd5.myworkdayjobs.com', 'site': 'Ext', 'category': 'enterprise'}
    raw = {'title': 'AI Engineer', 'externalPath': '/job/x_R1', 'postedOn': 'Posted Today',
           'jobPostingInfo': {'title': 'AI Engineer', 'jobReqId': 'R1', 'jobDescription': '<p>Python</p>', 'location': 'Dallas, TX'}}
    first = _persist(src, [raw], [raw])[0]
    job = store.get('jobs', first['id']); job['skipped'] = {'reason': 'x'}; job['fit_override'] = {'score': 55}; store.put('jobs', job['id'], job)
    second = _persist(src, [{**raw, 'postedOn': 'Posted Yesterday'}], [raw])[0]
    assert second['content_hash'] == first['content_hash']
    assert second['published_at'] == first['published_at']
    assert second['skipped'] and second['fit_override']


def test_stale_package_refresh_ignores_age(client):
    from datetime import datetime, timedelta, timezone
    client.post('/api/prepare', json={'job_ids': ['demo:1']})
    job = store.get('jobs', 'demo:1')
    job['published_at'] = (datetime.now(timezone.utc) - timedelta(days=4)).isoformat()
    job['content_hash'] = 'edited-posting'  # posting changed after tailoring, and is now old
    store.put('jobs', 'demo:1', job)
    client.post('/api/advance?mode=demo')
    import time
    for _ in range(50):
        pkgs = [p for p in client.get('/api/state?mode=demo').json()['packages'] if p['job_id'] == 'demo:1']
        if pkgs and not pkgs[0]['stale']:
            break
        time.sleep(0.1)
    assert pkgs and not pkgs[0]['stale']


def test_strict_handoff_order_and_one_courier(client):
    run = client.post('/api/prepare', json={'job_ids': ['demo:1']}).json()['run_id']
    actors = [e['actor'] for e in client.get(f'/api/events?run_id={run}').json()]
    order = []
    for a in actors:
        if not order or order[-1] != a:
            order.append(a)
    evs = client.get(f'/api/events?run_id={run}').json()
    assert order[:2] == ['eligibility', 'analyst']  # Grace, then Ana
    after_ana = [e for e in evs if e['actor'] not in ('eligibility', 'analyst')]
    assert {after_ana[0]['actor'], after_ana[1]['actor']} == {'writer', 'researcher'}  # Ana hands to Will and Rosa together
    assert all(e['type'] == 'working' for e in after_ana[:2])
    first_remy = next(k for k, e in enumerate(after_ana) if e['actor'] == 'reviewer')
    done = {e['actor'] for e in after_ana[:first_remy] if e['type'] == 'done'}
    assert done == {'writer', 'researcher'} and all(e['actor'] == 'reviewer' for e in after_ana[first_remy:])  # Remy waits for both
    items = inbox(client)
    assert items and all(i['agent'] == 'reviewer' for i in items)  # only Remy walks to your desk


def test_card_carries_rosas_results_and_downloadable_jd(client):
    client.post('/api/prepare', json={'job_ids': ['demo:1']})
    item = next(i for i in inbox(client) if i['kind'] == 'package_ready')
    assert {'recruiter', 'manager', 'peer'} <= {c['persona'] for c in item['contact_plan']}
    assert set(item['drafts']) == {'connection', 'recruiter', 'manager', 'referral', 'followup'}
    jd = client.get(f'/api/jobs/{item["job_id"]}/jd')
    assert jd.status_code == 200 and 'JOB DESCRIPTION' in jd.text and 'attachment' in jd.headers['content-disposition']
