'use strict';

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
async function studioMaps(arg, context) {
  if (arg === 'new') return newMapStudio(context);
  if (arg) return mapStudio(arg, context);
  const maps = (await context.doc('maps/index', { items: [] })).items;
  let q = S.mapSearch || '',
    filter = S.mapFilter || 'all';
  const pending = await context.api('/api/maps/pending');
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
    context.view,
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
async function newMapStudio(context) {
  const f = S.creation || (S.creation = freshBrief());
  let step = S.creationStep || 0;
  const threads = (await context.doc('threads', { threads: [] })).threads;
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
    context.view,
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

async function mapStudio(slugArg, context) {
  const main = context.view;
  const index = await context.doc('maps/index', { items: [] });
  const m = index.items.find((x) => x.slug === slugArg);
  const workspace = await context.api('/api/maps/' + slugArg + '/workspace');
  let brief = await context.doc(
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
  const key = await context.doc(
    keyName,
    blank('map_key', { map: m.name, session: m.session || '' }),
  );
  const c = await context.doc('codex', { entries: [] });
  const t = await context.doc('threads', { threads: [] });
  const art = await context.doc('art', { items: [] });
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
          selected = blank('area', {
            n: Math.max(0, ...key.areas.map((a) => a.n)) + 1,
            name: f.name.trim(),
            kind: f.kind,
            at,
          });
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
        c.entries.push(blank('codex_entry', { id, type, ...f, map: slugArg, area: selected.n }));
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
            () => blank('loot'),
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
            () => blank('event', { id: uid('event') }),
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
                  a.journal.push(blank('journal', { id: uid('journal'), title: 'New entry' }));
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
