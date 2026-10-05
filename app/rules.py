"""Tailoring rules from docs/TAILORING-RESEARCH.md, as code.

[DETERMINISTIC] rules are checked here (lint + hard gates). [PROMPT] rules are fed
to the model writer. Rule numbers refer to the research doc.
"""
import re

from .taxonomy import find_skills

ACTION_VERBS = set("""built developed engineered designed architected implemented prototyped programmed integrated containerized
automated instrumented configured fine-tuned trained indexed embedded orchestrated authored created shipped deployed launched
released delivered productionized migrated rolled scaled operationalized hardened published onboarded piloted reduced cut
accelerated optimized improved increased raised streamlined refactored consolidated stabilized eliminated lowered boosted
upgraded debugged resolved simplified evaluated benchmarked measured analyzed diagnosed profiled validated tested audited
investigated quantified identified researched modeled forecasted led owned drove spearheaded mentored coordinated partnered
presented advised championed standardized defined directed facilitated connected added calibrated introduced enforced secured
maintained sustained wrote established converted extended exposed modernized""".split())
WEAK = ['responsible for', 'worked on', 'helped with', 'helped to', 'assisted with', 'involved in', 'participated in', 'tasked with',
        'duties included', 'utilized', 'leveraged', 'various', 'familiar with', 'exposure to', 'hands-on with', 'proficient in',
        'dealt with', 'was part of', 'successfully', 'effectively', 'seamlessly', 'results-driven', 'team player',
        'detail-oriented', 'go-getter', 'passionate about', 'and more', 'etc.']
AI_TELLS = ['delve', 'realm', 'intricate', 'showcasing', 'pivotal', 'leverage', 'synergy', 'cutting-edge', 'passionate',
            'spearheaded a paradigm', 'tapestry', 'testament to', 'meticulous', 'game-changer', 'unlock', 'elevate']
SENIOR_WORDS = ['seasoned', 'veteran', 'expert', 'thought leader', 'decade', 'senior', 'staff', 'principal']
PRONOUNS = re.compile(r"\b(I|me|my|mine|we|our|us)\b")
NUMBER = re.compile(r'\d[\d,.]*')

PROMPT_RULES = """
Write like a strong human recruiter-facing engineer, not like an AI. Rules (from recruiter and ATS research):
1. Truth first: every number, %, $, employer, title, date, tool and model name must come from CAREER EVIDENCE or the RESUME. Never invent a metric; if no number exists use honest scope words ("for internal sales teams").
2. Mirror the job's exact skill names and casing for skills the candidate truly has (e.g. "LangGraph", "retrieval-augmented generation (RAG)"); spell out an acronym once on first use.
3. Put the job's top keywords in the summary and in the first bullets of the most recent role. Coverage beats repetition: a keyword at most once per bullet and at most 3 times on the whole resume.
4. Summary: 2-3 sentences, at most 60 words, no first person. Structure: [target title] + accurate years ("3+ years") + the 2-3 strongest job-matched capabilities each tied to a shipped system or outcome + a domain/fit hook. Lead with production impact, not a tool list. Never append lists like "Hands-on with X and Y" or "Proficient in ...".
5. Bullets: start with a strong past-tense action verb (present tense only for ongoing work in the current role); shape = verb + what was built/changed + method/tool (job term within the first 8 words) + measurable result or honest scope. One idea per bullet, no semicolons, 120-200 characters, never over 220.
6. AI-engineering framing: prefer production deployment, evals (LLM-as-judge, regression evals), RAG internals (hybrid search, embeddings, reranking), agents and tool use, guardrails/human-in-the-loop, latency and cost. Phrase outcomes as engineering results ("cut review time 35%"), naming the stack after the result.
7. Seniority honesty: with under 5 years, never use "seasoned", "expert", "veteran", "senior", "decade".
8. Never add qualifiers that change a claim (concurrent, real-time, large-scale, high-performance, global, thousands of) unless the evidence says so.
9. Never use: responsible for, worked on, helped with, utilized, leveraged, various, delve, realm, intricate, showcasing, pivotal, synergy, cutting-edge, passionate, seamless(ly).
10. Vary verbs: no two bullets in one role start with the same verb.
11. Follow the CANDIDATE RULES exactly (e.g. naming conventions, mention a client only once).
""".strip()


def _first_word(text):
    m = re.match(r"\W*([A-Za-z][\w-]*)", text or '')
    return m[1].lower() if m else ''


def bullet_issues(text, current_role=False):
    """Lint one bullet. Returns a list of (severity, message)."""
    issues, low = [], (text or '').lower()
    if len(text) > 220:
        issues.append(('warn', f'{len(text)} chars (keep ≤ 220, ~2 lines)'))
    verb = _first_word(text)
    if verb and verb not in ACTION_VERBS and not (current_role and verb.endswith('s') and verb[:-1] in ACTION_VERBS):
        issues.append(('info', f'starts with “{verb}”, not a strong action verb'))
    if PRONOUNS.search(text):
        issues.append(('warn', 'first-person pronoun'))
    for w in WEAK:
        if w in low:
            issues.append(('warn', f'weak phrase “{w}”'))
    for w in AI_TELLS:
        if re.search(r'\b' + re.escape(w), low):
            issues.append(('warn', f'AI-sounding word “{w}”'))
    if ';' in text:
        issues.append(('info', 'semicolon: keep one idea per bullet'))
    tools = re.findall(r'[A-Z][\w./+-]+', text)
    if len(re.findall(r',', text)) >= 5 and not NUMBER.search(text):
        issues.append(('info', 'reads like a tool list with no result'))
    return issues


def summary_issues(text, years=None):
    issues, low = [], (text or '').lower()
    words = len(text.split())
    if words > 60:
        issues.append(('warn', f'summary is {words} words (keep ≤ 60)'))
    if PRONOUNS.search(text):
        issues.append(('warn', 'first-person pronoun in summary'))
    if re.search(r'hands-on with|proficient in|skilled in:', low):
        issues.append(('warn', 'tacked-on skills list in summary'))
    if (years or 0) < 5:
        for w in SENIOR_WORDS:
            if re.search(r'\b' + w + r'\b', low):
                issues.append(('warn', f'seniority word “{w}” with < 5 years'))
    for w in AI_TELLS:
        if re.search(r'\b' + re.escape(w), low):
            issues.append(('warn', f'AI-sounding word “{w}”'))
    return issues


def keyword_overuse(text, reqs, limit=3):
    out = []
    for skill, r in reqs.items():
        alias = r.get('alias') or skill
        n = len(re.findall(r'(?<![\w-])' + re.escape(alias) + r'(?![\w-])', text, re.I))
        if n > limit:
            out.append((skill, n))
    return out


# Words a model likes to add that change what you claimed ("40 users" -> "40 concurrent users").
INFLATIONS = ['concurrent', 'real-time', 'realtime', 'high-performance', 'high performance', 'large-scale', 'at scale',
              'massive', 'millions', 'billions', 'enterprise-grade', 'mission-critical', 'world-class', 'state-of-the-art',
              'industry-leading', 'cutting-edge', 'petabyte', 'global', 'fortune 500', 'thousands of', 'hundreds of',
              'single-handedly', 'sole', 'led a team', 'managed a team', 'architected', 'spearheaded', 'pioneered', 'invented']


def inflation(new, base_text):
    low_new, low_base = (new or '').lower(), (base_text or '').lower()
    return [w for w in INFLATIONS if re.search(r'(?<![\w-])' + re.escape(w) + r'(?![\w-])', low_new)
            and not re.search(r'(?<![\w-])' + re.escape(w) + r'(?![\w-])', low_base)]


def hard_gate(new, allowed_numbers, allowed_skills, base_text='', pronouns_ok=False, max_len=260):
    """Fail-closed truthfulness checks for model-written text (rules 1, 3, 33)."""
    if not new or not new.strip():
        return 'empty'
    inflated = inflation(new, base_text)
    if inflated:
        return 'adds a claim not in your evidence: ' + ', '.join(inflated)
    nums = {n.rstrip('.').replace(',', '') for n in NUMBER.findall(new)}
    bad = nums - allowed_numbers
    if bad:
        return 'number not in your evidence: ' + ', '.join(sorted(bad))
    extra = set(find_skills(new)) - set(allowed_skills) - set(find_skills(base_text))
    if extra:
        return 'skill without evidence: ' + ', '.join(sorted(extra))
    if not pronouns_ok and PRONOUNS.search(new):
        return 'first-person pronoun'
    if len(new) > max_len:
        return 'too long'
    return None


STOP = {'a', 'an', 'the', 'and', 'or', 'for', 'of', 'to', 'in', 'on', 'with', 'by', 'from', 'using', 'across', 's'}


def similarity(a, b):
    norm = lambda t: (t or '').lower().replace('’', "'")
    ta = set(re.findall(r'[a-z0-9]+', norm(a))) - STOP
    tb = set(re.findall(r'[a-z0-9]+', norm(b))) - STOP
    return len(ta & tb) / max(1, len(ta | tb))
