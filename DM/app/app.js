/* DM Screen: the private campaign manager. Data lives in DM/data as JSON; server.py saves it and runs jobs. */
'use strict';

const $ = (s, el = document) => el.querySelector(s);
const root = $('#main'); // each route renders into a fresh view (S.view), swapped in only if still the latest
const S = { state: null, docs: {}, base: {}, revs: {}, pending: {}, jobs: [], route: 0 };
let PCS = [];
const THREAD_STATES = ['open', 'planned', 'foreshadowed', 'resolved'];
const TYPES = {
  pc: 'Heroes',
  npc: 'People',
  god: 'Gods',
  place: 'Places',
  faction: 'Factions',
  item: 'Items',
  monster: 'Monsters',
};
const KINDS = {
  'battle map': 'Battle map',
  npc: 'NPC',
  item: 'Item',
  event: 'Event',
  journal: 'Journal entry',
  encounter: 'Encounter',
  handout: 'Handout',
  plot: 'Plot',
  other: 'Other',
  'stock map': 'Stock a map',
};

/* ---------- tiny DOM helper ---------- */
function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  if (tag === 'button') el.type = 'button';
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
    else if (k === 'class') el.className = v;
    else if (k === 'value') el.value = v;
    else if (k === 'checked') el.checked = !!v;
    else el.setAttribute(k, v === true ? '' : v);
  }
  for (const kid of kids.flat(Infinity))
    if (kid != null && kid !== false) el.append(kid.nodeType ? kid : String(kid));
  return el;
}
const fileUrl = (p) => '/files/' + p.split('/').map(encodeURIComponent).join('/');
function render(el, ...children) {
  el.replaceChildren(
    ...children.flat(Infinity).filter((child) => child != null && child !== false),
  );
}
const slug = (s) =>
  s
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-|-$/g, '');
const uid = (p) => p + '-' + Date.now().toString(36) + Math.random().toString(36).slice(2, 5);
const clone = (x) => JSON.parse(JSON.stringify(x));
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
const isObj = (x) => x && typeof x === 'object' && !Array.isArray(x);
const when = (t) =>
  new Date(typeof t === 'number' && t < 1e12 ? t * 1000 : t).toLocaleString([], {
    dateStyle: 'short',
    timeStyle: 'short',
  });

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
function imagePicker(onUploaded, label = 'Upload image') {
  const input = h('input', {
    type: 'file',
    accept: 'image/png,image/jpeg,image/webp,image/gif',
    hidden: true,
    onchange: async (e) => {
      try {
        if (e.target.files[0]) onUploaded((await uploadImage(e.target.files[0])).path);
      } catch (err) {
        alert(err.message);
      }
      input.value = '';
    },
  });
  return h('span', {}, input, h('button', { onclick: () => input.click() }, label));
}
async function queueArt({
  map = '',
  area = null,
  codex = '',
  title = '',
  prompt: suggested = '',
} = {}) {
  const f = { title: title || 'Campaign image', prompt: suggested };
  modal(
    'Queue an image',
    'Describe the character, object or scene to illustrate.',
    h(
      'div',
      {},
      formInput(f, 'title', 'Title'),
      formInput(f, 'prompt', 'Image brief', { type: 'textarea', rows: 6 }),
    ),
    async () => {
      if (!f.prompt.trim()) throw new Error('Describe the image first.');
      const art = await doc('art', { items: [] });
      art.items.unshift({
        id: uid('art'),
        title: f.title,
        prompt: f.prompt.trim(),
        map,
        area,
        codex,
        image: '',
        status: 'queued',
        created: Date.now(),
      });
      save('art');
      await flush('art');
      go('#/art');
      route(true);
    },
    'Queue image',
  );
}
async function attachArt(item, path) {
  item.image = path;
  item.status = 'ready';
  save('art');
  if (item.map) {
    const name = 'mapkey/' + item.map;
    const key = await doc(name, null);
    const target = item.area == null ? key : key?.areas?.find((a) => a.n === item.area);
    if (target) {
      target.images = target.images || [];
      if (!target.images.includes(path)) target.images.push(path);
      save(name);
    }
  }
  if (item.codex) {
    const c = await doc('codex', { entries: [] });
    const entry = c.entries.find((e) => e.id === item.codex);
    if (entry) {
      entry.image = path;
      save('codex');
    }
  }
  refreshSoon();
}

/* ---------- documents: load, save, and merge when Claude (or another tab) changed them meanwhile ---------- */
async function load(name, fallback) {
  const r = await fetch('/api/doc/' + name, { cache: 'no-store' });
  if (r.ok) {
    S.docs[name] = await r.json();
    S.revs[name] = r.headers.get('X-Rev');
  } else {
    S.docs[name] = clone(fallback);
    S.revs[name] = '0';
  }
  S.base[name] = clone(S.docs[name]);
}
async function doc(name, fallback) {
  if (!(name in S.docs)) await load(name, fallback);
  return S.docs[name];
}

/* Fold the server's version into ours, in place (pages hold references into these objects): anything we
   haven't changed since we loaded it takes the server's value; lists of objects with ids merge item by item. */
function mergeInto(base, local, server) {
  if (Array.isArray(local) && Array.isArray(server)) {
    const keyed = (a) => a.every((x) => isObj(x) && ('id' in x || 'n' in x));
    const idOf = (x) => ('id' in x ? x.id : 'n' + x.n);
    if (keyed(local) && keyed(server)) {
      const b = new Map((Array.isArray(base) ? base : []).filter(isObj).map((x) => [idOf(x), x]));
      const l = new Map(local.map((x) => [idOf(x), x]));
      const s = new Map(server.map((x) => [idOf(x), x]));
      server.forEach((x, i) => {
        if (l.has(idOf(x))) mergeInto(b.get(idOf(x)), l.get(idOf(x)), x);
        else if (!b.has(idOf(x))) local.splice(Math.min(i, local.length), 0, x); // new on the server
      });
      for (let i = local.length - 1; i >= 0; i--) {
        // deleted on the server and untouched here
        const x = local[i];
        if (!s.has(idOf(x)) && b.has(idOf(x)) && same(b.get(idOf(x)), x)) local.splice(i, 1);
      }
      return local;
    }
    return same(base, local) ? server : local;
  }
  if (isObj(local) && isObj(server)) {
    for (const k of Object.keys(server)) {
      const bv = isObj(base) ? base[k] : undefined;
      if (!(k in local)) {
        if (!(isObj(base) && k in base)) local[k] = server[k];
        continue;
      }
      if (isObj(local[k]) || Array.isArray(local[k])) {
        const m = mergeInto(bv, local[k], server[k]);
        if (m !== local[k]) local[k] = m;
      } else if (same(bv, local[k])) local[k] = server[k];
    }
    return local;
  }
  return same(base, local) ? server : local;
}

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
    const r = await fetch('/api/doc/' + name, {
      method: 'PUT',
      headers: {
        'Content-Type': 'application/json',
        'X-DM-Site': '1',
        'X-Rev': S.revs[name] || '',
      },
      body: JSON.stringify(S.docs[name]),
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
    S.base[name] = clone(S.docs[name]);
    delete S.pending[name];
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
    if (names.length) {
      const revs = await api('/api/revs?names=' + names.join(','));
      for (const n of names) {
        if (S.pending[n] || revs[n] === S.revs[n] || revs[n] === '0') continue;
        const r = await fetch('/api/doc/' + n, { cache: 'no-store' });
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

/* ---------- form helpers ---------- */
function field(docName, obj, key, opts = {}) {
  const { label, type = 'text', rows, placeholder, options, onchange } = opts;
  let el;
  const commit = (v) => {
    obj[key] = v;
    save(docName);
    onchange && onchange(v);
  };
  if (type === 'textarea')
    el = h('textarea', {
      rows,
      placeholder,
      value: obj[key] || '',
      oninput: (e) => commit(e.target.value),
    });
  else if (type === 'select')
    el = h(
      'select',
      { onchange: (e) => commit(e.target.value) },
      options.map((o) => h('option', { value: o, selected: o === obj[key] }, o || '—')),
    );
  else if (type === 'checkbox')
    el = h('input', { type, checked: obj[key], onchange: (e) => commit(e.target.checked) });
  else
    el = h('input', {
      type,
      placeholder,
      value: obj[key] || '',
      oninput: (e) => commit(e.target.value),
    });
  if (label) {
    el.id = uid('field');
    return h('div', { class: opts.class }, h('label', { for: el.id }, label), el);
  }
  return el;
}

function listEditor(docName, arr, { checklist = false, placeholder = 'Add…' } = {}) {
  const box = h('div');
  const draw = () => {
    render(
      box,
      ...arr.map((item, i) => {
        const text = checklist ? item.text : item;
        const set = (v) => {
          if (checklist) item.text = v;
          else arr[i] = v;
          save(docName);
        };
        return h(
          'div',
          { class: 'check' + (checklist && item.done ? ' done' : '') },
          checklist &&
            h('input', {
              type: 'checkbox',
              checked: item.done,
              onchange: (e) => {
                item.done = e.target.checked;
                save(docName);
                draw();
              },
            }),
          h('input', { type: 'text', value: text, oninput: (e) => set(e.target.value) }),
          h(
            'button',
            {
              class: 'danger',
              title: 'Remove',
              onclick: () => {
                arr.splice(i, 1);
                save(docName);
                draw();
              },
            },
            '×',
          ),
        );
      }),
      h('input', {
        type: 'text',
        placeholder,
        onkeydown: (e) => {
          if (e.key !== 'Enter' || !e.target.value.trim()) return;
          arr.push(
            checklist ? { text: e.target.value.trim(), done: false } : e.target.value.trim(),
          );
          save(docName);
          draw();
          box.lastChild.focus();
        },
      }),
    );
  };
  draw();
  return box;
}

/* editable list of small objects (loot rows, events) */
function rowsEditor(docName, arr, cols, blank) {
  const box = h('div');
  const draw = () =>
    render(
      box,
      ...arr.map((row, i) =>
        h(
          'div',
          { class: 'rowedit' },
          cols.map(([key, ph, w]) =>
            h('input', {
              type: 'text',
              placeholder: ph,
              value: row[key] || '',
              style: `flex:${w}`,
              oninput: (e) => {
                row[key] = e.target.value;
                save(docName);
              },
            }),
          ),
          h(
            'button',
            {
              class: 'danger',
              title: 'Remove',
              onclick: () => {
                arr.splice(i, 1);
                save(docName);
                draw();
              },
            },
            '×',
          ),
        ),
      ),
      h(
        'button',
        {
          class: 'small',
          onclick: () => {
            const row = clone(blank);
            if (row.id) row.id = uid('row');
            arr.push(row);
            save(docName);
            draw();
          },
        },
        '+ add',
      ),
    );
  draw();
  return box;
}

function picker(docName, arr, choices, { placeholder = 'Add…', cls = '' } = {}) {
  const box = h('div', { class: 'pick' });
  const draw = () => {
    const input = h('input', { type: 'text', placeholder });
    const opts = h('div', { class: 'opts', hidden: true });
    const show = () => {
      const q = input.value.toLowerCase();
      const hits = choices()
        .filter((c) => !arr.includes(c.id) && c.name.toLowerCase().includes(q))
        .slice(0, 30);
      render(
        opts,
        ...hits.map((c) =>
          h(
            'button',
            {
              type: 'button',
              onmousedown: (e) => e.preventDefault(),
              onclick: () => {
                arr.push(c.id);
                save(docName);
                draw();
              },
            },
            c.name,
          ),
        ),
      );
      opts.hidden = !hits.length;
    };
    input.addEventListener('input', show);
    input.addEventListener('focus', show);
    input.addEventListener('blur', () => {
      opts.hidden = true;
    });
    const names = Object.fromEntries(choices().map((c) => [c.id, c.name]));
    render(
      box,
      h(
        'div',
        { class: 'row', style: 'margin-bottom:6px' },
        arr.map((id, i) =>
          h(
            'span',
            { class: 'chip ' + cls },
            names[id] || id,
            h(
              'button',
              {
                title: 'Remove',
                onclick: () => {
                  arr.splice(i, 1);
                  save(docName);
                  draw();
                },
              },
              '×',
            ),
          ),
        ),
      ),
      input,
      opts,
    );
  };
  draw();
  return box;
}

/* ---------- lookups ---------- */
const codexChoices = () =>
  (S.docs.codex?.entries || []).map((e) => ({
    id: e.id,
    name: e.name + (e.type === 'npc' ? '' : ' · ' + e.type),
  }));
const threadChoices = () =>
  (S.docs.threads?.threads || []).map((t) => ({ id: t.id, name: t.title }));
const hero = (id) =>
  S.state.public.heroes.find((x) => x.id === id) ||
  S.docs.codex?.entries.find((x) => x.id === id) || { name: id };
const pcChoices = () => PCS.map((id) => ({ id, name: hero(id).name }));
const pcName = (id) => hero(id).name.split(' ')[0];
const lastSession = () =>
  S.state.public.sessions.reduce((a, b) => (b.n > a.n ? b : a), {
    n: 0,
    title: 'No sessions recorded',
    chapter: '',
  });
const plain = (t) => (t || '').replace(/<[^>]+>/g, '');

function sessionsMentioning(entry) {
  const names = [entry.name, entry.name.split(' ')[0], ...(entry.aka || [])].filter(
    (n) => n && n.length > 2,
  );
  const re = new RegExp(
    '\\b(' + names.map((n) => n.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|') + ')\\b',
    'i',
  );
  return S.state.public.sessions
    .filter((s) => re.test(plain(s.text)) || re.test(s.title))
    .map((s) => s.n);
}

/* ---------- requests and jobs ---------- */
async function addRequest(fields) {
  const box = await doc('inbox', { items: [] });
  const item = Object.assign(
    {
      id: uid('req'),
      to: 'claude',
      kind: 'other',
      status: 'new',
      text: '',
      result: '',
      created: Date.now(),
    },
    fields,
  );
  box.items.unshift(item);
  S.pending.inbox = true;
  await flush('inbox');
  return item;
}
async function sendToClaude(item) {
  if (!S.state.claude) {
    alert(
      'Claude Code is unavailable. Export the prompt pack and import a proposal from another assistant.',
    );
    return;
  }
  if (S.pending.inbox) await flush('inbox');
  try {
    await post('/api/requests/' + item.id + '/run', {});
  } catch (e) {
    alert(e.message);
    return;
  }
  await load('inbox', { items: [] });
  await poll();
  route(true);
}
async function importRequestProposal(item) {
  if (S.pending.inbox) await flush('inbox');
  const f = { text: item.draft ? JSON.stringify(item.draft, null, 2) : '' };
  modal(
    item.draft ? 'Edit proposal' : 'Import proposal',
    'Paste or edit the JSON proposal, then validate it before applying.',
    formInput(f, 'text', 'Proposal JSON', { type: 'textarea', rows: 12 }),
    async () => {
      await post('/api/requests/' + item.id + '/stage', { draft: JSON.parse(f.text) });
      await load('inbox', { items: [] });
      route(true);
    },
    'Validate & review',
  );
}
async function applyRequestProposal(item) {
  if (S.pending.inbox) await flush('inbox');
  try {
    await post('/api/requests/' + item.id + '/apply', {});
    await load('inbox', { items: [] });
    await poll();
    route(true);
  } catch (e) {
    alert(e.message);
  }
}
function statusChip(it) {
  const job = it.job && S.jobs.find((j) => j.id === it.job);
  const txt =
    it.status === 'doing'
      ? job && job.status === 'queued'
        ? 'queued for Claude'
        : 'Claude is drafting…'
      : it.status === 'review'
        ? 'Ready to review'
        : it.status;
  return h(
    'span',
    {
      class:
        'chip st-' +
        ({ new: 'open', doing: 'planned', review: 'planned', done: 'resolved' }[it.status] ||
          'open'),
    },
    txt,
  );
}
function requestCard(it, box, draw) {
  const isMap = ['battle map', 'stock map'].includes(it.kind);
  return h(
    'div',
    { class: 'card', style: 'margin-bottom:10px' + (it.status === 'done' ? ';opacity:.8' : '') },
    h(
      'div',
      { class: 'spread' },
      h(
        'div',
        { class: 'row' },
        h('b', {}, KINDS[it.kind] || it.kind),
        statusChip(it),
        it.session
          ? h('a', { class: 'chip', href: '#/prep/' + it.session }, it.session.toUpperCase())
          : null,
        it.map ? h('a', { class: 'chip', href: '#/maps/' + it.map }, 'map: ' + it.map) : null,
      ),
      h(
        'div',
        { class: 'row' },
        isMap
          ? h('a', { class: 'btn primary', href: '#/maps/new' }, 'Open map studio')
          : it.status !== 'doing' && it.status !== 'done'
            ? h(
                'button',
                { class: 'primary', onclick: () => sendToClaude(it) },
                it.status === 'review' ? 'Draft again' : 'Draft with Claude',
              )
            : null,
        !isMap && it.status !== 'doing' && it.status !== 'done'
          ? h(
              'a',
              {
                class: 'btn',
                href: '/api/requests/' + it.id + '/pack',
                download: 'request-' + it.id + '.json',
              },
              'Export prompt pack',
            )
          : null,
        !isMap && it.status !== 'doing' && it.status !== 'done'
          ? h(
              'button',
              { onclick: () => importRequestProposal(it) },
              it.draft ? 'Edit proposal' : 'Import proposal',
            )
          : null,
        it.status === 'review'
          ? h(
              'button',
              { class: 'primary', onclick: () => applyRequestProposal(it) },
              'Apply to campaign',
            )
          : null,
        it.status === 'done'
          ? h(
              'button',
              {
                onclick: async () => {
                  if (it.applied) {
                    await addRequest({ kind: it.kind, session: it.session || '' });
                    route(true);
                  } else {
                    it.status = 'new';
                    save('inbox');
                    draw();
                  }
                },
              },
              it.applied ? 'New follow-up' : 'Reopen',
            )
          : null,
        h(
          'button',
          {
            class: 'danger',
            onclick: () => {
              if (confirm('Delete this request?')) {
                box.items.splice(box.items.indexOf(it), 1);
                save('inbox');
                draw();
              }
            },
          },
          'Delete',
        ),
      ),
    ),
    field('inbox', it, 'text', {
      type: 'textarea',
      rows: 2,
      placeholder: 'What do you want made?',
    }),
    h(
      'label',
      {},
      'Session prep (needed for scenes and handouts)',
      h(
        'select',
        {
          onchange: (event) => {
            it.session = event.target.value;
            save('inbox');
            draw();
          },
        },
        h('option', { value: '', selected: !it.session }, 'No session'),
        (S.state.prep || []).map((name) =>
          h('option', { value: name, selected: it.session === name }, name.toUpperCase()),
        ),
      ),
    ),
    it.result
      ? h('div', {}, h('label', {}, 'Result'), h('pre', { class: 'file' }, it.result))
      : null,
    it.error ? h('p', { class: 'err' }, it.error) : null,
    it.status === 'review' && it.draft
      ? h(
          'details',
          { open: true },
          h('summary', {}, 'Review additions'),
          ...['entries', 'threads', 'scenes', 'handouts', 'goals', 'loot', 'checklist', 'notes']
            .filter((key) => it.draft[key]?.length)
            .map((key) =>
              h(
                'div',
                {},
                h('b', {}, key),
                h('pre', { class: 'file' }, JSON.stringify(it.draft[key], null, 2)),
              ),
            ),
        )
      : null,
    h(
      'div',
      { class: 'muted', style: 'font-size:12px;margin-top:6px' },
      'Added ' + when(it.created),
    ),
  );
}
function jobBox(job) {
  const box = h('div', { class: 'card job' });
  const draw = async () => {
    let j;
    try {
      j = await api('/api/jobs/' + job.id);
    } catch (e) {
      return;
    }
    const pct = [...(j.log || '').matchAll(/(\d+)%/g)].map((m) => +m[1]).pop();
    render(
      box,
      h(
        'div',
        { class: 'spread' },
        h('b', {}, j.label),
        h(
          'span',
          {
            class:
              'chip st-' +
              { done: 'resolved', failed: 'open', running: 'planned', queued: 'planned' }[j.status],
          },
          j.status,
        ),
      ),
      j.status === 'running'
        ? h(
            'div',
            { class: 'bar' },
            h('i', { style: `width:${/roofing/.test(j.log) ? 92 : pct ? pct * 0.85 : 5}%` }),
          )
        : null,
      h(
        'pre',
        { class: 'log' },
        (j.log || '').trim().split('\n').slice(-8).join('\n') || 'waiting…',
      ),
      j.status === 'done' && j.slug
        ? h('a', { class: 'btn', href: '#/maps/' + j.slug }, 'Open the map →')
        : null,
    );
    if ((j.status === 'running' || j.status === 'queued') && box.isConnected)
      setTimeout(draw, 1500);
    else if (j.status === 'running' || j.status === 'queued') setTimeout(draw, 3000);
  };
  draw();
  return box;
}

/* ---------- pages ---------- */
async function prepPage(name) {
  const main = S.view;
  if (!name) {
    const names = S.state.prep;
    return go('#/prep/' + (names[names.length - 1] || 'new'), true);
  }
  if (name === 'new') return newPrep();
  const docName = 'prep/' + name;
  const p = await doc(docName, null);
  if (!p) {
    render(
      main,
      h('h1', {}, 'No such prep'),
      h('button', { onclick: newPrep }, 'Start the next session'),
    );
    return;
  }
  p.loot = p.loot || [];
  p.handouts = p.handouts || [];
  const threads = (await doc('threads', { threads: [] })).threads;
  await doc('codex', { entries: [] });
  const scenesBox = h('div');
  const drawScenes = () =>
    render(
      scenesBox,
      ...p.scenes.map((sc, i) =>
        h(
          'div',
          { class: 'card scene' + (sc.done ? ' done' : '') },
          h(
            'div',
            { class: 'spread' },
            h(
              'div',
              { class: 'row', style: 'flex:1' },
              h('input', {
                type: 'checkbox',
                checked: sc.done,
                title: 'Played',
                onchange: (e) => {
                  sc.done = e.target.checked;
                  save(docName);
                  drawScenes();
                },
              }),
              h(
                'div',
                { style: 'flex:1' },
                field(docName, sc, 'title', { placeholder: 'Scene title' }),
              ),
            ),
            h(
              'div',
              { class: 'row' },
              i > 0 &&
                h(
                  'button',
                  {
                    title: 'Move up',
                    onclick: () => {
                      p.scenes.splice(i - 1, 0, p.scenes.splice(i, 1)[0]);
                      save(docName);
                      drawScenes();
                    },
                  },
                  '↑',
                ),
              h(
                'button',
                {
                  class: 'danger',
                  onclick: () => {
                    if (confirm('Remove this scene?')) {
                      p.scenes.splice(i, 1);
                      save(docName);
                      drawScenes();
                    }
                  },
                },
                'Remove',
              ),
            ),
          ),
          h(
            'div',
            { class: 'two' },
            field(docName, sc, 'where', { label: 'Where' }),
            field(docName, sc, 'map', { label: 'Battle map / Foundry scene' }),
          ),
          h('label', {}, 'Who is there'),
          picker(docName, (sc.npcs = sc.npcs || []), codexChoices, {
            placeholder: 'Add from the codex…',
          }),
          field(docName, sc, 'encounter', {
            label: 'Encounter / challenge',
            type: 'textarea',
            rows: 2,
          }),
          field(docName, sc, 'notes', {
            label: 'Notes, read-aloud text, twists',
            type: 'textarea',
            rows: 4,
          }),
        ),
      ),
    );
  drawScenes();
  const others = S.state.prep.filter((n) => n !== name);
  render(
    main,
    h(
      'div',
      { class: 'spread' },
      h('h1', {}, p.title),
      h(
        'div',
        { class: 'row' },
        others.slice(-4).map((n) => h('a', { class: 'btn', href: '#/prep/' + n }, n.toUpperCase())),
        h('button', { onclick: newPrep }, '+ Next session'),
      ),
    ),
    h(
      'div',
      { class: 'three' },
      field(docName, p, 'title', { label: 'Title' }),
      field(docName, p, 'date', { label: 'Date', type: 'date' }),
      field(docName, p, 'status', {
        label: 'Status',
        type: 'select',
        options: ['planning', 'ready', 'played'],
      }),
    ),
    field(docName, p, 'recap', { label: 'Recap to open with', type: 'textarea', rows: 4 }),
    h(
      'div',
      { class: 'two' },
      h(
        'div',
        {},
        h('label', {}, 'Goals for the session'),
        listEditor(docName, p.goals, { placeholder: 'Add a goal and press Enter' }),
      ),
      h(
        'div',
        {},
        h('label', {}, 'Threads to push'),
        picker(docName, p.threads, threadChoices, { placeholder: 'Add a thread…' }),
        h(
          'div',
          { style: 'margin-top:8px' },
          p.threads
            .map((id) => threads.find((t) => t.id === id))
            .filter(Boolean)
            .map((t) =>
              h(
                'p',
                { class: 'clamp', style: '-webkit-line-clamp:2' },
                h('b', {}, t.title + ': '),
                t.detail,
              ),
            ),
        ),
      ),
    ),
    await makePanel(name),
    h('h2', {}, 'Scenes'),
    scenesBox,
    h(
      'button',
      {
        onclick: () => {
          p.scenes.push({
            id: uid('scene'),
            title: '',
            where: '',
            map: '',
            npcs: [],
            encounter: '',
            notes: '',
            done: false,
          });
          save(docName);
          drawScenes();
        },
      },
      '+ Add scene',
    ),
    h('h2', {}, 'Handouts for this session'),
    ...p.handouts.map((handout) =>
      h(
        'div',
        { class: 'card' },
        field(docName, handout, 'title', { label: 'Title' }),
        field(docName, handout, 'player_text', {
          label: 'Player text',
          type: 'textarea',
          rows: 5,
        }),
        field(docName, handout, 'secrets', {
          label: 'GM secrets',
          type: 'textarea',
          rows: 3,
        }),
        h(
          'button',
          {
            class: 'danger',
            onclick: () => {
              if (confirm('Remove this handout?')) {
                p.handouts.splice(p.handouts.indexOf(handout), 1);
                save(docName);
                route(true);
              }
            },
          },
          'Remove handout',
        ),
      ),
    ),
    h(
      'button',
      {
        onclick: () => {
          p.handouts.push({
            id: uid('handout'),
            title: '',
            player_text: '',
            secrets: '',
          });
          save(docName);
          route(true);
        },
      },
      '+ Add handout',
    ),
    h('h2', {}, 'Loot for this session'),
    rowsEditor(
      docName,
      p.loot,
      [
        ['item', 'Item', 3],
        ['where', 'Where / who has it', 2],
        ['value', 'Value', 1],
      ],
      { item: '', where: '', value: '' },
    ),
    h(
      'div',
      { class: 'two', style: 'margin-top:24px' },
      h(
        'div',
        {},
        h('h2', {}, 'Checklist'),
        listEditor(docName, p.checklist, {
          checklist: true,
          placeholder: 'Add a task and press Enter',
        }),
      ),
      h(
        'div',
        {},
        h('h2', {}, 'Loose notes'),
        field(docName, p, 'notes', { type: 'textarea', rows: 10 }),
      ),
    ),
  );
}

/* "Make for this session": requests tied to this prep, plus maps generated for it */
async function makePanel(session) {
  const box = await doc('inbox', { items: [] });
  const maps = (await doc('maps/index', { items: [] })).items.filter((m) => m.session === session);
  const panel = h('div', { class: 'card make' });
  const formBox = h('div');
  const list = h('div');
  const draw = () => {
    const mine = box.items.filter((it) => it.session === session);
    render(
      list,
      maps.length
        ? h(
            'div',
            { class: 'row', style: 'margin:8px 0' },
            h('span', { class: 'muted' }, 'Maps for this session:'),
            maps.map((m) =>
              h(
                'a',
                { class: 'chip', href: '#/maps/' + m.slug },
                m.name + (m.stocked ? ' · stocked' : ''),
              ),
            ),
          )
        : null,
      ...mine.map((it) => requestCard(it, box, draw)),
      mine.length || maps.length
        ? null
        : h('p', { class: 'muted' }, 'Nothing requested for this session yet.'),
    );
  };
  const hints = {
    npc: 'e.g. A harbour captain: proud, by the book and hiding a divided loyalty.',
    item: 'e.g. An enchanted reward for resolving the temple dispute.',
    encounter: 'e.g. A patrol boards the party’s ship; negotiation may avoid combat.',
    handout: 'e.g. A wanted poster issued after a controversial trial.',
    other: 'Anything else to prepare.',
  };
  const open = (kind) => {
    const ta = h('textarea', { rows: 3, placeholder: hints[kind] || '' });
    const submit = async (now) => {
      if (!ta.value.trim()) {
        ta.focus();
        return;
      }
      const item = await addRequest({ kind, text: ta.value.trim(), session });
      render(formBox);
      draw();
      if (now) await sendToClaude(item);
    };
    render(
      formBox,
      h(
        'div',
        { class: 'card', style: 'margin:10px 0;background:var(--bg2)' },
        h('b', {}, 'New ' + (KINDS[kind] || kind).toLowerCase()),
        ta,
        h(
          'div',
          { class: 'row', style: 'margin-top:8px' },
          h('button', { class: 'primary', onclick: () => submit(true) }, 'Draft with Claude'),
          h('button', { onclick: () => submit(false) }, 'Save for later'),
          h('button', { onclick: () => render(formBox) }, 'Close'),
        ),
      ),
    );
    ta.focus();
  };
  panel.append(
    h(
      'div',
      { class: 'spread' },
      h('h2', { style: 'margin:0' }, 'Make for this session'),
      h(
        'div',
        { class: 'row' },
        ['npc', 'item', 'encounter', 'handout', 'other'].map((k) =>
          h('button', { onclick: () => open(k) }, '+ ' + KINDS[k]),
        ),
        h('a', { class: 'btn', href: '#/maps/new' }, '+ Battle map'),
      ),
    ),
    h(
      'p',
      { class: 'muted', style: 'margin:6px 0 0' },
      'Describe what you need. Review the structured draft before adding it to the codex or this session prep. Use the map studio for maps and keyed locations.',
    ),
    formBox,
    list,
  );
  draw();
  return panel;
}

async function newPrep() {
  const names = S.state.prep;
  const last = names.length ? await doc('prep/' + names[names.length - 1], null) : null;
  const n = Math.max(lastSession().n, last ? last.n : 0) + 1;
  const name = 's' + n;
  if (!names.includes(name)) {
    S.docs['prep/' + name] = {
      n,
      title: 'Session ' + n,
      date: '',
      status: 'planning',
      recap: '',
      goals: [],
      threads: [],
      scenes: [],
      checklist: (last ? last.checklist : []).map((c) => ({ text: c.text, done: false })),
      notes: '',
      loot: [],
    };
    S.base['prep/' + name] = {};
    S.revs['prep/' + name] = '0';
    save('prep/' + name);
    names.push(name);
  }
  go('#/prep/' + name);
}

async function threadsPage() {
  const main = S.view;
  const docName = 'threads';
  const t = await doc(docName, { threads: [] });
  let filter = S.threadFilter || 'all';
  const list = h('div');
  const draw = () => {
    S.threadFilter = filter;
    const shown = t.threads.filter(
      (x) => filter === 'all' || x.status === filter || x.pcs.includes(filter),
    );
    render(
      list,
      ...THREAD_STATES.map((st) => {
        const group = shown.filter((x) => x.status === st);
        return group.length
          ? h(
              'div',
              {},
              h('h2', {}, st[0].toUpperCase() + st.slice(1)),
              group.map((x) =>
                h(
                  'div',
                  { class: 'card', style: 'margin-bottom:10px' },
                  h(
                    'div',
                    { class: 'spread' },
                    h('div', { style: 'flex:1' }, field(docName, x, 'title')),
                    field(docName, x, 'status', {
                      type: 'select',
                      options: THREAD_STATES,
                      onchange: draw,
                    }),
                    h(
                      'button',
                      {
                        class: 'danger',
                        onclick: () => {
                          if (confirm('Delete this thread?')) {
                            t.threads.splice(t.threads.indexOf(x), 1);
                            save(docName);
                            draw();
                          }
                        },
                      },
                      'Delete',
                    ),
                  ),
                  field(docName, x, 'detail', { type: 'textarea', rows: 2 }),
                  h(
                    'div',
                    { style: 'margin-top:6px' },
                    picker(docName, x.pcs, pcChoices, { placeholder: 'Tag a hero…', cls: 'pc' }),
                  ),
                  x.source &&
                    h(
                      'div',
                      { class: 'muted', style: 'font-size:12px;margin-top:4px' },
                      'From ' + x.source,
                    ),
                ),
              ),
            )
          : null;
      }),
    );
    filters
      .querySelectorAll('button')
      .forEach((b) => b.classList.toggle('on', b.dataset.f === filter));
  };
  const filters = h(
    'div',
    { class: 'filters' },
    ['all', ...THREAD_STATES, ...PCS.filter((pc) => t.threads.some((x) => x.pcs.includes(pc)))].map(
      (f) =>
        h(
          'button',
          {
            'data-f': f,
            onclick: () => {
              filter = f;
              draw();
            },
          },
          THREAD_STATES.includes(f) || f === 'all' ? f : pcName(f),
        ),
    ),
  );
  render(
    main,
    h(
      'div',
      { class: 'spread' },
      h('h1', {}, 'Threads'),
      h(
        'button',
        {
          class: 'primary',
          onclick: () => {
            t.threads.unshift({
              id: uid('thread'),
              title: 'New thread',
              pcs: [],
              status: 'open',
              detail: '',
              source: '',
            });
            save(docName);
            filter = 'all';
            draw();
          },
        },
        '+ New thread',
      ),
    ),
    h('p', { class: 'sub' }, 'Plot threads, promises and fortunes: what the story owes each hero.'),
    filters,
    list,
  );
  draw();
}

async function codexPage(id) {
  const main = S.view;
  const docName = 'codex';
  const c = await doc(docName, { entries: [] });
  if (id) return codexEntry(c, id, main);
  let type = S.codexType || 'all',
    q = S.codexQ || '';
  const grid = h('div', { class: 'grid codex' });
  const draw = () => {
    S.codexType = type;
    S.codexQ = q;
    const hits = c.entries.filter(
      (e) =>
        (type === 'all' || e.type === type) &&
        (e.name + ' ' + e.public + ' ' + e.secrets + ' ' + (e.tags || []).join(' '))
          .toLowerCase()
          .includes(q),
    );
    render(
      grid,
      ...hits.map((e) =>
        h(
          'div',
          { class: 'card link', onclick: () => go('#/codex/' + e.id) },
          e.image
            ? h('img', { class: 'thumb', src: fileUrl(e.image), loading: 'lazy', alt: '' })
            : h('div', { class: 'thumb' }),
          h(
            'div',
            {},
            h(
              'h3',
              {},
              e.name,
              e.status === 'dead'
                ? h('span', { class: 'chip dead', style: 'margin-left:6px' }, 'dead')
                : '',
            ),
            h(
              'div',
              { class: 'row' },
              h('span', { class: 'chip' }, e.type),
              (e.tags || []).slice(0, 2).map((t) => h('span', { class: 'chip' }, t)),
              e.secrets ? h('span', { class: 'chip dead' }, 'secrets') : '',
            ),
            h('p', { class: 'clamp' }, e.public || e.notes),
          ),
        ),
      ),
    );
    filters
      .querySelectorAll('button')
      .forEach((b) => b.classList.toggle('on', b.dataset.t === type));
  };
  const filters = h(
    'div',
    { class: 'filters' },
    ['all', ...Object.keys(TYPES).filter((t) => c.entries.some((e) => e.type === t))].map((t) =>
      h(
        'button',
        {
          'data-t': t,
          onclick: () => {
            type = t;
            draw();
          },
        },
        t === 'all' ? 'Everything' : TYPES[t],
      ),
    ),
    h('input', {
      type: 'search',
      placeholder: 'Search names, text, secrets…',
      value: q,
      oninput: (e) => {
        q = e.target.value.toLowerCase();
        draw();
      },
    }),
  );
  render(
    main,
    h(
      'div',
      { class: 'spread' },
      h('h1', {}, 'Codex'),
      h(
        'button',
        {
          class: 'primary',
          onclick: () => {
            const name = prompt('Name of the new entry?');
            if (!name) return;
            const e = {
              id:
                slug(name) +
                (c.entries.some((x) => x.id === slug(name)) ? '-' + Date.now().toString(36) : ''),
              type: type === 'all' ? 'npc' : type,
              name,
              group: '',
              status: '',
              public: '',
              secrets: '',
              notes: '',
              image: '',
              files: [],
              tags: [],
            };
            c.entries.push(e);
            save(docName);
            go('#/codex/' + e.id);
          },
        },
        '+ New entry',
      ),
    ),
    h(
      'p',
      { class: 'sub' },
      'People, places and objects in your campaign. “Known to players” is what the table has seen; secrets stay separate from player-facing text.',
    ),
    filters,
    grid,
  );
  draw();
}

async function codexEntry(c, id, main) {
  const docName = 'codex';
  const e = c.entries.find((x) => x.id === id);
  if (!e) {
    render(main, h('h1', {}, 'Not in the codex'), h('a', { href: '#/codex' }, 'Back to the codex'));
    return;
  }
  e.tags = e.tags || [];
  e.files = e.files || [];
  const img = h('div');
  const drawImg = () =>
    render(
      img,
      e.image
        ? h('img', {
            class: 'thumb big',
            src: fileUrl(e.image),
            alt: e.name,
            style: 'cursor:zoom-in',
            onclick: () => lightbox(e.image),
          })
        : h('div', { class: 'thumb big', style: 'height:200px' }),
    );
  drawImg();
  const files = h('div');
  for (const f of e.files) {
    const pre = h('pre', { class: 'file' }, 'Loading…');
    files.append(h('label', {}, f), pre);
    fetch(fileUrl(f))
      .then((r) => r.text())
      .then((t) => {
        pre.textContent = t.trim() || '(empty file)';
      });
  }
  const sessions = sessionsMentioning(e);
  render(
    main,
    h(
      'div',
      { class: 'spread' },
      h('h1', {}, e.name),
      h(
        'div',
        { class: 'row' },
        h('a', { class: 'btn', href: '#/codex' }, '← Codex'),
        h(
          'button',
          {
            class: 'danger',
            onclick: () => {
              if (confirm(`Delete ${e.name} from the codex?`)) {
                c.entries.splice(c.entries.indexOf(e), 1);
                save(docName);
                go('#/codex');
              }
            },
          },
          'Delete',
        ),
      ),
    ),
    h(
      'div',
      { class: 'detail' },
      h(
        'div',
        {},
        img,
        field(docName, e, 'image', {
          label: 'Image (path in the campaign folder)',
          placeholder: 'e.g. DM/uploads/portrait.png',
          onchange: drawImg,
        }),
        imagePicker((path) => {
          e.image = path;
          save(docName);
          drawImg();
        }, 'Upload portrait or item art'),
        h(
          'button',
          {
            onclick: () =>
              queueArt({
                codex: e.id,
                title: e.name,
                prompt: `Illustration of ${e.name}, ${e.public || e.notes || 'a character or object from this campaign'}. No text or lettering.`,
              }),
          },
          'Queue AI artwork',
        ),
        h(
          'div',
          { class: 'two' },
          field(docName, e, 'type', { label: 'Type', type: 'select', options: Object.keys(TYPES) }),
          field(docName, e, 'status', {
            label: 'Status',
            type: 'select',
            options: ['', 'alive', 'dead', 'missing', 'unknown'],
          }),
        ),
        field(docName, e, 'group', { label: 'Group / allegiance' }),
        h('label', {}, 'Tags'),
        listEditor(docName, e.tags, { placeholder: 'Add a tag and press Enter' }),
        h('label', {}, 'Mentioned in session summaries'),
        h(
          'div',
          { class: 'row' },
          sessions.length
            ? sessions.map((n) => h('span', { class: 'chip' }, 'S' + n))
            : h('span', { class: 'muted' }, 'none yet'),
        ),
      ),
      h(
        'div',
        {},
        field(docName, e, 'public', { label: 'Known to players', type: 'textarea', rows: 5 }),
        h(
          'div',
          { class: 'card secret', style: 'margin-top:12px' },
          field(docName, e, 'secrets', { label: 'Secrets (DM only)', type: 'textarea', rows: 5 }),
        ),
        field(docName, e, 'notes', {
          label: 'Notes, voice, mannerisms, stats',
          type: 'textarea',
          rows: 4,
        }),
        e.files.length ? h('h2', {}, 'Scripts and backstories') : null,
        files,
      ),
    ),
  );
}

/* ---------- battle maps ---------- */
function genForm({ session = '', compact = false } = {}) {
  const gens = S.state.generators || { city: 'City district' };
  const f = {
    type: Object.keys(gens)[0],
    name: '',
    width: 60,
    height: 60,
    density: 0.75,
    alleys: 0.6,
    canal: false,
    wall: false,
    market: true,
    cell: 150,
    darkness: 0.15,
    seed: '',
    session,
  };
  const jobs = h('div');
  const num = (key, label, min, max, step) => {
    const out = h('output', {}, ' ' + f[key]);
    return h(
      'div',
      {},
      h('label', {}, label, out),
      h('input', {
        type: 'range',
        min,
        max,
        step,
        value: f[key],
        oninput: (e) => {
          f[key] = +e.target.value;
          out.textContent = ' ' + f[key];
        },
      }),
    );
  };
  const wIn = h('input', {
    type: 'number',
    min: 20,
    max: 160,
    value: f.width,
    style: 'width:90px',
    oninput: (e) => {
      f.width = +e.target.value;
    },
  });
  const hIn = h('input', {
    type: 'number',
    min: 20,
    max: 160,
    value: f.height,
    style: 'width:90px',
    oninput: (e) => {
      f.height = +e.target.value;
    },
  });
  const box = h(
    'div',
    { class: compact ? '' : 'card' },
    compact ? null : h('h2', { style: 'margin-top:0' }, 'Generate a new map'),
    h(
      'div',
      { class: 'three' },
      h(
        'div',
        {},
        h('label', {}, 'Type'),
        h(
          'select',
          {
            onchange: (e) => {
              f.type = e.target.value;
            },
          },
          Object.entries(gens).map(([k, v]) => h('option', { value: k }, v)),
        ),
      ),
      h(
        'div',
        {},
        h('label', {}, 'Name'),
        h('input', {
          type: 'text',
          placeholder: 'e.g. Harbour district',
          oninput: (e) => {
            f.name = e.target.value;
          },
        }),
      ),
      h(
        'div',
        {},
        h('label', {}, 'For session'),
        h(
          'select',
          {
            onchange: (e) => {
              f.session = e.target.value;
            },
          },
          h('option', { value: '' }, '—'),
          S.state.prep.map((n) =>
            h('option', { value: n, selected: n === session }, n.toUpperCase()),
          ),
        ),
      ),
    ),
    h('label', {}, 'Size in 5 ft squares'),
    h(
      'div',
      { class: 'row' },
      wIn,
      '×',
      hIn,
      [
        [40, 40],
        [60, 60],
        [80, 80],
        [120, 80],
        [120, 120],
      ].map(([w, hh]) =>
        h(
          'button',
          {
            class: 'small',
            onclick: () => {
              f.width = w;
              f.height = hh;
              wIn.value = w;
              hIn.value = hh;
            },
          },
          `${w}×${hh}`,
        ),
      ),
    ),
    h(
      'div',
      { class: 'three' },
      num('density', 'How built-up', 0.2, 1, 0.05),
      num('alleys', 'Back alleys', 0, 1, 0.05),
      num('darkness', 'Darkness', 0, 1, 0.05),
    ),
    h(
      'div',
      { class: 'row', style: 'margin-top:10px' },
      ...[
        ['canal', 'Canal with bridges'],
        ['wall', 'City wall, towers and gates'],
        ['market', 'Market square'],
      ].map(([k, label]) =>
        h(
          'label',
          { class: 'radio' },
          h('input', {
            type: 'checkbox',
            checked: f[k],
            onchange: (e) => {
              f[k] = e.target.checked;
            },
          }),
          label,
        ),
      ),
      h(
        'label',
        { class: 'radio' },
        'Pixels per square',
        h(
          'select',
          {
            onchange: (e) => {
              f.cell = +e.target.value;
            },
          },
          [100, 150].map((v) => h('option', { value: v, selected: v === 150 }, v)),
        ),
      ),
      h(
        'label',
        { class: 'radio' },
        'Seed',
        h('input', {
          type: 'text',
          placeholder: 'random',
          style: 'width:90px',
          oninput: (e) => {
            f.seed = e.target.value;
          },
        }),
      ),
    ),
    compact
      ? null
      : h(
          'div',
          { class: 'row', style: 'margin-top:12px' },
          h('button', { class: 'primary', onclick: () => box.run() }, 'Generate'),
          h(
            'span',
            { class: 'muted' },
            'A 60×60 district takes about 3 minutes; 120×120 about 12.',
          ),
        ),
    jobs,
  );
  box.run = async () => {
    try {
      const job = await post('/api/generate', f);
      jobs.prepend(jobBox(job));
      poll();
    } catch (e) {
      alert(e.message);
    }
  };
  return box;
}

async function handoutsPage() {
  const main = S.view;
  render(main, h('h1', {}, 'Handouts'), h('p', { class: 'sub' }, 'Loading uploaded handouts…'));
  const imgs = await api('/api/images?dir=DM/uploads');
  let q = '';
  const gallery = h('div', { class: 'gallery' });
  const draw = () =>
    render(
      gallery,
      ...imgs
        .filter((i) => i.path.toLowerCase().includes(q))
        .sort((a, b) => b.mtime - a.mtime)
        .map((i) =>
          h(
            'figure',
            { onclick: () => lightbox(i.path) },
            h('img', { src: fileUrl(i.path), loading: 'lazy', alt: '' }),
            h('figcaption', {}, i.path.replace(/^DM\/uploads\//, '')),
          ),
        ),
    );
  render(
    main,
    h('h1', {}, 'Handouts'),
    h('p', { class: 'sub' }, `${imgs.length} uploaded images, newest first.`),
    h(
      'div',
      { class: 'filters' },
      h('input', {
        type: 'search',
        placeholder: 'Filter by file name…',
        oninput: (e) => {
          q = e.target.value.toLowerCase();
          draw();
        },
      }),
    ),
    gallery,
  );
  draw();
}

async function inboxPage() {
  const main = S.view;
  const box = await doc('inbox', { items: [] });
  const list = h('div');
  const draw = () =>
    render(
      list,
      ...box.items.map((it) => requestCard(it, box, draw)),
      box.items.length
        ? null
        : h(
            'p',
            { class: 'muted' },
            "No requests yet. Add one here, or from a session's prep page.",
          ),
    );
  const add = h(
    'select',
    {
      style: 'width:auto',
      onchange: async (e) => {
        if (!e.target.value) return;
        await addRequest({ kind: e.target.value });
        e.target.value = '';
        draw();
        list.querySelector('textarea')?.focus();
      },
    },
    h('option', { value: '' }, '+ New request…'),
    Object.entries(KINDS)
      .filter(([k]) => !['battle map', 'stock map', 'event', 'journal'].includes(k))
      .map(([k, v]) => h('option', { value: k }, v)),
  );
  render(
    main,
    h('div', { class: 'spread' }, h('h1', {}, 'Requests'), add),
    h(
      'p',
      { class: 'sub' },
      S.state.claude
        ? 'Requests become structured drafts for you to review before applying. Claude can draft in the background; prompt packs work with other assistants too.'
        : 'Requests become structured drafts for you to review before applying. Export a prompt pack and import a proposal from another assistant.',
    ),
    list,
  );
  draw();
}

function notesPage() {
  const main = S.view;
  render(
    main,
    h('h1', {}, 'Campaign notes'),
    h(
      'p',
      { class: 'sub' },
      'Read-only notes from DM/data/notes.txt. Create this file locally to add reference notes.',
    ),
    h('pre', { class: 'notes' }, S.state.notes),
  );
}

function lightbox(path) {
  const lb = $('#lightbox');
  $('img', lb).src = fileUrl(path);
  $('p', lb).textContent = path;
  lb.hidden = false;
}
$('#lightbox').addEventListener('click', () => {
  $('#lightbox').hidden = true;
});

/* ---------- router ---------- */
function go(hash, replace) {
  if (replace) {
    history.replaceState(null, '', hash);
    route();
  } else location.hash = hash;
}
async function route(soft) {
  const [page, arg] = location.hash.replace(/^#\/?/, '').split('/').map(decodeURIComponent);
  document
    .querySelectorAll('#side [data-nav]')
    .forEach((a) =>
      a.classList.toggle(
        'on',
        a.getAttribute('href') === '#/' + (page || '') || a.dataset.nav === page,
      ),
    );
  const token = (S.route = (S.route || 0) + 1);
  const view = (S.view = h('div'));
  const y = window.scrollY;
  try {
    await (
      {
        '': studioDashboard,
        prep: prepPage,
        threads: threadsPage,
        codex: codexPage,
        maps: studioMaps,
        art: studioArt,
        settings: studioSettings,
        handouts: handoutsPage,
        inbox: inboxPage,
        notes: notesPage,
      }[page || ''] || studioDashboard
    )(arg);
  } catch (e) {
    render(
      view,
      h('h1', {}, 'Something went wrong'),
      h('pre', { class: 'file' }, e.stack || String(e)),
    );
  }
  if (token !== S.route) return; // the DM has already moved on
  // keep open <details> open across a refresh
  const open =
    soft === true
      ? [...root.querySelectorAll('details[open] > summary b')].map((s) => s.textContent)
      : [];
  render(root, view);
  if (soft === true) {
    view.querySelectorAll('details').forEach((d) => {
      if (open.includes(d.querySelector('summary b')?.textContent)) d.open = true;
    });
    window.scrollTo(0, y);
  } else window.scrollTo(0, 0);
}
window.addEventListener('hashchange', () => route());
window.addEventListener('DOMContentLoaded', async () => {
  try {
    S.state = await api('/api/state');
    S.jobs = await api('/api/jobs').catch(() => []);
    const c = await doc('codex', { entries: [] });
    PCS = [
      ...new Set([
        ...S.state.public.heroes.map((h) => h.id),
        ...c.entries.filter((e) => e.type === 'pc').map((e) => e.id),
      ]),
    ];
    initStudio();
    route();
  } catch (e) {
    render(root, h('h1', {}, 'Could not connect'), h('p', {}, e.message));
  }
});
