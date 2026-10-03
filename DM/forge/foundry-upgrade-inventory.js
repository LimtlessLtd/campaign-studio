// Run as GM inside the original Foundry v12 world. This exports package metadata
// and activation state only. It does not change packages or world documents.
if (!game.user.isGM) return ui.notifications.warn('Run this macro as the GM.');
if (Number(String(game.version).split('.')[0]) !== 12)
  return ui.notifications.error('This inventory export requires Foundry v12.');
const moduleConfiguration = game.settings.get('core', 'moduleConfiguration');
if (
  !moduleConfiguration ||
  typeof moduleConfiguration !== 'object' ||
  Array.isArray(moduleConfiguration)
)
  return ui.notifications.error('The world module configuration could not be read.');

const packageInfo = (pack) => {
  const data = pack.toObject();
  return {
    id: pack.id,
    title: pack.title,
    version: pack.version,
    manifest: pack.manifest ?? data.manifest ?? '',
    compatibility: data.compatibility ?? {},
    relationships: data.relationships ?? {},
    locked: Boolean(pack.locked ?? data.locked),
  };
};
const inventory = {
  format: 'campaign-studio-foundry-upgrade-inventory',
  schema: 2,
  exportedAt: new Date().toISOString(),
  world: {
    id: game.world.id,
    title: game.world.title,
    system: game.system.id,
    coreVersion: game.version,
  },
  system: packageInfo(game.system),
  enabledModuleIds: Object.entries(moduleConfiguration)
    .filter(([, enabled]) => enabled === true)
    .map(([id]) => id),
  modules: Array.from(game.modules.values()).map((pack) => ({
    ...packageInfo(pack),
    enabled: Boolean(pack.active),
  })),
};
const blob = new Blob([JSON.stringify(inventory, null, 2)], { type: 'application/json' });
if (blob.size > 2 * 1024 * 1024)
  return ui.notifications.error('The upgrade inventory exceeds the 2 MB import limit.');
const url = URL.createObjectURL(blob);
const link = document.createElement('a');
link.href = url;
link.download = `campaign-studio-${game.world.id}-upgrade-inventory.json`;
document.body.append(link);
link.click();
link.remove();
setTimeout(() => URL.revokeObjectURL(url), 30000);
ui.notifications.info('Downloaded a read-only upgrade inventory for Campaign Studio.');
