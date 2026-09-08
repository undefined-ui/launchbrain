// LaunchBrain chat relay — a Cloudflare Worker.
//
// The terminal is a static page, so a chat that works for every visitor with
// zero setup needs one server-side secret somewhere. This is that somewhere:
// the Groq API key lives in the worker's environment, never in the page.
//
// Deploy (dashboard, no CLI):
//   1. dash.cloudflare.com -> Workers & Pages -> Create -> Worker
//   2. paste this file, Deploy
//   3. Settings -> Variables and Secrets -> add secret GROQ_API_KEY
//      (a free key from console.groq.com/keys)
//   4. put the worker URL into AI_RELAY_URL in index.html
//
// Groq rotates its model catalogue, so the worker asks /models what exists
// and picks the best match from a preference list; set a MODEL variable to
// pin one explicitly. Groq's own 429s pass through to the page, which
// already shows an honest provider error.

const CORS = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Methods': 'POST, OPTIONS',
  'Access-Control-Allow-Headers': 'content-type',
};

// cheap in-isolate cache; a stale entry heals via the 404 retry below
let MODEL_CACHE = null;

async function pickModel(env) {
  if (env.MODEL) return env.MODEL;
  if (MODEL_CACHE) return MODEL_CACHE;
  try {
    const r = await fetch('https://api.groq.com/openai/v1/models', {
      headers: { authorization: 'Bearer ' + env.GROQ_API_KEY },
    });
    if (r.ok) {
      const ids = (((await r.json()) || {}).data || []).map(m => m.id);
      // plain instruct models first: reasoning models (gpt-oss) burn the
      // token cap on thinking and can return an empty content field
      for (const p of ['llama-4-scout', 'llama-3.3-70b', 'instant',
                       'llama', 'qwen', 'gpt-oss-20b']) {
        const hit = ids.find(i => i.toLowerCase().includes(p));
        if (hit) { MODEL_CACHE = hit; return hit; }
      }
      if (ids.length) { MODEL_CACHE = ids[0]; return MODEL_CACHE; }
    }
  } catch {}
  return 'llama-3.3-70b-versatile';
}

async function complete(env, messages, model) {
  return fetch('https://api.groq.com/openai/v1/chat/completions', {
    method: 'POST',
    headers: {
      'content-type': 'application/json',
      authorization: 'Bearer ' + env.GROQ_API_KEY,
    },
    body: JSON.stringify({ model, messages, max_tokens: 600, temperature: 0.3 }),
  });
}

export default {
  async fetch(req, env) {
    if (req.method === 'OPTIONS') return new Response(null, { headers: CORS });
    if (req.method !== 'POST')
      return json({ error: 'POST only' }, 405);
    if (!env.GROQ_API_KEY)
      return json({ error: 'relay is not configured: GROQ_API_KEY secret is missing' }, 500);

    let body;
    try { body = await req.json(); } catch { return json({ error: 'bad json' }, 400); }

    // accept only what the chat actually sends; everything is clamped
    const messages = (Array.isArray(body.messages) ? body.messages : [])
      .slice(-12)
      .map(m => ({
        role: ['system', 'user', 'assistant'].includes(m && m.role) ? m.role : 'user',
        content: String((m && m.content) || '').slice(0, 8000),
      }))
      .filter(m => m.content);
    if (!messages.length) return json({ error: 'no messages' }, 400);

    let r = await complete(env, messages, await pickModel(env));
    if (r.status === 404 && !env.MODEL) {   // model rotated away; re-discover
      MODEL_CACHE = null;
      r = await complete(env, messages, await pickModel(env));
    }
    const text = await r.text();
    return new Response(text, {
      status: r.status,
      headers: { ...CORS, 'content-type': 'application/json' },
    });
  },
};

function json(obj, status) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: { ...CORS, 'content-type': 'application/json' },
  });
}
