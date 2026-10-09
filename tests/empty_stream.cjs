const assert = require('node:assert/strict');
const {retryEmptyStream} = require('../runtime/empty_stream.cjs');
const event = value => 'data: ' + JSON.stringify(value) + '\n\n';
const empty = event({choices:[],usage:{completion_tokens:0}}) + 'data: [DONE]\n\n';
const ok = event({choices:[{delta:{content:'OK'},finish_reason:'stop'}]}) + 'data: [DONE]\n\n';
async function check(first, expectedCalls, expected = first) {
  let calls = 0;
  const response = await retryEmptyStream(async () => new Response(++calls === 1 ? first : ok,
    {headers:{'content-type':'text/event-stream'}}));
  assert.equal(await response.text(), expected);
  assert.equal(calls, expectedCalls);
}
(async () => {
  await check(empty, 2, ok);
  for (const delta of [{content:'partial'}, {reasoning_content:'thinking'}, {tool_calls:[{id:'a'}]}]) {
    await check(event({choices:[{delta}]}) + empty, 1);
  }
  await check(event({error:{message:'upstream failed'}}) + empty, 1);
  await check('data: malformed\n\n' + empty, 1);
  await check(event({choices:[]}), 1); // Interrupted stream without DONE.
  await check(empty + 'data: {"choices":[{"delta":{"content":"tail"}}]}', 1);
  let calls = 0;
  const result = await retryEmptyStream(async () => {calls++; return new Response(empty,
    {headers:{'content-type':'text/event-stream'}})});
  assert.equal(await result.text(), empty);
  assert.equal(calls, 2);
  // Once a tool arrives, do not wait for the stream to end before forwarding it.
  const encoder = new TextEncoder();
  let upstream;
  const streaming = await retryEmptyStream(async () => new Response(new ReadableStream({
    start(controller) {upstream=controller; controller.enqueue(encoder.encode(event({choices:[{delta:{tool_calls:[{id:'a'}]}}]})));},
  }), {headers:{'content-type':'text/event-stream'}}));
  const reader = streaming.body.getReader();
  assert.ok((await reader.read()).value.length > 0);
  upstream.close();
  await reader.cancel();
  console.log('PASS: empty retry limit, partial/tool/reasoning preservation, errors, interrupted streams, immediate streaming');
})().catch(error => {console.error(error);process.exitCode=1;});
