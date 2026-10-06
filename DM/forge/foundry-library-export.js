// Run this Script macro as GM in the connected Foundry world.
// It downloads a read-only snapshot, or opens a live GM bridge when Studio adds its origin.
// It does not create, update or delete Foundry documents.
if (!game.user.isGM) return ui.notifications.warn('Run this macro as the GM.');

const textOf = (html) => {
  const parsed = new DOMParser().parseFromString(String(html ?? ''), 'text/html');
  parsed.querySelectorAll('script, style').forEach((node) => node.remove());
  parsed.body
    .querySelectorAll('br')
    .forEach((node) => node.replaceWith(parsed.createTextNode('\n')));
  parsed.body
    .querySelectorAll('p, div, li, h1, h2, h3, h4, h5, h6, blockquote')
    .forEach((node) => node.append(parsed.createTextNode('\n')));
  return (parsed.body.textContent ?? '').replace(/\n{3,}/g, '\n\n').trim();
};
const common = (doc) => ({
  id: doc.id,
  uuid: doc.uuid,
  name: doc.name,
  folder: doc.folder?.name ?? '',
  type: doc.type ?? '',
  image: doc.img ?? '',
});
const readable = (doc) =>
  doc.testUserPermission?.(game.user, CONST.DOCUMENT_OWNERSHIP_LEVELS.OBSERVER) === true;
const summaryOf = (doc) =>
  textOf(
    doc.system?.details?.biography?.value ??
      doc.system?.description?.value ??
      doc.system?.details?.description ??
      '',
  ).slice(0, 20000);

const snapshotOf = () => ({
  format: 'campaign-studio-foundry-library',
  schema: 1,
  world: {
    id: game.world.id,
    title: game.world.title,
    system: game.system.id,
    systemVersion: game.system.version,
    coreVersion: game.version,
  },
  exportedAt: new Date().toISOString(),
  documents: {
    scenes: game.scenes.contents.filter(readable).map((scene) => ({
      ...common(scene),
      image: scene.background?.src ?? scene.thumb ?? '',
      summary: textOf(scene.description ?? '').slice(0, 20000),
    })),
    journals: game.journal.contents.filter(readable).map((journal) => ({
      ...common(journal),
      pages: journal.pages.contents.filter(readable).map((page) => ({
        id: page.id,
        name: page.name,
        text: textOf(page.text?.content ?? page.text?.markdown ?? '').slice(0, 100000),
        image: page.src ?? '',
      })),
    })),
    actors: game.actors.contents.filter(readable).map((actor) => ({
      ...common(actor),
      summary: summaryOf(actor),
    })),
    items: game.items.contents.filter(readable).map((item) => ({
      ...common(item),
      summary: summaryOf(item),
    })),
  },
});

if (typeof CAMPAIGN_STUDIO_LIVE_ORIGIN !== 'undefined') {
  const studioOrigin = new URL(CAMPAIGN_STUDIO_LIVE_ORIGIN).origin;
  if (!/^https?:\/\//.test(studioOrigin))
    return ui.notifications.error('Use an HTTP or HTTPS Campaign Studio URL.');
  window.__campaignStudioLiveBridge?.stop();
  const studioTab = window.open(studioOrigin + '/#/library', '_blank');
  if (!studioTab)
    return ui.notifications.error('Allow the Campaign Studio popup, then run the macro again.');
  const nonce = `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
  const message = (type, extra = {}) =>
    studioTab.postMessage({ studioLive: 1, type, nonce, ...extra }, studioOrigin);
  const announce = () => {
    if (studioTab.closed) stop();
    else message('hello', { world: { id: game.world.id, title: game.world.title } });
  };
  const changed = () => message('changed');
  const hooks = [
    'createScene',
    'updateScene',
    'deleteScene',
    'createJournalEntry',
    'updateJournalEntry',
    'deleteJournalEntry',
    'createJournalEntryPage',
    'updateJournalEntryPage',
    'deleteJournalEntryPage',
    'createActor',
    'updateActor',
    'deleteActor',
    'createItem',
    'updateItem',
    'deleteItem',
  ];
  const receive = (event) => {
    const data = event.data;
    if (
      event.source !== studioTab ||
      event.origin !== studioOrigin ||
      data?.studioLive !== 1 ||
      data.nonce !== nonce ||
      data.type !== 'request' ||
      typeof data.requestId !== 'string' ||
      data.requestId.length > 100
    )
      return;
    if (!game.user.isGM) return ui.notifications.error('Only a GM can refresh the live library.');
    const snapshot = snapshotOf();
    if (new Blob([JSON.stringify(snapshot)]).size > 20 * 1024 * 1024)
      return message('error', {
        requestId: data.requestId,
        error: 'The World Library exceeds 20 MB.',
      });
    message('snapshot', { requestId: data.requestId, snapshot });
  };
  const stop = () => {
    clearInterval(ping);
    window.removeEventListener('message', receive);
    hooks.forEach((name) => Hooks.off(name, changed));
    window.removeEventListener('beforeunload', stop);
    if (window.__campaignStudioLiveBridge?.stop === stop) delete window.__campaignStudioLiveBridge;
  };
  window.addEventListener('message', receive);
  hooks.forEach((name) => Hooks.on(name, changed));
  window.addEventListener('beforeunload', stop);
  const ping = setInterval(announce, 3000);
  window.__campaignStudioLiveBridge = { stop };
  announce();
  ui.notifications.info('Approve the live GM connection in the Campaign Studio tab.');
} else {
  const bytes = new Blob([JSON.stringify(snapshotOf())], { type: 'application/json' });
  if (bytes.size > 20 * 1024 * 1024)
    return ui.notifications.error('The World Library snapshot exceeds 20 MB.');
  const url = URL.createObjectURL(bytes);
  const link = document.createElement('a');
  link.href = url;
  link.download = `campaign-studio-${game.world.id}-library.json`;
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 30000);
  ui.notifications.info(
    'Downloaded a read-only World Library snapshot. Import it in Campaign Studio.',
  );
}
