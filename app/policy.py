"""Explainable gates and a deliberately labelled skill-coverage baseline."""
import hashlib
import json
import re
from datetime import datetime, timedelta, timezone

from .resume import parseability, plain_text
from .scoring import ats_keyword, base_match, evidence_map, potential_match, requirements
from .taxonomy import find_skills

ROLE_HINT = re.compile(r'\b(ai|genai|generative|llm|rag|agentic|conversational|retrieval|knowledge|context|intelligent automation|forward deployed|llmops)\b|python.*(developer|backend)|backend.*python', re.I)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def skills(text):
    return set(find_skills(text))


def parse_time(value):
    if not value:
        return None
    try:
        value = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return value if value.tzinfo else None
    except (ValueError, TypeError):
        return None


NEGATIVE = [re.compile(p, re.I) for p in [
        r'\bno\s+(?:visa\s+|employment\s+|immigration\s+)?sponsorship\b',
        r'\b(?:cannot|can not|do not|does not|will not|unable to|not able to)\s+(?:provide\s+|offer\s+)?(?:visa\s+)?sponsor',
        r'\bwithout\s+(?:the need for\s+)?(?:current or future\s+|employment\s+|visa\s+)?sponsorship',
        r'\bsponsorship\s+(?:is\s+|will be\s+)?not\s+(?:available|provided|offered|supported)',
        r'\bnot\s+eligible\s+for\s+(?:visa\s+)?sponsorship',
        r'\b(?:do not|does not|will not)\s+(?:currently\s+)?(?:provide\s+|offer\s+|support\s+)(?:employment\s+|visa\s+|immigration\s+)?sponsorship',
        r'\b(?:must|should)\s+not\s+require\b.{0,70}\bsponsorship',
        r'\b(?:us|u\.s\.)\s+(?:citizens|citizenship)\s+(?:only|required)',
        r'\b(?:u\.?s\.?|united states)\s+citizenship\s+(?:is\s+)?(?:required|mandatory)',
        r'\bmust\s+be\s+(?:a\s+)?(?:u\.?s\.?|united states)\s+citizen',
        r'\b(?:only|open to)\s+(?:u\.?s\.?\s+)?citizens?\s+(?:and|or|&)\s+(?:green card|permanent resident)',
        r'\b(?:active|current)\s+(?:secret|top secret|ts/sci|ts)\b.{0,30}\bclearance',
        r'\bno\s+(?:h-?1b|visa)\b',
        r"\b(?:aren't|are not|isn't|is not)\s+able\s+to\s+(?:successfully\s+)?sponsor(?!.{0,60}\bevery\b)",
        r'\b(?:candidates|applicants)\s+(?:who\s+)?(?:require|requiring|need|needing)\s+(?:visa\s+)?sponsorship\s+will\s+not',
]]
# Conditions that may or may not apply to the user's authorization: human review.
CONDITIONAL = [re.compile(p, re.I) for p in [r'\b(?:no|not accepting|cannot accept|do not accept)\s+(?:opt|cpt|ead)\b',
               r'\bgreen card holders?\s+only\b']]
POSITIVE = [re.compile(p, re.I) for p in [r'\b(?:provide|offer|support|available for)\s+(?:visa\s+|immigration\s+)?sponsorship',
                r'\bsponsorship\s+(?:is\s+)?available', r'\bwill\s+sponsor\b',
                r'\b(?:do|does|can|we)\s+sponsor\s+(?:work\s+)?(?:visas?|h-?1bs?)\b',
                r'\b(?:open to|welcome)\s+(?:candidates\s+)?(?:requiring|who require|needing)\s+(?:visa\s+)?sponsorship']]
AUTH_WORDS = re.compile(r'sponsor|visa|citizen|clearance|h-?1b|\bopt\b|\bcpt\b|\bead\b|green card|permanent resident', re.I)


def sponsorship(text):
    text = re.sub(r'\bU\.S\.(?:A\.)?', 'US', text or '')  # keep "U.S. citizen" in one sentence
    if not AUTH_WORDS.search(text):
        return {'status': 'unknown', 'evidence': '', 'label': 'Sponsorship unstated'}
    sentences = [x for x in re.split(r'(?<=[.!?;])\s+|\n+', text) if AUTH_WORDS.search(x)]
    for s in sentences:
        if any(p.search(s) for p in NEGATIVE):
            return {'status': 'excluded', 'evidence': s.strip(), 'label': 'Explicit restriction'}
    for s in sentences:
        if any(p.search(s) for p in CONDITIONAL):
            return {'status': 'review', 'evidence': s.strip(), 'label': 'Work-authorization condition'}
    for s in sentences:
        if any(p.search(s) for p in POSITIVE):
            # Conditional language never becomes confirmed support.
            state = 'review' if re.search(r'\b(may|case.by.case|depending|eligible candidates)\b', s, re.I) else 'supported'
            return {'status': state, 'evidence': s.strip(), 'label': 'Conditional support' if state == 'review' else 'Sponsorship stated'}
    return {'status': 'unknown', 'evidence': '', 'label': 'Sponsorship unstated'}


FOREIGN_COUNTRIES = (r'\b(Canada|United Kingdom|UK|England|Scotland|Ireland|India|Germany|France|Spain|Portugal|Italy|Netherlands|Belgium|'
                     r'Switzerland|Austria|Poland|Czechia|Czech Republic|Romania|Hungary|Greece|Sweden|Norway|Denmark|Finland|Estonia|'
                     r'Lithuania|Latvia|Ukraine|Turkey|T[uü]rkiye|Israel|UAE|United Arab Emirates|Saudi Arabia|Qatar|Egypt|Nigeria|Kenya|'
                     r'South Africa|Australia|New Zealand|Singapore|Japan|Korea|China|Hong Kong|Taiwan|Philippines|Vietnam|Indonesia|'
                     r'Malaysia|Thailand|Pakistan|Mexico|Brazil|Argentina|Colombia|Chile|Peru|Costa Rica|Uruguay|LATAM|EMEA|APAC|Europe)\b')
FOREIGN_CITIES = (r'\b(London|Toronto|Vancouver|Montreal|Bangalore|Bengaluru|Hyderabad|Pune|Chennai|Berlin|Munich|Paris|Amsterdam|Dublin|'
                  r'Madrid|Barcelona|Lisbon|Warsaw|Krakow|Prague|Zurich|Stockholm|Istanbul|Tel Aviv|Dubai|Sydney|Melbourne|Tokyo|'
                  r'Seoul|S[aã]o Paulo|Mexico City)\b')
US_STATES = 'AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC'.split()


def country(location, structured=None):
    if structured:
        return 'US' if str(structured).upper() in ['US', 'USA', 'UNITED STATES', 'UNITED STATES OF AMERICA'] else 'other'
    if re.search(r'\b(united states|usa)\b', location, re.I) or re.search(r'\bUS\b|\bU\.S\.', location):
        return 'US'
    if re.search(FOREIGN_COUNTRIES, location, re.I):
        return 'other'
    if re.search(r',\s*(?:' + '|'.join(US_STATES) + r')\b', location):  # before cities: Dublin, OH and Paris, TX are US
        return 'US'
    if re.search(FOREIGN_CITIES, location, re.I):
        return 'other'
    if re.search(r'\b(New York|San Francisco|Dallas|Austin|Seattle|Boston|Chicago|Los Angeles|Atlanta)\b', location, re.I):
        return 'US'
    return 'unknown'


def experience(text):
    matches = []
    preferred = False
    for line in text.splitlines():
        if re.search(r'preferred|nice.to.have|bonus qualifications', line, re.I):
            preferred = True
        if re.search(r'required|minimum qualifications|basic qualifications', line, re.I):
            preferred = False
        for m in re.finditer(r'\b(\d{1,2})\s*(?:[-–]\s*\d{1,2})?\s*\+?\s*(?:or more\s+)?years?\b(?:\s+of)?(?:\s+[\w/&+.-]+){0,5}?\s+(?:experience|exp)\b', line, re.I):
            if not preferred and not re.search(r'preferred|nice.to.have', line, re.I):
                matches.append((int(m[1]), line.strip()))
    return max(matches, default=(None, ''), key=lambda m: m[0] if m[0] is not None else -1)


def parse_pay(text):
    # USD is explicit; do not mistake CAD/AUD dollar signs for USD.
    if re.search(r'\b(CAD|AUD|EUR|GBP)\b|C\$|A\$', text, re.I):
        return None
    pattern = r'\$\s*(\d[\d,]*(?:\.\d+)?)\s*([kK])?\s*(?:[-–]|to)\s*\$?\s*(\d[\d,]*(?:\.\d+)?)\s*([kK])?'
    # Also "120,000 USD - 189,750 USD" (common on Workday).
    usd = r'(\d[\d,]*(?:\.\d+)?)\s*([kK])?\s*USD\s*(?:[-–]|to)\s*(\d[\d,]*(?:\.\d+)?)\s*([kK])?\s*USD'
    for m in [*re.finditer(pattern, text), *re.finditer(usd, text, re.I)]:
        lo = float(m[1].replace(',', '')) * (1000 if m[2] else 1)
        hi = float(m[3].replace(',', '')) * (1000 if m[4] else 1)
        if m[4] and not m[2] and lo < 1000:
            lo *= 1000
        nearby = text[max(0, m.start()-70):m.end()+100]
        unit = 'hour' if re.search(r'per hour|/hr|/hour|hourly', nearby, re.I) else 'year' if lo >= 20000 else None
        if unit:
            return {'min': lo, 'max': hi, 'unit': unit, 'currency': 'USD', 'evidence': nearby.strip()}
    return None


def freshness(job, at=None):
    """(status, label, evidence) for the 24h window. Cheap; recomputed on every read.

    Catch-up: a job Sam first found right after the laptop was off/asleep carries catch_up_hours
    (24-72). For 24h after discovery it counts as fresh if posted within that longer look-back.
    """
    at = at or datetime.now(timezone.utc)
    day = timedelta(hours=24)
    pub = parse_time(job.get('published_at'))
    seen = parse_time(job.get('first_seen_at'))
    lookback = job.get('catch_up_hours') or 0
    away = bool(lookback > 24 and seen and at - seen <= day)
    cutoff = (seen - timedelta(hours=lookback)) if away else at - day
    away_label = f'Posted while you were away (looked back {int(lookback)}h)'

    if job.get('manual'):  # you chose this job: its age is shown, never used to filter
        return 'pass', 'Added by you' + (f' · posted {pub.date().isoformat()}' if pub else ''), job.get('published_at') or 'Posting date not published'
    bound = parse_time(job.get('published_before'))
    if job.get('publication_kind') == 'relative_day' and pub:
        # pub = collection time minus the reported whole days, so the window keeps moving.
        hint, age = job.get('posted_label', ''), at - pub
        if re.search(r'today', hint, re.I) and age <= day:
            return 'pass', hint, 'Workday reports whole days, not timestamps'
        if away and pub >= cutoff - day:  # whole-day granularity: give the look-back one extra day
            return 'pass', f'{hint} · {away_label}', 'Workday reports whole days, not timestamps'
        return ('review' if age <= 2 * day else 'excluded'), hint or 'Relative posting day', 'Workday reports whole days, not timestamps; recheck the posting date'
    if job.get('created_at') and not pub:
        created = parse_time(job['created_at'])
        recent = bool(created) and cutoff <= created <= at
        # Publication can't precede creation, so a recent creation proves freshness.
        return ('pass' if recent else 'excluded'), ((away_label if away and created < at - day else 'Created in last 24h') if recent else 'Created over 24h ago'), f'Lever createdAt {job["created_at"]}; publication time itself is not published'
    if bound and not pub and bound < cutoff:
        return 'excluded', 'Not updated in 24h', f'Last updated {job.get("published_before")}; publication cannot be newer'
    fresh = pub is not None and cutoff <= pub <= at
    if fresh:
        if away and pub < at - day:
            return 'pass', away_label, job.get('published_at')
        return 'pass', 'Recently republished' if job.get('publication_kind') == 'last_published' else 'Published in last 24h', job.get('published_at')
    if pub and pub <= at:
        return 'excluded', 'Older than 24h', job.get('published_at')
    return 'review', 'Publication age unverified', job.get('published_at') or 'First seen is not a publication date'


def hours_since_posted(job, at=None):
    at = at or datetime.now(timezone.utc)
    when = parse_time(job.get('published_at')) or parse_time(job.get('created_at'))
    return None if when is None else max(0.0, (at - when).total_seconds() / 3600)


def priority(job, score):
    """Ranking: base-resume match first, plus a recency bonus so new postings rise (apply early)."""
    h = hours_since_posted(job)
    bonus = 0 if h is None else 15 if h <= 6 else 10 if h <= 12 else 6 if h <= 24 else 3 if h <= 48 else 1 if h <= 72 else 0
    return (score or 0) + bonus


_PARSE = {}


def _parse_score(profile):
    if not profile.get('header'):
        return 70
    key = profile.get('version')
    if key is None or key not in _PARSE:
        _PARSE[key] = parseability(profile)['score']
    return _PARSE[key]


def assess(job, profile, settings, at=None):
    at = at or datetime.now(timezone.utc)
    checks = []
    def add(key, status, label, evidence=''):
        checks.append({'key': key, 'status': status, 'label': label, 'evidence': evidence})
    c = country(job.get('location', ''), job.get('country'))
    if c == 'unknown' and re.search(FOREIGN_COUNTRIES, job.get('title', ''), re.I):  # e.g. "Engineer - Turkey", location "Remote"
        c = 'other'
    add('country', 'pass' if c == 'US' else 'excluded' if c == 'other' else 'review',
        'United States' if c == 'US' else 'Outside US' if c == 'other' else 'Confirm country', job.get('location', ''))
    sp = sponsorship(job.get('description', ''))
    add('sponsorship', 'pass' if sp['status'] in ('supported', 'unknown') else sp['status'], sp['label'], sp['evidence'])
    years, quote = experience(job.get('description', ''))
    add('experience', 'review' if years is None else 'excluded' if years > settings['max_experience'] else 'pass',
        'Confirm required experience' if years is None else f'{years}+ years required', quote)
    kind = job.get('employment_type', '').lower().replace('-', '').replace('_', '').replace(' ', '')
    add('employment', 'pass' if kind in ('fulltime', 'contract', 'contractor', 'contracttohire') else 'excluded' if kind in ('intern', 'internship', 'parttime') else 'review',
        job.get('employment_type') or 'Confirm employment type')
    add('freshness', *freshness(job, at))
    pay = job.get('pay') or parse_pay(job.get('description', ''))
    company = job.get('company_evidence', {})
    verified = bool(company.get('official_url') and company.get('people_url') and company.get('note') and company.get('reviewed_at'))
    if not verified and job.get('source_trust') == 'registry' and not company:
        # The posting came from the established company's own careers system (curated, live-verified registry).
        verified = True
        company = {'note': f'Official {job.get("provider", "ATS")} careers board for {job.get("company")} from the curated registry'}
    if pay and pay.get('currency') == 'USD' and pay.get('unit') in ('year', 'hour'):
        floor = settings['annual_min'] if pay['unit'] == 'year' else settings['hourly_min']
        upper, lower = pay.get('max'), pay.get('min')
        if lower is not None and lower >= floor:
            status = 'pass'
        elif upper is not None and upper < floor:
            status = 'review' if pay['unit'] == 'hour' and upper >= settings['hourly_review_min'] else 'excluded'
        else:
            status = 'review'
        add('pay', status, f'${lower:,.0f}–${upper:,.0f}/{pay["unit"]}' if lower is not None and upper is not None else 'Confirm compensation', pay.get('evidence', 'Disclosed by source'))
    else:
        add('pay', 'pass' if not pay and verified else 'review',
            'Pay undisclosed · company evidence reviewed' if not pay and verified else 'Pay undisclosed — verify company and people',
            'Allowed after company review per your preference; salary remains unknown')
    if not pay:
        add('company', 'pass' if verified else 'review', 'Company evidence reviewed' if verified else 'Company and real people need review', company.get('note', ''))
    if job.get('closed'):
        add('availability', 'excluded', 'Posting no longer open')
    reqs = job.get('requirements') if job.get('requirements_hash') == job.get('content_hash') and job.get('requirements') is not None else requirements(job.get('description', ''))
    confirmed = (profile or {}).get('confirmed_skills', [])
    evidence = evidence_map(reqs, profile, confirmed) if profile else {s: [] for s in reqs}
    matched = sorted(s for s, ids in evidence.items() if any(i != 'user-confirmed' for i in ids))
    confirmed_only = sorted(s for s, ids in evidence.items() if ids == ['user-confirmed'])
    missing = sorted((s for s, ids in evidence.items() if not ids), key=lambda s: -reqs[s]['weight'])
    score = base_match(reqs, evidence) if profile else None
    ats = None
    if profile:
        ats = ats_keyword(reqs, plain_text(profile), job.get('title', ''), _parse_score(profile))
    if not profile:
        add('profile', 'review', 'Your base resume is needed')
    elif len(reqs) < 3:
        add('fit', 'review', 'Too few recognized skills to score reliably', f'{len(reqs)} recognized skills in this description')
    else:
        add('fit', 'pass' if score >= settings['match_min'] else 'excluded' if score < settings['review_min'] else 'review', f'{score}% base-resume match', 'Weighted: required skills ×2, mentioned ×1.5, preferred ×1. Only base-resume facts count; not an employer ATS score')
    status = 'excluded' if any(c['status'] == 'excluded' for c in checks) else 'review' if any(c['status'] == 'review' for c in checks) else 'qualified'
    return {'status': status, 'score': score, 'matched': matched, 'missing': missing,
            'confirmed_only': confirmed_only, 'potential': potential_match(reqs, evidence) if profile else None,
            'requirements': reqs, 'ats': ats,
            'evidence': evidence, 'checks': checks, 'sponsorship': sp, 'pay': pay,
            'rubric': 'weighted-skill-coverage-v2', 'profile_version': profile.get('version') if profile else None,
            'computed_at': at.isoformat()}
