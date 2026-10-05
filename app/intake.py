"""Ivy, the intake agent: turn a pasted job link or pasted job description into a job.

Order of preference for a link:
1. A board Sam already reads (Greenhouse, Lever, Ashby, SmartRecruiters, Workday):
   the posting's own API, giving exact title, location, posted date and pay.
2. Any other page: the schema.org JobPosting data most career sites embed for Google Jobs.
3. Fallback: the page's readable text.
Pages behind logins (LinkedIn, Indeed, Glassdoor) usually can't be read; paste the text instead.
"""
import asyncio
import ipaddress
import json
import re
import socket
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse, parse_qs

import httpx

from . import store
from .collectors import UA, board_from_url, normalize, plain
from .policy import digest, parse_pay
from .scoring import requirements

LOGIN_WALLS = re.compile(r'(^|\.)(linkedin\.com|indeed\.com|glassdoor\.com|ziprecruiter\.com|wellfound\.com|dice\.com)$', re.I)
BROWSER_UA = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36',
              'Accept': 'text/html,application/xhtml+xml'}


class IntakeError(ValueError):
    pass


# ---------------------------------------------------------------- safe fetching
async def _check_host(url):
    parsed = urlparse(url)
    if parsed.scheme not in ('https', 'http') or not parsed.hostname or parsed.username:
        raise IntakeError('Use a normal https:// job link')
    infos = await asyncio.to_thread(socket.getaddrinfo, parsed.hostname, parsed.port or 443)
    if not infos or any(not ipaddress.ip_address(i[4][0]).is_global for i in infos):
        raise IntakeError('That address is not a public website')


async def fetch_page(url, client):
    """GET a public page, following up to 5 redirects and re-checking every hop."""
    for _ in range(6):
        await _check_host(url)
        r = await client.get(url, headers=BROWSER_UA, follow_redirects=False)
        if r.is_redirect and r.headers.get('location'):
            url = urljoin(url, r.headers['location'])
            continue
        if r.status_code in (401, 403, 429, 999):
            raise IntakeError('This site blocks automated reading (login or bot wall). Paste the job description text instead.')
        r.raise_for_status()
        if len(r.content) > 3_000_000:
            raise IntakeError('Page is too large')
        return str(r.url), r.text
    raise IntakeError('Too many redirects')


# ---------------------------------------------------------------- known job boards
def _posting_parts(url):
    """Pull the posting id out of a known board URL."""
    found = board_from_url(url)
    if not found:
        q = parse_qs(urlparse(url).query)
        if q.get('gh_jid'):
            jid = q['gh_jid'][0]
            if q.get('for'):
                return {'provider': 'greenhouse', 'board': q['for'][0], 'posting': jid}
            # A company careers page wrapping Greenhouse: find the board from a job Sam already collected.
            with store.connection() as c:
                row = c.execute("SELECT id FROM records WHERE kind='jobs' AND id LIKE ?", (f'greenhouse:%:{jid}',)).fetchone()
            if row:
                return {'provider': 'greenhouse', 'board': row['id'].split(':')[1], 'posting': jid}
            return {'provider': 'greenhouse', 'board': None, 'posting': jid}  # board found from the page itself
        return None
    p, path = found['provider'], urlparse(url).path
    if p == 'greenhouse':
        m = re.search(r'/jobs/(\d+)', path) or re.search(r'gh_jid=(\d+)', url)
    elif p == 'lever':
        m = re.search(r'/[\w.-]+/([0-9a-f-]{36})', path)
    elif p == 'ashby':
        m = re.search(r'/[\w.-]+/([0-9a-f-]{36})', path)
    elif p == 'smartrecruiters':
        m = re.search(r'/[\w.-]+/(\d{6,})', path)
    else:  # workday: /<site>/job/<location>/<slug>_<req>
        m = re.search(r'(/job/.+)$', path)
    return {**found, 'posting': m[1]} if m else None


async def from_board(parts, client, url):
    p, board, pid = parts['provider'], parts['board'], parts['posting']
    source = {'id': f'{p}:{board}', 'provider': p, 'board': board, 'company': board.replace('-', ' ').title(),
              'host': parts.get('host'), 'site': parts.get('site'), 'category': 'manual'}
    known = store.get('sources', source['id'])
    if known:
        source.update(company=known['company'], company_url=known.get('company_url', ''), category=known.get('category', 'manual'))
    if p == 'greenhouse':
        r = await client.get(f'https://boards-api.greenhouse.io/v1/boards/{board}/jobs/{pid}?pay_transparency=true')
        r.raise_for_status()
        raw = r.json()
        source['company'] = raw.get('company_name') or source['company']
    elif p == 'lever':
        r = await client.get(f'https://api.lever.co/v0/postings/{board}/{pid}?mode=json')
        r.raise_for_status()
        raw = r.json()
    elif p == 'ashby':
        r = await client.get(f'https://api.ashbyhq.com/posting-api/job-board/{board}?includeCompensation=true')
        r.raise_for_status()
        raw = next((j for j in r.json().get('jobs', []) if str(j.get('id')) == pid or pid in (j.get('jobUrl') or '')), None)
        if not raw:
            raise IntakeError('That Ashby posting is no longer listed')
    elif p == 'smartrecruiters':
        r = await client.get(f'https://api.smartrecruiters.com/v1/companies/{board}/postings/{pid}')
        r.raise_for_status()
        raw = r.json()
        source['company'] = (raw.get('company') or {}).get('name') or source['company']
    else:
        r = await client.get(f'https://{parts["host"]}/wday/cxs/{board}/{parts["site"]}{pid}')
        r.raise_for_status()
        raw = {**r.json(), 'externalPath': pid, 'postedOn': (r.json().get('jobPostingInfo') or {}).get('postedOn', '')}
    job = normalize(raw, source)
    job['via'] = f'{p} job board API'
    # A board from the verified registry is the company's own careers system: same trust as Sam's finds.
    job['source_trust'] = 'registry' if known and known.get('category') not in (None, 'custom', 'manual') else 'user-added'
    return job


# ---------------------------------------------------------------- any other page
class _Scripts(HTMLParser):
    def __init__(self):
        super().__init__()
        self.jsonld, self.meta, self.title, self._in, self._buf = [], {}, '', None, []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == 'script' and (a.get('type') or '').lower() == 'application/ld+json':
            self._in, self._buf = 'ld', []
        elif tag == 'title':
            self._in, self._buf = 'title', []
        elif tag == 'meta' and (a.get('property') or a.get('name')):
            self.meta[(a.get('property') or a.get('name')).lower()] = a.get('content') or ''

    def handle_data(self, data):
        if self._in:
            self._buf.append(data)

    def handle_endtag(self, tag):
        if self._in == 'ld' and tag == 'script':
            self.jsonld.append(''.join(self._buf))
            self._in = None
        elif self._in == 'title' and tag == 'title':
            self.title = ''.join(self._buf).strip()
            self._in = None


def _walk(node):
    if isinstance(node, list):
        for x in node:
            yield from _walk(x)
    elif isinstance(node, dict):
        yield node
        for key in ('@graph', 'itemListElement', 'mainEntity'):
            if key in node:
                yield from _walk(node[key])


def job_posting(html):
    """The schema.org JobPosting embedded in a page, if any."""
    parser = _Scripts()
    try:
        parser.feed(html)
    except Exception:
        pass
    for block in parser.jsonld:
        try:
            data = json.loads(block.strip())
        except ValueError:
            continue
        for node in _walk(data):
            kind = node.get('@type')
            if kind == 'JobPosting' or (isinstance(kind, list) and 'JobPosting' in kind):
                return node, parser
    return None, parser


def _location(node):
    locs = node.get('jobLocation') or []
    locs = locs if isinstance(locs, list) else [locs]
    out = []
    for loc in locs:
        addr = (loc or {}).get('address') or {}
        if isinstance(addr, dict):
            parts = [addr.get('addressLocality'), addr.get('addressRegion')]
            out.append(', '.join(p for p in parts if p) or addr.get('addressCountry', ''))
    if str(node.get('jobLocationType', '')).upper() == 'TELECOMMUTE':
        out.append('Remote')
    return ' · '.join(o for o in out if o)


def _country(node):
    locs = node.get('jobLocation') or []
    locs = locs if isinstance(locs, list) else [locs]
    for loc in locs:
        c = ((loc or {}).get('address') or {}).get('addressCountry')
        if isinstance(c, dict):
            c = c.get('name')
        if c:
            return c
    req = node.get('applicantLocationRequirements')
    if isinstance(req, dict):
        return req.get('name')
    return None


def _salary(node):
    s = node.get('baseSalary') or {}
    v = s.get('value') if isinstance(s, dict) else None
    if not isinstance(v, dict) or (s.get('currency') or 'USD') != 'USD':
        return None
    unit = {'YEAR': 'year', 'HOUR': 'hour'}.get(str(v.get('unitText', '')).upper())
    lo, hi = v.get('minValue') or v.get('value'), v.get('maxValue') or v.get('value')
    if not unit or lo is None:
        return None
    return {'min': float(lo), 'max': float(hi), 'unit': unit, 'currency': 'USD', 'evidence': 'schema.org baseSalary on the posting'}


def _employment(node):
    t = node.get('employmentType')
    t = ' '.join(t) if isinstance(t, list) else str(t or '')
    if re.search(r'full', t, re.I):
        return 'Full-time'
    if re.search(r'contract|temporary', t, re.I):
        return 'Contract'
    if re.search(r'part', t, re.I):
        return 'Part-time'
    if re.search(r'intern', t, re.I):
        return 'Intern'
    return ''


def readable_text(html):
    html = re.sub(r'(?is)<(script|style|noscript|svg|header|footer|nav)[^>]*>.*?</\1>', ' ', html)
    return plain(html)


async def from_page(url):
    async with httpx.AsyncClient(timeout=25) as client:
        final, html = await fetch_page(url, client)
    node, parser = job_posting(html)
    meta = parser.meta
    if node:
        org = node.get('hiringOrganization') or {}
        company = org.get('name') if isinstance(org, dict) else str(org)
        return {'title': plain(node.get('title') or ''), 'company': company or meta.get('og:site_name') or urlparse(final).hostname,
                'description': plain(node.get('description') or ''), 'location': _location(node), 'country': _country(node),
                'published_at': node.get('datePosted'), 'employment_type': _employment(node), 'pay': _salary(node),
                'url': final, 'apply_url': node.get('url') or final, 'via': 'schema.org JobPosting on the page'}
    text = readable_text(html)
    if len(text) < 500:
        raise IntakeError("Couldn't find a job description on that page (it may load with JavaScript or need a login). Paste the job description text instead.")
    title = meta.get('og:title') or parser.title
    return {'title': re.split(r'\s[|\-–]\s', title)[0].strip() if title else '', 'company': meta.get('og:site_name') or (urlparse(final).hostname or '').replace('www.', ''),
            'description': text[:40000], 'location': '', 'country': None, 'published_at': None, 'employment_type': '', 'pay': None,
            'url': final, 'apply_url': final, 'via': 'page text (no structured job data found; check the details)'}


# ---------------------------------------------------------------- pasted text
def guess_title_company(text):
    lines = [l.strip(' \t-•*#') for l in text.splitlines() if l.strip()]
    title = company = ''
    for l in lines[:8]:
        m = re.match(r'(?i)^(job title|title|role|position)\s*[:\-]\s*(.+)$', l)
        if m:
            title = m[2].strip()
        m = re.match(r'(?i)^(company|employer|organization)\s*[:\-]\s*(.+)$', l)
        if m:
            company = m[2].strip()
    if not title:
        title = next((l for l in lines[:5] if 3 <= len(l) <= 90 and re.search(r'(?i)engineer|developer|scientist|architect|consultant|analyst|manager|lead|specialist', l)), '')
    if not company:
        m = re.search(r'(?i)\b(?:at|join)\s+([A-Z][\w&.\- ]{1,40}?)(?:[,.!]|\s+(?:we|is|as|and|to)\b)', text[:1500])
        company = m[1].strip() if m else ''
    return title, company


# ---------------------------------------------------------------- build the job record
def make_job(data, demo=False):
    description = (data.get('description') or '').strip()
    if len(description) < 200:
        raise IntakeError('The job description is too short to match against (paste the full posting).')
    title = (data.get('title') or '').strip() or 'Untitled role'
    company = (data.get('company') or '').strip() or 'Unknown company'
    key = data.get('url') or description[:2000]
    job = {'id': ('demo-manual:' if demo else 'manual:') + digest(key)[:14], 'source_id': 'manual', 'provider': 'manual',
           'company': company, 'company_url': '', 'title': title, 'location': data.get('location') or '',
           'country': data.get('country'), 'description': description, 'url': data.get('url') or '',
           'apply_url': data.get('apply_url') or data.get('url') or '', 'published_at': data.get('published_at'),
           'publication_kind': 'manual', 'employment_type': data.get('employment_type') or '',
           'pay': data.get('pay') or parse_pay(description), 'demo': demo, 'closed': False, 'manual': True,
           'source_trust': 'user-added', 'via': data.get('via', 'pasted text'),
           'first_seen_at': store.now(), 'last_checked_at': store.now()}
    job['content_hash'] = digest({k: job[k] for k in ['title', 'description', 'location', 'pay', 'employment_type']})
    job['requirements'] = requirements(description)
    job['requirements_hash'] = job['content_hash']
    return job


async def intake(url=None, text=None, title='', company='', location='', apply_url='', demo=False):
    """Returns a job record ready for the Gatekeeper. Raises IntakeError with a human message."""
    if url:
        url = url.strip()
        host = urlparse(url).hostname or ''
        if LOGIN_WALLS.search(host):
            raise IntakeError(f'{host} needs a login, so I can\'t read it. Open the posting, click "Apply" to reach the company\'s own page and paste that link, or paste the job description text.')
        parts = _posting_parts(url)
        try:
            if parts and parts['provider'] == 'greenhouse' and not parts['board']:
                async with httpx.AsyncClient(timeout=25) as client:
                    _, html = await fetch_page(url, client)
                m = re.search(r'greenhouse\.io/(?:embed/job_(?:board|app)(?:/js)?\?for=|v1/boards/|)([\w-]+)', html)
                parts = {**parts, 'board': m[1]} if m and m[1] not in ('embed', 'v1') else None
            if parts:
                async with httpx.AsyncClient(timeout=25, headers=UA) as client:
                    job = await from_board(parts, client, url)
                job.update(id=('demo-manual:' if demo else 'manual:') + digest(url)[:14], manual=True,
                           source_id='manual', board_source=job.get('source_id'),
                           publication_kind='manual', published_at=job.get('published_at'), url=url, demo=demo)
                job['apply_url'] = job.get('apply_url') or url
                return job
            data = await from_page(url)
        except IntakeError:
            raise
        except httpx.HTTPStatusError as exc:
            raise IntakeError(f'The site answered {exc.response.status_code}; the posting may be closed. Paste the job description text instead.')
        except (httpx.HTTPError, OSError) as exc:
            raise IntakeError(f"Couldn't reach that page ({type(exc).__name__}). Check the link or paste the job description text.")
        for k, v in (('title', title), ('company', company), ('location', location), ('apply_url', apply_url)):
            if v:
                data[k] = v
        return make_job(data, demo)
    if text:
        g_title, g_company = guess_title_company(text)
        return make_job({'title': title or g_title, 'company': company or g_company, 'location': location,
                         'description': plain(text) if '<' in text else text, 'url': apply_url, 'apply_url': apply_url,
                         'via': 'pasted job description'}, demo)
    raise IntakeError('Paste a job link or the job description text')
