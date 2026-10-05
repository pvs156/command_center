"""Turn confirmed resume text into a structured, citable evidence profile."""
import re
from datetime import date

from .taxonomy import find_skills

SECTION_NAMES = {
    'summary': ['summary', 'professional summary', 'profile', 'objective', 'about', 'career summary'],
    'skills': ['skills', 'technical skills', 'core skills', 'skills & tools', 'technologies', 'core competencies', 'tech stack'],
    'experience': ['experience', 'professional experience', 'work experience', 'employment', 'employment history', 'work history'],
    'projects': ['projects', 'personal projects', 'selected projects', 'academic projects', 'key projects'],
    'education': ['education', 'academics'],
    'certifications': ['certifications', 'certificates', 'licenses', 'awards', 'achievements', 'publications'],
}
MONTHS = 'jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec|january|february|march|april|june|july|august|september|october|november|december'
DATE = rf'(?:(?:{MONTHS})\.?\s+\d{{4}}|\d{{1,2}}/\d{{4}}|\d{{4}})'
DATE_RANGE = re.compile(rf'({DATE})\s*(?:[-–—]|to)\s*(present|current|now|{DATE})', re.I)
BULLET = re.compile(r'^\s*(?:[•●▪◦‣∙·*\-–]|\d+[.)])\s+')
EMAIL = re.compile(r'[\w.+-]+@[\w-]+\.[\w.-]+')
PHONE = re.compile(r'(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}')
NUMBER = re.compile(r'\d+(?:[.,]\d+)?%?')


KEYWORDS = [('summary', r'summary|profile|objective'), ('experience', r'experience|employment|work history'),
            ('skills', r'skills|competencies|tech stack|technologies'), ('projects', r'projects?'),
            ('education', r'education|academics'), ('certifications', r'certifications?|licenses|awards|publications')]


def section_key(line):
    clean = re.sub(r'[^a-z& ]', '', line.lower()).strip()
    for key, names in SECTION_NAMES.items():
        if clean in names:
            return key
    # Short heading lines such as "EDUCATION & CERTIFICATIONS" or "TECHNICAL SKILLS & TOOLS".
    words = clean.replace('&', ' ').split()
    if 0 < len(words) <= 4 and (line.strip().isupper() or len(words) <= 3) and ':' not in line:
        for key, pattern in KEYWORDS:
            if re.search(r'\b(' + pattern + r')\b', clean):
                return key
    return None


def _month_index(token, end=False):
    token = token.strip().lower().rstrip('.')
    if token in ('present', 'current', 'now'):
        today = date.today()
        return today.year * 12 + today.month - 1
    m = re.match(rf'({MONTHS})\.?\s+(\d{{4}})', token, re.I)
    if m:
        month = [x[:3] for x in MONTHS.split('|')].index(m[1][:3].lower()) % 12
        return int(m[2]) * 12 + month
    m = re.match(r'(\d{1,2})/(\d{4})', token)
    if m:
        return int(m[2]) * 12 + int(m[1]) - 1
    m = re.match(r'(\d{4})', token)
    if m:
        return int(m[1]) * 12 + (11 if end else 0)
    return None


def parse(text, name='Candidate'):
    """Return a profile dict with facts (citable lines) and a role structure."""
    facts, roles, contact_lines = [], [], []
    section, current_role, pending_header = 'header', None, None

    def add(kind, text_, sect, role=None):
        fact = {'id': f'F{len(facts) + 1}', 'section': sect, 'kind': kind, 'text': text_, 'verified': True}
        if role is not None:
            fact['role'] = role
        facts.append(fact)
        return fact

    lines = [raw.rstrip() for raw in text.splitlines()]
    for i, raw in enumerate(lines):
        line = raw.strip()
        if not line:
            continue
        if line.startswith('#'):
            key = section_key(line.lstrip('#')) or line.lstrip('#').strip().lower()
            section, current_role, pending_header = key, None, None
            continue
        key = section_key(line)
        if key and len(line) < 40:
            section, current_role, pending_header = key, None, None
            continue
        if section == 'header':
            contact_lines.append(line)
            continue
        is_bullet = bool(BULLET.match(line))
        body = BULLET.sub('', line).strip()
        if section in ('experience', 'projects'):
            has_dates = bool(DATE_RANGE.search(line))
            nxt = next((l.strip() for l in lines[i + 1:] if l.strip()), '')
            looks_header = not is_bullet and len(body) < 120 and not body.endswith('.')
            if looks_header and has_dates:
                header = f'{pending_header} | {body}' if pending_header else body
                pending_header = None
                current_role = len(roles)
                m = DATE_RANGE.search(header)
                roles.append({'index': current_role, 'section': section, 'header': header,
                              'start': _month_index(m[1]) if m else None,
                              'end': _month_index(m[2], end=True) if m else None, 'fact_ids': []})
                roles[-1]['fact_ids'].append(add('header', header, section, current_role)['id'])
                continue
            if looks_header and not has_dates and DATE_RANGE.search(nxt) and not BULLET.match(nxt):
                pending_header = body  # e.g. "Company — City" above "Title  Jan 2022 – Present"
                continue
            if looks_header and section == 'projects' and not has_dates and (BULLET.match(nxt) or current_role is None):
                current_role = len(roles)
                roles.append({'index': current_role, 'section': section, 'header': body, 'start': None, 'end': None, 'fact_ids': []})
                roles[-1]['fact_ids'].append(add('header', body, section, current_role)['id'])
                continue
            if pending_header:
                body = f'{pending_header} {body}'
                pending_header = None
            fact = add('bullet', body, section, current_role)
            if current_role is not None:
                roles[current_role]['fact_ids'].append(fact['id'])
            continue
        kind = {'summary': 'summary', 'skills': 'skills', 'education': 'education'}.get(section, 'other')
        add(kind, body, section)

    header = {'name': name, 'lines': contact_lines}
    if contact_lines and not EMAIL.search(contact_lines[0]) and len(contact_lines[0]) < 60:
        header['name'] = contact_lines[0]
        header['lines'] = contact_lines[1:]
    joined = ' '.join(contact_lines)
    header['email'] = (EMAIL.search(joined) or [None])[0]
    header['phone'] = (PHONE.search(joined) or [None])[0]
    header['linkedin'] = (re.search(r'linkedin\.com/in/[\w-]+', joined, re.I) or [None])[0]
    profile = {'name': header['name'], 'header': header, 'facts': facts, 'roles': roles,
               'years_experience': years_of_experience(roles)}
    profile['resume_skills'] = sorted(find_skills('\n'.join(f['text'] for f in facts)))
    return profile


def years_of_experience(roles):
    spans = sorted((r['start'], r['end']) for r in roles if r['section'] == 'experience' and r['start'] is not None and r['end'] is not None and r['end'] >= r['start'])
    months, cursor = 0, None
    for start, end in spans:
        if cursor is None or start > cursor:
            months += end - start + 1
            cursor = end
        elif end > cursor:
            months += end - cursor
            cursor = end
    return round(months / 12, 1)


def plain_text(profile):
    return '\n'.join(f['text'] for f in profile.get('facts', []))


def parseability(profile):
    """ATS-style parse checks on the base resume. Not any vendor's real ATS."""
    header = profile.get('header', {})
    facts = profile.get('facts', [])
    sections = {f['section'] for f in facts}
    text = plain_text(profile)
    words = len(text.split())
    exp_roles = [r for r in profile.get('roles', []) if r['section'] == 'experience']
    checks = [
        ('Email address', bool(header.get('email'))),
        ('Phone number', bool(header.get('phone'))),
        ('LinkedIn URL', bool(header.get('linkedin'))),
        ('Summary section', 'summary' in sections),
        ('Skills section', 'skills' in sections),
        ('Experience section', 'experience' in sections),
        ('Education section', 'education' in sections),
        ('Dates on every role', bool(exp_roles) and all(r['start'] for r in exp_roles)),
        ('2+ bullets per role', bool(exp_roles) and all(len(r['fact_ids']) >= 3 for r in exp_roles)),
        ('Quantified results', sum(1 for f in facts if f['kind'] == 'bullet' and NUMBER.search(f['text'])) >= 3),
        ('Length 350–1,100 words', 350 <= words <= 1100),
    ]
    score = round(100 * sum(ok for _, ok in checks) / len(checks))
    return {'score': score, 'checks': [{'label': l, 'ok': ok} for l, ok in checks], 'words': words}
