/* Aviary uses authenticated jobs and one revision for its network and destinations. */
const NETWORK = '@network';
const COLORS = { white:'Branco', orange:'Laranja', magenta:'Magenta', light_blue:'Azul-claro', yellow:'Amarelo', lime:'Verde-lima', pink:'Rosa', gray:'Cinza', light_gray:'Cinza-claro', cyan:'Ciano', purple:'Roxo', blue:'Azul', brown:'Marrom', green:'Verde', red:'Vermelho', black:'Preto' };

export function aviaryValues(data, id) {
  if (id === NETWORK) return data.network ? {
    fieldPickup: data.network.fieldPickup, discoverPublic: data.network.discoverPublic,
    maxOwnedPerches: data.network.maxOwnedPerches,
    ...(Number.isFinite(data.shortcutDistance) ? { maxFlights:data.maxFlights, shortcutDistance:data.shortcutDistance } : {}),
  } : null;
  const p = data.ports.find(port => port.id === id);
  if (!p) return null;
  return { name:p.name, shared:p.shared, departureYaw:p.departureYaw ?? null, arrivalYaw:p.arrivalYaw ?? null,
    ...(p.perch ? { color:p.perch.color, style:p.perch.style, birdName:p.perch.birdName, hub:p.perch.hub } : {}) };
}

export function aviaryAnchorLabel(port) {
  if (port.kind !== 'perch' && !port.perch) return 'Destino existente';
  return { active:'Poleiro instalado', inactive:'Em mudança', missing:'Suporte ausente', unloaded:'Área não carregada' }[port.anchorStatus]
    || (port.active === false ? 'Inativo' : 'Poleiro');
}

export function aviaryEditState({ data, id, revision, dirty, pending, editable }) {
  const current = id === NETWORK ? data.network : data.ports.find(p => p.id === id);
  const stale = data.revision !== revision;
  const busy = id !== NETWORK && current?.busy;
  return {
    canSave: !!editable && !pending && dirty && !!current && !busy && !stale && !data.error,
    canDiscard: !pending && (dirty || stale || !current),
    message: pending ? 'Aplicando…' : data.error ? 'Não foi possível confirmar as configurações do servidor.' : !current ? 'Este destino foi removido.' : stale ? 'As configurações mudaram. Descarte para carregar a versão atual.' : busy ? 'Este destino está em uso por uma viagem.' : dirty ? 'Alterações não aplicadas' : 'Configurações vigentes',
  };
}

export async function renderAviary(ctx) {
  const { el, button, field, input, select, heading, api, operation, state, can, openMap } = ctx;
  const generation = state.generation;
  let data = await api('/aviary');
  if (generation !== state.generation) return;
  const root = document.querySelector('#view');
  const live = el('p', 'muted');
  const flights = el('div', 'aviary-flights');
  const notice = el('p', 'notice'); notice.setAttribute('role', 'status'); notice.hidden = true;
  root.replaceChildren(heading('Aviary'), live, flights, notice);
  if (!data.available) { live.textContent = data.message; return; }
  const layout = el('div', 'aviary-layout');
  const ports = el('div', 'aviary-port-list');
  const query = input('', 'search'); query.placeholder = 'Buscar destino'; query.setAttribute('aria-label', 'Buscar destino');
  const network = button('Configurações da rede', () => choose(NETWORK), 'aviary-network');
  network.hidden = !data.network;
  const sidebar = el('section', 'aviary-ports', network, query, ports);
  sidebar.setAttribute('aria-label', 'Destinos');
  const editor = el('section', 'aviary-editor'); editor.setAttribute('aria-label', 'Configurações');
  layout.append(sidebar, editor); root.append(layout);
  let selected = null, draft = null, revision = data.revision, baseline = '', pending = null, submitting = false;
  let listSignature = '', flightSignature = '';
  let syncForm = () => {};
  const editable = can('aviary_edit');
  const coordinates = p => [p.x, p.y, p.z].map(Math.round).join(', ');
  const current = id => id === NETWORK ? data.network : data.ports.find(p => p.id === id);
  function report(message) { notice.textContent = message; notice.hidden = !message; }
  function list() {
    const matches = data.ports.filter(p => `${p.name} ${p.id} ${p.ownerName || ''}`.toLocaleLowerCase('pt-BR').includes(query.value.toLocaleLowerCase('pt-BR')));
    const signature = JSON.stringify([selected, matches.map(p => [p.id,p.name,p.x,p.y,p.z,p.busy,p.shared,p.anchorStatus,p.kind,p.active])]);
    network.hidden = !data.network;
    network.setAttribute('aria-pressed', String(selected === NETWORK));
    if (signature !== listSignature) {
      const focused = ports.contains(document.activeElement) ? document.activeElement.dataset.port : null;
      listSignature = signature;
      ports.replaceChildren(...matches.map(p => {
        const b = button('', () => choose(p.id), 'aviary-port'); b.dataset.port = p.id;
        b.replaceChildren(el('strong', '', p.name), el('span', 'muted', coordinates(p)),
          el('small', p.busy ? 'aviary-busy' : 'muted', p.busy ? 'Em uso' : `${aviaryAnchorLabel(p)} · ${p.shared ? 'Público' : 'Privado'}`));
        b.setAttribute('aria-pressed', String(selected === p.id)); return b;
      }));
      if (!matches.length) ports.append(el('p', 'muted', data.ports.length ? 'Nenhum destino encontrado.' : 'Coloque um Perch no jogo para criar o primeiro destino.'));
      if (focused) [...ports.querySelectorAll('button')].find(b => b.dataset.port === focused)?.focus({ preventScroll:true });
    }
    live.textContent = data.error || (!data.enabled ? 'Viagens desativadas' : `${data.ports.length} destinos · ${data.flights.length} de ${data.maxFlights} aves em viagem${data.waitingCalls > 0 ? ` · ${data.waitingCalls} na fila` : ''}`);
    const phases = { prepare:'Preparando', call:'Aproximação', greet:'Aguardando embarque', waiting:'Aguardando embarque', board:'Embarque', depart:'Decolagem', flight:'Voo', 'fade-out':'Partida', transfer:'Em trânsito', 'arrival-load':'Em trânsito', 'fade-in':'Aproximação', arrive:'Pouso', settle:'Desembarque', farewell:'Despedida' };
    const portName = id => data.ports.find(p => p.id === id)?.name || (String(id || '').startsWith('field') ? 'Chamada em campo' : id);
    const nextFlights = JSON.stringify(data.flights);
    if (nextFlights !== flightSignature) {
      flightSignature = nextFlights;
      flights.replaceChildren(...data.flights.map(f => el('div', 'aviary-flight', el('strong', '', `${portName(f.origin)} → ${portName(f.destination)}`), el('span', 'muted', `${phases[f.phase] || 'Em viagem'} · ${f.seconds} s`))));
    }
  }

  function choose(id, saved = null) {
    if (pending || submitting) return;
    if (state.dirty) { if (selected !== id) report('Aplique ou descarte as alterações antes de mudar de destino.'); return; }
    const p = current(id); if (!p) return;
    const networkMode = id === NETWORK;
    const values = aviaryValues(data, id);
    selected = id;
    // Saved drafts may predate a physical anchor; keep only the current form contract.
    draft = Object.fromEntries(Object.entries(values).map(([key, value]) => [key, saved?.values && Object.hasOwn(saved.values, key) ? saved.values[key] : value]));
    baseline = JSON.stringify(values);
    revision = JSON.stringify(draft) === baseline ? data.revision : saved?.revision ?? data.revision;
    const form = el('form', 'aviary-form');
    const save = el('button', 'button primary', 'Aplicar'); save.type = 'submit';
    const discard = button('Descartar', () => {
      state.dirty = false; state.saveDraft('aviary', null); report('');
      choose(current(id) ? id : data.network ? NETWORK : data.ports[0]?.id);
    }, 'button ghost');
    const stateLabel = el('p', 'muted'); stateLabel.setAttribute('role', 'status');
    const controls = [], syncControls = [];
    function update() {
      state.dirty = JSON.stringify(draft) !== baseline;
      state.saveDraft('aviary', state.dirty ? { id, values:draft, revision } : null);
      syncForm();
    }
    function bind(node, key, event = 'change', transform = value => value) {
      controls.push(node);
      node.addEventListener(event, () => { draft[key] = transform(node.value); update(); });
      return node;
    }
    function textInput(key, maximum) {
      const node = input(draft[key]); node.required = true; node.maxLength = maximum;
      return bind(node, key, 'input');
    }
    form.append(el('h2', '', networkMode ? 'Rede de poleiros' : p.name));
    if (networkMode) {
      const discovery = bind(select({ visited:'Após visitar', all:'Visíveis para todos' }, draft.discoverPublic ? 'visited' : 'all'), 'discoverPublic', 'change', value => value === 'visited');
      const pickup = bind(select({ enabled:'Permitidas', disabled:'Desativadas' }, draft.fieldPickup ? 'enabled' : 'disabled'), 'fieldPickup', 'change', value => value === 'enabled');
      const limit = input(draft.maxOwnedPerches, 'number'); Object.assign(limit, { min:1, max:16, step:1, required:true });
      bind(limit, 'maxOwnedPerches', 'input', Number);
      form.append(el('div', 'aviary-fields', field('Destinos públicos', discovery), field('Poleiros por jogador', limit)),
        el('p', 'muted', 'Destinos privados exigem convite. Pontos públicos principais ficam disponíveis sem visita.'),
        field('Chamadas em campo aberto', pickup),
        el('p', 'muted', 'Permite chamar uma ave pelo apito fora de um poleiro. O embarque exige terreno seguro; o destino continua sendo um poleiro.'),
        el('p', 'muted', 'Novos limites valem para a criação de poleiros. Destinos existentes são preservados.'));
      if (Number.isFinite(draft.shortcutDistance)) {
        const limits = el('details', 'aviary-details');
        const flightsLimit = input(draft.maxFlights, 'number'); Object.assign(flightsLimit, { min:1, max:4, step:1, required:true });
        const distance = input(draft.shortcutDistance, 'number'); Object.assign(distance, { min:100, max:500, step:'any', required:true });
        limits.append(el('summary', '', 'Limites de viagem'), el('div', 'aviary-fields',
          field('Viagens simultâneas', bind(flightsLimit, 'maxFlights', 'input', Number)),
          field('Transição após (blocos)', bind(distance, 'shortcutDistance', 'input', Number))),
          el('p', 'muted', 'Acima dessa distância, o voo usa uma transição até perto do destino. Alterações valem para as próximas viagens.'));
        form.append(limits);
      }
    } else {
      const anchorLabel = el('span', 'muted', aviaryAnchorLabel(p));
      syncControls.push(() => { const port = current(id); if (port) anchorLabel.textContent = aviaryAnchorLabel(port); });
      form.append(el('div', 'aviary-location', el('div', 'aviary-location-copy', el('span', 'muted', coordinates(p)), anchorLabel), button('Ver no mapa', () => openMap(current(id) || p), 'button ghost small')));
      const access = bind(select({ shared:'Público', private:'Privado' }, draft.shared ? 'shared' : 'private'), 'shared', 'change', value => value === 'shared');
      form.append(el('div', 'aviary-fields', field('Nome', textInput('name', 48)), field('Acesso', access)));
      if (p.perch) {
        const hub = bind(select({ discovered:'Seguir a regra da rede', hub:'Disponível sem visita' }, draft.hub ? 'hub' : 'discovered'), 'hub', 'change', value => value === 'hub');
        syncControls.push(() => { hub.disabled = !editable || !!pending || submitting || !draft.shared; });
        form.append(field('Descoberta do destino público', hub));
        const appearance = el('details', 'aviary-details');
        appearance.append(el('summary', '', 'Aparência'), el('div', 'aviary-fields',
          field('Madeira', bind(select({ oak:'Carvalho', spruce:'Pinheiro', birch:'Bétula' }, draft.style), 'style')),
          field('Tecido', bind(select(COLORS, draft.color), 'color'))), field('Nome da ave', textInput('birdName', 32)));
        form.append(appearance);
      }
      const ownership = el('details', 'aviary-details');
      const guests = p.perch?.guests || [];
      ownership.append(el('summary', '', 'Proprietário e convidados'), el('p', 'aviary-identity', p.ownerName || p.owner || 'Servidor'));
      if (guests.length) {
        const names = p.guestNames || {};
        ownership.append(el('ul', 'aviary-guests', ...guests.map(uuid => el('li', 'aviary-identity', names[uuid] || uuid))));
      } else ownership.append(el('p', 'muted', 'Nenhum convite individual.'));
      if (p.perch) ownership.append(el('p', 'muted', 'O proprietário gerencia os convites pelo poleiro no jogo.'));
      form.append(ownership);
      const angles = el('div', 'aviary-directions');
      for (const [key, label] of [['departureYaw', 'Saída'], ['arrivalYaw', 'Chegada']]) {
        const mode = select({ auto:'Automática', custom:'Direção preferida' }, draft[key] == null ? 'auto' : 'custom');
        const degrees = input(draft[key] ?? 0, 'number'); Object.assign(degrees, { min:-180, max:180, step:'any', required:true });
        const compass = el('div', 'aviary-compass', el('span', '', 'N'));
        const arrow = el('span', 'aviary-direction-arrow', '↑'); compass.append(arrow); compass.setAttribute('aria-hidden', 'true');
        const syncDirection = () => {
          degrees.disabled = mode.value === 'auto' || !editable || !!pending || submitting;
          arrow.style.transform = `rotate(${180 + Number(degrees.value)}deg)`;
          arrow.hidden = mode.value === 'auto';
        };
        mode.addEventListener('change', () => { draft[key] = mode.value === 'auto' ? null : Number(degrees.value); update(); });
        degrees.addEventListener('input', () => { draft[key] = Number(degrees.value); update(); });
        controls.push(mode); syncControls.push(syncDirection);
        angles.append(el('div', 'aviary-direction', el('h3', '', label), compass, field('Direção', mode), field('Graus', degrees)));
      }
      const directionDetails = el('details', 'aviary-details');
      directionDetails.append(el('summary', '', 'Aproximação e saída'), angles,
        el('p', 'muted', '0° sul · 90° oeste · ±180° norte · −90° leste. Uma direção bloqueada usa uma alternativa segura.'));
      form.append(directionDetails);
      const checkLabel = el('p', 'muted'); checkLabel.setAttribute('role', 'status');
      const check = button('Verificar pouso', async () => {
        data = await api('/aviary/check', { port:id }); if (generation !== state.generation) return;
        list(); syncForm();
      });
      controls.push(check);
      syncControls.push(() => {
        const checkResult = current(id)?.check;
        check.disabled = !editable || !!pending || submitting || !current(id);
        checkLabel.textContent = checkResult ? `${checkResult.clear ? 'Área de pouso livre' : checkResult.message} · ${new Date(checkResult.checked_at * 1000).toLocaleTimeString('pt-BR', { hour:'2-digit', minute:'2-digit' })}` : 'Área de pouso ainda não verificada.';
      });
      form.append(el('div', 'aviary-check', checkLabel, check));
    }
    syncForm = () => {
      const result = aviaryEditState({ data, id, revision, dirty:state.dirty, pending:pending || submitting, editable });
      save.disabled = !result.canSave; discard.disabled = !result.canDiscard;
      for (const control of controls) control.disabled = !editable || !!pending || submitting;
      for (const sync of syncControls) sync();
      stateLabel.textContent = result.message;
    };
    form.addEventListener('submit', async event => {
      event.preventDefault(); if (!form.reportValidity() || save.disabled) return;
      submitting = true; syncForm();
      try {
        await operation('aviary_edit', { action:networkMode ? 'policy' : 'edit', ...(networkMode ? {} : { port:id }), revision, ...draft }, {
          onQueued(job) { if (generation !== state.generation) return; pending = job.id; syncForm(); },
        });
      } catch (error) { if (generation === state.generation) report(error.message); }
      finally { submitting = false; if (generation === state.generation) syncForm(); }
    });
    form.append(el('div', 'form-actions', stateLabel, discard, save));
    editor.replaceChildren(form); update(); list();
  }
  query.addEventListener('input', list);
  list();
  const saved = state.getDraft('aviary');
  const initial = saved && current(saved.id) ? saved.id : data.ports[0]?.id || (data.network ? NETWORK : null);
  if (initial) choose(initial, saved?.id === initial ? saved : null);
  async function refresh() {
    if (generation !== state.generation) return;
    try {
      if (!document.hidden) {
        const next = await api('/aviary'); if (generation !== state.generation) return;
        if (!next.available) { data = { ...data, error:next.message || 'Aviary indisponível.' }; report(data.error); list(); syncForm(); return; }
        data = next;
        if (pending) {
          const job = await api(`/jobs/${pending}`); if (generation !== state.generation) return;
          if (['completed', 'failed', 'cancelled', 'interrupted'].includes(job.state)) {
            pending = null;
            if (job.state === 'completed') {
              const applied = await api('/aviary'); if (generation !== state.generation) return;
              if (!applied.available) { data = { ...data, error:'Configurações indisponíveis.' }; report('Operação concluída. Reconecte para consultar as configurações.'); syncForm(); return; }
              data = applied; state.dirty = false; state.saveDraft('aviary', null);
              choose(current(selected) ? selected : data.network ? NETWORK : data.ports[0]?.id);
              report('Configurações aplicadas.');
            } else report('Não aplicado. Abra a operação para consultar o motivo.');
          }
        }
        if (!state.dirty && !pending && !submitting && (data.revision !== revision || !selected || !current(selected))) {
          const nextId = current(selected) ? selected : data.network ? NETWORK : data.ports[0]?.id;
          if (nextId) choose(nextId);
          else { selected = null; editor.replaceChildren(); }
        }
        list(); syncForm();
      }
    } catch (error) { if (generation === state.generation) { data = { ...data, error:error.message }; report(error.message); syncForm(); } }
    finally { if (generation === state.generation) setTimeout(refresh, 5000); }
  }
  setTimeout(refresh, 5000);
}
