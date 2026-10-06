'use strict';

/* A GM Script macro and this tab exchange snapshots without granting Foundry cross-origin API access. */
function liveLibraryCard(context, onImported) {
  const card = h('section', { class: 'card library-live' });
  const status = h('div', { role: 'status' });
  const actions = h('div', { class: 'row' });
  let world = null;
  let peer = null;
  let connected = false;
  let pending = '';
  let timeout = null;

  const download = async () => {
    const response = await fetch('/api/foundry/library/macro');
    if (!response.ok) throw new Error('Could not download the Foundry macro.');
    const script =
      `const CAMPAIGN_STUDIO_LIVE_ORIGIN = ${JSON.stringify(location.origin)};\n` +
      (await response.text());
    const url = URL.createObjectURL(new Blob([script], { type: 'text/javascript' }));
    const link = h('a', { href: url, download: 'campaign-studio-live-library.js' });
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 30000);
  };

  const request = () => {
    if (!peer || pending) return;
    pending = uid('live');
    render(status, h('p', { class: 'muted' }, 'Reading the running Foundry world…'));
    try {
      peer.source.postMessage(
        { studioLive: 1, type: 'request', nonce: peer.nonce, requestId: pending },
        peer.origin,
      );
    } catch (error) {
      pending = '';
      render(
        status,
        h('p', { class: 'error-text' }, 'The Foundry tab closed. Run the macro again.'),
      );
      return;
    }
    timeout = setTimeout(() => {
      pending = '';
      render(
        status,
        h('p', { class: 'error-text' }, 'Foundry did not answer. Run the macro again.'),
      );
    }, 30000);
  };

  const showPeer = () => {
    if (!peer) return;
    const matches = world && peer.world.id === world.id;
    render(
      status,
      h(
        'p',
        { class: matches ? 'muted' : 'error-text' },
        matches
          ? `Foundry GM at ${peer.origin} offers world ${peer.world.title}. Only documents the GM can read are sent.`
          : `Foundry offered ${peer.world.title} (${peer.world.id}). Select that world in Studio Settings before connecting.`,
      ),
    );
    render(
      actions,
      h(
        'button',
        { disabled: !matches, onclick: request },
        connected ? 'Refresh from Foundry' : 'Connect and import',
      ),
    );
  };

  const receive = async (event) => {
    const data = event.data;
    if (
      context.signal.aborted ||
      event.source !== window.opener ||
      !/^https?:\/\//.test(event.origin) ||
      data?.studioLive !== 1
    )
      return;
    if (data.type === 'hello') {
      if (
        typeof data.nonce !== 'string' ||
        data.nonce.length > 100 ||
        typeof data.world?.id !== 'string' ||
        typeof data.world?.title !== 'string'
      )
        return;
      if (
        peer?.source === event.source &&
        peer.origin === event.origin &&
        peer.nonce === data.nonce
      )
        return;
      peer = {
        source: event.source,
        origin: event.origin,
        nonce: data.nonce,
        world: data.world,
      };
      connected = false;
      pending = '';
      clearTimeout(timeout);
      showPeer();
      return;
    }
    if (!peer || event.origin !== peer.origin || data.nonce !== peer.nonce) return;
    if (data.type === 'changed' && connected && !pending) {
      render(
        status,
        h('p', { class: 'small-note' }, 'Foundry changed. Refresh to read its current documents.'),
      );
      return;
    }
    if (!pending || data.requestId !== pending) return;
    clearTimeout(timeout);
    pending = '';
    if (data.type === 'error') {
      render(
        status,
        h('p', { class: 'error-text' }, String(data.error || 'Foundry could not refresh.')),
      );
      return;
    }
    if (data.type !== 'snapshot') return;
    try {
      const report = await post('/api/foundry/library/live-import', data.snapshot);
      if (context.signal.aborted) return;
      connected = true;
      toast(importSummary(report));
      await onImported();
      showPeer();
    } catch (error) {
      render(status, h('p', { class: 'error-text' }, error.message));
    }
  };

  window.addEventListener('message', receive);
  context.signal.addEventListener(
    'abort',
    () => {
      window.removeEventListener('message', receive);
      clearTimeout(timeout);
    },
    { once: true },
  );
  render(
    card,
    h('h3', {}, 'Live Foundry connection'),
    h(
      'p',
      { class: 'muted' },
      'Download the Script macro, run it as GM in the selected Foundry world, then approve its request in the Studio tab it opens. Refreshes keep entries you edited in Studio.',
    ),
    h('button', { onclick: () => attempt(download) }, 'Download live bridge macro'),
    status,
    actions,
  );
  return {
    element: card,
    setWorld(selected) {
      world = selected;
      if (peer) showPeer();
    },
  };
}
