/* Document helpers shared by the app: copies, comparison, new records and the three-way autosave merge.
   Free of DOM access so tools/check_merge.cjs can test them in Node. */
'use strict';

const clone = (x) => JSON.parse(JSON.stringify(x));
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
const isObj = (x) => x && typeof x === 'object' && !Array.isArray(x);

/* A new record of a stored shape ({required, defaults}, defined once in DM/shapes.py): required fields
   first, then the defaults, then extra links such as map or area. */
function newRecord(shape, kind, fields = {}) {
  if (!shape) throw new Error('Unknown record shape: ' + kind);
  const missing = shape.required.filter((k) => !(k in fields));
  if (missing.length) throw new Error(`A new ${kind} needs ${missing.join(', ')}.`);
  const record = {};
  for (const k of shape.required) record[k] = fields[k];
  for (const [k, v] of Object.entries(shape.defaults))
    record[k] = k in fields ? fields[k] : clone(v);
  return Object.assign(record, fields);
}

/* Fold the server's version into ours, in place (pages hold references into these objects): anything we
   haven't changed since we loaded it takes the server's value; lists of objects with ids merge item by item. */
function mergeInto(base, local, server) {
  if (Array.isArray(local) && Array.isArray(server)) {
    const keyed = (a) => a.every((x) => isObj(x) && ('id' in x || 'n' in x));
    const idOf = (x) => ('id' in x ? x.id : 'n' + x.n);
    if (keyed(local) && keyed(server)) {
      const b = new Map((Array.isArray(base) ? base : []).filter(isObj).map((x) => [idOf(x), x]));
      const l = new Map(local.map((x) => [idOf(x), x]));
      const s = new Map(server.map((x) => [idOf(x), x]));
      server.forEach((x, i) => {
        if (l.has(idOf(x))) mergeInto(b.get(idOf(x)), l.get(idOf(x)), x);
        else if (!b.has(idOf(x))) local.splice(Math.min(i, local.length), 0, x); // new on the server
      });
      for (let i = local.length - 1; i >= 0; i--) {
        // deleted on the server and untouched here
        const x = local[i];
        if (!s.has(idOf(x)) && b.has(idOf(x)) && same(b.get(idOf(x)), x)) local.splice(i, 1);
      }
      return local;
    }
    // A list without ids (goals, checklist, loot) keeps our edits, adds what the server added and drops
    // what the server removed that we had not touched. It changes in place: open editors hold it.
    if (same(base, server)) return local;
    const has = (list, x) => Array.isArray(list) && list.some((y) => same(x, y));
    const merged = same(base, local)
      ? server
      : local.filter((x) => !(has(base, x) && !has(server, x)));
    if (merged !== server)
      for (const x of server) if (!has(base, x) && !has(merged, x)) merged.push(x);
    local.splice(0, local.length, ...merged);
    return local;
  }
  if (isObj(local) && isObj(server)) {
    for (const k of Object.keys(server)) {
      const bv = isObj(base) ? base[k] : undefined;
      if (!(k in local)) {
        if (!(isObj(base) && k in base)) local[k] = server[k];
        continue;
      }
      if (isObj(local[k]) || Array.isArray(local[k])) {
        const m = mergeInto(bv, local[k], server[k]);
        if (m !== local[k]) local[k] = m;
      } else if (same(bv, local[k])) local[k] = server[k];
    }
    for (const k of Object.keys(local)) {
      // removed on the server (such as a cleared error) and untouched here
      if (!(k in server) && isObj(base) && k in base && same(base[k], local[k])) delete local[k];
    }
    return local;
  }
  return same(base, local) ? server : local;
}
