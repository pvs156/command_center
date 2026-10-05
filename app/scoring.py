"""Three separate, explainable scores.

1. Base match   - weighted coverage of JD skills by verified base-resume evidence.
                  This is the gate (>=60 prepare, 50-59 review, <50 skip).
2. ATS keyword  - literal JD keyword presence in a resume's text, blended with
                  title alignment and parseability. Computed for the base resume
                  and again for the tailored resume to show the lift.
3. Parseability - structural checks (see resume.parseability).

None of these are an employer's ATS score or an interview probability.
"""
import re
from functools import lru_cache

from .taxonomy import find_skills, skill_counts


@lru_cache(maxsize=512)
def text_skills(text):
    return frozenset(find_skills(text))

REQUIRED_HEAD = re.compile(r'(requirements|required|qualifications|what you.ll (?:need|bring)|who you are|must.have|you have|minimum|basic qualifications|about you|skills)', re.I)
PREFERRED_HEAD = re.compile(r'(preferred|nice.to.have|bonus|plus|extra credit|ideally|desired|good to have)', re.I)
NOISE_HEAD = re.compile(r'(benefits|perks|about (?:us|the company)|our (?:values|mission)|equal opportunity|compensation|salary|why join)', re.I)
SENIORITY = re.compile(r'\b(senior|sr\.?|staff|principal|lead|junior|jr\.?|associate|mid|entry|i{1,3}|iv|level \d)\b', re.I)


def requirements(description):
    """Split a JD into weighted skills: required (2), preferred (1), mentioned (1)."""
    block, reqs = 'body', {}
    counts = skill_counts(description)
    for line in (description or '').splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if len(stripped) < 70:
            if PREFERRED_HEAD.search(stripped):
                block = 'preferred'
            elif NOISE_HEAD.search(stripped):
                block = 'noise'
            elif REQUIRED_HEAD.search(stripped):
                block = 'required'
        if block == 'noise':
            continue
        inline = block
        if re.match(r'\W*(required|requirements|must[- ]have|minimum qualifications|basic qualifications)\b', stripped, re.I):
            inline = 'required'
        elif re.match(r'\W*(preferred|nice[- ]to[- ]have|bonus|plus)\b', stripped, re.I) or (PREFERRED_HEAD.search(stripped) and len(stripped) > 70):
            inline = 'preferred'
        for skill, alias in find_skills(stripped).items():
            kind = {'required': 'required', 'preferred': 'preferred'}.get(inline, 'mentioned')
            old = reqs.get(skill)
            rank = {'required': 3, 'mentioned': 2, 'preferred': 1}
            if not old or rank[kind] > rank[old['kind']]:
                reqs[skill] = {'kind': kind, 'alias': alias, 'count': counts.get(skill, 1)}
    for skill, r in reqs.items():
        r['weight'] = 2 if r['kind'] == 'required' else 1.5 if r['kind'] == 'mentioned' else 1
    return reqs


_FACT_CACHE = {}


def _fact_skills(profile):
    key = (profile.get('version'), len(profile.get('facts', [])))
    if key not in _FACT_CACHE or key[0] is None:
        _FACT_CACHE[key] = {f['id']: set(find_skills(f['text'])) for f in profile.get('facts', []) if f.get('verified')}
    return _FACT_CACHE[key]


def evidence_map(reqs, profile, extra_skills=()):
    """skill -> list of verified fact ids (resume) or ['user-confirmed']."""
    out = {}
    fact_skills = _fact_skills(profile or {})
    for skill in reqs:
        ids = [fid for fid, found in fact_skills.items() if skill in found]
        if not ids and skill in extra_skills:
            ids = ['user-confirmed']
        out[skill] = ids
    return out


def base_match(reqs, evidence):
    """Weighted coverage counting only base-resume facts (not user confirmations)."""
    if not reqs:
        return None
    total = sum(r['weight'] for r in reqs.values())
    got = sum(r['weight'] for s, r in reqs.items() if any(i != 'user-confirmed' for i in evidence.get(s, [])))
    return round(100 * got / total)


def potential_match(reqs, evidence):
    """Coverage including skills the user confirmed but the resume does not show yet."""
    if not reqs:
        return None
    total = sum(r['weight'] for r in reqs.values())
    got = sum(r['weight'] for s, r in reqs.items() if evidence.get(s))
    return round(100 * got / total)


def title_alignment(job_title, resume_text):
    core = SENIORITY.sub(' ', re.sub(r'\(.*?\)|[-,/|]', ' ', job_title or '')).lower().split()
    core = [w for w in core if len(w) > 1 and w not in ('and', 'the', 'of', 'for', 'engineer', 'developer')]
    if not core:
        return 100
    text = (resume_text or '').lower()
    return round(100 * sum(1 for w in core if re.search(r'\b' + re.escape(w) + r'\b', text)) / len(core))


def ats_keyword(reqs, resume_text, job_title, parse_score):
    """Keyword presence weighted by requirement type and JD frequency."""
    if not reqs:
        return {'score': None, 'found': [], 'missing': []}
    present = text_skills(resume_text or '')
    weight = {s: r['weight'] * min(3, r['count']) for s, r in reqs.items()}
    total = sum(weight.values())
    found = sorted(s for s in reqs if s in present)
    coverage = 100 * sum(weight[s] for s in found) / total
    title = title_alignment(job_title, resume_text)
    score = round(0.7 * coverage + 0.1 * title + 0.2 * (parse_score or 0))
    missing = sorted((s for s in reqs if s not in present), key=lambda s: -weight[s])
    return {'score': score, 'coverage': round(coverage), 'title': title, 'parse': parse_score,
            'found': found, 'missing': missing}
