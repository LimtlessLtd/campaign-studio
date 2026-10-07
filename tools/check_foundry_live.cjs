// Exercise the GM macro's read permission and paired-window boundary with synthetic documents.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;

const script = new AsyncFunction(
  'game', 'ui', 'window', 'Hooks', 'CONST', 'Blob', 'URL', 'DOMParser',
  'setInterval', 'clearInterval', 'setTimeout', 'document',
  `const CAMPAIGN_STUDIO_LIVE_ORIGIN = 'http://127.0.0.1:8766';\n` +
    fs.readFileSync('DM/forge/foundry-library-export.js', 'utf8'),
);
const doc = (id, visible) => ({
  id, uuid: `Actor.${id}`, name: id, type: 'npc', img: '', system: {},
  testUserPermission: () => visible,
});
const game = {
  user: { isGM: true },
  version: '13.351',
  world: { id: 'fixture-world', title: 'Fixture' },
  system: { id: 'dnd5e', version: '4.0' },
  scenes: { contents: [] }, journal: { contents: [
    { ...doc('journal', true), pages: { contents: [doc('readable-page', true), doc('hidden-page', false)] } },
  ] },
  actors: { contents: [
    doc('visible', true),
    { ...doc('old-compendium', true), _stats: { compendiumSource: '' }, flags: { core: { sourceId: 'Compendium.dnd5e.monsters.test' } } },
    doc('secret', false),
  ] },
  items: { contents: [] },
};
const sent = [];
const studioTab = { closed: false, postMessage: (data, origin) => sent.push({ data, origin }) };
const listeners = new Map();
const window = {
  open: () => studioTab,
  addEventListener: (name, callback) => listeners.set(name, callback),
  removeEventListener: (name) => listeners.delete(name),
};
const hooks = new Map();
const Hooks = {
  on: (name, callback) => hooks.set(name, callback),
  off: (name) => hooks.delete(name),
};
const notices = [];
const ui = { notifications: {
  info: (value) => notices.push(value),
  warn: (value) => notices.push(value),
  error: (value) => notices.push(value),
} };
class DOMParser {
  parseFromString() {
    return { querySelectorAll: () => [], body: { querySelectorAll: () => [], textContent: '' } };
  }
}

(async () => {
  const args = [game, ui, window, Hooks, { DOCUMENT_OWNERSHIP_LEVELS: { OBSERVER: 2 } },
    Blob, URL, DOMParser, () => 1, () => {}, () => {}, {}];
  await script(...args);
  assert.equal(sent[0].data.type, 'hello');
  assert.equal(sent[0].origin, 'http://127.0.0.1:8766');
  const nonce = sent[0].data.nonce;
  const request = { studioLive: 1, type: 'request', nonce, requestId: 'once' };
  listeners.get('message')({ source: studioTab, origin: 'https://other.example', data: request });
  assert.equal(sent.length, 1);
  listeners.get('message')({ source: studioTab, origin: 'http://127.0.0.1:8766', data: request });
  assert.equal(sent[1].data.type, 'snapshot');
  assert.deepEqual(sent[1].data.snapshot.documents.actors.map((actor) => actor.id), ['visible', 'old-compendium']);
  assert.equal(sent[1].data.snapshot.documents.actors[1].compendium, true);
  assert.deepEqual(sent[1].data.snapshot.documents.journals[0].pages.map((page) => page.id), ['readable-page']);
  hooks.get('updateActor')();
  assert.equal(sent[2].data.type, 'changed');
  game.user.isGM = false;
  listeners.get('message')({ source: studioTab, origin: 'http://127.0.0.1:8766', data: request });
  assert.equal(sent.length, 3);
  assert.match(notices.at(-1), /Only a GM/);
  window.__campaignStudioLiveBridge.stop();
  assert.equal(listeners.size, 0);
  assert.equal(hooks.size, 0);
  console.log('Foundry live GM bridge and read permission fixture passed.');
})().catch((error) => { console.error(error); process.exitCode = 1; });
