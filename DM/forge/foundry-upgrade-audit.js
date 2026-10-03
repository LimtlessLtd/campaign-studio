// Run as GM in the isolated clone after Foundry has migrated it to v13 or v14.
// Exports package metadata and activation state only; changes nothing in Foundry.
if (!game.user.isGM) return ui.notifications.warn('Run this macro as the GM.');
if (![13, 14].includes(Number(String(game.version).split('.')[0])))
  return ui.notifications.error('This migration audit export requires Foundry v13 or v14.');
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
  phase: 'migrated-clone',
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
  return ui.notifications.error('The migration audit inventory exceeds the 2 MB import limit.');
const url = URL.createObjectURL(blob);
const link = document.createElement('a');
link.href = url;
link.download = `campaign-studio-${game.world.id}-migration-audit.json`;
document.body.append(link);
link.click();
link.remove();
setTimeout(() => URL.revokeObjectURL(url), 30000);
ui.notifications.info('Downloaded a read-only migration audit inventory for Campaign Studio.');
