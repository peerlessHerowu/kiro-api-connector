const context = require('./context_policy.cjs');

function chatRequest(input, config) {
  if (typeof input?.model !== 'string' || !config.models.includes(input.model)) {
    throw new Error('Requested model is not configured');
  }
  if (!Array.isArray(input.input)) throw new Error('Expected Responses input array');
  const messages = [];
  if (input.instructions) messages.push({role: 'system', content: input.instructions});
  for (const item of input.input) {
    if (item.type === 'function_call_output') {
      messages.push({role: 'tool', tool_call_id: item.call_id, content: item.output});
    } else if (item.type === 'function_call') {
      let assistant = messages.at(-1);
      if (assistant?.role !== 'assistant') {
        assistant = {role: 'assistant', content: null, tool_calls: []};
        messages.push(assistant);
      }
      assistant.tool_calls ||= [];
      assistant.tool_calls.push({id: item.call_id, type: 'function', function: {
        name: item.name, arguments: item.arguments,
      }});
    } else if (item.role === 'user' || item.role === 'assistant') {
      let content = item.content;
      if (Array.isArray(content)) content = content.map(part => {
        if (part.type === 'input_text' || part.type === 'output_text') return {type: 'text', text: part.text};
        if (part.type === 'input_image') return {type: 'image_url', image_url: {url: part.image_url}};
        throw new Error(`Unsupported Responses content type: ${part.type}`);
      });
      if (typeof content !== 'string' && !Array.isArray(content)) throw new Error('Unsupported Responses message');
      messages.push({role: item.role, content});
    } else if (item.type !== 'reasoning') {
      throw new Error(`Unsupported Responses item type: ${item.type || 'unknown'}`);
    }
  }
  const budget = context.budget(config, input.model);
  const clipped = context.protectMessages(messages, budget);
  const payload = {model: `${config.prefix}/${input.model}`, messages, stream: true};
  if (input.tools?.length) payload.tools = input.tools.map(tool => ({type: 'function', function: {
    name: tool.name, description: tool.description, parameters: tool.parameters,
  }}));
  if (input.reasoning?.effort && input.reasoning.effort !== 'none') {
    if (!config.effortModels.includes(input.model)) throw new Error('Reasoning effort is not configured for this model');
    payload.reasoning_effort = input.reasoning.effort;
  }
  return {payload, budget, clipped, summaryClipped: !!messages.connectorSummaryClipped};
}

async function createResponse(req, res, input, config, router, key) {
  let request;
  try { request = chatRequest(input, config); }
  catch (error) {
    res.writeHead(400, {'Content-Type': 'application/json'});
    res.end(JSON.stringify({error: {message: error.message, type: 'invalid_request_error'}}));
    return;
  }
  const upstream = await fetch(`${router}/v1/chat/completions`, {method: 'POST',
    headers: {'Content-Type': 'application/json', Authorization: `Bearer ${key}`},
    body: JSON.stringify(request.payload), signal: AbortSignal.timeout(300000)});
  if (!upstream.ok || !upstream.body) {
    const error = await upstream.text();
    res.writeHead(upstream.status, {'Content-Type': 'application/json'});
    res.end(error.slice(0, 8192));
    return;
  }
  if (!upstream.headers.get('content-type')?.includes('text/event-stream')) {
    res.writeHead(502, {'Content-Type': 'application/json'});
    res.end(JSON.stringify({error: {message: 'Router did not return an SSE stream'}}));
    await upstream.body.cancel();
    return;
  }
  res.writeHead(200, {'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache'});
  const send = event => res.write(`data: ${JSON.stringify(event)}\n\n`);
  const calls = new Map();
  let pending = '', usage, finishReason, output = false;
  const decoder = new TextDecoder();
  const processLine = line => {
    if (!line.startsWith('data:')) return;
    const data = line.slice(5).trim();
    if (!data || data === '[DONE]') return;
    const event = JSON.parse(data);
    if (event.error) throw new Error(event.error.message || 'Router stream failed');
    if (event.usage) usage = event.usage;
    const choice = event.choices?.[0];
    if (!choice) return;
    if (choice.finish_reason) finishReason = choice.finish_reason;
    const delta = choice.delta || {};
    if (delta.content) { send({type: 'response.output_text.delta', delta: delta.content}); output = true; }
    if (delta.reasoning_content) send({type: 'response.reasoning_text.delta', delta: delta.reasoning_content});
    for (const tool of delta.tool_calls || []) {
      const call = calls.get(tool.index) || {id: tool.id, name: '', arguments: ''};
      if (tool.id) call.id = tool.id;
      if (tool.function?.name) call.name += tool.function.name;
      if (tool.function?.arguments) call.arguments += tool.function.arguments;
      calls.set(tool.index, call);
    }
  };
  try {
    for await (const chunk of upstream.body) {
      pending += decoder.decode(chunk, {stream: true});
      const lines = pending.split('\n');
      pending = lines.pop();
      for (const line of lines) processLine(line.trimEnd());
    }
    pending += decoder.decode();
    if (pending.trim()) processLine(pending.trim());
    for (const [index, call] of calls) {
      const id = call.id || `call_${index}`;
      const item = {type: 'function_call', call_id: id, name: call.name, arguments: call.arguments};
      send({type: 'response.output_item.added', item: {...item, arguments: ''}});
      send({type: 'response.function_call_arguments.delta', delta: call.arguments});
      send({type: 'response.function_call_arguments.done'});
      send({type: 'response.output_item.done', item});
      output = true;
    }
    if (!output || !finishReason) throw new Error('Router stream ended without a complete model response');
    const percentage = context.usage({usage, inputEstimate: context.estimate(request.payload.messages),
      contextBudget: request.budget, contextClipped: request.clipped,
      summaryClipped: request.summaryClipped});
    const incomplete = finishReason === 'length';
    send({type: incomplete ? 'response.incomplete' : 'response.completed', response: {
      status: incomplete ? 'incomplete' : 'completed',
      ...(incomplete ? {incomplete_details: {reason: 'max_output_tokens'}} : {}),
      usage: {'kiro:context_usage_percentage': percentage},
    }});
    res.end();
  } catch (error) {
    console.error(`create_response_error=${error.name}`);
    send({type: 'error', error: {type: 'server_error', message: 'Local response conversion failed'}});
    res.end();
  }
}

module.exports = {chatRequest, createResponse};
