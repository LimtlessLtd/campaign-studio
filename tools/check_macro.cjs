// Foundry Script macros permit top-level await; node --check does not.
const fs = require('node:fs');
const assert = require('node:assert/strict');
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
for (const file of [
  'DM/forge/foundry-import-macro.js',
  'DM/forge/foundry-library-export.js',
  'DM/forge/foundry-upgrade-inventory.js',
]) {
  new AsyncFunction(fs.readFileSync(file, 'utf8'));
}
async function testUpgradeInventory() {
  const script = new AsyncFunction(
    'game',
    'ui',
    'document',
    'URL',
    'Blob',
    'setTimeout',
    fs.readFileSync('DM/forge/foundry-upgrade-inventory.js', 'utf8'),
  );
  const pack = (id, active) => ({
    id,
    title: id,
    version: '1.0.0',
    active,
    toObject: () => ({ compatibility: {}, relationships: {} }),
  });
  let downloaded;
  const link = { click() {}, remove() {} };
  await script(
    {
      user: { isGM: true },
      version: '12.331',
      world: { id: 'fixture-world', title: 'Fixture' },
      system: pack('dnd5e', true),
      modules: new Map([
        ['alpha', pack('alpha', true)],
        ['beta', pack('beta', false)],
      ]),
      settings: { get: () => ({ alpha: true, missing: true, beta: false }) },
    },
    { notifications: { info() {}, warn() {}, error() {} } },
    { createElement: () => link, body: { append() {} } },
    { createObjectURL: (blob) => ((downloaded = blob), 'blob:fixture'), revokeObjectURL() {} },
    Blob,
    () => {},
  );
  const exported = JSON.parse(await downloaded.text());
  assert.equal(exported.schema, 2);
  assert.deepEqual(exported.enabledModuleIds, ['alpha', 'missing']);
  assert.deepEqual(
    exported.modules.map((item) => [item.id, item.enabled]),
    [
      ['alpha', true],
      ['beta', false],
    ],
  );
}
testUpgradeInventory()
  .then(() => console.log('Foundry macro syntax and v12 inventory fixture passed.'))
  .catch((error) => {
    console.error(error);
    process.exitCode = 1;
  });
