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
  lore: 'Lore',
};
const KINDS = {
  session: 'Plan whole session',
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
    const name = recordName('codex', item.codex);
    const entry = await doc(name, null);
    if (entry) {
      entry.image = path;
      save(name);
    }
  }
  refreshSoon();
}

const codexName = (id) => S.recordIndex.codex.find((x) => x.id === id)?.name || id;

/* One button on an entry: reuse its open expansion request, or start one, and draft it. */
async function expandEntry(e) {
  const name = recordName('codex', e.id);
  if (S.pending[name]) await flush(name);
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
  S.recordIndex.codex.map((e) => ({
    id: e.id,
    name: e.name + (e.type === 'npc' ? '' : ' · ' + e.type),
  }));
const threadChoices = () => S.recordIndex.threads.map((t) => ({ id: t.id, name: t.title }));
const hero = (id) =>
  S.state.public.heroes.find((x) => x.id === id) ||
  S.recordIndex.codex.find((x) => x.id === id) || { name: id };
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
    rejectedItems.delete(item.id);
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
      rejectedItems.delete(item.id);
      await load('inbox', { items: [] });
      route(true);
    },
    'Validate & review',
  );
}
/* Items of a proposal the GM has unticked, by request ID, as the `kind:id` keys the server accepts. */
const rejectedItems = new Map();
const REVIEWED_KINDS = ['entries', 'threads', 'scenes', 'handouts'];
function itemChoice(item, kind, row) {
  const key = kind + ':' + row.id;
  const rejected = rejectedItems.get(item.id) || new Set();
  return h(
    'div',
    { class: 'card', style: 'margin:4px 0' },
    h(
      'label',
      { class: 'row' },
      h('input', {
        type: 'checkbox',
        checked: !rejected.has(key),
        onchange: (event) => {
          if (event.target.checked) rejected.delete(key);
          else rejected.add(key);
          rejectedItems.set(item.id, rejected);
        },
      }),
      h('b', {}, row.name || row.title || row.id),
      h('span', { class: 'muted' }, 'Include when applying'),
    ),
    h('pre', { class: 'file', tabindex: 0 }, JSON.stringify(row, null, 2)),
  );
}
async function applyRequestProposal(item) {
  if (S.pending.inbox) await flush('inbox');
  try {
    await post('/api/requests/' + item.id + '/apply', {
      rejected: [...(rejectedItems.get(item.id) || [])],
    });
    rejectedItems.delete(item.id);
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
    { class: 'card', style: 'margin-bottom:10px' },
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
                    await addRequest({
                      kind: it.kind,
                      session: it.session || '',
                      ...(it.kind === 'session' ? { settings: it.settings } : {}),
                    });
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
      rows: it.kind === 'session' ? 5 : 2,
      placeholder:
        it.kind === 'session' ? 'What is the next session about?' : 'What do you want made?',
    }),
    it.kind === 'session' && it.settings
      ? h(
          'p',
          { class: 'muted' },
          `${it.settings.hours} hours · combat ${it.settings.combat}/5 · social ${it.settings.social}/5`,
        )
      : null,
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
      ? h('div', {}, h('label', {}, 'Result'), h('pre', { class: 'file', tabindex: 0 }, it.result))
      : null,
    it.error ? h('p', { class: 'err' }, it.error) : null,
    it.created_maps?.length
      ? h(
          'div',
          { class: 'row' },
          h('span', { class: 'muted' }, 'Map briefs:'),
          it.created_maps.map((map) =>
            h('a', { class: 'chip', href: '#/maps/' + map.slug }, map.name),
          ),
        )
      : null,
    it.status === 'review' && it.draft
      ? h(
          'details',
          { open: true },
          h('summary', {}, 'Review additions'),
          ...[
            'recap',
            'goals',
            'maps',
            'entries',
            'threads',
            'thread_changes',
            'scenes',
            'handouts',
            'loot',
            'checklist',
            'notes',
          ]
            .filter((key) => it.draft[key]?.length)
            .map((key) =>
              h(
                'div',
                {},
                h('b', {}, key),
                it.kind !== 'session' && REVIEWED_KINDS.includes(key)
                  ? it.draft[key].map((row) => itemChoice(it, key, row))
                  : h(
                      'pre',
                      { class: 'file', tabindex: 0 },
                      JSON.stringify(it.draft[key], null, 2),
                    ),
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
  const threads = await recordChoices('threads', context.signal);
  await recordChoices('codex', context.signal);
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
          sc.map ? h('a', { class: 'chip', href: '#/maps/' + sc.map }, 'Open linked map') : null,
          field(docName, sc, 'purpose', { label: 'Purpose of this scene' }),
          h(
            'label',
            {},
            'Map location number (0 until a new map is keyed)',
            h('input', {
              type: 'number',
              min: 0,
              value: sc.area || 0,
              oninput: (e) => {
                sc.area = Math.max(0, Number(e.target.value) || 0);
                save(docName);
              },
            }),
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
          sc.encounter_detail
            ? h(
                'details',
                {},
                h('summary', {}, 'Encounter plan'),
                h(
                  'div',
                  { class: 'two' },
                  field(docName, sc.encounter_detail, 'difficulty', { label: 'Target difficulty' }),
                  field(docName, sc.encounter_detail, 'terrain', { label: 'Terrain' }),
                ),
                field(docName, sc.encounter_detail, 'tactics', {
                  label: 'Tactics',
                  type: 'textarea',
                  rows: 2,
                }),
                field(docName, sc.encounter_detail, 'resolution', {
                  label: 'Ways this can end',
                  type: 'textarea',
                  rows: 2,
                }),
                ...sc.encounter_detail.creatures.map((creature) =>
                  h(
                    'div',
                    { class: 'row' },
                    field(docName, creature, 'name', { label: 'Creature' }),
                    h(
                      'label',
                      {},
                      'Count',
                      h('input', {
                        type: 'number',
                        min: 1,
                        max: 100,
                        value: creature.count,
                        oninput: (e) => {
                          creature.count = Math.max(1, Number(e.target.value) || 1);
                          save(docName);
                        },
                      }),
                    ),
                    h(
                      'button',
                      {
                        class: 'danger',
                        onclick: () => {
                          sc.encounter_detail.creatures.splice(
                            sc.encounter_detail.creatures.indexOf(creature),
                            1,
                          );
                          save(docName);
                          drawScenes();
                        },
                      },
                      'Remove creature',
                    ),
                  ),
                ),
                h(
                  'button',
                  {
                    onclick: () => {
                      sc.encounter_detail.creatures.push({ name: '', count: 1 });
                      save(docName);
                      drawScenes();
                    },
                  },
                  '+ Creature',
                ),
              )
            : null,
          sc.clues?.length
            ? h(
                'div',
                {},
                h('label', {}, 'Thread clues'),
                ...sc.clues.map((clue) =>
                  h(
                    'div',
                    { class: 'row' },
                    h(
                      'select',
                      {
                        'aria-label': 'Clue thread',
                        onchange: (e) => {
                          clue.thread = e.target.value;
                          save(docName);
                        },
                      },
                      threadChoices().map((t) =>
                        h(
                          'option',
                          {
                            value: t.id,
                            selected: t.id === clue.thread,
                          },
                          t.name,
                        ),
                      ),
                    ),
                    field(docName, clue, 'text', { label: 'Clue' }),
                    h(
                      'button',
                      {
                        class: 'danger',
                        onclick: () => {
                          sc.clues.splice(sc.clues.indexOf(clue), 1);
                          save(docName);
                          drawScenes();
                        },
                      },
                      'Remove clue',
                    ),
                  ),
                ),
              )
            : null,
          h(
            'button',
            {
              onclick: () => {
                sc.clues ||= [];
                sc.clues.push(blank('scene_clue'));
                save(docName);
                drawScenes();
              },
            },
            '+ Thread clue',
          ),
          field(docName, sc, 'read_aloud', { label: 'Read-aloud text', type: 'textarea', rows: 3 }),
          field(docName, sc, 'notes', {
            label: 'GM notes and twists',
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
    field(docName, p, 'pitch', { label: 'Session pitch', type: 'textarea', rows: 4 }),
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
        field(docName, handout, 'image_prompt', {
          label: 'Image brief',
          type: 'textarea',
          rows: 2,
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
  const prep = await context.doc('prep/' + session, null);
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
    session:
      'e.g. The party follows the smuggler lead into the flooded quarter, where the watch captain needs their help.',
    npc: 'e.g. A harbour captain: proud, by the book and hiding a divided loyalty.',
    item: 'e.g. An enchanted reward for resolving the temple dispute.',
    encounter: 'e.g. A patrol boards the party’s ship; negotiation may avoid combat.',
    handout: 'e.g. A wanted poster issued after a controversial trial.',
    other: 'Anything else to prepare.',
  };
  const open = (kind) => {
    const ta = h('textarea', { rows: 3, placeholder: hints[kind] || '' });
    const settings = {
      hours: 4,
      combat: 2,
      social: 2,
      threads: [...(prep?.threads || [])].slice(0, 12),
    };
    const threadBox = h('div');
    const drawThreads = () =>
      render(
        threadBox,
        h('label', {}, 'Threads to push'),
        h(
          'div',
          { class: 'row' },
          settings.threads.map((id) =>
            h(
              'button',
              {
                class: 'small',
                onclick: () => {
                  settings.threads = settings.threads.filter((value) => value !== id);
                  drawThreads();
                },
              },
              'Remove ' + (threadChoices().find((t) => t.id === id)?.name || id),
            ),
          ),
        ),
        h(
          'select',
          {
            'aria-label': 'Add a thread to the session',
            onchange: (e) => {
              if (
                e.target.value &&
                !settings.threads.includes(e.target.value) &&
                settings.threads.length < 12
              )
                settings.threads.push(e.target.value);
              drawThreads();
            },
          },
          h('option', { value: '' }, 'Add a thread…'),
          threadChoices()
            .filter((t) => !settings.threads.includes(t.id))
            .map((t) => h('option', { value: t.id }, t.name)),
        ),
      );
    if (kind === 'session') drawThreads();
    const submit = async (now) => {
      if (!ta.value.trim()) {
        ta.focus();
        return;
      }
      const item = await addRequest({
        kind,
        text: ta.value.trim(),
        session,
        ...(kind === 'session' ? { settings } : {}),
      });
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
        kind === 'session'
          ? h(
              'div',
              {},
              h(
                'p',
                { class: 'muted' },
                'One pitch drafts a recap, linked scenes, map briefs, cast, handouts and thread changes for review.',
              ),
              h(
                'div',
                { class: 'three' },
                formInput(settings, 'hours', 'Length (hours)', { type: 'number', min: 1, max: 12 }),
                formInput(settings, 'combat', 'Combat focus (0–5)', {
                  type: 'number',
                  min: 0,
                  max: 5,
                }),
                formInput(settings, 'social', 'Social focus (0–5)', {
                  type: 'number',
                  min: 0,
                  max: 5,
                }),
              ),
              threadBox,
            )
          : null,
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
        h('button', { class: 'primary', onclick: () => open('session') }, 'Plan whole session'),
        ['npc', 'item', 'encounter', 'handout', 'other'].map((k) =>
          h('button', { onclick: () => open(k) }, '+ ' + KINDS[k]),
        ),
        h('a', { class: 'btn', href: '#/maps/new' }, '+ Battle map'),
      ),
    ),
    h(
      'p',
      { class: 'muted', style: 'margin:6px 0 0' },
      'Draft a complete session from one pitch, or request individual pieces. Review every proposal before applying it. New map briefs wait in the map workflow.',
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
  await recordChoices('threads', context.signal);
  let filter = S.threadFilter || 'all';
  let offset = 0;
  const limit = 30;
  let result = { items: [], total: 0 };
  const list = h('div');
  const pages = h('div', { class: 'row' });
  const draw = () => {
    S.threadFilter = filter;
    const shown = result.items.map(({ record: x, rev }) => {
      const name = recordName('threads', x.id);
      if (!S.pending[name]) {
        S.docs[name] = x;
        S.base[name] = clone(x);
        S.revs[name] = rev;
      }
      return S.docs[name];
    });
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
                    h(
                      'div',
                      { style: 'flex:1' },
                      field(recordName('threads', x.id), x, 'title', { ariaLabel: 'Thread title' }),
                    ),
                    field(recordName('threads', x.id), x, 'status', {
                      ariaLabel: 'Thread status',
                      type: 'select',
                      options: THREAD_STATES,
                      onchange: () => recordChoices('threads').then(draw),
                    }),
                    h(
                      'button',
                      {
                        class: 'danger',
                        onclick: () =>
                          attempt(async () => {
                            if (confirm('Delete this thread?')) {
                              await removeRecord('threads', x.id);
                              await loadPage();
                            }
                          }),
                      },
                      'Delete',
                    ),
                  ),
                  field(recordName('threads', x.id), x, 'detail', {
                    type: 'textarea',
                    rows: 2,
                    ariaLabel: 'Thread details',
                  }),
                  h(
                    'div',
                    { style: 'margin-top:6px' },
                    picker(recordName('threads', x.id), x.pcs, pcChoices, {
                      placeholder: 'Tag a hero…',
                      cls: 'pc',
                    }),
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
    render(
      pages,
      h(
        'button',
        {
          disabled: offset === 0,
          onclick: () => {
            offset -= limit;
            loadPage();
          },
        },
        'Previous',
      ),
      h(
        'span',
        { class: 'muted' },
        `${result.total ? offset + 1 : 0}–${Math.min(offset + limit, result.total)} of ${result.total}`,
      ),
      h(
        'button',
        {
          disabled: offset + limit >= result.total,
          onclick: () => {
            offset += limit;
            loadPage();
          },
        },
        'Next',
      ),
    );
  };
  const loadPage = async () => {
    const params = new URLSearchParams({ offset, limit });
    if (THREAD_STATES.includes(filter)) params.set('status', filter);
    else if (filter !== 'all') params.set('pc', filter);
    result = await context.api('/api/records/threads?' + params);
    draw();
  };
  const filters = h(
    'div',
    { class: 'filters' },
    [
      'all',
      ...THREAD_STATES,
      ...PCS.filter((pc) => S.recordIndex.threads.some((x) => (x.pcs || []).includes(pc))),
    ].map((f) =>
      h(
        'button',
        {
          'data-f': f,
          onclick: () => {
            filter = f;
            offset = 0;
            loadPage();
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
          onclick: () =>
            attempt(async () => {
              await createRecord(
                'threads',
                blank('thread', { id: uid('thread'), title: 'New thread' }),
              );
              filter = 'all';
              offset = 0;
              await loadPage();
            }),
        },
        '+ New thread',
      ),
    ),
    h('p', { class: 'sub' }, 'Plot threads, promises and fortunes: what the story owes each hero.'),
    filters,
    list,
    pages,
  );
  await loadPage();
}

async function codexPage(id, context) {
  const main = context.view;
  if (id) return codexEntry(await context.doc(recordName('codex', id), null), id, main, context);
  await recordChoices('codex', context.signal);
  let type = S.codexType || 'all',
    q = S.codexQ || '',
    tag = S.codexTag || '',
    source = S.codexSource || '';
  let offset = 0;
  const limit = 40;
  let result = { items: [], total: 0 };
  let sequence = 0;
  const grid = h('div', { class: 'grid codex' });
  const pages = h('div', { class: 'row' });
  const draw = () => {
    S.codexType = type;
    S.codexQ = q;
    S.codexTag = tag;
    S.codexSource = source;
    render(
      grid,
      ...result.items.map((e) => {
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
              e.has_secrets ? h('span', { class: 'chip dead' }, 'secrets') : '',
            ),
            h('p', { class: 'clamp' }, e.public || e.notes),
          ),
        );
      }),
    );
    filters
      .querySelectorAll('button')
      .forEach((b) => b.classList.toggle('on', b.dataset.t === type));
    render(
      pages,
      h(
        'button',
        {
          disabled: offset === 0,
          onclick: () => {
            offset -= limit;
            loadPage();
          },
        },
        'Previous',
      ),
      h(
        'span',
        { class: 'muted' },
        `${result.total ? offset + 1 : 0}–${Math.min(offset + limit, result.total)} of ${result.total}`,
      ),
      h(
        'button',
        {
          disabled: offset + limit >= result.total,
          onclick: () => {
            offset += limit;
            loadPage();
          },
        },
        'Next',
      ),
    );
  };
  const loadPage = async () => {
    const request = ++sequence;
    const params = new URLSearchParams({ offset, limit });
    if (type !== 'all') params.set('type', type);
    if (q) params.set('q', q);
    if (tag) params.set('tag', tag);
    if (source) params.set('source', source);
    const next = await context.api('/api/records/codex?' + params);
    if (request === sequence) {
      result = next;
      draw();
    }
  };
  let searchTimer;
  const search = () => {
    offset = 0;
    clearTimeout(searchTimer);
    searchTimer = setTimeout(
      () =>
        loadPage().catch((e) => {
          if (!context.signal.aborted) alert(e.message);
        }),
      200,
    );
  };
  const filters = h(
    'div',
    { class: 'filters' },
    ['all', ...Object.keys(TYPES).filter((t) => S.recordIndex.codex.some((e) => e.type === t))].map(
      (t) =>
        h(
          'button',
          {
            'data-t': t,
            onclick: () => {
              type = t;
              search();
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
        search();
      },
    }),
    h('input', {
      type: 'search',
      placeholder: 'Filter by tag…',
      'aria-label': 'Filter codex by tag',
      value: tag,
      oninput: (e) => {
        tag = e.target.value;
        search();
      },
    }),
    h(
      'select',
      {
        'aria-label': 'Filter codex by source',
        onchange: (e) => {
          source = e.target.value;
          search();
        },
      },
      ...[
        ['', 'Any source'],
        ['studio', 'Studio'],
        ['foundry', 'Foundry'],
        ['ai', 'AI draft'],
      ].map(([value, label]) => h('option', { value, selected: source === value }, label)),
    ),
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
          onclick: () =>
            attempt(async () => {
              const name = prompt('Name of the new entry?');
              if (!name) return;
              const e = blank('codex_entry', {
                id:
                  slug(name) +
                  (S.recordIndex.codex.some((x) => x.id === slug(name))
                    ? '-' + Date.now().toString(36)
                    : ''),
                type: type === 'all' ? 'npc' : type,
                name,
              });
              await createRecord('codex', e);
              go('#/codex/' + e.id);
            }),
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
    pages,
  );
  await loadPage();
}

async function codexEntry(e, id, main, context) {
  const docName = recordName('codex', id);
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
            onclick: () => deleteCodexEntry(e),
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
async function deleteCodexEntry(e) {
  try {
    await flushAll(); // so the server sees every linked record's pending edits
    const id = encodeURIComponent(e.id);
    const { uses, rev } = await api(`/api/codex/${id}/uses`);
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
    const { changed } = await post(`/api/codex/${id}/delete`, { rev });
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
