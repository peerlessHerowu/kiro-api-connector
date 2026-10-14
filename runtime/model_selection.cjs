'use strict';

// Only unwrap Kiro's namespace and explicit user mappings. Never guess a provider model.
function selectModel(requested, config) {
  let model = requested && requested !== 'auto' ? requested : config.defaultModel;
  if (typeof model !== 'string') throw new Error('Model ID must be a string');
  if (model.startsWith('qdev::')) model = model.slice(6);
  let effort;
  const suffix = model.match(/::(low|medium|high|xhigh)$/);
  if (suffix) { effort = suffix[1]; model = model.slice(0, -suffix[0].length); }
  const mapped = config.modelAliases?.[model];
  if (mapped) model = mapped;
  if (!config.models.includes(model)) {
    const error = new Error('Requested model is not configured in Kiro API Connector');
    error.model = model;
    throw error;
  }
  return { model, effort, mapped: !!mapped };
}
module.exports = { selectModel };
