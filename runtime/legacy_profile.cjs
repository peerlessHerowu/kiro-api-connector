// Preserve real policy data for Q Developer profiles that the Kiro control plane rejects.
module.exports = async function legacyProfile(response, profileArn, headers, requestFetch = fetch) {
  const match = /^arn:aws:codewhisperer:(us-east-1):\d+:profile\/[^/]+$/.exec(profileArn || '');
  if (!match || ![400, 403].includes(response.status)) return response;
  const error = await response.clone().json().catch(() => ({}));
  if (!String(error.__type || '').includes('AccessDeniedException') && error.message !== 'Access denied') {
    return response;
  }
  const actual = await requestFetch(`https://q.${match[1]}.amazonaws.com/`, {
    method: 'POST', headers: { ...headers, 'content-type': 'application/x-amz-json-1.0',
      'x-amz-target': 'AmazonCodeWhispererService.GetProfile' },
    body: JSON.stringify({ profileArn }), signal: AbortSignal.timeout(15000),
  }).catch(() => null);
  if (!actual?.ok) return response;
  const data = await actual.clone().json().catch(() => ({}));
  if (data.profile?.arn !== profileArn || data.profile?.status !== 'ACTIVE') return response;
  console.log('legacy_profile_adapter=official_active_profile');
  return actual;
};
