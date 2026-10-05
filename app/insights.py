"""Weekend learning loop: what the market asked for, what the resume can't show yet,
which projects would create real evidence, and what has actually worked.
"""
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

from .policy import parse_time

# Project ideas keyed by the gap skills they produce evidence for. Several extend
# this command center so the work doubles as a portfolio piece.
CATALOG = [
    {'id': 'evals', 'skills': {'LLM Evaluation', 'LLMOps', 'Testing', 'Observability'},
     'title': 'Evaluation harness for the resume-tailoring agent',
     'pitch': 'Build a golden set of 40 job/resume pairs and score the tailoring worker for keyword lift, unsupported-claim rate and validator rejections; track runs in Langfuse or MLflow.',
     'deliverables': ['eval dataset + runner (pytest)', 'LLM-as-judge rubric with agreement check against 20 hand labels', 'dashboard of scores per prompt version', 'README with results table'],
     'metric': 'Unsupported-claim rate < 1%, judge/human agreement ≥ 0.8', 'hours': 10, 'extends': 'Job Search Command Center'},
    {'id': 'rag', 'skills': {'RAG', 'Vector Databases', 'Embeddings', 'LlamaIndex', 'LangChain', 'Elasticsearch'},
     'title': 'Hybrid-search RAG over job descriptions',
     'pitch': 'Index every collected JD with pgvector + BM25, add reranking, and answer "which roles want X and Y?" with cited postings.',
     'deliverables': ['ingestion pipeline with chunking strategy comparison', 'hybrid retrieval + reranker', 'retrieval eval (recall@5, MRR) on 50 labelled queries', 'FastAPI endpoint + small UI'],
     'metric': 'recall@5 ≥ 0.85 with documented chunking ablation', 'hours': 12, 'extends': 'Job Search Command Center'},
    {'id': 'agents', 'skills': {'AI Agents', 'LangGraph', 'MCP', 'Function Calling', 'CrewAI', 'Claude', 'OpenAI API'},
     'title': 'MCP server + LangGraph agent for company research',
     'pitch': 'Expose the command center\'s jobs, resume evidence and tracker as an MCP server, then build a LangGraph agent that researches a company and drafts a dossier with citations.',
     'deliverables': ['MCP server (3–5 tools, typed schemas)', 'LangGraph graph with human-approval node', 'tool-call traces', 'demo video'],
     'metric': 'Dossier citation accuracy checked on 10 companies', 'hours': 10, 'extends': 'Job Search Command Center'},
    {'id': 'deploy', 'skills': {'Docker', 'Kubernetes', 'AWS', 'Terraform', 'CI/CD', 'GCP', 'Azure', 'Serverless', 'Bedrock'},
     'title': 'Ship the command center to the cloud with IaC',
     'pitch': 'Containerise the app, provision it with Terraform (AWS ECS or a small EKS cluster), add GitHub Actions CI/CD and a scheduled collector.',
     'deliverables': ['Dockerfile + compose', 'Terraform module', 'GitHub Actions pipeline with tests', 'cost + architecture write-up'],
     'metric': 'One-command deploy; CI green; monthly cost estimate documented', 'hours': 8, 'extends': 'Job Search Command Center'},
    {'id': 'finetune', 'skills': {'Fine-tuning', 'PyTorch', 'Open-source LLMs', 'Deep Learning', 'NLP', 'Machine Learning', 'TensorFlow'},
     'title': 'LoRA fine-tune a small model to extract job requirements',
     'pitch': 'Fine-tune a 1–3B open model with LoRA to label required vs preferred skills in JDs, and compare against the regex baseline and a frontier model.',
     'deliverables': ['labelled dataset (300 JDs)', 'training notebook + model card', 'F1 comparison table', 'served with vLLM or Ollama'],
     'metric': 'F1 vs baseline, latency and cost per 1k JDs', 'hours': 14, 'extends': None},
    {'id': 'guardrails', 'skills': {'Guardrails', 'Security'},
     'title': 'Prompt-injection red-team suite',
     'pitch': 'Write 50 adversarial job postings that try to hijack the tailoring agent; measure and harden defenses.',
     'deliverables': ['attack corpus', 'automated red-team runner', 'before/after defense metrics'],
     'metric': 'Attack success rate before vs after', 'hours': 6, 'extends': 'Job Search Command Center'},
    {'id': 'fullstack', 'skills': {'React', 'Next.js', 'TypeScript', 'JavaScript', 'HTML/CSS', 'Streamlit'},
     'title': 'Rebuild the command center front end in Next.js + TypeScript',
     'pitch': 'Port the dashboard to Next.js with typed API clients, server components and streaming run updates.',
     'deliverables': ['Next.js app', 'typed API client', 'Playwright tests', 'Lighthouse report'],
     'metric': 'Feature parity + e2e tests passing', 'hours': 12, 'extends': 'Job Search Command Center'},
    {'id': 'data', 'skills': {'Spark', 'Airflow', 'Kafka', 'Data Pipelines', 'Snowflake', 'SQL', 'PostgreSQL'},
     'title': 'Job-market data pipeline',
     'pitch': 'Schedule daily collection with Airflow/Dagster into Postgres, model skill-demand trends in SQL, publish a weekly chart.',
     'deliverables': ['DAG with retries + alerts', 'dbt-style SQL models', 'trend dashboard'],
     'metric': '30 days of clean history; data-quality tests', 'hours': 8, 'extends': 'Job Search Command Center'},
    {'id': 'voice', 'skills': {'Conversational AI', 'Prompt Engineering', 'Context Engineering'},
     'title': 'Voice mock-interviewer',
     'pitch': 'A voice agent that runs a mock technical screen from a saved JD and scores answers against the rubric.',
     'deliverables': ['speech pipeline', 'question generator grounded in the JD', 'scoring rubric + transcript review'],
     'metric': 'Latency < 1.5s/turn; rubric consistency on 10 sessions', 'hours': 10, 'extends': None},
]


def weekly(jobs, profile, days=7, now=None):
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(days=days)
    hard = ('country', 'sponsorship', 'experience', 'employment')
    window = []
    for j in jobs:
        seen = parse_time(j.get('first_seen_at'))
        if not seen or seen < since:
            continue
        checks = {c['key']: c['status'] for c in j['assessment']['checks']}
        if any(checks.get(k) == 'excluded' for k in hard):
            continue
        window.append(j)
    demand = defaultdict(lambda: {'jobs': 0, 'required': 0, 'preferred': 0, 'companies': set(), 'examples': []})
    for j in window:
        for skill, r in j['assessment'].get('requirements', {}).items():
            d = demand[skill]
            d['jobs'] += 1
            if r['kind'] == 'required':
                d['required'] += 1
            elif r['kind'] == 'preferred':
                d['preferred'] += 1
            d['companies'].add(j['company'])
            if len(d['examples']) < 6:
                d['examples'].append({'title': j['title'], 'company': j['company'], 'url': j.get('url'), 'id': j['id']})
    resume_skills = set((profile or {}).get('resume_skills', []))
    confirmed = set((profile or {}).get('confirmed_skills', []))
    rows = []
    for skill, d in demand.items():
        status = 'on resume' if skill in resume_skills else 'confirmed, not on resume' if skill in confirmed else 'gap'
        rows.append({'skill': skill, 'jobs': d['jobs'], 'required': d['required'], 'preferred': d['preferred'],
                     'companies': len(d['companies']), 'share': round(100 * d['jobs'] / max(1, len(window))),
                     'status': status, 'examples': d['examples']})
    rows.sort(key=lambda r: (-r['jobs'], -r['required']))
    gaps = [r for r in rows if r['status'] == 'gap']
    gap_weight = {r['skill']: r['jobs'] + r['required'] for r in gaps}
    projects = []
    for p in CATALOG:
        covered = [s for s in p['skills'] if s in gap_weight]
        if not covered:
            continue
        value = sum(gap_weight[s] for s in covered)
        cites = []
        for r in gaps:
            if r['skill'] in covered:
                cites += [e for e in r['examples'] if e not in cites]
        projects.append({**{k: v for k, v in p.items() if k != 'skills'}, 'covers': sorted(covered, key=lambda s: -gap_weight[s]),
                         'value': value, 'cites': cites[:6],
                         'resume_line': f'Built {p["title"].lower()} — [result/metric once complete]'})
    projects.sort(key=lambda p: -p['value'])
    return {'window_days': days, 'since': since.isoformat(), 'roles_in_window': len(window),
            'demand': rows[:40], 'gaps': gaps[:15],
            'quick_wins': [r for r in rows if r['status'] == 'confirmed, not on resume'][:10],
            'projects': projects[:3], 'generated_at': now.isoformat()}


def outcomes(jobs, packages, history):
    """Funnel and response rates with denominators. Correlation only."""
    applied = [j for j in jobs if j['outcome'].get('applied_at')]
    responded_stages = {'replied', 'screening', 'interviewing', 'offer'}
    reached = defaultdict(set)
    for h in history:
        reached[h['job_id']].add(h['stage'])

    def rate(group):
        n = len(group)
        r = sum(1 for j in group if reached[j['id']] & responded_stages or j['outcome']['stage'] in responded_stages)
        return {'n': n, 'responses': r, 'rate': round(100 * r / n) if n else None, 'small_sample': n < 10}

    def band(score):
        if score is None:
            return 'unscored'
        return '80%+' if score >= 80 else '70–79%' if score >= 70 else '60–69%' if score >= 60 else '<60%'

    pkg = {p['job_id']: p for p in packages}
    groups = {
        'Base match': lambda j: band(j['assessment']['score']),
        'Source': lambda j: j.get('provider', 'unknown'),
        'Sponsorship wording': lambda j: j['assessment']['sponsorship']['label'],
        'Tailoring mode': lambda j: (pkg.get(j['id']) or {}).get('mode', 'none'),
        'Outreach': lambda j: 'contacted someone' if any(o.get('status') in ('sent', 'replied') for o in (pkg.get(j['id']) or {}).get('outreach_log', [])) else 'applied only',
    }
    breakdowns = {}
    for name, key in groups.items():
        buckets = defaultdict(list)
        for j in applied:
            buckets[key(j)].append(j)
        breakdowns[name] = sorted(({'bucket': b, **rate(g)} for b, g in buckets.items()), key=lambda x: -x['n'])
    stages = Counter(j['outcome']['stage'] for j in jobs)
    funnel = [{'stage': s, 'count': c} for s, c in [
        ('collected', len(jobs)),
        ('eligible', sum(1 for j in jobs if j['assessment']['status'] != 'excluded')),
        ('prepared', len({p['job_id'] for p in packages})),
        ('approved', sum(1 for j in jobs if j['outcome']['stage'] != 'discovered')),
        ('applied', len(applied)),
        ('responded', sum(1 for j in applied if reached[j['id']] & responded_stages or j['outcome']['stage'] in responded_stages)),
        ('interviewing', sum(1 for j in applied if 'interviewing' in reached[j['id']] or j['outcome']['stage'] in ('interviewing', 'offer'))),
        ('offer', stages.get('offer', 0)),
    ]]
    return {'funnel': funnel, 'overall': rate(applied), 'breakdowns': breakdowns,
            'notice': 'Response rates compare groups of different jobs; they suggest what to try next, not what caused a result.'}
