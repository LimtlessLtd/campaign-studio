'use strict';

// The upgrade wizard's post-migration repair and readiness step, between the migrated-clone audit and
// cutover: a dry run of the clone repairs, the repair after a verified world backup, and the encounter
// smoke test. foundryUpgradeCard places `nodes` and passes each passing audit to `useAudit`.
function foundryRepairStep(hasWorld) {
  const form = { audit: '', closed: false, installation: '', reviewed: false };
  const smokeForm = { folder: '' };
  let scan = null;
  let summons = {};
  let macros = new Set();
  let scenes = new Set();
  const scanOutput = h('div', { class: 'upgrade-report', role: 'status' });
  const applyOutput = h('div', { class: 'upgrade-report', role: 'status' });
  const smokeChoices = h('div', { class: 'repair-smoke-choices' });
  const smokeOutput = h('div', { class: 'upgrade-report', role: 'status' });
  const auditInput = formInput(form, 'audit', 'Migrated-clone audit to check', {
    help: 'A passing audit fills this in automatically. You can paste an earlier audit path.',
  });
  const installationInput = formInput(form, 'installation', 'Foundry installation folder', {
    placeholder: 'The folder Foundry is installed in',
    help: 'The installation that opens the clone. Studio writes with the LevelDB library bundled in it, run by Node.js or by that installation itself.',
  });
  const count = (items, one, many = one + 's') => `${items} ${items === 1 ? one : many}`;
  const list = (items, line) => h('ul', {}, ...items.map((item) => h('li', {}, line(item))));
  const section = (title, items, line, open = false) =>
    items.length
      ? h('details', { open }, h('summary', {}, `${title} (${items.length})`), list(items, line))
      : null;

  const summonChoice = (finding) => {
    const id = uid('summon');
    return h(
      'div',
      { class: 'form-field' },
      h(
        'label',
        { for: id },
        `${finding.item} › ${finding.profile || finding.activity}: ${finding.uuid}`,
      ),
      h(
        'select',
        {
          id,
          onchange: (event) => {
            if (event.target.value) summons[finding.id] = event.target.value;
            else delete summons[finding.id];
          },
        },
        h('option', { value: '' }, 'Leave unchanged'),
        ...finding.candidates.map((actor) =>
          h('option', { value: actor.uuid }, `Summon ${actor.name}`),
        ),
      ),
      finding.candidates.length
        ? null
        : h('small', { class: 'muted' }, 'No world actor has a matching name.'),
    );
  };
  const toggle = (set, value, label, help) => {
    const state = { on: false };
    return formInput(state, 'on', label, {
      type: 'checkbox',
      help,
      change: () => (state.on ? set.add(value) : set.delete(value)),
    });
  };
  const macroChoice = (finding) =>
    h(
      'div',
      { class: 'repair-macro' },
      h('b', {}, finding.name),
      list(
        finding.uses,
        (use) =>
          `Line ${use.line}: ${use.field} → ${use.replacement}${use.rewrite ? '' : ' (the effect already has it; edit by hand)'}. ${use.text}`,
      ),
      finding.rewritable
        ? toggle(macros, finding.id, `Rewrite ${finding.name} to use name and img`)
        : null,
    );

  const showScan = () => {
    const found = scan.findings;
    const total =
      found.floors.length +
      found.source_ids.length +
      found.formulas.length +
      found.summons.length +
      found.macros.length;
    render(
      scanOutput,
      h('h4', {}, total ? 'Repairs found in the migrated clone' : 'Nothing to repair'),
      h(
        'p',
        {},
        `Scene floors ${scan.summary.floors_before} → ${scan.summary.floors_after} in ${count(scan.summary.scenes, 'scene')} · ${count(scan.summary.source_ids, 'relative item source ID')} · ${count(scan.summary.formulas, '"[object Object]" field')} · ${count(scan.summary.summons, 'summon link')} · ${count(scan.summary.macros, 'macro')}.`,
      ),
      section('Scene floors to merge', found.floors, (floor) => [
        `${floor.scene}: ${floor.before} → ${floor.after} floors, ${count(floor.documents_updated, 'document')} updated.`,
        list(floor.changes, (change) => change),
        ...floor.kept_back.map((reason) => h('p', { class: 'error-text' }, reason)),
      ]),
      section(
        'Relative source IDs to remove (they stop AC, HP and save DCs computing)',
        found.source_ids,
        (item) => item,
      ),
      section(
        '"[object Object]" fields to clear',
        found.formulas,
        (item) =>
          `${item.item}: ${item.field}${item.field === 'recharge' ? ' (set the recharge again in Foundry)' : ''}`,
      ),
      section(
        'Other "[object Object]" fields, not repaired: fix these in Foundry',
        found.other_fields,
        (item) => `${item.item}: ${item.field}`,
        true,
      ),
      found.summons.length
        ? h(
            'fieldset',
            {},
            h('legend', {}, 'Summon profiles that link to no actor'),
            ...found.summons.map(summonChoice),
          )
        : null,
      found.macros.length
        ? h(
            'fieldset',
            {},
            h('legend', {}, 'Macros that create effects with label or icon, removed in v14'),
            ...found.macros.map(macroChoice),
          )
        : null,
      h('p', { class: 'backup-path' }, `Saved dry run: ${scan.scan_path}`),
    );
    showSmokeChoices();
  };

  const showSmokeChoices = () => {
    const { folders, scenes: available } = scan.smoke_choices;
    smokeForm.folder = folders[0]?.id || '';
    scenes = new Set();
    render(
      smokeChoices,
      formInput(smokeForm, 'folder', 'Party folder', {
        options: folders.map((folder) => [
          folder.id,
          `${folder.name} (${count(folder.characters, 'character')})`,
        ]),
      }),
      h(
        'fieldset',
        { class: 'repair-scenes' },
        h('legend', {}, 'Scenes whose NPCs the party fights'),
        ...available.map((scene) =>
          toggle(scenes, scene.id, scene.name, count(scene.npcs, 'NPC token')),
        ),
      ),
    );
  };

  const runScan = h(
    'button',
    {
      class: 'primary',
      disabled: !hasWorld,
      onclick: () =>
        attempt(async () => {
          runScan.disabled = true;
          render(scanOutput, 'Reading the migrated clone’s scenes, actors, items and macros…');
          try {
            scan = await post('/api/foundry/upgrade/repair-scan', {
              audit_path: form.audit,
              confirmed_closed: form.closed,
            });
            summons = {};
            macros = new Set();
            showScan();
          } catch (error) {
            render(scanOutput, h('span', { class: 'error-text' }, error.message));
            throw error;
          } finally {
            runScan.disabled = false;
          }
        }),
    },
    'Dry run: find repairs',
  );

  const status = {
    repaired: 'Clone repaired and verified',
    nothing_to_repair: 'Nothing to repair',
    failed: 'Repair failed; nothing was written',
    restored: 'Repair undone: the world backup was restored',
    restore_failed: 'Repair failed and the restore did not finish',
  };
  const applyRepairs = h(
    'button',
    {
      class: 'primary',
      disabled: !hasWorld,
      onclick: () =>
        attempt(async () => {
          if (!scan) throw new Error('Run the dry run first.');
          applyRepairs.disabled = true;
          render(
            applyOutput,
            'Backing up the clone’s world, writing the repairs and verifying them…',
          );
          try {
            const result = await post('/api/foundry/upgrade/repair-apply', {
              scan_path: scan.scan_path,
              installation: form.installation,
              summons,
              macros: [...macros],
              confirmed_closed: form.closed,
              confirmed_reviewed: form.reviewed,
            });
            render(
              applyOutput,
              h('h4', {}, status[result.status] || result.status),
              result.error ? h('p', { class: 'error-text' }, result.error) : null,
              result.verification
                ? list(
                    result.verification.checks,
                    (check) =>
                      `${check.ok ? 'Passed' : 'Failed'}: ${check.name}. ${check.detail.join('; ')}`,
                  )
                : null,
              result.snapshot_path
                ? h('p', { class: 'backup-path' }, `World backup: ${result.snapshot_path}`)
                : null,
              h('p', { class: 'backup-path' }, `Saved repair record: ${result.repair_path}`),
              result.status === 'repaired'
                ? h(
                    'p',
                    { class: 'small-note' },
                    'Open the clone in Foundry and look at the repaired scenes and actors before cutover. Run the dry run again if you change the clone.',
                  )
                : null,
            );
          } catch (error) {
            render(applyOutput, h('span', { class: 'error-text' }, error.message));
            throw error;
          } finally {
            applyRepairs.disabled = false;
          }
        }),
    },
    'Back up the world and repair',
  );

  const encounterLine = (encounter) => [
    h('b', {}, encounter.scene),
    `: ${count(encounter.npcs, 'NPC')} tested, ${count(encounter.actions, 'action')}, ${encounter.failing_actions} failing. `,
    `Rough rounds to win against ${count(encounter.enemies, encounter.enemies_hostile_only ? 'hostile token' : 'NPC token')}: party ${encounter.rounds_for_party_to_win ?? 'never'} (${encounter.party_damage_per_round} damage a round), enemies ${encounter.rounds_for_enemies_to_win ?? 'never'} (${encounter.enemy_damage_per_round} a round).`,
  ];
  const runSmoke = h(
    'button',
    {
      disabled: !hasWorld,
      onclick: () =>
        attempt(async () => {
          if (!scan) throw new Error('Run the dry run first.');
          runSmoke.disabled = true;
          render(smokeOutput, 'Rolling every action of the party and the chosen scenes’ NPCs…');
          try {
            const report = await post('/api/foundry/upgrade/smoke-test', {
              scan_path: scan.scan_path,
              party_folder: smokeForm.folder,
              scene_ids: [...scenes],
            });
            render(
              smokeOutput,
              h(
                'h4',
                {},
                report.problems.length
                  ? `${count(report.problems.length, 'problem')} found`
                  : 'Every action rolled',
              ),
              h('p', { class: 'small-note' }, report.note),
              h('h5', {}, `Party: ${report.party_folder}`),
              list(
                report.party,
                (member) =>
                  `${member.name}: AC about ${member.ac}, HP ${member.hp}, ${count(member.actions, 'action')}, ${member.failures} failing${member.best.name ? `; best attack ${member.best.name} +${member.best.to_hit}, about ${member.best.average} damage` : ''}.`,
              ),
              h('h5', {}, 'Encounters'),
              list(report.encounters, encounterLine),
              report.problems.length ? h('h5', {}, 'Problems') : null,
              report.problems.length
                ? list(report.problems, (problem) => h('span', { class: 'error-text' }, problem))
                : null,
              h('p', { class: 'backup-path' }, `Saved smoke test: ${report.report_path}`),
            );
          } catch (error) {
            render(smokeOutput, h('span', { class: 'error-text' }, error.message));
            throw error;
          } finally {
            runSmoke.disabled = false;
          }
        }),
    },
    'Run encounter smoke test',
  );

  return {
    useAudit(path) {
      form.audit = path;
      auditInput.querySelector('input').value = path;
    },
    nodes: [
      h('hr'),
      h('h3', {}, 'Repair and check the migrated clone'),
      h(
        'p',
        { class: 'muted' },
        'After the audit passes, close both Foundry installations. The dry run looks for known v14 migration defects in the clone: duplicate Scene Levels floors, relative item source IDs that stop AC and HP computing, "[object Object]" fields, summon links to no actor, and macros using removed effect fields. It changes nothing.',
      ),
      auditInput,
      formInput(form, 'closed', 'Both Foundry installations are closed', { type: 'checkbox' }),
      runScan,
      scanOutput,
      h('h4', {}, 'Repair the clone'),
      h(
        'p',
        { class: 'muted' },
        'Studio copies the clone’s world folder beside the upgrade backup and checks every file, then writes the dry run’s repairs and your choices above. It verifies the result against that copy and restores the copy if any check fails.',
      ),
      installationInput,
      formInput(form, 'reviewed', 'I reviewed the dry run and my choices', { type: 'checkbox' }),
      applyRepairs,
      applyOutput,
      h('h4', {}, 'Encounter smoke test'),
      h(
        'p',
        { class: 'muted' },
        'Rolls every attack, save, heal, cast and summon of the party and of the NPCs on the chosen scenes, and checks that linked spells, summoned actors and consumed items exist. It reads the clone and changes nothing.',
      ),
      smokeChoices,
      runSmoke,
      smokeOutput,
    ],
  };
}
