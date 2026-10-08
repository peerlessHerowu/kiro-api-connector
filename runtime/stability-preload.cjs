// Main bridge preload; installed kRouter bundles and Kiro client stay untouched.
const Module = require('node:module');
const compile = Module.prototype._compile;
const patches = [
  ['3120.js', 'function j(a){return!function(a)',
   'function j(a){if(new RegExp("(?:^|/)gpt-6(?:[.-]|$)").test(a.model||""))return a;return!function(a)'],
  ['3120.js', 'H=this.config?.timeoutMs||d.pH,',
   'H=this.config?.timeoutMs||120000,'],
  ['4315.js', '502:{attempts:3,delayMs:3e3},503:{attempts:3,delayMs:2e3},504:{attempts:2,delayMs:3e3}',
   '502:{attempts:1,delayMs:3e3},503:{attempts:1,delayMs:2e3},504:{attempts:1,delayMs:3e3}'],
];
function patch(source, filename) {
  for (const [file, before, after] of patches) {
    if (!filename.replaceAll('\\', '/').endsWith('/chunks/' + file)) continue;
    if (source.split(before).length !== 2) throw new Error('Stability patch layout mismatch: ' + file);
    source = source.replace(before, after);
  }
  return source;
}
Module.prototype._compile = function(source, filename) {
  return compile.call(this, patch(source, filename), filename);
};
require('./upstream_headers.cjs');
const fetchUpstream = globalThis.fetch;
let sequence = 0;
globalThis.fetch = async function(input, init) {
  const url = new URL(typeof input === 'string' || input instanceof URL ? input : input.url);
  if (url.origin !== 'https://new-api.iohubonline.club' || url.pathname !== '/v1/chat/completions') {
    return fetchUpstream.call(this, input, init);
  }
  let body;
  try { body = JSON.parse(init?.body); } catch { return fetchUpstream.call(this, input, init); }
  const id = ++sequence;
  for (let attempt = 1; attempt <= 2; attempt++) {
    const started = Date.now();
    try {
      const response = await fetchUpstream.call(this, input, init);
      console.log('upstream_diagnostic=' + JSON.stringify({id,attempt,model:body.model,
        messages:body.messages?.length,lastRole:body.messages?.at(-1)?.role,
        effort:body.reasoning_effort || null,status:response.status,headersMs:Date.now()-started,
        requestId:response.headers.get('x-request-id')}));
      // Only an explicit rejection before any stream output can be retried here.
      if (attempt === 1 && body.model === 'gpt-6.1-sol' && response.status === 400) {
        let error;
        try { error = await response.clone().json(); } catch {}
        if (error?.error?.message === "The 'gpt-6.1-sol' model is not supported when using Codex with a ChatGPT account." && !init?.signal?.aborted) {
          await response.body?.cancel();
          console.log('upstream_retry=known_account_model_rejection_before_stream');
          continue;
        }
      }
      return response;
    } catch (error) {
      console.log('upstream_diagnostic=' + JSON.stringify({id,attempt,model:body.model,
        errorType:error.name,elapsedMs:Date.now()-started}));
      throw error;
    }
  }
};
module.exports = {patch};
