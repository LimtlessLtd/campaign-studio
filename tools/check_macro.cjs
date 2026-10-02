// Foundry Script macros permit top-level await; node --check does not.
const fs = require('node:fs');
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
new AsyncFunction(fs.readFileSync('DM/forge/foundry-import-macro.js', 'utf8'));
console.log('Foundry macro syntax passed (not executed).');
