import {ROLES, mountOffice, unmountOffice, applyEvent, selectAgent, setReduced, setPending, setNames} from './office.js';

// ------------------------------------------------------------------ helpers
const $ = s => document.querySelector(s);
const $$ = s => [...document.querySelectorAll(s)];
const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
const safeUrl = u => { try { const x = new URL(u); return ['https:', 'http:'].includes(x.protocol) ? x.href : '#'; } catch { return '#'; } };
const link = (url, label) => `<a href="${esc(safeUrl(url))}" target="_blank" rel="noopener noreferrer">${esc(label)} ↗</a>`;
const when = t => t ? new Date(t).toLocaleString(undefined, {month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit'}) : '—';
const day = t => t ? new Date(t).toLocaleDateString(undefined, {month: 'short', day: 'numeric'}) : '—';
const age = t => { if (!t) return 'Age unverified'; const h = Math.max(0, Math.floor((Date.now() - new Date(t)) / 3600000)); return h < 24 ? `${h}h ago` : `${Math.floor(h / 24)}d ago`; };
const pill = (value, cls = '') => `<span class="pill ${esc(cls)}">${esc(value)}</span>`;
const pct = v => v == null ? '—' : `${v}%`;
const check = (j, key) => j.assessment.checks.find(c => c.key === key) || {};
const isFresh = j => check(j, 'freshness').status === 'pass';
const scoreClass = v => v == null ? '' : v >= 70 ? 'hi' : v >= 60 ? 'mid' : v >= 50 ? 'low' : 'bad';
const bar = (v, label = '') => `<div class="scorebar ${scoreClass(v)}" title="${esc(label)}"><span style="width:${Math.max(0, Math.min(100, v || 0))}%"></span><b>${pct(v)}</b></div>`;

let data = null, mode = localStorage.getItem('cc-mode') || 'live', view = location.hash.slice(1) || 'office';
let selected = new Set(), events = [], lastSeq = 0, replayTimer = null, replaying = false, activeRun = null, toastTimer;
const seenRuns = new Set();
let jobFilter = {term: '', status: 'actionable', fresh: 'fresh'};
let pkgTab = 'resume', currentPackage = null, currentJob = null, insightCache = null;
if (!['live', 'demo'].includes(mode)) mode = 'live';
$('#mode').value = mode;

async function api(path, options = {}) {
  const opts = {...options};
  if (opts.body && !(opts.body instanceof FormData)) { opts.body = JSON.stringify(opts.body); opts.headers = {'Content-Type': 'application/json'}; }
  const r = await fetch('/api' + path, opts);
  if (!r.ok) {
    let e; try { e = await r.json(); } catch { e = {detail: r.statusText}; }
    throw new Error(typeof e.detail === 'string' ? e.detail : (e.detail || []).map(x => x.msg || JSON.stringify(x)).join('; '));
  }
  return r.json();
}
function toast(message, error = false) {
  clearTimeout(toastTimer);
  $('#toast').textContent = message; $('#toast').className = error ? 'error' : ''; $('#toast').style.display = 'block';
  toastTimer = setTimeout(() => $('#toast').style.display = 'none', 6500);
}
function heading(eyebrow, title, sub, actions = '') {
  return `<div class="page-heading"><div><div class="eyebrow">${eyebrow}</div><h1>${title}</h1><p class="subtitle">${sub}</p></div><div class="actions">${actions}</div></div>`;
}
function empty(title, text, action = '') { return `<div class="empty"><span class="symbol">▧</span><h2>${title}</h2><p>${text}</p>${action}</div>`; }
function providerLabel() {
  const p = data.settings.provider;
  return p === 'extractive' ? 'Extractive · no model' : ((data.models.labels || {})[p] || p);
}
function banner() {
  if (mode === 'demo') return `<div class="notice warning"><strong>SAMPLE WORKSPACE</strong> Fictional jobs and resume, extractive processing, no real applications or messages. <a href="#profile">Add your own resume →</a></div>`;
  if (!data.sources.length) return `<div class="notice"><strong>Start here.</strong> Load the starter registry of 250 verified company boards, then collect. <a href="#sources">Set up sources →</a></div>`;
  if (!data.profile) return `<div class="notice"><strong>Jobs can be collected now.</strong> Match scores and tailoring start once you confirm your base resume. <a href="#profile">Add resume →</a></div>`;
  return `<div class="notice"><strong>${esc(providerLabel())}</strong> Base-resume match drives ranking. Tailoring only adds keywords your evidence supports. You approve every batch.</div>`;
}

// ------------------------------------------------------------------ office
function stats() {
  const js = data.jobs, applied = js.filter(j => j.outcome.applied_at);
  const responded = applied.filter(j => ['replied', 'screening', 'interviewing', 'offer'].includes(j.outcome.stage));
  const ready = data.inbox.filter(i => i.kind === 'package_ready').length;
  return `<div class="stats stats-5">${[
    ['Fresh in 24h', js.filter(isFresh).length, `${js.length} on-target roles tracked`],
    ['Ready to apply', ready, 'Tailored, waiting for you'],
    ['Needs your call', data.inbox.length - ready, 'Agents waiting in your office'],
    ['Applied', applied.length, 'Recorded by you'],
    ['Response rate', applied.length ? Math.round(100 * responded.length / applied.length) + '%' : '—', `${responded.length} of ${applied.length}`],
  ].map(([label, value, note]) => `<div class="stat"><div class="stat-label">${label}</div><div class="stat-value">${typeof value === 'number' ? String(value).padStart(2, '0') : value}</div><div class="stat-note">${note}</div></div>`).join('')}</div>`;
}
function jobMini(j) {
  return `<div class="job-mini" data-action="job" data-id="${esc(j.id)}"><span class="company-icon">${esc(j.company[0])}</span><div><div class="title">${esc(j.title)}</div><div class="meta">${esc(j.company)} · ${esc(j.location)} · ${esc(age(j.published_at || j.created_at))}</div></div><div class="score">${pct(j.assessment.score)}<small>base match</small></div></div>`;
}
const pendingCounts = () => data.inbox.reduce((m, i) => (m[i.agent] = (m[i.agent] || 0) + 1, m), {});
let sideTab = 'inbox';
function office() {
  const sch = data.schedule, counts = pendingCounts();
  return heading('YOUR NEXT CHAPTER', 'Your search, in motion.', `${ROLES.scout.name} searches every ${sch.config.interval_minutes} minutes and ${ROLES.intake.name} reads any job you bring in. Roles that meet your rules are tailored automatically and brought to your office.`,
    `<button data-action="add-job">＋ Add a job</button><button data-action="collect">↻ Search now</button><a class="button primary" href="#review">Your review desk (${data.inbox.filter(i => i.kind === 'package_ready').length}) ↗</a>`) + banner() + stats() + `
  <div class="office-layout"><section class="panel"><div class="panel-head"><div><h2>The working floor</h2><p class="subtitle">Click anyone to talk. Agents with a number above their head are waiting in your office.</p></div><span id="scene-state" class="status-pill">● ${mode === 'demo' ? 'SAMPLE' : sch.config.enabled ? `SCOUT · NEXT ${new Date(sch.next.collect).toLocaleTimeString([], {hour: 'numeric', minute: '2-digit'})}` : 'SCHEDULE OFF'}</span></div>
  <div class="scene-wrap"><canvas id="office-canvas" role="img" aria-label="Pixel office. Agents walk to your review room when they need a decision. Use the buttons below to talk to each agent."></canvas><div class="scene-caption"><strong id="scene-caption">${data.inbox.length ? `${data.inbox.length} thing${data.inbox.length === 1 ? '' : 's'} waiting for you in your office.` : 'All clear. The team is working.'}</strong><span>CLICK A WORKER TO TALK</span></div></div>
  <div class="office-controls"><button class="small-button" data-action="replay">▷ Replay latest run</button><button class="small-button" data-action="stop-replay">Stop replay</button><label><input id="reduced-motion" type="checkbox" ${matchMedia('(prefers-reduced-motion: reduce)').matches ? 'checked' : ''}> Reduce motion</label></div>
  <div class="agent-buttons">${Object.entries(ROLES).filter(([id]) => id !== 'lead').map(([id, r]) => `<button data-action="agent" data-id="${id}" style="--agent-color:${r.color}"><span></span>${esc(r.name)} <small class="role">${esc(r.role)}</small>${counts[id] ? ` <b class="badge">${counts[id]}</b>` : ''}</button>`).join('')}</div></section>
  <section class="panel side-panel"><div class="seg"><button class="${sideTab === 'inbox' ? 'active' : ''}" data-action="side-tab" data-tab="inbox">Waiting for you <b class="badge">${data.inbox.length}</b></button><button class="${sideTab === 'activity' ? 'active' : ''}" data-action="side-tab" data-tab="activity">Activity</button></div>
  <div id="side-body">${sideTab === 'inbox' ? inboxList() : '<div id="activity-list" class="activity-list"></div>'}</div></section></div>
  <div class="bottom-grid"><section class="panel"><div class="panel-head"><h2>The pipeline</h2><span class="pill">NOTHING GETS STUCK</span></div>${pipelineStrip()}</section>
  <section class="panel"><div class="panel-head"><h2>The search brief</h2><span class="pill">YOUR RULES</span></div><div class="focus-card"><div class="number">${data.settings.match_min}<span style="font-size:19px">%</span></div><p>Base-resume match to tailor automatically. ${data.settings.review_min}–${data.settings.match_min - 1}%: the Analyst asks you. Below ${data.settings.review_min}%: skipped.</p><ul><li>US · 0–${data.settings.max_experience} years · full-time & contract</li><li>$${(data.settings.annual_min / 1000).toFixed(0)}k+ salary · $${data.settings.hourly_min}/hr contract floor</li><li>Explicit sponsorship restrictions are excluded</li><li>${sch.config.enabled ? `Scout every ${sch.config.interval_minutes} min · next ${when(sch.next.collect)}` : 'Schedule off · search manually'}</li></ul></div></section></div>`;
}
function pipelineStrip() {
  const js = data.jobs, ready = data.inbox.filter(i => i.kind === 'package_ready').length;
  const mine = js.filter(j => j.manual).length;
  const stages = [
    [ROLES.scout.name + ' · Scout', 'scout', `${js.length} on-target roles`, `${data.excluded_count || 0} filtered out`],
    [ROLES.intake.name + ' · Intake', 'intake', `${mine} job${mine === 1 ? '' : 's'} you brought in`, 'paste a link or a JD'],
    [ROLES.eligibility.name + ' · Gatekeeper', 'eligibility', `${js.filter(j => j.assessment.status !== 'excluded').length} pass hard rules`, `${js.filter(isFresh).length} fresh in 24h`],
    [ROLES.analyst.name + ' · Analyst', 'analyst', data.profile ? `${js.filter(j => (j.assessment.score || 0) >= data.settings.match_min).length} ≥ ${data.settings.match_min}% match` : 'Needs your resume', `${data.inbox.filter(i => i.kind === 'fit_review').length} in review band`],
    [ROLES.writer.name + ' · Writer', 'writer', `${data.packages.length} tailored`, 'resume + 5 drafts'],
    [ROLES.researcher.name + ' · Researcher', 'researcher', 'who to contact', 'what to say'],
    [ROLES.reviewer.name + ' · Reviewer', 'reviewer', `${data.inbox.length} brought to you`, 'one courier to your desk'],
    ['You', 'lead', `${ready} ready to apply`, `${js.filter(j => j.outcome.applied_at).length} applied`],
  ];
  return `<div class="pipeline">${stages.map(([name, id, a, b]) => `<button class="stage" data-action="${id === 'lead' ? 'goto-review' : 'agent'}" data-id="${id}" style="--agent-color:${ROLES[id].color}"><span class="dot"></span><b>${name}</b><span>${esc(a)}</span><small>${esc(b)}</small></button>`).join('<i>→</i>')}</div>`;
}
function inboxList() {
  if (!data.inbox.length) return `<div class="empty-activity">Nothing needs you right now.<br><br>${esc(ROLES.scout.name)} searches every ${data.schedule.config.interval_minutes} min; anything that meets your rules goes ${esc(ROLES.eligibility.name)} → ${esc(ROLES.analyst.name)} → ${esc(ROLES.writer.name)} + ${esc(ROLES.researcher.name)} (side by side) → ${esc(ROLES.reviewer.name)}, who brings it here.</div>`;
  return `<div class="inbox-list">${data.inbox.map(i => `<button class="inbox-row" data-action="inbox-open" data-id="${esc(i.id)}" style="--agent-color:${ROLES[i.from_agent || i.agent]?.color}"><span class="dot"></span><div><b>${esc(i.kind === 'package_ready' ? `${i.company} · ${i.title}` : i.title + (i.company ? ` · ${i.company}` : ''))}</b><small>${esc(inboxSummary(i))}</small></div>${i.match != null ? `<span class="score-chip ${scoreClass(i.match)}">${i.match}%</span>` : ''}</button>`).join('')}</div>`;
}
function inboxSummary(i) {
  if (i.kind === 'package_ready') return `ready to apply · ${(i.contact_plan || []).length} people to reach · ${age(i.posted_at)}${i.flags.some(f => f.ack) ? ' · has flags' : ''}`;
  if (i.kind === 'fit_review') return `from ${ROLES.analyst.name}: below threshold, tailor anyway?`;
  if (i.kind === 'follow_up') return `from ${ROLES.writer.name}: follow-up due`;
  if (i.kind === 'sources_failing') return `from ${ROLES.scout.name}: boards failing`;
  if (i.kind === 'stale_packages') return `from ${ROLES.writer.name}: needs re-tailoring`;
  return i.detail || '';
}
const RELAY = ['eligibility', 'analyst', 'writer', 'researcher', 'reviewer'];
const DRAFT_LABELS = {connection: 'LinkedIn note', recruiter: 'Recruiter email', manager: 'Hiring manager', referral: 'Referral ask', followup: 'Follow-up'};
const cardDraft = {};
function relayLine(i) {
  const start = i.manual ? 'intake' : 'scout';
  const tag = id => `<span style="--agent-color:${ROLES[id].color}"><i class="dot"></i>${esc(ROLES[id].name)}</span>`;
  return [tag(start), tag('eligibility'), tag('analyst'), `<span class="pair">${tag('writer')}<em>+</em>${tag('researcher')}</span>`, tag('reviewer')].join('<b>→</b>') + '<b>→</b><span>You</span>';
}
function outreachPanel(i) {
  const key = cardDraft[i.package_id] || 'connection', text = (i.drafts || {})[key] || '';
  const contacts = i.contacts || [], log = i.outreach_log || [];
  return `<aside class="reach">
    <div class="reach-head"><span class="dot" style="background:${ROLES.researcher.color}"></span><b>${esc(ROLES.researcher.name)}'s outreach plan</b><span class="note">reach out while you apply</span></div>
    <h4>Who to contact</h4>
    ${(i.contact_plan || []).map(c => `<div class="persona"><div><b>${esc(c.label)}</b><small>${esc(c.why)}</small></div><div class="actions">${link(c.linkedin, 'LinkedIn')}${link(c.google, 'Google')}</div></div>`).join('')}
    ${contacts.length ? `<div class="saved-contacts">${contacts.map(c => `<p>👤 ${link(c.url, c.name)} · ${esc(c.role)}</p>`).join('')}</div>` : ''}
    <button class="small-link" data-action="contact" data-id="${esc(i.job_id)}">+ Save a person you found</button>${contacts.length ? ` · <button class="small-link" data-action="personalize" data-id="${esc(i.package_id)}" data-inline="1">Personalise drafts for ${esc(contacts[0].name.split(' ')[0])}</button>` : ''}
    <h4>What to say</h4>
    <div class="draft-tabs">${Object.entries(DRAFT_LABELS).map(([k, l]) => `<button class="${k === key ? 'active' : ''}" data-action="card-draft" data-id="${esc(i.package_id)}" data-key="${k}">${l}</button>`).join('')}</div>
    <textarea class="card-draft-text" data-pkg="${esc(i.package_id)}" data-key="${key}" rows="${key === 'connection' || key === 'followup' ? 4 : 8}">${esc(text)}</textarea>
    <div class="actions"><button class="small-button" data-action="card-copy" data-id="${esc(i.package_id)}">Copy</button><button class="small-button" data-action="card-save-draft" data-id="${esc(i.package_id)}">Save edit</button><span class="note">${text.length} chars${key === 'connection' && text.length > 300 ? ' · over LinkedIn’s 300' : ''}</span></div>
    <form class="inline-form card-log" data-id="${esc(i.package_id)}"><input name="contact" type="text" placeholder="Who did you message?" required><select name="channel"><option value="linkedin">LinkedIn</option><option value="email">Email</option><option value="referral">Referral</option></select><button class="small-button">Log as sent</button></form>
    ${log.length ? `<div class="note">${log.map(o => `✉ ${esc(o.contact)} · ${esc(o.channel)} · ${esc(o.status)} · ${day(o.at)}`).join('<br>')}</div>` : ''}
  </aside>`;
}
function inboxCard(i, full = true) {
  const from = ROLES[i.from_agent || i.agent], courier = ROLES[i.agent];
  if (i.kind === 'package_ready') {
    const ack = i.flags.filter(f => f.ack), info = i.flags.filter(f => !f.ack);
    const pay = i.pay ? `$${Math.round(i.pay.min / (i.pay.unit === 'year' ? 1000 : 1))}${i.pay.unit === 'year' ? 'k' : ''}–${Math.round(i.pay.max / (i.pay.unit === 'year' ? 1000 : 1))}${i.pay.unit === 'year' ? 'k' : '/hr'}` : 'Pay undisclosed';
    return `<article class="apply-card wide" data-pkg="${esc(i.package_id)}" style="--agent-color:${courier.color}">
      <div class="apply-head"><span class="relay">${relayLine(i)}</span><span class="note">${esc(courier.name)} brought this${i.manual ? ' · you added it' : ''}</span></div>
      <div class="card-cols"><div class="apply-main">
      <h2>${esc(i.title)}</h2><div class="meta-line">${esc(i.company)} · ${esc(i.location || 'Location unstated')} · ${esc(i.employment_type || 'type unstated')} · ${esc(pay)}</div>
      <div class="apply-grid">
        <div><small>JD match (base resume)</small>${bar(i.match)}</div>
        <div><small>ATS keywords · before → after</small><div class="ats-pair">${bar(i.ats_before)}<span>→</span>${bar(i.ats_after)}</div></div>
        ${i.quality != null ? `<div><small>Tailoring quality</small>${bar(i.quality)}<span class="note">${i.coverage[0]}/${i.coverage[1]} must-haves covered</span></div>` : ''}
        <div><small>Posted</small><b>${esc(i.posted_at ? when(i.posted_at) : 'Date not published')}</b><span class="note"> · ${esc(age(i.posted_at))}${i.posted_label ? ' · ' + esc(i.posted_label) : ''}</span></div>
      </div>
      ${i.added.length ? `<div class="tags"><span class="note">Added to your resume:</span>${i.added.map(s => `<span class="tag added">+ ${esc(s)}</span>`).join('')}</div>` : ''}
      ${i.gaps.length ? `<div class="tags"><span class="note">Not claimed (gaps):</span>${i.gaps.map(s => `<span class="tag missing">${esc(s)}</span>`).join('')}</div>` : ''}
      <div class="flags">${ack.length ? `<b class="small-h">Flags to check before applying</b>${ack.map(f => `<label class="flag ${f.severity === 'exclusion' ? 'excl' : ''}"><input type="checkbox" class="flag-ack" value="${esc(f.key)}"> <span><b>${esc(f.label)}</b>${f.evidence ? `<small>${esc(f.evidence)}</small>` : ''}</span></label>`).join('')}` : '<div class="flag ok">✓ No flags. Every check passed.</div>'}
      ${info.map(f => `<div class="flag ${f.good ? 'ok' : 'info'}">${f.good ? '✓' : 'ⓘ'} ${esc(f.label)}${f.evidence ? `<small>${esc(f.evidence)}</small>` : ''}</div>`).join('')}</div>
      <ol class="apply-steps">
        <li><a class="button primary small-button" href="/api/packages/${esc(i.package_id)}/export?format=docx">1 · Download tailored resume</a> <a class="small-link" href="/api/packages/${esc(i.package_id)}/export?format=pdf">PDF</a> <button class="small-link" data-action="package" data-id="${esc(i.package_id)}">preview / edit</button> <a class="small-link" href="/api/jobs/${encodeURIComponent(i.job_id)}/jd">Download JD</a></li>
        <li><a class="button small-button" href="${esc(safeUrl(i.apply_url))}" target="_blank" rel="noopener noreferrer">2 · Open application ↗</a> <span class="note">upload the resume and apply on their site</span></li>
        <li><button class="small-button primary-outline" data-action="mark-applied" data-id="${esc(i.package_id)}">3 · I've applied ✓</button> <button class="small-link" data-action="skip-job" data-id="${esc(i.job_id)}">Skip this role</button></li>
      </ol></div>
      ${outreachPanel(i)}</div></article>`;
  }
  const actions = {
    open_resume: `<a class="button primary small-button" href="#profile" data-action="close">Upload resume</a>`,
    tailor_anyway: `<button class="primary small-button" data-action="tailor-anyway" data-id="${esc(i.job_id)}">Tailor anyway</button>`,
    skip: `<button class="small-button" data-action="skip-job" data-id="${esc(i.job_id)}">Skip</button>`,
    pause_failing: `<button class="small-button" data-action="pause-failing">Pause failing boards</button><a class="button small-button" href="#sources" data-action="close">Open sources</a>`,
    open_package: i.package_id ? `<button class="small-button" data-action="package" data-id="${esc(i.package_id)}">Open follow-up draft</button>` : '',
    retailor: `<button class="primary small-button" data-action="retailor">Re-tailor now</button>`,
    followed_up: `<button class="primary small-button" data-action="followed-up" data-id="${esc(i.job_id)}">Done, I followed up</button>`,
  };
  return `<article class="apply-card" style="--agent-color:${from.color}"><div class="apply-head"><span class="who"><span class="dot"></span>${esc(courier.name)} brought this from ${esc(from.name)}</span></div>
    <h2>${esc(i.title)}${i.company ? ` · ${esc(i.company)}` : ''}</h2>${i.match != null ? bar(i.match) : ''}<p class="section-gap">${esc(i.detail || '')}</p>
    ${i.url ? `<p>${link(i.url, 'Original posting')}</p>` : ''}<div class="actions">${(i.actions || []).map(a => actions[a] || '').join('')}</div></article>`;
}

// ------------------------------------------------------------------ opportunities
function jobs() {
  return heading('THE OPPORTUNITY BOARD', 'Find your kind of work.', 'Ranked by evidence in your base resume. Titles find jobs; they don’t set priority.', `<button data-action="add-job">＋ Add a job</button><button data-action="collect">↻ Search now</button>`) + banner() +
  `<div class="filterbar"><input id="job-search" type="text" placeholder="Search company, title, or location" aria-label="Search opportunities" value="${esc(jobFilter.term)}">
   <select id="job-fresh" aria-label="Freshness"><option value="fresh">Posted in last 24h</option><option value="recent">Last 48h / unverified age</option><option value="all">Any age</option></select>
   <select id="job-filter" aria-label="Eligibility filter"><option value="actionable">Eligible + needs review</option><option value="qualified">Eligible only</option><option value="review">Needs review</option><option value="excluded">Excluded</option><option value="all">All roles</option></select></div>
   ${data.excluded_count ? `<p class="note">${data.excluded_count} excluded roles hidden: ${data.excluded_reasons.map(([r, n]) => `${esc(r)} (${n})`).join(' · ')}. Choose “Excluded” to inspect them.</p>` : ''}
   <div id="selectionbar"></div><section class="panel"><div id="job-table"></div></section>`;
}
function jobTable() {
  if (view !== 'jobs') return;
  $('#job-fresh').value = jobFilter.fresh; $('#job-filter').value = jobFilter.status;
  const term = jobFilter.term.toLowerCase();
  const rows = data.jobs.filter(j => {
    const s = j.assessment.status;
    if (jobFilter.status === 'actionable' ? s === 'excluded' : jobFilter.status !== 'all' && s !== jobFilter.status) return false;
    if (jobFilter.fresh === 'fresh' && !isFresh(j)) return false;
    if (jobFilter.fresh === 'recent' && check(j, 'freshness').status === 'excluded') return false;
    return `${j.title} ${j.company} ${j.location}`.toLowerCase().includes(term);
  });
  const shown = rows.slice(0, 300);
  $('#job-table').innerHTML = rows.length ? `<div class="table-wrap"><table class="table"><thead><tr><th><input type="checkbox" id="select-visible" aria-label="Select visible roles that can be prepared"></th><th>ROLE & COMPANY</th><th>BASE MATCH</th><th>ATS KEYWORDS</th><th>ELIGIBILITY</th><th>SPONSORSHIP</th><th>PAY</th><th>POSTED</th></tr></thead><tbody>${shown.map(j => {
    const a = j.assessment, blocker = a.checks.find(c => c.status === 'excluded') || a.checks.find(c => c.status === 'review');
    return `<tr><td><input type="checkbox" class="job-check" value="${esc(j.id)}" aria-label="Select ${esc(j.title)} at ${esc(j.company)}" ${selected.has(j.id) ? 'checked' : ''} ${canPrepare(j) ? '' : 'disabled'}></td>
    <td><button class="title-button" data-action="job" data-id="${esc(j.id)}">${esc(j.title)}</button><small>${esc(j.company)} · ${esc(j.location || 'Location unstated')} · ${esc(j.employment_type || 'type?')}</small></td>
    <td>${bar(a.score)}${a.potential > a.score ? `<small>${a.potential}% with confirmed skills</small>` : ''}</td>
    <td>${bar(a.ats?.score)}</td>
    <td>${pill(a.status === 'qualified' ? 'Eligible' : a.status === 'review' ? 'Needs review' : 'Excluded', a.status)}${blocker && a.status !== 'qualified' ? `<small>${esc(blocker.label)}</small>` : ''}</td>
    <td>${pill(a.sponsorship.label, a.sponsorship.status)}</td>
    <td>${a.pay ? `$${Math.round(a.pay.min / (a.pay.unit === 'year' ? 1000 : 1))}${a.pay.unit === 'year' ? 'k' : ''}–${Math.round(a.pay.max / (a.pay.unit === 'year' ? 1000 : 1))}${a.pay.unit === 'year' ? 'k' : '/hr'}` : '<span class="note">Undisclosed</span>'}</td>
    <td>${esc(age(j.published_at || j.created_at))}<small>${esc(j.posted_label || (j.publication_kind === 'last_published' ? 'latest publication' : j.publication_kind === 'unverified' ? 'created date' : 'first published'))}</small></td></tr>`;
  }).join('')}</tbody></table></div>${rows.length > shown.length ? `<p class="note form-panel">Showing 300 of ${rows.length}. Narrow the search to see more.</p>` : ''}` :
    empty('No roles in this view', jobFilter.fresh === 'fresh' ? `Nothing confirmed posted in the last 24h yet${data.jobs.some(j => check(j, 'freshness').status === 'review') ? ', but some roles are 24–48h old or have an unverified age. Try “Last 48h”' : ''}. New roles appear through the day.` : 'Add company boards or change the filters.');
  selectionBar();
}
function canPrepare(j) { return !!data.profile && j.assessment.status !== 'excluded' && j.assessment.score >= data.settings.match_min; }
function selectionBar() {
  if (!$('#selectionbar')) return;
  $('#selectionbar').innerHTML = selected.size ? `<div class="selectionbar"><span>${selected.size} role${selected.size === 1 ? '' : 's'} selected</span><button class="primary small-button" data-action="prepare-selected">Tailor & prepare →</button></div>` : '';
}

async function showJob(id) {
  const j = await api('/jobs/' + encodeURIComponent(id));
  currentJob = j;
  const a = j.assessment, reqs = Object.entries(a.requirements || {}).sort((x, y) => y[1].weight - x[1].weight);
  const evidence = s => (a.evidence[s] || []);
  const status = s => { const e = evidence(s); return e.some(x => x !== 'user-confirmed') ? 'resume' : e.length ? 'confirmed' : 'gap'; };
  const pkg = data.packages.find(p => p.job_id === id);
  openDialog(dialogHead(j.title, j.company) + `<div class="meta-line">${esc(j.location)} · ${esc(j.employment_type || 'Type unverified')} · ${esc(age(j.published_at || j.created_at))} · ${j.demo ? 'fictional sample' : link(j.url, 'Original posting')}</div>
  <div class="score-row"><div><small>Base-resume match</small>${bar(a.score)}</div><div><small>With confirmed skills</small>${bar(a.potential)}</div><div><small>ATS keywords (base resume)</small>${bar(a.ats?.score)}</div></div>
  <div class="detail-grid"><section><h3>Eligibility checklist</h3>${a.checks.map(c => `<div class="check-row ${c.status}"><span class="mark">${c.status === 'pass' ? '✓' : c.status === 'excluded' ? '×' : '?'}</span><div>${esc(c.label)}${c.evidence ? `<small>${esc(c.evidence)}</small>` : ''}${c.resolution ? `<small>Reviewed: ${esc(c.resolution.note)}</small>` : ''}</div>${c.status === 'review' && ['country', 'experience', 'employment', 'pay', 'freshness', 'sponsorship'].includes(c.key) ? `<button class="small-button" data-action="resolve" data-id="${esc(j.id)}" data-key="${c.key}">Review</button>` : ''}${c.status === 'review' && c.key === 'company' ? `<button class="small-button" data-action="company" data-id="${esc(j.id)}">Check</button>` : ''}</div>`).join('')}
  <div class="actions section-gap"><button class="primary" data-action="prepare-one" data-id="${esc(j.id)}" ${canPrepare({...j, assessment: a}) ? '' : 'disabled'}>${pkg ? 'Re-tailor package' : 'Tailor & prepare'}</button>${pkg ? `<button data-action="package" data-id="${esc(pkg.id)}">Open package</button>` : ''}<button data-action="company" data-id="${esc(j.id)}">Company evidence</button><a class="button" href="https://h1bdata.info/index.php?em=${encodeURIComponent(j.company)}" target="_blank" rel="noopener noreferrer">H-1B history ↗</a></div>
  <p class="note section-gap">H-1B history shows past filings only. It never overrides what this posting says.</p></section>
  <section><h3>Requirements vs. your evidence</h3><p class="note">Required ×2 · mentioned ×1.5 · preferred ×1. Confirming a skill lets tailoring add it to your Skills section; it doesn’t change the base score.</p>
  <table class="req-table">${reqs.map(([s, r]) => { const st = status(s); return `<tr class="${st}"><td><b>${esc(s)}</b><small>${esc(r.kind)}${r.count > 1 ? ` · ${r.count}×` : ''}</small></td><td>${st === 'resume' ? '✓ on resume' : st === 'confirmed' ? '◐ you confirmed' : '✗ gap'}</td><td>${st === 'gap' && data.profile ? `<button class="small-button" data-action="confirm-skill" data-skill="${esc(s)}" data-id="${esc(j.id)}">I have this</button>` : st === 'confirmed' ? `<button class="small-button" data-action="unconfirm-skill" data-skill="${esc(s)}" data-id="${esc(j.id)}">Undo</button>` : ''}</td></tr>`; }).join('') || '<tr><td>No recognised skills in this description.</td></tr>'}</table>
  <details><summary>Job description</summary><div class="jd">${esc(j.description)}</div></details>
  <h3 class="section-gap">Contacts</h3>${(j.contacts || []).map(c => `<p class="note">${link(c.url, c.name)} · ${esc(c.role)}<br>${esc(c.relevance)}</p>`).join('') || '<p class="note">No verified contacts yet. Package → Contacts has search links for each type of person.</p>'}<button class="small-button" data-action="contact" data-id="${esc(j.id)}">Add a sourced contact</button></section></div>`);
}

// ------------------------------------------------------------------ Ivy: bring in a job yourself
let addJobTab = 'link', intakeWatch = null;
function addJobDialog(tab) {
  if (tab) addJobTab = tab;
  const ivy = ROLES.intake.name, grace = ROLES.eligibility.name;
  openDialog(dialogHead('Bring in a job', `${ivy.toUpperCase()} · INTAKE`) + `
  <p class="note">${esc(ivy)} reads the posting, ${esc(grace)} checks it against your rules, and the team tailors your Word resume. You get the match %, flags and a downloadable resume on your review desk. Jobs you bring in skip the 24-hour rule, and anything outside your rules shows as a flag instead of being dropped.</p>
  <div class="view-toggle section-gap"><button class="${addJobTab === 'link' ? 'active' : ''}" data-action="addjob-tab" data-tab="link">Paste a link</button><button class="${addJobTab === 'text' ? 'active' : ''}" data-action="addjob-tab" data-tab="text">Paste the description</button></div>
  <form id="intake-form">${addJobTab === 'link' ? `
    <label class="field"><span>Job link</span><input name="url" type="url" required placeholder="https://jobs.lever.co/company/… or the job page on the company's site"></label>
    <p class="note">Best: links to the single job on the company's site, or on Greenhouse, Lever, Ashby, Workday or SmartRecruiters. LinkedIn, Indeed and Glassdoor need a login, so use the company's “Apply” link or paste the description instead.</p>` : `
    <label class="field"><span>Job description</span><textarea name="text" required minlength="200" style="min-height:240px" placeholder="Paste the whole posting: title, responsibilities, requirements, pay…"></textarea></label>
    <div class="two"><label class="field"><span>Job title (optional)</span><input name="title" type="text" placeholder="Detected from the text if blank"></label><label class="field"><span>Company (optional)</span><input name="company" type="text"></label></div>
    <div class="two"><label class="field"><span>Location (optional)</span><input name="location" type="text" placeholder="e.g. Dallas, TX or Remote (US)"></label><label class="field"><span>Application link (optional)</span><input name="apply_url" type="url" placeholder="https://…"></label></div>`}
    <button class="primary">Send to ${esc(ivy)} →</button></form>
  <div id="intake-status" class="section-gap"></div>`);
  $('#intake-form input, #intake-form textarea')?.focus();
}
async function startIntake(values) {
  const body = {mode, ...Object.fromEntries(Object.entries(values).filter(([, v]) => v && String(v).trim()))};
  const r = await api('/intake', {method: 'POST', body});
  activeRun = r.run_id;
  watchIntake(r.run_id);
}
function watchIntake(runId) {
  clearInterval(intakeWatch);
  const step = async () => {
    const box = $('#intake-status');
    let run, evs;
    try { [run, evs] = await Promise.all([api('/runs/' + runId), api('/events?run_id=' + runId)]); } catch { return; }
    if (box) box.innerHTML = `<div class="intake-log">${evs.map(e => `<div class="activity ${e.type}"><strong>${esc(ROLES[e.actor]?.name || e.actor)}</strong> · ${esc(e.message)}</div>`).join('') || '<div class="activity"><strong>' + esc(ROLES.intake.name) + '</strong> · starting…</div>'}</div>`;
    if (run.status === 'running') return;
    clearInterval(intakeWatch);
    await refresh(view !== 'office');
    if (run.status === 'failed') {
      if (box) box.insertAdjacentHTML('beforeend', `<div class="flag section-gap"><span><b>${esc(run.error || 'Could not read that posting')}</b><br><button class="small-button section-gap" data-action="addjob-tab" data-tab="text">Paste the description instead</button></span></div>`);
      return;
    }
    const item = data.inbox.find(i => i.job_id === run.job_id && i.kind === 'package_ready');
    if (item) { openDialog(dialogHead('Ready: ' + item.title, `${ROLES.intake.name.toUpperCase()} BROUGHT THIS IN`) + inboxCard(item)); toast('Your tailored resume is ready.'); }
    else if (box) box.insertAdjacentHTML('beforeend', `<div class="flag section-gap"><span>${esc(run.error || 'Added to Opportunities.')} <a href="#jobs">Open Opportunities</a></span></div>`);
  };
  intakeWatch = setInterval(step, 900);
  step();
}

// ------------------------------------------------------------------ review desk
function review() {
  const ready = data.inbox.filter(i => i.kind === 'package_ready'), other = data.inbox.filter(i => i.kind !== 'package_ready');
  const applied = data.jobs.filter(j => j.outcome.applied_at).sort((a, b) => (b.outcome.applied_at || '').localeCompare(a.outcome.applied_at || ''));
  return heading('HUMAN DIRECTION', 'Your review desk.', 'Each card has the JD match, the tailored resume, any flags, the posting date and the application link. Download, apply on their site, then tick “I’ve applied”.') + banner() +
  (ready.length ? `<div class="apply-list">${ready.map(i => inboxCard(i)).join('')}</div>` :
    `<section class="panel">${empty('Nothing ready to apply yet', data.profile || mode === 'demo' ? `${esc(ROLES.scout.name)} searches every ${data.schedule.config.interval_minutes} minutes. Roles that pass your rules at ≥${data.settings.match_min}% are tailored automatically and appear here.` : 'Upload your base resume so roles can be scored and tailored.', `<a class="button primary" href="#${data.profile || mode === 'demo' ? 'jobs' : 'profile'}">${data.profile || mode === 'demo' ? 'Browse opportunities' : 'Add resume'}</a>`)}</section>`) +
  (other.length ? `<h2 class="section-gap">Other decisions</h2><div class="apply-cards section-gap">${other.map(i => inboxCard(i)).join('')}</div>` : '') +
  (applied.length ? `<section class="panel section-gap"><div class="panel-head"><h2>Applied</h2><a class="inline-link" href="#tracker">Tracker ↗</a></div>${applied.slice(0, 12).map(j => `<div class="job-mini" data-action="job" data-id="${esc(j.id)}"><span class="company-icon">✓</span><div><div class="title">${esc(j.title)}</div><div class="meta">${esc(j.company)} · applied ${day(j.outcome.applied_at)} · ${esc(j.outcome.stage)}${j.outcome.follow_up ? ' · follow up ' + esc(j.outcome.follow_up) : ''}</div></div></div>`).join('')}</section>` : '');
}
function inboxItemDialog(id) {
  const i = data.inbox.find(x => x.id === id); if (!i) { toast('That item was already handled.'); return; }
  openDialog(dialogHead(i.kind === 'package_ready' ? 'Ready to apply' : 'Needs your call', ROLES[i.agent].name.toUpperCase()) + inboxCard(i));
}

// ------------------------------------------------------------------ talk to an agent
let chatAgent = null, pendingUpload = null;
async function showAgent(id) {
  if (id === 'lead') { location.hash = 'review'; return; }
  selectAgent(id); chatAgent = id;
  const info = await api(`/agents/${id}/chat?mode=${mode}`);
  const mine = data.inbox.filter(i => i.agent === id), log = events.filter(e => e.actor === id).slice(-12).reverse();
  openDialog(dialogHead(info.name, `${(info.title || ROLES[id].role).toUpperCase()} · ${ROLES[id].title.toUpperCase()}`) + `<div class="agent-dialog">
    <div class="agent-left">
      ${mine.length ? `<h3>Waiting for you (${mine.length})</h3>${mine.map(i => inboxCard(i, false)).join('')}` : `<p class="note">${esc(info.role)}</p><div class="flag ok">Nothing from me needs you right now.</div>`}
      <details class="section-gap"><summary>Recent work (${log.length})</summary>${log.map(e => `<div class="activity ${e.type}"><strong>${esc(e.message)}</strong><small>${esc(e.type)} · ${when(e.at)}</small></div>`).join('') || '<p class="note">No activity yet.</p>'}</details>
    </div>
    <div class="agent-chat"><div class="chat-log" id="chat-log">${chatBubbles(info.messages, info)}</div>
      <div class="chips">${info.examples.map(x => { const t = x.replace(/"/g, '').replace(/<company>/g, data.inbox.find(i => i.company)?.company || data.jobs[0]?.company || 'Acme'); return `<button class="chip" data-action="chat-example" data-text="${esc(t)}">${esc(t)}</button>`; }).join('')}</div>
      <form id="chat-form" class="chat-form"><input name="message" type="text" autocomplete="off" placeholder="Talk to ${esc(info.name)}…" required><button class="primary small-button">Send</button></form>
      <p class="note">${data.settings.provider !== 'extractive' && data.models[data.settings.provider] ? `Replies by ${esc(providerLabel())}.` : 'Answers from live data. Add a Claude key for free-form conversation.'} Agents can search, tailor or skip; they never apply or send messages.</p></div></div>`);
  const logEl = $('#chat-log'); if (logEl) logEl.scrollTop = logEl.scrollHeight;
  $('#chat-form input')?.focus();
}
function chatBubbles(messages, info) {
  if (!messages.length) return `<div class="bubble agent">Hi, I'm ${esc(info.name)}. ${esc(info.role)} Ask me anything about my work.</div>`;
  return messages.map(m => `<div class="bubble ${m.role === 'user' ? 'me' : 'agent'}">${esc(m.text).replace(/\n/g, '<br>')}${m.result ? `<small>→ ${esc(m.result)}</small>` : ''}</div>`).join('');
}
async function sendChat(text) {
  const logEl = $('#chat-log');
  logEl.insertAdjacentHTML('beforeend', `<div class="bubble me">${esc(text)}</div><div class="bubble agent typing">…</div>`); logEl.scrollTop = logEl.scrollHeight;
  const r = await api(`/agents/${chatAgent}/chat`, {method: 'POST', body: {message: text, mode}});
  const info = await api(`/agents/${chatAgent}/chat?mode=${mode}`);
  logEl.innerHTML = chatBubbles(r.messages, info); logEl.scrollTop = logEl.scrollHeight;
  if (r.action) {
    if (r.result === 'tailoring' || (r.action.type === 'run_search' && r.result && !['busy', 'skipped'].includes(r.result))) activeRun = 'pending';
    await refresh(false); syncOffice();
  }
}
function syncOffice() {
  if (view !== 'office' || !$('#office-canvas')) return;
  setPending(pendingCounts());
  const cap = $('#scene-caption');
  if (cap && !replaying) cap.textContent = data.inbox.length ? `${data.inbox.length} thing${data.inbox.length === 1 ? '' : 's'} waiting for you in your office.` : 'All clear. The team is working.';
  if (sideTab === 'inbox' && $('#side-body')) $('#side-body').innerHTML = inboxList();
  const tabBadge = $('.seg [data-tab="inbox"] .badge'); if (tabBadge) tabBadge.textContent = data.inbox.length;
  $$('.agent-buttons button[data-id]').forEach(b => { const n = pendingCounts()[b.dataset.id]; const badge = b.querySelector('.badge'); if (n && !badge) b.insertAdjacentHTML('beforeend', ` <b class="badge">${n}</b>`); else if (badge) n ? badge.textContent = n : badge.remove(); });
}

function showPackage(id, tab) {
  const p = data.packages.find(p => p.id === id); if (!p) return;
  currentPackage = p; if (tab) pkgTab = tab;
  const j = data.jobs.find(j => j.id === p.job_id) || {company: '', title: ''};
  const approved = data.batches.some(b => b.package_ids.includes(p.id));
  const tabs = [['resume', 'Tailored resume'], ['outreach', 'Outreach drafts'], ['contacts', 'Who to contact'], ['review', 'Checks']];
  openDialog(dialogHead(`${j.company} · ${j.title}`, 'APPLICATION PACKAGE') +
    `<div class="score-row"><div><small>Base match</small>${bar(p.scores.base)}</div><div><small>ATS · base resume</small>${bar(p.scores.ats_base)}</div><div><small>ATS · tailored</small>${bar(p.scores.ats_tailored)}</div></div>
    <div class="tabs" role="tablist">${tabs.map(([k, l]) => `<button role="tab" class="${pkgTab === k ? 'active' : ''}" data-action="pkg-tab" data-tab="${k}">${l}</button>`).join('')}</div>
    <div class="tab-body">${{resume: pkgResume, outreach: pkgOutreach, contacts: pkgContacts, review: pkgReview}[pkgTab](p, j, approved)}</div>`);
  if (pkgTab === 'resume' && resumeView === 'preview') loadPreview(p).catch(e => { if ($('#docx-preview')) $('#docx-preview').textContent = e.message; });
}
let resumeView = 'preview';
function pkgResume(p, j, approved) {
  const r = p.resume, edits = resumeView === 'edit';
  return `<div class="actions"><a class="button small-button primary" href="/api/packages/${p.id}/export?format=docx">Download DOCX ↓</a><a class="button small-button" href="/api/packages/${p.id}/export?format=pdf">PDF ↓</a><a class="button small-button" href="/api/packages/${p.id}/export?format=docx&highlight=true" title="Same document with tailored text highlighted in yellow. For your review, not for applying.">Highlighted review copy ↓</a>${j.apply_url || j.url ? `<a class="button small-button" href="${esc(safeUrl(j.apply_url || j.url))}" target="_blank" rel="noopener noreferrer">Open application ↗</a>` : ''}</div>
  <div class="view-toggle section-gap"><button class="${edits ? '' : 'active'}" data-action="resume-view" data-view="preview">Preview with highlights</button><button class="${edits ? 'active' : ''}" data-action="resume-view" data-view="edit" ${approved ? 'disabled title="Applied packages are locked"' : ''}>Edit lines</button></div>
  ${edits ? resumeEditor(p, r, approved) : `<div id="docx-preview" class="docx-page loading">Rendering your document…</div>`}`;
}
function resumeEditor(p, r, approved) {
  const order = r.order || ['summary', 'skills', ...r.sections.map(s => s.key)];
  const bySection = Object.fromEntries(r.sections.map(s => [s.key, s]));
  const byOther = Object.fromEntries(r.other.map(o => [o.key || o.title.toLowerCase(), o]));
  const parts = order.map(key => {
    if (key === 'summary') return `<div class="r-head">Summary</div><textarea name="summary" class="r-edit">${esc(r.summary)}</textarea>`;
    if (key === 'skills') return `<div class="r-head">Skills</div>${r.skills.map(g => `<p class="r-skill"><b>${esc(g.category)}:</b> ${g.items.map(i => i.added ? `<mark>${esc(i.name)}</mark>` : esc(i.name)).join(', ')}</p>`).join('')}`;
    if (bySection[key]) return `<div class="r-head">${esc(bySection[key].title)}</div>${bySection[key].roles.map(role => `${role.header ? `<div class="r-role">${esc(role.header)}</div>` : ''}${role.bullets.map(b => `<div class="r-bullet ${b.changed ? 'changed' : ''}"><textarea name="b:${esc(b.id)}" rows="2">${esc(b.text)}</textarea>${b.changed ? `<small>Original: ${esc(b.original)}</small>` : ''}</div>`).join('')}`).join('')}`;
    if (byOther[key]) return `<div class="r-head">${esc(byOther[key].title)}</div>${byOther[key].lines.map(l => `<p>${esc(l)}</p>`).join('')}`;
    return '';
  }).join('');
  return `<p class="note">Edits are applied to your Word file the same way: only the words you change are touched.</p>
  <form id="resume-edit" data-id="${esc(p.id)}"><div class="resume-doc"><div class="r-name">${esc(r.header.name)}</div>${parts}</div><button class="primary section-gap">Save edits & rescore</button></form>`;
}
async function loadPreview(p) {
  const box = $('#docx-preview'); if (!box) return;
  const r = await api(`/packages/${p.id}/preview`);
  if (!$('#docx-preview') || currentPackage?.id !== p.id) return;
  const added = r.paragraphs.reduce((n, para) => n + para.runs.filter(x => x.added).length, 0);
  const runHtml = x => { let t = esc(x.text).replace(/\t+/g, '<span class="tab"></span>'); if (x.bold) t = `<b>${t}</b>`; if (x.italic) t = `<i>${t}</i>`; return x.added ? `<mark class="tailored">${t}</mark>` : t; };
  const src = r.report.source === 'your Word file'
    ? `<div class="flag ok"><span>✓ Edited inside your own Word file <b>${esc(r.report.docx_name || '')}</b>. Section order, fonts and layout are yours; ${r.report.mapped}/${r.report.total} lines matched.${r.report.failed.length ? ` ${r.report.failed.length} edit(s) couldn't be applied safely and were left as in your original.` : ''}</span></div>`
    : `<div class="flag info"><span>ⓘ No Word file attached, so this is a generated layout in your section order. Upload your .docx on <a href="#profile" data-action="goto-profile">Resume evidence</a> to have every resume edited inside your own document.</span></div>`;
  box.className = 'docx-page';
  box.innerHTML = `${src}<div class="legend"><mark class="tailored">highlighted</mark> = added or changed for this job · ${added ? `${added} change${added === 1 ? '' : 's'}` : 'no text changes'} · everything else is exactly your original</div>
    <div class="page">${r.paragraphs.map(para => {
      if (para.empty) return '<div class="pp-gap"></div>';
      // "Title | Company<TAB>Dates": your tab pushes the dates to the right margin, so mirror that.
      const left = [], right = []; let hit = false;
      for (const x of para.runs) {
        if (!hit && x.text.includes('\t')) { const k = x.text.indexOf('\t'); hit = true; left.push({...x, text: x.text.slice(0, k)}); right.push({...x, text: x.text.slice(k).replace(/^\t+/, '')}); }
        else (hit ? right : left).push(x);
      }
      const cls = `pp ${para.list ? 'li' : ''} ${para.center ? 'center' : ''}`;
      return hit ? `<div class="${cls} split"><span>${left.map(runHtml).join('')}</span><span>${right.map(runHtml).join('')}</span></div>` : `<div class="${cls}">${para.runs.map(runHtml).join('')}</div>`;
    }).join('')}</div>`;
}
function pkgOutreach(p, j) {
  const labels = {connection: 'LinkedIn connection note (≤300 chars)', recruiter: 'Email to recruiter', manager: 'Email to hiring manager', referral: 'Referral ask (peer / alumni)', followup: 'Follow-up after 5–7 days'};
  const contacts = p.contacts || [];
  return `<div class="notice">Drafts only. Nothing is sent from here. Copy a draft, send it yourself, then log it below so the tracker can learn what works.</div>
  ${contacts.length ? `<div class="actions"><label class="note">Personalise for <select id="personalize-contact">${contacts.map((c, i) => `<option value="${i}">${esc(c.name)} · ${esc(c.role)}</option>`).join('')}</select></label><button class="small-button" data-action="personalize" data-id="${esc(p.id)}">Rewrite drafts for this person</button></div>` : '<p class="note">Add a verified contact under “Who to contact” to personalise these drafts.</p>'}
  ${Object.entries(labels).map(([k, l]) => `<div class="draft"><div class="draft-head"><b>${l}</b><span class="note" data-count="${k}">${(p.drafts[k] || '').length} chars</span><button class="small-button" data-action="copy-draft" data-key="${k}">Copy</button><button class="small-button" data-action="save-draft" data-key="${k}" data-id="${esc(p.id)}">Save</button></div><textarea id="draft-${k}" rows="${k === 'connection' || k === 'followup' ? 3 : 8}">${esc(p.drafts[k] || '')}</textarea></div>`).join('')}
  <h3 class="section-gap">Outreach log</h3>${(p.outreach_log || []).map(o => `<div class="check-row"><span class="mark">✉</span><div>${esc(o.contact)} · ${esc(o.channel)} · <b>${esc(o.status)}</b><small>${when(o.at)}${o.note ? ' · ' + esc(o.note) : ''}</small></div></div>`).join('') || '<p class="note">Nothing logged yet.</p>'}
  <form id="outreach-form" data-id="${esc(p.id)}" class="inline-form"><input name="contact" type="text" placeholder="Who did you contact?" required><select name="channel"><option value="linkedin">LinkedIn</option><option value="email">Email</option><option value="referral">Referral</option><option value="other">Other</option></select><select name="status"><option value="sent">Sent</option><option value="replied">Replied</option><option value="referred">Referred me</option><option value="no_response">No response</option></select><button class="small-button primary">Log</button></form>`;
}
function pkgContacts(p, j) {
  return `<p class="note">These searches open LinkedIn or Google so you can find real people. Nothing here guesses names or emails. When you find someone relevant, save them with their profile link.</p>
  ${p.contact_plan.map(c => `<div class="persona"><div><b>${esc(c.label)}</b><small>${esc(c.why)}</small></div><div class="actions">${link(c.linkedin, 'LinkedIn')}${link(c.google, 'Google X-ray')}</div></div>`).join('')}
  <h3 class="section-gap">Verified contacts</h3>${(p.contacts || []).map(c => `<p class="note">${link(c.url, c.name)} · ${esc(c.role)}<br>${esc(c.relevance)}</p>`).join('') || '<p class="note">None saved yet.</p>'}
  <button class="small-button" data-action="contact" data-id="${esc(p.job_id)}">Add a sourced contact</button>`;
}
function qualityBlock(q, r) {
  if (!q) return '<p class="note">Re-tailor this package to see the quality report.</p>';
  const warns = q.issues.filter(i => i.sev === 'warn'), infos = q.issues.filter(i => i.sev !== 'warn');
  return `<div class="quality"><div class="q-score ${scoreClass(q.score)}"><b>${q.score}</b><small>tailoring quality</small></div>
    <div><b>Must-haves covered in summary or bullets: ${q.covered.length}/${q.musts.length}</b>
    <div class="tags">${q.covered.map(k => `<span class="tag added">✓ ${esc(k)}</span>`).join('')}${q.uncovered.map(k => `<span class="tag missing">○ ${esc(k)}</span>`).join('')}</div>
    <p class="note">${q.from_bank} bullet${q.from_bank === 1 ? '' : 's'} pulled from your career profile${q.rewritten ? ` · ${q.rewritten} rewritten by the model` : ''}${r.summary_source && r.summary_source !== 'your resume' ? ` · summary variant: “${esc(r.summary_source)}”` : ''}${r.headline ? ` · headline set to “${esc(r.headline)}”` : ''}.</p></div></div>
    ${warns.length ? `<h3 class="section-gap">Fix before sending (${warns.length})</h3>${warns.map(i => `<div class="check-row review"><span class="mark">!</span><div>${esc(i.msg)}<small>${esc(i.where)}</small></div></div>`).join('')}` : '<div class="flag ok section-gap"><span>✓ No rule violations: lengths, verbs, pronouns, weak phrases and AI-sounding words all pass.</span></div>'}
    ${q.needs_baseline.length ? `<h3 class="section-gap">Numbers you should be ready to explain</h3>${q.needs_baseline.map(x => `<div class="check-row"><span class="mark">#</span><div>${esc(x)}</div></div>`).join('')}` : ''}
    ${infos.length ? `<details class="section-gap"><summary>Style suggestions (${infos.length})</summary>${infos.map(i => `<div class="check-row"><span class="mark">·</span><div>${esc(i.msg)}<small>${esc(i.where)}</small></div></div>`).join('')}</details>` : ''}`;
}
function pkgReview(p) {
  const v = p.resume.validation || [];
  return qualityBlock(p.resume.quality, p.resume) + `<h3 class="section-gap">Truthfulness checks</h3><div class="check-row"><span class="mark">✓</span><div>${p.review.unsupported_claims_blocked} proposed rewrites blocked by the validator<small>Rule: no new numbers, no skills outside your evidence or confirmations.</small></div></div>
  <div class="check-row review"><span class="mark">?</span><div>Gaps not claimed: ${esc(p.review.gaps_not_claimed.join(', ') || 'none')}<small>If you genuinely have one, open the job and click “I have this”, then re-tailor.</small></div></div>
  ${p.review.notes.map(n => `<div class="check-row"><span class="mark">•</span><div>${esc(n)}</div></div>`).join('')}
  ${v.length ? `<h3 class="section-gap">Validator log</h3>${v.map(x => `<div class="check-row excluded"><span class="mark">×</span><div>${esc(x.where)}: ${esc(x.reason)}${x.proposed ? `<small>Proposed: ${esc(x.proposed)}</small>` : ''}</div></div>`).join('')}` : ''}
  ${(p.resume.notes || []).length ? `<h3 class="section-gap">Model notes</h3>${p.resume.notes.map(n => `<p class="note">${esc(n)}</p>`).join('')}` : ''}`;
}

// ------------------------------------------------------------------ tracker
function tracker() {
  const rows = data.jobs.filter(j => j.outcome.stage !== 'discovered').sort((a, b) => (b.outcome.at || '').localeCompare(a.outcome.at || ''));
  const today = new Date().toISOString().slice(0, 10);
  const due = rows.filter(j => j.outcome.follow_up && j.outcome.follow_up <= today && !['rejected', 'withdrawn', 'offer'].includes(j.outcome.stage));
  return heading('FOLLOW THE SIGNAL', 'Every application has a story.', 'Record what you actually submitted, then track responses and follow-ups.', `<a class="button" href="/api/tracker.csv?mode=${mode}">Export CSV ↓</a>`) +
  (due.length ? `<div class="notice warning"><strong>${due.length} follow-up${due.length === 1 ? '' : 's'} due</strong> ${due.map(j => esc(j.company)).join(', ')}</div>` : '') +
  `<section class="panel">${rows.length ? `<div class="table-wrap"><table class="table"><thead><tr><th>ROLE</th><th>STAGE</th><th>APPLIED</th><th>FOLLOW-UP</th><th>UPDATE</th></tr></thead><tbody>${rows.map(j => {
    const o = j.outcome, overdue = o.follow_up && o.follow_up <= today;
    return `<tr><td><button class="title-button" data-action="job" data-id="${esc(j.id)}">${esc(j.title)}</button><small>${esc(j.company)} · base ${pct(j.assessment.score)}${o.note ? ' · ' + esc(o.note) : ''}</small></td>
    <td>${pill(o.stage, ['rejected', 'withdrawn'].includes(o.stage) ? 'excluded' : o.stage === 'ready' ? 'review' : 'qualified')}<small>${when(o.at)}</small></td><td>${day(o.applied_at)}</td>
    <td class="${overdue ? 'overdue' : ''}">${o.follow_up ? day(o.follow_up + 'T12:00') : '—'}</td>
    <td><form class="outcome-form inline-form" data-id="${esc(j.id)}"><select name="stage" aria-label="Stage for ${esc(j.company)}">${['applied', 'replied', 'screening', 'interviewing', 'offer', 'rejected', 'withdrawn'].map(s => `<option ${s === (o.stage === 'ready' ? 'applied' : o.stage) ? 'selected' : ''}>${s}</option>`).join('')}</select><input type="date" name="follow_up" value="${esc(o.follow_up || '')}" aria-label="Follow-up date"><input type="text" name="note" placeholder="Note" value=""><button class="small-button">Save</button></form></td></tr>`;
  }).join('')}</tbody></table></div>` : empty('Your tracker is ready', 'When you tick “I’ve applied” on a review card, the role lands here with a follow-up date. A draft is never counted as an application.')}</section>`;
}

// ------------------------------------------------------------------ resume evidence
function profile() {
  const p = data.profile, parse = data.parse, demo = mode === 'demo';
  const textValue = p ? (p.source_text || p.facts.map((f, i) => `${i === 0 || p.facts[i - 1].section !== f.section ? '# ' + f.section + '\n' : ''}${f.kind === 'bullet' ? '- ' : ''}${f.text}`).join('\n')) : '';
  return heading('THE SOURCE OF TRUTH', 'Your experience. In your words.', 'Your base resume is versioned. Tailoring never invents employers, titles, dates or metrics.') +
  `<div class="notice ${demo ? 'warning' : ''}">${demo ? 'You are viewing fictional sample evidence. Switch to Live search before saving your own resume.' : 'Upload DOCX, PDF or TXT, check the extracted text, and confirm. Saving it tailors every role that already qualifies.'}</div>
  <div class="form-grid"><section class="panel form-panel"><h2>Import your base resume</h2>
  <label class="field section-gap"><span>Resume file</span><input type="file" id="resume-file" accept=".docx,.txt,.md,.pdf" ${demo ? 'disabled' : ''}></label><div id="resume-preview"></div>
  <form id="profile-form"><label class="field"><span>Your name</span><input name="name" type="text" required value="${esc(demo ? 'Sample candidate' : p?.name || 'Prashanth')}" ${demo ? 'disabled' : ''}></label>
  <label class="field"><span>Resume text</span><textarea name="text" id="resume-text" style="min-height:340px" required ${demo ? 'disabled' : ''} placeholder="Name&#10;email | phone | linkedin&#10;# Summary&#10;…&#10;# Experience&#10;AI Engineer | Company | Jan 2023 – Present&#10;- Built …">${esc(textValue)}</textarea><small>Headings like Summary, Skills, Experience, Projects, Education are detected automatically. Prefix with # to force one. Role lines need a date range.</small></label>
  <label class="check-label"><input type="checkbox" name="confirmed" required ${demo ? 'disabled' : ''}>I checked this text and confirm it accurately represents my experience.</label><button class="primary" ${demo ? 'disabled' : ''}>Save base-resume evidence</button></form></section>
  <section class="panel form-panel"><h2>What the system sees</h2>${p ? `<p class="note section-gap">${p.facts.length} facts · ${p.roles?.length || 0} roles · ${p.years_experience ?? '?'} years · version ${esc(p.version)}</p>` : ''}
  ${p && !demo ? (p.docx ? `<div class="flag ok section-gap">✓ Base Word file: <b>${esc(p.docx_name)}</b> · ${p.docx_coverage ?? '?'}% of lines matched. Every tailored resume is an edit of this document: same section order, fonts and layout.</div>` : `<div class="flag section-gap">No Word file attached yet. Upload your <b>.docx</b> on the left and save, so tailored resumes are edits of your own document instead of a generated layout.</div>`) : ''}
  ${parse ? `<h3 class="section-gap">ATS parseability ${bar(parse.score)}</h3>${parse.checks.map(c => `<div class="check-row ${c.ok ? '' : 'review'}"><span class="mark">${c.ok ? '✓' : '?'}</span><div>${esc(c.label)}</div></div>`).join('')}` : ''}
  ${p ? `<h3 class="section-gap">Skills found on your resume</h3><div class="tags">${(p.resume_skills || []).map(s => `<span class="tag">${esc(s)}</span>`).join('')}</div>
  <h3 class="section-gap">Skills you confirmed (not on resume yet)</h3><div class="tags">${(p.confirmed_skills || []).map(s => `<span class="tag added">${esc(s)} <button class="tag-x" data-action="unconfirm-skill" data-skill="${esc(s)}" aria-label="Remove ${esc(s)}">×</button></span>`).join('') || '<span class="note">None. Add skills you really have so tailoring can list them.</span>'}</div>
  <form id="skill-form" class="inline-form"><select name="skill">${data.skills_catalog.filter(s => !(p.resume_skills || []).includes(s) && !(p.confirmed_skills || []).includes(s)).map(s => `<option>${esc(s)}</option>`).join('')}</select><input name="note" type="text" placeholder="Where you used it (optional)"><button class="small-button">Confirm skill</button></form>
  <details><summary>Evidence library (${p.facts.length} facts)</summary>${p.facts.map(f => `<div class="fact"><small>${esc(f.id)} · ${esc(f.section)} · ${esc(f.kind || '')}</small>${esc(f.text)}</div>`).join('')}</details>` : empty('Ready for your resume', 'Jobs can be collected now. Match scores and tailored packages start once your evidence is confirmed.')}</section></div>`;
}

// ------------------------------------------------------------------ career profile
let careerCache = null;
function careerPage() {
  return heading('EVERYTHING ABOUT YOU', 'Your career profile.', 'One local Markdown file with every role, bullet variant, metric, project and skill you have. Tailoring picks the best evidence from here for each job.') +
    `<div id="career-body" class="loading">Opening your career profile…</div>`;
}
async function loadCareer() {
  const r = await api('/career'); careerCache = r;
  if (view !== 'career') return;
  const s = r.summary;
  $('#career-body').className = '';
  $('#career-body').innerHTML = `<div class="form-grid career-grid">
  <section class="panel form-panel"><h2>Source of truth</h2>
    <p class="note section-gap">Saved locally at <code>${esc(r.path)}</code>. Edit it here or in any editor. Lines containing <code>[FILL …]</code>, <code>[CHECK …]</code>, <code>[TO CONFIRM …]</code> or <code>{unverified}</code> are kept for you but never used in a resume.</p>
    <label class="field section-gap"><span>Import a file (.docx master dump, .md or .txt)</span><input type="file" id="career-file" accept=".docx,.md,.txt,.json"></label>
    <label class="check-label"><input type="checkbox" id="career-append"> Append to the current profile instead of replacing it</label>
    <form id="career-form"><label class="field"><span>career.md</span><textarea name="markdown" id="career-md" class="mono" spellcheck="false" placeholder="# Career profile&#10;&#10;## 3. Company, Title&#10;### End-to-end story&#10;…&#10;### Bullet bank&#10;#### Theme&#10;- Built …&#10;### Keywords this role proves&#10;Python · RAG · …">${esc(r.markdown)}</textarea></label>
    <button class="primary">Save career profile</button> <span class="note">Saving re-tailors every package that used the old version.</span></form>
    <details class="section-gap"><summary>Format guide</summary><pre>## 3. Company, Job title          ← one section per role (company, then title)
### End-to-end story               ← context the writer can draw on
### Bullet bank
#### Theme name                    ← variants of one story compete; one wins per resume
- Built … (current resume)         ← "(current …)", "(first version …)" tags are ignored
### Keywords this role proves
Python · LangGraph · MCP
### Metrics                        ← table: Metric | Value | Baseline / method | Status
## 2. Profile → ### Summary variants → #### Variant name → paragraph
## 7. Projects → ### Project name → paragraph
## 9. Skills inventory → table: Category | Skill | Where it's proven
## 1. How to use this document → bullet list of your own rules</pre></details>
  </section>
  <section class="panel form-panel"><h2>What tailoring will use</h2>
    ${s ? `<div class="mini-stats"><div><b>${s.roles.reduce((n, x) => n + x.verified, 0)}</b><small>verified bullets</small></div><div><b>${s.summaries}</b><small>summary variants</small></div><div><b>${s.proven_skills}/${s.skills}</b><small>skills with proof</small></div><div><b>${s.unverified_lines}</b><small>unverified items</small></div></div>
    <h3 class="section-gap">Roles</h3>${r.career.roles.map(role => `<details class="role-card"><summary><b>${esc(role.company)}</b> · ${esc(role.title)} <span class="note">${role.bullets.filter(b => b.verified).length} bullets · ${new Set(role.bullets.map(b => b.theme)).size} themes · ${role.keywords.length} keywords</span></summary>
      ${Object.entries(role.bullets.reduce((m, b) => ((m[b.theme] = m[b.theme] || []).push(b), m), {})).map(([theme, bs]) => `<div class="theme"><small>${esc(theme)}</small>${bs.map(b => `<p class="${b.verified ? '' : 'unverified'}">${b.verified ? '' : '⚠ '}${esc(b.text)}</p>`).join('')}</div>`).join('')}
      ${role.metrics.length ? `<div class="theme"><small>Metrics</small>${role.metrics.map(m => `<p>${esc(m.value)} · ${esc(m.metric)} <span class="note">${esc(m.status)}</span></p>`).join('')}</div>` : ''}</details>`).join('')}
    <h3 class="section-gap">Summary variants</h3>${r.career.summaries.map(v => `<div class="fact"><small>${esc(v.name)}</small>${esc(v.text)}</div>`).join('') || '<p class="note">None yet. Add them under “Summary variants”.</p>'}
    <h3 class="section-gap">Your rules</h3>${r.career.rules.map(x => `<p class="note">• ${esc(x)}</p>`).join('') || '<p class="note">None.</p>'}`
    : empty('No career profile yet', 'Import your experience master dump (.docx) or start writing Markdown on the left. Until then, tailoring can only reuse lines from your base resume.')}
  </section></div>`;
}

// ------------------------------------------------------------------ sources
function sources() {
  const all = data.sources, cats = {};
  all.forEach(s => cats[s.category || 'custom'] = (cats[s.category || 'custom'] || 0) + 1);
  const healthy = all.filter(s => s.status === 'healthy' || s.status === 'partial').length, errors = all.filter(s => s.status === 'error');
  const seedCats = [['ai-lab', 'AI labs'], ['ai-startup', 'AI startups'], ['saas', 'SaaS / product'], ['fintech', 'Fintech'], ['enterprise', 'Enterprise (Workday)'], ['dallas-area', 'Dallas–Fort Worth'], ['consulting', 'Consulting']];
  return heading('FRESH FROM THE SOURCE', 'Start at the company door.', 'Public ATS boards (Greenhouse, Ashby, Lever, SmartRecruiters, Workday). Coverage is exactly the companies listed here.', all.length ? `<button data-action="collect">↻ Search now</button>` : '') +
  `<div class="form-grid"><section class="panel form-panel"><h2>Starter registry</h2><p class="note section-gap">250 company boards verified live on Oct 3, 2026: AI labs, AI startups, product companies, and large sponsors including 12 DFW employers. Pick categories to add.</p>
  <form id="seed-form">${seedCats.map(([k, l]) => `<label class="check-label"><input type="checkbox" name="${k}" checked> ${l}${cats[k] ? ` <span class="note">· ${cats[k]} added</span>` : ''}</label>`).join('')}<button class="primary">Add selected categories</button></form>
  <h2 class="section-gap">Add a company</h2><p class="note section-gap">Paste its careers or job-board URL, e.g. jobs.ashbyhq.com/acme, boards.greenhouse.io/acme, jobs.lever.co/acme, or acme.wd5.myworkdayjobs.com/External.</p>
  <form id="source-form"><label class="field"><span>Company name</span><input name="company" type="text" required></label><label class="field"><span>Careers / job board URL</span><input name="careers_url" type="url" required placeholder="https://jobs.ashbyhq.com/company"></label><label class="field"><span>Company website (optional)</span><input name="company_url" type="url" placeholder="https://company.com"></label><button class="primary">Verify & add board</button></form></section>
  <section class="panel"><div class="panel-head"><div><h2>Connected boards · ${all.length}</h2><p class="subtitle">${healthy} healthy · ${errors.length} errors · ${all.filter(s => s.enabled === false).length} paused</p></div><input id="source-search" type="text" placeholder="Filter" style="max-width:160px"></div>
  <div id="source-list" class="source-list">${sourceRows('')}</div>
  <div class="form-panel note">Freshness evidence: Greenhouse first-published time · Ashby latest publication (reposts possible) · Lever creation time · SmartRecruiters release date · Workday “Posted Today/Yesterday” (whole days only).</div></section></div>`;
}
function sourceRows(term) {
  const rows = data.sources.filter(s => `${s.company} ${s.provider} ${s.category}`.toLowerCase().includes(term.toLowerCase()))
    .sort((a, b) => (b.relevant || 0) - (a.relevant || 0) || a.company.localeCompare(b.company));
  return rows.length ? rows.map(s => `<div class="source-row ${s.enabled === false ? 'paused' : ''}"><div><b>${esc(s.company)}</b><small>${esc(s.provider)} / ${esc(s.board)} · ${esc(s.category || 'custom')} · checked ${when(s.last_checked_at)}</small>${s.error ? `<small class="err">${esc(s.error)}</small>` : ''}</div>
  <div class="source-meta">${pill(s.status === 'not_checked' ? 'new' : s.status, s.status === 'healthy' ? 'qualified' : s.status === 'error' ? 'excluded' : 'review')}<small>${s.relevant ?? 0} matching / ${s.total_open ?? s.open_jobs ?? 0} open</small></div>
  <div class="actions"><button class="small-button" data-action="toggle-source" data-id="${esc(s.id)}" data-enabled="${s.enabled === false ? 'true' : 'false'}">${s.enabled === false ? 'Resume' : 'Pause'}</button><button class="small-button danger" data-action="delete-source" data-id="${esc(s.id)}" aria-label="Remove ${esc(s.company)}">×</button></div></div>`).join('') :
    empty('No boards connected', 'Add the starter registry on the left to cover 250 companies at once.');
}

// ------------------------------------------------------------------ insights
function insights() {
  return heading('THE WEEKEND LOOP', 'Listen to the market.', 'What eligible roles asked for this week, what your resume can’t show yet, and which projects would close the gap.',
    `<button data-action="snapshot">Save this week’s report</button>`) + `<div id="report-content" class="loading">Reading the week’s roles…</div>`;
}
async function loadReport() {
  const r = await api('/insights?mode=' + mode); insightCache = r;
  if (view !== 'insights') return;
  const w = r.weekly, o = r.outcomes, maxJobs = Math.max(1, ...w.demand.map(d => d.jobs));
  $('#report-content').className = '';
  $('#report-content').innerHTML = `<div class="notice">Last ${w.window_days} days · ${w.roles_in_window} roles that pass country, sponsorship, experience and employment checks · generated ${when(w.generated_at)}. ${new Date().getDay() >= 5 || new Date().getDay() === 0 ? '<b>It’s the weekend. Pick one project below.</b>' : 'A full report is saved automatically on ' + esc(data.schedule.config.weekly_day) + ' at ' + esc(data.schedule.config.weekly_time) + ' CT.'}</div>
  <h2>Weekend project ideas</h2><p class="note">Ranked by how many of this week’s roles ask for the skills you can’t show yet. Add a resume line only after the project exists.</p>
  <div class="cards section-gap">${w.projects.map((p, i) => `<article class="card project"><div class="eyebrow">#${i + 1} · ~${p.hours}h${p.extends ? ' · extends ' + esc(p.extends) : ''}</div><h2>${esc(p.title)}</h2><p class="section-gap">${esc(p.pitch)}</p>
   <div class="tags">${p.covers.map(s => `<span class="tag missing">${esc(s)}</span>`).join('')}</div>
   <b class="small-h">Deliverables</b><ul class="tight">${p.deliverables.map(d => `<li>${esc(d)}</li>`).join('')}</ul><b class="small-h">Done when</b><p>${esc(p.metric)}</p>
   <details><summary>Roles asking for this (${p.cites.length})</summary>${p.cites.map(c => `<p class="note">${c.url ? link(c.url, c.company + ' · ' + c.title) : esc(c.company + ' · ' + c.title)}</p>`).join('')}</details></article>`).join('') || '<p class="note">No gap clusters yet. Collect roles and add your resume.</p>'}</div>
  ${w.quick_wins.length ? `<section class="panel form-panel section-gap"><h2>Quick wins: skills you confirmed but your resume doesn’t show</h2><p class="note section-gap">Tailoring adds these to the Skills section. Add a real bullet to your base resume to strengthen your base match.</p><div class="tags">${w.quick_wins.map(q => `<span class="tag added">${esc(q.skill)} · ${q.jobs} roles</span>`).join('')}</div></section>` : ''}
  <section class="panel section-gap"><div class="panel-head"><h2>Market demand this week</h2><span class="pill">${w.demand.length} SKILLS</span></div><div class="table-wrap"><table class="table"><thead><tr><th>SKILL</th><th>ROLES</th><th>REQUIRED</th><th>COMPANIES</th><th>YOUR EVIDENCE</th></tr></thead><tbody>${w.demand.map(d => `<tr><td><b>${esc(d.skill)}</b></td><td><div class="hbar"><span style="width:${100 * d.jobs / maxJobs}%"></span></div><small>${d.jobs} · ${d.share}%</small></td><td>${d.required}</td><td>${d.companies}</td><td>${pill(d.status, d.status === 'on resume' ? 'qualified' : d.status === 'gap' ? 'excluded' : 'review')}</td></tr>`).join('')}</tbody></table></div></section>
  <h2 class="section-gap">What’s working</h2><p class="note">${esc(o.notice)}</p>
  <section class="panel form-panel section-gap"><div class="funnel">${o.funnel.map(f => `<div><b>${f.count}</b><small>${esc(f.stage)}</small></div>`).join('')}</div></section>
  <div class="cards section-gap">${Object.entries(o.breakdowns).map(([name, rows]) => `<article class="card"><h3>${esc(name)}</h3>${rows.length ? `<table class="req-table">${rows.map(r => `<tr><td>${esc(r.bucket)}</td><td>${r.responses}/${r.n}</td><td><b>${r.rate ?? '—'}%</b>${r.small_sample ? ' <small>small sample</small>' : ''}</td></tr>`).join('')}</table>` : '<p>No applications recorded yet.</p>'}</article>`).join('')}</div>
  ${r.snapshots.length ? `<section class="panel form-panel section-gap"><h2>Saved weekly reports</h2>${r.snapshots.map(s => `<p class="note">${when(s.generated_at)} · <a href="/api/insights/snapshots/${esc(s.id)}" target="_blank">JSON ↗</a></p>`).join('')}</section>` : ''}`;
}

// ------------------------------------------------------------------ settings
function settings() {
  const s = data.settings, sch = data.schedule.config, m = data.models;
  return heading('YOUR SEARCH RULES', 'A clear brief for the team.', 'Confirmed requirements, model provider and how often Scout searches.') +
  `<div class="form-grid"><section class="panel form-panel"><h2>Confirmed preferences</h2>
  ${[['Dallas, TX · open to relocation', 'US remote, hybrid and onsite · 0–4 years · full-time and contract'], ['$90,000 minimum annual compensation', 'Undisclosed pay is allowed when the company is verified (registry boards count; boards you add need your check).'], ['Sponsorship needed in the future', 'Explicit restrictions (no sponsorship, citizens only, clearance, no H-1B) are excluded. Unstated stays visible as unknown.'], [`Base match ≥${s.match_min}%: tailored automatically · ${s.review_min}–${s.match_min - 1}%: Analyst asks you · <${s.review_min}%: skip`, 'Weighted skill coverage against your base resume. Tailoring can’t raise it.'], ['You apply from your review desk. No cap on qualifying applications.', 'Nothing is ever submitted or sent by the app.']].map(([a, b]) => `<div class="check-row"><span class="mark">✓</span><div>${a}<small>${b}</small></div></div>`).join('')}</section>
  <section class="panel form-panel"><h2>Runtime</h2><form id="settings-form" class="section-gap">
  <label class="field"><span>Tailoring and chat provider</span><select name="provider"><option value="extractive" ${s.provider === 'extractive' ? 'selected' : ''}>Extractive · free, no model, keeps your wording</option><option value="claude" ${s.provider === 'claude' ? 'selected' : ''} ${m.claude ? '' : 'disabled'}>Claude · ${esc(m.claude_model)} rewrites bullets (validated)</option><option value="groq" ${s.provider === 'groq' ? 'selected' : ''} ${m.groq ? '' : 'disabled'}>Groq · open model rewrites (validated)</option><option value="gemini" ${s.provider === 'gemini' ? 'selected' : ''} ${m.gemini ? '' : 'disabled'}>${esc((m.labels || {}).gemini || 'Gemini')} · resume + messages (validated)</option><option value="nvidia" ${s.provider === 'nvidia' ? 'selected' : ''} ${m.nvidia ? '' : 'disabled'}>${esc((m.labels || {}).nvidia || 'NVIDIA')} · free backup (validated)</option></select><small>${m.gemini ? 'Gemini is configured (free tier). Your name, email, phone and links are swapped for placeholders before anything is sent.' : 'Add GEMINI_API_KEY to the .env file next to the app and restart to enable Gemini (free).'} Every model edit goes through the same truthfulness checks; anything that fails keeps your original wording. Claude: about $0.03–0.06 per resume.</small></label>
  <div class="two"><label class="field"><span>Tailor at ≥ (%)</span><input name="match_min" type="number" min="40" max="95" value="${s.match_min}"></label><label class="field"><span>Ask me from (%)</span><input name="review_min" type="number" min="30" max="90" value="${s.review_min}"></label></div>
  <label class="field"><span>Contract minimum (USD/hour)</span><input name="hourly_min" type="number" min="40" max="500" step="0.5" value="${s.hourly_min}" required><small>$40 up to this floor goes to review.</small></label>
  <h3 class="section-gap">Background</h3>
  <label class="check-label"><input type="checkbox" name="notifications" ${s.notifications !== false ? 'checked' : ''}> Windows notification when ${esc(ROLES.reviewer.name)} brings new jobs or a follow-up is due</label>
  <label class="check-label"><input type="checkbox" name="backup" ${s.backup !== false ? 'checked' : ''}> Back up my data to OneDrive every night (keeps 14 days)</label>
  <div class="actions"><button type="button" class="small-button" data-action="test-notify">Send a test notification</button><button type="button" class="small-button" data-action="backup-now">Back up now</button>${data.schedule.state?.last_backup ? `<span class="note">Last backup: ${esc(data.schedule.state.last_backup)}</span>` : ''}</div>
  <h3 class="section-gap">Your team's names</h3><div class="names-grid">${Object.entries(ROLES).filter(([id]) => id !== 'lead').map(([id, r]) => `<label class="field"><span>${esc(r.role)}</span><input name="name:${id}" type="text" maxlength="20" value="${esc(r.name)}"></label>`).join('')}</div>
  <h3 class="section-gap">${esc(ROLES.scout.name)} · Scout</h3>
  <label class="check-label"><input type="checkbox" name="enabled" ${sch.enabled ? 'checked' : ''}> Search automatically while the app is running</label>
  <label class="field"><span>Search every (minutes)</span><input name="interval_minutes" type="number" min="15" max="1440" step="15" value="${sch.interval_minutes}"></label>
  <label class="check-label"><input type="checkbox" name="auto_prepare" ${sch.auto_prepare ? 'checked' : ''}> Tailor every role that meets my rules and bring it to my review desk</label>
  <div class="two"><label class="field"><span>Weekly report day</span><select name="weekly_day">${['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'].map(d => `<option ${d === sch.weekly_day ? 'selected' : ''}>${d}</option>`).join('')}</select></label><label class="field"><span>Time (CT)</span><input name="weekly_time" type="time" value="${esc(sch.weekly_time)}"></label></div>
  <p class="note">Last search: ${when(data.schedule.next.last_collect)} · next: ${when(data.schedule.next.collect)} · next report: ${when(data.schedule.next.weekly)}. After the laptop sleeps, one catch-up search runs.</p>
  <button class="primary">Save preferences</button></form></section></div>`;
}

// ------------------------------------------------------------------ render
const NAMES = {office: 'The office', jobs: 'Opportunities', review: 'Review desk', tracker: 'Application tracker', profile: 'Base resume', career: 'Career profile', sources: 'Company sources', insights: 'Weekend insights', settings: 'Preferences'};
async function render() {
  if (!data) return;
  clearInterval(replayTimer); replaying = false; unmountOffice();
  if (!NAMES[view]) view = 'office';
  if (render.last !== view) window.scrollTo(0, 0);
  render.last = view;
  $('#crumb').textContent = NAMES[view];
  $$('[data-view]').forEach(a => a.classList.toggle('active', a.dataset.view === view));
  $('#jobs-count').textContent = data.jobs.filter(isFresh).length;
  $('#review-count').textContent = data.inbox.length;
  $('#footer-mode').textContent = mode === 'demo' ? 'Fictional sample · no submissions' : 'Local · private · user-directed';
  const views = {office, jobs, review, tracker, profile, career: careerPage, sources, insights, settings};
  $('#main').innerHTML = views[view]();
  if (view === 'office') { mountOffice($('#office-canvas'), showAgent); renderActivity(); events.slice(-12).forEach(applyEvent); setPending(pendingCounts()); }
  if (view === 'jobs') jobTable();
  if (view === 'insights') loadReport().catch(e => toast(e.message, true));
  if (view === 'career') loadCareer().catch(e => toast(e.message, true));
}
async function refresh(redraw = true) {
  const wantExcluded = view === 'jobs' && ['excluded', 'all'].includes(jobFilter.status);
  data = await api('/state?mode=' + mode + (wantExcluded ? '&excluded=true' : ''));
  setNames(data.agent_names);
  if (redraw) await render();
  else { $('#review-count').textContent = data.inbox.length; syncOffice(); }
}
function openDialog(content) { $('#detail-content').innerHTML = content; if (!$('#detail').open) $('#detail').showModal(); }
function dialogHead(title, sub = '') { return `<div class="detail-heading"><div><div class="eyebrow">${sub}</div><h2>${esc(title)}</h2></div><button class="close" data-action="close" aria-label="Close details">×</button></div>`; }
function companyForm(id) {
  const j = data.jobs.find(j => j.id === id) || currentJob || {}, e = j.company_evidence || {};
  openDialog(dialogHead('Check the company', 'PAY UNDISCLOSED · EVIDENCE REQUIRED') + `<p class="note">Confirm the posting belongs to the company, the business is credible, and identifiable people work there. A website alone isn’t proof. The app records your evidence; it doesn’t certify the company.</p><form id="company-form" data-id="${esc(id)}"><label class="field"><span>Official company or careers URL</span><input name="official_url" type="url" required value="${esc(e.official_url || j.company_url || '')}"></label><label class="field"><span>Source showing real people at the company</span><input name="people_url" type="url" required value="${esc(e.people_url || '')}" placeholder="Team page or LinkedIn company people page"></label><label class="field"><span>What did you verify?</span><textarea name="note" minlength="30" required>${esc(e.note || '')}</textarea></label><label class="check-label"><input name="confirmed" type="checkbox" required>I checked this evidence and accept keeping this role despite undisclosed pay.</label><button class="primary">Save company evidence</button></form>`);
}
function resolutionForm(id, key) { openDialog(dialogHead('Document your check', key.toUpperCase()) + `<p class="note">Use source evidence for this unresolved item. A confirmed exclusion can’t be overridden here.</p><form id="resolution-form" data-id="${esc(id)}" data-key="${esc(key)}"><label class="field"><span>Evidence URL</span><input name="source_url" type="url" required></label><label class="field"><span>What confirms this requirement is met?</span><textarea name="note" minlength="15" required></textarea></label><button class="primary">Save review</button></form>`); }
function contactForm(id) {
  openDialog(dialogHead('Add a professional contact', 'SOURCE BEFORE OUTREACH') + `<form id="contact-form" data-id="${esc(id)}"><label class="field"><span>Type</span><select name="persona"><option value="recruiter">Recruiter</option><option value="manager">Hiring manager</option><option value="peer">Engineer on the team</option><option value="alumni">Alumni</option></select></label>${[['name', 'Name'], ['role', 'Current role'], ['url', 'Profile URL (LinkedIn or company page)'], ['relevance', 'Why this person is relevant to this opening']].map(([n, l]) => `<label class="field"><span>${l}</span><input name="${n}" type="${n === 'url' ? 'url' : 'text'}" required ${n === 'relevance' ? 'minlength="10"' : ''}></label>`).join('')}<button class="primary">Save contact</button></form>`);
}
function renderActivity() {
  if (!$('#activity-list')) return;
  const recent = events.slice(-10).reverse();
  $('#activity-list').innerHTML = recent.length ? recent.map(e => `<div class="activity ${e.type}"><strong>${esc(ROLES[e.actor]?.name || e.actor)}</strong> · ${esc(e.message)}<small><span>${esc(e.type)}</span><span>${new Date(e.at).toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'})}</span></small></div>`).join('') : '<div class="empty-activity">No work has run yet.<br><br>Load company sources and collect, or use the sample workspace to see a full run.</div>';
}
async function poll() {
  try {
    const incoming = await api('/events?after=' + lastSeq);
    if (incoming.length) lastSeq = incoming.at(-1).seq;
    // A run we haven't seen (e.g. Scout's hourly search) triggers one refresh so its events can animate.
    const unseen = incoming.some(e => !data.runs.some(r => r.id === e.run_id) && !seenRuns.has(e.run_id) && !/^(batch|report|scheduler)[:]/.test(e.run_id));
    incoming.forEach(e => seenRuns.add(e.run_id));
    if (unseen) await refresh(false);
    const allowed = new Set(data?.runs.map(r => r.id) || []);
    const fresh = incoming.filter(e => allowed.has(e.run_id) || /^(batch|report)[:]/.test(e.run_id));
    if (fresh.length) {
      events.push(...fresh); events = events.slice(-1000);
      if (!replaying) { fresh.forEach(applyEvent); renderActivity(); if ($('#scene-caption')) $('#scene-caption').textContent = fresh.at(-1).message; }
    }
    const running = data.runs.find(r => r.status === 'running');
    if (running && activeRun !== running.id) activeRun = running.id;
    if (activeRun) {
      const runs = await api('/runs/active');
      if (runs.running.length) activeRun = runs.running[0].id;
      else {
        const run = runs.latest.find(r => r.id === activeRun); activeRun = null;
        const before = data.inbox.filter(i => i.kind === 'package_ready').length;
        await refresh(false);
        const after = data.inbox.filter(i => i.kind === 'package_ready').length;
        if (after > before) toast(`${after - before} new package${after - before === 1 ? '' : 's'} waiting at your review desk.`);
        else if (run) toast(run.status === 'completed' ? `${run.task}: done.` : `${run.task}: finished with items needing attention.`, run.status !== 'completed');
        if (!$('#detail').open && ['jobs', 'review', 'tracker', 'sources'].includes(view)) await render();
      }
    }
  } catch (e) { if (activeRun) toast('Connection interrupted. Saved work will be available when the server returns.', true); }
}
async function startPreparation(ids) {
  if (!ids.length) { toast('No roles meet the tailoring threshold yet. Check your resume and eligibility.'); return; }
  const result = await api('/prepare', {method: 'POST', body: {job_ids: ids}});
  activeRun = result.run_id; if ($('#detail').open) $('#detail').close();
  location.hash = 'office'; view = 'office'; await refresh();
  toast(`Tailoring ${ids.length} package${ids.length === 1 ? '' : 's'}. Follow along at each desk.`);
}
async function reopenJob() { if (currentJob) { await refresh(false); await showJob(currentJob.id); } }

// ------------------------------------------------------------------ events
document.addEventListener('click', async e => {
  const b = e.target.closest('[data-action]'); if (!b) return;
  const {action, id, key} = b.dataset;
  try {
    if (action === 'close') $('#detail').close();
    else if (action === 'job') await showJob(id);
    else if (action === 'package') { pkgTab = 'resume'; resumeView = 'preview'; showPackage(id); }
    else if (action === 'pkg-tab') showPackage(currentPackage.id, b.dataset.tab);
    else if (action === 'resume-view') { resumeView = b.dataset.view; showPackage(currentPackage.id, 'resume'); }
    else if (action === 'goto-profile') { e.preventDefault(); location.hash = 'profile'; }
    else if (action === 'agent') await showAgent(id);
    else if (action === 'add-job') addJobDialog();
    else if (action === 'test-notify') { const r = await api('/notify/test', {method: 'POST'}); toast(r.shown ? 'Sent. Look at the bottom-right of your screen.' : 'Notifications are not available on this system.', !r.shown); }
    else if (action === 'backup-now') { const r = await api('/backup', {method: 'POST'}); toast('Backed up to ' + r.path); await refresh(); }
    else if (action === 'card-draft') { cardDraft[id] = key; const card = b.closest('.apply-card'); const item = data.inbox.find(x => x.package_id === id); if (card && item) card.querySelector('.reach').outerHTML = outreachPanel(item); }
    else if (action === 'card-copy') { const t = b.closest('.reach').querySelector('.card-draft-text'); await navigator.clipboard.writeText(t.value); toast('Copied. Paste it into LinkedIn or your email.'); }
    else if (action === 'card-save-draft') { const t = b.closest('.reach').querySelector('.card-draft-text'); await api(`/packages/${id}/drafts`, {method: 'PUT', body: {key: t.dataset.key, text: t.value}}); await refresh(false); toast('Draft saved.'); }
    else if (action === 'addjob-tab') addJobDialog(b.dataset.tab);
    else if (action === 'side-tab') { sideTab = b.dataset.tab; $$('.seg button').forEach(x => x.classList.toggle('active', x.dataset.tab === sideTab)); $('#side-body').innerHTML = sideTab === 'inbox' ? inboxList() : '<div id="activity-list" class="activity-list"></div>'; renderActivity(); }
    else if (action === 'inbox-open') inboxItemDialog(id);
    else if (action === 'goto-review') location.hash = 'review';
    else if (action === 'chat-example') { const input = $('#chat-form input'); input.value = b.dataset.text; input.focus(); }
    else if (action === 'mark-applied') {
      const card = b.closest('.apply-card'), acks = [...card.querySelectorAll('.flag-ack')];
      const unticked = acks.filter(x => !x.checked);
      if (unticked.length) { unticked[0].closest('.flag').classList.add('shake'); throw Error('Tick each flag to confirm you checked it before marking applied.'); }
      let r;
      try { r = await api(`/packages/${id}/applied`, {method: 'POST', body: {acknowledged_flags: acks.map(x => x.value)}}); }
      catch (err) { activeRun = 'pending'; if ($('#detail').open) $('#detail').close(); await refresh(view !== 'office'); throw err; }
      if ($('#detail').open) $('#detail').close();
      await refresh(view !== 'office'); toast(`Recorded. Follow-up reminder set for ${r.follow_up}.`);
    }
    else if (action === 'skip-job') { await api(`/jobs/${encodeURIComponent(id)}/skip`, {method: 'POST', body: {reason: 'Skipped from review desk'}}); if ($('#detail').open) $('#detail').close(); await refresh(view !== 'office'); toast('Skipped. It won’t come back unless the posting changes.'); }
    else if (action === 'tailor-anyway') { const r = await api(`/jobs/${encodeURIComponent(id)}/tailor-anyway`, {method: 'POST'}); if (r.run_id) activeRun = r.run_id; if ($('#detail').open) $('#detail').close(); await refresh(view !== 'office'); toast(r.notice || 'The Writer is tailoring it now.'); }
    else if (action === 'retailor') { await api('/advance?mode=' + mode, {method: 'POST'}); activeRun = 'pending'; if ($('#detail').open) $('#detail').close(); toast('The Writer is re-tailoring those packages.'); }
    else if (action === 'pause-failing') { const r = await api('/sources/pause-failing', {method: 'POST'}); if ($('#detail').open) $('#detail').close(); await refresh(view !== 'office'); toast(`Paused ${r.paused.length} failing boards.`); }
    else if (action === 'followed-up') { await api(`/jobs/${encodeURIComponent(id)}/followed-up`, {method: 'POST'}); if ($('#detail').open) $('#detail').close(); await refresh(view !== 'office'); toast('Follow-up recorded.'); }
    else if (action === 'company') companyForm(id);
    else if (action === 'resolve') resolutionForm(id, key);
    else if (action === 'contact') contactForm(id);
    else if (action === 'confirm-skill') { await api('/skills', {method: 'POST', body: {skill: b.dataset.skill, mode, note: 'Confirmed from job page'}}); toast(`${b.dataset.skill} confirmed. Re-tailor to include it.`); await reopenJob(); }
    else if (action === 'unconfirm-skill') { await api('/skills/' + encodeURIComponent(b.dataset.skill) + '?mode=' + mode, {method: 'DELETE'}); if (id) await reopenJob(); else await refresh(); }
    else if (action === 'collect') {
      if (mode === 'demo') { toast('Collection uses Live search. Sample jobs are fictional.'); return; }
      const r = await api('/collect', {method: 'POST', body: {auto_prepare: false}}); activeRun = r.run_id; await refresh(); toast(`Checking ${data.sources.filter(s => s.enabled !== false).length} company boards…`);
    }
    else if (action === 'prepare-one') await startPreparation([id]);
    else if (action === 'prepare-selected') await startPreparation([...selected]);
    else if (action === 'prepare-all') {
      const existing = new Set(data.packages.map(p => p.job_id));
      await startPreparation(data.jobs.filter(j => canPrepare(j) && (isFresh(j) || mode === 'demo') && !existing.has(j.id)).map(j => j.id));
    }
    else if (action === 'select-ready') $$('.package-check:not(:disabled)').forEach(x => x.checked = true);
    else if (action === 'approve-batch') {
      const ids = $$('.package-check:checked').map(x => x.value); if (!ids.length) throw Error('Select the reviewed packages first');
      const r = await api('/batches', {method: 'POST', body: {package_ids: ids, acknowledged: $('#batch-ack').checked}}); await refresh(); toast(r.notice);
    }
    else if (action === 'copy-draft') { await navigator.clipboard.writeText($('#draft-' + key).value); toast('Copied. Paste it into LinkedIn or email.'); }
    else if (action === 'save-draft') { const d = await api(`/packages/${id}/drafts`, {method: 'PUT', body: {key, text: $('#draft-' + key).value}}); currentPackage.drafts = d; toast('Draft saved.'); }
    else if (action === 'personalize' && b.dataset.inline) { await api(`/packages/${id}/personalize`, {method: 'POST', body: {contact_index: 0}}); await refresh(view !== 'office'); const item = data.inbox.find(x => x.package_id === id); const card = b.closest('.apply-card'); if (card && item) card.outerHTML = inboxCard(item); toast('Drafts personalised.'); }
    else if (action === 'personalize') { await api(`/packages/${id}/personalize`, {method: 'POST', body: {contact_index: Number($('#personalize-contact').value)}}); await refresh(false); showPackage(id, 'outreach'); toast('Drafts personalised.'); }
    else if (action === 'toggle-source') { await api(`/sources/${encodeURIComponent(id)}/toggle`, {method: 'POST', body: {enabled: b.dataset.enabled === 'true'}}); await refresh(); }
    else if (action === 'delete-source') { if (!confirm('Remove this board? Jobs already collected stay in your history.')) return; await api(`/sources/${encodeURIComponent(id)}`, {method: 'DELETE'}); await refresh(); }
    else if (action === 'snapshot') { await api('/insights/snapshot?mode=' + mode, {method: 'POST'}); toast('Weekly report saved.'); await loadReport(); }
    else if (action === 'replay') {
      clearInterval(replayTimer);
      const run = data.runs.find(r => r.status !== 'running'); if (!run) throw Error('Run something first, then replay its real event log');
      const recorded = await api('/events?run_id=' + run.id); if (!recorded.length) throw Error('No recorded events found');
      mountOffice($('#office-canvas'), showAgent); replaying = true; $('#scene-state').textContent = '▷ REPLAY · VISUALLY PACED';
      let n = 0;
      replayTimer = setInterval(() => {
        if (n >= recorded.length) { clearInterval(replayTimer); replaying = false; $('#scene-caption').textContent = 'Replay complete. Original timestamps are preserved.'; return; }
        const ev = recorded[n++]; applyEvent(ev); $('#scene-caption').textContent = `${n}/${recorded.length} · ${ev.message}`;
      }, 900);
    }
    else if (action === 'stop-replay') { clearInterval(replayTimer); replaying = false; await render(); }
  } catch (err) { toast(err.message, true); }
});
document.addEventListener('change', async e => {
  const el = e.target;
  try {
    if (el.id === 'mode') { mode = el.value; localStorage.setItem('cc-mode', mode); selected.clear(); events = []; lastSeq = 0; activeRun = null; jobFilter.fresh = mode === 'demo' ? 'all' : 'fresh'; await refresh(); }
    if (el.id === 'job-filter') { jobFilter.status = el.value; if (['excluded', 'all'].includes(el.value) && !data.includes_excluded) { await refresh(false); } jobTable(); }
    if (el.id === 'job-fresh') { jobFilter.fresh = el.value; jobTable(); }
    if (el.classList.contains('job-check')) { el.checked ? selected.add(el.value) : selected.delete(el.value); selectionBar(); }
    if (el.id === 'select-visible') { $$('.job-check:not(:disabled)').forEach(x => { x.checked = el.checked; el.checked ? selected.add(x.value) : selected.delete(x.value); }); selectionBar(); }
    if (el.id === 'reduced-motion') setReduced(el.checked);
    if (el.id === 'career-file' && el.files[0]) {
      const form = new FormData(); form.append('file', el.files[0]);
      const r = await api('/career/import?replace=' + !$('#career-append').checked, {method: 'POST', body: form});
      toast(`Imported: ${r.summary.roles.length} roles, ${r.summary.roles.reduce((n, x) => n + x.verified, 0)} verified bullets, ${r.summary.unverified_lines} items to confirm.`);
      await loadCareer();
    }
    if (el.id === 'resume-file' && el.files[0]) {
      const form = new FormData(); form.append('file', el.files[0]);
      const r = await api('/profile/preview', {method: 'POST', body: form});
      $('#resume-text').value = r.text; pendingUpload = r.upload_id ? {upload_id: r.upload_id, filename: r.filename} : null;
      const pv = r.preview;
      $('#resume-preview').innerHTML = `<div class="notice"><div><b>Detected:</b> ${pv.roles.length} roles · ${pv.years} years · ${pv.skills.length} skills · parseability ${pv.parse.score}%<br><small>${pv.roles.map(esc).join(' · ') || 'No dated roles found. Make sure role lines include a date range, e.g. Jan 2023 – Present.'}</small></div></div>`;
      toast(r.notice);
    }
  } catch (err) { toast(err.message, true); }
});
document.addEventListener('input', e => {
  if (e.target.id === 'job-search') { jobFilter.term = e.target.value; jobTable(); }
  if (e.target.id === 'source-search') $('#source-list').innerHTML = sourceRows(e.target.value);
  if (e.target.id?.startsWith('draft-')) { const k = e.target.id.slice(6), c = $(`[data-count="${k}"]`); if (c) c.textContent = `${e.target.value.length} chars${k === 'connection' && e.target.value.length > 300 ? ' · over LinkedIn’s 300 limit' : ''}`; }
});
document.addEventListener('submit', async e => {
  e.preventDefault();
  const f = e.target, values = Object.fromEntries(new FormData(f));
  const b = f.querySelector('button:not([type=button])'); if (b) b.disabled = true;
  try {
    if (f.classList.contains('card-log')) { await api(`/packages/${f.dataset.id}/outreach`, {method: 'POST', body: {...values, status: 'sent'}}); await refresh(view !== 'office'); const item = data.inbox.find(x => x.package_id === f.dataset.id); const card = f.closest('.apply-card'); if (card && item) card.outerHTML = inboxCard(item); toast('Logged. The tracker will learn whether outreach helps.'); return; }
    if (f.id === 'intake-form') { await startIntake(values); f.querySelector('button').textContent = 'Working…'; return; }
    if (f.id === 'career-form') { const r = await api('/career', {method: 'PUT', body: {markdown: values.markdown}}); toast(`Saved. ${r.summary.roles.length} roles, ${r.summary.summaries} summary variants. Packages are being re-tailored.`); activeRun = 'pending'; await loadCareer(); }
    if (f.id === 'chat-form') { f.reset(); await sendChat(values.message); }
    if (f.id === 'source-form') { await api('/sources', {method: 'POST', body: values}); await refresh(); toast('Board verified and connected.'); }
    if (f.id === 'seed-form') { const r = await api('/sources/seed', {method: 'POST', body: {categories: Object.keys(values)}}); await refresh(); toast(`${r.added} boards added (${r.total} total). Click Collect when ready.`); }
    if (f.id === 'profile-form') { const saved = await api('/profile', {method: 'POST', body: {...values, confirmed: values.confirmed === 'on', ...(pendingUpload || {})}}); pendingUpload = null; await refresh(); toast(saved.docx ? `Saved. Tailored resumes will now be edits of ${saved.docx_name} (${saved.docx_coverage}% of lines matched).` : 'Resume saved. Upload the .docx version to keep your exact Word formatting.'); }
    if (f.id === 'skill-form') { await api('/skills', {method: 'POST', body: {skill: values.skill, note: values.note, mode}}); await refresh(); toast(`${values.skill} confirmed.`); }
    if (f.id === 'settings-form') {
      await api('/settings', {method: 'POST', body: {hourly_min: Number(values.hourly_min), provider: values.provider, match_min: Number(values.match_min), review_min: Number(values.review_min),
        schedule: {enabled: values.enabled === 'on', interval_minutes: Number(values.interval_minutes) || 60, auto_prepare: values.auto_prepare === 'on', weekly_day: values.weekly_day, weekly_time: values.weekly_time},
        notifications: values.notifications === 'on', backup: values.backup === 'on',
        agent_names: Object.fromEntries(Object.entries(values).filter(([k]) => k.startsWith('name:')).map(([k, v]) => [k.slice(5), v]))}});
      await refresh(); toast('Preferences saved. Packages prepared under old rules must be re-tailored before approval.');
    }
    if (f.id === 'company-form') { await api('/jobs/' + encodeURIComponent(f.dataset.id) + '/company', {method: 'POST', body: {...values, confirmed: values.confirmed === 'on'}}); await refresh(false); await showJob(f.dataset.id); toast('Company evidence recorded.'); }
    if (f.id === 'resolution-form') { await api('/jobs/' + encodeURIComponent(f.dataset.id) + '/resolve', {method: 'POST', body: {...values, key: f.dataset.key}}); await refresh(false); await showJob(f.dataset.id); toast('Evidence review recorded.'); }
    if (f.id === 'contact-form') { await api('/jobs/' + encodeURIComponent(f.dataset.id) + '/contacts', {method: 'POST', body: values}); await refresh(false); const item = data.inbox.find(x => x.job_id === f.dataset.id && x.kind === 'package_ready'); const p = data.packages.find(p => p.job_id === f.dataset.id); if (item) openDialog(dialogHead(item.title, item.company.toUpperCase()) + inboxCard(item)); else if (p) showPackage(p.id, 'outreach'); else await showJob(f.dataset.id); toast('Contact saved with its source.'); }
    if (f.id === 'resume-edit') {
      const bullets = {}; Object.entries(values).forEach(([k, v]) => { if (k.startsWith('b:')) bullets[k.slice(2)] = v; });
      const p = await api(`/packages/${f.dataset.id}/resume`, {method: 'PUT', body: {summary: values.summary, bullets}});
      await refresh(false); showPackage(p.id, 'resume'); toast(`Saved. ATS keywords now ${p.scores.ats_tailored}%.`);
    }
    if (f.id === 'outreach-form') { await api(`/packages/${f.dataset.id}/outreach`, {method: 'POST', body: values}); await refresh(false); showPackage(f.dataset.id, 'outreach'); toast('Outreach logged.'); }
    if (f.classList.contains('outcome-form')) {
      await api('/jobs/' + encodeURIComponent(f.dataset.id) + '/outcome', {method: 'POST', body: {stage: values.stage, note: values.note || '', follow_up: values.follow_up || null}});
      await refresh(); toast('Tracker updated.');
    }
  } catch (err) { toast(err.message, true); } finally { if (b) b.disabled = false; }
});
window.addEventListener('hashchange', () => { view = location.hash.slice(1) || 'office'; selected.clear(); if ($('#detail').open) $('#detail').close(); render(); });
$('#detail').addEventListener('click', e => { if (e.target === $('#detail')) $('#detail').close(); });
if (mode === 'demo') jobFilter.fresh = 'all';
await refresh().catch(e => { $('#main').innerHTML = empty('Could not open the workspace', esc(e.message), '<button onclick="location.reload()">Reload</button>'); });
const latest = await api('/events?tail=300').catch(() => []);
lastSeq = latest.at(-1)?.seq || 0;
const known = new Set(data?.runs.map(r => r.id) || []);
events = latest.filter(e => known.has(e.run_id)).slice(-200);
if (view === 'office') { renderActivity(); events.slice(-12).forEach(applyEvent); }
setInterval(poll, 2000);
