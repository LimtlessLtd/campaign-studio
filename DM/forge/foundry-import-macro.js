// Map Forge importer for Foundry VTT (v11, v12, v13).
// Paste into a new macro (type: Script) and run it as the GM. It lists maps and session bundles copied into
// Data/wotg-maps/ and imports the chosen one in one go:
//   - the scene: background, grid, walls, doors, windows, lights
//   - roofs: one overhead tile per building that fades when a token walks under it
//   - the DM key: a GM-only journal (one page per numbered area: description, loot, events, who is there)
//     with pins on the map
// Run it again for a map that is already a scene to replace the forged parts; tokens and your own notes stay.
const TAG = 'wotgForge';
const esc = (s) => foundry.utils.escapeHTML(String(s ?? ''));
// An entry imported from this world carries its Foundry UUID: link to that document, never copy it.
const original = (e) => {
  if (!e.uuid) return null;
  try {
    return fromUuidSync(e.uuid) ?? null;
  } catch {
    return null;
  }
};
if (!game.user.isGM) return ui.notifications.warn('Run Campaign Studio imports as the GM.');

// The exported scene uses the v12 shape. Keep version and system differences here so the import
// workflow below only manages document ownership and stable Studio IDs.
const modernCore = {
  scene: (source) => source,
  changes: (source) => ({
    background: source.background,
    width: source.width,
    height: source.height,
    grid: source.grid,
    flags: source.flags,
    ...(source.environment ? { environment: source.environment } : {}),
  }),
  roof: (base, above) => ({
    ...base,
    elevation: above,
    restrictions: { light: false, weather: true },
  }),
};
const CORE_ADAPTERS = {
  11: {
    scene: (source) => {
      const { environment, fog, ...legacy } = source;
      return {
        ...legacy,
        darkness: environment?.darknessLevel ?? 0,
        globalLight: environment?.globalLight?.enabled ?? true,
        fogExploration: fog?.exploration ?? true,
      };
    },
    changes: (source) => ({
      background: source.background,
      width: source.width,
      height: source.height,
      grid: source.grid,
      flags: source.flags,
      darkness: source.darkness,
      globalLight: source.globalLight,
      fogExploration: source.fogExploration,
    }),
    roof: (base) => ({ ...base, overhead: true, roof: true }),
  },
  12: modernCore,
  13: modernCore,
};
const core = CORE_ADAPTERS[game.release?.generation];
if (!core)
  return ui.notifications.warn(
    `Campaign Studio import supports Foundry v11–v13; this world runs v${game.release?.generation ?? '?'}.`,
  );

const paragraph = (text) => `<p>${esc(text).replace(/\n/g, '<br>')}</p>`;
const SYSTEM_ADAPTERS = {
  dnd5e: {
    entityOption:
      '<p><label><input type="checkbox" name="entities" checked> Create / update linked NPCs and items</label></p><p>NPC stat blocks and item mechanics are added as notes. Review their sheets before play.</p>',
    entityDoc: (entry, type) => {
      const notes =
        paragraph(entry.public || '') +
        '<h3>GM notes</h3>' +
        paragraph(entry.notes || '') +
        (entry.secrets ? '<h3>Secrets</h3>' + paragraph(entry.secrets) : '');
      return {
        name: entry.name,
        type,
        system:
          type === 'npc'
            ? { details: { biography: { public: paragraph(entry.public || ''), value: notes } } }
            : { description: { value: notes, chat: paragraph(entry.public || '') } },
      };
    },
  },
};
const systemAdapter = SYSTEM_ADAPTERS[game.system.id] ?? {
  entityOption:
    '<p>Linked characters and items are included in the GM journal. Actor and item sheets currently support D&D 5e.</p>',
};

const index = await fetch('wotg-maps/index.json', { cache: 'no-store' })
  .then((r) => r.json())
  .catch(() => []);
const sessions = await fetch('wotg-maps/sessions/index.json', { cache: 'no-store' })
  .then((r) => r.json())
  .catch(() => []);
if (!Array.isArray(index) || !Array.isArray(sessions))
  return ui.notifications.warn('The Studio import index has an unsupported format.');
const availableSessions = sessions.filter((s) => s?.targetWorld === game.world.id);
if (!(index.length || availableSessions.length))
  return ui.notifications.warn('No maps or session bundles in Data/wotg-maps yet.');

const options = [
  ...availableSessions.map(
    (s) => `<option value="session:${esc(s.id)}">Session: ${esc(s.title)}</option>`,
  ),
  ...index.map((m) => `<option value="map:${esc(m.slug)}">Map: ${esc(m.name)}</option>`),
].join('');
const choice = await Dialog.prompt({
  title: 'Campaign Studio: import into this world',
  content: `<p>Import a map or a whole session. Session bundles include linked maps, sheets and GM handouts.</p>
            <p><select name="selection" style="width:100%">${options}</select></p>
            <p><label><input type="checkbox" name="roofs" checked> Roofs</label>
               <label><input type="checkbox" name="journal" checked> Journal and pins</label></p>
            ${systemAdapter.entityOption}`,
  label: 'Import',
  callback: (html) => ({
    selection: html.find('[name=selection]').val(),
    roofs: html.find('[name=roofs]').is(':checked'),
    journal: html.find('[name=journal]').is(':checked'),
    entities: html.find('[name=entities]').is(':checked'),
  }),
  rejectClose: false,
});
const selection = choice?.selection ?? (choice?.slug ? `map:${choice.slug}` : '');
if (!selection) return;

async function upsertEntities(entries, collection, DocumentClass, type, owner, folder = null) {
  let count = 0;
  for (const entry of entries) {
    if (!entry.id || !entry.name || original(entry)) continue;
    const doc = {
      ...systemAdapter.entityDoc(entry, type),
      ...(folder ? { folder: folder.id } : {}),
      flags: { world: { [TAG]: { entry: entry.id, ...owner } } },
    };
    if (entry.image) doc.img = entry.image;
    const old = collection.find((row) => row.flags?.world?.[TAG]?.entry === entry.id);
    if (old) await old.update(doc);
    else
      await DocumentClass.create({
        ...doc,
        ownership: { default: CONST.DOCUMENT_OWNERSHIP_LEVELS.NONE },
      });
    count++;
  }
  return count;
}

async function importMap(slug, choice, quiet = false) {
  const data = await fetch(`wotg-maps/${slug}.json`, { cache: 'no-store' }).then((r) => r.json());
  if (
    !data?.background?.src ||
    !data?.grid?.size ||
    !Array.isArray(data?.walls) ||
    !Array.isArray(data?.lights)
  )
    return ui.notifications.warn(
      'This map export has an unsupported scene format. Export it again from Campaign Studio.',
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
  const sceneData = core.scene(data);

  let scene = game.scenes.find((s) => s.flags?.world?.[TAG]?.plan === slug);
  if (scene) {
    const ok =
      choice.confirmed ||
      (await Dialog.confirm({
        title: 'Replace the forged parts?',
        content: `<p><b>${esc(scene.name)}</b> came from this map already. Update its generated image, walls, lights, roofs and journal?</p><p>Tokens and custom notes stay.${scene.flags?.world?.[TAG]?.managed ? ' Custom walls and lights stay.' : ' This older import has no wall ownership tags: its walls and lights will be replaced on this update.'}</p>`,
      }));
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
    await scene.update(core.changes(sceneData));
    await scene.createEmbeddedDocuments('Wall', sceneData.walls);
    await scene.createEmbeddedDocuments('AmbientLight', sceneData.lights);
  } else {
    scene = await Scene.create(sceneData);
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
      return core.roof(base, above);
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
          `<li><strong>${original(e) ? `@UUID[${e.uuid}]{${esc(e.name)}}` : esc(e.name)}</strong>` +
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
    let journal = game.journal.find((j) => j.flags?.world?.[TAG]?.plan === slug);
    const pages = [overview, ...key.areas.map(page), ...journalPages];
    if (journal) {
      await journal.update({ name: `${data.name} (DM key)` });
      for (const p of pages) {
        const old = journal.pages.find(
          (old) => old.flags?.world?.[TAG]?.page === p.flags.world[TAG].page,
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
        flags: { world: { [TAG]: { plan: slug } } },
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
    const byArea = new Map(
      journal.pages
        .filter((p) => p.flags?.world?.[TAG]?.page?.startsWith('area-'))
        .map((p) => [p.flags.world[TAG].page, p.id]),
    );
    const notes = key.areas
      .filter((a) => worthAPin(a) && a.at?.length === 2)
      .map((a) => ({
        entryId: journal.id,
        pageId: byArea.get(`area-${a.n}`),
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
  if (choice.entities && systemAdapter.entityDoc && key) {
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
      entities += await upsertEntities(unique.values(), collection, DocumentClass, type, {
        sourceMap: slug,
      });
    }
  }

  if (!quiet)
    ui.notifications.info(
      `${scene.name}: ${data.walls.length} walls, ${data.lights.length} lights` +
        (choice.roofs && roofs.length ? `, ${roofs.length} roofs` : '') +
        (pins ? `, journal with ${pins} pins` : '') +
        (entities ? `, ${entities} NPC / item sheets` : '') +
        '.',
    );
  return {
    scene,
    journal: choice.journal ? game.journal.find((j) => j.flags?.world?.[TAG]?.plan === slug) : null,
  };
}

const sessionId = /^[a-z0-9][a-z0-9-]{0,63}$/;
if (selection.startsWith('map:')) {
  const slug = selection.slice(4);
  if (!index.some((m) => m.slug === slug && sessionId.test(slug)))
    return ui.notifications.warn('Choose a map from this export.');
  await importMap(slug, choice);
} else if (selection.startsWith('session:')) {
  const id = selection.slice(8);
  if (!availableSessions.some((s) => s.id === id && sessionId.test(id)))
    return ui.notifications.warn('Choose a session from this export.');
  let bundle;
  try {
    bundle = await fetch(`wotg-maps/sessions/${id}.json`, { cache: 'no-store' }).then((r) =>
      r.json(),
    );
  } catch {
    return ui.notifications.warn('The session bundle is missing. Export it again from Studio.');
  }
  const validEntity = (entry) =>
    entry &&
    typeof entry.id === 'string' &&
    entry.id &&
    typeof entry.name === 'string' &&
    entry.name;
  const validHandout = (entry) =>
    entry &&
    typeof entry.id === 'string' &&
    entry.id &&
    typeof entry.title === 'string' &&
    entry.title &&
    typeof entry.player_text === 'string' &&
    typeof entry.secrets === 'string';
  if (
    bundle?.format !== 'campaign-studio-session-bundle' ||
    bundle.schema !== 1 ||
    bundle.session !== id ||
    bundle.targetWorld !== game.world.id ||
    !Array.isArray(bundle.maps) ||
    !Array.isArray(bundle.actors) ||
    !Array.isArray(bundle.items) ||
    !Array.isArray(bundle.handouts) ||
    bundle.maps.length > 12 ||
    bundle.actors.length + bundle.items.length > 100 ||
    bundle.handouts.length > 50 ||
    typeof bundle.title !== 'string' ||
    !bundle.title.trim() ||
    bundle.maps.some(
      (m) => !m || !sessionId.test(m.slug) || !index.some((e) => e.slug === m.slug),
    ) ||
    new Set(bundle.maps.map((m) => m.slug)).size !== bundle.maps.length ||
    bundle.actors.some((entry) => !validEntity(entry)) ||
    bundle.items.some((entry) => !validEntity(entry)) ||
    new Set([...bundle.actors, ...bundle.items].map((entry) => entry.id)).size !==
      bundle.actors.length + bundle.items.length ||
    bundle.handouts.some((entry) => !validHandout(entry)) ||
    new Set(bundle.handouts.map((entry) => entry.id)).size !== bundle.handouts.length
  )
    return ui.notifications.warn('The session bundle is invalid or belongs to another world.');
  const approved = await Dialog.confirm({
    title: 'Import the whole session?',
    content: `<p>Import <b>${esc(bundle.title)}</b>: ${bundle.maps.length} maps, ${bundle.actors.length} actors, ${bundle.items.length} items and ${bundle.handouts.length} handouts?</p><p>Existing Studio documents update in place. GM additions stay.</p>`,
  });
  if (!approved) return;
  const folderFor = async (type) => {
    const old = game.folders.find(
      (folder) => folder.type === type && folder.flags?.world?.[TAG]?.session === id,
    );
    if (old) {
      if (old.name !== bundle.title) await old.update({ name: bundle.title });
      return old;
    }
    return Folder.create({
      name: bundle.title,
      type,
      flags: { world: { [TAG]: { session: id } } },
    });
  };
  let imported = 0;
  try {
    const sceneFolder = bundle.maps.length ? await folderFor('Scene') : null;
    const journalFolder = await folderFor('JournalEntry');
    const upsertJournal = async (identity, title, page) => {
      const old = game.journal.find((row) =>
        Object.entries(identity).every(([key, value]) => row.flags?.world?.[TAG]?.[key] === value),
      );
      if (old) {
        await old.update({ name: title, folder: journalFolder.id });
        const managed = old.pages.find(
          (row) => row.flags?.world?.[TAG]?.page === page.flags.world[TAG].page,
        );
        if (managed)
          await old.updateEmbeddedDocuments('JournalEntryPage', [{ ...page, _id: managed.id }]);
        else await old.createEmbeddedDocuments('JournalEntryPage', [page]);
      } else
        await JournalEntry.create({
          name: title,
          folder: journalFolder.id,
          pages: [page],
          ownership: { default: CONST.DOCUMENT_OWNERSHIP_LEVELS.NONE },
          flags: { world: { [TAG]: identity } },
        });
    };
    for (const map of bundle.maps) {
      const result = await importMap(map.slug, { ...choice, confirmed: true }, true);
      if (!result) throw new Error(`Map ${map.slug} could not be imported.`);
      await result.scene.update({ folder: sceneFolder.id });
      if (result.journal) await result.journal.update({ folder: journalFolder.id });
      imported++;
    }
    if (choice.entities && systemAdapter.entityDoc) {
      for (const [entries, collection, DocumentClass, type, folderType] of [
        [bundle.actors, game.actors, Actor, 'npc', 'Actor'],
        [bundle.items, game.items, Item, 'loot', 'Item'],
      ]) {
        if (!entries.length) continue;
        const folder = await folderFor(folderType);
        await upsertEntities(entries, collection, DocumentClass, type, { session: id }, folder);
      }
    }
    const list = (title, entries) =>
      entries.length
        ? `<h3>${title}</h3><ul>${entries
            .map(
              (entry) =>
                `<li><strong>${original(entry) ? `@UUID[${esc(entry.uuid)}]{${esc(entry.name)}}` : esc(entry.name)}</strong>` +
                (entry.public ? `<br>${esc(entry.public)}` : '') +
                (entry.notes ? `<br>${esc(entry.notes)}` : '') +
                (entry.secrets ? `<br>GM secrets: ${esc(entry.secrets)}` : '') +
                '</li>',
            )
            .join('')}</ul>`
        : '';
    await upsertJournal({ session: id, journal: 'overview' }, `${bundle.title} (GM overview)`, {
      name: 'Overview',
      type: 'text',
      text: {
        format: 1,
        content: list('Cast', bundle.actors) + list('Items', bundle.items),
      },
      flags: { world: { [TAG]: { page: 'overview' } } },
    });
    for (const handout of bundle.handouts) {
      const content =
        `<h2>${esc(handout.title)}</h2>` +
        paragraph(handout.player_text) +
        (handout.secrets ? '<h3>GM secrets</h3>' + paragraph(handout.secrets) : '');
      const page = {
        name: handout.title,
        type: 'text',
        text: { format: 1, content },
        flags: { world: { [TAG]: { page: 'handout' } } },
      };
      await upsertJournal({ session: id, handout: handout.id }, handout.title, page);
    }
    ui.notifications.info(
      `${bundle.title}: imported ${imported} maps, ${bundle.actors.length} actors, ${bundle.items.length} items and ${bundle.handouts.length} GM handouts.`,
    );
  } catch (error) {
    ui.notifications.warn(
      `Session import stopped after ${imported} maps: ${error.message}. Fix the export and run it again; tagged documents update in place.`,
    );
  }
} else ui.notifications.warn('Choose a map or session from this export.');
