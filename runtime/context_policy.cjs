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
  return clipped;
}

function usage(state) {
  const tokens = state.usage?.prompt_tokens;
  const input = Number.isFinite(tokens) && tokens > 0 ? tokens : state.inputEstimate;
  const output = state.usage?.completion_tokens || 0;
  const percentage = Math.min(100, Math.max(0, (input + output) / state.contextBudget * 100));
  // Force native summarization after protection; the IDE retains the original tool output.
  return state.contextClipped ? Math.max(85, percentage) : percentage;
}

module.exports = { budget, estimate, protectMessages, usage };
