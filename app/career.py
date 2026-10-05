"""Career profile: everything about you, in one editable Markdown file.

data/career/career.md is the source of truth (edit it in the app or any editor).
It is parsed into structured JSON for matching and tailoring:

  roles      company, title, story, themed bullet bank, keywords, metrics
  summaries  named summary variants
  projects   name + description
  skills     category, skill, where it's proven
  rules      your own tailoring rules ("name Fidelity Investments once")

Lines containing [FILL ...], [CHECK ...], [TO CONFIRM ...] or {unverified} are kept
for you but never used by the tailoring engine.
"""
import json
import re
from pathlib import Path

from docx import Document
from docx.enum.text import WD_COLOR_INDEX
from docx.table import Table
from docx.text.paragraph import Paragraph

from . import store

DIR = store.ROOT / 'data' / 'career'
MD = DIR / 'career.md'
JSON = DIR / 'career.json'
UNVERIFIED = re.compile(r'\[(FILL|CHECK|TO CONFIRM)[^\]]*\]|\{unverified\}', re.I)
VARIANT_TAG = re.compile(r'\s*\((current|first version|current resume|older|alt)[^)]*\)\s*$', re.I)
NUMBER = re.compile(r'\d[\d,.]*\s*[%xX+KkMm]?')


# ---------------------------------------------------------------- DOCX -> Markdown
def _cell(text):
    return re.sub(r'\s+', ' ', text.replace('|', '/')).strip()


def docx_to_markdown(path_or_bytes):
    doc = Document(path_or_bytes)
    lines = []
    first = True
    for child in doc.element.body.iterchildren():
        tag = child.tag.rsplit('}', 1)[-1]
        if tag == 'p':
            p = Paragraph(child, doc)
            text = p.text.strip()
            if not text:
                continue
            flagged = any(r.font.highlight_color == WD_COLOR_INDEX.YELLOW for r in p.runs if r.text.strip())
            if flagged and not UNVERIFIED.search(text):
                text += ' {unverified}'
            style = (p.style.name if p.style is not None else '') or ''
            is_list = p._p.pPr is not None and p._p.pPr.numPr is not None
            if first:
                lines += [f'# {text}', '']
                first = False
            elif style.startswith('Heading 1'):
                lines += ['', f'## {text}', '']
            elif style.startswith('Heading 2'):
                lines += ['', f'### {text}', '']
            elif style.startswith('Heading 3'):
                lines += ['', f'#### {text}', '']
            elif is_list or 'List' in style:
                lines.append(f'- {text}')
            else:
                lines += [text, '']
        elif tag == 'tbl':
            table = Table(child, doc)
            rows = [[_cell(c.text) for c in r.cells] for r in table.rows]
            if not rows:
                continue
            lines += ['', '| ' + ' | '.join(rows[0]) + ' |', '|' + '---|' * len(rows[0])]
            lines += ['| ' + ' | '.join(r) + ' |' for r in rows[1:]]
            lines.append('')
    return re.sub(r'\n{3,}', '\n\n', '\n'.join(lines)).strip() + '\n'


# ---------------------------------------------------------------- Markdown -> JSON
def _blocks(md):
    """Flatten Markdown into blocks tagged with their h2/h3/h4 context."""
    h2 = h3 = h4 = ''
    blocks, table = [], None
    for raw in md.splitlines():
        line = raw.rstrip()
        if line.startswith('|'):
            cells = [c.strip() for c in line.strip('|').split('|')]
            if all(re.fullmatch(r'-{3,}', c or '---') for c in cells):
                continue
            if table is None:
                table = {'type': 'table', 'h2': h2, 'h3': h3, 'h4': h4, 'header': cells, 'rows': []}
                blocks.append(table)
            else:
                table['rows'].append(cells)
            continue
        table = None
        if not line.strip():
            continue
        m = re.match(r'^(#{1,4})\s+(.*)', line)
        if m:
            level, text = len(m[1]), m[2].strip()
            if level == 2:
                h2, h3, h4 = re.sub(r'^\d+\.\s*', '', text), '', ''
            elif level == 3:
                h3, h4 = text, ''
            elif level == 4:
                h4 = text
            continue
        kind = 'li' if re.match(r'^\s*[-*]\s+', line) else 'p'
        blocks.append({'type': kind, 'h2': h2, 'h3': h3, 'h4': h4, 'text': re.sub(r'^\s*[-*]\s+', '', line).strip()})
    return blocks


def _verified(text):
    return not UNVERIFIED.search(text or '')


NOTE_AFTER_SENTENCE = re.compile(r'(?<=[.!?])\s*\([^()]*\)\s*$')


def _clean_bullet(text):
    """Drop your annotations ("(current resume)", "(shorter, without scope)") so they never reach a resume."""
    text = VARIANT_TAG.sub('', text).strip()
    while NOTE_AFTER_SENTENCE.search(text):
        text = NOTE_AFTER_SENTENCE.sub('', text).strip()
    return text


def parse(md):
    blocks = _blocks(md)
    sections = {}
    for b in blocks:
        sections.setdefault(b['h2'], []).append(b)
    career = {'roles': [], 'summaries': [], 'projects': [], 'skills': [], 'rules': [], 'identity': {},
              'timeline': [], 'keyword_index': [], 'checklist': [], 'notes': []}
    for name, items in sections.items():
        low = name.lower()
        bank = [b for b in items if b['type'] == 'li' and b['h3'].lower().startswith('bullet bank')]
        if bank:
            company, _, title = name.partition(',')
            role = {'key': re.sub(r'[^a-z0-9]+', '-', company.lower()).strip('-'), 'company': company.strip(), 'title': title.strip(),
                    'story': [b['text'] for b in items if b['type'] == 'p' and 'story' in b['h3'].lower() and _verified(b['text'])],
                    'bullets': [], 'keywords': [], 'metrics': [], 'details': {}}
            for b in bank:
                role['bullets'].append({'text': _clean_bullet(b['text']), 'theme': b['h4'] or 'General',
                                        'verified': _verified(b['text']),
                                        'variant': (VARIANT_TAG.search(b['text']) or [''])[0].strip(' ()')})
            for b in items:
                if b['type'] == 'p' and b['h3'].lower().startswith('keywords'):
                    role['keywords'] += [k.strip() for k in re.split(r'\s*[·•;]\s*', b['text']) if k.strip()]
                if b['type'] == 'table' and b['h3'].lower().startswith('metric'):
                    for r in b['rows']:
                        row = dict(zip([h.lower() for h in b['header']], r))
                        role['metrics'].append({'metric': row.get('metric', r[0]), 'value': row.get('value', ''),
                                                'method': row.get('baseline / method', ''), 'status': row.get('status', '')})
                if b['type'] == 'table' and not b['h3'] and [h.lower() for h in b['header'][:2]] == ['field', 'detail']:
                    role['details'].update({r[0]: r[1] for r in b['rows'] if len(r) > 1})
            career['roles'].append(role)
            continue
        for b in items:
            if (b['type'] == 'p' and 'summary' in b['h3'].lower() and b['h4'] and _verified(b['text']) and len(b['text']) > 80
                    and not re.match(r'(tip|note)\b', b['text'], re.I)):
                career['summaries'].append({'name': b['h4'], 'text': b['text']})
            elif b['type'] == 'li' and 'how to use' in low:
                career['rules'].append(b['text'])
            elif b['type'] == 'li' and ('checklist' in low or 'consistency' in low):
                career['checklist'].append(b['text'])
            elif b['type'] == 'p' and 'project' in low and b['h3']:
                proj = next((x for x in career['projects'] if x['name'] == b['h3']), None)
                if proj is None:
                    proj = {'name': b['h3'], 'text': [], 'verified': True}
                    career['projects'].append(proj)
                if _verified(b['text']):
                    proj['text'].append(b['text'])
                else:
                    proj['verified'] = False
            elif b['type'] == 'p' and 'experience timeline' in b['h3'].lower():
                career['notes'].append(b['text'])
            elif b['type'] == 'table':
                head = [h.lower() for h in b['header']]
                if head[:2] == ['field', 'value']:
                    career['identity'].update({r[0]: r[1] for r in b['rows'] if len(r) > 1})
                elif head[:3] == ['role', 'company', 'dates']:
                    career['timeline'] += [dict(zip(head, r)) for r in b['rows']]
                elif 'skill' in head and len(head) >= 3:
                    for r in b['rows']:
                        row = dict(zip(head, r))
                        where = row.get("where it's proven", row.get('where it’s proven', r[-1]))
                        career['skills'].append({'category': row.get('category', ''), 'skill': row.get('skill', ''), 'evidence': where,
                                                 'proven': _verified(where) and not re.search(r'skills only', where, re.I)})
                elif head and 'keyword' in head[0]:
                    career['keyword_index'] += [dict(zip(['keyword', 'evidence', 'bullet'], r)) for r in b['rows']]
    for p in career['projects']:
        p['text'] = ' '.join(p['text'])
    return career


# ---------------------------------------------------------------- storage
def version():
    """Changes whenever the career profile is edited, so packages are re-tailored."""
    import hashlib
    return hashlib.sha256(JSON.read_bytes()).hexdigest()[:12] if JSON.exists() else None


def load():
    if JSON.exists():
        try:
            return json.loads(JSON.read_text(encoding='utf-8'))
        except ValueError:
            pass
    if MD.exists():
        return save_markdown(MD.read_text(encoding='utf-8'))['career']
    return None


def markdown():
    return MD.read_text(encoding='utf-8') if MD.exists() else ''


def save_markdown(md):
    DIR.mkdir(parents=True, exist_ok=True)
    MD.write_text(md, encoding='utf-8')
    data = parse(md)
    JSON.write_text(json.dumps(data, indent=2), encoding='utf-8')
    return {'career': data, 'summary': summarize(data)}


def summarize(c):
    if not c:
        return None
    return {'roles': [{'company': r['company'], 'title': r['title'], 'bullets': len(r['bullets']),
                       'verified': sum(b['verified'] for b in r['bullets']), 'keywords': len(r['keywords'])} for r in c['roles']],
            'summaries': len(c['summaries']), 'projects': len(c['projects']),
            'skills': len(c['skills']), 'proven_skills': sum(s['proven'] for s in c['skills']),
            'rules': len(c['rules']), 'unverified_lines': len(UNVERIFIED.findall(markdown()))}


def role_for(career, header):
    """Match a resume role header ("AI Engineer | InRhythm | Dallas, TX") to a career role."""
    if not career:
        return None
    h = (header or '').lower()
    best = None
    for r in career['roles']:
        name = r['company'].lower()
        short = re.split(r'[\s(]', name)[0]
        if name in h or (len(short) > 2 and re.search(r'\b' + re.escape(short) + r'\b', h)):
            best = r
            break
    return best


def known_numbers(career):
    """Numbers you've stated in verified career text; tailoring may reuse these, never invent others."""
    out = set()
    if not career:
        return out
    texts = [s['text'] for s in career['summaries']] + [p['text'] for p in career['projects'] if p['verified']]
    for r in career['roles']:
        texts += r['story'] + [b['text'] for b in r['bullets'] if b['verified']]
        texts += [m['value'] for m in r['metrics'] if m.get('value') and _verified(m.get('status', '') + m.get('method', ''))]
    for t in texts:
        out |= {n.rstrip('%xX+KkMm').replace(',', '').rstrip('.') for n in re.findall(r'\d[\d,.]*', t)}
    return {n for n in out if n}
