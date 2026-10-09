'use strict';

const PASSAGE_PAGE = 20;
const KIND_LABEL = { play: 'In-game', banter: 'Table banter', unclear: 'Unclear' };
const SHOW_LABEL = {
  pending: 'Needs a decision',
  known: 'Matches known table lore',
  confirmed: 'Confirmed',
  all: 'All passages',
};
const plural = (count, word) => `${count.toLocaleString()} ${word}${count === 1 ? '' : 's'}`;

/* Sorting a transcript into play and table banter. Claude proposes passages window by window; nothing counts
   as in-game until the GM confirms it here. Transcript text is shown as text, never as HTML. */
async function startSorting(item, restart) {
  const plan = restart ? item.whole : item.plan;
  const lost =
    restart && item.review.confirmed
      ? ` This discards your ${plural(item.review.confirmed, 'decision')}.`
      : '';
  const ask =
    `Claude (your signed-in Claude Code) will read this transcript in ${plural(plan.requests, 'request')}, ` +
    `about ${plural(plan.characters, 'character')} in all, on your subscription. ` +
    `Nothing counts as in-game until you confirm it.${lost} Continue?`;
  if (!confirm(ask)) return;
  await post(`/api/transcripts/${encodeURIComponent(item.id)}/classify`, { restart });
  toast('Sorting started. It runs in the background; you can leave this page.');
  S.jobs = await api('/api/jobs');
  route(true);
}

/* The sorting state of one transcript, with its buttons. env: { review(item) } opens the review list. */
function sortingRow(item, env) {
  const state = item.classification;
  const counts = item.review;
  const live = S.jobs.find(
    (job) => job.classify === item.id && ['queued', 'running'].includes(job.status),
  );
  const sorted = counts.passages > 0;
  const button = (label, onclick, primary = false) =>
    h(
      'button',
      {
        class: primary ? 'primary' : '',
        'aria-label': `${label}: ${item.title}`,
        onclick: () => attempt(onclick),
      },
      label,
    );
  let summary =
    'Not sorted yet. Claude can mark what is play and what is table talk; you confirm each passage.';
  if (live) {
    summary = `Claude is sorting segments ${live.first + 1}–${live.stop} of ${item.segment_count}.`;
  } else if (state.status === 'failed' || state.status === 'running') {
    summary =
      `Sorting stopped at segment ${state.cursor + 1} of ${item.segment_count}. ${state.error}`.trim();
  } else if (sorted) {
    summary =
      `${plural(counts.passages, 'passage')}: ${counts.pending} to decide, ` +
      `${counts.known} matching known table lore, ${counts.confirmed} confirmed.`;
  }
  const actions = [];
  if (!live && state.status !== 'done') {
    actions.push(
      button(
        sorted ? 'Continue sorting' : 'Sort play from banter',
        () => startSorting(item, false),
        !sorted,
      ),
    );
  }
  if (!live && sorted) actions.push(button('Sort again', () => startSorting(item, true)));
  if (sorted) actions.push(button('Review passages', () => env.review(item)));
  return h('div', {}, h('p', { class: 'muted' }, summary), h('div', { class: 'row' }, actions));
}

/* One passage in the review list: what was said, Claude's proposal and the GM's decision. */
function passageCard(passage, notes, decide, read) {
  const range = `${clock(passage.start)}–${clock(passage.end)}`;
  const noteId = `note-${passage.id}`;
  const note = h('input', {
    id: noteId,
    type: 'text',
    maxlength: 200,
    value: passage.remember,
  });
  notes.set(passage.id, note);
  const choose = (kind, confirmed) => () =>
    decide([{ id: passage.id, kind, confirmed, remember: note.value }]);
  const button = (label, onclick) =>
    h('button', { 'aria-label': `${label}, ${range}`, onclick }, label);
  return h(
    'li',
    { class: 'passage' },
    h(
      'div',
      { class: 'spread' },
      h('b', {}, range),
      h(
        'span',
        {},
        badge(KIND_LABEL[passage.kind], passage.kind === 'play' ? 'good' : ''),
        passage.confirmed ? badge('Confirmed', 'good') : badge('Proposed'),
        passage.lore ? badge('Known table lore') : null,
      ),
    ),
    passage.gist ? h('p', {}, passage.gist) : null,
    h('p', { class: 'passage-text muted' }, passage.excerpt),
    passage.kind === 'play' || passage.lore
      ? null
      : h('div', {}, h('label', { for: noteId }, `Table lore note for ${range} (optional)`), note),
    h(
      'div',
      { class: 'row' },
      button('In-game', choose('play', true)),
      button('Table banter', choose('banter', true)),
      passage.confirmed ? button('Undo', choose(passage.kind, false)) : null,
      button('Read in context', read),
    ),
  );
}

/* The review list for one transcript. S.review = { id, show, offset } survives the page refreshing. */
async function drawReview(box, item, env) {
  const view = S.review;
  const url = `/api/transcripts/${encodeURIComponent(item.id)}/passages?`;
  let page = await api(
    url + new URLSearchParams({ show: view.show, offset: view.offset, limit: PASSAGE_PAGE }),
  );
  if (!page.items.length && view.offset > 0) {
    view.offset = Math.max(0, view.offset - PASSAGE_PAGE);
    page = await api(
      url + new URLSearchParams({ show: view.show, offset: view.offset, limit: PASSAGE_PAGE }),
    );
  }
  const notes = new Map();
  const status = h('p', { role: 'status', 'aria-live': 'polite' });
  const decide = (decisions) =>
    attempt(async () => {
      await post(`/api/transcripts/${encodeURIComponent(item.id)}/review`, { decisions });
      await drawReview(box, item, env);
      const next = box.querySelector('.passage button');
      (next || box.querySelector('#review-title')).focus();
    });
  const counts = page.counts;
  const proposed = page.items.filter((passage) => !passage.confirmed && passage.kind !== 'unclear');
  const select = h(
    'select',
    {
      id: 'review-show',
      onchange: () => {
        view.show = select.value;
        view.offset = 0;
        attempt(() => drawReview(box, item, env));
      },
    },
    Object.entries(SHOW_LABEL).map(([value, label]) =>
      h('option', { value, selected: value === view.show }, label),
    ),
  );
  box.hidden = false;
  render(
    box,
    h('h2', { id: 'review-title', tabindex: '-1' }, `Review passages: ${item.title}`),
    h(
      'p',
      { class: 'muted' },
      'Claude proposed these passages. Nothing counts as in-game until you confirm it, and table banter is never used as campaign history. Confirming banter with a note saves it as table lore, so later runs skip it.',
    ),
    h(
      'p',
      {},
      `${counts.pending} need a decision · ${counts.known} match known table lore · ${counts.confirmed} confirmed.`,
    ),
    h('label', { for: 'review-show' }, 'Show'),
    select,
    status,
    page.items.length
      ? h(
          'ol',
          { class: 'plain-list', 'aria-label': 'Passages' },
          page.items.map((passage) =>
            passageCard(passage, notes, decide, () =>
              env.read(item, Math.max(0, passage.first - 2)),
            ),
          ),
        )
      : h('p', { class: 'muted' }, 'Nothing to show here.'),
    h(
      'div',
      { class: 'row' },
      proposed.length && view.show === 'pending'
        ? h(
            'button',
            {
              class: 'primary',
              onclick: () =>
                decide(
                  proposed.map((passage) => ({
                    id: passage.id,
                    confirmed: true,
                    remember: notes.get(passage.id)?.value ?? passage.remember,
                  })),
                ),
            },
            `Confirm the ${plural(proposed.length, 'proposed passage')} on this page`,
          )
        : null,
      h(
        'button',
        {
          disabled: view.offset === 0,
          onclick: () => {
            view.offset = Math.max(0, view.offset - PASSAGE_PAGE);
            attempt(() => drawReview(box, item, env));
          },
        },
        'Previous',
      ),
      h(
        'button',
        {
          disabled: view.offset + PASSAGE_PAGE >= page.total,
          onclick: () => {
            view.offset += PASSAGE_PAGE;
            attempt(() => drawReview(box, item, env));
          },
        },
        'Next',
      ),
      h('button', { onclick: env.close }, 'Done reviewing'),
    ),
  );
}

/* The gags and invented fiction the GM confirmed as table banter. */
function tableLoreBox(lore) {
  return h(
    'section',
    { class: 'card' },
    h('h2', {}, 'Table lore'),
    h(
      'p',
      { class: 'muted' },
      'Jokes and invented gags you confirmed as table banter. Later sorting runs recognise them and do not propose them as play.',
    ),
    lore.items.length
      ? h(
          'ul',
          { class: 'plain-list' },
          lore.items.map((entry) =>
            h(
              'li',
              { class: 'recording-row' },
              h('span', {}, h('b', {}, entry.text)),
              h(
                'button',
                {
                  'aria-label': `Remove table lore: ${entry.text}`,
                  onclick: () => {
                    if (!confirm('Remove this note? Passages that saved it stay as table banter.'))
                      return;
                    attempt(async () => {
                      await post(`/api/table-lore/${encodeURIComponent(entry.id)}/remove`);
                      toast('Table lore removed.');
                      route(true);
                    });
                  },
                },
                'Remove',
              ),
            ),
          ),
        )
      : h(
          'p',
          { class: 'muted' },
          'No table lore yet. Confirm a banter passage with a note to add one.',
        ),
  );
}
