// Navigation must stop an obsolete page load before it can replace the current view.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const root = { children: [], querySelectorAll: () => [] };
const location = { hash: '#/' };
let cancelled = false;
const render = (node, ...children) => (node.children = children);
const emptyPage = async () => {};
const context = {
  AbortController,
  S: { route: 0 },
  location,
  root,
  window: { scrollY: 0, scrollTo: () => {}, addEventListener: () => {} },
  document: { querySelectorAll: () => [], addEventListener: () => {} },
  history: { replaceState: () => {} },
  h: () => ({ children: [], querySelectorAll: () => [] }),
  render,
  api: (_path, { signal }) =>
    new Promise((_resolve, reject) =>
      signal.addEventListener('abort', () => {
        cancelled = true;
        reject(new DOMException('cancelled', 'AbortError'));
      }),
    ),
  studioDashboard: async (_arg, page) => {
    await page.api('/slow');
    render(page.view, 'obsolete');
  },
  studioSettings: async (_arg, page) => render(page.view, 'settings'),
  studioWelcome: emptyPage,
  studioLibrary: emptyPage,
  memoryPage: emptyPage,
  recordingsPage: emptyPage,
  prepPage: emptyPage,
  threadsPage: emptyPage,
  arcsPage: emptyPage,
  codexPage: emptyPage,
  studioMaps: emptyPage,
  worldPage: emptyPage,
  studioArt: emptyPage,
  handoutsPage: emptyPage,
  inboxPage: emptyPage,
  notesPage: emptyPage,
};
vm.runInNewContext(fs.readFileSync('DM/app/app.js', 'utf8') + '\nthis.route = route;', context);

(async () => {
  const first = context.route();
  location.hash = '#/settings';
  await context.route();
  await Promise.race([
    first,
    new Promise((_resolve, reject) =>
      setTimeout(() => reject(new Error('Obsolete route did not finish')), 1000),
    ),
  ]);
  assert.equal(cancelled, true);
  assert.equal(root.children[0].children[0], 'settings');
  console.log('Route cancellation keeps the current page visible.');
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
