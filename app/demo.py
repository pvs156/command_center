"""Explicit fictional fixtures, isolated from real applications and profiles."""
from datetime import datetime, timedelta, timezone
from . import store
from .policy import digest


SAMPLE_RESUME = """Sample Candidate
Dallas, TX | sample@example.com | (555) 010-0100 | linkedin.com/in/sample-candidate
# Summary
Sample candidate — applied AI engineer building Python services and LLM applications.
# Skills
Languages: Python, TypeScript, SQL
GenAI: LLMs, RAG, LangGraph, MCP, Claude
Cloud & DevOps: AWS, Docker, GitHub Actions
# Experience
AI Engineer | Example Studio | Jan 2023 – Present
- Built Python and FastAPI services for a RAG application using vector search and PostgreSQL, serving 40 internal users.
- Developed AI agents with LangGraph and MCP tool access, with evaluation before every release.
- Deployed Docker services on AWS with GitHub Actions CI/CD and observability dashboards.
# Projects
Demo knowledge assistant
- Built a React interface with TypeScript for a Claude-powered knowledge assistant.
# Education
B.S. Computer Science, Fictional University, 2022
"""


def seed():
    existing = store.get('profiles', 'demo')
    if existing and existing.get('version') == 'sample-v2':
        return
    if existing:  # older sample format: rebuild the fictional workspace only
        with store.connection() as c:
            c.execute("DELETE FROM records WHERE kind IN ('jobs','outcomes') AND id LIKE 'demo:%'")
            c.execute("DELETE FROM records WHERE kind IN ('packages','batches') AND json_extract(value,'$.demo')=1")
    from .resume import parse
    profile = parse(SAMPLE_RESUME, 'Sample candidate')
    profile.update(name='Sample candidate', demo=True, confirmed=True, version='sample-v2',
                   updated_at=store.now(), confirmed_skills=[])
    store.put('profiles', 'demo', profile)
    templates = [
        ('Northstar Labs', 'Applied AI Engineer', 'Remote · United States', 'Full-time',
         'Required: 2 years of experience. Build Python, FastAPI, RAG and LangGraph services on AWS. Use Docker and evaluation. We offer visa sponsorship.\nBase salary: $110,000–$145,000 per year.', 3),
        ('Juniper Systems', 'LLM Application Engineer', 'Austin, TX', 'Full-time',
         'Required: 3 years of experience. Python, LLMs, RAG, PostgreSQL, LangChain, AWS and Kubernetes.\nAnnual base salary $95,000–$125,000.', 5),
        ('Morrow Works', 'AI Software Engineer', 'Dallas, TX', 'Contract',
         'Required: 2 years of experience. Python, FastAPI, React, TypeScript, Claude, Docker and evaluation.\nContract rate $50–$65 per hour. Sponsorship is available.', 8),
        ('Cedar Research', 'AI Product Engineer', 'Boston, MA', 'Full-time',
         'Required: 1 year of experience. Python, React, LLMs, RAG, FastAPI and security. Competitive compensation. Meet our team on our website.', 10),
        ('Signal Foundry', 'Agentic AI Engineer', 'Seattle, WA', 'Full-time',
         'Required: 3 years of experience. Python, LangGraph, MCP, AWS, evaluation and Docker.\n$120,000–$150,000 per year. We cannot provide visa sponsorship now or in the future.', 12),
        ('Orchid Cloud', 'AI Platform Engineer', 'New York, NY', 'Full-time',
         'Required: 7 years of experience. Python, AWS, Kubernetes, Terraform, security, GCP and Azure.\n$150,000–$190,000 per year.', 6),
        ('Fieldnote AI', 'RAG Engineer', 'Remote · United States', 'Full-time',
         'Required: 2 years of experience. Python, RAG, vector search and PostgreSQL.\nSalary $100,000–$130,000 per year.', 30),
    ]
    for i, (company, title, location, employment, desc, hours) in enumerate(templates):
        job = {'id': f'demo:{i+1}', 'company': company, 'title': title, 'location': location,
               'employment_type': employment, 'description': desc, 'published_at': (datetime.now(timezone.utc)-timedelta(hours=hours)).isoformat(),
               'publication_kind': 'first_published', 'demo': True, 'url': 'https://example.com',
               'apply_url': 'https://example.com', 'company_url': 'https://example.com',
               'source_id': 'demo', 'provider': 'sample', 'first_seen_at': store.now(), 'last_checked_at': store.now()}
        job['content_hash'] = digest({k: job[k] for k in ['title', 'description', 'location']})
        store.put('jobs', job['id'], job)
