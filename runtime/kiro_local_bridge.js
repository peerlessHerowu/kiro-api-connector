// Local smoke-test adapter for kRouter 0.5.163 and Kiro 1.0.395.
const http = require('node:http');
const fs = require('node:fs');
const Module = require('node:module');
const { DatabaseSync } = require('node:sqlite');
const legacyProfile = require('./legacy_profile.cjs');

const path = require('node:path');
const config = JSON.parse(fs.readFileSync(process.env.KIRO_CONNECTOR_CONFIG || path.join(__dirname, 'config.json'), 'utf8'));
const router = 'http://127.0.0.1:' + config.routerPort;
const defaultModel = config.defaultModel;
const efforts = ['low', 'medium', 'high', 'xhigh'];

function json(res, status, value) {
  res.writeHead(status, { 'Content-Type': 'application/json' });
  res.end(JSON.stringify(value));
}

let catalog;
async function getModels() {
  // The wizard saves the chosen catalog. Upstream credentials belong only to kRouter.
  catalog = config.models.map(id => ({id}))
    .map(item => ({ modelId: item.id, modelName: item.id, modelProvider: 'new-api',
      ...config.effortModels.includes(item.id) ? { additionalModelRequestFieldsSchema: {
        type: 'object', properties: { reasoning: { type: 'object', properties: {
          effort: { type: 'string', enum: efforts, default: 'high' },
        } } },
      } } : {},
    }));
  return catalog;
}

async function start() {
  const routerDb = new DatabaseSync(path.join(config.dataDir, 'db/data.sqlite'), { readOnly: true });
  const routerKey = routerDb.prepare('SELECT key FROM apiKeys WHERE id = ? AND isActive = 1 LIMIT 1')
    .get(config.routerKeyId)?.key;
  routerDb.close();
  if (!routerKey) throw new Error('Local router test key not found');
  process.env.ROUTER_API_KEY = routerKey;
  process.env.MITM_ROUTER_BASE = router;
  const authorized = await fetch(`${router}/v1/models`, {
    headers: { Authorization: `Bearer ${routerKey}` },
  });
  if (!authorized.ok) throw new Error(`Local router key rejected: ${authorized.status}`);

  // Reuse the installed protocol converter without starting its TLS/DNS listener.
  const filename = path.join(config.routerApp, 'src/mitm/server.js');
  let source = fs.readFileSync(filename, 'utf8');
  const finishStart = source.indexOf('function Pd(e){');
  const finishEnd = source.indexOf('async function Od(', finishStart);
  if (finishStart < 0 || finishEnd < 0 || !source.includes('let i=Pd(t);')) {
    throw new Error('Installed kRouter finish converter layout changed');
  }
  // Kiro 1.0.395 needs metadataEvent.stopReason to avoid retrying a complete reply.
  source = source.slice(0, finishStart) + `function Pd(e,reason){
    const frames=[];
    if(e.hasToolCalls)for(const key of Object.keys(e.toolCallInit).sort()){
      const tool=e.toolCallInit[key];
      frames.push(ot("toolUseEvent",{name:tool.name,stop:true,toolUseId:tool.id}));
    }
    const stopReason=e.hasToolCalls?"TOOL_USE":reason==="length"?"MAX_TOKENS":"END_TURN";
    frames.push(ot("metadataEvent",{stopReason,...e.usage?{tokenUsage:{
      uncachedInputTokens:e.usage.prompt_tokens||0,outputTokens:e.usage.completion_tokens||0
    }}:{}}));
    e.finishSent=true;e.toolCallInit={};return frames;
  }` + source.slice(finishEnd);
  source = source.replace('let i=Pd(t);', 'let i=Pd(t,r.finish_reason);')
    .replaceAll('ot("reasoningContentEvent",{content:', 'ot("reasoningContentEvent",{text:');
  const requestMarker = 'o={model:r,messages:s,stream:!0,';
  if (!source.includes(requestMarker)) throw new Error('Installed kRouter request converter layout changed');
  source = source.replace(requestMarker,
    'o={model:r,messages:s,stream:!0,...n.reasoning_effort?{reasoning_effort:n.reasoning_effort}:{},');
  const boundary = source.indexOf('var _u=require("https"),eh=require("http2")');
  if (boundary < 0) throw new Error('Installed kRouter bundle layout changed');
  const converter = new Module(filename, module);
  converter.filename = filename;
  converter.paths = Module._nodeModulePaths(require('node:path').dirname(filename));
  converter._compile(source.slice(0, boundary) + '\nmodule.exports=Zo();', filename);
  const kiro = converter.exports;

  const server = http.createServer(async (req, res) => {
    try {
      const url = new URL(req.url, `http://127.0.0.1:${config.bridgePort}`);
      const target = String(req.headers['x-amz-target'] || '');
      console.log(`${req.method} ${url.pathname} ${target}`);
      if (url.pathname === '/health') return json(res, 200, { ok: true });
      if (url.pathname === '/List-Available-Models' || target.includes('ListAvailableModels')) {
        req.resume();
        const models = await getModels();
        return json(res, 200, { models, ...(models.some(m => m.modelId === defaultModel)
          ? { defaultModel: { modelId: defaultModel } } : {}) });
      }
      const chunks = [];
      let size = 0;
      for await (const chunk of req) {
        size += chunk.length;
        if (size > 16 * 1024 * 1024) return json(res, 413, { message: 'Request too large' });
        chunks.push(chunk);
      }
      const body = Buffer.concat(chunks);
      if (/generateAssistantResponse/i.test(url.pathname) ||
          target.includes('GenerateAssistantResponse')) {
        const requested = JSON.parse(body).conversationState?.currentMessage?.userInputMessage?.modelId;
        const selected = requested && requested !== 'auto' ? requested : defaultModel;
        const [model, legacyEffort] = selected.split('::');
        if (!(await getModels()).some(item => item.modelId === model)) {
          return json(res, 400, { message: 'Requested model is not available to this new-api key' });
        }
        const payload = JSON.parse(body);
        const effort = payload.additionalModelRequestFields?.reasoning?.effort || legacyEffort;
        if (effort && (!config.effortModels.includes(model) || !efforts.includes(effort))) {
          return json(res, 400, { message: 'Unsupported reasoning effort' });
        }
        if (effort) {
          payload.reasoning_effort = effort;
          console.log(`reasoning_selection=${model}:${effort}`);
        }
        return await kiro.intercept(req, res, Buffer.from(JSON.stringify(payload)), `${config.prefix}/${model}`);
      }
      // Preserve official profile/enterprise policy responses; do not fabricate permissions.
      const origin = ['/Get-Profile', '/Get-Usage-Limits'].includes(url.pathname) ||
        target.includes('ControlPlane') || target.endsWith('.GetProfile')
        ? 'https://management.us-east-1.kiro.dev'
        : url.pathname === '/getUsageLimits' ? 'https://q.us-east-1.amazonaws.com'
        : 'https://runtime.us-east-1.kiro.dev';
      const headers = { ...req.headers };
      for (const name of ['host', 'content-length', 'connection', 'transfer-encoding']) delete headers[name];
      let response = await fetch(origin + req.url, {
        method: req.method, headers, ...(body.length ? { body } : {}),
        signal: AbortSignal.timeout(20000),
      });
      if (target.endsWith('.GetProfile') || url.pathname === '/Get-Profile') {
        const profileArn = JSON.parse(body).profileArn;
        response = await legacyProfile(response, profileArn, headers);
      }
      const contentType = response.headers.get('content-type') || 'application/json';
      res.writeHead(response.status, { 'Content-Type': contentType });
      res.end(Buffer.from(await response.arrayBuffer()));
    } catch (error) {
      console.error(`bridge_error=${error.name}`);
      if (!res.headersSent) json(res, 502, { message: 'Local bridge request failed' });
      else res.end();
    }
  });
  server.listen(config.bridgePort, '127.0.0.1', () => console.log('local_bridge_ready=' + config.bridgePort));
}
start().catch(error => { console.error(error.message); process.exitCode = 1; });
