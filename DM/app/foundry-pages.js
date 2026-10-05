'use strict';

function foundryBackupCard(hasWorld, context) {
  const form = { destination: '', path: '', restore: '', closed: false };
  const scan = h('div', { class: 'provider-status backup-status', role: 'status' }, 'Scanning…');
  const result = h('div', { class: 'backup-result', role: 'status' });
  const destination = formInput(form, 'destination', 'Backup destination folder', {
    placeholder: 'Choose a folder outside Foundry User Data',
    help: 'The app creates a new, timestamped backup folder here. Keep it on a separate drive if possible.',
  });
  const backupPath = formInput(form, 'path', 'Backup folder to verify or restore', {
    help: 'A successful backup fills this in automatically. You can paste an earlier backup folder.',
  });
  const restorePath = formInput(form, 'restore', 'New restore test folder', {
    help: 'Must not exist yet. The live Foundry User Data folder is never overwritten.',
  });
  const showResult = (...content) => render(result, ...content);
  const refresh = async () => {
    render(scan, 'Scanning Foundry User Data…');
    try {
      const info = await context.api('/api/foundry/backup/plan');
      if (!form.destination) {
        form.destination = info.suggested_destination;
        destination.querySelector('input').value = form.destination;
      }
      render(
        scan,
        h('b', {}, `${info.world.title} · Foundry ${info.world.foundry_version || 'unknown'}`),
        h(
          'span',
          {},
          `${info.files.toLocaleString()} files · ${(info.bytes / 1024 ** 3).toFixed(2)} GB`,
        ),
        h('span', { class: 'backup-path' }, info.user_data),
        info.foundry_processes.length
          ? h('strong', { class: 'error-text' }, 'Foundry is running. Close it before backup.')
          : h('span', {}, 'No running Foundry process detected.'),
      );
    } catch (error) {
      render(scan, h('span', { class: 'error-text' }, error.message));
    }
  };
  const backupButton = h(
    'button',
    {
      class: 'primary',
      disabled: !hasWorld,
      onclick: () =>
        attempt(async () => {
          backupButton.disabled = true;
          showResult(
            'Copying and verifying the complete Foundry User Data folder. Keep this page open…',
          );
          try {
            const copy = await post('/api/foundry/backup/create', {
              destination: form.destination,
              confirmed_closed: form.closed,
            });
            form.path = copy.path;
            form.restore = copy.path + '-restore-test';
            backupPath.querySelector('input').value = form.path;
            restorePath.querySelector('input').value = form.restore;
            showResult(
              h('b', {}, 'Backup verified: '),
              h('span', { class: 'backup-path' }, copy.path),
              h('p', {}, `${copy.files.toLocaleString()} files checked by SHA-256.`),
            );
            await refresh();
          } catch (error) {
            showResult(h('span', { class: 'error-text' }, error.message));
            throw error;
          } finally {
            backupButton.disabled = false;
          }
        }),
    },
    'Create and verify full backup',
  );
  const verifyButton = h(
    'button',
    {
      onclick: () =>
        attempt(async () => {
          showResult('Checking every file in the backup…');
          try {
            const check = await post('/api/foundry/backup/verify', { path: form.path });
            showResult(
              `Backup verified: ${check.files.toLocaleString()} files.`,
              h('br'),
              check.path,
            );
          } catch (error) {
            showResult(h('span', { class: 'error-text' }, error.message));
            throw error;
          }
        }),
    },
    'Verify backup again',
  );
  const rehearseButton = h(
    'button',
    {
      onclick: () =>
        attempt(async () => {
          rehearseButton.disabled = true;
          showResult('Creating and verifying an isolated restore copy…');
          try {
            const restored = await post('/api/foundry/backup/rehearse', {
              path: form.path,
              destination: form.restore,
            });
            showResult(
              h('b', {}, 'Restore copy verified: '),
              h('span', { class: 'backup-path' }, restored.path),
              h('p', { class: 'backup-path' }, `Restore receipt: ${restored.receipt_path}`),
              h(
                'p',
                {},
                `Next, open this copy with Foundry ${restored.world.core_version || 'the original version'} using its separate User Data path. Confirm the world, maps and assets load before upgrading the live world.`,
              ),
            );
          } catch (error) {
            showResult(h('span', { class: 'error-text' }, error.message));
            throw error;
          } finally {
            rehearseButton.disabled = false;
          }
        }),
    },
    'Create restore test copy',
  );
  const card = h(
    'section',
    { class: 'card backup-card' },
    h('div', { class: 'section-icon' }, icon('clock')),
    h('h2', {}, 'Foundry backup and restore test'),
    h(
      'p',
      { class: 'muted' },
      'Before upgrading a Foundry world, take a Snapshot in Foundry Setup. Then close Foundry and copy its complete User Data folder here. The full copy includes assets outside world packages.',
    ),
    scan,
    h('button', { onclick: () => attempt(refresh) }, 'Refresh scan'),
    destination,
    formInput(form, 'closed', 'I have closed Foundry VTT', { type: 'checkbox' }),
    backupButton,
    h('hr'),
    backupPath,
    restorePath,
    h('div', { class: 'row' }, verifyButton, rehearseButton),
    result,
    h(
      'p',
      { class: 'small-note' },
      'Keep your current Foundry installer for rollback. Checksums compare copied files with the backup manifest; the restore is fully tested only after you open the isolated copy in the original Foundry version. This feature does not upgrade or change the live world.',
    ),
  );
  if (hasWorld) refresh();
  else render(scan, 'Save a local Foundry world in Settings to scan its User Data.');
  return card;
}

function foundryUpgradeCard(hasWorld) {
  const form = { backup: '', disabled: '', approved: '' };
  const cloneForm = { report: '', receipt: '', destination: '', inspected: false, reviewed: false };
  const reviewForm = { plan: '', confirmed: false };
  const auditForm = {
    review: '',
    confirmed: false,
    launch: false,
    scenes: false,
    journals: false,
    actorsItems: false,
    modules: false,
  };
  const cutoverForm = { audit: '', closed: false };
  let inventory = null;
  let cloneInventory = null;
  let migratedInventory = null;
  const status = h(
    'div',
    { class: 'provider-status', role: 'status' },
    'Import a GM inventory first.',
  );
  const output = h('div', { class: 'upgrade-report', 'aria-live': 'polite' });
  const cloneOutput = h('div', { class: 'upgrade-report', role: 'status' });
  const reviewOutput = h('div', { class: 'upgrade-report', role: 'status' });
  const auditOutput = h('div', { class: 'upgrade-report', role: 'status' });
  const cutoverOutput = h('div', { class: 'upgrade-report', role: 'status' });
  const reportInput = formInput(cloneForm, 'report', 'Saved compatibility report', {
    help: 'The latest report fills this in automatically. You can paste an earlier report path.',
  });
  const cloneDestinationInput = formInput(cloneForm, 'destination', 'New upgrade clone folder', {
    help: 'Must be a new path, separate from the live world, backup and restore test copy.',
  });
  const reviewPlanInput = formInput(reviewForm, 'plan', 'Saved clone plan', {
    help: 'The latest clone plan fills this in automatically. You can paste an earlier plan path.',
  });
  const auditReviewInput = formInput(auditForm, 'review', 'Passing v12 clone review', {
    help: 'A passing review fills this in automatically. You can paste an earlier review path.',
  });
  const cutoverAuditInput = formInput(cutoverForm, 'audit', 'Passing migrated-clone audit', {
    help: 'A passing audit fills this in automatically. You can paste an earlier audit path.',
  });
  const input = h('input', {
    type: 'file',
    accept: '.json,application/json',
    onchange: (event) =>
      attempt(async () => {
        const file = event.target.files?.[0];
        if (!file) return;
        inventory = null;
        if (file.size > 2 * 1024 * 1024) throw new Error('The inventory must be under 2 MB.');
        inventory = JSON.parse(await file.text());
        if (inventory.format !== 'campaign-studio-foundry-upgrade-inventory')
          throw new Error('Choose the GM upgrade inventory export.');
        render(
          status,
          `${inventory.world?.title || 'World'} · Foundry ${inventory.world?.coreVersion || '?'} · ${inventory.modules?.filter((m) => m.enabled).length || 0} enabled modules`,
        );
        render(output);
      }),
  });
  const ids = (value) =>
    value
      .split(',')
      .map((id) => id.trim())
      .filter(Boolean);
  const compatibility = (value) =>
    value
      ? `Foundry ${value.minimum || '?'}–${value.maximum || 'later'}, verified ${value.verified || 'unknown'}`
      : 'No compatible release selected';
  const run = h(
    'button',
    {
      class: 'primary',
      disabled: !hasWorld,
      onclick: () =>
        attempt(async () => {
          if (!inventory) throw new Error('Import the GM inventory first.');
          run.disabled = true;
          render(output, h('p', {}, 'Verifying the backup and checking Foundry package releases…'));
          try {
            const report = await post('/api/foundry/upgrade/report', {
              inventory,
              backup_path: form.backup,
              disabled_modules: ids(form.disabled),
              approved_dependencies: ids(form.approved),
            });
            cloneForm.report = report.report_path;
            reportInput.querySelector('input').value = cloneForm.report;
            if (!cloneForm.destination) {
              cloneForm.destination = report.backup_path + '-upgrade-clone';
              cloneDestinationInput.querySelector('input').value = cloneForm.destination;
            }
            render(
              output,
              h(
                'h3',
                {},
                report.recommended_build ? `Foundry ${report.recommended_build}` : 'No full match',
              ),
              h(
                'p',
                {},
                report.recommended_build
                  ? `${report.needs_clone_testing ? 'Needs clone testing. ' : ''}System ${report.system.id}: ${report.system.original_version} → ${report.system.selected_version}.`
                  : 'Stay on v12 or explicitly choose modules to disable, then run the report again.',
              ),
              report.newest_all_verified_build
                ? h(
                    'p',
                    {},
                    `Newest build with all selected packages verified: ${report.newest_all_verified_build}.`,
                  )
                : report.recommended_build
                  ? h('p', {}, 'No build has verification declared by every selected package.')
                  : null,
              report.locked_changes.length
                ? h(
                    'p',
                    { class: 'error-text' },
                    `Locked packages need an explicit unlock in the clone before installation: ${report.locked_changes.join(', ')}.`,
                  )
                : null,
              report.requires_gm_choice
                ? h(
                    'p',
                    { class: 'error-text' },
                    'The report includes excluded modules or explicit GM choices. Review these before changing a clone.',
                  )
                : null,
              report.activation_discrepancies?.length
                ? h(
                    'p',
                    { class: 'error-text' },
                    `Module configuration and active state differ for: ${report.activation_discrepancies.join(', ')}. Check these modules in the v12 clone.`,
                  )
                : null,
              h('h4', {}, 'Installed module decisions'),
              h(
                'ul',
                {},
                ...report.modules.map((module) =>
                  h(
                    'li',
                    {},
                    h('b', {}, module.id),
                    ` · ${module.original_version} → ${module.selected_version || 'none'} · enabled ${module.original_enabled ? 'yes' : 'no'} → ${module.proposed_enabled === null ? 'undecided' : module.proposed_enabled ? 'yes' : 'no'} · ${module.directory_status} · ${module.disabled_reason || module.activation_reason || 'retain in clone'}`,
                    module.directory_url
                      ? h(
                          'a',
                          {
                            href: module.directory_url,
                            target: '_blank',
                            rel: 'noopener noreferrer',
                          },
                          ' Directory',
                        )
                      : null,
                    module.selected_manifest
                      ? h(
                          'small',
                          {},
                          ` · ${compatibility(module.selected_compatibility)} · ${module.selected_manifest}`,
                        )
                      : null,
                  ),
                ),
              ),
              h('h4', {}, 'Required dependencies'),
              h(
                'ul',
                {},
                ...(report.dependencies.length
                  ? report.dependencies.map((dependency) =>
                      h(
                        'li',
                        {},
                        `${dependency.id} ${dependency.version} · ${dependency.manifest}`,
                      ),
                    )
                  : [h('li', {}, 'No additional module activations selected.')]),
              ),
              h(
                'details',
                {},
                h('summary', {}, `All ${report.candidates.length} candidate builds and blockers`),
                h(
                  'ul',
                  {},
                  ...report.candidates.map((candidate) =>
                    h(
                      'li',
                      {},
                      `${candidate.build}: ${candidate.full_match ? (candidate.all_verified ? 'full match, verified' : 'full match, needs clone testing') : candidate.blockers.join(' ')}`,
                    ),
                  ),
                ),
              ),
              h(
                'p',
                { class: 'small-note' },
                `Inventory and report saved beside the verified backup: ${report.report_path}`,
              ),
              h(
                'p',
                { class: 'small-note' },
                'This report does not disable modules or migrate the world. Test the restored v12 copy, then disable excluded modules in that clone before its first launch in a newer Foundry build.',
              ),
            );
          } finally {
            run.disabled = false;
          }
        }),
    },
    'Build compatibility report',
  );
  const prepareClone = h(
    'button',
    {
      class: 'primary',
      disabled: !hasWorld,
      onclick: () =>
        attempt(async () => {
          prepareClone.disabled = true;
          render(
            cloneOutput,
            'Verifying evidence and copying the v12 backup into a separate clone…',
          );
          try {
            const plan = await post('/api/foundry/upgrade/prepare-clone', {
              report_path: cloneForm.report,
              restore_receipt_path: cloneForm.receipt,
              destination: cloneForm.destination,
              confirmed_v12_restore: cloneForm.inspected,
              confirmed_report: cloneForm.reviewed,
            });
            reviewForm.plan = plan.plan_path;
            reviewPlanInput.querySelector('input').value = plan.plan_path;
            render(
              cloneOutput,
              h('h4', {}, `v12 clone prepared for Foundry ${plan.target_build}`),
              h('p', { class: 'backup-path' }, plan.clone_path),
              h('p', {}, `Saved plan: ${plan.plan_path}`),
              h(
                'p',
                {},
                plan.disable_in_v12.length
                  ? `In Foundry v12 Manage Modules, disable: ${plan.disable_in_v12.map((item) => `${item.id} (${item.reason})`).join('; ')}.`
                  : 'The report has no enabled modules to disable.',
              ),
              h(
                'p',
                {},
                'Launch only this clone with Foundry v12. Save and reload its module configuration, then confirm the excluded modules are off before opening the clone in a newer Foundry build. Migration is not yet available in Studio.',
              ),
            );
          } finally {
            prepareClone.disabled = false;
          }
        }),
    },
    'Prepare isolated v12 clone',
  );
  const cloneInventoryInput = h('input', {
    type: 'file',
    accept: '.json,application/json',
    onchange: (event) =>
      attempt(async () => {
        const file = event.target.files?.[0];
        if (!file) return;
        cloneInventory = null;
        if (file.size > 2 * 1024 * 1024) throw new Error('The inventory must be under 2 MB.');
        cloneInventory = JSON.parse(await file.text());
        if (cloneInventory.format !== 'campaign-studio-foundry-upgrade-inventory')
          throw new Error('Choose a GM upgrade inventory export from the v12 clone.');
        render(
          reviewOutput,
          `Imported ${cloneInventory.world?.title || 'world'} · Foundry ${cloneInventory.world?.coreVersion || '?'} · ${cloneInventory.enabledModuleIds?.length || 0} configured modules`,
        );
      }),
  });
  const reviewClone = h(
    'button',
    {
      class: 'primary',
      disabled: !hasWorld,
      onclick: () =>
        attempt(async () => {
          if (!cloneInventory) throw new Error('Import a fresh inventory from the v12 clone.');
          reviewClone.disabled = true;
          render(reviewOutput, 'Checking the clone against its saved plan and backup…');
          try {
            const review = await post('/api/foundry/upgrade/review-clone', {
              plan_path: reviewForm.plan,
              inventory: cloneInventory,
              confirmed_clone: reviewForm.confirmed,
            });
            if (review.status === 'v12_modules_reviewed') {
              auditForm.review = review.review_path;
              auditReviewInput.querySelector('input').value = review.review_path;
            }
            render(
              reviewOutput,
              h(
                'h4',
                {},
                review.status === 'v12_modules_reviewed'
                  ? 'v12 module review passed'
                  : 'v12 module review blocked',
              ),
              h('p', {}, `Expected enabled: ${review.expected_enabled.join(', ') || 'none'}.`),
              h('p', {}, `Saved configuration: ${review.configured_enabled.join(', ') || 'none'}.`),
              h('p', {}, `Active in Foundry: ${review.runtime_active.join(', ') || 'none'}.`),
              ...review.blockers.map((blocker) => h('p', { class: 'error-text' }, blocker)),
              review.locked_changes.length
                ? h(
                    'p',
                    { class: 'error-text' },
                    `Unlock these packages in the isolated installation before changing releases: ${review.locked_changes.join(', ')}.`,
                  )
                : null,
              review.status === 'v12_modules_reviewed'
                ? h(
                    'details',
                    {},
                    h('summary', {}, 'Selected releases to install for the target build'),
                    h(
                      'ul',
                      {},
                      ...review.selected_releases.map((item) =>
                        h('li', {}, `${item.type} ${item.id} ${item.version} · ${item.manifest}`),
                      ),
                    ),
                  )
                : null,
              h('p', { class: 'backup-path' }, `Saved review: ${review.review_path}`),
              h(
                'p',
                { class: 'small-note' },
                review.status === 'v12_modules_reviewed'
                  ? `Keep the clone isolated. Install the selected releases, then launch Foundry ${review.target_build} with User Data Path ${review.clone_path} and open world ${review.world_id}. Foundry migrates that clone when it opens. Inspect the migrated world before importing its GM audit. Studio has not approved cutover.`
                  : 'Resolve the listed differences in the v12 clone, save and reload its module configuration, export a fresh inventory, and review again.',
              ),
            );
          } finally {
            reviewClone.disabled = false;
          }
        }),
    },
    'Review v12 clone modules',
  );
  const migratedInventoryInput = h('input', {
    type: 'file',
    accept: '.json,application/json',
    onchange: (event) =>
      attempt(async () => {
        const file = event.target.files?.[0];
        if (!file) return;
        migratedInventory = null;
        if (file.size > 2 * 1024 * 1024) throw new Error('The inventory must be under 2 MB.');
        migratedInventory = JSON.parse(await file.text());
        if (migratedInventory.phase !== 'migrated-clone')
          throw new Error('Choose the migrated-clone GM audit export.');
        render(
          auditOutput,
          `Imported ${migratedInventory.world?.title || 'world'} · Foundry ${migratedInventory.world?.coreVersion || '?'} · ${migratedInventory.enabledModuleIds?.length || 0} configured modules`,
        );
      }),
  });
  const auditMigration = h(
    'button',
    {
      class: 'primary',
      disabled: !hasWorld,
      onclick: () =>
        attempt(async () => {
          if (!migratedInventory)
            throw new Error('Import an inventory from the migrated clone first.');
          auditMigration.disabled = true;
          render(auditOutput, 'Comparing the migrated clone with the selected package plan…');
          try {
            const audit = await post('/api/foundry/upgrade/audit-migration', {
              review_path: auditForm.review,
              inventory: migratedInventory,
              confirmed_clone: auditForm.confirmed,
              manual_checks: {
                launch: auditForm.launch,
                scenes: auditForm.scenes,
                journals: auditForm.journals,
                actors_items: auditForm.actorsItems,
                modules: auditForm.modules,
              },
            });
            if (audit.status === 'reviewed') {
              cutoverForm.audit = audit.audit_path;
              cutoverAuditInput.querySelector('input').value = audit.audit_path;
            }
            render(
              auditOutput,
              h(
                'h4',
                {},
                audit.status === 'reviewed'
                  ? 'Migrated clone review recorded'
                  : 'Migrated clone review blocked',
              ),
              h('p', {}, `Foundry ${audit.reported_build} · target ${audit.target_build}.`),
              h(
                'p',
                {},
                `System ${audit.selected_system.id} ${audit.installed_system_version || 'missing'} · selected ${audit.selected_system.version}.`,
              ),
              h('h5', {}, 'Selected module and dependency releases'),
              h(
                'ul',
                {},
                ...Object.entries(audit.selected_modules)
                  .sort(([left], [right]) => left.localeCompare(right))
                  .map(([id, selected]) =>
                    h(
                      'li',
                      {},
                      `${id}: selected ${selected}; installed ${audit.installed_module_versions[id] || 'missing'}; saved ${audit.configured_enabled.includes(id) ? 'on' : 'off'}; active ${audit.runtime_active.includes(id) ? 'on' : 'off'}.`,
                    ),
                  ),
              ),
              ...audit.blockers.map((blocker) => h('p', { class: 'error-text' }, blocker)),
              h('p', { class: 'backup-path' }, `Saved audit: ${audit.audit_path}`),
              h(
                'p',
                { class: 'small-note' },
                'Package metadata and GM checks cannot certify module behavior. Keep the v12 backup and installer for rollback. Studio does not cut over the live world.',
              ),
            );
          } finally {
            auditMigration.disabled = false;
          }
        }),
    },
    'Audit migrated clone',
  );
  const reviewCutover = h(
    'button',
    {
      class: 'primary',
      disabled: !hasWorld,
      onclick: () =>
        attempt(async () => {
          reviewCutover.disabled = true;
          render(cutoverOutput, 'Rechecking the audit, clone, original User Data and backup…');
          try {
            const review = await post('/api/foundry/upgrade/review-cutover', {
              audit_path: cutoverForm.audit,
              confirmed_closed: cutoverForm.closed,
            });
            render(
              cutoverOutput,
              h('h4', {}, 'Ready for manual cutover'),
              h('p', {}, `Target Foundry ${review.target_build} · world ${review.world_id}.`),
              h('p', { class: 'backup-path' }, `Use this User Data path: ${review.clone_path}`),
              h(
                'p',
                { class: 'backup-path' },
                `Keep the original at: ${review.original_user_data}`,
              ),
              h('p', { class: 'backup-path' }, `Keep the verified backup: ${review.backup_path}`),
              h('p', {}, `Saved cutover review: ${review.review_path}`),
              h(
                'p',
                { class: 'small-note' },
                'Studio has not moved any data or changed Foundry settings. Point the target installation to the clone only after this check, and keep the original v12 installation and backup for rollback. Recheck here if either User Data folder changes before switching.',
              ),
            );
          } finally {
            reviewCutover.disabled = false;
          }
        }),
    },
    'Review cutover readiness',
  );
  return h(
    'section',
    { class: 'card upgrade-card' },
    h('div', { class: 'section-icon' }, icon('check')),
    h('h2', {}, 'Foundry upgrade compatibility report'),
    h(
      'p',
      { class: 'muted' },
      'In the original v12 world, run the GM inventory macro. Use a verified offline backup of that same world. This step reads package listings and leaves Foundry unchanged.',
    ),
    h(
      'a',
      { class: 'btn', href: fileUrl('DM/forge/foundry-upgrade-inventory.js'), download: '' },
      'Download GM inventory macro',
    ),
    h('label', {}, 'Import GM inventory JSON', input),
    status,
    formInput(form, 'backup', 'Verified backup folder', {
      help: 'Paste the timestamped backup folder from the backup step above.',
    }),
    h(
      'details',
      {},
      h('summary', {}, 'Explicit GM choices'),
      formInput(form, 'disabled', 'Enabled module IDs to disable in the clone', {
        help: 'Comma-separated IDs. Eligible modules are never dropped automatically.',
      }),
      formInput(form, 'approved', 'Required dependency IDs to enable in the clone', {
        help: 'Comma-separated IDs. Only approve dependencies you intend to activate in the isolated clone.',
      }),
    ),
    run,
    output,
    h('hr'),
    h('h3', {}, 'Prepare an isolated v12 clone'),
    h(
      'p',
      { class: 'muted' },
      'After opening and inspecting the restore test copy in Foundry v12, use its receipt and a reviewed report to make a separate clone. This step does not launch Foundry or change module settings.',
    ),
    reportInput,
    formInput(cloneForm, 'receipt', 'Restore test receipt', {
      help: 'Shown after Create restore test copy. Keep the receipt beside the backup.',
    }),
    cloneDestinationInput,
    formInput(cloneForm, 'inspected', 'I opened and inspected the restore copy in Foundry v12', {
      type: 'checkbox',
    }),
    formInput(cloneForm, 'reviewed', 'I reviewed the report and excluded module decisions', {
      type: 'checkbox',
    }),
    prepareClone,
    cloneOutput,
    h('hr'),
    h('h3', {}, 'Review v12 clone modules'),
    h(
      'p',
      { class: 'muted' },
      'After disabling excluded modules in Foundry v12, save and reload the clone, then run the GM inventory macro there again. This checks the saved and active module states against the plan without changing the clone.',
    ),
    reviewPlanInput,
    h('label', {}, 'Import fresh v12 clone inventory JSON', cloneInventoryInput),
    formInput(reviewForm, 'confirmed', 'I exported this inventory from the isolated v12 clone', {
      type: 'checkbox',
    }),
    reviewClone,
    reviewOutput,
    h('hr'),
    h('h3', {}, 'Audit migrated clone'),
    h(
      'p',
      { class: 'muted' },
      'After manually installing the selected releases and migrating only the isolated clone, run the migrated-clone GM macro in Foundry v13 or v14. Inspect the world before recording these checks.',
    ),
    h(
      'a',
      { class: 'btn', href: fileUrl('DM/forge/foundry-upgrade-audit.js'), download: '' },
      'Download migrated-clone audit macro',
    ),
    auditReviewInput,
    h('label', {}, 'Import migrated-clone inventory JSON', migratedInventoryInput),
    formInput(
      auditForm,
      'confirmed',
      'I exported this inventory from the isolated migrated clone',
      {
        type: 'checkbox',
      },
    ),
    formInput(auditForm, 'launch', 'The migrated clone launches and opens this world', {
      type: 'checkbox',
    }),
    formInput(auditForm, 'scenes', 'I inspected key scenes and map assets', {
      type: 'checkbox',
    }),
    formInput(auditForm, 'journals', 'I inspected key journals', { type: 'checkbox' }),
    formInput(auditForm, 'actorsItems', 'I inspected key actors and items', {
      type: 'checkbox',
    }),
    formInput(auditForm, 'modules', 'I tested retained module behavior', { type: 'checkbox' }),
    auditMigration,
    auditOutput,
    h('hr'),
    h('h3', {}, 'Review cutover readiness'),
    h(
      'p',
      { class: 'muted' },
      'After the migrated-clone audit passes, close both Foundry installations. Studio rechecks that the clone still matches the audit, the original User Data is unchanged since backup, and the verified backup remains intact.',
    ),
    cutoverAuditInput,
    formInput(cutoverForm, 'closed', 'I closed both Foundry installations', {
      type: 'checkbox',
    }),
    reviewCutover,
    cutoverOutput,
  );
}

function foundryWorldPicker(settings, context) {
  const state = { root: '' };
  const roots = h('div', { class: 'foundry-roots' });
  const choices = h('div', { class: 'world-choices', 'aria-live': 'polite' });
  const rootInput = formInput(state, 'root', 'Foundry User Data folder', {
    placeholder: '…/FoundryVTT',
    help: 'Choose the folder containing Data/worlds. Studio will list its worlds.',
  });
  const scan = async (path = state.root) => {
    render(choices, h('p', { class: 'muted' }, 'Scanning local Foundry worlds…'));
    try {
      const result = await context.api(
        '/api/foundry/worlds' + (path ? '?root=' + encodeURIComponent(path) : ''),
      );
      state.root = result.root;
      rootInput.querySelector('input').value = result.root;
      render(
        roots,
        ...result.roots.map((root) =>
          h('button', { class: 'world-root', onclick: () => scan(root) }, root),
        ),
      );
      render(
        choices,
        result.worlds.length
          ? result.worlds.map((world) => {
              const id = uid('world');
              return h(
                'label',
                { class: 'world-choice', for: id },
                h('input', {
                  id,
                  type: 'radio',
                  name: 'foundry-world',
                  checked: settings.world_path === world.path,
                  onchange: () => {
                    settings.world_path = world.path;
                  },
                }),
                h(
                  'span',
                  {},
                  h('b', {}, world.title),
                  h(
                    'small',
                    {},
                    `${world.system} · Foundry ${world.foundry_version || 'version unknown'}`,
                  ),
                  h('small', { class: 'world-path' }, world.path),
                ),
              );
            })
          : h('p', { class: 'muted' }, 'No Foundry worlds found in this User Data folder.'),
      );
    } catch (error) {
      render(choices, h('p', { class: 'error-text' }, error.message));
    }
  };
  const picker = h(
    'div',
    { class: 'foundry-world-picker' },
    rootInput,
    h('div', { class: 'row' }, h('button', { onclick: () => scan() }, 'Scan for worlds')),
    roots,
    choices,
    h(
      'details',
      {},
      h('summary', {}, 'Enter a world folder manually'),
      formInput(settings, 'world_path', 'Folder containing world.json'),
    ),
  );
  scan();
  return picker;
}

async function studioWelcome(_arg, context) {
  const result = await context.api('/api/settings');
  const settings = clone(result.settings);
  if (S.state.onboarding_needed && settings.campaign_name === 'Campaign Studio')
    settings.campaign_name = '';
  let mode = 'existing';
  const instructions = h('div');
  const modeButtons = h('div', { class: 'segmented' });
  const drawMode = () => {
    render(
      modeButtons,
      ...[
        ['existing', 'Connect an existing world'],
        ['new', 'Start a new Foundry world'],
      ].map(([value, label]) =>
        h(
          'button',
          {
            class: mode === value ? 'on' : '',
            onclick: () => {
              mode = value;
              drawMode();
            },
          },
          label,
        ),
      ),
    );
    render(
      instructions,
      mode === 'new'
        ? h(
            'p',
            { class: 'muted' },
            'Create the Foundry world in Foundry Setup and choose its game system there. Return here, scan its User Data folder, then select the new world.',
          )
        : h(
            'p',
            { class: 'muted' },
            'Select your existing Foundry world. This step only connects the Studio project; it does not change that world.',
          ),
    );
  };
  const finish = async (withoutWorld = false) => {
    if (!settings.campaign_name.trim()) throw new Error('Name your Studio project.');
    if (!withoutWorld && !settings.world_path) throw new Error('Select a Foundry world first.');
    if (withoutWorld) settings.world_path = '';
    await post('/api/settings', settings);
    S.state.campaign = settings.campaign_name.trim();
    S.state.onboarding_needed = false;
    if (!withoutWorld)
      // One action connects and imports; a world Studio cannot read yet is handled in the library.
      await post('/api/foundry/world/import').catch(() => null);
    initStudio();
    go(withoutWorld ? '#/' : '#/library');
  };
  drawMode();
  render(
    context.view,
    pageHead(
      'WELCOME TO CAMPAIGN STUDIO',
      'Set up your campaign.',
      'Create your Studio project and connect it to one Foundry world.',
    ),
    h(
      'div',
      { class: 'welcome-layout' },
      h(
        'section',
        { class: 'card' },
        h('h2', {}, '1. Name your Studio project'),
        formInput(settings, 'campaign_name', 'Campaign name'),
        h('p', { class: 'small-note' }, 'This Studio installation stores one campaign locally.'),
        h('h2', {}, '2. Connect a Foundry world'),
        modeButtons,
        instructions,
        foundryWorldPicker(settings, context),
        h(
          'div',
          { class: 'welcome-actions' },
          h(
            'button',
            { class: 'primary', onclick: () => attempt(() => finish()) },
            'Create project and import world',
          ),
          h(
            'button',
            { onclick: () => attempt(() => finish(true)) },
            'Continue without a Foundry world',
          ),
        ),
      ),
      h(
        'aside',
        { class: 'card' },
        h('h3', {}, 'What happens next'),
        h(
          'p',
          {},
          'Browse media in the selected world and import a read-only document snapshot from Foundry.',
        ),
        h(
          'p',
          {},
          'Your journals, NPCs, items and scenes remain in Foundry. Studio drafts and exports stay separate until you apply them as GM.',
        ),
      ),
    ),
  );
}
