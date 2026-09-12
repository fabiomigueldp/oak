import { time } from './model.js';

export const OPERATOR_KINDS = { shell: 'Shell', python: 'Python', javascript: 'JavaScript', command: 'Minecraft' };
const JOB_LABELS = { queued: 'Na fila', running: 'Em execução', completed: 'Concluído', succeeded: 'Concluído', failed: 'Falhou', cancelled: 'Cancelado', interrupted: 'Interrompido', timed_out: 'Tempo esgotado' };
export const isJobActive = job => ['queued', 'running', 'cancelling'].includes(job?.state);
export function triggerLabel(trigger = {}) {
  if (trigger.type === 'interval') return `A cada ${trigger.seconds} s`;
  if (trigger.type === 'once') return time(trigger.at, true);
  if (trigger.type === 'event') return `Evento: ${trigger.name}`;
  return 'Sem gatilho';
}
export function parseObject(source, label) {
  let value;
  try { value = JSON.parse(source || '{}'); } catch { throw new Error(`${label}: JSON inválido.`); }
  if (!value || Array.isArray(value) || typeof value !== 'object') throw new Error(`${label}: use um objeto JSON.`);
  return value;
}
export function makeTrigger(type, value) {
  if (type === 'interval') {
    const seconds = Number(value);
    if (!Number.isFinite(seconds) || seconds <= 0) throw new Error('Informe um intervalo maior que zero.');
    return { type, seconds };
  }
  if (type === 'once') {
    const at = new Date(value).getTime() / 1000;
    if (!Number.isFinite(at)) throw new Error('Informe a data e a hora.');
    return { type, at };
  }
  if (type === 'event' && String(value).trim()) return { type, name: String(value).trim() };
  throw new Error('Informe o nome do evento.');
}
export function createEventFeed(read) {
  const feed = { events: [], cursor: 0, hasMore: false, refresh };
  let pending;
  function refresh() {
    pending ||= drain().finally(() => { pending = null; });
    return pending;
  }
  async function drain() {
    for (let pageIndex = 0; pageIndex < 10; pageIndex++) {
      const page = await read(`/events?after=${feed.cursor}`);
      const next = page.next_cursor;
      if (!Array.isArray(page.events) || !Number.isSafeInteger(next) || next < feed.cursor || (page.has_more && next === feed.cursor)) {
        throw new Error('Resposta de eventos inválida. Atualize para tentar novamente.');
      }
      const added = page.events.filter(event => Number.isSafeInteger(event.id) && event.id > feed.cursor && event.id <= next);
      feed.events = [...feed.events, ...added].slice(-200);
      feed.cursor = next;
      feed.hasMore = Boolean(page.has_more);
      if (!feed.hasMore) break;
    }
    return feed;
  }
  return feed;
}
function localDateTime(timestamp) {
  const date = new Date(timestamp * 1000);
  if (!Number.isFinite(date.getTime())) return '';
  const shifted = new Date(date.getTime() - date.getTimezoneOffset() * 60000);
  return shifted.toISOString().slice(0, 16);
}

export async function renderOperator(ctx) {
  const { el, button, field, input, select, heading, api, state } = ctx;
  const root = document.querySelector('#view');
  if (state.session?.user.role !== 'owner') {
    root.replaceChildren(heading('Operador'), el('p', 'muted', 'Disponível para o proprietário.'));
    return;
  }
  const generation = state.generation;
  const controller = new AbortController();
  let timer, disposed = false, selectedJob = null, selectedNotebook = null;
  let jobs = [], routines = [], notebooks = [], events = [], discovery = {}, services = [], packages = [];
  let outputOffset = 0, outputText = '', jobGeneration = 0, notebookGeneration = 0;
  let activeTab = 'execute', notebookBaseline = '', notebookRevision = null, editingRoutine = null;
  let submitting = false, notebookSaving = false, idempotency = null;
  const live = () => !disposed && generation === state.generation && state.session?.user.role === 'owner';
  const read = path => api(`/operator${path}`, undefined, 'GET', {}, { signal: controller.signal });
  const eventFeed = createEventFeed(read);
  const write = (path, data, method = 'POST') => api(`/operator${path}`, data, method);
  const call = (method, data = {}) => write('/call', { method, data });
  state.disposeView = () => { disposed = true; clearTimeout(timer); controller.abort(); };
  const notice = el('p', 'operator-notice'); notice.setAttribute('role', 'status'); notice.hidden = true;
  const report = message => { if (live()) { notice.textContent = message; notice.hidden = !message; } };
  const textarea = (value = '', rows = 12) => {
    const node = el('textarea', 'operator-code');
    Object.assign(node, { value, rows, spellcheck: false, autocapitalize: 'off', autocomplete: 'off' });
    return node;
  };
  const action = (label, fn, style = 'button') => button(label, async () => {
    try { await fn(); } catch (error) { if (error.name !== 'AbortError') report(error.message); }
  }, style);
  const detail = (label, content) => el('details', 'operator-details', el('summary', '', label), content);
  const status = el('p', 'operator-status muted');
  const panels = {};
  const navigation = el('nav', 'operator-tabs'); navigation.setAttribute('aria-label', 'Área do operador');
  const tabs = { execute: 'Executar', routines: 'Rotinas', services: 'Serviços', packages: 'Pacotes', notebooks: 'Cadernos', activity: 'Atividade' };
  const switchTab = key => {
    activeTab = key;
    report('');
    for (const [name, panel] of Object.entries(panels)) panel.hidden = name !== key;
    for (const child of navigation.children) child.setAttribute('aria-pressed', String(child.dataset.tab === key));
  };
  for (const [key, label] of Object.entries(tabs)) {
    const tab = action(label, () => switchTab(key), 'operator-tab'); tab.dataset.tab = key;
    navigation.append(tab); panels[key] = el('section', `operator-panel operator-${key}`);
    panels[key].setAttribute('aria-label', label);
  }
  const workspace = el('div', 'operator-workspace', heading('Operador'), status, navigation, notice, ...Object.values(panels));
  root.replaceChildren(workspace);

  const saved = state.getDraft('operator') || {};
  idempotency = saved.idempotency || null;
  const kind = select(OPERATOR_KINDS, saved.kind || 'shell');
  const label = input(saved.label || ''); label.maxLength = 160;
  const source = textarea(saved.source || ''); source.required = true; source.setAttribute('aria-describedby', 'operator-execution-note');
  const cwd = input(saved.cwd || ''); cwd.placeholder = 'Diretório padrão do executor';
  const timeout = input(saved.timeout_seconds ?? '', 'number'); Object.assign(timeout, { min: 0, step: 1 }); timeout.placeholder = 'Sem limite';
  const environment = textarea(saved.environment || '{}', 3);
  const resources = input(saved.resources || ''); resources.placeholder = 'Ex.: world:arena';
  const executionNote = el('p', 'muted operator-hint', 'Executa no servidor. Fechar esta página mantém o trabalho em execução.'); executionNote.id = 'operator-execution-note';
  const draftState = el('span', 'muted operator-hint'); draftState.setAttribute('role', 'status');
  const currentDraft = () => ({ kind: kind.value, label: label.value, source: source.value, cwd: cwd.value,
    timeout_seconds: timeout.value === '' || Number(timeout.value) === 0 ? null : Number(timeout.value), environment: environment.value, resources: resources.value, idempotency });
  const persistDraft = () => {
    idempotency = null;
    state.saveDraft('operator', currentDraft());
    state.dirty = true;
    draftState.textContent = state.draftSaved ? 'Rascunho salvo neste navegador' : 'Rascunho não salvo';
  };
  const jobSpec = () => ({
    kind: kind.value, source: source.value, label: label.value.trim() || OPERATOR_KINDS[kind.value],
    ...(cwd.value.trim() ? { cwd: cwd.value.trim() } : {}), timeout_seconds: timeout.value === '' || Number(timeout.value) === 0 ? null : Number(timeout.value),
    environment: parseObject(environment.value, 'Variáveis'),
    resources: resources.value.split(',').map(value => value.trim()).filter(Boolean),
  });
  for (const node of [kind, label, source, cwd, timeout, environment, resources]) node.addEventListener('input', persistDraft);
  const run = el('button', 'button primary', 'Executar'); run.type = 'submit';
  const editor = el('form', 'operator-editor');
  const output = el('pre', 'operator-output'); output.tabIndex = 0; output.setAttribute('aria-label', 'Saída da execução');
  const jobStatus = el('p', 'operator-job-status muted', 'Selecione uma execução.'); jobStatus.setAttribute('role', 'status');
  const jobIdentity = el('span', 'operator-job-id muted');
  const cancel = action('Cancelar', async () => {
    if (!selectedJob) return;
    await write(`/jobs/${encodeURIComponent(selectedJob)}/cancel`, {});
    report('Cancelamento solicitado.'); await refreshSelected();
  }); cancel.hidden = true;
  const copyOutput = action('Copiar saída', async () => {
    await navigator.clipboard.writeText(outputText); report('Saída copiada.');
  }, 'button ghost');
  const reuse = action('Carregar código', async () => {
    if (!selectedJob) return;
    const id = selectedJob;
    const result = await read(`/jobs/${encodeURIComponent(id)}`);
    if (!live() || id !== selectedJob) return;
    const job = result.job || result;
    fillEditor(job.spec || job);
    source.focus(); report('Código carregado.');
  }, 'button ghost'); reuse.hidden = true;
  const jobList = el('div', 'operator-list');
  const jobFilter = input('', 'search'); jobFilter.placeholder = 'Buscar execução'; jobFilter.setAttribute('aria-label', 'Buscar execução');
  const jobListPanel = el('aside', 'operator-job-list', el('h2', '', 'Execuções'), jobFilter, jobList);
  const outputPanel = el('section', 'operator-result',
    el('div', 'operator-toolbar', el('div', '', el('h2', '', 'Saída'), jobStatus), cancel),
    output, el('div', 'operator-toolbar operator-result-actions', jobIdentity, el('div', 'operator-actions', reuse, copyOutput)));
  function fillEditor(job) {
    kind.value = job.kind || 'shell'; label.value = job.label || ''; source.value = job.source || '';
    cwd.value = job.cwd || ''; timeout.value = job.timeout_seconds ?? '';
    environment.value = JSON.stringify(job.environment || {}, null, 2); resources.value = (job.resources || []).join(', ');
    persistDraft(); switchTab('execute');
  }
  editor.append(el('div', 'operator-fields', field('Linguagem', kind), field('Nome', label)), field('Código', source),
    detail('Opções', el('div', 'operator-options',
      el('div', 'operator-fields', field('Diretório', cwd), field('Tempo máximo (s)', timeout, 'Opcional. Vazio ou 0: sem limite.')),
      field('Variáveis (JSON)', environment), field('Recursos compartilhados', resources, 'Separe por vírgulas. Execuções com o mesmo recurso aguardam sua vez.'))),
    executionNote, el('div', 'operator-toolbar', draftState, run));
  editor.addEventListener('submit', async event => {
    event.preventDefault(); if (submitting || !editor.reportValidity()) return;
    submitting = true; run.disabled = true;
    try {
      const spec = jobSpec();
      idempotency ||= crypto.randomUUID();
      state.saveDraft('operator', currentDraft());
      const result = await write('/jobs', { ...spec, idempotency });
      if (!live()) return;
      idempotency = null;
      state.saveDraft('operator', currentDraft());
      await selectJob((result.job || result).id);
      await refreshLists(); report('Execução enviada.');
    } catch (error) { report(error.message); }
    finally { submitting = false; run.disabled = false; }
  });
  panels.execute.append(el('div', 'operator-execution-layout', el('div', 'operator-execution-main', editor, outputPanel), jobListPanel));
  function jobLabel(job) { return JOB_LABELS[job.state] || job.state || 'Estado desconhecido'; }
  function drawJobs() {
    const query = jobFilter.value.toLocaleLowerCase('pt-BR');
    const matches = jobs.filter(job => `${job.label} ${job.kind} ${job.id}`.toLocaleLowerCase('pt-BR').includes(query));
    preserveList(jobList, matches, job => {
      const node = action('', () => selectJob(job.id), 'operator-list-button');
      node.dataset.item = job.id; node.setAttribute('aria-pressed', String(job.id === selectedJob));
      node.append(el('strong', '', job.label || OPERATOR_KINDS[job.kind] || job.id),
        el('span', 'muted', `${jobLabel(job)} · ${time(job.created_at || job.created, true)}`));
      return node;
    }, query ? 'Nenhuma execução encontrada.' : 'As execuções aparecerão aqui.');
  }
  jobFilter.addEventListener('input', drawJobs);
  async function selectJob(id) {
    if (!id || !live()) return;
    selectedJob = id; ++jobGeneration; outputOffset = 0; outputText = ''; output.textContent = '';
    jobStatus.textContent = 'Carregando saída…'; jobIdentity.textContent = id; cancel.hidden = true; reuse.hidden = false;
    drawJobs(); await refreshSelected();
  }
  async function refreshSelected() {
    if (!selectedJob) return;
    const id = selectedJob, token = jobGeneration, offset = outputOffset;
    const result = await read(`/jobs/${encodeURIComponent(id)}?offset=${offset}`);
    if (!live() || token !== jobGeneration || id !== selectedJob || offset !== outputOffset) return;
    const job = result.job || result;
    const stick = output.scrollHeight - output.scrollTop - output.clientHeight < 60;
    outputText += typeof result.output === 'string' ? result.output : '';
    if (outputText.length > 500000) outputText = '[Saída anterior disponível pela API]\n' + outputText.slice(-480000);
    outputOffset = result.next_offset ?? result.output_size ?? outputOffset;
    output.textContent = outputText || (isJobActive(job) ? 'Aguardando saída…' : 'Sem saída.');
    if (stick) output.scrollTop = output.scrollHeight;
    jobStatus.textContent = `${jobLabel(job)}${job.exit_code == null ? '' : ` · código ${job.exit_code}`}${job.simulated || job.demo ? ' · simulação' : ''}`;
    cancel.hidden = !isJobActive(job); cancel.disabled = job.state === 'cancelling';
  }

  const routineList = el('div', 'operator-list');
  const routineName = input(''); routineName.required = true; routineName.maxLength = 160;
  const triggerType = select({ interval: 'Intervalo', once: 'Data e hora', event: 'Evento' }, 'interval');
  const triggerValue = input(3600, 'number'); triggerValue.min = 1; triggerValue.required = true;
  const triggerField = field('Intervalo (s)', triggerValue);
  const routineNote = el('p', 'muted operator-hint', 'Usa o código e as opções da aba Executar.');
  const routineSave = el('button', 'button primary', 'Criar rotina'); routineSave.type = 'submit';
  const clearRoutine = action('Nova rotina', () => { editingRoutine = null; routineName.value = ''; routineSave.textContent = 'Criar rotina'; }, 'button ghost');
  const routineForm = el('form', 'operator-routine-form', el('h2', '', 'Nova rotina'), field('Nome', routineName),
    el('div', 'operator-fields', field('Gatilho', triggerType), triggerField), routineNote, el('div', 'operator-actions', routineSave, clearRoutine));
  function updateTriggerField() {
    const type = triggerType.value;
    triggerValue.type = type === 'interval' ? 'number' : type === 'once' ? 'datetime-local' : 'text';
    triggerValue.value = type === 'interval' ? '3600' : '';
    triggerField.firstElementChild.textContent = { interval: 'Intervalo (s)', once: 'Data e hora local', event: 'Nome do evento' }[type];
    triggerValue.placeholder = type === 'event' ? 'Ex.: minecraft.player.join' : '';
  }
  triggerType.addEventListener('change', updateTriggerField);
  routineForm.addEventListener('submit', async event => {
    event.preventDefault(); if (!routineForm.reportValidity()) return;
    if (!source.value.trim()) { report('Preencha o código na aba Executar.'); switchTab('execute'); source.focus(); return; }
    routineSave.disabled = true;
    try {
      if (!editor.reportValidity()) { switchTab('execute'); return; }
      const payload = { name: routineName.value.trim(), trigger: makeTrigger(triggerType.value, triggerValue.value), job: jobSpec(),
        ...(editingRoutine ? { revision: editingRoutine.revision, enabled: editingRoutine.enabled } : { enabled: true }) };
      await write(editingRoutine ? `/routines/${encodeURIComponent(editingRoutine.id)}` : '/routines', payload, editingRoutine ? 'PATCH' : 'POST');
      if (!live()) return;
      editingRoutine = null; routineSave.textContent = 'Criar rotina'; routineName.value = ''; await refreshLists(); report('Rotina salva.');
    } catch (error) { report(error.message); }
    finally { routineSave.disabled = false; }
  });
  panels.routines.append(el('div', 'operator-split', el('section', '', el('h2', '', 'Rotinas'), routineList), routineForm));
  function drawRoutines() {
    preserveList(routineList, routines, routine => {
      const id = encodeURIComponent(routine.id);
      const controls = el('div', 'operator-actions',
        action('Editar', () => {
          editingRoutine = routine; routineName.value = routine.name; routineSave.textContent = 'Salvar rotina';
          triggerType.value = routine.trigger.type; updateTriggerField();
          triggerValue.value = routine.trigger.type === 'interval' ? routine.trigger.seconds : routine.trigger.type === 'event' ? routine.trigger.name : localDateTime(routine.trigger.at);
          fillEditor(routine.job); switchTab('routines'); routineName.focus();
        }, 'button small'),
        action(routine.enabled ? 'Pausar' : 'Ativar', async () => {
          await write(`/routines/${id}`, { name: routine.name, trigger: routine.trigger, job: routine.job, revision: routine.revision, enabled: !routine.enabled }, 'PATCH'); await refreshLists();
        }, 'button small'),
        action('Executar agora', async () => {
          const result = await write(`/routines/${id}/run`, {});
          if (!live()) return;
          switchTab('execute'); await selectJob((result.job || result).id); await refreshLists();
        }, 'button small'),
        action('Carregar código', () => fillEditor(routine.job), 'button ghost small'),
        action('Excluir', async () => {
          await write(`/routines/${id}`, { revision: routine.revision }, 'DELETE'); await refreshLists(); report('Rotina excluída.');
        }, 'button ghost small'));
      const node = el('article', 'operator-record', el('strong', '', routine.name),
        el('p', 'muted', `${routine.enabled ? 'Ativa' : 'Pausada'} · ${triggerLabel(routine.trigger)}`), controls);
      node.dataset.item = routine.id;
      if (routine.last_error) node.append(el('p', 'operator-notice', routine.last_error));
      return node;
    }, 'Crie uma rotina para executar código por horário ou evento.');
  }

  const serviceList = el('div', 'operator-list');
  const serviceName = input(''); serviceName.required = true; serviceName.pattern = '[a-z][a-z0-9-]*'; serviceName.placeholder = 'Ex.: arena';
  const serviceSave = el('button', 'button primary', 'Salvar serviço'); serviceSave.type = 'submit';
  const serviceOutput = el('pre', 'operator-output'); serviceOutput.hidden = true; serviceOutput.tabIndex = 0; serviceOutput.setAttribute('aria-label', 'Logs do serviço');
  const serviceForm = el('form', 'operator-routine-form', el('h2', '', 'Salvar serviço'), field('Nome', serviceName),
    el('p', 'operator-hint muted', 'Usa o código da aba Executar. Após salvar, inicie ou habilite o serviço.'), serviceSave);
  serviceForm.addEventListener('submit', async event => {
    event.preventDefault(); if (!serviceForm.reportValidity()) return;
    if (!source.value.trim() || !editor.reportValidity()) { switchTab('execute'); source.focus(); report('Preencha o código do serviço.'); return; }
    if (kind.value === 'command') { report('Serviços usam Shell, Python ou JavaScript.'); return; }
    serviceSave.disabled = true;
    try { await call('services.upsert', { name: serviceName.value, ...jobSpec() }); await refreshExtras(); report('Serviço salvo.'); }
    catch (error) { report(error.message); }
    finally { serviceSave.disabled = false; }
  });
  panels.services.append(el('div', 'operator-split', el('section', '', el('h2', '', 'Serviços'), serviceList, serviceOutput), serviceForm));
  function drawServices() {
    preserveList(serviceList, services, service => {
      const name = service.name;
      const node = el('article', 'operator-record', el('strong', '', name),
        el('p', 'muted', service.demo ? 'Simulação' : ({ active: 'Ativo', inactive: 'Parado', failed: 'Falhou', activating: 'Iniciando' }[service.state] || service.state || service.status || 'Estado indisponível')),
        !service.demo && el('p', 'muted', service.enabled ? 'Inicia com o servidor' : 'Inicialização manual'),
        el('div', 'operator-actions', ...[['Iniciar', 'start'], ['Parar', 'stop'], ['Reiniciar', 'restart'], ['Habilitar', 'enable'], ['Desabilitar', 'disable']].map(([text, actionName]) => action(text, async () => {
          await call('services.control', { name, action: actionName }); await refreshExtras();
          report(state.session?.demo ? `${text}: simulação concluída.` : `${text}: comando concluído.`);
        }, 'button small')),
        action('Carregar código', () => { serviceName.value = name; fillEditor(service); }, 'button ghost small'),
        action('Logs', async () => {
          const result = await call('services.logs', { name }); if (!live()) return;
          serviceOutput.hidden = false; serviceOutput.textContent = result.output || 'Sem saída.';
        }, 'button ghost small'),
        action('Excluir', async () => { await call('services.delete', { name }); await refreshExtras(); report('Serviço excluído.'); }, 'button ghost small')));
      node.dataset.item = name; return node;
    }, 'Salve um serviço para manter um programa em execução.');
  }

  const packageList = el('div', 'operator-list');
  const packageName = input(''); packageName.required = true; packageName.pattern = '[a-z][a-z0-9-]*';
  const packageVersion = input('1.0.0'); packageVersion.required = true;
  const packageKind = select({ script: 'Scripts', datapack: 'Datapack' }, 'script');
  const packageFiles = textarea('{}', 12); packageFiles.required = true;
  const packageSave = el('button', 'button primary', 'Instalar versão'); packageSave.type = 'submit';
  const packageForm = el('form', 'operator-routine-form', el('h2', '', 'Instalar pacote'),
    field('Nome', packageName), el('div', 'operator-fields', field('Versão', packageVersion), field('Tipo', packageKind)),
    field('Arquivos (JSON)', packageFiles, 'Objeto com caminho relativo e conteúdo de cada arquivo.'),
    el('p', 'operator-hint muted', 'Ativar um datapack recarrega os datapacks do jogo.'), packageSave);
  packageForm.addEventListener('submit', async event => {
    event.preventDefault(); if (!packageForm.reportValidity()) return;
    packageSave.disabled = true;
    try {
      await call('packages.install', { name: packageName.value, version: packageVersion.value, kind: packageKind.value, files: parseObject(packageFiles.value, 'Arquivos') });
      await refreshExtras(); report('Pacote instalado.');
    } catch (error) { report(error.message); }
    finally { packageSave.disabled = false; }
  });
  panels.packages.append(el('div', 'operator-split', el('section', '', el('h2', '', 'Pacotes'), packageList), packageForm));
  function drawPackages() {
    preserveList(packageList, packages, pack => {
      const node = el('article', 'operator-record', el('strong', '', pack.name),
        el('p', 'muted', `${pack.version || ''} · ${pack.kind === 'datapack' ? 'Datapack' : 'Scripts'}${pack.active ? ' · Ativo' : ''}`),
        pack.path && el('p', 'operator-job-id muted', pack.path),
        el('div', 'operator-actions',
          action('Carregar', async () => {
            const result = await call('packages.get', { name: pack.name }); if (!live()) return;
            const value = result.package || result;
            const contents = value.file_contents || (!Array.isArray(value.files) && value.files);
            if (!contents) { report(`Arquivos disponíveis em ${value.path || pack.path || pack.name}.`); return; }
            packageName.value = value.name; packageVersion.value = value.version; packageKind.value = value.kind;
            packageFiles.value = JSON.stringify(contents, null, 2); packageName.focus();
          }, 'button small'),
          pack.kind === 'datapack' && action(pack.active ? 'Desativar' : 'Ativar', async () => {
            await call(pack.active ? 'packages.deactivate' : 'packages.activate', { name: pack.name }); await refreshExtras();
          }, 'button small')));
      node.dataset.item = pack.name; return node;
    }, 'Instale scripts ou datapacks com arquivos e versão.');
  }
  async function refreshExtras() {
    const results = await Promise.all([call('services.list'), call('packages.list')]);
    if (!live()) return;
    services = results[0].services || []; packages = results[1].packages || []; drawServices(); drawPackages();
  }

  const notebookList = el('div', 'operator-list');
  const notebookTitle = input(''); notebookTitle.required = true; notebookTitle.maxLength = 160;
  const notebookContent = textarea('', 20); notebookContent.classList.add('operator-markdown');
  const notebookStatus = el('p', 'muted operator-hint'); notebookStatus.setAttribute('role', 'status');
  const notebookSave = el('button', 'button primary', 'Salvar'); notebookSave.type = 'submit';
  const notebookDelete = action('Excluir', async () => {
    if (!selectedNotebook) return;
    await write(`/notebooks/${encodeURIComponent(selectedNotebook)}`, { revision: notebookRevision }, 'DELETE');
    if (!live()) return;
    newNotebook(); await refreshLists(); report('Caderno excluído.');
  }, 'button ghost'); notebookDelete.hidden = true;
  const notebookForm = el('form', 'operator-notebook-form', field('Título', notebookTitle), field('Conteúdo (Markdown)', notebookContent),
    el('div', 'operator-toolbar', notebookStatus, el('div', 'operator-actions', notebookDelete, notebookSave)));
  const notebookDraft = () => ({ id: selectedNotebook, title: notebookTitle.value, content: notebookContent.value, revision: notebookRevision });
  const saveNotebookDraft = () => {
    const changed = JSON.stringify([notebookTitle.value, notebookContent.value]) !== notebookBaseline;
    state.saveDraft('operator-notebook', changed ? notebookDraft() : null);
    state.dirty = changed;
    notebookStatus.textContent = changed ? state.draftSaved ? 'Rascunho salvo neste navegador' : 'Alterações não salvas' : notebookRevision ? `Revisão ${notebookRevision}` : 'Novo caderno';
  };
  for (const control of [notebookTitle, notebookContent]) control.addEventListener('input', saveNotebookDraft);
  function loadNotebook(notebook, draft = null) {
    selectedNotebook = notebook.id || null;
    notebookRevision = draft?.revision ?? notebook.revision ?? null;
    notebookTitle.value = draft?.title ?? notebook.title ?? '';
    notebookContent.value = draft?.content ?? notebook.content ?? '';
    notebookBaseline = JSON.stringify([notebook.title || '', notebook.content || '']);
    notebookDelete.hidden = !selectedNotebook;
    saveNotebookDraft(); drawNotebooks();
  }
  function newNotebook() { ++notebookGeneration; loadNotebook({}); notebookTitle.focus(); }
  async function chooseNotebook(id) {
    if (notebookSaving) return;
    const changed = JSON.stringify([notebookTitle.value, notebookContent.value]) !== notebookBaseline;
    if (changed) { report('Salve o caderno ou descarte o rascunho antes de abrir outro.'); return; }
    const token = ++notebookGeneration;
    const notebook = await read(`/notebooks/${encodeURIComponent(id)}`);
    if (live() && token === notebookGeneration) loadNotebook(notebook.notebook || notebook);
  }
  notebookForm.addEventListener('submit', async event => {
    event.preventDefault(); if (notebookSaving || !notebookForm.reportValidity()) return;
    notebookSaving = true; notebookSave.disabled = true;
    const payload = { title: notebookTitle.value, content: notebookContent.value, ...(selectedNotebook ? { revision: notebookRevision } : {}) };
    try {
      const result = await write(selectedNotebook ? `/notebooks/${encodeURIComponent(selectedNotebook)}` : '/notebooks', payload, selectedNotebook ? 'PATCH' : 'POST');
      if (!live()) return;
      state.saveDraft('operator-notebook', null); loadNotebook(result.notebook || result); await refreshLists(); report('Caderno salvo.');
    } catch (error) { report(error.message); }
    finally { notebookSaving = false; notebookSave.disabled = false; }
  });
  const addNotebook = action('Novo caderno', () => {
    if (JSON.stringify([notebookTitle.value, notebookContent.value]) !== notebookBaseline) { report('Salve o caderno ou descarte o rascunho antes de criar outro.'); return; }
    newNotebook();
  }, 'button');
  const discardNotebook = action('Descartar rascunho', async () => {
    state.saveDraft('operator-notebook', null); state.dirty = false;
    notebookBaseline = JSON.stringify([notebookTitle.value, notebookContent.value]);
    if (selectedNotebook) await chooseNotebook(selectedNotebook); else newNotebook();
  }, 'button ghost');
  panels.notebooks.append(el('div', 'operator-notebook-layout', el('aside', '', el('div', 'operator-toolbar', el('h2', '', 'Cadernos'), addNotebook), notebookList),
    el('div', '', notebookForm, discardNotebook)));
  function drawNotebooks() {
    preserveList(notebookList, notebooks, notebook => {
      const node = action('', () => chooseNotebook(notebook.id), 'operator-list-button'); node.dataset.item = notebook.id;
      node.setAttribute('aria-pressed', String(notebook.id === selectedNotebook));
      node.append(el('strong', '', notebook.title), el('span', 'muted', `Revisão ${notebook.revision} · ${time(notebook.updated_at || notebook.updated, true)}`));
      return node;
    }, 'Guarde código, resultados e instruções para continuar.');
  }
  const eventList = el('div', 'operator-event-list');
  const eventStatus = el('p', 'muted operator-hint');
  const discoveryView = el('pre', 'operator-data');
  panels.activity.append(el('div', 'operator-toolbar', el('h2', '', 'Eventos'), action('Atualizar', refreshLists, 'button')),
    eventStatus, eventList, detail('Capacidades e conexão', discoveryView));
  function drawEvents() {
    eventStatus.textContent = eventFeed.hasMore ? 'Carregando histórico…' : 'Até 200 eventos mais recentes. Histórico completo pela API.';
    preserveList(eventList, [...events].reverse(), event => {
      const node = el('article', 'operator-event', el('time', 'muted', time(event.created_at || event.created || event.at, true)),
        el('div', '', el('strong', '', event.name || event.type || 'Evento'), el('span', 'muted', typeof event.actor === 'string' ? event.actor : event.actor?.id || '')),
        detail('Dados', el('pre', 'operator-data', JSON.stringify(event.payload || event.data || {}, null, 2))));
      node.dataset.item = String(event.id); return node;
    }, 'Os eventos do operador aparecerão aqui.');
  }
  function preserveList(node, values, render, emptyText) {
    const signature = JSON.stringify([values, selectedJob, selectedNotebook]);
    if (node.dataset.signature === signature) return;
    const active = node.contains(document.activeElement) ? document.activeElement : null;
    const row = active?.closest('[data-item]');
    const position = row ? [...row.querySelectorAll('button')].indexOf(active) : -1;
    node.dataset.signature = signature;
    node.replaceChildren(...values.map(render));
    if (!values.length) node.append(el('p', 'operator-empty muted', emptyText));
    if (row) {
      const replacement = [...node.querySelectorAll('[data-item]')].find(item => item.dataset.item === row.dataset.item);
      (replacement?.tagName === 'BUTTON' ? replacement : replacement?.querySelectorAll('button')[position])?.focus({ preventScroll: true });
    }
  }
  async function refreshLists() {
    const result = await Promise.all([read('/jobs'), read('/routines'), read('/notebooks'), eventFeed.refresh()]);
    if (!live()) return;
    jobs = result[0].jobs || []; routines = result[1].routines || []; notebooks = result[2].notebooks || []; events = eventFeed.events;
    drawJobs(); drawRoutines(); drawNotebooks(); drawEvents();
  }
  async function poll() {
    if (!live()) return;
    try {
      if (!document.hidden) {
        await refreshLists();
        if (activeTab === 'execute') await refreshSelected();
        if (activeTab === 'services' || activeTab === 'packages') await refreshExtras();
      }
    } catch (error) { if (error.name !== 'AbortError') report(error.message); }
    finally { if (live()) timer = setTimeout(poll, 2500); }
  }
  switchTab('execute');
  notebookBaseline = JSON.stringify(['', '']);
  try {
    discovery = await read('/discover');
    if (!live()) return;
    status.textContent = state.session.demo ? 'Demonstração. Execuções simuladas.' : discovery.available === false ? 'Executor indisponível.' : 'Executor conectado';
    discoveryView.textContent = JSON.stringify(discovery, null, 2);
    await refreshLists();
    await refreshExtras();
    if (!live()) return;
    const notebookSaved = state.getDraft('operator-notebook');
    if (notebookSaved) {
      if (notebookSaved.id) {
        try { const result = await read(`/notebooks/${encodeURIComponent(notebookSaved.id)}`); if (live()) loadNotebook(result.notebook || result, notebookSaved); }
        catch (error) { if (live()) { loadNotebook({}, { ...notebookSaved, revision: null }); report('Caderno indisponível. O rascunho foi preservado como novo.'); } }
      } else loadNotebook({}, notebookSaved);
    } else loadNotebook({});
    if (jobs.length) await selectJob(jobs[0].id);
  } catch (error) { if (error.name !== 'AbortError') { status.textContent = 'Sem conexão com o executor'; report(error.message); } }
  finally { if (live()) timer = setTimeout(poll, 2500); }
}
