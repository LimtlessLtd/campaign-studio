'use strict';

const ARC_KINDS = { resolution: 'Resolution', escalation: 'Escalation', twist: 'Twist' };
const ARC_STATUS = {
  running: 'Drafting',
  review: 'Ready to review',
  applied: 'Applied',
  failed: 'Failed',
};
const ARC_FIELDS = [
  ['title', 'Title', 'input'],
  ['summary', 'What happens', 'textarea'],
  ['hook', 'How the players meet it', 'textarea'],
  ['pitch', 'Pitch line for the next session', 'textarea'],
];
const arcThreadTitle = (id) => S.recordIndex.threads.find((x) => x.id === id)?.title || id;
const arcEntryName = (id) => S.recordIndex.codex.find((x) => x.id === id)?.name || id;

function arcUses(option) {
  const parts = [];
  if (option.entries.length) parts.push('Uses ' + option.entries.map(arcEntryName).join(', '));
  if (option.pcs.length) parts.push('Party: ' + option.pcs.map(arcEntryName).join(', '));
  return parts.length ? h('p', { class: 'muted' }, parts.join(' · ')) : null;
}

/* Story arcs: choose a few loose threads, let the signed-in AI propose a resolution, an escalation and a
   twist for each, then pick at most one option per thread. Nothing changes a thread until the GM applies;
   proposal text is shown as text, never as HTML. */
async function arcsPage(id, context) {
  if (id) return arcReviewPage(id, context);
  const [loose, listing, seeds] = await Promise.all([
    context.api('/api/threads/loose'),
    context.api('/api/arcs'),
    context.api('/api/arcs/seeds'),
  ]);
  const max = listing.max_threads;
  const picked = new Set();
  const start = h('button', { class: 'primary', disabled: true }, 'Propose arcs');
  const count = h('p', { class: 'muted', role: 'status', 'aria-live': 'polite' });
  const sync = () => {
    start.disabled = picked.size === 0 || picked.size > max;
    count.textContent =
      picked.size > max
        ? `Choose at most ${max} threads.`
        : `${picked.size} of ${max} threads chosen.`;
  };
  start.addEventListener('click', () =>
    attempt(async () => {
      await post('/api/arcs/start', { threads: [...picked] });
      toast('Drafting arc options. This can take a minute; you can leave this page.');
      S.jobs = await api('/api/jobs');
      route(true);
    }),
  );
  sync();

  const proposal = (item) =>
    h(
      'article',
      { class: 'card arc-card' },
      h(
        'div',
        { class: 'spread' },
        h('strong', {}, item.threads.map((thread) => thread.title).join(', ')),
        badge(ARC_STATUS[item.status] || item.status, item.status === 'applied' ? 'good' : ''),
      ),
      h(
        'p',
        { class: 'muted' },
        `Proposed ${when(item.created)}` +
          (item.status === 'applied' ? ` · ${item.choices} chosen` : ` · ${item.options} options`),
      ),
      item.error ? h('p', { role: 'alert' }, item.error) : null,
      item.status === 'running' ? jobBox({ id: item.job }, context.signal) : null,
      h(
        'div',
        { class: 'row' },
        item.status === 'running'
          ? null
          : h(
              'a',
              { class: 'btn', href: '#/arcs/' + item.id },
              item.status === 'review' ? 'Review options' : 'Open',
            ),
        item.status === 'running'
          ? null
          : h(
              'button',
              {
                class: 'danger',
                'aria-label': `Remove the proposal for ${item.threads.map((t) => t.title).join(', ')}`,
                onclick: () =>
                  attempt(async () => {
                    if (
                      !confirm(
                        'Remove this proposal? Threads it already changed keep those changes.',
                      )
                    )
                      return;
                    await post(`/api/arcs/${item.id}/remove`);
                    toast('Proposal removed.');
                    route(true);
                  }),
              },
              'Remove',
            ),
      ),
    );

  render(
    context.view,
    pageHead(
      'STORY ARCS',
      'Story arcs',
      'Pick the threads still open and let your signed-in AI (Claude Code or Codex, as chosen in Settings) propose where each could go next. You choose what happens; nothing changes until you apply it.',
    ),
    h(
      'section',
      { class: 'card' },
      h('h2', {}, 'Loose threads'),
      h(
        'p',
        { class: 'muted' },
        'Open, planned and foreshadowed threads, stalest first. One request reads the chosen threads, the codex entries they link, the party and your latest session logs.',
      ),
      loose.items.length
        ? h(
            'div',
            { class: 'arc-threads' },
            ...loose.items.map((item) =>
              h(
                'label',
                { class: 'row' },
                h('input', {
                  type: 'checkbox',
                  onchange: (event) => {
                    if (event.target.checked) picked.add(item.id);
                    else picked.delete(item.id);
                    sync();
                  },
                }),
                h(
                  'span',
                  {},
                  h('strong', {}, item.title),
                  h(
                    'span',
                    { class: 'muted' },
                    ` · ${item.status} · ${item.last_session || 'never touched in a session'}`,
                  ),
                ),
              ),
            ),
          )
        : h('p', {}, 'No loose threads. Add threads on the Story threads page.'),
      count,
      h('div', { class: 'row' }, start),
    ),
    h(
      'section',
      {},
      h('h2', {}, 'Proposals'),
      listing.items.length
        ? listing.items.map(proposal)
        : h('p', { class: 'muted' }, 'No proposals yet.'),
    ),
    seeds.items.length
      ? h(
          'section',
          { class: 'card' },
          h('h2', {}, 'Seeds for the next session'),
          h(
            'p',
            { class: 'muted' },
            'Pitch lines from the options you chose, for threads that are still unresolved. Session planning offers them when you write a pitch.',
          ),
          h(
            'ul',
            {},
            ...seeds.items.map((seed) =>
              h('li', {}, `${seed.title} (${seed.kind}): ${seed.pitch}`),
            ),
          ),
        )
      : null,
  );
  if (listing.items.some((item) => item.status === 'running')) {
    const timer = setTimeout(() => {
      if (!context.signal.aborted) route(true);
    }, 4000);
    context.signal.addEventListener('abort', () => clearTimeout(timer));
  }
}

async function arcReviewPage(id, context) {
  const back = h('a', { href: '#/arcs' }, 'All arc proposals');
  let arc;
  try {
    arc = await context.api('/api/arcs/' + encodeURIComponent(id));
  } catch (error) {
    if (context.signal.aborted) throw error;
    return render(
      context.view,
      pageHead('STORY ARCS', 'Arc options', 'This proposal could not be opened.'),
      h('p', { role: 'alert' }, error.message),
      back,
    );
  }
  const head = pageHead(
    'STORY ARCS',
    'Arc options',
    arc.status === 'applied'
      ? 'The options you chose, and the threads they now plan.'
      : 'Choose at most one option for each thread. Reword anything before you apply it.',
  );
  const byThread = (thread) => arc.options.filter((option) => option.thread === thread);

  if (arc.status === 'running') {
    render(
      context.view,
      head,
      h('p', { role: 'status' }, 'Drafting options. This page updates when they are ready.'),
      jobBox({ id: arc.job }, context.signal),
      back,
    );
    const timer = setTimeout(() => {
      if (!context.signal.aborted) route(true);
    }, 4000);
    context.signal.addEventListener('abort', () => clearTimeout(timer));
    return;
  }
  if (arc.status === 'failed') {
    return render(
      context.view,
      head,
      h('p', { role: 'alert' }, 'Drafting stopped: ' + arc.error),
      h(
        'div',
        { class: 'row' },
        h(
          'button',
          {
            class: 'primary',
            onclick: () =>
              attempt(async () => {
                await post('/api/arcs/start', { threads: arc.threads });
                await post(`/api/arcs/${arc.id}/remove`);
                S.jobs = await api('/api/jobs');
                go('#/arcs');
              }),
          },
          'Draft again',
        ),
        back,
      ),
    );
  }
  if (arc.status === 'applied') {
    const chosen = new Set(arc.choices);
    return render(
      context.view,
      head,
      h(
        'p',
        { role: 'status' },
        `Applied ${arc.choices.length} of ${arc.threads.length} threads; each is now planned with its arc.`,
      ),
      ...arc.threads.map((thread) => {
        const option = byThread(thread).find((x) => chosen.has(x.id));
        return h(
          'article',
          { class: 'card' },
          h('h2', {}, arcThreadTitle(thread)),
          option
            ? [
                h('p', {}, h('strong', {}, `${ARC_KINDS[option.kind]}: ${option.title}`)),
                h('p', {}, option.summary),
                option.hook ? h('p', { class: 'muted' }, 'Hook: ' + option.hook) : null,
                h('p', { class: 'muted' }, 'Pitch: ' + option.pitch),
                arcUses(option),
              ]
            : h('p', { class: 'muted' }, 'Left as it was.'),
        );
      }),
      back,
    );
  }

  const chosen = {};
  const draft = Object.fromEntries(
    arc.options.map((option) => [
      option.id,
      Object.fromEntries(ARC_FIELDS.map(([field]) => [field, option[field]])),
    ]),
  );
  const optionCard = (thread, option) => {
    const radio = h('input', {
      type: 'radio',
      name: 'arc-' + thread,
      'aria-label': `Choose ${ARC_KINDS[option.kind].toLowerCase()}: ${option.title}`,
      onchange: () => (chosen[thread] = option.id),
    });
    return h(
      'div',
      { class: 'arc-option' },
      h('label', { class: 'row' }, radio, h('strong', {}, ARC_KINDS[option.kind])),
      ...ARC_FIELDS.map(([field, label, tag]) => {
        const fieldId = `arc-${option.id}-${field}`;
        return h(
          'div',
          {},
          h('label', { for: fieldId }, label),
          h(tag, {
            id: fieldId,
            value: draft[option.id][field],
            rows: tag === 'textarea' ? 3 : null,
            oninput: (event) => (draft[option.id][field] = event.target.value),
          }),
        );
      }),
      arcUses(option),
    );
  };
  render(
    context.view,
    head,
    ...arc.threads.map((thread) =>
      h(
        'fieldset',
        { class: 'card arc-thread' },
        h('legend', {}, arcThreadTitle(thread)),
        h(
          'label',
          { class: 'row' },
          h('input', {
            type: 'radio',
            name: 'arc-' + thread,
            checked: true,
            'aria-label': `Leave ${arcThreadTitle(thread)} as it is`,
            onchange: () => delete chosen[thread],
          }),
          'Leave this thread as it is',
        ),
        ...byThread(thread).map((option) => optionCard(thread, option)),
      ),
    ),
    h(
      'div',
      { class: 'row' },
      h(
        'button',
        {
          class: 'primary',
          onclick: () =>
            attempt(async () => {
              const options = Object.values(chosen);
              if (!options.length) throw new Error('Choose an option for at least one thread.');
              await post(`/api/arcs/${arc.id}/apply`, {
                choices: options.map((option) => ({ option, ...draft[option] })),
              });
              toast('Chosen options applied. Those threads are now planned.');
              route(true);
            }),
        },
        'Apply chosen options',
      ),
      back,
    ),
  );
}

/* Seeds from chosen arcs, offered beside a session pitch. Each adds its pitch line to the text box. */
async function arcSeedsBox(pitchBox) {
  const { items } = await api('/api/arcs/seeds');
  if (!items.length) return null;
  return h(
    'div',
    {},
    h('label', {}, 'Seeds from your story arcs'),
    ...items.map((seed) =>
      h(
        'div',
        { class: 'row' },
        h('span', {}, `${seed.title} (${seed.kind}): ${seed.pitch}`),
        h(
          'button',
          {
            class: 'small',
            'aria-label': 'Add to the pitch: ' + seed.pitch,
            onclick: () => {
              pitchBox.value = [pitchBox.value.trim(), seed.pitch].filter(Boolean).join('\n');
              pitchBox.focus();
            },
          },
          'Add',
        ),
      ),
    ),
  );
}
