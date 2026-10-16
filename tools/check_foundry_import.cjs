// Contract fixtures for the standalone GM import macro. No Foundry installation or campaign data is used.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
const macro = new AsyncFunction(
  'game',
  'ui',
  'foundry',
  'fetch',
  'Dialog',
  'Scene',
  'JournalEntry',
  'Actor',
  'Item',
  'Folder',
  'CONST',
  'fromUuidSync',
  fs.readFileSync('DM/forge/foundry-import-macro.js', 'utf8'),
);

const clone = (value) => structuredClone(value);
const tag = (value) => value.flags?.world?.wotgForge;
const fixture = () => ({
  name: 'Fixture map',
  width: 200,
  height: 200,
  background: { src: 'wotg-maps/fixture-map.webp' },
  grid: { type: 1, size: 100, distance: 5, units: 'ft', color: '#000000', alpha: 0.2 },
  environment: { darknessLevel: 0.4, globalLight: { enabled: true } },
  fog: { exploration: true },
  walls: [{ c: [0, 0, 100, 0], move: 20 }],
  lights: [{ x: 20, y: 20, config: { bright: 5, dim: 15 } }],
  flags: {
    world: {
      wotgForge: {
        plan: 'fixture-map',
        targetWorld: 'fixture-world',
        roofs: [{ src: 'wotg-maps/fixture-map/roof.webp', x: 0, y: 0, width: 100, height: 100 }],
        key: {
          areas: [
            {
              n: 1,
              name: 'Landing',
              kind: 'tavern',
              at: [0, 0],
              npcs_detail: [
                {
                  id: 'npc-1',
                  name: 'Keeper',
                  public: 'Welcome.',
                  notes: 'Keeps the key.',
                  secrets: 'Hidden door.',
                },
                {
                  id: 'fvtt-hero',
                  name: 'Hero',
                  public: 'Already in the world.',
                  uuid: 'Actor.hero',
                  image: 'worlds/fixture-world/hero.webp',
                },
              ],
              items_detail: [{ id: 'item-1', name: 'Key', public: 'Iron key.' }],
            },
          ],
        },
      },
    },
  },
});

function world(generation, systemId, bundleMode = false) {
  const scenes = [];
  const journal = [];
  const actors = [{ id: 'hero', name: 'Hero', flags: {} }]; // imported from Foundry, not by the macro
  const items = [];
  const folders = [];
  const notices = [];
  const data = fixture();
  const secondData = fixture();
  secondData.name = 'Second map';
  secondData.background.src = 'wotg-maps/second-map.webp';
  secondData.flags.world.wotgForge.plan = 'second-map';
  const bundle = {
    format: 'campaign-studio-session-bundle',
    schema: 1,
    session: 's1',
    title: 'The crossing',
    targetWorld: 'fixture-world',
    maps: [
      { slug: 'fixture-map', name: 'Fixture map' },
      { slug: 'second-map', name: 'Second map' },
    ],
    actors: [
      { id: 'npc-1', name: 'Keeper', public: 'Welcome.' },
      ...Array.from({ length: 5 }, (_, n) => ({
        id: `npc-${n + 2}`,
        name: `Guard ${n + 2}`,
        public: 'On duty.',
      })),
    ],
    items: [{ id: 'item-1', name: 'Key', public: 'Iron key.' }],
    handouts: [
      { id: 'letter', title: 'Letter', player_text: 'Meet at dusk.', secrets: 'A trap.' },
      { id: 'sketch', title: 'Sketch', player_text: 'A bridge.', secrets: '' },
    ],
  };
  let nextId = 0;
  const id = () => String(++nextId);
  const kinds = { Wall: 'walls', AmbientLight: 'lights', Tile: 'tiles', Note: 'notes' };
  const makeEmbedded = (item) => ({ ...clone(item), id: id() });
  const makeScene = (item) => {
    const scene = {
      ...clone(item),
      id: id(),
      tokens: [],
      tiles: [],
      notes: [],
      walls: item.walls.map(makeEmbedded),
      lights: item.lights.map(makeEmbedded),
      async update(changes) {
        Object.assign(this, clone(changes));
      },
      async createThumbnail() {
        return { thumb: 'fixture-thumb.webp' };
      },
      async deleteEmbeddedDocuments(kind, ids) {
        this[kinds[kind]] = this[kinds[kind]].filter((entry) => !ids.includes(entry.id));
      },
      async createEmbeddedDocuments(kind, entries) {
        this[kinds[kind]].push(...entries.map(makeEmbedded));
      },
    };
    scenes.push(scene);
    return scene;
  };
  const makeJournal = (item) => {
    const entry = {
      ...clone(item),
      id: id(),
      pages: item.pages.map(makeEmbedded),
      async update(changes) {
        Object.assign(this, clone(changes));
      },
      async updateEmbeddedDocuments(kind, entries) {
        assert.equal(kind, 'JournalEntryPage');
        for (const change of entries) {
          const old = this.pages.find((page) => page.id === change._id);
          assert.ok(old);
          Object.assign(old, clone(change));
        }
      },
      async createEmbeddedDocuments(kind, entries) {
        assert.equal(kind, 'JournalEntryPage');
        this.pages.push(...entries.map(makeEmbedded));
      },
      async deleteEmbeddedDocuments(kind, ids) {
        assert.equal(kind, 'JournalEntryPage');
        this.pages = this.pages.filter((page) => !ids.includes(page.id));
      },
    };
    journal.push(entry);
    return entry;
  };
  const makeEntity = (collection) => async (item) => {
    const entity = {
      ...clone(item),
      id: id(),
      async update(changes) {
        Object.assign(this, clone(changes));
      },
    };
    collection.push(entity);
    return entity;
  };
  const game = {
    release: { generation },
    user: { isGM: true },
    world: { id: 'fixture-world' },
    system: { id: systemId },
    scenes,
    journal,
    actors,
    items,
    folders,
  };
  const run = () =>
    macro(
      game,
      { notifications: { warn: (message) => notices.push(message), info: () => {} } },
      {
        utils: {
          escapeHTML: (value) =>
            value.replace(
              /[&<>"']/g,
              (char) =>
                ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[char],
            ),
        },
      },
      async (path) => ({
        json: async () => {
          if (path === 'wotg-maps/index.json')
            return clone(bundleMode ? bundle.maps : bundle.maps.slice(0, 1));
          if (path === 'wotg-maps/sessions/index.json')
            return clone(
              bundleMode ? [{ id: 's1', title: bundle.title, targetWorld: 'fixture-world' }] : [],
            );
          if (path === 'wotg-maps/sessions/s1.json') return clone(bundle);
          return clone(path === 'wotg-maps/second-map.json' ? secondData : data);
        },
      }),
      {
        prompt: async () => ({
          selection: bundleMode ? 'session:s1' : 'map:fixture-map',
          roofs: true,
          journal: true,
          entities: true,
        }),
        confirm: async () => true,
      },
      { create: makeScene },
      { create: makeJournal },
      { create: makeEntity(actors) },
      { create: makeEntity(items) },
      {
        create: async (item) => {
          const folder = {
            ...clone(item),
            id: id(),
            async update(changes) {
              Object.assign(this, clone(changes));
            },
          };
          folders.push(folder);
          return folder;
        },
      },
      { OCCLUSION_MODES: { FADE: 3 }, DOCUMENT_OWNERSHIP_LEVELS: { NONE: 0 } },
      (uuid) => actors.find((actor) => `Actor.${actor.id}` === uuid) ?? null,
    );
  return { game, data, bundle, notices, run };
}

async function checkBundle(generation) {
  const state = world(generation, 'dnd5e', true);
  await state.run();
  const { game, bundle, notices } = state;
  assert.deepEqual(notices, []);
  assert.equal(game.scenes.length, 2);
  assert.equal(game.actors.length, 7); // six managed actors and the existing Foundry hero
  assert.equal(game.items.length, 1);
  assert.equal(game.journal.length, 5); // two map keys, a GM overview and two GM handouts
  assert.equal(game.folders.length, 4);
  const overview = game.journal.find((row) => tag(row)?.journal === 'overview');
  assert.match(overview.pages[0].text.content, /Cast/);
  assert.match(overview.pages[0].text.content, /Items/);
  const letter = game.journal.find((row) => tag(row)?.handout === 'letter');
  assert.equal(letter.ownership.default, 0);
  assert.match(letter.pages[0].text.content, /GM secrets/);
  const old = {
    scene: game.scenes[0].id,
    actor: game.actors.find((row) => tag(row)?.entry === 'npc-2').id,
    letter: letter.id,
    folders: game.folders.map((folder) => folder.id),
  };
  game.scenes[0].tokens.push({ id: 'gm-token', flags: {} });
  letter.pages.push({ id: 'gm-page', flags: {}, text: { content: 'Keep me.' } });
  bundle.actors[1].name = 'Guard captain';
  bundle.handouts[0].player_text = 'Meet at sunrise.';
  bundle.title = 'The return crossing';
  await state.run();
  assert.deepEqual(notices, []);
  assert.equal(game.scenes.length, 2);
  assert.equal(game.actors.length, 7);
  assert.equal(game.journal.length, 5);
  assert.equal(game.scenes[0].id, old.scene);
  assert.equal(game.scenes[0].tokens[0].id, 'gm-token');
  assert.equal(game.actors.find((row) => tag(row)?.entry === 'npc-2').id, old.actor);
  assert.equal(game.actors.find((row) => tag(row)?.entry === 'npc-2').name, 'Guard captain');
  assert.equal(letter.id, old.letter);
  assert.match(letter.pages.find((page) => tag(page)?.page === 'handout').text.content, /sunrise/);
  assert.equal(letter.pages.find((page) => page.id === 'gm-page').text.content, 'Keep me.');
  assert.deepEqual(game.folders.map((folder) => folder.id), old.folders);
  assert.ok(game.folders.every((folder) => folder.name === bundle.title));
}

async function checkJournalOnlyBundle() {
  const { game, notices, run } = world(12, 'other', true);
  await run();
  assert.deepEqual(notices, []);
  assert.equal(game.scenes.length, 2);
  assert.equal(game.actors.length, 1);
  assert.equal(game.items.length, 0);
  const overview = game.journal.find((row) => tag(row)?.journal === 'overview');
  assert.match(overview.pages[0].text.content, /Guard 2/);
  assert.match(overview.pages[0].text.content, /Iron key/);
  assert.equal(overview.ownership.default, 0);
}

async function check(generation, systemId) {
  const state = world(generation, systemId);
  await state.run();
  const { game, data } = state;
  assert.equal(game.scenes.length, 1);
  const scene = game.scenes[0];
  assert.equal(scene.walls.length, 1);
  assert.equal(scene.lights.length, 1);
  assert.equal(scene.tiles.length, 1);
  assert.equal(scene.notes.length, 1);
  if (generation === 11) {
    assert.equal(scene.darkness, 0.4);
    assert.equal(scene.globalLight, true);
    assert.equal(scene.fogExploration, true);
    assert.equal(scene.environment, undefined);
    assert.equal(scene.fog, undefined);
    assert.equal(scene.tiles[0].overhead, true);
    assert.equal(scene.tiles[0].roof, true);
  } else {
    assert.equal(scene.environment.darknessLevel, 0.4);
    assert.equal(scene.fog.exploration, true);
    assert.equal(scene.tiles[0].elevation, 20);
    assert.equal(scene.tiles[0].restrictions.weather, true);
  }
  assert.equal(game.actors.length, systemId === 'dnd5e' ? 2 : 1);
  assert.equal(game.items.length, systemId === 'dnd5e' ? 1 : 0);
  if (systemId === 'dnd5e') {
    assert.equal(game.actors[1].type, 'npc');
    assert.match(game.actors[1].system.details.biography.value, /Hidden door/);
    assert.equal(game.items[0].type, 'loot');
  }

  // A second export adds an area whose name matches an unrelated GM page. Reimport must update only
  // managed documents, preserve custom documents, and reuse the same scene/journal/entity IDs.
  const old = {
    scene: scene.id,
    journal: game.journal[0].id,
    actor: game.actors[1]?.id,
    item: game.items[0]?.id,
  };
  const custom = { id: 'custom', flags: {}, name: 'GM content' };
  scene.walls.push(clone(custom));
  scene.lights.push(clone(custom));
  scene.tiles.push(clone(custom));
  scene.notes.push(clone(custom));
  scene.tokens.push(clone(custom));
  const customPage = {
    id: 'custom',
    name: '2. Gallery',
    text: { content: 'GM-only addition' },
    flags: {},
  };
  game.journal[0].pages.push(customPage);
  game.journal[0].pages.push({
    id: 'custom-renamed',
    name: '1. Landing revised',
    text: { content: 'Another GM-only page' },
    flags: {},
  });
  game.actors.push({ id: 'custom', name: 'GM actor', flags: {} });
  game.items.push({ id: 'custom', name: 'GM item', flags: {} });
  data.flags.world.wotgForge.key.areas.push({ n: 2, name: 'Gallery', kind: 'shop', at: [1, 1] });
  data.flags.world.wotgForge.key.areas[0].name = 'Landing revised';
  data.environment.darknessLevel = 0.6;
  await state.run();

  assert.equal(game.scenes.length, 1);
  assert.equal(scene.id, old.scene);
  assert.equal(game.journal.length, 1);
  assert.equal(game.journal[0].id, old.journal);
  assert.equal(generation === 11 ? scene.darkness : scene.environment.darknessLevel, 0.6);
  for (const kind of ['walls', 'lights', 'tiles', 'notes', 'tokens']) {
    assert.equal(scene[kind].filter((entry) => entry.id === 'custom').length, 1, kind);
  }
  assert.equal(scene.walls.length, 2);
  assert.equal(scene.lights.length, 2);
  assert.equal(scene.tiles.length, 2);
  assert.equal(scene.notes.length, 3);
  assert.equal(
    game.journal[0].pages.find((page) => page.id === 'custom').text.content,
    'GM-only addition',
  );
  assert.ok(game.journal[0].pages.find((page) => tag(page)?.page === 'area-2'));
  const areaOnePage = game.journal[0].pages.find((page) => tag(page)?.page === 'area-1');
  assert.equal(scene.notes.find((note) => tag(note)?.area === 1).pageId, areaOnePage.id);
  assert.equal(
    game.journal[0].pages.find((page) => page.id === 'custom-renamed').text.content,
    'Another GM-only page',
  );
  assert.equal(game.actors.find((actor) => actor.id === 'custom').name, 'GM actor');
  assert.equal(game.items.find((item) => item.id === 'custom').name, 'GM item');
  if (systemId === 'dnd5e') {
    assert.equal(game.actors.length, 3);
    assert.equal(game.items.length, 2);
    assert.equal(game.actors.find((actor) => actor.id === old.actor).name, 'Keeper');
    assert.equal(game.items.find((item) => item.id === old.item).name, 'Key');
  } else {
    assert.equal(game.actors.length, 2);
    assert.equal(game.items.length, 1);
  }
  // The linked actor stays the only Hero, unchanged, and the GM page links to it.
  assert.deepEqual(
    game.actors.filter((actor) => actor.name === 'Hero'),
    [{ id: 'hero', name: 'Hero', flags: {} }],
  );
  const areaPage = game.journal[0].pages.find((page) => tag(page)?.page === 'area-1');
  assert.match(areaPage.text.content, /@UUID\[Actor\.hero\]\{Hero\}/);
}

async function main() {
  for (const generation of [11, 12, 13]) await checkBundle(generation);
  await checkJournalOnlyBundle();
  for (const generation of [11, 12, 13]) {
    for (const system of ['dnd5e', 'other']) await check(generation, system);
  }
  const unsupported = world(14, 'dnd5e');
  await unsupported.run();
  assert.equal(unsupported.game.scenes.length, 0);
  assert.match(unsupported.notices[0], /supports Foundry v11–v13/);
  const nonGM = world(12, 'dnd5e');
  nonGM.game.user.isGM = false;
  await nonGM.run();
  assert.equal(nonGM.game.scenes.length, 0);
  assert.match(nonGM.notices[0], /as the GM/);
  const otherWorld = world(12, 'dnd5e');
  otherWorld.game.world.id = 'another-world';
  await otherWorld.run();
  assert.equal(otherWorld.game.scenes.length, 0);
  assert.match(otherWorld.notices[0], /another world/);
  console.log('Foundry v11/v12/v13 and dnd5e/journal-only import/reimport fixtures passed.');
}
main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
