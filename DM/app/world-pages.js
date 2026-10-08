'use strict';

/* The world map page: one uploaded image per world map, with pins that link to battle maps.
   Pin positions are fractions of the image (0-1), so they survive any display size. */
const WORLD_DOC = 'world-maps';

const clampUnit = (value) => Math.min(1, Math.max(0, value));
const unitAt = (event, box) => [
  clampUnit((event.clientX - box.left) / box.width),
  clampUnit((event.clientY - box.top) / box.height),
];

async function worldPage(arg, context) {
  const store = await context.doc(WORLD_DOC, blank('world_maps', {}));
  const battleMaps = (await context.doc('maps/index', { items: [] })).items;
  const current = store.maps.find((m) => m.id === arg) || store.maps[0];
  if (!current) return renderWorldEmpty(context.view, store);
  const state = (S.worldView = S.worldView?.id === current.id ? S.worldView : { id: current.id });
  const pinOf = () => current.pins.find((p) => p.id === state.pin);
  const stage = h('div', { class: 'map-stage' });
  const viewport = h('div', { class: 'map-viewport' }, stage);
  const inspector = h('div', { class: 'world-inspector' });

  const placePin = (x, y) => {
    if (state.placing === 'new') {
      const pin = blank('world_pin', { id: uid('pin'), x, y });
      current.pins.push(pin);
      state.pin = pin.id;
    } else if (state.placing === 'move' && pinOf()) {
      Object.assign(pinOf(), { x, y });
    } else return;
    state.placing = false;
    save(WORLD_DOC);
    draw();
  };

  const drawStage = () => {
    viewport.classList.toggle('placing', !!state.placing);
    render(
      stage,
      h('img', {
        src: fileUrl(current.image),
        alt: current.name + ' world map',
        draggable: 'false',
      }),
      current.pins.map((pin, i) =>
        h(
          'button',
          {
            class: 'map-pin' + (pin.id === state.pin ? ' active' : ''),
            style: `left:${pin.x * 100}%;top:${pin.y * 100}%`,
            title: pin.label,
            'aria-label': `Pin ${i + 1}: ${pin.label || 'unnamed'}`,
            onclick: (e) => {
              e.stopPropagation();
              state.pin = pin.id;
              state.placing = false;
              draw();
            },
          },
          i + 1,
        ),
      ),
    );
  };
  stage.addEventListener('click', (e) => {
    if (!state.placing || e.target.closest('.map-pin')) return;
    const [x, y] = unitAt(e, stage.getBoundingClientRect());
    placePin(x, y);
  });

  const drawInspector = () => {
    const pin = pinOf();
    const battle = pin && battleMaps.find((m) => m.slug === pin.map);
    render(
      inspector,
      h('h2', {}, pin ? 'Pin ' + (current.pins.indexOf(pin) + 1) : 'Pins'),
      pin
        ? [
            formInput(pin, 'label', 'Place name', { change: () => save(WORLD_DOC) }),
            formInput(pin, 'map', 'Battle map', {
              options: [['', 'None'], ...battleMaps.map((m) => [m.slug, m.name])],
              change: () => {
                save(WORLD_DOC);
                drawInspector();
              },
            }),
            formInput(pin, 'note', 'Notes', {
              type: 'textarea',
              rows: 3,
              change: () => save(WORLD_DOC),
            }),
            h(
              'div',
              { class: 'world-nudge', role: 'group', 'aria-label': 'Adjust pin position' },
              [
                ['Up', 0, -0.01],
                ['Left', -0.01, 0],
                ['Right', 0.01, 0],
                ['Down', 0, 0.01],
              ].map(([label, dx, dy]) =>
                h(
                  'button',
                  {
                    'aria-label': `Move pin ${label.toLowerCase()} one percent`,
                    onclick: () => {
                      pin.x = clampUnit(pin.x + dx);
                      pin.y = clampUnit(pin.y + dy);
                      save(WORLD_DOC);
                      drawStage();
                    },
                  },
                  label,
                ),
              ),
            ),
            h(
              'div',
              { class: 'row' },
              battle
                ? h('a', { class: 'btn primary', href: '#/maps/' + battle.slug }, 'Open battle map')
                : null,
              h(
                'button',
                {
                  onclick: () => {
                    state.placing = 'move';
                    draw();
                  },
                },
                'Move pin',
              ),
              h(
                'button',
                {
                  onclick: () => {
                    current.pins.splice(current.pins.indexOf(pin), 1);
                    state.pin = null;
                    save(WORLD_DOC);
                    draw();
                  },
                },
                'Remove pin',
              ),
            ),
          ]
        : h('p', { class: 'muted' }, 'Choose a pin on the map, or add one.'),
      state.placing
        ? h(
            'div',
            { class: 'row' },
            h('p', { class: 'muted', role: 'status' }, 'Tap the map to place the pin.'),
            h('button', { onclick: () => placePin(0.5, 0.5) }, 'Place at center'),
            h(
              'button',
              {
                onclick: () => {
                  state.placing = false;
                  draw();
                },
              },
              'Cancel placement',
            ),
          )
        : null,
    );
  };
  const draw = () => {
    drawStage();
    drawInspector();
  };
  draw();

  render(
    context.view,
    pageHead(
      'WORLD BUILDING',
      current.name,
      'Pin your battle maps to where they sit in the world.',
      h(
        'div',
        { class: 'row' },
        store.maps.length > 1
          ? h(
              'select',
              {
                'aria-label': 'World map',
                onchange: (e) => go('#/world/' + encodeURIComponent(e.target.value)),
              },
              store.maps.map((m) =>
                h('option', { value: m.id, selected: m.id === current.id }, m.name),
              ),
            )
          : null,
        h(
          'button',
          {
            class: 'primary',
            onclick: () => {
              state.placing = 'new';
              draw();
            },
          },
          icon('plus'),
          'Add pin',
        ),
        h('button', { onclick: () => renameWorldMapDialog(current) }, 'Rename map'),
        h('button', { onclick: () => newWorldMapDialog(store) }, 'New world map'),
      ),
    ),
    h('div', { class: 'world-layout' }, h('div', { class: 'studio-canvas' }, viewport), inspector),
  );
}

function renderWorldEmpty(view, store) {
  render(
    view,
    pageHead('WORLD BUILDING', 'World map', 'Show where each battle map sits in your world.'),
    h(
      'div',
      { class: 'empty-state' },
      icon('map'),
      h('h3', {}, 'No world map yet'),
      h('p', {}, 'Upload a picture of your world, then pin battle maps onto it.'),
      h('button', { class: 'primary', onclick: () => newWorldMapDialog(store) }, 'Add a world map'),
    ),
  );
}

function renameWorldMapDialog(map) {
  const form = { name: map.name };
  modal(
    'Rename world map',
    'Pins stay where they are; only the name changes.',
    h('div', {}, formInput(form, 'name', 'Name')),
    async () => {
      if (!form.name.trim()) throw new Error('Give the map a name.');
      map.name = form.name.trim();
      await flush(WORLD_DOC);
      route(true);
    },
    'Rename',
  );
}

function newWorldMapDialog(store) {
  const form = { name: '', image: '' };
  const status = h('p', { class: 'muted' }, 'No image chosen yet.');
  modal(
    'New world map',
    'Name it and upload the image. You can add more world maps later.',
    h(
      'div',
      {},
      formInput(form, 'name', 'Name'),
      imagePicker((path) => {
        form.image = path;
        status.textContent = 'Image uploaded.';
      }, 'Upload world map image'),
      status,
    ),
    async () => {
      if (!form.name.trim() || !form.image) throw new Error('Give the map a name and an image.');
      const map = blank('world_map', {
        id: uid('world'),
        name: form.name.trim(),
        image: form.image,
      });
      store.maps.push(map);
      await flush(WORLD_DOC);
      go('#/world/' + encodeURIComponent(map.id));
    },
    'Add world map',
  );
}
