// WebAuthn in the browser: the server sends options as JSON (base64url fields), the browser needs ArrayBuffers, and
// the server wants the credential back as JSON. Newer browsers do the conversion natively; older ones get ours.
const b64uToBuf = (s) => {
  const b64 = s.replace(/-/g, '+').replace(/_/g, '/') + '==='.slice((s.length + 3) % 4);
  const bin = atob(b64);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out.buffer;
};
const bufToB64u = (buf) => {
  const bytes = new Uint8Array(buf);
  let bin = '';
  for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
  return btoa(bin).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
};

export const passkeysSupported = () => !!(window.PublicKeyCredential && navigator.credentials && navigator.credentials.get);

function requestOptions(json) {
  if (PublicKeyCredential.parseRequestOptionsFromJSON) return PublicKeyCredential.parseRequestOptionsFromJSON(json);
  return {
    ...json,
    challenge: b64uToBuf(json.challenge),
    allowCredentials: (json.allowCredentials || []).map((c) => ({ ...c, id: b64uToBuf(c.id) })),
  };
}
function creationOptions(json) {
  if (PublicKeyCredential.parseCreationOptionsFromJSON) return PublicKeyCredential.parseCreationOptionsFromJSON(json);
  return {
    ...json,
    challenge: b64uToBuf(json.challenge),
    user: { ...json.user, id: b64uToBuf(json.user.id) },
    excludeCredentials: (json.excludeCredentials || []).map((c) => ({ ...c, id: b64uToBuf(c.id) })),
  };
}
function credentialJSON(cred) {
  if (typeof cred.toJSON === 'function') return cred.toJSON();
  const r = cred.response;
  const response = { clientDataJSON: bufToB64u(r.clientDataJSON) };
  if (r.attestationObject) {
    response.attestationObject = bufToB64u(r.attestationObject);
    if (r.getTransports) response.transports = r.getTransports();
  }
  if (r.authenticatorData) response.authenticatorData = bufToB64u(r.authenticatorData);
  if (r.signature) response.signature = bufToB64u(r.signature);
  if (r.userHandle) response.userHandle = bufToB64u(r.userHandle);
  return {
    id: cred.id, rawId: bufToB64u(cred.rawId), type: cred.type, response,
    clientExtensionResults: cred.getClientExtensionResults ? cred.getClientExtensionResults() : {},
    authenticatorAttachment: cred.authenticatorAttachment || undefined,
  };
}

async function post(url, body, headers = {}) {
  const r = await fetch(url, {
    method: 'POST', credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json', ...headers },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || 'That didn’t work. Please try again.');
  return data;
}

export async function passkeySignIn(next) {
  const options = await post('/auth/passkey/options');
  const cred = await navigator.credentials.get({ publicKey: requestOptions(options) });
  const done = await post('/auth/passkey/verify', { credential: credentialJSON(cred), next });
  return done.next || '/';
}

export async function passkeyAdd(csrf, label) {
  const options = await post('/account/passkeys/options', undefined, { 'X-CSRF': csrf });
  const cred = await navigator.credentials.create({ publicKey: creationOptions(options) });
  await post('/account/passkeys/add', { credential: credentialJSON(cred), label }, { 'X-CSRF': csrf });
}
