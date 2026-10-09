'use strict';

const RUN_STATES = {
  running: ['Working', ''],
  waiting: ['Waiting for you', 'review'],
  stopped: ['Stopped', ''],
  ready: ['Ready', ''],
  done: ['Done', 'good'],
  ended: ['Ended', ''],
};
const STEP_STATES = {
  done: ['Done', 'good'],
  running: ['Working', ''],
  queued: ['Queued', ''],
  review: ['Needs you', 'review'],
  failed: ['Stopped', ''],
  todo: ['Not started', ''],
};
const RUN_POLL_MS = 5000;

const runSignature = (run) =>
  JSON.stringify([run.id, run.state, run.note, run.steps, run.usage.requests]);

function runUsageLine(usage) {
  if (!usage.requests) return 'No AI request has been made yet.';
  const tokens = usage.input_tokens + usage.output_tokens;
  const parts = [`${usage.requests} AI request${usage.requests === 1 ? '' : 's'}`];
  if (tokens) parts.push(`${tokens.toLocaleString()} tokens`);
  if (usage.cost_usd) parts.push(`$${usage.cost_usd.toFixed(2)} reported by Claude Code`);
  return parts.join(' · ') + '. A subscription has limits rather than a bill.';
}

function runStepRow(row) {
  const [label, kind] = STEP_STATES[row.state] || [row.state, ''];
  return h(
    'li',
    { class: 'recording-row' },
    h(
      'span',
      {},
      h('b', {}, row.label),
      row.note ? h('small', { class: 'muted' }, row.note) : null,
    ),
    badge(label, kind),
  );
}

/* The steps of a run, in order: one group for each recording, then the steps for the whole session. */
function runSteps(run) {
  const groups = new Map();
  for (const row of run.steps) {
    const key = row.recording || '';
    if (!groups.has(key)) groups.set(key, { subject: row.subject, rows: [] });
    groups.get(key).rows.push(row);
  }
  return [...groups.values()].map((group) =>
    h(
      'section',
      { class: 'run-group' },
      h('h3', {}, group.subject || 'The whole session'),
      h('ul', { class: 'plain-list' }, ...group.rows.map(runStepRow)),
    ),
  );
}

function runView(run, context, folder) {
  const [label, kind] = RUN_STATES[run.state] || [run.state, ''];
  const active = !run.finished;
  return h(
    'div',
    { class: 'auto-run' },
    h(
      'div',
      { class: 'spread' },
      h(
        'strong',
        {},
        `Run for session ${run.session}` +
          (run.next_session ? `, drafting ${run.next_session}` : ''),
      ),
      badge(label, kind),
    ),
    h('p', { class: 'muted' }, `Started ${when(run.created)} · ${run.folder}`),
    run.note ? h('p', { role: 'alert' }, run.note) : null,
    active && run.waiting.length
      ? h(
          'div',
          { class: 'card', role: 'status' },
          h('h3', {}, 'Waiting for you'),
          h('ul', {}, ...run.waiting.map((text) => h('li', {}, text))),
          h(
            'p',
            { class: 'muted' },
            'Review below. When you apply a review, the run carries on by itself.',
          ),
        )
      : null,
    ...runSteps(run),
    h('p', { class: 'muted' }, runUsageLine(run.usage)),
    active
      ? h(
          'div',
          { class: 'row' },
          ['stopped', 'ready'].includes(run.state)
            ? h(
                'button',
                {
                  class: 'primary',
                  onclick: () =>
                    attempt(async () => {
                      await post('/api/auto-run', { path: folder() });
                      toast('The run carries on.');
                      S.jobs = await api('/api/jobs');
                      route(true);
                    }),
                },
                run.state === 'stopped' ? 'Try again' : 'Start the run',
              )
            : null,
          h(
            'button',
            {
              'aria-label': `End the run for session ${run.session}`,
              onclick: () =>
                attempt(async () => {
                  if (
                    !confirm(
                      'End this run? Work already started carries on, nothing it made is removed, and the next run starts fresh.',
                    )
                  )
                    return;
                  await post(`/api/auto-run/${run.id}/end`);
                  toast('Run ended.');
                  route(true);
                }),
            },
            'End this run',
          ),
        )
      : null,
  );
}

/* What a new run would take, with the choices the GM can change, before anything starts. */
function planView(plan, engine, folder, drawPlan) {
  const chosen = new Set(plan.files.filter((file) => file.chosen).map((file) => file.id));
  const session = h(
    'select',
    { id: 'auto-run-session' },
    h('option', { value: '' }, 'Choose a session'),
    ...plan.sessions.map((name) =>
      h('option', { value: name, selected: name === plan.session }, 'Session ' + name),
    ),
  );
  const start = h(
    'button',
    {
      class: 'primary',
      onclick: () =>
        attempt(async () => {
          const run = await post('/api/auto-run', {
            path: folder(),
            session: session.value,
            files: [...chosen],
          });
          toast(`Run started for session ${run.session}. It stops whenever it needs you.`);
          S.jobs = await api('/api/jobs');
          route(true);
        }),
    },
    'Start the automatic run',
  );
  const sync = () => {
    const needsEngine = plan.files.some((file) => chosen.has(file.id) && !file.transcribed);
    start.disabled = !chosen.size || (needsEngine && !engine.available);
  };
  sync();
  return h(
    'div',
    { class: 'auto-run-plan' },
    plan.files.length
      ? h(
          'fieldset',
          { class: 'arc-thread' },
          h('legend', {}, 'Recordings for this run'),
          ...plan.files.map((file) =>
            h(
              'label',
              { class: 'row run-file' },
              h('input', {
                type: 'checkbox',
                checked: file.chosen,
                onchange: (event) => {
                  if (event.target.checked) chosen.add(file.id);
                  else chosen.delete(file.id);
                  sync();
                },
              }),
              h(
                'span',
                {},
                h('b', {}, file.name),
                h('small', { class: 'muted' }, `${megabytes(file.size)} · ${when(file.modified)}`),
                file.transcribed ? badge('Transcribed', 'good') : null,
              ),
            ),
          ),
        )
      : h('p', { class: 'muted' }, 'No recordings in this folder.'),
    h('label', { for: 'auto-run-session' }, 'Session for this run'),
    session,
    h(
      'p',
      { class: 'muted' },
      plan.sorting_requests
        ? `Sorting the transcripts already made needs about ${plan.sorting_requests} AI request${plan.sorting_requests === 1 ? '' : 's'}. `
        : '',
      'Every other step is counted when the run reaches it. Requests go to your signed-in Claude Code one at a time, and the run stops if a limit is reached.',
    ),
    h(
      'div',
      { class: 'row' },
      start,
      h('button', { onclick: () => attempt(drawPlan) }, 'Plan again'),
    ),
  );
}

/* The automatic run: new recordings in a folder to a reviewed draft of the next session. It stops at each
   review and shows exactly what waits for the GM. All text from the server is shown as text. */
async function autoRunBox(context, folderValue) {
  const { run, engine } = await context.api('/api/auto-run');
  const folderInput = h('input', {
    id: 'auto-run-path',
    type: 'text',
    value: folderValue,
    placeholder: 'Folder of new recordings',
  });
  const folder = () => folderInput.value.trim();
  const planBox = h('div');
  const drawPlan = async () => {
    const query = new URLSearchParams({ path: folder() });
    const plan = await api('/api/auto-run/plan?' + query);
    render(planBox, planView(plan, engine, folder, drawPlan));
  };
  const active = run && !run.finished;
  const box = h(
    'section',
    { class: 'card', 'aria-labelledby': 'auto-run-title' },
    h('h2', { id: 'auto-run-title' }, 'Automatic run'),
    h(
      'p',
      { class: 'muted' },
      'One action takes the new recordings of a session through transcription, sorting play from banter, the thread ledger, arc options and a draft of the next session. It stops at every review, and nothing changes your campaign until you apply it.',
    ),
    run ? runView(run, context, folder) : null,
    active
      ? null
      : h(
          'div',
          {},
          engine.available
            ? null
            : h('p', { class: 'muted' }, `${engine.label} is not ready: ${engine.problem}`),
          h('label', { for: 'auto-run-path' }, 'Folder of new recordings'),
          folderInput,
          h(
            'div',
            { class: 'row' },
            h('button', { onclick: () => attempt(drawPlan) }, 'Plan a run'),
          ),
          planBox,
        ),
  );
  if (run && run.state === 'running' && !run.finished) {
    // While work is in progress, redraw the page whenever a step changes, and not otherwise.
    const poll = () => {
      const timer = setTimeout(async () => {
        const next = await context.api('/api/auto-run').catch(() => null);
        if (context.signal.aborted) return;
        if (next?.run && runSignature(next.run) !== runSignature(run)) route(true);
        else poll();
      }, RUN_POLL_MS);
      context.signal.addEventListener('abort', () => clearTimeout(timer), { once: true });
    };
    poll();
  }
  return box;
}
