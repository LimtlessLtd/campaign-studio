'use strict';

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

function interruptedChanges() {
  return (S.state.interrupted_changes || []).map((change) =>
    h(
      'div',
      { class: 'connection-banner attention', role: 'alert' },
      icon('clock'),
      h(
        'div',
        {},
        h('b', {}, 'An interrupted change needs review'),
        h(
          'p',
          {},
          `${change.label} stopped part way${change.created ? ' on ' + when(change.created) : ''}. Documents changed elsewhere in the meantime were not overwritten, and the rest of the change was not applied.`,
        ),
        h(
          'p',
          {},
          change.targets
            .map(
              (target) =>
                target.name +
                ': ' +
                { written: 'completed', pending: 'not applied', changed: 'changed elsewhere' }[
                  target.state
                ],
            )
            .join(' · '),
        ),
        h(
          'p',
          {},
          `Check these documents and reapply the proposal if needed. The unapplied values are saved in DM/data/.commits/${change.id}.conflict.json (kept as .dismissed.json after you dismiss this).`,
        ),
      ),
      h(
        'button',
        {
          onclick: () =>
            attempt(async () => {
              const result = await post(`/api/commits/${change.id}/dismiss`);
              S.state.interrupted_changes = result.interrupted_changes;
              route(true);
            }),
        },
        'Dismiss',
      ),
    ),
  );
}
async function studioDashboard(_arg, context) {
  const maps = (await context.doc('maps/index', { items: [] })).items;
  const codex = (await context.doc('codex', { entries: [] })).entries;
  const threads = (await context.doc('threads', { threads: [] })).threads;
  const art = (await context.doc('art', { items: [] })).items;
  const next = S.state.prep.at(-1);
  render(
    context.view,
    pageHead(
      'YOUR CAMPAIGN',
      'A world ready for the table.',
      'Shape the places, people and stories your players will discover.',
      h('a', { class: 'btn primary', href: '#/maps/new' }, icon('plus'), 'Create a map'),
    ),
    interruptedChanges(),
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

function importSummary(report) {
  const parts = [`${report.added} added`, `${report.updated} refreshed`];
  if (report.kept) parts.push(`${report.kept} kept because you edited them in Studio`);
  const omitted = Object.values(report.omitted || {}).reduce((sum, count) => sum + count, 0);
  if (omitted) parts.push(`${omitted} omitted by the document limit`);
  const media = report.media_truncated ? `At least ${report.media}` : report.media;
  return `Imported the world's NPCs, items and scenes into the codex: ${parts.join(', ')}. ${media} media files are available in Media${report.media_truncated ? ' (listing limit reached)' : ''}.`;
}

async function studioLibrary(_arg, context) {
  let kind = S.libraryKind || 'scenes';
  let query = '';
  let offset = 0;
  let requestNumber = 0;
  const tabs = h('div', { class: 'library-tabs' });
  const status = h('div', { class: 'library-status' });
  const list = h('div', { class: 'library-list' });
  const detail = h('div', { class: 'library-detail card' });
  const pager = h('div', { class: 'library-pager' });
  const importCard = h('section', { class: 'card library-import' });
  const live = liveLibraryCard(context, () => refresh());
  let source = 'macro';
  let autoRead = false;
  let readError = '';
  let importNotice = S.worldImportNotice || null;
  S.worldImportNotice = null;
  const readFolder = async () => {
    render(status, h('p', { class: 'muted' }, 'Reading documents from the Foundry world folder…'));
    try {
      const result = await post('/api/foundry/library/read');
      readError = '';
      return result;
    } catch (error) {
      readError = error.message;
    }
  };
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
        importNotice = null;
        toast(importSummary(result));
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
      h(
        'div',
        { class: 'eyebrow' },
        kind === 'assets'
          ? 'FOUNDRY MEDIA'
          : source === 'live'
            ? 'RUNNING FOUNDRY'
            : source === 'folder'
              ? 'FOUNDRY WORLD'
              : 'FOUNDRY SNAPSHOT',
      ),
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
          : source === 'live'
            ? 'Read-only. Use Refresh from Foundry to see later changes.'
            : source === 'folder'
              ? 'Read-only. Changes made in Foundry appear when you open this page again.'
              : 'Read-only. Refresh the snapshot in Foundry to see later changes.',
      ),
    );
  };
  const refresh = async () => {
    const current = ++requestNumber;
    render(list, h('p', { class: 'muted' }, 'Loading World Library…'));
    const result = await context.api(
      '/api/foundry/library?' +
        new URLSearchParams({ kind, q: query, offset: String(offset), limit: '60' }),
    );
    if (current !== requestNumber) return;
    const snapshot = result.snapshot;
    if (
      result.readable &&
      !autoRead &&
      (!snapshot || (snapshot.source === 'folder' && snapshot.stale))
    ) {
      autoRead = true;
      await readFolder();
      return refresh();
    }
    if (!result.world) {
      live.setWorld(null);
      render(
        status,
        h('p', { class: 'muted' }, 'Connect a Foundry world in Settings to browse its library.'),
      );
      render(list);
      render(detail);
      render(pager);
      return;
    }
    live.setWorld(result.world);
    source = snapshot?.source || 'macro';
    const omitted = Object.entries(snapshot?.omitted || {}).map(
      ([category, count]) => `${count} more ${category} not shown`,
    );
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
        snapshot
          ? `${source === 'folder' ? 'Read from the world folder' : source === 'live' ? 'Read from the running Foundry GM' : 'Imported snapshot'}: ${when(snapshot.exported_at)} · Foundry ${snapshot.core_version}` +
              (omitted.length ? ` · ${omitted.join(', ')}` : '')
          : result.readable
            ? 'No documents read yet. Media files can still be browsed.'
            : 'No document snapshot imported yet. Media files can still be browsed.',
      ),
      readError
        ? h('small', { class: 'error-text' }, 'Could not read the world folder: ' + readError)
        : null,
      importNotice
        ? h(
            'small',
            { class: importNotice.error ? 'error-text' : 'small-note' },
            importNotice.message,
          )
        : null,
    );
    render(
      importCard,
      h(
        'div',
        {},
        h('h3', {}, 'Documents from the world folder'),
        h(
          'p',
          { class: 'muted' },
          result.readable
            ? "Campaign Studio reads scenes, journals, actors and items straight from this world's database files, and never changes them. Import adds its actors, items and scenes to the codex and keeps anything you edit in Studio."
            : 'This world has no readable document databases, so use the export macro below instead.',
        ),
      ),
      h(
        'button',
        {
          disabled: !result.readable,
          onclick: () =>
            attempt(async () => {
              await readFolder();
              await refresh();
            }),
        },
        'Read again now',
      ),
      h(
        'button',
        {
          class: 'primary',
          disabled: !result.readable,
          onclick: () =>
            attempt(async () => {
              const report = await post('/api/foundry/world/import');
              importNotice = null;
              toast(importSummary(report));
              await refresh();
            }),
        },
        'Import world into Studio',
      ),
      h(
        'details',
        { class: 'library-fallback', open: !result.readable || !!readError },
        h('summary', {}, 'Use the export macro instead'),
        h(
          'p',
          { class: 'muted' },
          'If the folder cannot be read, run the export Script macro as GM in Foundry, then import the downloaded JSON snapshot here.',
        ),
        h(
          'div',
          { class: 'row' },
          h(
            'a',
            {
              class: 'btn',
              href: '/api/foundry/library/macro',
              download: 'campaign-studio-library-export.js',
            },
            'Download export macro',
          ),
          upload,
          h('button', { onclick: () => upload.click() }, 'Import snapshot'),
        ),
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
              ? result.readable
                ? 'No documents have been read yet. Try "Read again now".'
                : 'Import a Foundry document snapshot to browse this category.'
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
    context.view,
    pageHead(
      'CONNECTED FOUNDRY WORLD',
      'World Library',
      'Browse Foundry documents and media before deciding what to build or change.',
      h('a', { class: 'btn', href: '#/settings' }, 'World settings'),
    ),
    status,
    live.element,
    importCard,
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

async function studioSettings(_arg, context) {
  const result = await context.api('/api/settings');
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
    context.view,
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
        foundryWorldPicker(f, context),
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
      foundryBackupCard(!!result.world, context),
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

async function studioArt(_arg, context) {
  const art = await context.doc('art', { items: [] });
  const cfg = await context.api('/api/settings');
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
                        art.items.unshift(
                          blank('art_item', {
                            ...i,
                            id: uid('art'),
                            image: '',
                            status: 'queued',
                            created: Date.now(),
                          }),
                        );
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
    context.view,
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
