'use strict';

const ICONS = {
  map: '<path d="m3 6 6-3 6 3 6-3v15l-6 3-6-3-6 3zM9 3v15m6-12v15"/>',
  home: '<path d="m3 10 9-7 9 7v11H3zM9 21v-8h6v8"/>',
  book: '<path d="M4 3h13a3 3 0 0 1 3 3v15H6a2 2 0 0 1-2-2V3Zm0 14h16M8 7h8m-8 4h6"/>',
  people:
    '<circle cx="9" cy="8" r="3"/><path d="M3 21v-3a6 6 0 0 1 12 0v3m1-17a3 3 0 0 1 0 6m2 4a5 5 0 0 1 3 4v3"/>',
  threads:
    '<circle cx="5" cy="5" r="2"/><circle cx="19" cy="5" r="2"/><circle cx="12" cy="19" r="2"/><path d="M5 7v3a3 3 0 0 0 3 3h8a3 3 0 0 0 3-3V7m-7 6v4"/>',
  image:
    '<rect x="3" y="3" width="18" height="18" rx="3"/><circle cx="8" cy="8" r="1.5"/><path d="m3 17 5-5 4 4 4-6 5 7"/>',
  spark: '<path d="m12 3 2.4 6.6L21 12l-6.6 2.4L12 21l-2.4-6.6L3 12l6.6-2.4z"/>',
  settings:
    '<path d="M12 3v3m0 12v3M3 12h3m12 0h3M5.6 5.6l2.1 2.1m8.6 8.6 2.1 2.1m0-12.8-2.1 2.1m-8.6 8.6-2.1 2.1"/><circle cx="12" cy="12" r="5"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  arrow: '<path d="M4 12h16m-6-6 6 6-6 6"/>',
  check: '<path d="m5 12 4 4L19 6"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  globe:
    '<circle cx="12" cy="12" r="9"/><ellipse cx="12" cy="12" rx="4" ry="9"/><path d="M3 12h18"/>',
  folder: '<path d="M3 6h7l2 2h9v12H3z"/>',
  upload: '<path d="M12 16V3m-5 5 5-5 5 5M4 15v6h16v-6"/>',
};
function icon(name) {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('viewBox', '0 0 24 24');
  svg.setAttribute('fill', 'none');
  svg.setAttribute('stroke', 'currentColor');
  svg.setAttribute('stroke-width', '1.6');
  svg.setAttribute('stroke-linecap', 'round');
  svg.setAttribute('stroke-linejoin', 'round');
  svg.setAttribute('aria-hidden', 'true');
  svg.innerHTML = ICONS[name] || ICONS.book;
  return h('span', { class: 'icon' }, svg);
}
function initStudio() {
  document.title = 'Campaign Studio · ' + S.state.campaign;
  render(
    $('#side'),
    h(
      'a',
      { class: 'studio-brand', href: '#/' },
      h('span', { class: 'brand-mark' }, icon('map')),
      h('span', {}, 'Campaign', h('b', {}, 'Studio')),
    ),
    h(
      'div',
      { class: 'campaign-switch' },
      h('span', { class: 'campaign-avatar' }, S.state.campaign[0] || 'C'),
      h('div', {}, h('b', {}, S.state.campaign), h('small', {}, 'Local campaign')),
    ),
    h('div', { class: 'nav-caption' }, 'WORKSPACE'),
    h(
      'nav',
      {},
      [
        ['', 'home', 'Overview'],
        ['library', 'book', 'World Library'],
        ['maps', 'map', 'Maps & locations'],
        ['prep', 'book', 'Session prep'],
        ['codex', 'people', 'Campaign codex'],
        ['threads', 'threads', 'Story threads'],
        ['art', 'image', 'Image studio'],
        ['inbox', 'spark', 'AI requests'],
        ['handouts', 'folder', 'Handouts'],
        ['notes', 'book', 'Campaign notes'],
      ].map(([path, ico, label]) =>
        h('a', { href: '#/' + path, 'data-nav': path }, icon(ico), label),
      ),
      h(
        'a',
        { class: 'mobile-settings', href: '#/settings', 'data-nav': 'settings' },
        icon('settings'),
        'Settings & Foundry',
      ),
    ),
    h(
      'div',
      { class: 'side-bottom' },
      h(
        'a',
        { class: 'settings-link', href: '#/settings', 'data-nav': 'settings' },
        icon('settings'),
        'Settings & Foundry',
      ),
      h('div', { id: 'jobs', 'aria-live': 'polite' }),
      h('div', { id: 'saved', 'aria-live': 'polite' }),
      h('small', { class: 'local-dot' }, 'Running locally'),
    ),
  );
}
function toast(message, error = false) {
  let box = $('#toast');
  if (!box) {
    box = h('div', { id: 'toast', role: 'status' });
    document.body.append(box);
  }
  box.textContent = message;
  box.className = error ? 'error' : '';
  box.hidden = false;
  clearTimeout(S.toastTimer);
  S.toastTimer = setTimeout(() => (box.hidden = true), 6000);
}
async function attempt(action) {
  try {
    return await action();
  } catch (e) {
    toast(e.message, true);
  }
}
async function flushAll() {
  for (const name of Object.keys(S.pending)) await flush(name);
}
function pageHead(kicker, title, description, actions = null) {
  return h(
    'div',
    { class: 'page-head' },
    h(
      'div',
      {},
      h('div', { class: 'eyebrow' }, kicker),
      h('h1', {}, title),
      h('p', { class: 'sub' }, description),
    ),
    actions,
  );
}
function badge(text, status = '') {
  return h('span', { class: 'badge ' + status }, text);
}
function formInput(obj, key, label, opts = {}) {
  const id = uid('input');
  const common = {
    id,
    value: obj[key] ?? '',
    placeholder: opts.placeholder,
    min: opts.min,
    max: opts.max,
    step: opts.step,
  };
  const update = (el) => {
    obj[key] = opts.type === 'number' || opts.type === 'range' ? +el.value : el.value;
    opts.change?.();
  };
  let el;
  if (opts.options)
    el = h(
      'select',
      { ...common, onchange: (e) => update(e.target) },
      opts.options.map((o) =>
        h(
          'option',
          {
            value: typeof o === 'string' ? o : o[0],
            selected: obj[key] === (typeof o === 'string' ? o : o[0]),
          },
          typeof o === 'string' ? o : o[1],
        ),
      ),
    );
  else if (opts.type === 'textarea')
    el = h('textarea', { ...common, rows: opts.rows || 4, oninput: (e) => update(e.target) });
  else if (opts.type === 'checkbox')
    return h(
      'label',
      { class: 'toggle-row', for: id },
      h('input', {
        id,
        type: 'checkbox',
        checked: obj[key],
        onchange: (e) => {
          obj[key] = e.target.checked;
          opts.change?.();
        },
      }),
      h('span', {}, h('b', {}, label), opts.help ? h('small', {}, opts.help) : null),
    );
  else el = h('input', { ...common, type: opts.type || 'text', oninput: (e) => update(e.target) });
  return h(
    'div',
    { class: 'form-field' },
    h('label', { for: id }, label),
    el,
    opts.help ? h('small', { class: 'muted' }, opts.help) : null,
  );
}
function modal(title, description, body, onSubmit, button = 'Save') {
  const dialog = h('dialog', { class: 'studio-dialog' });
  const submit = h('button', { class: 'primary', type: 'submit' }, button);
  const form = h(
    'form',
    {
      onsubmit: async (e) => {
        e.preventDefault();
        submit.disabled = true;
        try {
          await onSubmit();
          dialog.close();
          dialog.remove();
        } catch (err) {
          toast(err.message, true);
          submit.disabled = false;
        }
      },
    },
    h('div', { class: 'eyebrow' }, 'CAMPAIGN STUDIO'),
    h('h2', {}, title),
    h('p', { class: 'muted' }, description),
    body,
    h(
      'div',
      { class: 'dialog-actions' },
      h(
        'button',
        {
          type: 'button',
          onclick: () => {
            dialog.close();
            dialog.remove();
          },
        },
        'Cancel',
      ),
      submit,
    ),
  );
  dialog.append(form);
  document.body.append(dialog);
  dialog.showModal();
  dialog.querySelector('input,textarea,select')?.focus();
  dialog.addEventListener('cancel', () => dialog.remove());
  return dialog;
}
async function studioDashboard() {
  const maps = (await doc('maps/index', { items: [] })).items;
  const codex = (await doc('codex', { entries: [] })).entries;
  const threads = (await doc('threads', { threads: [] })).threads;
  const art = (await doc('art', { items: [] })).items;
  const next = S.state.prep.at(-1);
  render(
    S.view,
    pageHead(
      'YOUR CAMPAIGN',
      'A world ready for the table.',
      'Shape the places, people and stories your players will discover.',
      h('a', { class: 'btn primary', href: '#/maps/new' }, icon('plus'), 'Create a map'),
    ),
    h(
      'div',
      { class: 'overview-stats' },
      [
        ['map', maps.length, 'Maps & locations', 'maps'],
        ['people', codex.filter((e) => e.type === 'npc').length, 'NPCs in your world', 'codex'],
        [
          'threads',
          threads.filter((t) => t.status !== 'resolved').length,
          'Active story threads',
          'threads',
        ],
        ['image', art.filter((a) => a.status !== 'ready').length, 'Images to create', 'art'],
      ].map(([ico, n, label, path]) =>
        h(
          'a',
          { class: 'stat-tile', href: '#/' + path },
          icon(ico),
          h('strong', {}, n),
          h('span', {}, label),
        ),
      ),
    ),
    h(
      'div',
      { class: 'overview-columns' },
      h(
        'section',
        {},
        h(
          'div',
          { class: 'section-head' },
          h('h2', {}, 'Continue building'),
          h('a', { href: '#/maps' }, 'View all maps →'),
        ),
        h(
          'div',
          { class: 'grid maps' },
          maps
            .slice()
            .sort((a, b) => (b.updated || '').localeCompare(a.updated || ''))
            .slice(0, 3)
            .map(mapCard),
        ),
        !maps.length
          ? h(
              'div',
              { class: 'empty-state' },
              icon('map'),
              h('h3', {}, 'Your world starts with a place'),
              h('p', {}, 'Create a map and choose what the AI should bring to life there.'),
              h('a', { class: 'btn primary', href: '#/maps/new' }, 'Create your first map'),
            )
          : null,
      ),
      h(
        'aside',
        { class: 'card next-session' },
        badge('SESSION PREP'),
        h('h2', {}, next ? next.toUpperCase() : 'Your next adventure'),
        h(
          'p',
          { class: 'muted' },
          'Gather your scenes, encounters and story threads in one place.',
        ),
        h('a', { class: 'btn', href: '#/prep' }, 'Open session prep', icon('arrow')),
        h('hr'),
        h('h3', {}, 'Story threads to pick up'),
        threads
          .filter((t) => t.status === 'open' || t.status === 'planned')
          .slice(0, 4)
          .map((t) =>
            h(
              'a',
              { class: 'thread-preview', href: '#/threads' },
              h('span', {}, t.title),
              badge(t.status),
            ),
          ),
      ),
    ),
  );
}
function mapCard(m) {
  return h(
    'a',
    { class: 'map-card', href: '#/maps/' + m.slug },
    h(
      'div',
      { class: 'map-card-image' },
      h('img', {
        src: fileUrl(m.roofs_preview || m.image || m.check) + '?v=' + (m.updated || ''),
        alt: m.name,
        loading: 'lazy',
      }),
      badge(m.stocked ? 'Populated' : 'Layout ready', m.stocked ? 'good' : ''),
    ),
    h(
      'div',
      { class: 'map-card-body' },
      h('h3', {}, m.name),
      h('p', {}, m.summary || 'A place waiting for its story.'),
      h(
        'div',
        { class: 'map-card-meta' },
        h('span', {}, `${m.cells[0]} × ${m.cells[1]} squares`),
        h('span', {}, m.theme),
        icon('arrow'),
      ),
    ),
  );
}
async function studioMaps(arg) {
  if (arg === 'new') return newMapStudio();
  if (arg) return mapStudio(arg);
  const maps = (await doc('maps/index', { items: [] })).items;
  let q = S.mapSearch || '',
    filter = S.mapFilter || 'all';
  const pending = await api('/api/maps/pending');
  const cards = h('div', { class: 'grid maps' });
  const draw = () => {
    S.mapSearch = q;
    S.mapFilter = filter;
    const hits = maps.filter(
      (m) =>
        (m.name + ' ' + m.summary + ' ' + m.theme).toLowerCase().includes(q) &&
        (filter === 'all' || (filter === 'populated' ? m.stocked : !m.stocked)),
    );
    const drafts = pending.filter(
      (p) => filter !== 'populated' && p.brief.name.toLowerCase().includes(q),
    );
    render(
      cards,
      drafts.map((p) =>
        h(
          'a',
          { class: 'map-card pending-card', href: '#/maps/' + p.slug },
          h(
            'div',
            { class: 'art-placeholder' },
            icon('map'),
            badge(p.workflows[0]?.status || 'building'),
          ),
          h(
            'div',
            { class: 'map-card-body' },
            h('h3', {}, p.brief.name),
            h('p', {}, p.brief.prompt),
            h('span', {}, 'Open map workflow →'),
          ),
        ),
      ),
      hits
        .slice()
        .sort((a, b) => (b.updated || '').localeCompare(a.updated || ''))
        .map(mapCard),
    );
    if (!hits.length && !drafts.length)
      cards.append(
        h(
          'div',
          { class: 'empty-state' },
          icon('map'),
          h('h3', {}, 'No maps here yet'),
          h('p', {}, 'Try another filter, or build a new place for your campaign.'),
        ),
      );
  };
  const active = S.jobs.filter(
    (j) => ['queued', 'running'].includes(j.status) && j.lane === 'forge',
  );
  render(
    S.view,
    pageHead(
      'WORLD BUILDING',
      'Maps & locations',
      'Build the environment. Bring it to life. Keep refining it.',
      h(
        'div',
        { class: 'row' },
        h('button', { onclick: importMapDialog }, icon('upload'), 'Import map'),
        h('a', { class: 'btn primary', href: '#/maps/new' }, icon('plus'), 'Create map'),
      ),
    ),
    h(
      'div',
      { class: 'library-toolbar' },
      h('input', {
        type: 'search',
        placeholder: 'Search your maps…',
        value: q,
        'aria-label': 'Search maps',
        oninput: (e) => {
          q = e.target.value.toLowerCase();
          draw();
        },
      }),
      h(
        'select',
        {
          'aria-label': 'Map status',
          onchange: (e) => {
            filter = e.target.value;
            draw();
          },
        },
        [
          ['all', 'All maps'],
          ['populated', 'Populated'],
          ['layout', 'Layout ready'],
        ].map(([v, l]) => h('option', { value: v, selected: v === filter }, l)),
      ),
      h(
        'span',
        { class: 'muted' },
        maps.length + ' maps' + (pending.length ? ' · ' + pending.length + ' in progress' : ''),
      ),
    ),
    active.length ? h('div', { class: 'active-jobs' }, active.map(jobBox)) : null,
    cards,
  );
  draw();
}
function freshBrief() {
  return {
    name: '',
    prompt: '',
    type: 'city',
    theme: 'city',
    width: 60,
    height: 60,
    cell: 100,
    seed: '',
    density: 0.7,
    alleys: 0.5,
    darkness: 0.15,
    canal: false,
    wall: false,
    market: true,
    session: '',
    tone: 'Grounded fantasy',
    party_level: 5,
    threads: [],
    auto_content: true,
    content: { npcs: 5, items: 4, journals: 4, events: 5, art: true, threads: true },
  };
}
async function newMapStudio() {
  const f = S.creation || (S.creation = freshBrief());
  let step = S.creationStep || 0;
  const threads = (await doc('threads', { threads: [] })).threads;
  const content = h('div');
  const summary = h('aside', { class: 'creation-summary' });
  const steps = h('div', { class: 'creation-steps' });
  const drawSummary = () =>
    render(
      summary,
      h('div', { class: 'eyebrow' }, 'YOUR MAP BRIEF'),
      h('h2', {}, f.name || 'An undiscovered place'),
      h(
        'p',
        { class: 'muted' },
        f.prompt || 'Describe the place, its atmosphere, and what the party might do here.',
      ),
      h(
        'dl',
        {},
        h('dt', {}, 'Layout'),
        h('dd', {}, f.type === 'city' ? 'Procedural city district' : 'AI designed ' + f.theme),
        h('dt', {}, 'Scale'),
        h('dd', {}, `${f.width} × ${f.height} squares`),
        h('dt', {}, 'Party'),
        h('dd', {}, 'Level ' + f.party_level),
        h('dt', {}, 'Content'),
        h('dd', {}, `${f.content.npcs} NPCs · ${f.content.items} items`),
      ),
      h('hr'),
      h('div', { class: 'eyebrow' }, 'HOW IT COMES TO LIFE'),
      h(
        'ol',
        { class: 'pipeline-list' },
        [
          'Build the layout',
          'Draft people, items & stories',
          'Review and refine',
          'Prepare for Foundry',
        ].map((v, i) => h('li', {}, h('span', {}, i + 1), v)),
      ),
      h(
        'p',
        { class: 'small-note' },
        'AI drafts use this brief and your selected story threads. You review proposals before applying them.',
      ),
    );
  const redraw = () => {
    S.creationStep = step;
    render(
      steps,
      ...['The place', 'The layout', 'Bring it to life'].map((label, i) =>
        h(
          'button',
          {
            class: i === step ? 'on' : i < step ? 'done' : '',
            onclick: () => {
              step = i;
              redraw();
            },
          },
          h('span', {}, i < step ? '✓' : i + 1),
          label,
        ),
      ),
    );
    const change = drawSummary;
    const fields =
      step === 0
        ? [
            h('div', { class: 'eyebrow' }, '01 / THE PLACE'),
            h('h2', {}, 'What will your players discover?'),
            h(
              'p',
              { class: 'muted' },
              'A strong brief gives the map and its inhabitants a shared story.',
            ),
            formInput(f, 'name', 'Map name', { placeholder: 'e.g. The Sunken Quarter', change }),
            formInput(f, 'prompt', 'Describe the map', {
              type: 'textarea',
              rows: 6,
              placeholder:
                'An old port district half swallowed by a tidal flood. A shrine stands above the water, smugglers use the ruined cellars, and the city watch has sealed the bridges…',
              change,
            }),
            h(
              'div',
              { class: 'two' },
              formInput(f, 'tone', 'Atmosphere', {
                options: [
                  'Grounded fantasy',
                  'Dark & dangerous',
                  'Mythic & divine',
                  'Whimsical',
                  'Political intrigue',
                ],
                change,
              }),
              formInput(f, 'party_level', 'Party level', {
                type: 'number',
                min: 1,
                max: 30,
                change,
              }),
            ),
            formInput(f, 'session', 'For session', {
              options: [['', 'No session yet'], ...S.state.prep.map((n) => [n, n.toUpperCase()])],
              change,
            }),
          ]
        : step === 1
          ? [
              h('div', { class: 'eyebrow' }, '02 / THE LAYOUT'),
              h('h2', {}, 'Give the adventure room to unfold.'),
              h(
                'div',
                { class: 'layout-options' },
                [
                  ['city', 'Procedural district', 'Buildings, alleys, interiors and roofs.'],
                  [
                    'custom',
                    'AI designed environment',
                    'Dungeons, wilderness, temples and custom spaces.',
                  ],
                ].map(([v, title, desc]) =>
                  h(
                    'button',
                    {
                      class: 'choice-card ' + (f.type === v ? 'selected' : ''),
                      onclick: () => {
                        f.type = v;
                        f.theme = v === 'city' ? 'city' : 'outdoor';
                        redraw();
                      },
                    },
                    icon(v === 'city' ? 'map' : 'spark'),
                    h('b', {}, title),
                    h('small', {}, desc),
                  ),
                ),
              ),
              f.type === 'custom'
                ? formInput(f, 'theme', 'Environment', {
                    options: [
                      ['outdoor', 'Wilderness / settlement'],
                      ['dungeon', 'Dungeon'],
                      ['temple', 'Temple / shrine'],
                      ['cave', 'Cavern'],
                      ['ship', 'Ship'],
                      ['tavern', 'Tavern'],
                      ['cellar', 'Cellars'],
                    ],
                    change,
                  })
                : null,
              h(
                'div',
                { class: 'three' },
                formInput(f, 'width', 'Width (squares)', {
                  type: 'number',
                  min: 20,
                  max: 160,
                  change,
                }),
                formInput(f, 'height', 'Height (squares)', {
                  type: 'number',
                  min: 20,
                  max: 160,
                  change,
                }),
                formInput(f, 'cell', 'Detail per square', {
                  options: [
                    [100, '100 px · faster'],
                    [150, '150 px · more detail'],
                  ],
                  change,
                }),
              ),
              h(
                'div',
                { class: 'preset-sizes' },
                [
                  [40, 40],
                  [60, 60],
                  [80, 80],
                  [120, 80],
                  [160, 120],
                ].map(([w, hh]) =>
                  h(
                    'button',
                    {
                      type: 'button',
                      onclick: () => {
                        f.width = w;
                        f.height = hh;
                        redraw();
                      },
                    },
                    `${w} × ${hh}`,
                  ),
                ),
              ),
              f.type === 'city'
                ? h(
                    'div',
                    {},
                    h(
                      'div',
                      { class: 'two' },
                      formInput(f, 'density', 'Building density', {
                        type: 'range',
                        min: 0.2,
                        max: 1,
                        step: 0.05,
                        change,
                      }),
                      formInput(f, 'alleys', 'Back alleys', {
                        type: 'range',
                        min: 0,
                        max: 1,
                        step: 0.05,
                        change,
                      }),
                    ),
                    formInput(f, 'canal', 'Canal & bridges', { type: 'checkbox', change }),
                    formInput(f, 'wall', 'City walls & gates', { type: 'checkbox', change }),
                    formInput(f, 'market', 'Market square', { type: 'checkbox', change }),
                  )
                : null,
              h(
                'div',
                { class: 'two' },
                formInput(f, 'seed', 'Seed (optional)', {
                  placeholder: 'Random',
                  type: 'number',
                  change,
                }),
                formInput(f, 'darkness', 'Darkness', {
                  type: 'range',
                  min: 0,
                  max: 1,
                  step: 0.05,
                  change,
                }),
              ),
              h(
                'p',
                { class: 'small-note' },
                'Large maps take longer to paint. AI layouts first appear as a lightweight proposal to review.',
              ),
            ]
          : [
              h('div', { class: 'eyebrow' }, '03 / BRING IT TO LIFE'),
              h('h2', {}, 'Fill the map with reasons to explore.'),
              h(
                'p',
                { class: 'muted' },
                'Choose what the AI should draft from your map brief. All content is tied to numbered locations.',
              ),
              h(
                'div',
                { class: 'content-counts' },
                [
                  ['npcs', 'NPCs', 'people'],
                  ['items', 'Items', 'folder'],
                  ['journals', 'Journal entries', 'book'],
                  ['events', 'Events', 'spark'],
                ].map(([k, label, ico]) =>
                  h(
                    'div',
                    {},
                    icon(ico),
                    formInput(f.content, k, label, { type: 'number', min: 0, max: 30, change }),
                  ),
                ),
              ),
              formInput(f.content, 'art', 'Queue image briefs', {
                type: 'checkbox',
                help: 'Portraits, items and event illustrations linked to their content.',
                change,
              }),
              formInput(f.content, 'threads', 'Propose new story threads', {
                type: 'checkbox',
                help: 'Hooks and consequences with a status you can manage.',
                change,
              }),
              h('label', {}, 'Connect existing story threads'),
              h(
                'div',
                { class: 'thread-selection' },
                threads
                  .filter((t) => t.status !== 'resolved')
                  .map((t) =>
                    h(
                      'label',
                      { class: 'thread-checkbox' },
                      h('input', {
                        type: 'checkbox',
                        checked: f.threads.includes(t.id),
                        onchange: (e) => {
                          if (e.target.checked) f.threads.push(t.id);
                          else f.threads.splice(f.threads.indexOf(t.id), 1);
                          change();
                        },
                      }),
                      h('span', {}, t.title),
                      badge(t.status),
                    ),
                  ),
              ),
              formInput(f, 'auto_content', 'Start the content workflow after the layout', {
                type: 'checkbox',
                help: S.state.claude
                  ? 'Claude Code is available on this computer.'
                  : 'You can export the prompt pack for your preferred AI assistant.',
                change,
              }),
            ];
    const submit = h(
      'button',
      {
        class: 'primary',
        onclick: () =>
          attempt(async () => {
            if (!f.name.trim()) throw new Error('Give your map a name.');
            if (!f.prompt.trim())
              throw new Error('Describe the place so the AI has a map brief to work from.');
            submit.disabled = true;
            try {
              const result = await post('/api/maps/create', f);
              S.creation = null;
              S.creationStep = 0;
              await poll();
              go('#/maps/' + result.slug);
            } finally {
              submit.disabled = false;
            }
          }),
      },
      icon('spark'),
      'Create map & workflow',
    );
    render(
      content,
      ...fields,
      h(
        'div',
        { class: 'wizard-actions' },
        step > 0
          ? h(
              'button',
              {
                onclick: () => {
                  step--;
                  redraw();
                },
              },
              'Back',
            )
          : h('a', { href: '#/maps' }, 'Cancel'),
        step < 2
          ? h(
              'button',
              {
                class: 'primary',
                onclick: () => {
                  step++;
                  redraw();
                },
              },
              'Continue',
              icon('arrow'),
            )
          : submit,
      ),
    );
    drawSummary();
  };
  render(
    S.view,
    pageHead(
      'WORLD BUILDING / NEW MAP',
      'Create a place with a story.',
      'One brief connects the layout, characters, discoveries and artwork.',
    ),
    steps,
    h(
      'div',
      { class: 'creation-layout' },
      h('section', { class: 'creation-form' }, content),
      summary,
    ),
  );
  redraw();
}

async function mapStudio(slugArg) {
  const main = S.view;
  const index = await doc('maps/index', { items: [] });
  const m = index.items.find((x) => x.slug === slugArg);
  const workspace = await api('/api/maps/' + slugArg + '/workspace');
  let brief = await doc(
    'mapbrief/' + slugArg,
    Object.keys(workspace.brief).length
      ? workspace.brief
      : {
          ...freshBrief(),
          name: m?.name || 'New map',
          prompt: m?.summary || '',
          width: m?.cells?.[0] || 60,
          height: m?.cells?.[1] || 60,
        },
  );
  if (!m) {
    const jobs = S.jobs.filter((j) => j.slug === slugArg);
    render(
      main,
      pageHead(
        'MAP WORKFLOW',
        brief.name,
        'Your new place is taking shape.',
        h('a', { class: 'btn', href: '#/maps' }, 'Back to maps'),
      ),
      h(
        'div',
        { class: 'pending-map' },
        h(
          'div',
          { class: 'card' },
          icon('map'),
          h(
            'h2',
            {},
            jobs.some((j) => j.status === 'failed')
              ? 'The layout needs attention'
              : 'Building the first layout',
          ),
          h('p', { class: 'muted' }, brief.prompt),
          jobs.map(jobBox),
          workspace.has_plan && jobs.some((j) => j.status === 'failed')
            ? h(
                'button',
                {
                  onclick: () =>
                    attempt(async () => {
                      await post('/api/forge/' + slugArg, { populate: true });
                      await poll();
                      route(true);
                    }),
                },
                'Retry map rendering',
              )
            : null,
        ),
        h(
          'div',
          { class: 'card' },
          workspace.workflows.map((w) => workflowCard(w, slugArg)),
        ),
      ),
    );
    return;
  }
  const keyName = 'mapkey/' + slugArg;
  const key = await doc(keyName, {
    map: m.name,
    areas: [],
    events: [],
    notes: '',
    stocked: false,
    session: m.session || '',
  });
  const c = await doc('codex', { entries: [] });
  const t = await doc('threads', { threads: [] });
  const art = await doc('art', { items: [] });
  S.studioTabs = S.studioTabs || {};
  let tabName = S.studioTabs[slugArg] || 'locations';
  S.canvas = S.canvas || {};
  const state =
    S.canvas[slugArg] ||
    (S.canvas[slugArg] = { zoom: 1, pins: true, view: m.roofs_preview ? 'roofs' : 'map' });
  let selected = key.areas.find((a) => a.n === S.mapArea?.[slugArg]) || key.areas[0],
    placing = false,
    comparison = null,
    comparePercent = 50;
  const pic = h('div', { class: 'studio-canvas' }),
    inspector = h('aside', { class: 'map-inspector' }),
    tabs = h('div', { class: 'inspector-tabs' }),
    panel = h('div', { class: 'inspector-body' });
  const views = { roofs: m.roofs_preview, map: m.image, walls: m.check };
  const remember = () => {
    S.mapArea = { ...(S.mapArea || {}), [slugArg]: selected?.n };
  };
  const refresh = async () => {
    await flushAll();
    for (const n of [keyName, 'codex', 'threads', 'art', 'maps/index']) await load(n, S.docs[n]);
    route(true);
  };
  const drawCanvas = () => {
    const old = pic.querySelector('.map-viewport');
    const scroll = [old?.scrollLeft || 0, old?.scrollTop || 0];
    const stage = h(
      'div',
      { class: 'map-stage', style: `width:${state.zoom * 100}%` },
      h('img', {
        src: fileUrl(views[state.view] || m.image) + '?v=' + m.updated,
        alt: m.name,
        draggable: false,
      }),
    );
    if (comparison)
      stage.append(
        h('img', {
          class: 'comparison-image',
          src: fileUrl(comparison),
          alt: 'Previous layout',
          style: `clip-path:inset(0 ${100 - comparePercent}% 0 0)`,
        }),
        h('div', { class: 'comparison-line', style: `left:${comparePercent}%` }),
      );
    if (state.pins && !comparison)
      key.areas.forEach((a) => {
        if (!a.at) return;
        stage.append(
          h(
            'button',
            {
              class: 'map-pin ' + (selected?.n === a.n ? 'active' : ''),
              style: `left:${((a.at[1] + 0.5) / m.cells[0]) * 100}%;top:${((a.at[0] + 0.5) / m.cells[1]) * 100}%`,
              title: a.name,
              'aria-label': `Area ${a.n}: ${a.name}`,
              onclick: (e) => {
                e.stopPropagation();
                selected = a;
                remember();
                tabName = 'locations';
                drawInspector();
                drawCanvas();
              },
            },
            a.n,
          ),
        );
      });
    stage.addEventListener('click', (e) => {
      if (!placing || e.target.closest('.map-pin')) return;
      const r = stage.getBoundingClientRect();
      const at = [
        Math.min(
          m.cells[1] - 1,
          Math.max(0, Math.floor(((e.clientY - r.top) / r.height) * m.cells[1])),
        ),
        Math.min(
          m.cells[0] - 1,
          Math.max(0, Math.floor(((e.clientX - r.left) / r.width) * m.cells[0])),
        ),
      ];
      const f = { name: '', kind: 'location' };
      modal(
        'Name this location',
        `Row ${at[0] + 1}, column ${at[1] + 1}.`,
        h('div', {}, formInput(f, 'name', 'Location name'), formInput(f, 'kind', 'Kind')),
        async () => {
          if (!f.name.trim()) throw new Error('Give the location a name.');
          selected = {
            n: Math.max(0, ...key.areas.map((a) => a.n)) + 1,
            name: f.name.trim(),
            kind: f.kind,
            at,
            rooms: [],
            text: '',
            creatures: '',
            loot: [],
            events: [],
            npcs: [],
            items: [],
            journal: [],
            images: [],
            threads: [],
          };
          key.areas.push(selected);
          save(keyName);
          placing = false;
          remember();
          drawCanvas();
          drawInspector();
        },
        'Add location',
      );
    });
    const viewport = h('div', { class: 'map-viewport ' + (placing ? 'placing' : '') }, stage);
    let drag = null;
    viewport.addEventListener('pointerdown', (e) => {
      if (placing || e.target.closest('button') || state.zoom === 1) return;
      drag = { x: e.clientX, y: e.clientY, sx: viewport.scrollLeft, sy: viewport.scrollTop };
      viewport.setPointerCapture(e.pointerId);
    });
    viewport.addEventListener('pointermove', (e) => {
      if (!drag) return;
      viewport.scrollLeft = drag.sx + drag.x - e.clientX;
      viewport.scrollTop = drag.sy + drag.y - e.clientY;
    });
    viewport.addEventListener('pointerup', () => (drag = null));
    viewport.addEventListener('pointercancel', () => (drag = null));
    render(
      pic,
      h(
        'div',
        { class: 'canvas-toolbar' },
        h(
          'div',
          { class: 'segmented' },
          Object.entries({ roofs: 'Roofs', map: 'Interiors', walls: 'Walls' })
            .filter(([k]) => views[k])
            .map(([k, l]) =>
              h(
                'button',
                {
                  class: state.view === k ? 'on' : '',
                  onclick: () => {
                    state.view = k;
                    drawCanvas();
                  },
                },
                l,
              ),
            ),
        ),
        h(
          'div',
          { class: 'row' },
          h(
            'button',
            {
              title: 'Zoom out',
              'aria-label': 'Zoom out',
              onclick: () => {
                state.zoom = Math.max(1, state.zoom / 2);
                drawCanvas();
              },
            },
            '−',
          ),
          h(
            'button',
            {
              title: 'Fit map',
              onclick: () => {
                state.zoom = 1;
                drawCanvas();
              },
            },
            state.zoom === 1 ? 'Fit' : state.zoom + '×',
          ),
          h(
            'button',
            {
              title: 'Zoom in',
              'aria-label': 'Zoom in',
              onclick: () => {
                state.zoom = Math.min(8, state.zoom * 2);
                drawCanvas();
              },
            },
            '+',
          ),
        ),
      ),
      h(
        'div',
        { class: 'canvas-tools' },
        h(
          'button',
          {
            class: placing ? 'primary' : '',
            onclick: () => {
              placing = !placing;
              drawCanvas();
            },
          },
          icon('plus'),
          placing ? 'Click to place…' : 'Add location',
        ),
        h(
          'button',
          {
            class: state.pins ? 'on' : '',
            onclick: () => {
              state.pins = !state.pins;
              drawCanvas();
            },
          },
          state.pins ? 'Hide pins' : 'Show pins',
        ),
        comparison
          ? h(
              'button',
              {
                onclick: () => {
                  comparison = null;
                  drawCanvas();
                },
              },
              'Exit compare',
            )
          : null,
      ),
      viewport,
      comparison
        ? h(
            'label',
            { class: 'compare-control' },
            'Previous ←',
            h('input', {
              type: 'range',
              min: 0,
              max: 100,
              value: comparePercent,
              'aria-label': 'Compare layouts',
              oninput: (e) => {
                comparePercent = +e.target.value;
                const image = pic.querySelector('.comparison-image');
                image.style.clipPath = `inset(0 ${100 - comparePercent}% 0 0)`;
                pic.querySelector('.comparison-line').style.left = comparePercent + '%';
              },
            }),
            '→ Current',
          )
        : null,
      h(
        'div',
        { class: 'canvas-footer' },
        h('span', {}, icon('map'), `${m.cells[0]} × ${m.cells[1]} squares`),
        h('span', {}, key.areas.length + ' locations'),
        h('span', {}, m.doors || 0, ' doors · ', m.lights || 0, ' lights'),
      ),
    );
    viewport.scrollLeft = scroll[0];
    viewport.scrollTop = scroll[1];
  };
  const section = (title, body, open = false) =>
    h('details', { class: 'inspector-section', open }, h('summary', {}, h('b', {}, title)), body);
  const createEntry = (type) => {
    if (!selected) return;
    const f = { name: '', public: '', notes: '' };
    modal(
      'Create ' + (type === 'npc' ? 'NPC' : 'item'),
      'Link this entry to ' + selected.name + '.',
      h(
        'div',
        {},
        formInput(f, 'name', 'Name'),
        formInput(f, 'public', 'Description', { type: 'textarea', rows: 3 }),
        formInput(f, 'notes', type === 'npc' ? 'Voice, motive & stats' : 'Item mechanics', {
          type: 'textarea',
          rows: 3,
        }),
      ),
      async () => {
        if (!f.name.trim()) throw new Error('Enter a name.');
        const id = uid(type);
        c.entries.push({
          id,
          type,
          ...f,
          group: '',
          status: '',
          secrets: '',
          image: '',
          files: [],
          tags: [],
          map: slugArg,
          area: selected.n,
        });
        selected[type === 'npc' ? 'npcs' : 'items'].push(id);
        save('codex');
        save(keyName);
        drawInspector();
      },
      'Create & link',
    );
  };
  const generateOne = (kind) => {
    const f = { instruction: `Create a ${kind} for ${selected.name}, based on the map brief.` };
    modal(
      'Generate ' + kind,
      'Review the AI proposal before adding it to this location.',
      formInput(f, 'instruction', 'What should the AI create?', { type: 'textarea' }),
      async () => {
        await flushAll();
        await post('/api/maps/' + slugArg + '/populate', { ...f, area: selected.n, kind });
        await poll();
        workspace.workflows = (await api('/api/maps/' + slugArg + '/workspace')).workflows;
        tabName = 'workflow';
        drawInspector();
      },
      'Draft proposal',
    );
  };
  const drawLocations = () => {
    const choose = h(
      'select',
      {
        'aria-label': 'Select map location',
        onchange: (e) => {
          selected = key.areas.find((a) => a.n === +e.target.value);
          remember();
          drawCanvas();
          drawInspector();
        },
      },
      key.areas.map((a) =>
        h('option', { value: a.n, selected: a.n === selected?.n }, `${a.n}. ${a.name}`),
      ),
    );
    if (!selected) {
      render(
        panel,
        h(
          'div',
          { class: 'empty-state' },
          icon('map'),
          h('h3', {}, 'Mark the interesting places'),
          h(
            'p',
            {},
            'Add a location pin on the map, then fill it with people, discoveries and events.',
          ),
        ),
      );
      return;
    }
    const a = selected;
    for (const k of ['npcs', 'items', 'journal', 'images', 'events', 'loot', 'threads'])
      a[k] = a[k] || [];
    const links = (ids) =>
      h(
        'div',
        { class: 'linked-entries' },
        ids.map((id) => {
          const e = c.entries.find((e) => e.id === id);
          return e
            ? h(
                'a',
                { href: '#/codex/' + id },
                e.image
                  ? h('img', { src: fileUrl(e.image), alt: '' })
                  : icon(e.type === 'item' ? 'folder' : 'people'),
                h('span', {}, h('b', {}, e.name), h('small', {}, e.type)),
                icon('arrow'),
              )
            : null;
        }),
      );
    render(
      panel,
      h('div', { class: 'location-select' }, h('label', {}, 'LOCATION'), choose),
      h(
        'div',
        { class: 'location-heading' },
        badge('#' + a.n),
        h('h2', {}, a.name),
        h('span', { class: 'muted' }, a.kind),
      ),
      section(
        'Description & encounter',
        h(
          'div',
          {},
          h(
            'div',
            { class: 'two' },
            field(keyName, a, 'name', { label: 'Name' }),
            field(keyName, a, 'kind', { label: 'Kind' }),
          ),
          field(keyName, a, 'text', { label: 'Read-aloud description', type: 'textarea', rows: 4 }),
          field(keyName, a, 'creatures', {
            label: 'Encounter / who is here',
            type: 'textarea',
            rows: 2,
          }),
        ),
        true,
      ),
      section(
        `NPCs · ${a.npcs.length}`,
        h(
          'div',
          {},
          links(a.npcs),
          picker(
            keyName,
            a.npcs,
            () =>
              c.entries
                .filter((e) => ['npc', 'monster', 'pc'].includes(e.type))
                .map((e) => ({ id: e.id, name: e.name })),
            { placeholder: 'Link an existing character…' },
          ),
          h(
            'div',
            { class: 'row compact-actions' },
            h('button', { onclick: () => createEntry('npc') }, '+ Create NPC'),
            h('button', { onclick: () => generateOne('npc') }, icon('spark'), 'Generate'),
          ),
        ),
        true,
      ),
      section(
        `Items & loot · ${a.items.length}`,
        h(
          'div',
          {},
          links(a.items),
          picker(
            keyName,
            a.items,
            () =>
              c.entries.filter((e) => e.type === 'item').map((e) => ({ id: e.id, name: e.name })),
            { placeholder: 'Link an existing item…' },
          ),
          h(
            'div',
            { class: 'row compact-actions' },
            h('button', { onclick: () => createEntry('item') }, '+ Create item'),
            h('button', { onclick: () => generateOne('item') }, icon('spark'), 'Generate'),
          ),
          rowsEditor(
            keyName,
            a.loot,
            [
              ['item', 'Item', 3],
              ['where', 'Where', 2],
              ['value', 'Value', 1],
            ],
            { item: '', where: '', value: '' },
          ),
        ),
      ),
      section(
        `Events · ${a.events.length}`,
        h(
          'div',
          {},
          rowsEditor(
            keyName,
            a.events,
            [
              ['trigger', 'When…', 2],
              ['effect', '…then', 3],
            ],
            { id: uid('event'), trigger: '', effect: '' },
          ),
          h('button', { onclick: () => generateOne('event') }, icon('spark'), 'Generate event'),
        ),
      ),
      section(
        `Journal entries · ${a.journal.length}`,
        h(
          'div',
          {},
          a.journal.map((j, i) =>
            h(
              'div',
              { class: 'journal-card' },
              field(keyName, j, 'title', { label: 'Title' }),
              field(keyName, j, 'text', { label: 'Player facing text', type: 'textarea', rows: 3 }),
              field(keyName, j, 'secrets', { label: 'DM secrets', type: 'textarea', rows: 2 }),
              h(
                'button',
                {
                  class: 'small danger',
                  onclick: () => {
                    a.journal.splice(i, 1);
                    save(keyName);
                    drawInspector();
                  },
                },
                'Remove entry',
              ),
            ),
          ),
          h(
            'div',
            { class: 'row compact-actions' },
            h(
              'button',
              {
                onclick: () => {
                  a.journal.push({ id: uid('journal'), title: 'New entry', text: '', secrets: '' });
                  save(keyName);
                  drawInspector();
                },
              },
              '+ Add entry',
            ),
            h('button', { onclick: () => generateOne('journal') }, icon('spark'), 'Generate'),
          ),
        ),
      ),
      section(
        `Images · ${a.images.length}`,
        h(
          'div',
          {},
          h(
            'div',
            { class: 'area-images' },
            a.images.map((path) =>
              h('img', { src: fileUrl(path), alt: a.name, onclick: () => lightbox(path) }),
            ),
          ),
          h(
            'div',
            { class: 'row compact-actions' },
            imagePicker((path) => {
              a.images.push(path);
              save(keyName);
              drawInspector();
            }),
            h(
              'button',
              {
                onclick: () =>
                  queueArt({
                    map: slugArg,
                    area: a.n,
                    title: a.name,
                    prompt: `${brief.tone}. ${a.name} in ${m.name}. ${a.text} ${brief.prompt}`,
                  }),
              },
              icon('spark'),
              'Queue image',
            ),
          ),
        ),
      ),
      section(
        `Story threads · ${a.threads.length}`,
        h(
          'div',
          {},
          picker(keyName, a.threads, () => t.threads.map((x) => ({ id: x.id, name: x.title })), {
            placeholder: 'Connect a story thread…',
          }),
          h('a', { href: '#/threads' }, 'Manage thread statuses →'),
        ),
      ),
    );
  };
  const drawBrief = () => {
    render(
      panel,
      h('div', { class: 'eyebrow' }, 'MAP BRIEF'),
      h('h2', {}, 'The story behind this place'),
      field('mapbrief/' + slugArg, brief, 'prompt', {
        label: 'Map prompt',
        type: 'textarea',
        rows: 6,
      }),
      field('mapbrief/' + slugArg, brief, 'tone', { label: 'Atmosphere' }),
      formInput(brief, 'party_level', 'Party level', {
        type: 'number',
        min: 1,
        max: 30,
        change: () => save('mapbrief/' + slugArg),
      }),
      formInput(brief, 'session', 'Session', {
        options: [['', 'Unassigned'], ...S.state.prep.map((id) => [id, id.toUpperCase()])],
        change: () => {
          key.session = brief.session;
          save(keyName);
          save('mapbrief/' + slugArg);
        },
      }),
      h('label', {}, 'CONTENT TARGETS'),
      h(
        'div',
        { class: 'two' },
        ['npcs', 'items', 'journals', 'events'].map((k) =>
          formInput(brief.content, k, k[0].toUpperCase() + k.slice(1), {
            type: 'number',
            min: 0,
            max: 30,
            change: () => save('mapbrief/' + slugArg),
          }),
        ),
      ),
      formInput(brief.content, 'art', 'Queue image briefs', {
        type: 'checkbox',
        change: () => save('mapbrief/' + slugArg),
      }),
      formInput(brief.content, 'threads', 'Propose story threads', {
        type: 'checkbox',
        change: () => save('mapbrief/' + slugArg),
      }),
      h('label', {}, 'Connect existing story threads'),
      picker('mapbrief/' + slugArg, (brief.threads ||= []), () =>
        t.threads.map((x) => ({ id: x.id, name: x.title })),
      ),
      h(
        'button',
        {
          class: 'primary',
          onclick: () =>
            attempt(async () => {
              await flushAll();
              toast('Map brief saved.');
            }),
        },
        'Save brief',
      ),
      h('hr'),
      h('label', {}, 'NOTES FOR THE MAP'),
      field(keyName, key, 'notes', { type: 'textarea', rows: 4 }),
    );
  };
  const revise = () => {
    const f = { instruction: '' };
    modal(
      'Refine this map',
      'Describe what should change. The AI proposes changes to the existing layout; applying them saves a revision first.',
      formInput(f, 'instruction', 'Change request', {
        type: 'textarea',
        rows: 5,
        placeholder:
          'Widen the canal crossing, add a watch post beside the eastern bridge, and scatter debris near the flooded cellars.',
      }),
      async () => {
        await flushAll();
        await post('/api/maps/' + slugArg + '/revise', f);
        await poll();
        tabName = 'workflow';
        const fresh = await api('/api/maps/' + slugArg + '/workspace');
        workspace.workflows = fresh.workflows;
        drawInspector();
      },
      'Draft layout changes',
    );
  };
  const drawWorkflow = () =>
    render(
      panel,
      h('div', { class: 'eyebrow' }, 'AI WORKFLOW'),
      h('h2', {}, 'Bring this map to life'),
      h(
        'p',
        { class: 'muted' },
        'The AI uses your map brief, content targets, area key and campaign context.',
      ),
      h(
        'div',
        { class: 'workflow-actions' },
        h(
          'button',
          {
            class: 'primary',
            onclick: () =>
              attempt(async () => {
                await flushAll();
                await post('/api/maps/' + slugArg + '/populate', {});
                await poll();
                workspace.workflows = (await api('/api/maps/' + slugArg + '/workspace')).workflows;
                drawInspector();
              }),
          },
          icon('spark'),
          'Draft campaign content',
        ),
        h('button', { onclick: revise, disabled: m.imported }, icon('map'), 'Revise layout'),
      ),
      workspace.workflows.length
        ? workspace.workflows.map((w) => workflowCard(w, slugArg, refresh))
        : h(
            'div',
            { class: 'empty-state' },
            h(
              'p',
              {},
              'No AI drafts yet. Start a workflow, or revise the layout from a change request.',
            ),
          ),
    );
  const drawRevisions = () =>
    render(
      panel,
      h('div', { class: 'eyebrow' }, 'ITERATE WITH CONFIDENCE'),
      h('h2', {}, 'Revisions & comparison'),
      h(
        'p',
        { class: 'muted' },
        'Save the plan, area key and preview before making changes. Restoring a revision rebuilds the map from that checkpoint.',
      ),
      h(
        'button',
        {
          onclick: () => {
            const f = { label: 'Before the next change' };
            modal(
              'Save a checkpoint',
              'Keep a named version of the current map.',
              formInput(f, 'label', 'Revision name'),
              async () => {
                await flushAll();
                await post('/api/maps/' + slugArg + '/checkpoint', f);
                workspace.revisions = (await api('/api/maps/' + slugArg + '/workspace')).revisions;
                drawInspector();
              },
              'Save checkpoint',
            );
          },
        },
        icon('clock'),
        'Save checkpoint',
      ),
      workspace.revisions.map((r) =>
        h(
          'div',
          { class: 'revision-card' },
          r.preview ? h('img', { src: fileUrl(r.preview), alt: r.label }) : icon('map'),
          h(
            'div',
            {},
            h('b', {}, r.label),
            h('small', {}, when(r.created)),
            h(
              'div',
              { class: 'row' },
              r.preview
                ? h(
                    'button',
                    {
                      class: 'small',
                      onclick: () => {
                        comparison = r.preview;
                        drawCanvas();
                      },
                    },
                    'Compare',
                  )
                : null,
              h(
                'button',
                {
                  class: 'small',
                  onclick: () => {
                    modal(
                      'Restore this revision?',
                      r.label + ' · Your current map will be checkpointed first.',
                      h(
                        'p',
                        {},
                        'This restores the saved layout and area key. Characters and items remain in the campaign codex.',
                      ),
                      async () => {
                        await flushAll();
                        await post('/api/maps/' + slugArg + '/restore', { revision: r.id });
                        await poll();
                        toast('Revision restore queued.');
                      },
                      'Restore & rebuild',
                    );
                  },
                },
                'Restore',
              ),
            ),
          ),
        ),
      ),
      m.imported
        ? h(
            'p',
            { class: 'small-note' },
            'This imported image has no editable grid plan. Checkpoints preserve its location key and brief.',
          )
        : section(
            'Advanced: edit the map plan',
            h(
              'div',
              {},
              h(
                'p',
                { class: 'muted' },
                'Use grid characters to make precise changes. A checkpoint is saved before the plan changes.',
              ),
              h(
                'button',
                {
                  onclick: () =>
                    attempt(async () => {
                      const f = await api('/api/plan/' + slugArg);
                      modal(
                        'Edit map plan',
                        'Save and render the revised layout.',
                        formInput(f, 'text', 'Plan', { type: 'textarea', rows: 16 }),
                        async () => {
                          await api('/api/plan/' + slugArg, {
                            method: 'PUT',
                            headers: { 'Content-Type': 'application/json', 'X-DM-Site': '1' },
                            body: JSON.stringify(f),
                          });
                          await post('/api/forge/' + slugArg, {});
                          await poll();
                        },
                        'Save & render',
                      );
                    }),
                },
                'Open plan editor',
              ),
            ),
          ),
    );
  const drawFoundry = () =>
    render(
      panel,
      h('div', { class: 'eyebrow' }, 'FOUNDRY VTT'),
      h('h2', {}, 'Prepare for the table'),
      h(
        'p',
        { class: 'muted' },
        'Export the latest area key, linked characters, items, journal pages and images. Run the import macro in Foundry to apply them to your world.',
      ),
      h(
        'div',
        { class: 'foundry-card' },
        icon('globe'),
        h('b', {}, S.state.campaign),
        badge(m.in_foundry ? 'Export available' : 'Not exported', m.in_foundry ? 'good' : ''),
      ),
      h(
        'button',
        {
          class: 'primary',
          onclick: () =>
            attempt(async () => {
              await flushAll();
              await post('/api/maps/' + slugArg + '/export', {});
              await load('maps/index', { items: [] });
              toast('Export updated in your selected Foundry Data folder.');
            }),
        },
        icon('upload'),
        'Update Foundry export',
      ),
      h(
        'a',
        {
          class: 'btn',
          href: fileUrl('DM/forge/foundry-import-macro.js'),
          download: 'campaign-studio-import.js',
        },
        'Download import macro',
      ),
      h('a', { href: '#/settings' }, 'Choose Foundry world & providers →'),
      h('hr'),
      h(
        'div',
        { class: 'row' },
        h('a', { class: 'btn', href: fileUrl(m.scene), download: '' }, 'Scene JSON'),
        h('a', { class: 'btn', href: fileUrl(m.image), download: '' }, 'Map image'),
      ),
      h(
        'p',
        { class: 'small-note' },
        'Drafts stay in the studio until you apply them. Foundry documents are created through its own import macro.',
      ),
    );
  const drawInspector = () => {
    S.studioTabs[slugArg] = tabName;
    render(
      tabs,
      ...[
        ['locations', 'Locations'],
        ['brief', 'Brief'],
        ['workflow', 'AI workflow'],
        ['revisions', 'Revisions'],
        ['foundry', 'Foundry'],
      ].map(([k, l]) =>
        h(
          'button',
          {
            class: tabName === k ? 'on' : '',
            onclick: () => {
              tabName = k;
              drawInspector();
            },
          },
          l,
        ),
      ),
    );
    (
      ({
        locations: drawLocations,
        brief: drawBrief,
        workflow: drawWorkflow,
        revisions: drawRevisions,
        foundry: drawFoundry,
      })[tabName] || drawLocations
    )();
  };
  inspector.append(tabs, panel);
  drawCanvas();
  drawInspector();
  render(
    main,
    h(
      'div',
      { class: 'map-page-head' },
      h(
        'div',
        {},
        h('a', { class: 'breadcrumb', href: '#/maps' }, 'Maps & locations /'),
        h('h1', {}, m.name),
        h(
          'div',
          { class: 'row' },
          badge(m.theme),
          badge(key.stocked ? 'Populated' : 'Layout ready', key.stocked ? 'good' : ''),
          m.session ? badge(m.session.toUpperCase()) : null,
        ),
      ),
      h(
        'div',
        { class: 'row' },
        h(
          'button',
          {
            onclick: () => {
              tabName = 'revisions';
              drawInspector();
            },
          },
          icon('clock'),
          'Revisions',
        ),
        h('button', { onclick: revise, disabled: m.imported }, icon('spark'), 'Refine map'),
        h(
          'button',
          {
            class: 'primary',
            onclick: () => {
              tabName = 'foundry';
              drawInspector();
            },
          },
          'Prepare for Foundry',
          icon('arrow'),
        ),
      ),
    ),
    S.jobs
      .filter((j) => j.slug === slugArg && ['running', 'queued'].includes(j.status))
      .map(jobBox),
    h('div', { class: 'studio-map-layout' }, pic, inspector),
  );
}

function workflowCard(w, slugArg, onApplied = () => route(true)) {
  const working = w.status === 'running';
  const ready = w.status === 'review';
  const draft = w.draft;
  const labels = {
    layout: 'Layout design',
    revision: 'Layout revision',
    content: 'Campaign content',
  };
  const stageIndex = w.status === 'applied' ? 3 : ready ? 2 : working ? 1 : 0;
  return h(
    'div',
    { class: 'workflow-card' },
    h(
      'div',
      { class: 'spread' },
      h('b', {}, labels[w.kind] || w.kind),
      badge(w.status, w.status === 'applied' ? 'good' : ready ? 'review' : ''),
    ),
    h(
      'div',
      { class: 'workflow-track' },
      ['Brief', 'Draft', 'Review', 'Apply'].map((label, i) =>
        h(
          'span',
          { class: i <= stageIndex ? 'reached' : '' },
          h('i', {}, i < stageIndex ? '✓' : i + 1),
          label,
        ),
      ),
    ),
    w.instruction ? h('p', { class: 'muted' }, w.instruction) : null,
    w.error ? h('p', { class: 'error-text' }, w.error) : null,
    working
      ? h(
          'p',
          { class: 'muted' },
          'The AI is drafting a proposal. You can continue working elsewhere in the campaign.',
        )
      : null,
    draft
      ? h(
          'div',
          {},
          h('p', {}, draft.summary),
          w.preview
            ? h('img', {
                class: 'draft-preview',
                src: fileUrl(w.preview),
                alt: 'Proposed layout preview',
              })
            : null,
          w.kind === 'content'
            ? h(
                'div',
                {},
                h(
                  'div',
                  { class: 'draft-counts' },
                  ['npcs', 'items', 'journals', 'events', 'threads'].map((k) =>
                    badge(`${draft[k]?.length || 0} ${k}`),
                  ),
                ),
                h(
                  'details',
                  { class: 'draft-details' },
                  h('summary', {}, 'Review proposed content'),
                  ['npcs', 'items', 'journals', 'events', 'threads'].flatMap((k) =>
                    (draft[k] || []).map((e) =>
                      h(
                        'div',
                        { class: 'draft-entry' },
                        h('b', {}, e.name || e.title),
                        badge('Area ' + e.area),
                        h('p', {}, e.public || e.text || e.detail || e.trigger),
                        e.effect ? h('p', {}, e.effect) : null,
                        e.notes ? h('p', { class: 'muted' }, e.notes) : null,
                        e.secrets
                          ? h('details', {}, h('summary', {}, 'DM secret'), h('p', {}, e.secrets))
                          : null,
                      ),
                    ),
                  ),
                ),
              )
            : null,
          w.kind === 'content'
            ? h(
                'details',
                { class: 'draft-details' },
                h('summary', {}, 'Review location descriptions'),
                draft.areas.map((a) =>
                  h(
                    'div',
                    { class: 'draft-entry' },
                    h('b', {}, 'Area ' + a.n),
                    h('p', {}, a.text),
                    h('p', { class: 'muted' }, a.creatures),
                  ),
                ),
              )
            : null,
          draft.warnings?.length
            ? h(
                'details',
                {},
                h('summary', {}, draft.warnings.length + ' layout warnings'),
                h('pre', { class: 'file' }, draft.warnings.join('\n')),
              )
            : null,
        )
      : null,
    h(
      'div',
      { class: 'row compact-actions' },
      ready
        ? h(
            'button',
            {
              class: 'primary',
              onclick: () =>
                attempt(async () => {
                  await flushAll();
                  await post('/api/workflow/' + w.id + '/apply', {});
                  toast(
                    w.kind === 'content'
                      ? 'Campaign content applied.'
                      : 'Layout accepted. Rendering the detailed map.',
                  );
                  await poll();
                  await onApplied();
                }),
            },
            icon('check'),
            w.kind === 'content' ? 'Apply to campaign' : 'Apply & render',
          )
        : null,
      ready
        ? h(
            'button',
            {
              onclick: () => {
                const f = { instruction: '' };
                modal(
                  'Refine this proposal',
                  'The AI receives the current proposal and your requested changes.',
                  formInput(f, 'instruction', 'What should change?', { type: 'textarea', rows: 4 }),
                  async () => {
                    await post('/api/workflow/' + w.id + '/feedback', f);
                    await poll();
                    route(true);
                  },
                  S.state.claude ? 'Draft refinement' : 'Prepare refinement prompt',
                );
              },
            },
            'Refine proposal',
          )
        : null,
      S.state.claude && ['queued', 'ready', 'failed'].includes(w.status)
        ? h(
            'button',
            {
              onclick: () =>
                attempt(async () => {
                  await post('/api/workflow/' + w.id + '/run', {});
                  await poll();
                  route(true);
                }),
            },
            icon('spark'),
            w.status === 'failed' ? 'Retry AI draft' : 'Run with Claude',
          )
        : null,
      !working && w.status !== 'applied'
        ? h(
            'a',
            {
              class: 'btn small',
              href: '/api/workflow/' + w.id + '/pack',
              download: 'map-workflow-' + w.id + '.json',
            },
            'Export prompt pack',
          )
        : null,
      !working && w.status !== 'applied'
        ? h(
            'button',
            {
              class: 'small',
              onclick: () => {
                const f = { text: '' };
                modal(
                  'Import an AI proposal',
                  'Paste the JSON proposal produced from this workflow’s prompt pack.',
                  formInput(f, 'text', 'Proposal JSON', { type: 'textarea', rows: 10 }),
                  async () => {
                    await post('/api/workflow/' + w.id + '/stage', { draft: JSON.parse(f.text) });
                    route(true);
                  },
                  'Validate & review',
                );
              },
            },
            'Import proposal',
          )
        : null,
    ),
  );
}

function foundryBackupCard(hasWorld) {
  const form = { destination: '', path: '', restore: '', closed: false };
  const scan = h('div', { class: 'provider-status backup-status', role: 'status' }, 'Scanning…');
  const result = h('div', { class: 'backup-result', role: 'status' });
  const destination = formInput(form, 'destination', 'Backup destination folder', {
    placeholder: 'Choose a folder outside Foundry User Data',
    help: 'The app creates a new, timestamped backup folder here. Keep it on a separate drive if possible.',
  });
  const backupPath = formInput(form, 'path', 'Backup folder to verify or restore', {
    help: 'A successful backup fills this in automatically. You can paste an earlier backup folder.',
  });
  const restorePath = formInput(form, 'restore', 'New restore test folder', {
    help: 'Must not exist yet. The live Foundry User Data folder is never overwritten.',
  });
  const showResult = (...content) => render(result, ...content);
  const refresh = async () => {
    render(scan, 'Scanning Foundry User Data…');
    try {
      const info = await api('/api/foundry/backup/plan');
      if (!form.destination) {
        form.destination = info.suggested_destination;
        destination.querySelector('input').value = form.destination;
      }
      render(
        scan,
        h('b', {}, `${info.world.title} · Foundry ${info.world.foundry_version || 'unknown'}`),
        h(
          'span',
          {},
          `${info.files.toLocaleString()} files · ${(info.bytes / 1024 ** 3).toFixed(2)} GB`,
        ),
        h('span', { class: 'backup-path' }, info.user_data),
        info.foundry_processes.length
          ? h('strong', { class: 'error-text' }, 'Foundry is running. Close it before backup.')
          : h('span', {}, 'No running Foundry process detected.'),
      );
    } catch (error) {
      render(scan, h('span', { class: 'error-text' }, error.message));
    }
  };
  const backupButton = h(
    'button',
    {
      class: 'primary',
      disabled: !hasWorld,
      onclick: () =>
        attempt(async () => {
          backupButton.disabled = true;
          showResult(
            'Copying and verifying the complete Foundry User Data folder. Keep this page open…',
          );
          try {
            const copy = await post('/api/foundry/backup/create', {
              destination: form.destination,
              confirmed_closed: form.closed,
            });
            form.path = copy.path;
            form.restore = copy.path + '-restore-test';
            backupPath.querySelector('input').value = form.path;
            restorePath.querySelector('input').value = form.restore;
            showResult(
              h('b', {}, 'Backup verified: '),
              h('span', { class: 'backup-path' }, copy.path),
              h('p', {}, `${copy.files.toLocaleString()} files checked by SHA-256.`),
            );
            await refresh();
          } catch (error) {
            showResult(h('span', { class: 'error-text' }, error.message));
            throw error;
          } finally {
            backupButton.disabled = false;
          }
        }),
    },
    'Create and verify full backup',
  );
  const verifyButton = h(
    'button',
    {
      onclick: () =>
        attempt(async () => {
          showResult('Checking every file in the backup…');
          try {
            const check = await post('/api/foundry/backup/verify', { path: form.path });
            showResult(
              `Backup verified: ${check.files.toLocaleString()} files.`,
              h('br'),
              check.path,
            );
          } catch (error) {
            showResult(h('span', { class: 'error-text' }, error.message));
            throw error;
          }
        }),
    },
    'Verify backup again',
  );
  const rehearseButton = h(
    'button',
    {
      onclick: () =>
        attempt(async () => {
          rehearseButton.disabled = true;
          showResult('Creating and verifying an isolated restore copy…');
          try {
            const restored = await post('/api/foundry/backup/rehearse', {
              path: form.path,
              destination: form.restore,
            });
            showResult(
              h('b', {}, 'Restore copy verified: '),
              h('span', { class: 'backup-path' }, restored.path),
              h('p', { class: 'backup-path' }, `Restore receipt: ${restored.receipt_path}`),
              h(
                'p',
                {},
                `Next, open this copy with Foundry ${restored.world.core_version || 'the original version'} using its separate User Data path. Confirm the world, maps and assets load before upgrading the live world.`,
              ),
            );
          } catch (error) {
            showResult(h('span', { class: 'error-text' }, error.message));
            throw error;
          } finally {
            rehearseButton.disabled = false;
          }
        }),
    },
    'Create restore test copy',
  );
  const card = h(
    'section',
    { class: 'card backup-card' },
    h('div', { class: 'section-icon' }, icon('clock')),
    h('h2', {}, 'Foundry backup and restore test'),
    h(
      'p',
      { class: 'muted' },
      'Before upgrading a Foundry world, take a Snapshot in Foundry Setup. Then close Foundry and copy its complete User Data folder here. The full copy includes assets outside world packages.',
    ),
    scan,
    h('button', { onclick: () => attempt(refresh) }, 'Refresh scan'),
    destination,
    formInput(form, 'closed', 'I have closed Foundry VTT', { type: 'checkbox' }),
    backupButton,
    h('hr'),
    backupPath,
    restorePath,
    h('div', { class: 'row' }, verifyButton, rehearseButton),
    result,
    h(
      'p',
      { class: 'small-note' },
      'Keep your current Foundry installer for rollback. Checksums compare copied files with the backup manifest; the restore is fully tested only after you open the isolated copy in the original Foundry version. This feature does not upgrade or change the live world.',
    ),
  );
  if (hasWorld) refresh();
  else render(scan, 'Save a local Foundry world in Settings to scan its User Data.');
  return card;
}

function foundryUpgradeCard(hasWorld) {
  const form = { backup: '', disabled: '', approved: '' };
  const cloneForm = { report: '', receipt: '', destination: '', inspected: false, reviewed: false };
  const reviewForm = { plan: '', confirmed: false };
  const auditForm = {
    review: '',
    confirmed: false,
    launch: false,
    scenes: false,
    journals: false,
    actorsItems: false,
    modules: false,
  };
  let inventory = null;
  let cloneInventory = null;
  let migratedInventory = null;
  const status = h(
    'div',
    { class: 'provider-status', role: 'status' },
    'Import a GM inventory first.',
  );
  const output = h('div', { class: 'upgrade-report', 'aria-live': 'polite' });
  const cloneOutput = h('div', { class: 'upgrade-report', role: 'status' });
  const reviewOutput = h('div', { class: 'upgrade-report', role: 'status' });
  const auditOutput = h('div', { class: 'upgrade-report', role: 'status' });
  const reportInput = formInput(cloneForm, 'report', 'Saved compatibility report', {
    help: 'The latest report fills this in automatically. You can paste an earlier report path.',
  });
  const cloneDestinationInput = formInput(cloneForm, 'destination', 'New upgrade clone folder', {
    help: 'Must be a new path, separate from the live world, backup and restore test copy.',
  });
  const reviewPlanInput = formInput(reviewForm, 'plan', 'Saved clone plan', {
    help: 'The latest clone plan fills this in automatically. You can paste an earlier plan path.',
  });
  const auditReviewInput = formInput(auditForm, 'review', 'Passing v12 clone review', {
    help: 'A passing review fills this in automatically. You can paste an earlier review path.',
  });
  const input = h('input', {
    type: 'file',
    accept: '.json,application/json',
    onchange: (event) =>
      attempt(async () => {
        const file = event.target.files?.[0];
        if (!file) return;
        inventory = null;
        if (file.size > 2 * 1024 * 1024) throw new Error('The inventory must be under 2 MB.');
        inventory = JSON.parse(await file.text());
        if (inventory.format !== 'campaign-studio-foundry-upgrade-inventory')
          throw new Error('Choose the GM upgrade inventory export.');
        render(
          status,
          `${inventory.world?.title || 'World'} · Foundry ${inventory.world?.coreVersion || '?'} · ${inventory.modules?.filter((m) => m.enabled).length || 0} enabled modules`,
        );
        render(output);
      }),
  });
  const ids = (value) =>
    value
      .split(',')
      .map((id) => id.trim())
      .filter(Boolean);
  const compatibility = (value) =>
    value
      ? `Foundry ${value.minimum || '?'}–${value.maximum || 'later'}, verified ${value.verified || 'unknown'}`
      : 'No compatible release selected';
  const run = h(
    'button',
    {
      class: 'primary',
      disabled: !hasWorld,
      onclick: () =>
        attempt(async () => {
          if (!inventory) throw new Error('Import the GM inventory first.');
          run.disabled = true;
          render(output, h('p', {}, 'Verifying the backup and checking Foundry package releases…'));
          try {
            const report = await post('/api/foundry/upgrade/report', {
              inventory,
              backup_path: form.backup,
              disabled_modules: ids(form.disabled),
              approved_dependencies: ids(form.approved),
            });
            cloneForm.report = report.report_path;
            reportInput.querySelector('input').value = cloneForm.report;
            if (!cloneForm.destination) {
              cloneForm.destination = report.backup_path + '-upgrade-clone';
              cloneDestinationInput.querySelector('input').value = cloneForm.destination;
            }
            render(
              output,
              h(
                'h3',
                {},
                report.recommended_build ? `Foundry ${report.recommended_build}` : 'No full match',
              ),
              h(
                'p',
                {},
                report.recommended_build
                  ? `${report.needs_clone_testing ? 'Needs clone testing. ' : ''}System ${report.system.id}: ${report.system.original_version} → ${report.system.selected_version}.`
                  : 'Stay on v12 or explicitly choose modules to disable, then run the report again.',
              ),
              report.newest_all_verified_build
                ? h(
                    'p',
                    {},
                    `Newest build with all selected packages verified: ${report.newest_all_verified_build}.`,
                  )
                : report.recommended_build
                  ? h('p', {}, 'No build has verification declared by every selected package.')
                  : null,
              report.locked_changes.length
                ? h(
                    'p',
                    { class: 'error-text' },
                    `Locked packages need an explicit unlock in the clone before installation: ${report.locked_changes.join(', ')}.`,
                  )
                : null,
              report.requires_gm_choice
                ? h(
                    'p',
                    { class: 'error-text' },
                    'The report includes excluded modules or explicit GM choices. Review these before changing a clone.',
                  )
                : null,
              report.activation_discrepancies?.length
                ? h(
                    'p',
                    { class: 'error-text' },
                    `Module configuration and active state differ for: ${report.activation_discrepancies.join(', ')}. Check these modules in the v12 clone.`,
                  )
                : null,
              h('h4', {}, 'Installed module decisions'),
              h(
                'ul',
                {},
                ...report.modules.map((module) =>
                  h(
                    'li',
                    {},
                    h('b', {}, module.id),
                    ` · ${module.original_version} → ${module.selected_version || 'none'} · enabled ${module.original_enabled ? 'yes' : 'no'} → ${module.proposed_enabled === null ? 'undecided' : module.proposed_enabled ? 'yes' : 'no'} · ${module.directory_status} · ${module.disabled_reason || module.activation_reason || 'retain in clone'}`,
                    module.directory_url
                      ? h(
                          'a',
                          {
                            href: module.directory_url,
                            target: '_blank',
                            rel: 'noopener noreferrer',
                          },
                          ' Directory',
                        )
                      : null,
                    module.selected_manifest
                      ? h(
                          'small',
                          {},
                          ` · ${compatibility(module.selected_compatibility)} · ${module.selected_manifest}`,
                        )
                      : null,
                  ),
                ),
              ),
              h('h4', {}, 'Required dependencies'),
              h(
                'ul',
                {},
                ...(report.dependencies.length
                  ? report.dependencies.map((dependency) =>
                      h(
                        'li',
                        {},
                        `${dependency.id} ${dependency.version} · ${dependency.manifest}`,
                      ),
                    )
                  : [h('li', {}, 'No additional module activations selected.')]),
              ),
              h(
                'details',
                {},
                h('summary', {}, `All ${report.candidates.length} candidate builds and blockers`),
                h(
                  'ul',
                  {},
                  ...report.candidates.map((candidate) =>
                    h(
                      'li',
                      {},
                      `${candidate.build}: ${candidate.full_match ? (candidate.all_verified ? 'full match, verified' : 'full match, needs clone testing') : candidate.blockers.join(' ')}`,
                    ),
                  ),
                ),
              ),
              h(
                'p',
                { class: 'small-note' },
                `Inventory and report saved beside the verified backup: ${report.report_path}`,
              ),
              h(
                'p',
                { class: 'small-note' },
                'This report does not disable modules or migrate the world. Test the restored v12 copy, then disable excluded modules in that clone before its first launch in a newer Foundry build.',
              ),
            );
          } finally {
            run.disabled = false;
          }
        }),
    },
    'Build compatibility report',
  );
  const prepareClone = h(
    'button',
    {
      class: 'primary',
      disabled: !hasWorld,
      onclick: () =>
        attempt(async () => {
          prepareClone.disabled = true;
          render(
            cloneOutput,
            'Verifying evidence and copying the v12 backup into a separate clone…',
          );
          try {
            const plan = await post('/api/foundry/upgrade/prepare-clone', {
              report_path: cloneForm.report,
              restore_receipt_path: cloneForm.receipt,
              destination: cloneForm.destination,
              confirmed_v12_restore: cloneForm.inspected,
              confirmed_report: cloneForm.reviewed,
            });
            reviewForm.plan = plan.plan_path;
            reviewPlanInput.querySelector('input').value = plan.plan_path;
            render(
              cloneOutput,
              h('h4', {}, `v12 clone prepared for Foundry ${plan.target_build}`),
              h('p', { class: 'backup-path' }, plan.clone_path),
              h('p', {}, `Saved plan: ${plan.plan_path}`),
              h(
                'p',
                {},
                plan.disable_in_v12.length
                  ? `In Foundry v12 Manage Modules, disable: ${plan.disable_in_v12.map((item) => `${item.id} (${item.reason})`).join('; ')}.`
                  : 'The report has no enabled modules to disable.',
              ),
              h(
                'p',
                {},
                'Launch only this clone with Foundry v12. Save and reload its module configuration, then confirm the excluded modules are off before opening the clone in a newer Foundry build. Migration is not yet available in Studio.',
              ),
            );
          } finally {
            prepareClone.disabled = false;
          }
        }),
    },
    'Prepare isolated v12 clone',
  );
  const cloneInventoryInput = h('input', {
    type: 'file',
    accept: '.json,application/json',
    onchange: (event) =>
      attempt(async () => {
        const file = event.target.files?.[0];
        if (!file) return;
        cloneInventory = null;
        if (file.size > 2 * 1024 * 1024) throw new Error('The inventory must be under 2 MB.');
        cloneInventory = JSON.parse(await file.text());
        if (cloneInventory.format !== 'campaign-studio-foundry-upgrade-inventory')
          throw new Error('Choose a GM upgrade inventory export from the v12 clone.');
        render(
          reviewOutput,
          `Imported ${cloneInventory.world?.title || 'world'} · Foundry ${cloneInventory.world?.coreVersion || '?'} · ${cloneInventory.enabledModuleIds?.length || 0} configured modules`,
        );
      }),
  });
  const reviewClone = h(
    'button',
    {
      class: 'primary',
      disabled: !hasWorld,
      onclick: () =>
        attempt(async () => {
          if (!cloneInventory) throw new Error('Import a fresh inventory from the v12 clone.');
          reviewClone.disabled = true;
          render(reviewOutput, 'Checking the clone against its saved plan and backup…');
          try {
            const review = await post('/api/foundry/upgrade/review-clone', {
              plan_path: reviewForm.plan,
              inventory: cloneInventory,
              confirmed_clone: reviewForm.confirmed,
            });
            if (review.status === 'v12_modules_reviewed') {
              auditForm.review = review.review_path;
              auditReviewInput.querySelector('input').value = review.review_path;
            }
            render(
              reviewOutput,
              h(
                'h4',
                {},
                review.status === 'v12_modules_reviewed'
                  ? 'v12 module review passed'
                  : 'v12 module review blocked',
              ),
              h('p', {}, `Expected enabled: ${review.expected_enabled.join(', ') || 'none'}.`),
              h('p', {}, `Saved configuration: ${review.configured_enabled.join(', ') || 'none'}.`),
              h('p', {}, `Active in Foundry: ${review.runtime_active.join(', ') || 'none'}.`),
              ...review.blockers.map((blocker) => h('p', { class: 'error-text' }, blocker)),
              review.locked_changes.length
                ? h(
                    'p',
                    { class: 'error-text' },
                    `Unlock these packages in the isolated installation before changing releases: ${review.locked_changes.join(', ')}.`,
                  )
                : null,
              review.status === 'v12_modules_reviewed'
                ? h(
                    'details',
                    {},
                    h('summary', {}, 'Selected releases to install for the target build'),
                    h(
                      'ul',
                      {},
                      ...review.selected_releases.map((item) =>
                        h('li', {}, `${item.type} ${item.id} ${item.version} · ${item.manifest}`),
                      ),
                    ),
                  )
                : null,
              h('p', { class: 'backup-path' }, `Saved review: ${review.review_path}`),
              h(
                'p',
                { class: 'small-note' },
                review.status === 'v12_modules_reviewed'
                  ? `Keep the clone isolated. The next steps are to install the plan's selected releases, run Foundry ${review.target_build} on this clone, and manually inspect the migrated world. Studio has not approved migration or cutover.`
                  : 'Resolve the listed differences in the v12 clone, save and reload its module configuration, export a fresh inventory, and review again.',
              ),
            );
          } finally {
            reviewClone.disabled = false;
          }
        }),
    },
    'Review v12 clone modules',
  );
  const migratedInventoryInput = h('input', {
    type: 'file',
    accept: '.json,application/json',
    onchange: (event) =>
      attempt(async () => {
        const file = event.target.files?.[0];
        if (!file) return;
        migratedInventory = null;
        if (file.size > 2 * 1024 * 1024) throw new Error('The inventory must be under 2 MB.');
        migratedInventory = JSON.parse(await file.text());
        if (migratedInventory.phase !== 'migrated-clone')
          throw new Error('Choose the migrated-clone GM audit export.');
        render(
          auditOutput,
          `Imported ${migratedInventory.world?.title || 'world'} · Foundry ${migratedInventory.world?.coreVersion || '?'} · ${migratedInventory.enabledModuleIds?.length || 0} configured modules`,
        );
      }),
  });
  const auditMigration = h(
    'button',
    {
      class: 'primary',
      disabled: !hasWorld,
      onclick: () =>
        attempt(async () => {
          if (!migratedInventory)
            throw new Error('Import an inventory from the migrated clone first.');
          auditMigration.disabled = true;
          render(auditOutput, 'Comparing the migrated clone with the selected package plan…');
          try {
            const audit = await post('/api/foundry/upgrade/audit-migration', {
              review_path: auditForm.review,
              inventory: migratedInventory,
              confirmed_clone: auditForm.confirmed,
              manual_checks: {
                launch: auditForm.launch,
                scenes: auditForm.scenes,
                journals: auditForm.journals,
                actors_items: auditForm.actorsItems,
                modules: auditForm.modules,
              },
            });
            render(
              auditOutput,
              h(
                'h4',
                {},
                audit.status === 'reviewed'
                  ? 'Migrated clone review recorded'
                  : 'Migrated clone review blocked',
              ),
              h('p', {}, `Foundry ${audit.reported_build} · target ${audit.target_build}.`),
              h(
                'p',
                {},
                `System ${audit.selected_system.id} ${audit.installed_system_version || 'missing'} · selected ${audit.selected_system.version}.`,
              ),
              h('p', {}, `Enabled modules: ${audit.configured_enabled.join(', ') || 'none'}.`),
              ...audit.blockers.map((blocker) => h('p', { class: 'error-text' }, blocker)),
              h('p', { class: 'backup-path' }, `Saved audit: ${audit.audit_path}`),
              h(
                'p',
                { class: 'small-note' },
                'Package metadata and GM checks cannot certify module behavior. Keep the v12 backup and installer for rollback. Studio does not cut over the live world.',
              ),
            );
          } finally {
            auditMigration.disabled = false;
          }
        }),
    },
    'Audit migrated clone',
  );
  return h(
    'section',
    { class: 'card upgrade-card' },
    h('div', { class: 'section-icon' }, icon('check')),
    h('h2', {}, 'Foundry upgrade compatibility report'),
    h(
      'p',
      { class: 'muted' },
      'In the original v12 world, run the GM inventory macro. Use a verified offline backup of that same world. This step reads package listings and leaves Foundry unchanged.',
    ),
    h(
      'a',
      { class: 'btn', href: fileUrl('DM/forge/foundry-upgrade-inventory.js'), download: '' },
      'Download GM inventory macro',
    ),
    h('label', {}, 'Import GM inventory JSON', input),
    status,
    formInput(form, 'backup', 'Verified backup folder', {
      help: 'Paste the timestamped backup folder from the backup step above.',
    }),
    h(
      'details',
      {},
      h('summary', {}, 'Explicit GM choices'),
      formInput(form, 'disabled', 'Enabled module IDs to disable in the clone', {
        help: 'Comma-separated IDs. Eligible modules are never dropped automatically.',
      }),
      formInput(form, 'approved', 'Required dependency IDs to enable in the clone', {
        help: 'Comma-separated IDs. Only approve dependencies you intend to activate in the isolated clone.',
      }),
    ),
    run,
    output,
    h('hr'),
    h('h3', {}, 'Prepare an isolated v12 clone'),
    h(
      'p',
      { class: 'muted' },
      'After opening and inspecting the restore test copy in Foundry v12, use its receipt and a reviewed report to make a separate clone. This step does not launch Foundry or change module settings.',
    ),
    reportInput,
    formInput(cloneForm, 'receipt', 'Restore test receipt', {
      help: 'Shown after Create restore test copy. Keep the receipt beside the backup.',
    }),
    cloneDestinationInput,
    formInput(cloneForm, 'inspected', 'I opened and inspected the restore copy in Foundry v12', {
      type: 'checkbox',
    }),
    formInput(cloneForm, 'reviewed', 'I reviewed the report and excluded module decisions', {
      type: 'checkbox',
    }),
    prepareClone,
    cloneOutput,
    h('hr'),
    h('h3', {}, 'Review v12 clone modules'),
    h(
      'p',
      { class: 'muted' },
      'After disabling excluded modules in Foundry v12, save and reload the clone, then run the GM inventory macro there again. This checks the saved and active module states against the plan without changing the clone.',
    ),
    reviewPlanInput,
    h('label', {}, 'Import fresh v12 clone inventory JSON', cloneInventoryInput),
    formInput(reviewForm, 'confirmed', 'I exported this inventory from the isolated v12 clone', {
      type: 'checkbox',
    }),
    reviewClone,
    reviewOutput,
    h('hr'),
    h('h3', {}, 'Audit migrated clone'),
    h(
      'p',
      { class: 'muted' },
      'After manually installing the selected releases and migrating only the isolated clone, run the migrated-clone GM macro in Foundry v13 or v14. Inspect the world before recording these checks.',
    ),
    h(
      'a',
      { class: 'btn', href: fileUrl('DM/forge/foundry-upgrade-audit.js'), download: '' },
      'Download migrated-clone audit macro',
    ),
    auditReviewInput,
    h('label', {}, 'Import migrated-clone inventory JSON', migratedInventoryInput),
    formInput(
      auditForm,
      'confirmed',
      'I exported this inventory from the isolated migrated clone',
      {
        type: 'checkbox',
      },
    ),
    formInput(auditForm, 'launch', 'The migrated clone launches and opens this world', {
      type: 'checkbox',
    }),
    formInput(auditForm, 'scenes', 'I inspected key scenes and map assets', {
      type: 'checkbox',
    }),
    formInput(auditForm, 'journals', 'I inspected key journals', { type: 'checkbox' }),
    formInput(auditForm, 'actorsItems', 'I inspected key actors and items', {
      type: 'checkbox',
    }),
    formInput(auditForm, 'modules', 'I tested retained module behavior', { type: 'checkbox' }),
    auditMigration,
    auditOutput,
  );
}

function foundryWorldPicker(settings) {
  const state = { root: '' };
  const roots = h('div', { class: 'foundry-roots' });
  const choices = h('div', { class: 'world-choices', 'aria-live': 'polite' });
  const rootInput = formInput(state, 'root', 'Foundry User Data folder', {
    placeholder: '…/FoundryVTT',
    help: 'Choose the folder containing Data/worlds. Studio will list its worlds.',
  });
  const scan = async (path = state.root) => {
    render(choices, h('p', { class: 'muted' }, 'Scanning local Foundry worlds…'));
    try {
      const result = await api(
        '/api/foundry/worlds' + (path ? '?root=' + encodeURIComponent(path) : ''),
      );
      state.root = result.root;
      rootInput.querySelector('input').value = result.root;
      render(
        roots,
        ...result.roots.map((root) =>
          h('button', { class: 'world-root', onclick: () => scan(root) }, root),
        ),
      );
      render(
        choices,
        result.worlds.length
          ? result.worlds.map((world) => {
              const id = uid('world');
              return h(
                'label',
                { class: 'world-choice', for: id },
                h('input', {
                  id,
                  type: 'radio',
                  name: 'foundry-world',
                  checked: settings.world_path === world.path,
                  onchange: () => {
                    settings.world_path = world.path;
                  },
                }),
                h(
                  'span',
                  {},
                  h('b', {}, world.title),
                  h(
                    'small',
                    {},
                    `${world.system} · Foundry ${world.foundry_version || 'version unknown'}`,
                  ),
                  h('small', { class: 'world-path' }, world.path),
                ),
              );
            })
          : h('p', { class: 'muted' }, 'No Foundry worlds found in this User Data folder.'),
      );
    } catch (error) {
      render(choices, h('p', { class: 'error-text' }, error.message));
    }
  };
  const picker = h(
    'div',
    { class: 'foundry-world-picker' },
    rootInput,
    h('div', { class: 'row' }, h('button', { onclick: () => scan() }, 'Scan for worlds')),
    roots,
    choices,
    h(
      'details',
      {},
      h('summary', {}, 'Enter a world folder manually'),
      formInput(settings, 'world_path', 'Folder containing world.json'),
    ),
  );
  scan();
  return picker;
}

async function studioWelcome() {
  const result = await api('/api/settings');
  const settings = clone(result.settings);
  if (S.state.onboarding_needed && settings.campaign_name === 'Campaign Studio')
    settings.campaign_name = '';
  let mode = 'existing';
  const instructions = h('div');
  const modeButtons = h('div', { class: 'segmented' });
  const drawMode = () => {
    render(
      modeButtons,
      ...[
        ['existing', 'Connect an existing world'],
        ['new', 'Start a new Foundry world'],
      ].map(([value, label]) =>
        h(
          'button',
          {
            class: mode === value ? 'on' : '',
            onclick: () => {
              mode = value;
              drawMode();
            },
          },
          label,
        ),
      ),
    );
    render(
      instructions,
      mode === 'new'
        ? h(
            'p',
            { class: 'muted' },
            'Create the Foundry world in Foundry Setup and choose its game system there. Return here, scan its User Data folder, then select the new world.',
          )
        : h(
            'p',
            { class: 'muted' },
            'Select your existing Foundry world. This step only connects the Studio project; it does not change that world.',
          ),
    );
  };
  const finish = async (withoutWorld = false) => {
    if (!settings.campaign_name.trim()) throw new Error('Name your Studio project.');
    if (!withoutWorld && !settings.world_path) throw new Error('Select a Foundry world first.');
    if (withoutWorld) settings.world_path = '';
    await post('/api/settings', settings);
    S.state.campaign = settings.campaign_name.trim();
    S.state.onboarding_needed = false;
    initStudio();
    go(withoutWorld ? '#/' : '#/library');
  };
  drawMode();
  render(
    S.view,
    pageHead(
      'WELCOME TO CAMPAIGN STUDIO',
      'Set up your campaign.',
      'Create your Studio project and connect it to one Foundry world.',
    ),
    h(
      'div',
      { class: 'welcome-layout' },
      h(
        'section',
        { class: 'card' },
        h('h2', {}, '1. Name your Studio project'),
        formInput(settings, 'campaign_name', 'Campaign name'),
        h('p', { class: 'small-note' }, 'This Studio installation stores one campaign locally.'),
        h('h2', {}, '2. Connect a Foundry world'),
        modeButtons,
        instructions,
        foundryWorldPicker(settings),
        h(
          'div',
          { class: 'welcome-actions' },
          h(
            'button',
            { class: 'primary', onclick: () => attempt(() => finish()) },
            'Create project and open library',
          ),
          h(
            'button',
            { onclick: () => attempt(() => finish(true)) },
            'Continue without a Foundry world',
          ),
        ),
      ),
      h(
        'aside',
        { class: 'card' },
        h('h3', {}, 'What happens next'),
        h(
          'p',
          {},
          'Browse media in the selected world and import a read-only document snapshot from Foundry.',
        ),
        h(
          'p',
          {},
          'Your journals, NPCs, items and scenes remain in Foundry. Studio drafts and exports stay separate until you apply them as GM.',
        ),
      ),
    ),
  );
}

async function studioLibrary() {
  let kind = S.libraryKind || 'scenes';
  let query = '';
  let offset = 0;
  let requestNumber = 0;
  const tabs = h('div', { class: 'library-tabs' });
  const status = h('div', { class: 'library-status' });
  const list = h('div', { class: 'library-list' });
  const detail = h('div', { class: 'library-detail card' });
  const pager = h('div', { class: 'library-pager' });
  const upload = h('input', {
    type: 'file',
    accept: '.json,application/json',
    hidden: true,
    onchange: async (event) => {
      await attempt(async () => {
        const file = event.target.files?.[0];
        if (!file) return;
        if (file.size > 20 * 1024 * 1024) throw new Error('The snapshot must be under 20 MB.');
        const snapshot = JSON.parse(await file.text());
        const result = await post('/api/foundry/library/import', snapshot);
        toast(
          `Imported ${Object.values(result.counts).reduce((a, b) => a + b, 0)} document summaries.`,
        );
        await refresh();
      });
      upload.value = '';
    },
  });
  const showDetail = (item) => {
    const image =
      kind === 'assets' && item.type.startsWith('image/')
        ? h('img', {
            class: 'library-preview',
            src: '/api/foundry/asset?path=' + encodeURIComponent(item.path),
            alt: item.name,
          })
        : null;
    render(
      detail,
      h('div', { class: 'eyebrow' }, kind === 'assets' ? 'FOUNDRY MEDIA' : 'FOUNDRY SNAPSHOT'),
      h('h2', {}, item.name),
      item.folder ? h('p', { class: 'muted' }, 'Folder: ' + item.folder) : null,
      item.type && kind !== 'assets' ? badge(item.type) : null,
      image,
      item.summary ? h('p', { class: 'library-text' }, item.summary) : null,
      ...(item.pages || []).map((page) =>
        h(
          'section',
          { class: 'library-page' },
          h('h3', {}, page.name),
          page.text ? h('p', { class: 'library-text' }, page.text) : null,
          page.image ? h('small', { class: 'muted' }, 'Image: ' + page.image) : null,
        ),
      ),
      item.image && kind !== 'assets'
        ? h('small', { class: 'muted world-path' }, 'Image: ' + item.image)
        : null,
      h('small', { class: 'muted world-path' }, item.uuid || item.path || item.id),
      h(
        'p',
        { class: 'small-note' },
        kind === 'assets'
          ? 'Read-only. Refresh this page to see media files added later.'
          : 'Read-only. Refresh the snapshot in Foundry to see later changes.',
      ),
    );
  };
  const refresh = async () => {
    const current = ++requestNumber;
    render(list, h('p', { class: 'muted' }, 'Loading World Library…'));
    const result = await api(
      '/api/foundry/library?' +
        new URLSearchParams({ kind, q: query, offset: String(offset), limit: '60' }),
    );
    if (current !== requestNumber) return;
    if (!result.world) {
      render(
        status,
        h('p', { class: 'muted' }, 'Connect a Foundry world in Settings to browse its library.'),
      );
      render(list);
      render(detail);
      render(pager);
      return;
    }
    render(
      status,
      h('b', {}, result.world.title),
      h(
        'span',
        {},
        ` · ${result.world.system} · Foundry ${result.world.foundry_version || 'unknown'}`,
      ),
      h(
        'small',
        {},
        result.snapshot
          ? `Document snapshot: ${when(result.snapshot.exported_at)} · Foundry ${result.snapshot.core_version}`
          : 'No document snapshot imported yet. Media files can still be browsed.',
      ),
    );
    render(
      tabs,
      ...[
        ['scenes', 'Scenes'],
        ['journals', 'Journals'],
        ['actors', 'Actors & NPCs'],
        ['items', 'Items'],
        ['assets', 'Media'],
      ].map(([value, label]) =>
        h(
          'button',
          {
            class: kind === value ? 'on' : '',
            onclick: () => {
              kind = value;
              S.libraryKind = kind;
              offset = 0;
              render(detail, h('p', { class: 'muted' }, 'Select an entry to view it.'));
              attempt(refresh);
            },
          },
          label,
          value !== 'assets' ? ` (${result.counts[value]})` : '',
        ),
      ),
    );
    render(
      list,
      result.items.length
        ? result.items.map((item) =>
            h(
              'button',
              { class: 'library-entry', onclick: () => showDetail(item) },
              h('b', {}, item.name),
              h('small', {}, item.folder || item.path || item.type || ''),
            ),
          )
        : h(
            'p',
            { class: 'muted' },
            kind !== 'assets' && !result.snapshot
              ? 'Import a Foundry document snapshot to browse this category.'
              : 'No entries found.',
          ),
    );
    render(
      pager,
      h('span', { class: 'muted' }, `${result.total} ${kind} found`),
      h(
        'button',
        {
          disabled: offset === 0,
          onclick: () => {
            offset = Math.max(0, offset - 60);
            attempt(refresh);
          },
        },
        'Previous',
      ),
      h(
        'button',
        {
          disabled: offset + 60 >= result.total,
          onclick: () => {
            offset += 60;
            attempt(refresh);
          },
        },
        'Next',
      ),
      result.truncated ? h('small', {}, 'Media scan limited to 20,000 files.') : null,
    );
  };
  render(
    S.view,
    pageHead(
      'CONNECTED FOUNDRY WORLD',
      'World Library',
      'Browse Foundry documents and media before deciding what to build or change.',
      h('a', { class: 'btn', href: '#/settings' }, 'World settings'),
    ),
    status,
    h(
      'section',
      { class: 'card library-import' },
      h(
        'div',
        {},
        h('h3', {}, 'Read existing Foundry documents'),
        h(
          'p',
          { class: 'muted' },
          'In Foundry, run the export Script macro as GM. Then import the downloaded JSON snapshot here. Repeat whenever you want a fresh view.',
        ),
      ),
      h(
        'div',
        { class: 'row' },
        h(
          'a',
          {
            class: 'btn',
            href: fileUrl('DM/forge/foundry-library-export.js'),
            download: 'campaign-studio-library-export.js',
          },
          'Download export macro',
        ),
        upload,
        h('button', { onclick: () => upload.click() }, 'Import snapshot'),
      ),
    ),
    tabs,
    h(
      'div',
      { class: 'library-search' },
      h('input', {
        type: 'search',
        placeholder: 'Search this category…',
        'aria-label': 'Search World Library category',
        oninput: (event) => {
          query = event.target.value;
          offset = 0;
          clearTimeout(S.librarySearchTimer);
          S.librarySearchTimer = setTimeout(() => attempt(refresh), 200);
        },
      }),
    ),
    h('div', { class: 'library-layout' }, h('div', {}, list, pager), detail),
  );
  render(detail, h('p', { class: 'muted' }, 'Select an entry to view it.'));
  await refresh();
}

async function studioSettings() {
  const result = await api('/api/settings');
  const f = clone(result.settings);
  const saved = h(
    'div',
    { class: 'provider-status' },
    result.world
      ? h(
          'div',
          {},
          icon('check'),
          h('b', {}, result.world.title),
          h(
            'span',
            {},
            `${result.world.system} · Foundry ${result.world.foundry_version || 'world detected'}`,
          ),
        )
      : h('span', {}, result.error || 'No Foundry world selected yet.'),
  );
  render(
    S.view,
    pageHead(
      'LOCAL SETUP',
      'Your campaign, your tools.',
      'Connect a Foundry world and choose how AI and artwork are generated.',
    ),
    h(
      'div',
      { class: 'settings-grid' },
      h(
        'section',
        { class: 'card' },
        h('div', { class: 'section-icon' }, icon('globe')),
        h('h2', {}, 'Campaign & Foundry'),
        formInput(f, 'campaign_name', 'Campaign name'),
        foundryWorldPicker(f),
        saved,
        h(
          'p',
          { class: 'small-note' },
          'Changing the linked world does not move Studio content. Campaign changes are applied in Foundry using the import macro.',
        ),
      ),
      h(
        'section',
        { class: 'card' },
        h('div', { class: 'section-icon' }, icon('spark')),
        h('h2', {}, 'AI workflow'),
        badge(
          result.claude ? 'Claude Code detected' : 'Claude Code unavailable',
          result.claude ? 'good' : '',
        ),
        formInput(f.ai, 'model', 'Claude model (optional)', {
          placeholder: 'Use your CLI default',
        }),
        h(
          'p',
          { class: 'muted' },
          'The workflow passes a map brief and campaign context to Claude and validates its structured proposal. You can also export prompt packs for another assistant.',
        ),
        h(
          'p',
          { class: 'small-note' },
          'AI drafts are reviewed before they become campaign content.',
        ),
      ),
      h(
        'section',
        { class: 'card' },
        h('div', { class: 'section-icon' }, icon('image')),
        h('h2', {}, 'Image generation'),
        formInput(f.images, 'endpoint', 'Image API endpoint', {
          placeholder: 'http://127.0.0.1:…/v1/images/generations',
          help: 'Use a local or HTTPS provider accepting model, prompt, size and n, returning data[0].b64_json.',
        }),
        formInput(f.images, 'model', 'Image model'),
        h(
          'div',
          { class: 'two' },
          formInput(f.images, 'key_env', 'API key environment variable'),
          formInput(f.images, 'size', 'Image size', {
            options: ['1024x1024', '1536x1024', '1024x1536'],
          }),
        ),
        badge(
          result.image_key_available
            ? 'API key available'
            : 'Local provider or environment key required',
        ),
        h(
          'p',
          { class: 'small-note' },
          'Keys are read from the server environment. Save only the variable name here. Image generation runs when you press Generate in Image studio.',
        ),
      ),
      h(
        'section',
        { class: 'card' },
        h('div', { class: 'section-icon' }, icon('folder')),
        h('h2', {}, 'Portable source'),
        h(
          'p',
          { class: 'muted' },
          'Package the app code with an empty campaign so other users can run their own studio.',
        ),
        h(
          'button',
          {
            onclick: () =>
              attempt(async () => {
                const r = await post('/api/package', {});
                toast('Source package created.');
                const a = h(
                  'a',
                  { class: 'btn primary', href: fileUrl(r.path), download: '' },
                  'Download source package',
                );
                saved.append(a);
              }),
          },
          'Build clean source package',
        ),
        h(
          'p',
          { class: 'small-note' },
          'The source package excludes campaign data, maps, uploads, credentials and local settings.',
        ),
      ),
      foundryBackupCard(!!result.world),
      foundryUpgradeCard(!!result.world),
    ),
    h(
      'div',
      { class: 'settings-actions' },
      h(
        'button',
        {
          class: 'primary',
          onclick: () =>
            attempt(async () => {
              if (
                result.settings.world_path &&
                f.world_path !== result.settings.world_path &&
                !confirm(
                  'Connect this Studio project to a different Foundry world? Existing Studio maps, NPCs and notes will remain in this project.',
                )
              )
                return;
              await post('/api/settings', f);
              S.state = await api('/api/state');
              initStudio();
              toast('Settings saved.');
              route(true);
            }),
        },
        'Save settings',
      ),
    ),
  );
}

async function studioArt() {
  const art = await doc('art', { items: [] });
  const cfg = await api('/api/settings');
  let filter = S.artFilter || 'all';
  const grid = h('div', { class: 'art-grid' });
  const draw = () => {
    S.artFilter = filter;
    const items = art.items.filter(
      (i) =>
        filter === 'all' || (filter === 'queued' ? i.status !== 'ready' : i.status === 'ready'),
    );
    render(
      grid,
      ...items.map((i) =>
        h(
          'div',
          { class: 'art-studio-card' },
          i.image
            ? h('img', {
                class: 'art-result',
                src: fileUrl(i.image),
                alt: i.title,
                onclick: () => lightbox(i.image),
              })
            : h(
                'div',
                { class: 'art-placeholder' },
                icon('image'),
                h('span', {}, i.status === 'generating' ? 'Generating…' : 'Image brief ready'),
              ),
          h(
            'div',
            { class: 'art-card-content' },
            h(
              'div',
              { class: 'spread' },
              h('h3', {}, i.title),
              badge(i.status, i.status === 'ready' ? 'good' : ''),
            ),
            i.map
              ? h(
                  'a',
                  { class: 'muted', href: '#/maps/' + i.map },
                  i.map + (i.area ? ' · Area ' + i.area : ''),
                )
              : null,
            field('art', i, 'prompt', { type: 'textarea', rows: 3, label: 'Image brief' }),
            i.error ? h('p', { class: 'error-text' }, i.error) : null,
            h(
              'div',
              { class: 'row compact-actions' },
              i.status !== 'generating' && cfg.settings.images.endpoint
                ? h(
                    'button',
                    {
                      class: 'primary',
                      onclick: () =>
                        attempt(async () => {
                          await flushAll();
                          await post('/api/art/generate', { id: i.id });
                          await poll();
                          await load('art', { items: [] });
                          route(true);
                        }),
                    },
                    icon('spark'),
                    i.image ? 'Generate new version' : 'Generate image',
                  )
                : null,
              imagePicker((path) => attachArt(i, path), 'Upload image'),
              i.image
                ? h(
                    'button',
                    {
                      class: 'small',
                      onclick: () => {
                        art.items.unshift({
                          ...i,
                          id: uid('art'),
                          image: '',
                          status: 'queued',
                          created: Date.now(),
                        });
                        save('art');
                        draw();
                      },
                    },
                    'Queue variation',
                  )
                : null,
            ),
          ),
        ),
      ),
    );
    if (!items.length)
      grid.append(
        h(
          'div',
          { class: 'empty-state' },
          icon('image'),
          h('h3', {}, 'Illustrate the moments that matter'),
          h(
            'p',
            {},
            'AI workflows can queue portraits and discoveries, or you can add your own image brief.',
          ),
        ),
      );
  };
  render(
    S.view,
    pageHead(
      'CAMPAIGN ARTWORK',
      'Image studio',
      'Create portraits, objects and scenes with a consistent campaign brief.',
      h('button', { class: 'primary', onclick: () => queueArt() }, icon('plus'), 'New image brief'),
    ),
    !cfg.settings.images.endpoint
      ? h(
          'div',
          { class: 'connection-banner' },
          icon('image'),
          h(
            'div',
            {},
            h('b', {}, 'Connect an image provider to generate here'),
            h(
              'p',
              {},
              'You can use a local image service, an API provider, or upload artwork you already made.',
            ),
          ),
          h('a', { class: 'btn', href: '#/settings' }, 'Configure provider'),
        )
      : null,
    h(
      'div',
      { class: 'filters' },
      [
        ['all', 'All artwork'],
        ['queued', 'To create'],
        ['ready', 'Ready'],
      ].map(([v, l]) =>
        h(
          'button',
          {
            class: filter === v ? 'on' : '',
            onclick: () => {
              filter = v;
              draw();
            },
          },
          l,
        ),
      ),
    ),
    grid,
  );
  draw();
}
function importMapDialog() {
  const f = { name: '', cell: 100, image: '' };
  const status = h('span', { class: 'muted' }, 'No image selected');
  modal(
    'Import a battle map',
    'Upload a map image and set its grid scale. You can add numbered locations and campaign content after import.',
    h(
      'div',
      {},
      formInput(f, 'name', 'Map name'),
      formInput(f, 'cell', 'Pixels per square', { type: 'number', min: 50, max: 300 }),
      h(
        'div',
        { class: 'row' },
        imagePicker((path) => {
          f.image = path;
          status.textContent = 'Image selected';
        }),
        status,
      ),
    ),
    async () => {
      if (!f.image) throw new Error('Choose a map image.');
      const r = await post('/api/maps/import', f);
      await load('maps/index', { items: [] });
      go('#/maps/' + r.slug);
    },
    'Import map',
  );
}
