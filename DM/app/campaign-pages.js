'use strict';

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
  expand: 'Expand an entry',
};

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
      art.items.unshift(
        blank('art_item', {
          id: uid('art'),
          prompt: f.prompt.trim(),
          title: f.title,
          codex,
          created: Date.now(),
          map,
          area,
        }),
      );
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

const codexName = (id) => S.docs.codex?.entries.find((x) => x.id === id)?.name || id;

/* One button on an entry: reuse its open expansion request, or start one, and draft it. */
async function expandEntry(e) {
  if (S.pending.codex) await flush('codex');
  const box = await doc('inbox', { items: [] });
  let item = box.items.find((it) => it.kind === 'expand' && it.codex === e.id && !it.applied);
  if (!item) {
    item = await addRequest({
      kind: 'expand',
      codex: e.id,
      text: `Expand the codex entry "${e.name}": add detail and secrets that fit the campaign, an illustration brief, and any related entries that deepen it.`,
    });
  }
  if (item.status === 'new') await sendToAI(item);
  go('#/inbox');
}

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

function codexImageUrl(entry) {
  if (!entry.image) return null;
  const imported = entry.foundry;
  if (imported?.image === entry.image) {
    if (!imported.world_key || imported.world_key !== S.state.world_key) return null;
    return (
      '/api/foundry/asset?' +
      new URLSearchParams({ path: entry.image, world_key: imported.world_key })
    );
  }
  return fileUrl(entry.image);
}

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
      context_pins: [],
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
async function sendToAI(item) {
  if (!S.state.ai.available) {
    alert(
      `${S.state.ai.label} is unavailable. Export the prompt pack and import a proposal from another assistant.`,
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
        ? `queued for ${S.state.ai.label}`
        : `${S.state.ai.label} is drafting…`
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
        it.kind === 'expand' && it.codex
          ? h('a', { class: 'chip', href: '#/codex/' + it.codex }, 'entry: ' + codexName(it.codex))
          : null,
      ),
      h(
        'div',
        { class: 'row' },
        isMap
          ? h('a', { class: 'btn primary', href: '#/maps/new' }, 'Open map studio')
          : it.status !== 'doing' && it.status !== 'done'
            ? h(
                'button',
                { class: 'primary', onclick: () => sendToAI(it) },
                it.status === 'review' ? 'Draft again' : `Draft with ${S.state.ai.label}`,
              )
            : null,
        !isMap && it.status !== 'doing' && it.status !== 'done'
          ? h(
              'button',
              {
                onclick: () =>
                  attempt(async () => {
                    if (S.pending.inbox) await flush('inbox');
                    await contextPreview(
                      '/api/requests/' + it.id + '/pack',
                      '/api/requests/' + it.id + '/context',
                      async () => {
                        await load('inbox', { items: [] });
                        route(true);
                      },
                    );
                  }),
              },
              'Preview context',
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
/* A live job card for the current page. It stops polling once the page's view context is aborted. */
function jobBox(job, signal) {
  const box = h('div', { class: 'card job' });
  const draw = async () => {
    if (signal?.aborted) return;
    let j;
    try {
      j = await api('/api/jobs/' + job.id, { signal });
    } catch (e) {
      return;
    }
    if (signal?.aborted) return;
    const pct = j.progress ? j.progress.percent : null;
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
            h('i', { style: `width:${pct === null ? 5 : Math.max(5, pct)}%` }),
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
      j.status === 'running' || j.status === 'queued'
        ? h(
            'button',
            {
              class: 'btn',
              onclick: () =>
                post('/api/jobs/' + j.id + '/cancel').then(draw, (e) => toast(e.message, true)),
            },
            'Cancel',
          )
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
async function prepPage(name, context) {
  const main = context.view;
  if (!name) {
    const names = activePreps();
    return go('#/prep/' + (names[names.length - 1] || 'new'), true);
  }
  if (name === 'new') return newPrep(context);
  const docName = 'prep/' + name;
  const p = await context.doc(docName, null);
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
  p.log = Object.assign({ summary: '', notes: '', outcomes: [] }, p.log);
  const threads = (await context.doc('threads', { threads: [] })).threads;
  await context.doc('codex', { entries: [] });
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
  const others = activePreps().filter((n) => n !== name);
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
        h(
          'button',
          {
            title: p.archived
              ? 'Bring this session back into the active list'
              : 'Hide this session from the active list; nothing is deleted',
            onclick: async () => {
              p.archived = !p.archived;
              await flush(docName);
              const set = S.state.prep_archived || (S.state.prep_archived = []);
              if (p.archived) set.push(name);
              else S.state.prep_archived = set.filter((n) => n !== name);
              go('#/prep', true);
            },
          },
          p.archived ? 'Restore session' : 'Archive session',
        ),
      ),
    ),
    (S.state.prep_archived || []).filter((n) => n !== name).length
      ? h(
          'div',
          { class: 'row' },
          h('span', { class: 'muted' }, 'Archived sessions'),
          S.state.prep_archived
            .filter((n) => n !== name)
            .map((n) => h('a', { class: 'btn', href: '#/prep/' + n }, n.toUpperCase())),
        )
      : null,
    p.archived
      ? h(
          'div',
          { class: 'card' },
          'This session is archived. Restore it to bring it back to the list.',
        )
      : null,
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
    await makePanel(name, context),
    h('h2', {}, 'Session log'),
    h(
      'p',
      { class: 'muted' },
      'What actually happened when this session was played. Later AI drafts read the newest logs as canon.',
    ),
    field(docName, p.log, 'summary', { label: 'What the players did', type: 'textarea', rows: 5 }),
    field(docName, p.log, 'notes', {
      label: 'GM notes (not for players)',
      type: 'textarea',
      rows: 3,
    }),
    h('label', {}, 'Outcomes: changes that now stand'),
    listEditor(docName, p.log.outcomes, { placeholder: 'Add an outcome and press Enter' }),
    h('h2', {}, 'Scenes'),
    scenesBox,
    h(
      'div',
      { class: 'prep-section-action' },
      h(
        'button',
        {
          onclick: () => {
            p.scenes.push(blank('scene', { id: uid('scene') }));
            save(docName);
            drawScenes();
          },
        },
        '+ Add scene',
      ),
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
      'div',
      { class: 'prep-section-action' },
      h(
        'button',
        {
          onclick: () => {
            p.handouts.push(blank('handout', { id: uid('handout') }));
            save(docName);
            route(true);
          },
        },
        '+ Add handout',
      ),
    ),
    h('h2', {}, 'Loot for this session'),
    h(
      'div',
      { class: 'prep-section-action' },
      rowsEditor(
        docName,
        p.loot,
        [
          ['item', 'Item', 3],
          ['where', 'Where / who has it', 2],
          ['value', 'Value', 1],
        ],
        () => blank('loot'),
      ),
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
        field(docName, p, 'notes', { type: 'textarea', rows: 10, ariaLabel: 'Loose notes' }),
      ),
    ),
  );
}

/* "Make for this session": requests tied to this prep, plus maps generated for it */
async function makePanel(session, context) {
  const box = await context.doc('inbox', { items: [] });
  const maps = (await context.doc('maps/index', { items: [] })).items.filter(
    (m) => m.session === session,
  );
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
      if (now) await sendToAI(item);
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
          h(
            'button',
            { class: 'primary', onclick: () => submit(true) },
            `Draft with ${S.state.ai.label}`,
          ),
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

/* Session names not archived, oldest first. Archived preps stay in `S.state.prep` for numbering. */
function activePreps() {
  const archived = S.state.prep_archived || [];
  return S.state.prep.filter((n) => !archived.includes(n));
}

async function newPrep(context) {
  const names = S.state.prep;
  const active = activePreps();
  const last = active.length ? await context.doc('prep/' + active[active.length - 1], null) : null;
  const existingNumbers = names.map((name) => Number(/^s(\d+)$/.exec(name)?.[1] || 0));
  const n = Math.max(lastSession().n, ...existingNumbers, last ? last.n : 0) + 1;
  const name = 's' + n;
  if (!names.includes(name)) {
    S.docs['prep/' + name] = blank('prep', {
      n,
      title: 'Session ' + n,
      checklist: (last ? last.checklist : []).map((c) => blank('checklist_item', { text: c.text })),
    });
    S.base['prep/' + name] = {};
    S.revs['prep/' + name] = '0';
    save('prep/' + name);
    names.push(name);
  }
  go('#/prep/' + name);
}

async function threadsPage(_arg, context) {
  const main = context.view;
  const docName = 'threads';
  const t = await context.doc(docName, { threads: [] });
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
            t.threads.unshift(blank('thread', { id: uid('thread'), title: 'New thread' }));
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

async function codexPage(id, context) {
  const main = context.view;
  const docName = 'codex';
  const c = await context.doc(docName, { entries: [] });
  if (id) return codexEntry(c, id, main, context);
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
      ...hits.map((e) => {
        const imageUrl = codexImageUrl(e);
        return h(
          'div',
          { class: 'card link', onclick: () => go('#/codex/' + e.id) },
          imageUrl
            ? h('img', { class: 'thumb', src: imageUrl, loading: 'lazy', alt: '' })
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
        );
      }),
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
            const e = blank('codex_entry', {
              id:
                slug(name) +
                (c.entries.some((x) => x.id === slug(name)) ? '-' + Date.now().toString(36) : ''),
              type: type === 'all' ? 'npc' : type,
              name,
            });
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

async function codexEntry(c, id, main, context) {
  const docName = 'codex';
  const e = c.entries.find((x) => x.id === id);
  if (!e) {
    render(main, h('h1', {}, 'Not in the codex'), h('a', { href: '#/codex' }, 'Back to the codex'));
    return;
  }
  e.tags = e.tags || [];
  e.files = e.files || [];
  const img = h('div');
  const drawImg = () => {
    const imageUrl = codexImageUrl(e);
    render(
      img,
      imageUrl
        ? h('img', {
            class: 'thumb big',
            src: imageUrl,
            alt: e.name,
            style: 'cursor:zoom-in',
            onclick: () => lightbox(e.image, imageUrl),
          })
        : h('div', { class: 'thumb big', style: 'height:200px' }),
    );
  };
  drawImg();
  const files = h('div');
  for (const f of e.files) {
    const pre = h('pre', { class: 'file' }, 'Loading…');
    files.append(h('label', {}, f), pre);
    fetch(fileUrl(f), { signal: context.signal })
      .then((r) => r.text())
      .then((t) => {
        pre.textContent = t.trim() || '(empty file)';
      })
      .catch(() => {
        if (!context.signal.aborted) pre.textContent = 'File unavailable';
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
            onclick: () => deleteCodexEntry(e, docName),
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
          label:
            e.foundry?.image === e.image
              ? 'Foundry image (upload a Studio image to replace it)'
              : 'Image (path in the campaign folder)',
          placeholder: 'e.g. ' + S.state.uploads_dir + '/portrait.png',
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
        h('button', { onclick: () => expandEntry(e) }, 'Draft related content'),
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
        e.related?.length
          ? [
              h('label', {}, 'Related entries'),
              h(
                'div',
                { class: 'row' },
                e.related.map((id) =>
                  h('a', { class: 'chip', href: '#/codex/' + id }, codexName(id)),
                ),
              ),
            ]
          : null,
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

async function handoutsPage(_arg, context) {
  const main = context.view;
  render(main, h('h1', {}, 'Handouts'), h('p', { class: 'sub' }, 'Loading uploaded handouts…'));
  const imgs = await context.api('/api/images?dir=' + encodeURIComponent(S.state.uploads_dir));
  const uploadsPrefix = S.state.uploads_dir + '/';
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
            h(
              'figcaption',
              {},
              i.path.startsWith(uploadsPrefix) ? i.path.slice(uploadsPrefix.length) : i.path,
            ),
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

async function inboxPage(_arg, context) {
  const main = context.view;
  const box = await context.doc('inbox', { items: [] });
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
      'aria-label': 'New request',
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
      .filter(([k]) => !['battle map', 'stock map', 'event', 'journal', 'expand'].includes(k))
      .map(([k, v]) => h('option', { value: k }, v)),
  );
  render(
    main,
    h('div', { class: 'spread' }, h('h1', {}, 'Requests'), add),
    h(
      'p',
      { class: 'sub' },
      S.state.ai.available
        ? `Requests become structured drafts for you to review before applying. ${S.state.ai.label} can draft in the background; prompt packs work with other assistants too.`
        : 'Requests become structured drafts for you to review before applying. Export a prompt pack and import a proposal from another assistant.',
    ),
    list,
  );
  draw();
}

function notesPage(_arg, context) {
  const main = context.view;
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

/* Delete a codex entry on the server, which also unlinks it from every thread, scene, area and request. */
async function deleteCodexEntry(e, docName) {
  try {
    await flush(docName); // so the server sees the edits made so far
    const id = encodeURIComponent(e.id);
    const { uses } = await api(`/api/codex/${id}/uses`);
    const shown = uses.slice(0, 8).map(
      (u) => `
- ${u.where}`,
    );
    const more =
      uses.length > 8
        ? `
…and ${uses.length - 8} more`
        : '';
    const used = uses.length
      ? `

It is used in ${uses.length} place(s); those links will be removed:${shown.join('')}${more}`
      : '';
    if (!confirm(`Delete ${e.name} from the codex?${used}`)) return;
    const { changed } = await post(`/api/codex/${id}/delete`);
    for (const name of changed) {
      delete S.docs[name];
      delete S.base[name];
      delete S.revs[name];
    }
    go('#/codex');
  } catch (error) {
    alert('Not deleted: ' + error.message);
  }
}
