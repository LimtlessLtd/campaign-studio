'use strict';
// Applies reviewed document changes to a closed Foundry world's LevelDB folders. Campaign Studio runs it
// with the classic-level library bundled in the Foundry installation that opens the world, so the files
// are written by the same LevelDB code Foundry reads them with.
//
//   <node> foundry_leveldb_write.cjs <classic-level folder> --probe
//   <node> foundry_leveldb_write.cjs <classic-level folder> <job.json>
//
// job.json: {"collections": [{"folder": "...", "ops": [{"type": "put" | "del", "key": "...",
//   "expected_sha256": "<hash of the stored bytes>" | null, "value": <document>}]}]}
// Every database is opened first; LevelDB's lock makes that fail while Foundry has the world open. Every
// key must still hold the bytes the plan was made from, or nothing is written. Each database's changes
// are one atomic batch. Prints one JSON line: {"ok", "committed": [folders], "error"}.
const crypto = require('node:crypto');
const fs = require('node:fs');

const [library, jobPath] = process.argv.slice(2);
const print = (value) => process.stdout.write(JSON.stringify(value) + '\n');
const sha256 = (bytes) => crypto.createHash('sha256').update(bytes).digest('hex');

async function stored(db, key) {
  try {
    return await db.get(key); // classic-level 3 resolves undefined for a missing key
  } catch (error) {
    if (error.code === 'LEVEL_NOT_FOUND' || error.notFound) return undefined; // classic-level 1
    throw error;
  }
}

async function main() {
  const { ClassicLevel } = require(library);
  if (jobPath === '--probe') {
    const { version } = JSON.parse(fs.readFileSync(`${library}/package.json`, 'utf8'));
    return print({ ok: true, classic_level: version, node: process.versions.node });
  }
  const job = JSON.parse(fs.readFileSync(jobPath, 'utf8'));
  const opened = [];
  const committed = [];
  try {
    for (const collection of job.collections) {
      const db = new ClassicLevel(collection.folder, {
        keyEncoding: 'utf8',
        valueEncoding: 'buffer',
        createIfMissing: false,
      });
      await db.open();
      opened.push({ db, collection });
    }
    for (const { db, collection } of opened) {
      for (const op of collection.ops) {
        const bytes = await stored(db, op.key);
        if ((bytes === undefined ? null : sha256(bytes)) !== op.expected_sha256)
          throw new Error(`A document changed after the dry run: ${op.key}`);
      }
    }
    for (const { db, collection } of opened) {
      await db.batch(
        collection.ops.map((op) =>
          op.type === 'del'
            ? { type: 'del', key: op.key }
            : { type: 'put', key: op.key, value: Buffer.from(JSON.stringify(op.value), 'utf8') },
        ),
      );
      committed.push(collection.folder);
    }
    print({ ok: true, committed });
  } catch (error) {
    print({ ok: false, committed, error: String(error?.message ?? error) });
  } finally {
    for (const { db } of opened) await db.close().catch(() => {});
  }
}

main().catch((error) => {
  print({ ok: false, committed: [], error: String(error?.message ?? error) });
  process.exitCode = 1;
});
