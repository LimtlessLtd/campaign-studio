// Foundry Script macros permit top-level await; node --check does not.
const fs = require('node:fs');
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
for (const file of ['DM/forge/foundry-import-macro.js', 'DM/forge/foundry-library-export.js']) {
  new AsyncFunction(fs.readFileSync(file, 'utf8'));
}
console.log('Foundry macro syntax passed (not executed).');
