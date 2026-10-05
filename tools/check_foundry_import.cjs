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
  'CONST',
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
              ],
              items_detail: [{ id: 'item-1', name: 'Key', public: 'Iron key.' }],
            },
          ],
        },
      },
    },
  },
});

function world(generation, systemId) {
  const scenes = [];
  const journal = [];
  const actors = [];
  const items = [];
  const notices = [];
  const data = fixture();
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
        json: async () =>
          clone(
            path.endsWith('index.json') ? [{ slug: 'fixture-map', name: 'Fixture map' }] : data,
          ),
      }),
      {
        prompt: async () => ({ slug: 'fixture-map', roofs: true, journal: true, entities: true }),
        confirm: async () => true,
      },
      { create: makeScene },
      { create: makeJournal },
      { create: makeEntity(actors) },
      { create: makeEntity(items) },
      { OCCLUSION_MODES: { FADE: 3 }, DOCUMENT_OWNERSHIP_LEVELS: { NONE: 0 } },
    );
  return { game, data, notices, run };
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
  assert.equal(game.actors.length, systemId === 'dnd5e' ? 1 : 0);
  assert.equal(game.items.length, systemId === 'dnd5e' ? 1 : 0);
  if (systemId === 'dnd5e') {
    assert.equal(game.actors[0].type, 'npc');
    assert.match(game.actors[0].system.details.biography.value, /Hidden door/);
    assert.equal(game.items[0].type, 'loot');
  }

  // A second export adds an area whose name matches an unrelated GM page. Reimport must update only
  // managed documents, preserve custom documents, and reuse the same scene/journal/entity IDs.
  const old = {
    scene: scene.id,
    journal: game.journal[0].id,
    actor: game.actors[0]?.id,
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
    assert.equal(game.actors.length, 2);
    assert.equal(game.items.length, 2);
    assert.equal(game.actors.find((actor) => actor.id === old.actor).name, 'Keeper');
    assert.equal(game.items.find((item) => item.id === old.item).name, 'Key');
  } else {
    assert.equal(game.actors.length, 1);
    assert.equal(game.items.length, 1);
  }
}

async function main() {
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
