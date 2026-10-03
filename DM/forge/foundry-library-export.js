// Run this Script macro as GM in the connected Foundry world.
// It downloads a read-only snapshot for Campaign Studio's World Library.
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
const summaryOf = (doc) =>
  textOf(
    doc.system?.details?.biography?.value ??
      doc.system?.description?.value ??
      doc.system?.details?.description ??
      '',
  ).slice(0, 20000);

const snapshot = {
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
    scenes: game.scenes.contents.map((scene) => ({
      ...common(scene),
      image: scene.background?.src ?? scene.thumb ?? '',
      summary: textOf(scene.description ?? '').slice(0, 20000),
    })),
    journals: game.journal.contents.map((journal) => ({
      ...common(journal),
      pages: journal.pages.contents.map((page) => ({
        id: page.id,
        name: page.name,
        text: textOf(page.text?.content ?? page.text?.markdown ?? '').slice(0, 100000),
        image: page.src ?? '',
      })),
    })),
    actors: game.actors.contents.map((actor) => ({
      ...common(actor),
      summary: summaryOf(actor),
    })),
    items: game.items.contents.map((item) => ({
      ...common(item),
      summary: summaryOf(item),
    })),
  },
};

const bytes = new Blob([JSON.stringify(snapshot)], { type: 'application/json' });
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
