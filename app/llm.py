"""Model providers for JSON-producing workers. Keys stay server-side (in .env).

Every call is single-shot structured JSON; deterministic validators downstream
decide what is kept. Usage is logged as an event so spend stays visible.

Providers: gemini (free tier, default when GEMINI_API_KEY is set), nvidia (free),
groq (free), claude (paid). Before text leaves the machine for a third-party model,
your name, email, phone and profile links are replaced with placeholders and put
back afterwards.
"""
import asyncio
import json
import os
import re
from pathlib import Path

import httpx

from . import store

ROOT = Path(__file__).resolve().parents[1]
GEMINI_MODELS = ['gemini-3.8-flash', 'gemini-3.7-flash', 'gemini-3.5-flash', 'gemini-flash-latest', 'gemini-3.1-flash-lite']
NVIDIA_MODEL = 'meta/llama-3.3-70b-instruct'


def load_env():
    """Minimal .env reader so keys can live in an ignored file next to the app."""
    path = ROOT / '.env'
    if not path.exists():
        return
    for line in path.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        if value.strip() and not os.environ.get(key.strip()):
            os.environ[key.strip()] = value.strip().strip('"').strip("'")


def available():
    return {'claude': bool(os.getenv('ANTHROPIC_API_KEY')), 'groq': bool(os.getenv('GROQ_API_KEY')),
            'gemini': bool(os.getenv('GEMINI_API_KEY')), 'nvidia': bool(os.getenv('NVIDIA_API_KEY'))}


def claude_model():
    return os.getenv('ANTHROPIC_MODEL', 'claude-opus-5-5')


def gemini_models():
    preferred = os.getenv('GEMINI_MODEL')
    return ([preferred] if preferred else []) + [m for m in GEMINI_MODELS if m != preferred]


def label(provider):
    return {'gemini': f'Gemini ({gemini_models()[0]}, free tier)', 'nvidia': f'NVIDIA ({NVIDIA_MODEL})',
            'groq': 'Groq', 'claude': f'Claude ({claude_model()})'}.get(provider, provider)


# ---------------------------------------------------------------- privacy
def _identity():
    """Strings that identify you, taken from your saved resume header."""
    profile = store.get('profiles', 'real') or {}
    header = profile.get('header') or {}
    values = {'name': header.get('name') or profile.get('name') or ''}
    joined = ' '.join(header.get('lines') or [])
    values['email'] = header.get('email') or (re.search(r'[\w.+-]+@[\w-]+\.[\w.-]+', joined) or [''])[0]
    values['phone'] = header.get('phone') or ''
    links = re.findall(r'(?:https?://)?(?:www\.)?(?:linkedin\.com/in/[\w-]+|github\.com/[\w-]+|[\w-]+\.framer\.website)', joined, re.I)
    return values, links


def redact(text):
    """Replace your contact details with placeholders. Returns (text, restore_map)."""
    values, links = _identity()
    restore = {}
    if values['name']:
        parts = [p for p in re.split(r'\s+', values['name'].strip()) if len(p) > 1]
        text = re.sub(re.escape(values['name']), '[MY_NAME]', text, flags=re.I)
        restore['[MY_NAME]'] = values['name'].title() if values['name'].isupper() else values['name']
        if parts:
            first = parts[0].title() if parts[0].isupper() else parts[0]
            text = re.sub(r'\b' + re.escape(parts[0]) + r'\b', '[MY_FIRST_NAME]', text, flags=re.I)
            restore['[MY_FIRST_NAME]'] = first
    for kind in ('email', 'phone'):
        if values[kind]:
            text = text.replace(values[kind], f'[MY_{kind.upper()}]')
            restore[f'[MY_{kind.upper()}]'] = values[kind]
    for i, link in enumerate(links):
        text = text.replace(link, f'[MY_LINK_{i}]')
        restore[f'[MY_LINK_{i}]'] = link
    return text, restore


def unredact(value, restore):
    if isinstance(value, str):
        for k, v in restore.items():
            value = value.replace(k, v)
        return value
    if isinstance(value, list):
        return [unredact(v, restore) for v in value]
    if isinstance(value, dict):
        return {k: unredact(v, restore) for k, v in value.items()}
    return value


# ---------------------------------------------------------------- entry point
async def complete_json(provider, system, prompt, schema, run_id=None, actor='writer'):
    if provider == 'claude':
        return await _claude(system, prompt, schema, run_id, actor)
    if provider not in ('gemini', 'nvidia', 'groq'):
        raise ValueError(f'Unknown model provider {provider}')
    system_r, r1 = redact(system)
    prompt_r, r2 = redact(prompt)
    restore = {**r1, **r2}
    order = [provider] + [p for p in ('gemini', 'nvidia', 'groq') if p != provider and available()[p]]
    last = None
    for p in order:  # free providers back each other up when one is busy or out of quota
        try:
            fn = {'gemini': _gemini, 'nvidia': _nvidia, 'groq': _groq}[p]
            return unredact(await fn(system_r, prompt_r, schema, run_id, actor), restore)
        except ValueError as exc:
            last = exc
    raise last or ValueError('No model provider available')


def _gemini_schema(schema):
    """Gemini's JSON-schema mode accepts a subset; keep the structure, drop what it rejects."""
    if isinstance(schema, dict):
        return {k: _gemini_schema(v) for k, v in schema.items() if k not in ('$schema',)}
    if isinstance(schema, list):
        return [_gemini_schema(v) for v in schema]
    return schema


async def _gemini(system, prompt, schema, run_id, actor):
    key = os.getenv('GEMINI_API_KEY')
    if not key:
        raise ValueError('GEMINI_API_KEY is not configured')
    body = {'systemInstruction': {'parts': [{'text': system}]},
            'contents': [{'role': 'user', 'parts': [{'text': prompt}]}],
            'generationConfig': {'responseMimeType': 'application/json', 'responseJsonSchema': _gemini_schema(schema),
                                 'temperature': 0.4, 'maxOutputTokens': 8192}}
    problems = []
    async with httpx.AsyncClient(timeout=120, headers={'x-goog-api-key': key}) as c:
        for model in gemini_models():
            for attempt in range(2):
                try:
                    r = await c.post(f'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent', json=body)
                except httpx.HTTPError as exc:
                    problems.append(f'{model}: {type(exc).__name__}')
                    break
                if r.status_code in (429, 500, 503):  # busy or out of free quota: retry once, then the next model
                    problems.append(f'{model}: HTTP {r.status_code}')
                    if attempt == 0 and r.status_code != 429:
                        await asyncio.sleep(2)
                        continue
                    break
                if r.status_code in (401, 403):
                    raise ValueError('GEMINI_API_KEY was rejected')
                if not r.is_success:
                    problems.append(f'{model}: HTTP {r.status_code}')
                    break
                data = r.json()
                cand = (data.get('candidates') or [{}])[0]
                text = ''.join(p.get('text', '') for p in (cand.get('content') or {}).get('parts', []) if not p.get('thought'))
                if run_id:
                    u = data.get('usageMetadata', {})
                    store.event(run_id, actor, 'usage', f'{model} responded (free tier)', model=model,
                                usage={'input_tokens': u.get('promptTokenCount'), 'output_tokens': u.get('candidatesTokenCount')})
                try:
                    return json.loads(text)
                except ValueError:
                    problems.append(f'{model}: invalid JSON ({cand.get("finishReason")})')
                    break
    raise ValueError('Gemini unavailable: ' + '; '.join(problems[-4:]))


async def _openai_compatible(url, key, model, system, prompt, schema, run_id, actor, name):
    async with httpx.AsyncClient(timeout=120) as c:
        r = await c.post(url, headers={'Authorization': f'Bearer {key}'},
                         json={'model': model, 'temperature': 0.3, 'max_tokens': 6000, 'response_format': {'type': 'json_object'},
                               'messages': [{'role': 'system', 'content': system + '\nReturn only JSON matching: ' + json.dumps(schema)},
                                            {'role': 'user', 'content': prompt}]})
    if not r.is_success:
        raise ValueError(f'{name} request failed (HTTP {r.status_code})')
    body = r.json()
    if run_id:
        store.event(run_id, actor, 'usage', f'{body.get("model") or model} responded', model=body.get('model') or model, usage=body.get('usage', {}))
    raw = (body['choices'][0]['message'].get('content') or '').strip()
    if raw.startswith('```'):
        raw = raw.split('\n', 1)[-1].rsplit('```', 1)[0]
    return json.loads(raw)


async def _nvidia(system, prompt, schema, run_id, actor):
    key = os.getenv('NVIDIA_API_KEY')
    if not key:
        raise ValueError('NVIDIA_API_KEY is not configured')
    return await _openai_compatible('https://integrate.api.nvidia.com/v1/chat/completions', key, os.getenv('NVIDIA_MODEL', NVIDIA_MODEL),
                                    system, prompt, schema, run_id, actor, 'NVIDIA')


async def _groq(system, prompt, schema, run_id, actor):
    key = os.getenv('GROQ_API_KEY')
    if not key:
        raise ValueError('GROQ_API_KEY is not configured')
    return await _openai_compatible('https://api.groq.com/openai/v1/chat/completions', key, os.getenv('GROQ_MODEL', 'openai/gpt-oss-120b'),
                                    system, prompt, schema, run_id, actor, 'Groq')


async def _claude(system, prompt, schema, run_id, actor):
    import anthropic
    client = anthropic.AsyncAnthropic(max_retries=2, timeout=180)
    try:
        response = await client.beta.messages.create(
            model=claude_model(),
            max_tokens=16000,
            system=system,
            messages=[{'role': 'user', 'content': prompt}],
            output_config={'effort': os.getenv('ANTHROPIC_EFFORT', 'medium'),
                           'format': {'type': 'json_schema', 'schema': schema}},
            betas=['server-side-fallback-2026-07-01'],
            fallbacks='default',
        )
    except anthropic.AuthenticationError:
        raise ValueError('ANTHROPIC_API_KEY was rejected')
    except anthropic.RateLimitError:
        raise ValueError('Claude rate limit reached; try the batch again later')
    except anthropic.APIStatusError as exc:
        raise ValueError(f'Claude request failed (HTTP {exc.status_code})')
    except anthropic.APIConnectionError:
        raise ValueError('Could not reach the Claude API')
    usage = response.usage
    if run_id:
        store.event(run_id, actor, 'usage', f'{response.model} responded', model=response.model,
                    usage={'input_tokens': usage.input_tokens, 'output_tokens': usage.output_tokens})
    if response.stop_reason == 'refusal':
        raise ValueError('Claude declined this request; the extractive draft was kept')
    if response.stop_reason == 'max_tokens':
        raise ValueError('Claude output was truncated; the extractive draft was kept')
    text = next((b.text for b in response.content if b.type == 'text'), '')
    return json.loads(text)
