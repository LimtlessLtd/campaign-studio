'use strict';

const $ = (s, el = document) => el.querySelector(s);
const root = $('#main'); // each route renders into a private view before replacing this element

function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  if (tag === 'button') el.type = 'button';
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
    else if (k === 'class') el.className = v;
    else if (k === 'value') el.value = v;
    else if (k === 'checked') el.checked = !!v;
    else el.setAttribute(k, v === true ? '' : v);
  }
  for (const kid of kids.flat(Infinity))
    if (kid != null && kid !== false) el.append(kid.nodeType ? kid : String(kid));
  return el;
}
const fileUrl = (p) => '/files/' + p.split('/').map(encodeURIComponent).join('/');
function render(el, ...children) {
  el.replaceChildren(
    ...children.flat(Infinity).filter((child) => child != null && child !== false),
  );
}
const slug = (s) =>
  s
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-|-$/g, '');
const uid = (p) => p + '-' + Date.now().toString(36) + Math.random().toString(36).slice(2, 5);
const when = (t) =>
  new Date(typeof t === 'number' && t < 1e12 ? t * 1000 : t).toLocaleString([], {
    dateStyle: 'short',
    timeStyle: 'short',
  });

function imagePicker(onUploaded, label = 'Upload image') {
  const input = h('input', {
    type: 'file',
    accept: 'image/png,image/jpeg,image/webp,image/gif',
    hidden: true,
    onchange: async (e) => {
      try {
        if (e.target.files[0]) onUploaded((await uploadImage(e.target.files[0])).path);
      } catch (err) {
        alert(err.message);
      }
      input.value = '';
    },
  });
  return h('span', {}, input, h('button', { onclick: () => input.click() }, label));
}

/* ---------- form helpers ---------- */
function field(docName, obj, key, opts = {}) {
  const { label, type = 'text', rows, placeholder, options, onchange } = opts;
  let el;
  const commit = (v) => {
    obj[key] = v;
    save(docName);
    onchange && onchange(v);
  };
  if (type === 'textarea')
    el = h('textarea', {
      rows,
      placeholder,
      value: obj[key] || '',
      oninput: (e) => commit(e.target.value),
    });
  else if (type === 'select')
    el = h(
      'select',
      { onchange: (e) => commit(e.target.value) },
      options.map((o) => h('option', { value: o, selected: o === obj[key] }, o || '—')),
    );
  else if (type === 'checkbox')
    el = h('input', { type, checked: obj[key], onchange: (e) => commit(e.target.checked) });
  else
    el = h('input', {
      type,
      placeholder,
      value: obj[key] || '',
      oninput: (e) => commit(e.target.value),
    });
  if (!label && opts.ariaLabel) el.setAttribute('aria-label', opts.ariaLabel);
  if (label) {
    el.id = uid('field');
    return h('div', { class: opts.class }, h('label', { for: el.id }, label), el);
  }
  return el;
}

function listEditor(docName, arr, { checklist = false, placeholder = 'Add…' } = {}) {
  const box = h('div');
  const draw = () => {
    render(
      box,
      ...arr.map((item, i) => {
        // Look the item up when it is used: an autosave merge may have moved it since this was drawn.
        let value = item;
        const at = () => (arr[i] === value ? i : arr.indexOf(value));
        const text = checklist ? item.text : item;
        const set = (v) => {
          if (checklist) item.text = v;
          else {
            const j = at();
            if (j < 0) arr.push(v);
            else arr[j] = v;
            value = v;
          }
          save(docName);
        };
        return h(
          'div',
          { class: 'check' + (checklist && item.done ? ' done' : '') },
          checklist &&
            h('input', {
              type: 'checkbox',
              checked: item.done,
              onchange: (e) => {
                item.done = e.target.checked;
                save(docName);
                draw();
              },
            }),
          h('input', { type: 'text', value: text, oninput: (e) => set(e.target.value) }),
          h(
            'button',
            {
              class: 'danger',
              title: 'Remove',
              onclick: () => {
                const j = at();
                if (j >= 0) arr.splice(j, 1);
                save(docName);
                draw();
              },
            },
            '×',
          ),
        );
      }),
      h('input', {
        type: 'text',
        placeholder,
        onkeydown: (e) => {
          if (e.key !== 'Enter' || !e.target.value.trim()) return;
          const text = e.target.value.trim();
          arr.push(checklist ? blank('checklist_item', { text }) : text);
          save(docName);
          draw();
          box.lastChild.focus();
        },
      }),
    );
  };
  draw();
  return box;
}

/* editable list of small objects (loot rows, events) */
function rowsEditor(docName, arr, cols, make) {
  const box = h('div');
  const draw = () =>
    render(
      box,
      ...arr.map((row) =>
        h(
          'div',
          { class: 'rowedit' },
          cols.map(([key, ph, w]) =>
            h('input', {
              type: 'text',
              placeholder: ph,
              value: row[key] || '',
              style: `flex:${w}`,
              oninput: (e) => {
                row[key] = e.target.value;
                save(docName);
              },
            }),
          ),
          h(
            'button',
            {
              class: 'danger',
              title: 'Remove',
              onclick: () => {
                const j = arr.indexOf(row);
                if (j >= 0) arr.splice(j, 1);
                save(docName);
                draw();
              },
            },
            '×',
          ),
        ),
      ),
      h(
        'button',
        {
          class: 'small',
          onclick: () => {
            arr.push(make());
            save(docName);
            draw();
          },
        },
        '+ add',
      ),
    );
  draw();
  return box;
}

function picker(docName, arr, choices, { placeholder = 'Add…', cls = '' } = {}) {
  const box = h('div', { class: 'pick' });
  const draw = () => {
    const input = h('input', { type: 'text', placeholder });
    const opts = h('div', { class: 'opts', hidden: true });
    const show = () => {
      const q = input.value.toLowerCase();
      const hits = choices()
        .filter((c) => !arr.includes(c.id) && c.name.toLowerCase().includes(q))
        .slice(0, 30);
      render(
        opts,
        ...hits.map((c) =>
          h(
            'button',
            {
              type: 'button',
              onmousedown: (e) => e.preventDefault(),
              onclick: () => {
                arr.push(c.id);
                save(docName);
                draw();
              },
            },
            c.name,
          ),
        ),
      );
      opts.hidden = !hits.length;
    };
    input.addEventListener('input', show);
    input.addEventListener('focus', show);
    input.addEventListener('blur', () => {
      opts.hidden = true;
    });
    const names = Object.fromEntries(choices().map((c) => [c.id, c.name]));
    render(
      box,
      h(
        'div',
        { class: 'row', style: 'margin-bottom:6px' },
        arr.map((id) =>
          h(
            'span',
            { class: 'chip ' + cls },
            names[id] || id,
            h(
              'button',
              {
                title: 'Remove',
                onclick: () => {
                  const j = arr.indexOf(id);
                  if (j >= 0) arr.splice(j, 1);
                  save(docName);
                  draw();
                },
              },
              '×',
            ),
          ),
        ),
      ),
      input,
      opts,
    );
  };
  draw();
  return box;
}

/* ---------- lookups ---------- */

function lightbox(path) {
  const lb = $('#lightbox');
  $('img', lb).src = fileUrl(path);
  $('p', lb).textContent = path;
  lb.hidden = false;
}
$('#lightbox').addEventListener('click', () => {
  $('#lightbox').hidden = true;
});

const ICONS = {
  map: '<path d="m3 6 6-3 6 3 6-3v15l-6 3-6-3-6 3zM9 3v15m6-12v15"/>',
  home: '<path d="m3 10 9-7 9 7v11H3zM9 21v-8h6v8"/>',
  book: '<path d="M4 3h13a3 3 0 0 1 3 3v15H6a2 2 0 0 1-2-2V3Zm0 14h16M8 7h8m-8 4h6"/>',
  people:
    '<circle cx="9" cy="8" r="3"/><path d="M3 21v-3a6 6 0 0 1 12 0v3m1-17a3 3 0 0 1 0 6m2 4a5 5 0 0 1 3 4v3"/>',
  threads:
    '<circle cx="5" cy="5" r="2"/><circle cx="19" cy="5" r="2"/><circle cx="12" cy="19" r="2"/><path d="M5 7v3a3 3 0 0 0 3 3h8a3 3 0 0 0 3-3V7m-7 6v4"/>',
  image:
    '<rect x="3" y="3" width="18" height="18" rx="3"/><circle cx="8" cy="8" r="1.5"/><path d="m3 17 5-5 4 4 4-6 5 7"/>',
  spark: '<path d="m12 3 2.4 6.6L21 12l-6.6 2.4L12 21l-2.4-6.6L3 12l6.6-2.4z"/>',
  settings:
    '<path d="M12 3v3m0 12v3M3 12h3m12 0h3M5.6 5.6l2.1 2.1m8.6 8.6 2.1 2.1m0-12.8-2.1 2.1m-8.6 8.6-2.1 2.1"/><circle cx="12" cy="12" r="5"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  arrow: '<path d="M4 12h16m-6-6 6 6-6 6"/>',
  check: '<path d="m5 12 4 4L19 6"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  globe:
    '<circle cx="12" cy="12" r="9"/><ellipse cx="12" cy="12" rx="4" ry="9"/><path d="M3 12h18"/>',
  folder: '<path d="M3 6h7l2 2h9v12H3z"/>',
  upload: '<path d="M12 16V3m-5 5 5-5 5 5M4 15v6h16v-6"/>',
};
function icon(name) {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('viewBox', '0 0 24 24');
  svg.setAttribute('fill', 'none');
  svg.setAttribute('stroke', 'currentColor');
  svg.setAttribute('stroke-width', '1.6');
  svg.setAttribute('stroke-linecap', 'round');
  svg.setAttribute('stroke-linejoin', 'round');
  svg.setAttribute('aria-hidden', 'true');
  svg.innerHTML = ICONS[name] || ICONS.book;
  return h('span', { class: 'icon' }, svg);
}

function toast(message, error = false) {
  let box = $('#toast');
  if (!box) {
    box = h('div', { id: 'toast', role: 'status' });
    document.body.append(box);
  }
  box.textContent = message;
  box.className = error ? 'error' : '';
  box.hidden = false;
  clearTimeout(S.toastTimer);
  S.toastTimer = setTimeout(() => (box.hidden = true), 6000);
}
async function attempt(action) {
  try {
    return await action();
  } catch (e) {
    if (e.name === 'AbortError') return;
    toast(e.message, true);
  }
}
async function flushAll() {
  for (const name of Object.keys(S.pending)) await flush(name);
}
function pageHead(kicker, title, description, actions = null) {
  return h(
    'div',
    { class: 'page-head' },
    h(
      'div',
      {},
      h('div', { class: 'eyebrow' }, kicker),
      h('h1', {}, title),
      h('p', { class: 'sub' }, description),
    ),
    actions,
  );
}
function badge(text, status = '') {
  return h('span', { class: 'badge ' + status }, text);
}
function formInput(obj, key, label, opts = {}) {
  const id = uid('input');
  const common = {
    id,
    value: obj[key] ?? '',
    placeholder: opts.placeholder,
    min: opts.min,
    max: opts.max,
    step: opts.step,
  };
  const update = (el) => {
    obj[key] = opts.type === 'number' || opts.type === 'range' ? +el.value : el.value;
    opts.change?.();
  };
  let el;
  if (opts.options)
    el = h(
      'select',
      { ...common, onchange: (e) => update(e.target) },
      opts.options.map((o) =>
        h(
          'option',
          {
            value: typeof o === 'string' ? o : o[0],
            selected: obj[key] === (typeof o === 'string' ? o : o[0]),
          },
          typeof o === 'string' ? o : o[1],
        ),
      ),
    );
  else if (opts.type === 'textarea')
    el = h('textarea', { ...common, rows: opts.rows || 4, oninput: (e) => update(e.target) });
  else if (opts.type === 'checkbox')
    return h(
      'label',
      { class: 'toggle-row', for: id },
      h('input', {
        id,
        type: 'checkbox',
        checked: obj[key],
        onchange: (e) => {
          obj[key] = e.target.checked;
          opts.change?.();
        },
      }),
      h('span', {}, h('b', {}, label), opts.help ? h('small', {}, opts.help) : null),
    );
  else el = h('input', { ...common, type: opts.type || 'text', oninput: (e) => update(e.target) });
  return h(
    'div',
    { class: 'form-field' },
    h('label', { for: id }, label),
    el,
    opts.help ? h('small', { class: 'muted' }, opts.help) : null,
  );
}
function modal(title, description, body, onSubmit, button = 'Save') {
  const dialog = h('dialog', { class: 'studio-dialog' });
  const submit = h('button', { class: 'primary', type: 'submit' }, button);
  const form = h(
    'form',
    {
      onsubmit: async (e) => {
        e.preventDefault();
        submit.disabled = true;
        try {
          await onSubmit();
          dialog.close();
          dialog.remove();
        } catch (err) {
          toast(err.message, true);
          submit.disabled = false;
        }
      },
    },
    h('div', { class: 'eyebrow' }, 'CAMPAIGN STUDIO'),
    h('h2', {}, title),
    h('p', { class: 'muted' }, description),
    body,
    h(
      'div',
      { class: 'dialog-actions' },
      h(
        'button',
        {
          type: 'button',
          onclick: () => {
            dialog.close();
            dialog.remove();
          },
        },
        'Cancel',
      ),
      submit,
    ),
  );
  dialog.append(form);
  document.body.append(dialog);
  dialog.showModal();
  dialog.querySelector('input,textarea,select')?.focus();
  dialog.addEventListener('cancel', () => dialog.remove());
  return dialog;
}
