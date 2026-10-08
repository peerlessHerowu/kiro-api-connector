// This new-api deployment routes gpt-6.1-sol successfully with an X-Codex-* header.
// Keep the selected model/key and scope the workaround to the verified endpoint.
const originalFetch = globalThis.fetch;
globalThis.fetch = function (input, init) {
  const url = new URL(typeof input === 'string' || input instanceof URL ? input : input.url);
  if (url.origin === 'https://new-api.iohubonline.club' &&
      url.pathname === '/v1/chat/completions' && typeof init?.body === 'string') {
    let body;
    try { body = JSON.parse(init.body); } catch {}
    if (body?.reasoning_effort) console.log(`upstream_reasoning=${body.model}:${body.reasoning_effort}`);
    if (body?.model === 'gpt-6.1-sol') {
      const headers = new Headers(init.headers || (input instanceof Request ? input.headers : undefined));
      headers.set('X-Codex-Client', 'kiro-local-bridge');
      init = { ...init, headers };
      console.log('upstream_header_adapter=gpt-6.1-sol');
    }
  }
  return originalFetch.call(this, input, init);
};
