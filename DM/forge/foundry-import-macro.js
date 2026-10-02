// Map Forge importer for Foundry VTT (v11, v12, v13).
// Paste into a new macro (type: Script) and run it as the GM. It lists the maps that Map Forge copied into
// Data/wotg-maps/ and imports the chosen one in one go:
//   - the scene: background, grid, walls, doors, windows, lights
//   - roofs: one overhead tile per building that fades when a token walks under it
//   - the DM key: a GM-only journal (one page per numbered area: description, loot, events, who is there)
//     with pins on the map
// Run it again for a map that is already a scene to replace the forged parts; tokens and your own notes stay.
const TAG = 'wotgForge';
const esc = (s) => foundry.utils.escapeHTML(String(s ?? ''));
const gen = game.release.generation;
if (!game.user.isGM) return ui.notifications.warn('Run Campaign Studio imports as the GM.');

const index = await fetch('wotg-maps/index.json', { cache: 'no-store' })
  .then((r) => r.json())
  .catch(() => []);
if (!index.length) return ui.notifications.warn('No forged maps in Data/wotg-maps yet.');

const options = index.map((m) => `<option value="${m.slug}">${esc(m.name)}</option>`).join('');
const choice = await Dialog.prompt({
  title: 'Map Forge: import a map',
  content: `<p>Creates the scene with walls, doors, windows, lights, roofs and the DM's journal with map pins.</p>
            <p><select name="slug" style="width:100%">${options}</select></p>
            <p><label><input type="checkbox" name="roofs" checked> Roofs</label>
               <label><input type="checkbox" name="journal" checked> Journal and pins</label></p>
            ${game.system.id === 'dnd5e' ? '<p><label><input type="checkbox" name="entities" checked> Create / update linked NPCs and items</label></p><p>NPC stat blocks and item mechanics are added as notes. Review their sheets before play.</p>' : '<p>Linked characters and items are included in the GM journal. Actor and item sheets currently support D&D 5e.</p>'}`,
  label: 'Import',
  callback: (html) => ({
    slug: html.find('[name=slug]').val(),
    roofs: html.find('[name=roofs]').is(':checked'),
    journal: html.find('[name=journal]').is(':checked'),
    entities: html.find('[name=entities]').is(':checked'),
  }),
  rejectClose: false,
});
if (!choice?.slug) return;

const data = await fetch(`wotg-maps/${choice.slug}.json`, { cache: 'no-store' }).then((r) =>
  r.json(),
);
const forge = data.flags?.world?.[TAG] ?? {};
if (forge.targetWorld && forge.targetWorld !== game.world.id)
  return ui.notifications.warn(
    'This export belongs to another world. Choose this world in Campaign Studio Settings and export again.',
  );
const roofs = forge.roofs ?? [];
const key = forge.key;
// keep the bulky lists out of the scene's own flags
data.flags = { world: { [TAG]: { plan: forge.plan, forged: forge.forged, managed: true } } };
data.walls = (data.walls ?? []).map((w) => ({
  ...w,
  flags: { world: { [TAG]: { generated: true } } },
}));
data.lights = (data.lights ?? []).map((l) => ({
  ...l,
  flags: { world: { [TAG]: { generated: true } } },
}));

let scene = game.scenes.find((s) => s.flags?.world?.[TAG]?.plan === choice.slug);
if (scene) {
  const ok = await Dialog.confirm({
    title: 'Replace the forged parts?',
    content: `<p><b>${esc(scene.name)}</b> came from this map already. Update its generated image, walls, lights, roofs and journal?</p><p>Tokens and custom notes stay.${scene.flags?.world?.[TAG]?.managed ? ' Custom walls and lights stay.' : ' This older import has no wall ownership tags: its walls and lights will be replaced on this update.'}</p>`,
  });
  if (!ok) return;
  const mine = (d) => d.flags?.world?.[TAG];
  const managed = scene.flags?.world?.[TAG]?.managed;
  await scene.deleteEmbeddedDocuments(
    'Wall',
    scene.walls.filter((w) => !managed || mine(w)).map((w) => w.id),
  );
  await scene.deleteEmbeddedDocuments(
    'AmbientLight',
    scene.lights.filter((l) => !managed || mine(l)).map((l) => l.id),
  );
  await scene.deleteEmbeddedDocuments(
    'Tile',
    scene.tiles.filter(mine).map((t) => t.id),
  );
  await scene.deleteEmbeddedDocuments(
    'Note',
    scene.notes.filter(mine).map((n) => n.id),
  );
  await scene.update({
    background: data.background,
    width: data.width,
    height: data.height,
    grid: data.grid,
    flags: data.flags,
    ...(data.environment ? { environment: data.environment } : {}),
  });
  await scene.createEmbeddedDocuments('Wall', data.walls);
  await scene.createEmbeddedDocuments('AmbientLight', data.lights);
} else {
  scene = await Scene.create(data);
  const thumb = await scene.createThumbnail().catch(() => null);
  if (thumb) await scene.update({ thumb: thumb.thumb });
}

// Roofs: overhead tiles that fade when a token is underneath.
if (choice.roofs && roofs.length) {
  const FADE = (CONST.OCCLUSION_MODES ?? CONST.TILE_OCCLUSION_MODES).FADE;
  const above = scene.foregroundElevation ?? (scene.grid.distance ?? 5) * 4;
  const tiles = roofs.map((r) => {
    const base = {
      texture: { src: r.src },
      x: r.x,
      y: r.y,
      width: r.width,
      height: r.height,
      occlusion: { mode: FADE, alpha: 0 },
      flags: { world: { [TAG]: { roof: true } } },
    };
    return gen >= 12
      ? { ...base, elevation: above, restrictions: { light: false, weather: true } }
      : { ...base, overhead: true, roof: true };
  });
  await scene.createEmbeddedDocuments('Tile', tiles);
}

// The DM key: a journal only the GM can see, and a pin for every area worth a pin.
let pins = 0;
if (choice.journal && key?.areas?.length) {
  const list = (title, rows) => (rows.length ? `<h3>${title}</h3><ul>${rows.join('')}</ul>` : '');
  const block = (title, value) =>
    value ? `<h3>${title}</h3><p>${esc(value).replace(/\n/g, '<br>')}</p>` : '';
  const details = (entries) =>
    entries.map(
      (e) =>
        `<li><strong>${esc(e.name)}</strong>` +
        (e.public ? `<br>${esc(e.public)}` : '') +
        (e.secrets ? `<br><em>Secret:</em> ${esc(e.secrets)}` : '') +
        (e.notes ? `<br>${esc(e.notes)}` : '') +
        (e.image ? `<br><img src="${esc(e.image)}" style="max-width:320px">` : '') +
        '</li>',
    );
  const page = (a) => ({
    name: `${a.n}. ${a.name}`,
    type: 'text',
    flags: { world: { [TAG]: { page: 'area-' + a.n } } },
    text: {
      format: 1,
      content:
        `<p><em>${esc(a.kind)}${a.rooms?.length ? ' · ' + esc(a.rooms.join(', ')) : ''}</em></p>` +
        (a.text ? `<p>${esc(a.text).replace(/\n/g, '<br>')}</p>` : '') +
        (a.creatures ? `<p><strong>Here:</strong> ${esc(a.creatures)}</p>` : '') +
        list('NPCs and monsters', details(a.npcs_detail ?? [])) +
        list('Items', details(a.items_detail ?? [])) +
        list(
          'Loot',
          (a.loot ?? [])
            .filter((l) => l.item)
            .map(
              (l) =>
                `<li>${esc(l.item)}${l.where ? ` <em>(${esc(l.where)})</em>` : ''}${l.value ? ` — ${esc(l.value)}` : ''}</li>`,
            ),
        ) +
        list(
          'Events',
          (a.events ?? [])
            .filter((e) => e.trigger || e.effect)
            .map((e) => `<li><strong>${esc(e.trigger)}</strong>: ${esc(e.effect)}</li>`),
        ) +
        (a.images ?? [])
          .map((src) => `<p><img src="${esc(src)}" style="max-width:640px"></p>`)
          .join(''),
    },
  });
  const journalPages = key.areas.flatMap((a) =>
    (a.journal ?? [])
      .filter((j) => j.title || j.text)
      .map((j) => ({
        name: `${a.n}. ${a.name} — ${j.title || 'Journal entry'}`,
        type: 'text',
        flags: { world: { [TAG]: { page: 'journal-' + a.n + '-' + (j.id || j.title) } } },
        text: { format: 1, content: block('Text', j.text) + block('DM secrets', j.secrets) },
      })),
  );
  const overview = {
    name: 'Overview',
    type: 'text',
    flags: { world: { [TAG]: { page: 'overview' } } },
    text: {
      format: 1,
      content:
        (key.notes ? `<p>${esc(key.notes).replace(/\n/g, '<br>')}</p>` : '') +
        (key.images ?? [])
          .map((src) => `<p><img src="${esc(src)}" style="max-width:640px"></p>`)
          .join('') +
        list(
          'Events',
          (key.events ?? []).map(
            (e) => `<li><strong>${esc(e.trigger)}</strong>: ${esc(e.effect)}</li>`,
          ),
        ) +
        `<h3>Areas</h3><ol>${key.areas.map((a) => `<li>${esc(a.name)} <em>(${esc(a.kind)})</em></li>`).join('')}</ol>`,
    },
  };
  let journal = game.journal.find((j) => j.flags?.world?.[TAG]?.plan === choice.slug);
  const pages = [overview, ...key.areas.map(page), ...journalPages];
  if (journal) {
    await journal.update({ name: `${data.name} (DM key)` });
    for (const p of pages) {
      const old = journal.pages.find(
        (old) =>
          old.flags?.world?.[TAG]?.page === p.flags.world[TAG].page ||
          (!old.flags?.world?.[TAG] && old.name === p.name),
      );
      if (old) await journal.updateEmbeddedDocuments('JournalEntryPage', [{ ...p, _id: old.id }]);
      else await journal.createEmbeddedDocuments('JournalEntryPage', [p]);
    }
    const ids = new Set(pages.map((p) => p.flags.world[TAG].page));
    await journal.deleteEmbeddedDocuments(
      'JournalEntryPage',
      journal.pages
        .filter((p) => p.flags?.world?.[TAG]?.page && !ids.has(p.flags.world[TAG].page))
        .map((p) => p.id),
    );
  } else
    journal = await JournalEntry.create({
      name: `${data.name} (DM key)`,
      pages,
      ownership: { default: CONST.DOCUMENT_OWNERSHIP_LEVELS.NONE },
      flags: { world: { [TAG]: { plan: choice.slug } } },
    });
  const icons = {
    tavern: 'tankard',
    temple: 'temple',
    smithy: 'anvil',
    shop: 'item-bag',
    house: 'house',
    market: 'coins',
    bridge: 'bridge',
    gate: 'door-steel',
    tower: 'tower',
    guardhouse: 'shield',
    warehouse: 'barrel',
  };
  const size = scene.grid.size;
  const worthAPin = (a) => a.kind !== 'house' || a.text || (a.events ?? []).length;
  const byName = new Map(journal.pages.map((p) => [p.name, p.id]));
  const notes = key.areas
    .filter((a) => worthAPin(a) && a.at?.length === 2)
    .map((a) => ({
      entryId: journal.id,
      pageId: byName.get(`${a.n}. ${a.name}`),
      x: Math.round((a.at[1] + 0.5) * size),
      y: Math.round((a.at[0] + 0.5) * size),
      text: `${a.n}. ${a.name}`,
      fontSize: 28,
      iconSize: Math.max(32, Math.round(size * 0.5)),
      texture: { src: `icons/svg/${icons[a.kind] ?? 'book'}.svg` },
      flags: { world: { [TAG]: { area: a.n } } },
    }));
  await scene.createEmbeddedDocuments('Note', notes);
  pins = notes.length;
}

let entities = 0;
if (choice.entities && game.system.id === 'dnd5e' && key) {
  const paragraph = (text) => `<p>${esc(text).replace(/\n/g, '<br>')}</p>`;
  for (const [field, collection, DocumentClass, type] of [
    ['npcs_detail', game.actors, Actor, 'npc'],
    ['items_detail', game.items, Item, 'loot'],
  ]) {
    const unique = new Map(
      key.areas
        .flatMap((a) => a[field] ?? [])
        .filter((e) => e.id)
        .map((e) => [e.id, e]),
    );
    for (const e of unique.values()) {
      const notes =
        paragraph(e.public || '') +
        '<h3>GM notes</h3>' +
        paragraph(e.notes || '') +
        (e.secrets ? '<h3>Secrets</h3>' + paragraph(e.secrets) : '');
      const system =
        type === 'npc'
          ? { details: { biography: { public: paragraph(e.public || ''), value: notes } } }
          : { description: { value: notes, chat: paragraph(e.public || '') } };
      const doc = {
        name: e.name,
        type,
        system,
        flags: { world: { [TAG]: { entry: e.id, sourceMap: choice.slug } } },
      };
      if (e.image) doc.img = e.image;
      const old = collection.find((d) => d.flags?.world?.[TAG]?.entry === e.id);
      if (old) await old.update(doc);
      else
        await DocumentClass.create({
          ...doc,
          ownership: { default: CONST.DOCUMENT_OWNERSHIP_LEVELS.NONE },
        });
      entities++;
    }
  }
}

ui.notifications.info(
  `${scene.name}: ${data.walls.length} walls, ${data.lights.length} lights` +
    (choice.roofs && roofs.length ? `, ${roofs.length} roofs` : '') +
    (pins ? `, journal with ${pins} pins` : '') +
    (entities ? `, ${entities} NPC / item sheets` : '') +
    '.',
);
