'use strict';

const ledgerClock = (seconds) => {
  const whole = Math.floor(seconds);
  const pad = (value) => String(value).padStart(2, '0');
  return `${Math.floor(whole / 3600)}:${pad(Math.floor(whole / 60) % 60)}:${pad(whole % 60)}`;
};

/* A transcript's proposed campaign changes. Every checked row is a GM decision; the server
   validates its quote against confirmed play and applies selected rows in one recoverable change. */
async function showThreadLedger(box, transcript, sessionSelect) {
  const url = `/api/transcripts/${encodeURIComponent(transcript.id)}/ledger`;
  const { ledger } = await api(url);
  box.hidden = false;
  const close = () => {
    box.hidden = true;
    render(box);
  };
  const heading = h(
    'h2',
    { id: 'ledger-title', tabindex: '-1' },
    `Thread ledger: ${transcript.title}`,
  );
  const actions = [];
  let body;
  if (!ledger) {
    if (!transcript.session) {
      body = h(
        'p',
        {},
        'Link this transcript to a session prep first. Choose a session above, then link it here.',
      );
      actions.push(
        h(
          'button',
          {
            disabled: !sessionSelect.value,
            onclick: () =>
              attempt(async () => {
                await post(url + '/session', { session: sessionSelect.value });
                toast('Transcript linked to the session.');
                route(true);
              }),
          },
          'Link selected session',
        ),
      );
    } else if (transcript.classification?.status !== 'done' || transcript.review?.pending) {
      body = h('p', {}, 'Finish sorting and reviewing the transcript before drafting its ledger.');
    } else {
      body = h(
        'p',
        {},
        'Claude will propose thread, codex and session-log changes from only the play you confirmed. Nothing changes until you review and apply the proposal.',
      );
      actions.push(
        h(
          'button',
          {
            onclick: () =>
              attempt(async () => {
                await post(url + '/start', {});
                toast('Thread ledger draft started.');
                S.jobs = await api('/api/jobs');
                route(true);
              }),
          },
          'Draft thread ledger',
        ),
      );
    }
  } else {
    body = h(
      'p',
      { role: 'status' },
      ledger.status === 'running'
        ? `Drafting confirmed play. ${ledger.cursor} lines examined; refresh to see progress.`
        : ledger.status === 'failed'
          ? `Draft stopped: ${ledger.error}`
          : ledger.status === 'applied'
            ? `${ledger.selected.length} reviewed changes applied to the campaign.`
            : ledger.status === 'review'
              ? `${ledger.events.length} proposed changes. Check exactly what should become canon.`
              : 'The draft is ready to continue.',
    );
    if (ledger.status === 'running') {
      actions.push(
        h(
          'button',
          { onclick: () => attempt(() => showThreadLedger(box, transcript, sessionSelect)) },
          'Refresh ledger',
        ),
      );
    }
    if (ledger.status === 'failed' || ledger.status === 'ready') {
      actions.push(
        h(
          'button',
          {
            onclick: () =>
              attempt(async () => {
                await post(url + '/start', {});
                toast('Thread ledger draft resumed.');
                route(true);
              }),
          },
          'Resume draft',
        ),
      );
    }
    if (ledger.status === 'review' || ledger.status === 'failed') {
      actions.push(
        h(
          'button',
          {
            onclick: () =>
              attempt(async () => {
                if (!confirm('Discard this draft and propose a fresh ledger?')) return;
                await post(url + '/start', { restart: true });
                toast('Fresh thread ledger draft started.');
                S.jobs = await api('/api/jobs');
                route(true);
              }),
          },
          'Redraft ledger',
        ),
      );
    }
    if (ledger.status === 'review' && !ledger.events.length) {
      body = h(
        'p',
        { role: 'status' },
        'No campaign changes were proposed from the confirmed play.',
      );
    }
    if (ledger.status === 'review' && ledger.events.length) {
      const selected = new Set(ledger.events.map((item) => item.id));
      const kinds = { thread: 'Story thread', codex: 'Codex note', outcome: 'Session outcome' };
      const cards = ledger.events.map((item) => {
        const label = `${kinds[item.kind]} · ${item.title || item.target || transcript.session}`;
        const checkbox = h('input', {
          type: 'checkbox',
          checked: true,
          onchange: (event) => {
            if (event.target.checked) selected.add(item.id);
            else selected.delete(item.id);
          },
        });
        return h(
          'article',
          { class: 'card ledger-event' },
          h('label', { class: 'row' }, checkbox, h('strong', {}, label)),
          item.status ? badge(item.status) : null,
          h('p', {}, item.text),
          h('p', { class: 'muted' }, `Evidence at ${ledgerClock(item.at)} · ${item.quote}`),
          item.pcs.length ? h('p', { class: 'muted' }, `Heroes: ${item.pcs.join(', ')}`) : null,
        );
      });
      body = h('div', {}, body, ...cards);
      actions.push(
        h(
          'button',
          {
            onclick: () =>
              attempt(async () => {
                if (!selected.size) throw new Error('Choose at least one change to apply.');
                await post(url + '/apply', { selected: [...selected] });
                toast('Reviewed thread ledger applied.');
                route(true);
              }),
          },
          'Apply selected changes',
        ),
      );
    }
  }
  render(
    box,
    heading,
    body,
    h('div', { class: 'row' }, ...actions, h('button', { onclick: close }, 'Close')),
  );
  heading.focus();
  box.scrollIntoView({ block: 'start' });
}

function looseThreadsBox(items) {
  return h(
    'section',
    { class: 'card', 'aria-label': 'Loose threads' },
    h('h2', {}, 'Loose threads'),
    h(
      'p',
      { class: 'muted' },
      'Open and foreshadowed threads, stalest first. Resolved threads are left out.',
    ),
    items.length
      ? h(
          'ol',
          {},
          ...items.map((item) => {
            const evidence = item.evidence?.at(-1);
            return h(
              'li',
              {},
              h('strong', {}, item.title),
              h(
                'span',
                { class: 'muted' },
                ` · ${item.status} · ${item.last_session || 'never touched in a session'}${item.pcs.length ? ' · ' + item.pcs.join(', ') : ''}`,
              ),
              evidence
                ? h(
                    'p',
                    { class: 'muted' },
                    `${evidence.session} at ${ledgerClock(evidence.at)} · ${evidence.quote}`,
                  )
                : null,
            );
          }),
        )
      : h('p', {}, 'No loose threads.'),
  );
}
