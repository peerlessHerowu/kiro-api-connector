// Buffer only the empty prefix. Once any model output arrives it is streamed
// immediately and never retried, so tools and partial replies cannot run twice.
async function retryEmptyStream(fetchResponse, signal) {
  for (let attempt = 1; attempt <= 2; attempt++) {
    const response = await fetchResponse();
    if (!response.ok || !response.body || !response.headers.get('content-type')?.includes('text/event-stream')) return response;
    const reader = response.body.getReader();
    const chunks = [];
    const decoder = new TextDecoder();
    let pending = '', bytes = 0, output = false, error = false, complete = false, terminal = false;
    while (!output && bytes < 65536) {
      const item = await reader.read();
      if (item.done) { complete = true; break; }
      chunks.push(item.value);
      bytes += item.value.length;
      pending += decoder.decode(item.value, {stream:true});
      const lines = pending.split('\n');
      pending = lines.pop();
      for (const line of lines) {
        if (!line.trim().startsWith('data:')) continue;
        const data = line.trim().slice(5).trim();
        if (data === '[DONE]') { terminal = true; continue; }
        try {
          const event = JSON.parse(data);
          error ||= !!event.error;
          output ||= (event.choices || []).some(choice => {
            const delta = choice.delta || choice.message || {};
            return !!(delta.content || delta.reasoning_content || delta.reasoning || delta.tool_calls?.length || delta.function_call);
          });
        } catch { error = true; }
      }
      if (error) break;
    }
    if (complete && terminal && !pending.trim() && !output && !error && attempt === 1 && !signal?.aborted) {
      reader.releaseLock();
      console.log('empty_stream_retry=' + JSON.stringify({time:new Date().toISOString(), attempt}));
      continue;
    }
    const stream = new ReadableStream({
      start(controller) {
        for (const chunk of chunks) controller.enqueue(chunk);
        if (complete) { reader.releaseLock(); controller.close(); }
      },
      async pull(controller) {
        try {
          const item = await reader.read();
          if (item.done) { reader.releaseLock(); controller.close(); }
          else controller.enqueue(item.value);
        } catch (err) { controller.error(err); }
      },
      async cancel(reason) { await reader.cancel(reason); reader.releaseLock(); },
    });
    return new Response(stream, {status:response.status, statusText:response.statusText, headers:response.headers});
  }
}
module.exports = {retryEmptyStream};
