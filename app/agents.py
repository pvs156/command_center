"""Application-package pipeline. Each worker emits real events the office replays.

Deterministic code owns gates, scores, validation and state; models (when
configured) only propose rewrites that validators accept or reject.
"""
import asyncio
import ipaddress
import socket
from urllib.parse import urlparse

import httpx
from . import store
from .collectors import plain
from .outreach import analysis_view, contact_plan, drafts, llm_drafts
from .policy import digest
from .resume import parseability

# Bump when tailoring rules change so existing packages are re-tailored automatically.
TAILOR_VERSION = 6
SIGNATURE_SETTINGS = ('annual_min', 'hourly_min', 'hourly_review_min', 'match_min', 'review_min', 'max_experience', 'provider')


async def public_page(url):
    parsed = urlparse(url)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.port not in (None, 443):
        raise ValueError('Only public HTTPS pages are supported')
    addresses = await asyncio.to_thread(socket.getaddrinfo, parsed.hostname, 443)
    if not addresses or any(not ipaddress.ip_address(x[4][0]).is_global for x in addresses):
        raise ValueError('Private or local addresses are not allowed')
    async with httpx.AsyncClient(timeout=18, follow_redirects=False) as c:
        async with c.stream('GET', url, headers={'User-Agent': 'JobCommandCenter/0.2'}) as r:
            r.raise_for_status()
            if r.is_redirect:
                raise ValueError('Use the final public page URL; redirects are not followed')
            if 'text/' not in r.headers.get('content-type', ''):
                raise ValueError('Expected a text web page')
            chunks, size = [], 0
            async for chunk in r.aiter_bytes():
                size += len(chunk)
                if size > 1_000_000:
                    raise ValueError('Page exceeds research size limit')
                chunks.append(chunk)
    return {'url': url, 'text': plain(b''.join(chunks).decode('utf-8', errors='replace'))[:18000], 'retrieved_at': store.now()}


def validate_selection(ids, profile):
    allowed = {f['id'] for f in profile['facts'] if f.get('verified')}
    if not isinstance(ids, list) or any(not isinstance(x, str) or x not in allowed for x in ids):
        raise ValueError('Editor cited an unsupported resume fact')
    return list(dict.fromkeys(ids))


def context_signature(job, profile, settings):
    from . import career
    return digest({'tailor': TAILOR_VERSION, 'career': career.version() if not job.get('demo') else None,
                   'job': job.get('content_hash'), 'closed': job.get('closed', False),
                   'profile': profile.get('version'), 'confirmed': sorted(profile.get('confirmed_skills', [])),
                   'settings': {k: settings.get(k) for k in SIGNATURE_SETTINGS},
                   'company': job.get('company_evidence'), 'resolutions': job.get('resolutions')})


async def prepare(job, profile, settings, assessment, run_id, refresh=False):
    from .tailor import tailor
    provider = settings.get('provider', 'extractive') if not job['demo'] else 'extractive'
    manual = bool(job.get('manual'))  # you brought this job in: exclusions become flags on the card, not a block
    if not manual and any(c['status'] == 'excluded' and not (refresh and c['key'] == 'freshness') for c in assessment['checks']):
        raise ValueError('Excluded jobs cannot enter preparation')
    override = manual or (job.get('fit_override') and assessment['score'] is not None and assessment['score'] >= settings['review_min'])
    if not profile:
        raise ValueError('Upload your base resume first')
    if assessment['score'] is None and not manual or (assessment['score'] is not None and assessment['score'] < settings['match_min'] and not override):
        raise ValueError(f'Base-resume match must be at least {settings["match_min"]}% to tailor')
    jid = job['id']
    store.event(run_id, 'eligibility', 'working', f'Checking {job["company"]} · {job["title"]}', job_id=jid)
    await asyncio.sleep(0)
    store.event(run_id, 'eligibility', 'done', f'Eligibility: {assessment["status"]} · {assessment["sponsorship"]["label"]}',
                job_id=jid, checks=[{k: c[k] for k in ('key', 'status', 'label')} for c in assessment['checks']])
    store.event(run_id, 'analyst', 'working', 'Mapping JD requirements to verified resume evidence', job_id=jid)
    ats = assessment.get('ats') or {}
    store.event(run_id, 'analyst', 'done', f'Base match {assessment["score"]}% · ATS keywords {ats.get("score")}% · {len(assessment["missing"])} gaps',
                job_id=jid, matched=assessment['matched'], missing=assessment['missing'], confirmed_only=assessment.get('confirmed_only', []))
    # Ana hands the same analysis to Will and Rosa, who work at the same time; both pass to Remy.
    async def will():
        resume = await tailor(job, profile, assessment, provider, run_id, career={} if job['demo'] else None)
        store.event(run_id, 'writer', 'done',
                    f'Tailored: +{len(resume["added_to_skills"])} skills, {resume["rewritten_bullets"]} bullets changed, ATS {ats.get("score")}→{resume["ats"]["score"]}%. Passing to Remy.',
                    job_id=jid, added=resume['added_to_skills'], mode=resume['mode'])
        return resume

    async def rosa():
        plan = contact_plan(job, profile)
        contacts = job.get('contacts', [])
        if provider != 'extractive':
            from .career import load as load_career
            messages, rejected_drafts = await llm_drafts(provider, job, profile, assessment, None if job['demo'] else load_career(),
                                                         contacts[0] if contacts else None, run_id)
            if rejected_drafts:
                store.event(run_id, 'researcher', 'info', f'{len(rejected_drafts)} model draft(s) failed the checks; kept the template for those',
                            job_id=jid, rejected=rejected_drafts)
        else:
            messages = drafts(job, profile, analysis_view(profile, assessment), assessment['requirements'], contacts[0] if contacts else None)
        store.event(run_id, 'researcher', 'done', f'{len(plan)} people to look for · {len(messages)} drafts ready (not sent). Passing to Remy.',
                    job_id=jid, personas=[p['label'] for p in plan])
        return plan, contacts, messages

    store.event(run_id, 'writer', 'working', f"Tailoring your resume ({provider}) from Ana's analysis", job_id=jid)
    store.event(run_id, 'researcher', 'working', f"Planning who to contact at {job['company']} and what to say, from Ana's analysis", job_id=jid)
    resume, (plan, contacts, messages) = await asyncio.gather(will(), rosa())
    rejected = [v for v in resume['validation'] if v['status'] != 'accepted']
    store.event(run_id, 'reviewer', 'working', 'Checking the resume and outreach against your evidence', job_id=jid)
    review = {'unsupported_claims_blocked': len(rejected), 'gaps_not_claimed': resume['gaps'],
              'notes': ['Gaps were not added. Confirm a skill on the job page if you actually have it, then re-prepare.',
                        'Read the full job description before approving.']}
    package = {'id': digest({'job': jid, 'context': context_signature(job, profile, settings)})[:24],
               'job_id': jid, 'demo': job['demo'], 'created_at': store.now(), 'mode': resume['mode'],
               'profile_name': profile['name'], 'profile_version': profile['version'],
               'signature': context_signature(job, profile, settings), 'resume': resume,
               'scores': {'base': assessment['score'], 'potential': assessment.get('potential'),
                          'ats_base': ats.get('score'), 'ats_tailored': resume['ats']['score'],
                          'parse': parseability(profile)['score'] if profile.get('header') else None},
               'relevant_skills': assessment['matched'], 'missing_skills': resume['gaps'],
               'contact_plan': plan, 'contacts': contacts, 'drafts': messages, 'outreach_log': [],
               'review': review, 'run_id': run_id, 'status': 'prepared'}
    old = store.get('packages', package['id'])
    if old:  # same context: keep the user's edits and outreach history
        package['drafts'] = old.get('drafts', package['drafts'])
        package['outreach_log'] = old.get('outreach_log', [])
    store.put('packages', package['id'], package)
    applied = {b_id for b in store.all_records('batches') for b_id in b['package_ids']}
    for other in store.all_records('packages'):
        if other['job_id'] == jid and other['id'] != package['id'] and other['id'] not in applied:
            package.setdefault('replaced', []).append(other['id'])
            with store.connection() as c:
                c.execute("DELETE FROM records WHERE kind='packages' AND id=?", (other['id'],))
    store.event(run_id, 'reviewer', 'done', f'{len(rejected)} unsupported rewrites blocked; package ready', job_id=jid, package_id=package['id'])
    store.event(run_id, 'reviewer', 'waiting', f'Bringing {job["company"]} to your desk: match, resume, flags, apply link and who to contact', package_id=package['id'])
    return package
