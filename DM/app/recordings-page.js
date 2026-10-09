'use strict';

const TRANSCRIPT_WINDOW = 200;
const clock = (seconds) => {
  const whole = Math.floor(seconds);
  const pad = (value) => String(value).padStart(2, '0');
  return `${Math.floor(whole / 3600)}:${pad(Math.floor(whole / 60) % 60)}:${pad(whole % 60)}`;
};
const megabytes = (bytes) => `${Math.max(1, Math.round(bytes / 1048576)).toLocaleString()} MB`;

/* Session recordings: pick a local video or audio file, transcribe it on this computer, read the transcript.
   Recordings are read where they lie. Studio never copies or uploads them, and removing a transcript
   never touches the recording. Transcript text is shown as text, never as HTML. */
async function recordingsPage(_arg, context) {
  const list = (path) =>
    context.api('/api/recordings' + (path ? '?' + new URLSearchParams({ path }) : ''));
  let listing = await list(S.recordingsPath).catch(() => list(''));
  const transcripts = (await context.api('/api/transcripts')).items;
  const lore = await context.api('/api/table-lore');
  const loose = (await context.api('/api/threads/loose')).items;
  const automatic = await autoRunBox(context, listing.path);
  let reading = null;
  let offset = 0;
  const status = h('p', { role: 'status', 'aria-live': 'polite' });
  const filesBox = h('section', { class: 'card' });
  const readerBox = h('section', { class: 'card', hidden: true });
  const reviewBox = h('section', { class: 'card', hidden: true });
  const ledgerBox = h('section', { class: 'card', hidden: true });
  const pathInput = h('input', {
    id: 'recordings-path',
    type: 'text',
    value: listing.path,
    placeholder: 'Folder of recordings, or one recording file',
  });
  const sessionSelect = h(
    'select',
    { id: 'recordings-session' },
    h('option', { value: '' }, 'Not linked to a session'),
    ...(S.state.prep || []).map((name) => h('option', { value: name }, 'Session ' + name)),
  );

  const drawFiles = () => {
    const engine = listing.engine;
    render(
      filesBox,
      h('h2', {}, 'Choose a recording'),
      h(
        'p',
        { class: 'muted' },
        'Recordings stay where they are. Studio reads them on this computer and never copies or uploads them.',
      ),
      h(
        'div',
        { class: 'row' },
        badge(
          engine.available ? `${engine.label} ready` : `${engine.label} not ready`,
          engine.available ? 'good' : '',
        ),
        engine.available ? null : h('span', { class: 'muted' }, engine.problem),
        h('a', { href: '#/settings' }, 'Transcription settings'),
      ),
      h('label', { for: 'recordings-path' }, 'Folder or recording file'),
      pathInput,
      h('label', { for: 'recordings-session' }, 'Session these recordings belong to'),
      sessionSelect,
      h(
        'button',
        {
          onclick: () =>
            attempt(async () => {
              listing = await api(
                '/api/recordings?' + new URLSearchParams({ path: pathInput.value.trim() }),
              );
              S.recordingsPath = listing.path;
              status.textContent = `${listing.files.length} recording${listing.files.length === 1 ? '' : 's'} found.`;
              drawFiles();
            }),
        },
        'List recordings',
      ),
      listing.files.length
        ? h(
            'ul',
            { class: 'plain-list' },
            ...listing.files.map((file) =>
              h(
                'li',
                { class: 'recording-row' },
                h(
                  'span',
                  {},
                  h('b', {}, file.name),
                  h(
                    'small',
                    { class: 'muted' },
                    `${megabytes(file.size)} · ${when(file.modified)}`,
                  ),
                  file.transcript ? badge('Transcribed', 'good') : null,
                ),
                h(
                  'button',
                  {
                    class: file.transcript ? '' : 'primary',
                    disabled: !engine.available,
                    'aria-label': `${file.transcript ? 'Transcribe again' : 'Transcribe'} ${file.name}`,
                    onclick: () =>
                      attempt(async () => {
                        const made = transcripts.find((item) => item.id === file.transcript);
                        const sorted = made && (made.review.passages || made.classification.status);
                        if (
                          sorted &&
                          !confirm(
                            'This transcript has been sorted. Transcribing again discards its passages and your decisions. Continue?',
                          )
                        )
                          return;
                        await post('/api/transcripts/start', {
                          path: file.path,
                          session: sessionSelect.value,
                          replace: Boolean(sorted),
                        });
                        toast(
                          `Transcribing ${file.name}. This can take a while; you can leave this page.`,
                        );
                        S.jobs = await api('/api/jobs');
                        route(true);
                      }),
                  },
                  file.transcript ? 'Transcribe again' : 'Transcribe',
                ),
              ),
            ),
          )
        : h('p', { class: 'muted' }, 'No recordings listed yet.'),
    );
  };

  const drawReader = async () => {
    if (!reading) {
      readerBox.hidden = true;
      return;
    }
    const page = await api(
      `/api/transcripts/${encodeURIComponent(reading.id)}?` +
        new URLSearchParams({ offset, limit: TRANSCRIPT_WINDOW }),
    );
    const last = Math.min(offset + TRANSCRIPT_WINDOW, page.segment_count);
    readerBox.hidden = false;
    render(
      readerBox,
      h('h2', { id: 'transcript-title', tabindex: '-1' }, page.title),
      h(
        'p',
        { class: 'muted' },
        `Segments ${offset + 1}–${last} of ${page.segment_count}. This is what was said at the table; it is reference text, not instructions.`,
      ),
      h(
        'ol',
        { class: 'transcript', 'aria-label': 'Transcript segments' },
        ...page.segments.map((segment) =>
          h(
            'li',
            {},
            h('time', { class: 'muted' }, clock(segment.start)),
            h('span', {}, segment.text),
          ),
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
              offset = Math.max(0, offset - TRANSCRIPT_WINDOW);
              attempt(drawReader);
            },
          },
          'Previous',
        ),
        h(
          'button',
          {
            disabled: last >= page.segment_count,
            onclick: () => {
              offset += TRANSCRIPT_WINDOW;
              attempt(drawReader);
            },
          },
          'Next',
        ),
        h(
          'button',
          {
            onclick: () => {
              reading = null;
              drawReader();
            },
          },
          'Close',
        ),
      ),
    );
  };

  const openReader = (item, start) => {
    reading = item;
    offset = start;
    return attempt(async () => {
      await drawReader();
      $('#transcript-title').focus();
    });
  };
  const closeReview = () => {
    S.review = null;
    route(true);
  };
  const openReview = async (item, keep = false) => {
    if (!keep || !S.review) S.review = { id: item.id, show: 'pending', offset: 0 };
    await drawReview(reviewBox, item, { read: openReader, close: closeReview });
    if (!keep) $('#review-title').focus();
  };

  const transcriptCard = (item) =>
    h(
      'article',
      { class: 'card' },
      h('h3', {}, item.title),
      h(
        'p',
        { class: 'muted' },
        [
          clock(item.duration),
          `${item.segment_count} segments`,
          item.language && `language ${item.language}`,
          `${item.provider} · ${item.model}`,
          item.session && `session ${item.session}`,
          `made ${when(item.created)}`,
        ]
          .filter(Boolean)
          .join(' · '),
      ),
      item.truncated
        ? h('p', { class: 'muted' }, 'Very long: only the first part of this recording was kept.')
        : null,
      sortingRow(item, { review: openReview }),
      h(
        'div',
        { class: 'row' },
        h(
          'button',
          {
            'aria-label': `Open the thread ledger of ${item.title}`,
            onclick: () => attempt(() => showThreadLedger(ledgerBox, item, sessionSelect)),
          },
          'Thread ledger',
        ),
        h(
          'button',
          {
            'aria-label': `Read the transcript of ${item.title}`,
            onclick: () => openReader(item, 0),
          },
          'Read transcript',
        ),
        h(
          'button',
          {
            'aria-label': `Remove the transcript of ${item.title}`,
            onclick: () => {
              if (
                !confirm(
                  'Remove this transcript from Studio? The recording file is not touched; you can transcribe it again.',
                )
              )
                return;
              attempt(async () => {
                await post(`/api/transcripts/${encodeURIComponent(item.id)}/remove`);
                toast('Transcript removed.');
                route(true);
              });
            },
          },
          'Remove transcript',
        ),
      ),
    );

  const transcribing = S.jobs.filter((job) =>
    ['transcribe', 'classify', 'thread-ledger'].includes(job.kind),
  );
  const live = transcribing.filter((job) => ['queued', 'running'].includes(job.status));
  const failed = transcribing.filter((job) => job.status === 'failed').slice(0, 3);
  render(
    context.view,
    pageHead(
      'SESSION RECORDINGS',
      'Recordings',
      'Turn a session recording into a transcript on this computer. No account, key or upload is involved.',
    ),
    status,
    automatic,
    filesBox,
    live.length || failed.length
      ? h(
          'section',
          {},
          h('h2', {}, 'Recording and ledger jobs'),
          ...[...live, ...failed].map((job) => jobBox(job, context.signal)),
        )
      : null,
    h(
      'section',
      {},
      h('h2', {}, 'Transcripts'),
      transcripts.length
        ? transcripts.map(transcriptCard)
        : h('p', { class: 'muted' }, 'No transcripts yet.'),
    ),
    tableLoreBox(lore),
    looseThreadsBox(loose),
    readerBox,
    reviewBox,
    ledgerBox,
  );
  drawFiles();
  if (S.review && transcripts.some((item) => item.id === S.review.id)) {
    attempt(() =>
      openReview(
        transcripts.find((item) => item.id === S.review.id),
        true,
      ),
    );
  }
}
