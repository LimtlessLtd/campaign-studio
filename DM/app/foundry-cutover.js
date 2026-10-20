'use strict';

// The upgrade wizard's last step: review cutover readiness, confirm what changed in the live User Data
// since the backup, switch the live User Data to the migrated clone, and roll back. foundryUpgradeCard
// places `nodes` and passes each passing audit to `useAudit`.
function foundryCutoverStep(hasWorld) {
  const form = { audit: '', closed: false, receipt: '' };
  let confirmed = new Set();
  let review = null;
  const reviewOutput = h('div', { class: 'upgrade-report', role: 'status' });
  const switchOutput = h('div', { class: 'upgrade-report', role: 'status' });
  const rollbackOutput = h('div', { class: 'upgrade-report', role: 'status' });
  const auditInput = formInput(form, 'audit', 'Passing migrated-clone audit', {
    help: 'A passing audit fills this in automatically. You can paste an earlier audit path.',
  });
  const receiptInput = formInput(form, 'receipt', 'Saved cutover receipt', {
    help: 'The switch fills this in. Each receipt is saved beside the upgrade backup.',
  });
  const list = (items, line) => h('ul', {}, ...items.map((item) => h('li', {}, line(item))));
  const changed = {
    removed: 'gone since the backup',
    added: 'new since the backup',
    changed: 'changed since the backup',
  };
  const changeLine = (change) => {
    const text = `${change.area}: ${changed[change.change]} (${change.added} added, ${change.removed} removed, ${change.changed} changed: ${change.examples.join(', ')}).`;
    if (change.area === review.world_area) return h('span', { class: 'error-text' }, text);
    const state = { on: confirmed.has(change.area) };
    const leftOut = change.kind === 'world' && change.change === 'removed';
    return [
      text,
      formInput(
        state,
        'on',
        leftOut
          ? 'I removed this world on purpose: leave it out of the upgraded User Data'
          : 'I made this change on purpose: the upgraded User Data will not have it',
        {
          type: 'checkbox',
          change: () => (state.on ? confirmed.add(change.area) : confirmed.delete(change.area)),
        },
      ),
    ];
  };
  const heading = {
    blocked: 'Cutover blocked',
    needs_confirmation: 'Confirm the changes since the backup',
    ready_for_cutover: 'Ready for cutover',
  };

  const showReview = () => {
    render(switchOutput);
    switchButton.disabled = false;
    render(
      reviewOutput,
      h('h4', {}, heading[review.status] || review.status),
      h('p', {}, `Target Foundry ${review.target_build} · world ${review.world_id}.`),
      ...review.blockers.map((blocker) => h('p', { class: 'error-text' }, blocker)),
      review.changes.length
        ? [
            h(
              'h5',
              {},
              'Changes in the live User Data since the backup (Foundry logs are ignored)',
            ),
            list(review.changes, changeLine),
          ]
        : h('p', {}, 'The live User Data matches the backup.'),
      review.status === 'needs_confirmation'
        ? h(
            'p',
            { class: 'small-note' },
            'Tick each change you made on purpose, then review again. The upgraded User Data is a copy of the clone, which was made from the backup: changes since then stay only in the original folder, which Studio keeps for rollback.',
          )
        : null,
      review.status === 'ready_for_cutover'
        ? [
            h('p', { class: 'backup-path' }, `Live User Data: ${review.original_user_data}`),
            h('p', { class: 'backup-path' }, `Migrated clone to copy: ${review.clone_path}`),
            h('p', { class: 'backup-path' }, `Verified backup: ${review.backup_path}`),
            h('p', {}, `Saved cutover review: ${review.review_path}`),
            h(
              'p',
              { class: 'small-note' },
              `Studio renames the live folder to a dated rollback folder beside it, copies the clone into its place${review.removed_worlds.length ? ` without ${review.removed_worlds.join(', ')}` : ''}, and points Config/options.json back at it. Nothing is deleted.`,
            ),
            switchButton,
          ]
        : null,
    );
  };

  const reviewButton = h(
    'button',
    {
      class: 'primary',
      disabled: !hasWorld,
      onclick: () =>
        attempt(async () => {
          reviewButton.disabled = true;
          render(
            reviewOutput,
            'Rechecking the audit and clone, and comparing the live User Data with the backup…',
          );
          try {
            review = await post('/api/foundry/upgrade/review-cutover', {
              audit_path: form.audit,
              confirmed_closed: form.closed,
              confirmed_changes: [...confirmed],
            });
            confirmed = new Set(review.confirmed_changes);
            showReview();
          } catch (error) {
            render(reviewOutput, h('span', { class: 'error-text' }, error.message));
            throw error;
          } finally {
            reviewButton.disabled = false;
          }
        }),
    },
    'Review cutover readiness',
  );

  const switchButton = h(
    'button',
    {
      class: 'primary',
      onclick: () =>
        attempt(async () => {
          switchButton.disabled = true;
          render(
            switchOutput,
            'Reviewing again, copying the clone beside the live folder and switching…',
          );
          try {
            const done = await post('/api/foundry/upgrade/cut-over', {
              review_path: review.review_path,
              confirmed_closed: form.closed,
            });
            form.receipt = done.receipt_path;
            receiptInput.querySelector('input').value = done.receipt_path;
            render(
              switchOutput,
              h('h4', {}, 'Switched to the migrated clone'),
              h(
                'p',
                { class: 'backup-path' },
                `Live User Data, now Foundry ${done.target_build}: ${done.live_user_data}`,
              ),
              h('p', { class: 'backup-path' }, `Original kept at: ${done.rollback_user_data}`),
              h(
                'p',
                {},
                `Config/options.json: data path ${done.options.after.dataPath}, port ${done.options.after.port ?? 'unchanged'} (the clone had ${done.options.before.dataPath ?? 'none'}, port ${done.options.before.port ?? 'none'}).`,
              ),
              done.left_out_worlds.length
                ? h('p', {}, `Left out: ${done.left_out_worlds.join(', ')}.`)
                : null,
              done.stale_locks_left_out.length
                ? h('p', {}, `Stale lock folders removed: ${done.stale_locks_left_out.join(', ')}.`)
                : null,
              h('p', {}, `Saved cutover receipt: ${done.receipt_path}`),
              h(
                'p',
                { class: 'small-note' },
                `Open Foundry ${done.target_build} and world ${done.world_id}. Keep the original folder, the backup and the old installation until you have played on the upgraded world; Roll back below puts the original back.`,
              ),
            );
          } catch (error) {
            // A switch that went through stays disabled until the next ready review.
            switchButton.disabled = false;
            render(switchOutput, h('span', { class: 'error-text' }, error.message));
            throw error;
          }
        }),
    },
    'Switch live User Data to the migrated clone',
  );

  const rollbackButton = h(
    'button',
    {
      disabled: !hasWorld,
      onclick: () =>
        attempt(async () => {
          rollbackButton.disabled = true;
          render(rollbackOutput, 'Putting the original User Data back…');
          try {
            const back = await post('/api/foundry/upgrade/roll-back', {
              cutover_path: form.receipt,
              confirmed_closed: form.closed,
            });
            render(
              rollbackOutput,
              h('h4', {}, 'Rolled back to the original User Data'),
              h(
                'p',
                { class: 'backup-path' },
                `Live User Data, Foundry ${back.original_build} again: ${back.live_user_data}`,
              ),
              back.set_aside_user_data
                ? h(
                    'p',
                    { class: 'backup-path' },
                    `Upgraded copy kept at: ${back.set_aside_user_data}`,
                  )
                : null,
              h(
                'p',
                { class: 'small-note' },
                `Open the world with the Foundry ${back.original_build} installation. Never open the upgraded copy with it.`,
              ),
            );
          } catch (error) {
            render(rollbackOutput, h('span', { class: 'error-text' }, error.message));
            throw error;
          } finally {
            rollbackButton.disabled = false;
          }
        }),
    },
    'Roll back to the original User Data',
  );

  return {
    useAudit(path) {
      form.audit = path;
      auditInput.querySelector('input').value = path;
    },
    nodes: [
      h('hr'),
      h('h3', {}, 'Switch to the upgraded world'),
      h(
        'p',
        { class: 'muted' },
        'After the audit passes and the clone is repaired, close both Foundry installations. Studio rechecks the clone against its audit and compares the live User Data with the verified backup. Foundry’s logs are ignored; the world being upgraded must be unchanged, and you confirm any other change, such as a test world you uninstalled.',
      ),
      auditInput,
      formInput(form, 'closed', 'I closed both Foundry installations', { type: 'checkbox' }),
      reviewButton,
      reviewOutput,
      switchOutput,
      h('h4', {}, 'Roll back'),
      h(
        'p',
        { class: 'muted' },
        'With Foundry closed, renames the upgraded User Data aside and puts the original folder back under its name. Nothing is deleted.',
      ),
      receiptInput,
      rollbackButton,
      rollbackOutput,
    ],
  };
}
