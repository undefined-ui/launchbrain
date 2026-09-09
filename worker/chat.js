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

// cheap in-isolate cache of ranked chat models; heals via retry below
let MODELS = null;

async function candidates(env) {
  if (env.MODEL) return [env.MODEL];
  if (!MODELS) {
    try {
      const r = await fetch('https://api.groq.com/openai/v1/models', {
        headers: { authorization: 'Bearer ' + env.GROQ_API_KEY },
      });
      if (r.ok) {
        // the catalogue mixes in classifiers and speech models; only chat
        // models may answer. plain instruct first: reasoning models burn
        // the token cap on thinking.
        const ids = (((await r.json()) || {}).data || []).map(m => m.id)
          .filter(i => !/guard|whisper|tts|embed|moderat|safety|compound|allam|orpheus|canopylabs|audio/i.test(i));
        const prefs = ['llama-4-scout', 'llama-3.3-70b', 'llama-3.1-8b',
                       'instant', 'versatile', 'qwen', 'kimi', 'llama-4',
                       'gpt-oss-20b', 'llama'];
        const ranked = [];
        for (const p of prefs)
          for (const i of ids)
            if (i.toLowerCase().includes(p) && !ranked.includes(i)) ranked.push(i);
        for (const i of ids) if (!ranked.includes(i)) ranked.push(i);
        if (ranked.length) MODELS = ranked;
      }
    } catch {}
    if (!MODELS) MODELS = ['llama-3.3-70b-versatile'];
  }
  return MODELS.slice(0, 5);
}

async function complete(env, messages, model) {
  // every current groq chat model reasons; keep the thinking short so the
  // answer fits the cap (some models cap max_tokens at 512)
  const extra = /qwen/i.test(model) ? { reasoning_effort: 'none' }
    : /gpt-oss/i.test(model) ? { reasoning_effort: 'low' } : {};
  return fetch('https://api.groq.com/openai/v1/chat/completions', {
    method: 'POST',
    headers: {
      'content-type': 'application/json',
      authorization: 'Bearer ' + env.GROQ_API_KEY,
    },
    body: JSON.stringify({ model, messages, max_tokens: 450, temperature: 0.3, ...extra }),
  });
}

// a reasoning model may leak <think> blocks into content; strip them, and an
// answer that is nothing but thought counts as no answer
function cleanContent(raw) {
  return String(raw || '')
    .replace(/<think>[\s\S]*?<\/think>/g, '')
    .replace(/^[\s\S]*?<\/think>/, '')
    .replace(/<think>[\s\S]*$/, '')
    .trim();
}

export default {
  // Cloudflare's punctual cron stands in for GitHub's jittery one: each
  // firing dispatches the update-data workflow. No token, no action.
  async scheduled(event, env, ctx) {
    if (!env.GITHUB_TOKEN) return;
    ctx.waitUntil(fetch(
      'https://api.github.com/repos/undefined-ui/launchbrain/actions/workflows/update.yml/dispatches',
      {
        method: 'POST',
        headers: {
          'authorization': 'Bearer ' + env.GITHUB_TOKEN,
          'accept': 'application/vnd.github+json',
          'user-agent': 'launchbrain-cron',
          'content-type': 'application/json',
        },
        body: JSON.stringify({ ref: 'main' }),
      },
    ));
  },

  async fetch(req, env) {
    if (req.method === 'OPTIONS') return new Response(null, { headers: CORS });
    if (req.method === 'GET')       // which models the relay would try, in order
      return json({ candidates: env.GROQ_API_KEY ? await candidates(env) : [] }, 200);
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

    let lastText = '', lastStatus = 502;
    for (const m of await candidates(env)) {
      const r = await complete(env, messages, m);
      lastText = await r.text(); lastStatus = r.status;
      if (r.status === 404 || r.status === 400) {  // rotated away, capped, or
        if (!env.MODEL) MODELS = null;             // rejected a param: next
        continue;
      }
      if (!r.ok) break;              // 429/5xx: report honestly, no retry spray
      try {
        const d = JSON.parse(lastText);
        const msg = ((d.choices || [])[0] || {}).message;
        if (msg) {
          msg.content = cleanContent(msg.content);
          delete msg.reasoning;
          if (!msg.content) continue;       // thought itself to death
          return json(d, 200);
        }
      } catch {}
      break;
    }
    return new Response(lastText || JSON.stringify({ error: 'no model answered' }), {
      status: lastStatus,
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
