// Local safety budgets, not claims about an upstream model's official context window.
function budget(config, model) {
  const value = config.contextBudgets?.[model] ?? config.contextBudget ?? 200000;
  return Number.isInteger(value) && value >= 8000 && value <= 2000000 ? value : 200000;
}

function estimate(value) {
  const text = typeof value === 'string' ? value : JSON.stringify(value);
  // ponytail: conservative character estimate; actual upstream usage takes precedence.
  const nonAscii = (text.match(/[^\x00-\x7f]/g) || []).length;
  return Math.ceil((text.length - nonAscii) / 3 + nonAscii);
}

function shorten(text, allowance, label) {
  if (typeof text !== 'string' || text.length <= allowance) return text;
  const marker = `\n[Connector shortened this ${label} (${text.length} characters). Omitted content is not evidence. Continue from the retained beginning and end, or request the missing part in smaller pieces.]\n`;
  const keep = Math.max(128, allowance - marker.length);
  const head = Math.floor(keep * (label === 'tool result' ? .75 : .25));
  return text.slice(0, head) + marker + text.slice(-(keep - head));
}

function protectMessages(messages, limit) {
  const tools = messages.filter(m => m.role === 'tool' && typeof m.content === 'string');
  const perTool = Math.min(64000, limit);
  let remaining = limit; // Tool results collectively get roughly one third of the token budget.
  let clipped = 0;
  for (const tool of tools.reverse()) {
    const allowance = Math.max(512, Math.min(perTool, remaining));
    if (tool.content.length > allowance) {
      const marker = `\n[Connector shortened this tool result (${tool.content.length} characters). Omitted content is not evidence. Query fewer fields/rows or read the original output in smaller parts.]\n`;
      const keep = Math.max(0, allowance - marker.length);
      tool.content = tool.content.slice(0, Math.floor(keep * .75)) + marker +
        tool.content.slice(-(keep - Math.floor(keep * .75)));
      clipped++;
    }
    remaining -= tool.content.length;
  }

  // Native truncation summarization merges the original transcript into one
  // user message, bypassing the tool-only cap above. Leave ordinary requests
  // below 90% alone; reserve room for tools, output and estimate error.
  const target = Math.max(4096, Math.floor(limit * .75));
  let total = estimate(messages);
  if (total <= limit * .9) return clipped;
  const candidates = messages
    .filter(m => typeof m.content === 'string' && m.content.length > 2048)
    .sort((a, b) => b.content.length - a.content.length);
  for (const message of candidates) {
    if (total <= target) break;
    if (estimate(message.content) > target) {
      // Search using the same estimator: Chinese and source code have different
      // densities, so a proportional character cut can still exceed the budget.
      const original = message.content;
      const label = message.role === 'tool' ? 'tool result' : 'conversation message';
      let low = 256, high = original.length;
      while (low < high) {
        const mid = Math.ceil((low + high) / 2);
        message.content = shorten(original, mid, label);
        if (estimate(messages) <= target) low = mid;
        else high = mid - 1;
      }
      message.content = shorten(original, low, label);
      if (message.role !== 'tool' &&
          /automated summarization request|conversation data to summarize|conversation that exceeded context limits/i.test(original)) {
        messages.connectorSummaryClipped = true;
      }
      clipped++;
      total = estimate(messages);
    }
  }
  return clipped;
}

function usage(state) {
  const tokens = state.usage?.prompt_tokens;
  const input = Number.isFinite(tokens) && tokens > 0 ? tokens : state.inputEstimate;
  const output = state.usage?.completion_tokens || 0;
  const percentage = Math.min(100, Math.max(0, (input + output) / state.contextBudget * 100));
  // A clipped native summary is already the continuation input. Do not ask
  // Kiro to summarize the same summary again and create a retry loop.
  if (state.summaryClipped) return Math.min(79, percentage);
  // Force native summarization after protection; the IDE retains the original tool output.
  return state.contextClipped ? Math.max(85, percentage) : percentage;
}

module.exports = { budget, estimate, protectMessages, usage };
