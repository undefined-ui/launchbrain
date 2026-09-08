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
// The model is llama-3.1-8b-instant: on Groq's free tier it allows 14,400
// requests a day, which is plenty for short risk reads. Override with a
// MODEL variable if needed. Groq's own 429s pass through to the page, which
// already shows an honest provider error.

const CORS = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Methods': 'POST, OPTIONS',
  'Access-Control-Allow-Headers': 'content-type',
};

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

    const r = await fetch('https://api.groq.com/openai/v1/chat/completions', {
      method: 'POST',
      headers: {
        'content-type': 'application/json',
        authorization: 'Bearer ' + env.GROQ_API_KEY,
      },
      body: JSON.stringify({
        model: env.MODEL || 'llama-3.1-8b-instant',
        messages,
        max_tokens: 300,
        temperature: 0.3,
      }),
    });
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
