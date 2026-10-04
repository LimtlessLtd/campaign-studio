// The autosave merge must never drop an edit made on either side, and new records follow their shape.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');

const context = {};
vm.runInNewContext(
  fs.readFileSync('DM/app/merge.js', 'utf8') +
    '\nthis.mergeInto = mergeInto; this.clone = clone; this.newRecord = newRecord;',
  context,
);
const { mergeInto, clone, newRecord } = context;
const merge = (base, local, server) => {
  const result = clone(local);
  return JSON.parse(JSON.stringify(mergeInto(clone(base), result, clone(server))));
};

// Keyed lists merge item by item; untouched deletions follow the server.
assert.deepEqual(
  merge(
    {
      entries: [
        { id: 'a', name: 'A' },
        { id: 'b', name: 'B' },
      ],
    },
    {
      entries: [
        { id: 'a', name: 'A edited' },
        { id: 'b', name: 'B' },
      ],
    },
    {
      entries: [
        { id: 'a', name: 'A' },
        { id: 'c', name: 'C' },
      ],
    },
  ),
  {
    entries: [
      { id: 'a', name: 'A edited' },
      { id: 'c', name: 'C' },
    ],
  },
);

// Lists without ids keep both sides' additions instead of one side replacing the other.
assert.deepEqual(
  merge(
    { goals: ['Reach the gate'], checklist: [{ text: 'Map', done: false }] },
    { goals: ['Reach the gate', 'Find the key'], checklist: [{ text: 'Map', done: true }] },
    {
      goals: ['Reach the gate', 'Learn who sent the notice'],
      checklist: [
        { text: 'Map', done: false },
        { text: 'Prepare the patrol', done: false },
      ],
    },
  ),
  {
    goals: ['Reach the gate', 'Find the key', 'Learn who sent the notice'],
    checklist: [
      { text: 'Map', done: true },
      { text: 'Prepare the patrol', done: false },
    ],
  },
);

// An item the server removed is dropped unless it was edited here.
assert.deepEqual(merge({ goals: ['A', 'B'] }, { goals: ['A', 'B', 'C'] }, { goals: ['A'] }), {
  goals: ['A', 'C'],
});

// A field the server removed stays removed unless it was edited here.
assert.deepEqual(
  merge(
    { status: 'new', error: 'Failed' },
    { status: 'new', error: 'Failed' },
    { status: 'review' },
  ),
  { status: 'review' },
);
assert.deepEqual(merge({ notes: 'x' }, { notes: 'mine' }, {}), { notes: 'mine' });

// Unchanged local values take the server's; local edits win over server edits to the same value.
assert.deepEqual(merge({ a: 1, b: 1 }, { a: 2, b: 1 }, { a: 3, b: 4 }), { a: 2, b: 4 });

// Lists change in place, because an open editor keeps a reference to the array it renders.
for (const local of [['A', 'B'], ['A']]) {
  const page = { goals: local };
  const goals = page.goals;
  mergeInto({ goals: ['A'] }, page, { goals: ['A', 'C'] });
  assert.equal(page.goals, goals);
  assert.ok(goals.includes('C'));
}

// Items change in place too: an editor typing into a checklist or loot row keeps a live reference.
for (const edited of [false, true]) {
  const page = { checklist: [{ text: 'Map', done: false }] };
  const row = page.checklist[0];
  if (edited) page.checklist.push({ text: 'Rope', done: false });
  mergeInto({ checklist: [{ text: 'Map', done: false }] }, page, {
    checklist: [
      { text: 'Map', done: false },
      { text: 'Patrol', done: false },
    ],
  });
  assert.equal(page.checklist[0], row);
  assert.deepEqual(
    page.checklist.map((x) => x.text),
    edited ? ['Map', 'Rope', 'Patrol'] : ['Map', 'Patrol'],
  );
}

// Repeated values count: one removed on the server and one added here leaves one.
assert.deepEqual(
  merge({ loot: ['Potion', 'Rope'] }, { loot: ['Potion', 'Rope', 'Potion'] }, { loot: ['Rope'] }),
  { loot: ['Potion', 'Rope'] },
);
assert.deepEqual(merge({ goals: ['A'] }, { goals: ['A', 'A'] }, { goals: ['A', 'B'] }), {
  goals: ['A', 'A', 'B'],
});

// New records: required fields first, then fresh copies of the defaults, then extra links.
const thread = { required: ['id', 'title'], defaults: { status: 'open', pcs: [] } };
const made = newRecord(thread, 'thread', { map: 'harbour', title: 'T', id: 't1' });
assert.deepEqual(Object.keys(made), ['id', 'title', 'status', 'pcs', 'map']);
made.pcs.push('ash');
assert.deepEqual(thread.defaults.pcs, []);
assert.throws(() => newRecord(thread, 'thread', { id: 't2' }), /A new thread needs title/);
assert.throws(() => newRecord(undefined, 'unknown', {}), /Unknown record shape/);

console.log('Autosave merge and new record cases passed.');
