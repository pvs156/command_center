"""Public ATS board adapters. Never invent a publication date.

Freshness evidence by provider:
- Greenhouse: first_published (detail) or updated_at as an upper bound.
- Ashby: publishedAt (most recent publication; may be a repost).
- Lever: createdAt only; inside 24h it proves freshness, otherwise age is unknown.
- SmartRecruiters: releasedDate.
- Workday: postedOn ("Posted Today" / "Yesterday" / "N Days Ago") - whole days only.
"""
import asyncio
import html
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from . import store
from .policy import ROLE_HINT, digest, parse_pay, parse_time
from .scoring import requirements
from .taxonomy import load_target_titles, title_aliases

ROOT = Path(__file__).resolve().parents[1]
ALIASES = title_aliases(load_target_titles(ROOT / 'docs' / 'TARGET-ROLES.md'))
TOO_SENIOR = re.compile(r'\b(staff|principal|distinguished|director|head of|vp|vice president|manager|architect|fellow|lead|intern|internship|co-?op)\b', re.I)
WORKDAY_QUERIES = ['AI engineer', 'machine learning engineer', 'generative AI', 'LLM', 'AI developer']
UA = {'User-Agent': 'JobCommandCenter/0.2 (personal job search; respectful polling)'}


def title_matches(title):
    t = (title or '').lower()
    if TOO_SENIOR.search(t):
        return False
    if ROLE_HINT.search(t):
        return True
    return any(alias in t for alias in ALIASES if len(alias) > 6)


def plain(value):
    value = html.unescape(html.unescape(value or ''))
    value = re.sub(r'</?(?:p|li|h\d|div|br|ul|ol|tr)[^>]*>', '\n', value, flags=re.I)
    value = re.sub(r'<[^>]+>', '', value)
    return re.sub(r'\n\s*\n+', '\n', value).strip()


def employment_from(text, title=''):
    if re.search(r'\bcontract(?:or)?(?:[- ]to[- ]hire)?\b', title, re.I):
        return 'Contract'
    if re.search(r'full[- ]time', text, re.I):
        return 'Full-time'
    if re.search(r'\bcontract(?:or)? (?:role|position|engagement)\b|\bw2 contract\b|\bc2c\b', text, re.I):
        return 'Contract'
    if re.search(r'part[- ]time', text, re.I):
        return 'Part-time'
    return ''


def normalize(raw, source):
    provider = source['provider']
    published_before = created_at = posted_label = None
    if provider == 'greenhouse':
        title, ident = raw.get('title', ''), str(raw['id'])
        location = (raw.get('location') or {}).get('name', '')
        description = plain(raw.get('content', ''))
        url = raw.get('absolute_url', '')
        published, kind = raw.get('first_published'), 'first_published'
        if not published:
            published_before = raw.get('updated_at')
        structured_country = None
        employment = employment_from(description, title)
    elif provider == 'ashby':
        title = raw.get('title', '')
        url = raw.get('jobUrl', '')
        ident = str(raw.get('id') or url.rstrip('/').split('/')[-1])
        location = raw.get('location', '')
        if raw.get('isRemote') and 'remote' not in location.lower():
            location = f'{location} · Remote'.strip(' ·')
        description = raw.get('descriptionPlain') or plain(raw.get('descriptionHtml', ''))
        published, kind = raw.get('publishedAt'), 'last_published'
        addr = raw.get('address') or {}
        structured_country = (addr.get('postalAddress') or addr).get('addressCountry')
        employment = {'FullTime': 'Full-time', 'Contract': 'Contract', 'PartTime': 'Part-time', 'Intern': 'Intern'}.get(raw.get('employmentType', ''), raw.get('employmentType', ''))
    elif provider == 'lever':
        title, ident = raw.get('text', ''), str(raw['id'])
        cats = raw.get('categories') or {}
        location = cats.get('location', '')
        description = '\n'.join([plain(raw.get('description', ''))] + [plain(x.get('text', '') + '\n' + x.get('content', '')) for x in raw.get('lists', [])] + [plain(raw.get('additional', ''))])
        url = raw.get('hostedUrl', '')
        # createdAt is not a documented publication timestamp; kept separately.
        published, kind = None, 'unverified'
        if isinstance(raw.get('createdAt'), (int, float)):
            created_at = datetime.fromtimestamp(raw['createdAt'] / 1000, timezone.utc).isoformat()
        structured_country = raw.get('country')
        employment = cats.get('commitment', '') or employment_from(description, title)
    elif provider == 'smartrecruiters':
        title, ident = raw.get('name', ''), str(raw.get('id'))
        loc = raw.get('location') or {}
        location = ', '.join(x for x in [loc.get('city'), (loc.get('region') or '').upper() if len(loc.get('region') or '') == 2 else loc.get('region'), 'Remote' if loc.get('remote') else None] if x)
        sections = ((raw.get('jobAd') or {}).get('sections') or {})
        description = '\n'.join(plain((sections.get(k) or {}).get('text', '')) for k in ['jobDescription', 'qualifications', 'additionalInformation'])
        url = raw.get('postingUrl') or f'https://jobs.smartrecruiters.com/{source["board"]}/{ident}'
        published, kind = raw.get('releasedDate'), 'released'
        structured_country = loc.get('country')
        employment = (raw.get('typeOfEmployment') or {}).get('label', '') or employment_from(description, title)
    else:  # workday
        info = raw.get('jobPostingInfo') or {}
        title = info.get('title') or raw.get('title', '')
        ident = info.get('jobReqId') or raw.get('externalPath', '').rsplit('_', 1)[-1]
        location = info.get('location') or raw.get('locationsText', '')
        description = plain(info.get('jobDescription', ''))
        url = info.get('externalUrl') or f'https://{source["host"]}/{source["site"]}{raw.get("externalPath", "")}'
        posted_label = raw.get('postedOn', '')
        days = 0 if re.search(r'today', posted_label, re.I) else 1 if re.search(r'yesterday', posted_label, re.I) else int((re.search(r'(\d+)', posted_label) or [0, 99])[1])
        published = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        kind = 'relative_day'
        structured_country = ((info.get('country') or {}).get('descriptor'))
        employment = info.get('timeType', '') or employment_from(description, title)
        if re.search(r'full', employment, re.I):
            employment = 'Full-time'
    pay = parse_pay(description)
    comp = raw.get('compensation') or {}
    for item in comp.get('summaryComponents', []):
        if item.get('compensationType') == 'Salary' and item.get('currencyCode') == 'USD':
            unit = {'1 YEAR': 'year', '1 HOUR': 'hour'}.get(item.get('interval'))
            if unit:
                pay = {'min': item.get('minValue'), 'max': item.get('maxValue'), 'unit': unit, 'currency': 'USD', 'evidence': 'Structured ATS compensation'}
    job = {'id': f'{provider}:{source["board"]}:{ident}', 'source_id': source['id'],
           'provider': provider, 'company': source['company'], 'company_url': source.get('company_url', ''),
           'title': title, 'location': location, 'country': structured_country,
           'description': description, 'url': url, 'apply_url': raw.get('applyUrl') or url,
           'published_at': published, 'publication_kind': kind, 'published_before': published_before,
           'created_at': created_at, 'posted_label': posted_label, 'employment_type': employment,
           'pay': pay, 'demo': False, 'closed': False,
           'source_trust': 'registry' if source.get('category') not in (None, 'custom') else 'user-added', 'first_seen_at': store.now(), 'last_checked_at': store.now()}
    # Workday's date is an estimate recomputed every run; hashing it would make every search look like an edit.
    hashed = ['title', 'description', 'location', 'pay', 'employment_type'] + ([] if kind == 'relative_day' else ['published_at'])
    job['content_hash'] = digest({k: job[k] for k in hashed})
    job['requirements'] = requirements(description)
    job['requirements_hash'] = job['content_hash']
    return job


def _recent(value, hours):
    t = parse_time(value)
    return bool(t) and t >= datetime.now(timezone.utc) - timedelta(hours=hours)


async def _list(client, source):
    """Return (all open ids, raw postings to normalise) for a source."""
    provider, board = source['provider'], source['board']
    if provider == 'greenhouse':
        r = await client.get(f'https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true')
        r.raise_for_status()
        raws = r.json().get('jobs', [])
    elif provider == 'ashby':
        r = await client.get(f'https://api.ashbyhq.com/posting-api/job-board/{board}?includeCompensation=true')
        r.raise_for_status()
        raws = [x for x in r.json().get('jobs', []) if x.get('isListed') is not False]
    elif provider == 'lever':
        r = await client.get(f'https://api.lever.co/v0/postings/{board}?mode=json')
        r.raise_for_status()
        raws = r.json()
    elif provider == 'smartrecruiters':
        raws = []
        for offset in (0, 100, 200):
            r = await client.get(f'https://api.smartrecruiters.com/v1/companies/{board}/postings', params={'limit': 100, 'offset': offset})
            r.raise_for_status()
            body = r.json()
            raws += body.get('content', [])
            if offset + 100 >= body.get('totalFound', 0):
                break
    elif provider == 'workday':
        seen, raws = set(), []
        base = f'https://{source["host"]}/wday/cxs/{board}/{source["site"]}/jobs'
        for q in WORKDAY_QUERIES:
            r = await client.post(base, json={'appliedFacets': {}, 'limit': 20, 'offset': 0, 'searchText': q})
            r.raise_for_status()
            for p in r.json().get('jobPostings', []):
                if p.get('externalPath') and p['externalPath'] not in seen:
                    seen.add(p['externalPath'])
                    raws.append(p)
    else:
        raise ValueError('Unsupported source provider')
    if not isinstance(raws, list):
        raise ValueError('Source did not return a job list')
    return raws


def _title(raw, provider):
    return raw.get('text') if provider == 'lever' else raw.get('name') if provider == 'smartrecruiters' else raw.get('title', '')


async def collect(source, client=None):
    provider, board = source['provider'], source['board']
    if not re.fullmatch(r'[\w.-]{1,100}', board, re.ASCII):
        raise ValueError('Board must be its public name, not a URL')
    own = client is None
    client = client or httpx.AsyncClient(timeout=25, headers=UA)
    failures = []
    try:
        raws = await _list(client, source)
        matched = [r for r in raws if title_matches(_title(r, provider))]
        sem = asyncio.Semaphore(3)

        async def detail(raw):
            try:
                if provider == 'greenhouse' and not raw.get('first_published') and _recent(raw.get('updated_at'), 26):
                    async with sem:
                        r = await client.get(f'https://boards-api.greenhouse.io/v1/boards/{board}/jobs/{raw["id"]}?pay_transparency=true')
                        r.raise_for_status()
                    raw = {**raw, **r.json()}
                elif provider == 'smartrecruiters':
                    if not _recent(raw.get('releasedDate'), 48):
                        return None  # stale; skip the detail call and do not store
                    async with sem:
                        r = await client.get(f'https://api.smartrecruiters.com/v1/companies/{board}/postings/{raw["id"]}')
                        r.raise_for_status()
                    raw = {**raw, **r.json()}
                elif provider == 'workday':
                    if not re.search(r'today|yesterday', raw.get('postedOn', ''), re.I):
                        return None
                    async with sem:
                        r = await client.get(f'https://{source["host"]}/wday/cxs/{board}/{source["site"]}{raw["externalPath"]}')
                        r.raise_for_status()
                    raw = {**raw, **r.json()}
            except (httpx.HTTPError, ValueError) as exc:
                failures.append(f'Detail {raw.get("id") or raw.get("externalPath")}: {type(exc).__name__}')
                if provider in ('smartrecruiters', 'workday'):
                    return None
            return raw

        details = [r for r in await asyncio.gather(*(detail(r) for r in matched)) if r]
    finally:
        if own:
            await client.aclose()
    # Parsing, requirement extraction and DB writes are CPU/IO bound: keep them off the event loop.
    jobs = await asyncio.to_thread(_persist, source, details, raws)
    source.update(last_checked_at=store.now(), status='partial' if failures else 'healthy', error=None,
                  total_open=len(raws), relevant=len(jobs), errors=failures[:20])
    store.put('sources', source['id'], source)
    return {'count': len(jobs), 'total_open': len(raws), 'warnings': failures[:5]}


def _persist(source, details, raws):
    provider = source['provider']
    jobs = [normalize(raw, source) for raw in details]
    for job in jobs:
        old = store.get('jobs', job['id'])
        if old:
            for key in ['first_seen_at', 'company_evidence', 'resolutions', 'contacts', 'skipped', 'fit_override']:
                if key in old:
                    job[key] = old[key]
            if job.get('publication_kind') == 'relative_day' and old.get('published_at'):
                prev, new = parse_time(old['published_at']), parse_time(job['published_at'])
                if prev and new and abs((new - prev).total_seconds()) < 36 * 3600:
                    job['published_at'] = old['published_at']  # keep the first, stable estimate
            if old.get('content_hash') != job['content_hash']:
                job['resolutions'] = {}  # changed requirements require a new review
        store.put('jobs', job['id'], job)
    # Only providers whose listing returns every open job can prove a posting closed.
    if provider in ('greenhouse', 'ashby', 'lever'):
        listed = {normalize_id(r, source) for r in details}  # closed, or no longer a title match
        for old in store.records_by('jobs', 'source_id', source['id']):
            if old['id'] not in listed and not old.get('closed') and not old.get('manual'):
                old.update(closed=True, last_checked_at=store.now())
                store.put('jobs', old['id'], old)
    return jobs


def normalize_id(raw, source):
    p = source['provider']
    if p == 'ashby':
        ident = str(raw.get('id') or raw.get('jobUrl', '').rstrip('/').split('/')[-1])
    else:
        ident = str(raw.get('id'))
    return f'{p}:{source["board"]}:{ident}'


def board_from_url(url):
    """Detect provider + board from a careers URL the user pastes."""
    patterns = [
        ('greenhouse', r'(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/(?:embed/job_board\?for=)?([\w-]+)'),
        ('ashby', r'jobs\.ashbyhq\.com/([\w.-]+)'),
        ('lever', r'jobs\.lever\.co/([\w.-]+)'),
        ('smartrecruiters', r'(?:jobs|careers)\.smartrecruiters\.com/([\w.-]+)'),
    ]
    for provider, pattern in patterns:
        m = re.search(pattern, url or '', re.I)
        if m:
            return {'provider': provider, 'board': m[1]}
    m = re.search(r'https?://(([\w-]+)\.wd\d+\.myworkdayjobs\.com)/(?:[a-z]{2}-[A-Z]{2}/)?([\w-]+)', url or '')
    if m:
        return {'provider': 'workday', 'board': m[2], 'host': m[1], 'site': m[3]}
    return None
