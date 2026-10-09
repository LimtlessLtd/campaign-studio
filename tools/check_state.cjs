// A request aborted while its body is being read must reject, never read as an empty answer that
// replaces the lists pages rely on.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const reply = (json, ok = true) => ({ ok, statusText: 'x', json });
const context = {
  fetch: undefined,
  window: { addEventListener: () => {} },
  document: { addEventListener: () => {} },
  setInterval: () => 0,
  setTimeout,
  DOMException,
};
vm.runInNewContext(
  fs.readFileSync('DM/app/state.js', 'utf8') +
    '\nthis.api = api; this.recordChoices = recordChoices; this.S = S;',
  context,
);
const { api, recordChoices, S } = context;

(async () => {
  const aborted = () => Promise.reject(Object.assign(new Error('aborted'), { name: 'AbortError' }));
  context.fetch = async () => reply(aborted);
  await assert.rejects(api('/api/anything'), { name: 'AbortError' });
  await assert.rejects(recordChoices('codex'), { name: 'AbortError' });

  const unreadable = () => Promise.reject(new SyntaxError('Unexpected end of JSON input'));
  context.fetch = async () => reply(unreadable, false);
  await assert.rejects(api('/api/anything'), { message: 'x' }); // an error status keeps its reason

  S.recordIndex.codex = [{ id: 'mira' }];
  context.fetch = async () => reply(unreadable);
  await assert.rejects(recordChoices('codex'), /Could not read the codex list/);
  assert.deepEqual(S.recordIndex.codex, [{ id: 'mira' }]); // the last good list stays

  context.fetch = async () => reply(async () => [{ id: 'aria' }]);
  await recordChoices('codex');
  assert.deepEqual(S.recordIndex.codex, [{ id: 'aria' }]);
  console.log('API aborts and unreadable choices never replace the record lists.');
})();
