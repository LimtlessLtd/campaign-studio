'use strict';

async function memoryPage(_arg, context) {
  let kind = 'folder';
  let proposal = null;
  let offset = 0;
  const picked = new Set();
  const pickedFolders = new Set();
  const status = h('p', { role: 'status', 'aria-live': 'polite' });
  const sourceBox = h('section', { class: 'card' });
  const reviewBox = h('section');
  const pathInput = h('input', {
    type: 'text',
    placeholder: 'Local path to a campaign folder or session summaries',
    'aria-label': 'Local source path',
  });
  const folders = await context.api('/api/memory/lore-folders');

  const drawReview = () => {
    if (!proposal) {
      render(reviewBox);
      return;
    }
    const items = proposal.items;
    render(
      reviewBox,
      h('h2', {}, 'Review candidates'),
      h(
        'p',
        { class: 'muted' },
        `${items.length} candidates. Nothing is imported until you select and apply them. Existing entries and session logs are kept.`,
      ),
      h(
        'div',
        { class: 'row' },
        h(
          'button',
          {
            onclick: () => {
              items.forEach((item) => picked.add(item.key));
              drawReview();
            },
          },
          'Select all candidates',
        ),
        h(
          'button',
          {
            onclick: () => {
              picked.clear();
              drawReview();
            },
          },
          'Clear selection',
        ),
        h('span', { class: 'muted' }, `${picked.size} selected`),
      ),
      ...items.slice(offset, offset + 100).map((item) =>
        h(
          'article',
          { class: 'card memory-candidate' },
          h(
            'label',
            { class: 'row' },
            h('input', {
              type: 'checkbox',
              checked: picked.has(item.key),
              onchange: (event) => {
                if (event.target.checked) picked.add(item.key);
                else picked.delete(item.key);
                drawReview();
              },
            }),
            h('b', {}, item.title),
            h('small', { class: 'muted' }, item.kind),
          ),
          h('pre', { class: 'memory-preview' }, item.preview || 'No text'),
        ),
      ),
      h(
        'div',
        { class: 'row' },
        h(
          'button',
          {
            disabled: offset === 0,
            onclick: () => {
              offset -= 100;
              drawReview();
            },
          },
          'Previous',
        ),
        h('span', { class: 'muted' }, `${offset + 1}–${Math.min(offset + 100, items.length)}`),
        h(
          'button',
          {
            disabled: offset + 100 >= items.length,
            onclick: () => {
              offset += 100;
              drawReview();
            },
          },
          'Next',
        ),
      ),
      h(
        'button',
        {
          class: 'primary',
          disabled: picked.size === 0,
          onclick: () =>
            attempt(async () => {
              const result = await post('/api/memory/apply', {
                kind,
                path: pathInput.value.trim(),
                folders: [...pickedFolders],
                fingerprint: proposal.fingerprint,
                selected: [...picked],
              });
              status.textContent = `Imported ${result.added} new records, filled ${result.filled} session logs, and kept ${result.skipped} existing records.`;
              picked.clear();
              proposal = null;
              drawReview();
              await recordChoices('codex');
              await recordChoices('threads');
            }),
        },
        `Apply ${picked.size} selected`,
      ),
    );
  };

  const drawSource = () => {
    render(
      sourceBox,
      h('h2', {}, 'Choose a source'),
      h(
        'p',
        { class: 'muted' },
        'Studio reads local source files without changing them. Previewed text remains reference data. Choose only the entries you want to add.',
      ),
      h(
        'label',
        {},
        'Source type',
        h(
          'select',
          {
            'aria-label': 'Source type',
            onchange: (event) => {
              kind = event.target.value;
              proposal = null;
              picked.clear();
              drawSource();
              drawReview();
            },
          },
          ...[
            ['folder', 'Campaign Studio or DM-screen folder'],
            ['summaries', 'Session summaries (JSON or Markdown)'],
            ['lore', 'Foundry lore journal folders'],
          ].map(([value, label]) => h('option', { value, selected: value === kind }, label)),
        ),
      ),
      kind === 'lore'
        ? h(
            'div',
            {},
            folders.folders.length
              ? folders.folders.map((folder) =>
                  h(
                    'label',
                    { class: 'row' },
                    h('input', {
                      type: 'checkbox',
                      checked: pickedFolders.has(folder.id),
                      onchange: (event) => {
                        if (event.target.checked) pickedFolders.add(folder.id);
                        else pickedFolders.delete(folder.id);
                      },
                    }),
                    `${folder.name} · ${folder.count} journals`,
                  ),
                )
              : h(
                  'p',
                  { class: 'muted' },
                  'No journals are in the current snapshot. Read or import a world in the ',
                  h('a', { href: '#/library' }, 'World Library'),
                  '.',
                ),
          )
        : h(
            'div',
            {},
            h('label', { for: 'memory-source-path' }, 'Local folder or file path'),
            pathInput,
            h(
              'small',
              { class: 'muted' },
              kind === 'folder'
                ? 'Choose a folder containing DM/data or data. Codex, threads and played session logs become candidates.'
                : 'Choose a JSON or Markdown file, or a folder containing one file per session.',
            ),
          ),
      h(
        'button',
        {
          class: 'primary',
          onclick: () =>
            attempt(async () => {
              const result = await post('/api/memory/preview', {
                kind,
                path: pathInput.value.trim(),
                folders: [...pickedFolders],
              });
              proposal = result;
              picked.clear();
              offset = 0;
              status.textContent = `Preview ready: ${result.items.length} candidates.`;
              drawReview();
            }),
        },
        'Preview import',
      ),
    );
  };

  pathInput.id = 'memory-source-path';
  render(
    context.view,
    pageHead(
      'CAMPAIGN MEMORY',
      'Import memory',
      'Bring older campaign notes and selected Foundry lore into this campaign.',
    ),
    status,
    sourceBox,
    reviewBox,
  );
  drawSource();
}
