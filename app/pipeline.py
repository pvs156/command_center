"""What is waiting on the human. Every item is delivered by one courier (Remy, the Reviewer);
`from_agent` records which teammate produced it.

Every pending decision is owned by one agent. That agent walks to your office in
the pixel scene until you act, so nothing sits silently in one stage.
"""
from datetime import date

INFO_FLAGS = {'sponsorship'}  # shown on the card, but don't need an explicit acknowledgement


def _freshness(a):
    return next((c['status'] for c in a['checks'] if c['key'] == 'freshness'), 'review')


def flags(job, a):
    """Issues a human should look at before applying. Hard exclusions only appear for jobs you added yourself."""
    out = []
    if job.get('manual'):
        for c in a['checks']:
            if c['status'] == 'excluded':
                out.append({'key': c['key'], 'label': 'Outside your rules: ' + c['label'], 'evidence': (c.get('evidence') or '')[:240],
                            'ack': True, 'severity': 'exclusion'})
    for c in a['checks']:
        if c['status'] == 'review':
            out.append({'key': c['key'], 'label': c['label'], 'evidence': (c.get('evidence') or '')[:240], 'ack': True})
    sp = a['sponsorship']
    if sp['status'] == 'unknown':
        out.append({'key': 'sponsorship', 'label': 'Sponsorship not stated: ask the recruiter early', 'evidence': '', 'ack': False})
    elif sp['status'] == 'supported':
        out.append({'key': 'sponsorship', 'label': 'Sponsorship stated in posting', 'evidence': sp['evidence'][:240], 'ack': False, 'good': True})
    return out


def preparable(job, a, cfg, has_package, refresh=False):
    """Auto-tailor rule: not excluded (freshness may be 'review'), base match ≥ threshold (or you said tailor anyway).
    refresh=True re-tailors a stale package; it was tailored while fresh, so the posting's age no longer matters."""
    if has_package or job.get('skipped') or job.get('closed'):
        return False
    if job.get('manual'):
        return True  # you brought it in: always tailored, whatever the score
    if a['score'] is None:
        return False
    if any(c['status'] == 'excluded' and not (refresh and c['key'] == 'freshness') for c in a['checks']):
        return False
    return a['score'] >= cfg['match_min'] or (job.get('fit_override') and a['score'] >= cfg['review_min'])


def inbox(jobs, packages, sources, profile, cfg, demo_mode):
    items = []
    by_job = {j['id']: j for j in jobs}
    if not profile and not demo_mode:
        items.append({'id': 'resume', 'kind': 'resume_needed', 'agent': 'analyst', 'title': 'I need your base resume',
                      'detail': f'{sum(1 for j in jobs if j["assessment"]["status"] != "excluded")} roles are waiting to be scored. Upload it under Resume evidence.',
                      'actions': ['open_resume']})
    packaged = {p['job_id'] for p in packages if not p.get('stale')}
    stale = []
    for p in sorted(packages, key=lambda p: -(p['scores'].get('base') or 0)):
        j = by_job.get(p['job_id'])
        if not j or j.get('skipped') or j['outcome'].get('applied_at') or j['outcome']['stage'] in ('applied', 'rejected', 'withdrawn'):
            continue
        if p.get('stale'):
            stale.append(j)
            continue
        a = j['assessment']
        if not j.get('manual') and any(c['status'] == 'excluded' and c['key'] != 'freshness' for c in a['checks']):
            continue  # the posting changed into something we can't apply to
        fl = flags(j, a)
        needs_ack = [f for f in fl if f['ack']]
        items.append({'id': 'pkg:' + p['id'], 'kind': 'package_ready', 'agent': 'eligibility' if needs_ack else 'reviewer',
                      'job_id': j['id'], 'package_id': p['id'], 'title': j['title'], 'company': j['company'],
                      'location': j.get('location', ''), 'employment_type': j.get('employment_type', ''),
                      'match': p['scores'].get('base'), 'priority': j.get('priority'), 'ats_before': p['scores'].get('ats_base'), 'ats_after': p['scores'].get('ats_tailored'),
                      'added': p['resume'].get('added_to_skills', [])[:8], 'gaps': p['resume'].get('gaps', [])[:6],
                      'flags': fl, 'apply_url': j.get('apply_url') or j.get('url'), 'url': j.get('url'),
                      'posted_at': j.get('published_at') or j.get('created_at'), 'posted_label': j.get('posted_label') or '',
                      'publication_kind': j.get('publication_kind'), 'manual': bool(j.get('manual')), 'via': j.get('via'), 'pay': a.get('pay'), 'mode': p.get('mode'),
                      'quality': (p['resume'].get('quality') or {}).get('score'),
                      'coverage': [len((p['resume'].get('quality') or {}).get('covered', [])), len((p['resume'].get('quality') or {}).get('musts', []))],
                      'from_bank': (p['resume'].get('quality') or {}).get('from_bank', 0),
                      'contact_plan': p.get('contact_plan', []), 'contacts': p.get('contacts', []),
                      'drafts': p.get('drafts', {}), 'outreach_log': p.get('outreach_log', []), 'drafts_for': p.get('drafts_for'),
                      'actions': ['download', 'open_application', 'applied', 'skip']})
    for j in jobs:
        a = j['assessment']
        if j['id'] in packaged or j.get('skipped') or a['score'] is None or j.get('fit_override') or j.get('manual'):
            continue
        hard = any(c['status'] == 'excluded' for c in a['checks'] if c['key'] != 'fit')
        if not hard and cfg['review_min'] <= a['score'] < cfg['match_min'] and _freshness(a) != 'excluded':
            items.append({'id': 'fit:' + j['id'], 'kind': 'fit_review', 'agent': 'analyst', 'job_id': j['id'],
                          'title': j['title'], 'company': j['company'], 'match': a['score'],
                          'detail': f'{a["score"]}% base match is in your review band ({cfg["review_min"]}–{cfg["match_min"] - 1}%). Missing: {", ".join(a["missing"][:5]) or "n/a"}.',
                          'posted_at': j.get('published_at') or j.get('created_at'), 'url': j.get('url'),
                          'actions': ['tailor_anyway', 'skip']})
    if stale:
        items.append({'id': 'stale', 'kind': 'stale_packages', 'agent': 'writer',
                      'title': f'{len(stale)} package{"s" if len(stale) > 1 else ""} need re-tailoring',
                      'detail': 'Your resume, confirmed skills, rules or the posting changed. ' + ', '.join(j['company'] for j in stale[:6]),
                      'actions': ['retailor']})
    failing = [s for s in sources if s.get('status') == 'error' and s.get('enabled', True)]
    if failing and not demo_mode:
        items.append({'id': 'sources', 'kind': 'sources_failing', 'agent': 'scout',
                      'title': f'{len(failing)} board{"s" if len(failing) > 1 else ""} failed on my last search',
                      'detail': ', '.join(s['company'] for s in failing[:8]) + ('…' if len(failing) > 8 else ''),
                      'actions': ['pause_failing']})
    today = date.today().isoformat()
    for j in jobs:
        o = j['outcome']
        if o.get('follow_up') and o['follow_up'] <= today and o['stage'] not in ('rejected', 'withdrawn', 'offer'):
            items.append({'id': 'fu:' + j['id'], 'kind': 'follow_up', 'agent': 'writer', 'job_id': j['id'],
                          'package_id': o.get('package_id'), 'title': j['title'], 'company': j['company'],
                          'detail': f'Follow-up due {o["follow_up"]} (stage: {o["stage"]}). A follow-up draft is in the package.',
                          'actions': ['open_package', 'followed_up']})
    # One courier: whoever produced an item, Remy brings it to your desk in a fixed order.
    order = {'package_ready': 0, 'follow_up': 1, 'fit_review': 2, 'stale_packages': 3, 'resume_needed': 4, 'sources_failing': 5}
    for i in items:
        i['from_agent'] = i['agent']
        i['agent'] = 'reviewer'
    items.sort(key=lambda i: (order.get(i['kind'], 9), -(i.get('priority') if i.get('priority') is not None else (i.get('match') or 0))))
    return items
