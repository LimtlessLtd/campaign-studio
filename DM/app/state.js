'use strict';

const S = {
  state: null,
  shapes: {},
  docs: {},
  base: {},
  revs: {},
  pending: {},
  jobs: [],
  recordIndex: { codex: [], threads: [] },
  route: 0,
};

/* ---------- server ---------- */
async function api(path, opts) {
  const r = await fetch(path, Object.assign({ cache: 'no-store' }, opts));
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(body.error || r.statusText);
  return body;
}
const post = (path, body) =>
  api(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-DM-Site': '1' },
    body: JSON.stringify(body || {}),
  });
async function uploadImage(file) {
  if (!file || !['image/png', 'image/jpeg', 'image/webp', 'image/gif'].includes(file.type))
    throw new Error('Choose a PNG, JPEG, WebP or GIF image.');
  if (file.size > 25 * 1024 * 1024) throw new Error('Images must be under 25 MB.');
  return api('/api/upload-image', {
    method: 'POST',
    headers: {
      'Content-Type': file.type,
      'X-File-Name': encodeURIComponent(file.name),
      'X-DM-Site': '1',
    },
    body: file,
  });
}

/* ---------- documents: load, save, and merge when Claude (or another tab) changed them meanwhile ---------- */
const recordName = (kind, id) => kind + '/' + id;
const isRecord = (name) => name.startsWith('codex/') || name.startsWith('threads/');
const docUrl = (name) =>
  isRecord(name)
    ? '/api/records/' +
      name.split('/', 1)[0] +
      '/item?id=' +
      encodeURIComponent(name.split('/').slice(1).join('/'))
    : '/api/doc/' + name;
async function recordChoices(kind, signal) {
  const rows = await api('/api/records/' + kind + '/choices', { signal });
  S.recordIndex[kind] = rows;
  return rows;
}
async function createRecord(kind, value) {
  const name = recordName(kind, value.id);
  S.docs[name] = value;
  S.base[name] = {};
  S.revs[name] = '0';
  save(name);
  await flush(name);
  await recordChoices(kind);
}
async function removeRecord(kind, id) {
  const name = recordName(kind, id);
  if (S.pending[name]) await flush(name);
  if (!(name in S.docs)) await load(name, null);
  const response = await fetch(docUrl(name), {
    method: 'DELETE',
    headers: { 'X-DM-Site': '1', 'X-Rev': S.revs[name] || '0' },
  });
  if (!response.ok) throw new Error((await response.json()).error || 'Could not delete record.');
  delete S.docs[name];
  delete S.base[name];
  delete S.revs[name];
  await recordChoices(kind);
}
async function load(name, fallback, signal) {
  const r = await fetch(docUrl(name), { cache: 'no-store', signal });
  const value = r.ok ? await r.json() : clone(fallback);
  if (signal?.aborted) throw new DOMException('Page load cancelled', 'AbortError');
  if (r.ok) {
    S.docs[name] = value;
    S.revs[name] = r.headers.get('X-Rev');
  } else {
    S.docs[name] = value;
    S.revs[name] = '0';
  }
  S.base[name] = clone(S.docs[name]);
}
async function doc(name, fallback, signal) {
  if (signal?.aborted) throw new DOMException('Page load cancelled', 'AbortError');
  if (!(name in S.docs)) await load(name, fallback, signal);
  return S.docs[name];
}
/* A new stored record (codex entry, thread, scene…) with every field the server's shapes define. */
const blank = (kind, fields) => newRecord(S.shapes[kind], kind, fields);

const timers = {};
function save(name) {
  const flag = $('#saved');
  flag.className = '';
  flag.textContent = 'Saving…';
  S.pending[name] = true;
  clearTimeout(timers[name]);
  timers[name] = setTimeout(
    () =>
      flush(name).catch((e) => {
        flag.className = 'err';
        flag.textContent = 'Not saved: ' + e.message;
      }),
    600,
  );
}
async function flush(name) {
  clearTimeout(timers[name]);
  for (let attempt = 0; attempt < 4; attempt++) {
    // Edits can continue while the request is in flight: only what was sent becomes the saved base.
    const sent = JSON.stringify(S.docs[name]);
    const r = await fetch(docUrl(name), {
      method: 'PUT',
      headers: {
        'Content-Type': 'application/json',
        'X-DM-Site': '1',
        'X-Rev': S.revs[name] || '',
      },
      body: sent,
    });
    if (r.status === 409) {
      const { doc: server, rev } = await r.json();
      mergeInto(S.base[name], S.docs[name], server);
      S.base[name] = clone(server);
      S.revs[name] = rev;
      S.merged = true;
      continue;
    }
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || r.statusText);
    S.revs[name] = (await r.json()).rev;
    S.base[name] = JSON.parse(sent);
    if (JSON.stringify(S.docs[name]) === sent) delete S.pending[name];
    $('#saved').textContent =
      (S.merged ? 'Merged with changes made elsewhere · ' : '') +
      'Saved ' +
      new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    if (S.merged) {
      S.merged = false;
      refreshSoon();
    }
    return;
  }
  throw new Error('kept changing underneath; reload the page');
}
window.addEventListener('beforeunload', (e) => {
  if (Object.keys(S.pending).length) e.preventDefault();
});

/* Watch for changes made elsewhere (Claude doing a request, a map finishing) and for running jobs. */
let polling = false;
async function poll() {
  if (polling || !S.state) return;
  polling = true;
  try {
    const names = Object.keys(S.docs);
    let changed = false;
    for (let i = 0; i < names.length; i += 80) {
      const batch = names.slice(i, i + 80);
      const revs = await api('/api/revs?' + new URLSearchParams({ names: batch.join(',') }));
      for (const n of batch) {
        if (S.pending[n] || revs[n] === S.revs[n] || revs[n] === '0') continue;
        const r = await fetch(docUrl(n), { cache: 'no-store' });
        if (!r.ok) continue;
        const server = await r.json();
        mergeInto(S.base[n], S.docs[n], server);
        S.base[n] = clone(server);
        S.revs[n] = r.headers.get('X-Rev');
        changed = true;
      }
    }
    const before = JSON.stringify(S.jobs.map((j) => [j.id, j.status]));
    S.jobs = await api('/api/jobs');
    const active = S.jobs.filter((j) => j.status === 'running' || j.status === 'queued');
    $('#jobs').textContent = active.length
      ? `⚙ ${active.length} job${active.length > 1 ? 's' : ''} running`
      : '';
    if (before !== JSON.stringify(S.jobs.map((j) => [j.id, j.status]))) {
      changed = true;
      S.state = await api('/api/state');
    }
    if (changed) refreshSoon();
  } catch (e) {
    /* the server may be restarting */
  }
  polling = false;
}
setInterval(poll, 3000);

/* Re-render with fresh data, but never under the DM's fingers. */
function refreshSoon() {
  const a = document.activeElement;
  if (a && /INPUT|TEXTAREA|SELECT/.test(a.tagName) && root.contains(a)) {
    a.addEventListener('blur', () => setTimeout(refreshSoon, 50), { once: true });
    return;
  }
  route(true);
}
