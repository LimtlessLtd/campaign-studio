'use strict';

/* ---------- router ---------- */
function go(hash, replace) {
  if (replace) {
    history.replaceState(null, '', hash);
    route();
  } else location.hash = hash;
}
async function route(soft) {
  S.routeController?.abort();
  const controller = new AbortController();
  S.routeController = controller;
  const [page, arg] = location.hash.replace(/^#\/?/, '').split('/').map(decodeURIComponent);
  document
    .querySelectorAll('#side [data-nav]')
    .forEach((a) =>
      a.classList.toggle(
        'on',
        a.getAttribute('href') === '#/' + (page || '') || a.dataset.nav === page,
      ),
    );
  const token = (S.route = (S.route || 0) + 1);
  const view = h('div');
  const context = {
    view,
    signal: controller.signal,
    api: (path) => api(path, { signal: controller.signal }),
    doc: (name, fallback) => doc(name, fallback, controller.signal),
  };
  const y = window.scrollY;
  try {
    await (
      {
        '': studioDashboard,
        welcome: studioWelcome,
        library: studioLibrary,
        memory: memoryPage,
        prep: prepPage,
        recordings: recordingsPage,
        threads: threadsPage,
        codex: codexPage,
        maps: studioMaps,
        world: worldPage,
        art: studioArt,
        settings: studioSettings,
        handouts: handoutsPage,
        inbox: inboxPage,
        notes: notesPage,
      }[page || ''] || studioDashboard
    )(arg, context);
  } catch (e) {
    if (controller.signal.aborted) return;
    render(
      view,
      h('h1', {}, 'Something went wrong'),
      h('pre', { class: 'file' }, e.stack || String(e)),
    );
  }
  if (token !== S.route) return; // the DM has already moved on
  // keep open <details> open across a refresh
  const open =
    soft === true
      ? [...root.querySelectorAll('details[open] > summary b')].map((s) => s.textContent)
      : [];
  render(root, view);
  if (soft === true) {
    view.querySelectorAll('details').forEach((d) => {
      if (open.includes(d.querySelector('summary b')?.textContent)) d.open = true;
    });
    window.scrollTo(0, y);
  } else window.scrollTo(0, 0);
}
window.addEventListener('hashchange', () => route());
window.addEventListener('DOMContentLoaded', async () => {
  try {
    S.state = await api('/api/state');
    S.shapes = await api('/api/shapes');
    S.jobs = await api('/api/jobs').catch(() => []);
    const c = await recordChoices('codex');
    await recordChoices('threads');
    PCS = [
      ...new Set([
        ...S.state.public.heroes.map((h) => h.id),
        ...c.filter((e) => e.type === 'pc').map((e) => e.id),
      ]),
    ];
    initStudio();
    if (S.state.onboarding_needed) go('#/welcome', true);
    else route();
  } catch (e) {
    render(root, h('h1', {}, 'Could not connect'), h('p', {}, e.message));
  }
});
