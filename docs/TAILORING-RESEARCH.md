# Resume Tailoring Research (2024–2026) → Engine Rules

Scope: early-career (3–4 yrs) AI/GenAI/LLM engineer, US roles. Engine edits the candidate's own .docx in place, keeps section order/layout, **never fabricates** experience, metrics, employers, titles or dates.

## Key findings

1. **Humans decide; ATS ranks and searches.** Greenhouse does not auto-reject; only knockout questions do. Its Talent Matching is "assistive AI" that scores and orders applicants using a semantic skill-coverage base plus exact-skill, job-title, level, education and certification components ([Greenhouse Support – Talent Matching](https://support.greenhouse.io/hc/en-us/articles/41396009937307-Talent-Matching); [Jobscan – Greenhouse ATS](https://www.jobscan.co/blog/greenhouse-ats-what-job-seekers-need-to-know/)). In Enhancv's late-2025 survey, 23/25 recruiters said their ATS does not auto-reject; 76% valued natural keyword use ([TechRSeries – 92% of recruiters](https://techrseries.com/recruitment-and-on-boarding/the-75-ats-rejection-myth-shattered-92-of-recruiters-confirm-applicant-tracking-systems-do-not-auto-reject-resumes/)).
2. **Recruiters filter by skills and titles.** 99.7% of 384 surveyed recruiters use ATS filters; 76.4% filter by skills, 55.3% by job title. Resumes containing the target job title got 10.6x more interviews ([Jobscan – State of the Job Search 2025](https://www.jobscan.co/state-of-the-job-search); [Jobscan – ATS Resume](https://www.jobscan.co/blog/ats-resume/)).
3. **The first pass is a ~7.4-second skim** of titles, top-of-page overview, and bullets. Winners had clear headers, bolded titles and concise declarative bullets. Losers had long sentences and keywords stuffed in without context ([Ladders Eye-Tracking Study 2018, PR Newswire](https://www.prnewswire.com/news-releases/ladders-updates-popular-recruiter-eye-tracking-study-with-new-key-insights-on-how-job-seekers-can-improve-their-resumes-300744217.html)). Caveat: only 30 recruiters were studied.
4. **Use the XYZ bullet formula:** "Accomplished X as measured by Y by doing Z" (Laszlo Bock, Google). The order can vary; the point is that all three parts appear ([Resume.io – XYZ format](https://resume.io/blog/xyz-resume-format)). MIT recommends the equivalent PAR (Project–Action–Result) ([MIT CAPD – Crafting an effective resume](https://capd.mit.edu/resources/career-toolkit-crafting-an-effective-resume/)).
5. **Style basics:** past tense for previous roles, no personal pronouns, strong action verbs, quantify, tailor to the target role ([Harvard FAS – Create a strong resume](https://careerservices.fas.harvard.edu/resources/create-a-strong-resume/)). Avoid "responsible for" ([CNBC – Avoid these resume words](https://www.cnbc.com/2022/08/31/avoid-these-resume-words-at-all-costs-says-career-coach-here-are-power-verbs-to-use-instead.html)).
6. **Parsing:** tables, multi-column layouts, text boxes and header/footer text are frequently dropped or scrambled (Workday especially). Single-column, standard headings and .docx are safest ([Jobscan – ATS Resume](https://www.jobscan.co/blog/ats-resume/); [ResumeAdapter – How Workday parses](https://www.resumeadapter.com/ats/workday/how-it-parses)).
7. **Acronyms:** write both the full term and the acronym once. Some parsers and recruiter searches are literal, and recruiters may search either form ([Scale.jobs – Matching keywords](https://scale.jobs/blog/how-to-match-resume-keywords-to-job-descriptions); [SCU Career Center – Jobscan formatting mistakes](https://www.scu.edu/careercenter/toolkit/job-scan-common-ats-resume-formatting-mistakes/)).
8. **Generic AI-written text gets penalized.** 62% of employers reject AI-generated resumes that lack personalization ([Resume Now – AI Applicant Report](https://www.resume-now.com/job-resources/careers/ai-applicant-report)). Telltale words include "delve", "realm", "intricate", "showcasing" and "pivotal" ([ResumeGeni – How employers detect AI resumes](https://resumegeni.com/blog/how-employers-detect-ai-generated-resumes-2026)).
9. **AI-engineer screens in 2025–26 look for production evidence first:** shipped systems, RAG (vector DB, chunking, reranking), evals, and latency/cost/accuracy metrics, with the stack named *after* the result. "Built a RAG chatbot with LangChain" reads as junior ([MirrorCV – AI/ML engineer guide](https://mirrorcv.com/resume-guide/ai-ml-engineer); [Interview Query – AI engineer resume](https://www.interviewquery.com/p/ai-engineer-resume)). Hamel Husain argues weak eval systems are the most common reason LLM products fail, so concrete eval work stands out ([Hamel Husain – Your AI Product Needs Evals](https://hamel.dev/blog/posts/evals/index.html)).

### Myths vs evidence

| Claim | Verdict | Evidence |
|---|---|---|
| "75% of resumes are auto-rejected by ATS" | **Myth** | The figure traces to 2012 marketing from Preptel, a company that closed in 2013, with no published method ([Uncharted Career](https://unchartedcareer.com/blog/the-75-of-resumes-are-auto-rejected-myth-traced-to-its-source)); 92% of surveyed recruiters report no auto-reject |
| Higher keyword density = higher rank | **Myth** | Matching is skill coverage, not repetition. Stuffing hurt the human skim in the Ladders study, and Jobscan warns against it |
| ATS only does exact string matching | **Partly myth** | Greenhouse and Workday use semantic/NLP matching, but exact-skill and job-title matches are still separate scored components, and recruiter Boolean searches are literal |
| Spell out acronyms | **Evidence-backed (cheap insurance)** | Recruiters search both forms, and some parsers are literal |
| Tables, columns and headers break parsing | **Evidence-backed** | Workday drops header/footer text and garbles columns (vendor-observed, Jobscan) |
| Job title on the resume matters | **Evidence-backed** | 55.3% of recruiters filter by title; 10.6x interview lift (Jobscan, correlational) |
| Recruiters read every word | **Myth** | ~7.4 s first pass (Ladders) |

## Rules for the engine

**Truthfulness (hard gates)**
1. [DETERMINISTIC] Every number, %, $, date, employer, title, degree and tool name in the output must already exist in the master resume or fact bank. Diff the numeric tokens and named entities and reject any new ones. *Rationale: no fabrication; over-claiming fails the interview loop.*
2. [DETERMINISTIC] Employer names, job titles, dates and section order must be byte-identical to the source document. *Rationale: layout and facts are invariant; background checks verify titles and dates.*
3. [PROMPT] Only surface a JD skill if the master resume shows the candidate used it. If a JD must-have is missing, report it as a gap and do not insert it. *Rationale: Jobscan warns that stuffing "misrepresents your abilities."*

**Keywords**
4. [DETERMINISTIC] Pull the JD's must-have hard skills (the requirements section plus terms repeated 2+ times). Each one the candidate truly has must appear in the summary or at least one bullet, not only in Skills. *Rationale: recruiters filter on skills (76.4%), and context-free lists perform poorly (Jobscan, Ladders).*
5. [DETERMINISTIC] Use the JD's exact spelling and casing for tools and skills (e.g., "PyTorch", "LangGraph", "retrieval-augmented generation"). Normalize synonyms to the JD's form. *Rationale: exact-skill match is a scored component (Greenhouse).*
6. [DETERMINISTIC] On first use, write the full term followed by the acronym in parentheses, e.g., "retrieval-augmented generation (RAG)". After that, use the acronym only. *Rationale: recruiters search both forms.*
7. [DETERMINISTIC] No keyword may appear more than 3 times across the resume, counting the Skills section, with a maximum of 1 per bullet. *Rationale: anti-stuffing; coverage beats density.*
8. [DETERMINISTIC] Put the top 5 JD keywords in the summary or the most recent role's first 3 bullets. *Rationale: the F-pattern skim weights the top of the page and the first bullets (Ladders).*
9. [DETERMINISTIC] No bullet may be a bare comma list of 3+ tools with no verb or result. *Rationale: "keyword insertion rather than natural language" performed poorly (Ladders).*

**Summary / headline**
10. [DETERMINISTIC] The headline or first summary phrase uses the JD's role title (e.g., "AI Engineer") only when it truthfully describes the candidate's work. Never claim a title the candidate didn't hold as a past job title. *Rationale: 10.6x interview lift for a title match (Jobscan).*
11. [DETERMINISTIC] Summary length: 2–3 sentences, ≤ 60 words, no first-person pronouns (I, me, my, we, our). *Rationale: Harvard; 7.4-second skim.*
12. [PROMPT] Structure: [target title] + [years, accurately "3+" or "4"] + [2–3 strongest JD-matched capabilities, each tied to a shipped system or outcome] + [domain/fit hook]. Lead with production impact, not a tool list. *Rationale: hiring managers screen for production evidence first.*
13. [DETERMINISTIC] Ban tacked-on lists like "Hands-on with X, Y and Z" or "Proficient in…" in the summary; tools belong in Skills or in bullets. *Rationale: a buzzword list adds nothing to a skim.*
14. [DETERMINISTIC] Seniority guard: with fewer than 5 years of experience, ban "seasoned", "veteran", "expert", "thought leader", "Senior/Staff/Principal" (unless that title was held) and "decade". *Rationale: mismatched seniority wording reads as over-claiming.*

**Bullets**
15. [DETERMINISTIC] Each bullet is ≤ 220 characters (≈ 2 rendered lines), with a target of 120–200. *Rationale: Ladders found long sentences hurt; one idea per bullet.*
16. [DETERMINISTIC] The first word is an approved action verb (see list). Past roles use past tense. The current role uses present tense for ongoing work and past tense for completed work, consistently within a role. *Rationale: Harvard tense rule; mixed tense is a common defect.*
17. [DETERMINISTIC] No first-person pronouns and no weak phrases from the avoid list. *Rationale: Harvard, CNBC.*
18. [PROMPT] Use the XYZ shape: verb + what was built or changed + method/tool (JD term) + measurable result or honest scope (users, docs, requests/day, team size). Put the JD-relevant term in the first 8 words. *Rationale: Google XYZ / MIT PAR.*
19. [DETERMINISTIC] At least 60% of bullets in the two most recent roles contain a number already in the fact bank. If none exists, use scope words, never an invented metric. *Rationale: quantify truthfully (Harvard).*
20. [DETERMINISTIC] One idea per bullet: no more than one "and"-joined second accomplishment and no semicolons. *Rationale: a 7.4-second skim.*
21. [DETERMINISTIC] No two bullets in the same role start with the same verb, and no verb is used more than twice on the resume. *Rationale: variety signals range; repetition reads as templated.*
22. [DETERMINISTIC] Ban AI-tell words: delve, realm, intricate, showcasing, pivotal, leverage(d), synergy, cutting-edge, passionate, robust (unless the JD uses it). *Rationale: generic AI text gets rejected (Resume Now, ResumeGeni).*

**Selection and ordering**
23. [DETERMINISTIC] Bullets per role by recency: most recent role 4–6, the previous role 3–4, older roles 2–3. Keep the document's existing page count. *Rationale: recency weighting; one page is the norm under 10 years (MIT).*
24. [DETERMINISTIC] Score each master bullet as (JD must-have hits × 3) + (nice-to-have hits × 1) + (has a metric × 2). Pick the top N per role and sort them by score within the role. *Rationale: the first bullets get the most attention (Ladders F-pattern).*
25. [PROMPT] Swap a project in only when it covers a JD must-have that no selected work bullet covers, and drop the lowest-scoring project. *Rationale: coverage over volume.*
26. [DETERMINISTIC] Every JD must-have the candidate has should be covered by the selected bullets plus the summary. Log the coverage percentage and any uncovered must-haves. *Rationale: a testable tailoring outcome.*

**Skills section**
27. [DETERMINISTIC] Keep the existing group headings (e.g., Languages, LLM/GenAI, Data/Vector stores, Cloud/MLOps). Within each group, reorder skills by JD relevance, JD-mentioned first. *Rationale: recruiters filter by skills, and the layout must stay unchanged.*
28. [DETERMINISTIC] Every skill listed must appear in at least one bullet or project, or in the fact bank. Drop JD-irrelevant skills before cutting relevant ones, with at most ~12 per group. *Rationale: only list what the candidate can defend.*
29. [DETERMINISTIC] Separate skills with commas or pipes inside the existing paragraphs. Never add tables, columns or text boxes. *Rationale: Workday parsing.*

**AI/LLM specifics**
30. [PROMPT] Prefer bullets that show production deployment (users, traffic, SLAs), evals (golden sets, LLM-as-judge, regression tests, accuracy/faithfulness), RAG internals (chunking, embeddings, hybrid search, reranking), agents/tool use, guardrails (PII, prompt injection, structured output) and latency, cost or token metrics. *Rationale: the 2025–26 hiring-manager focus (MirrorCV, Hamel Husain).*
31. [PROMPT] Phrase AI work as engineering outcomes ("cut p95 latency…", "raised retrieval hit-rate…") rather than model use ("used GPT-4 to…"). Name the stack after the result. *Rationale: "built a chatbot with LangChain" reads as junior.*
32. [PROMPT] For Forward Deployed and AI Solutions roles, put customer-facing delivery first (stakeholders, integrations, POC-to-production). For Python-backend roles, put APIs, throughput and reliability first. *Rationale: the role-title variant changes which bullets score highest.*
33. [DETERMINISTIC] Use model and vendor names (GPT-4o, Claude, Llama) only if they appear in the source; never upgrade a model version. *Rationale: a precise and verifiable claim.*

**Final QA**
34. [DETERMINISTIC] Run a final lint before saving: pronouns, the avoid list, AI-tell words, length, tense, keyword caps, new-number diff and unchanged layout/section order. Fail closed on any truthfulness violation. *Rationale: defects compound in a short skim.*

## Action verbs (~80)

- **Build:** Built, Developed, Engineered, Designed, Architected, Implemented, Prototyped, Programmed, Integrated, Containerized, Automated, Instrumented, Configured, Fine-tuned, Trained, Indexed, Embedded, Orchestrated, Authored, Created
- **Ship:** Shipped, Deployed, Launched, Released, Delivered, Productionized, Migrated, Rolled out, Scaled, Operationalized, Hardened, Integrated, Published, Onboarded, Piloted
- **Improve:** Reduced, Cut, Accelerated, Optimized, Improved, Increased, Raised, Streamlined, Refactored, Consolidated, Stabilized, Eliminated, Lowered, Boosted, Upgraded, Debugged, Resolved, Simplified
- **Analyze:** Evaluated, Benchmarked, Measured, Analyzed, Diagnosed, Profiled, Validated, Tested, Audited, Investigated, Quantified, Identified, Researched, Modeled, Forecasted
- **Lead:** Led, Owned, Drove, Spearheaded, Mentored, Coordinated, Partnered, Presented, Advised, Championed, Standardized, Defined, Directed, Facilitated

## Weak verbs and phrases to avoid (~30)

responsible for · worked on · helped with · helped to · assisted with · involved in · participated in · tasked with · duties included · utilized · leveraged · various · several · many · multiple (with no number) · etc. · and more · familiar with · exposure to · hands-on with · proficient in (in bullets) · handled · dealt with · was part of · contributed to (with no specifics) · successfully · effectively · seamlessly · cutting-edge · synergy · passionate about · results-driven · team player · detail-oriented · go-getter

## Example (AI Engineer JD: "RAG, evals, production LLM services, Python, AWS")

**Weak:** "Worked on a chatbot using LangChain, OpenAI, Pinecone, RAG, Python, AWS, and helped improve various responses."

This bullet has a weak verb, a bare tool list, the vague word "various", and no result or scope.

**Strong:** "Shipped a retrieval-augmented generation (RAG) support assistant on AWS for 1,200 internal users, raising answer accuracy from 71% to 88% on a 300-question eval set."

This bullet starts with a ship verb, puts JD terms in the first 8 words and expands the acronym once. It includes scope and an eval-backed metric, covers one idea and is about 165 characters long. *Every number must come from the candidate's fact bank; if none exists, write "for internal support teams" instead of inventing one.*
