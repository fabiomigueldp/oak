/* Aviport editing uses the same authenticated, revision-checked job flow as Oak. */
export async function renderAviary(ctx) {
  const { el, button, field, input, select, heading, api, operation, state, can, openMap } = ctx;
  const generation = state.generation;
  let data = await api('/aviary');
  if (generation !== state.generation) return;
  const root = document.querySelector('#view');
  const live = el('p', 'muted');
  const flights = el('div', 'aviary-flights'); flights.lang = 'en';
  const notice = el('p', 'notice'); notice.setAttribute('role', 'status'); notice.hidden = true;
  root.replaceChildren(heading('Aviary'), live, flights, notice);
  if (!data.available) { live.textContent = data.message; return; }
  const layout = el('div', 'aviary-layout'); layout.lang = 'en';
  const ports = el('div', 'aviary-port-list');
  const query = input('', 'search'); query.placeholder = 'Find aviport'; query.setAttribute('aria-label', 'Find aviport');
  const sidebar = el('section', 'aviary-ports', query, ports);
  const editor = el('section', 'aviary-editor');
  layout.append(sidebar, editor); root.append(layout);
  let selected = null, draft = null, revision = 0, baseline = '', pending = null;
  let listSignature = '';
  let syncForm = () => {};
  const editable = can('aviary_edit');
  const coordinates = p => [p.x, p.y, p.z].map(Math.round).join(', ');
  function report(message) { notice.textContent = message; notice.hidden = !message; }
  function list() {
    const matches = data.ports.filter(p => `${p.name} ${p.id}`.toLowerCase().includes(query.value.toLowerCase()));
    const signature = JSON.stringify([selected, matches.map(p => [p.id,p.name,p.x,p.y,p.z,p.busy,p.shared])]);
    if (signature !== listSignature) {
      const focused = ports.contains(document.activeElement) ? document.activeElement.dataset.port : null;
      listSignature = signature;
      ports.replaceChildren(...matches.map(p => {
      const b = button('', () => choose(p.id), 'aviary-port');
      b.dataset.port = p.id;
      b.replaceChildren(el('strong', '', p.name), el('span', 'muted', coordinates(p)), el('small', p.busy ? 'aviary-busy' : 'muted', p.busy ? 'In use' : p.shared ? 'Shared' : 'Private'));
      b.setAttribute('aria-pressed', String(selected === p.id)); return b;
      }));
      if (!matches.length) ports.append(el('p', 'muted', data.ports.length ? 'No matching aviports.' : 'Create an aviport in the game with /aviary claim home.'));
      if (focused) [...ports.querySelectorAll('button')].find(b => b.dataset.port === focused)?.focus({ preventScroll: true });
    }
    live.textContent = data.error || (!data.enabled ? 'Travel disabled' : `${data.ports.length} aviports · ${data.flights.length} of ${data.maxFlights} birds in flight`);
    const phases = { prepare:'Preparing', call:'Approaching', greet:'Boarding', board:'Boarding', depart:'Taking off', flight:'Flying', 'fade-out':'Departing', transfer:'In transit', 'arrival-load':'In transit', 'fade-in':'Approaching', arrive:'Landing', settle:'Landed' };
    const portName = id => data.ports.find(p => p.id === id)?.name || id;
    flights.replaceChildren(...data.flights.map(f => el('div', 'aviary-flight', el('strong', '', `${portName(f.origin)} → ${portName(f.destination)}`), el('span', 'muted', `${phases[f.phase] || 'In flight'} · ${f.seconds} s`))));
  }
  const values = p => ({ name: p.name, shared: p.shared, departureYaw: p.departureYaw ?? null, arrivalYaw: p.arrivalYaw ?? null });
  function choose(id, saved = null) {
    if (pending) return;
    if (state.dirty && selected !== id) { report('Save or discard your changes before selecting another aviport.'); return; }
    const p = data.ports.find(p => p.id === id); if (!p) return;
    selected = id; draft = saved?.values || values(p); baseline = JSON.stringify(values(p));
    revision = JSON.stringify(draft) === baseline ? data.revision : saved?.revision ?? data.revision;
    const form = el('form', 'aviary-form');
    const title = el('h2', '', p.name);
    const name = input(draft.name); name.required = true; name.maxLength = 48;
    const access = select({ shared: 'Shared', private: 'Private' }, draft.shared ? 'shared' : 'private');
    const stateLabel = el('p', 'muted'); stateLabel.setAttribute('role', 'status');
    const checkLabel = el('p', 'muted'); checkLabel.setAttribute('role', 'status');
    const save = el('button', 'button primary', 'Apply'); save.type = 'submit';
    const discard = button('Discard', () => { state.dirty = false; state.saveDraft('aviary', null); report(''); choose(id); }, 'button ghost');
    const controls = [name, access];
    const directionControls = [];
    const angles = el('div', 'aviary-directions');
    for (const [key, label] of [['departureYaw', 'Departure'], ['arrivalYaw', 'Arrival']]) {
      const mode = select({ auto: 'Automatic', custom: 'Preferred heading' }, draft[key] == null ? 'auto' : 'custom');
      const degrees = input(draft[key] ?? 0, 'number'); Object.assign(degrees, { min: -180, max: 180, step: 'any', required: true });
      const compass = el('div', 'aviary-compass', el('span', '', 'N'));
      const arrow = el('span', 'aviary-direction-arrow', '↑'); compass.append(arrow); compass.setAttribute('aria-hidden', 'true');
      const syncDirection = () => {
        degrees.disabled = mode.value === 'auto' || !editable || !!pending;
        arrow.style.transform = `rotate(${180 + Number(degrees.value)}deg)`;
        arrow.hidden = mode.value === 'auto';
      };
      mode.addEventListener('change', () => { draft[key] = mode.value === 'auto' ? null : Number(degrees.value); syncDirection(); update(); });
      degrees.addEventListener('input', () => { draft[key] = Number(degrees.value); syncDirection(); update(); });
      controls.push(mode); syncDirection();
      directionControls.push(syncDirection);
      angles.append(el('div', 'aviary-direction', el('h3', '', label), compass, field('Approach', mode), field('Degrees', degrees)));
    }
    name.addEventListener('input', () => { draft.name = name.value; update(); });
    access.addEventListener('change', () => { draft.shared = access.value === 'shared'; update(); });
    function update() {
      state.dirty = JSON.stringify(draft) !== baseline;
      state.saveDraft('aviary', state.dirty ? { id, values: draft, revision } : null);
      syncForm();
    }
    syncForm = () => {
      const current = data.ports.find(p => p.id === id);
      const stale = data.revision !== revision;
      save.disabled = !editable || !!pending || !state.dirty || !current || !!current.busy || stale || !!data.error;
      discard.disabled = !!pending || (!state.dirty && !stale);
      for (const control of controls) control.disabled = !editable || !!pending;
      for (const sync of directionControls) sync();
      stateLabel.textContent = pending ? 'Applying…' : !current ? 'This aviport was removed.' : stale ? 'Aviports changed. Discard to load the current settings.' : current.busy ? 'A flight is using this aviport.' : state.dirty ? 'Changes not applied' : 'Current server settings';
      checkLabel.textContent = current?.check ? `${current.check.message} · ${new Date(current.check.checked_at * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}` : 'Landing area not checked in this session.';
    };
    const check = button('Check landing area', async () => { data = await api('/aviary/check', { port: id }); if (generation !== state.generation) return; syncForm(); list(); });
    check.disabled = !editable;
    form.addEventListener('submit', async event => {
      event.preventDefault(); if (!form.reportValidity() || save.disabled) return;
      try {
        await operation('aviary_edit', { action: 'edit', port: id, revision, ...draft }, { onQueued(job) { pending = job.id; syncForm(); } });
      } catch (error) { report(error.message); }
    });
    form.append(title, el('div', 'aviary-location', el('span', 'muted', coordinates(p)), button('View on map', () => openMap(p), 'button ghost small')),
      el('div', 'aviary-fields', field('Name', name), field('Access', access)), angles,
      el('p', 'muted', '0° south · 90° west · ±180° north · −90° east. A blocked heading uses a safe alternative.'),
      el('div', 'aviary-check', checkLabel, check), el('div', 'form-actions', stateLabel, discard, save));
    editor.replaceChildren(form); update(); list();
  }
  query.addEventListener('input', list);
  list();
  const saved = state.getDraft('aviary');
  if (data.ports.length) choose(data.ports.some(p => p.id === saved?.id) ? saved.id : data.ports[0].id, saved && data.ports.some(p => p.id === saved.id) ? saved : null);
  async function refresh() {
    if (generation !== state.generation) return;
    try {
      if (!document.hidden) {
        const next = await api('/aviary'); if (generation !== state.generation) return;
        if (!next.available) { report(next.message); return; }
        data = next;
        if (pending) {
          const job = await api(`/jobs/${pending}`); if (generation !== state.generation) return;
          if (['completed', 'failed', 'cancelled', 'interrupted'].includes(job.state)) {
            pending = null;
            if (job.state === 'completed') {
              const applied = await api('/aviary'); if (generation !== state.generation) return;
              if (!applied.available) { report('Operation completed. Reconnect to read the current settings.'); return; }
              data = applied; state.dirty = false; state.saveDraft('aviary', null); choose(selected); report('Aviport saved.');
            }
            else report('Not applied. Open the operation for details.');
          }
        }
        if (!state.dirty && !pending && data.ports.length && (data.revision !== revision || !selected)) choose(data.ports.some(p => p.id === selected) ? selected : data.ports[0].id);
        list(); syncForm();
      }
    } catch (error) { if (generation === state.generation) report(error.message); }
    finally { if (generation === state.generation) setTimeout(refresh, 5000); }
  }
  setTimeout(refresh, 5000);
}
