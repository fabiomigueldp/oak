import { bytes, time, ago, JOB_STATES } from './model.js';

let selected = null;
let query = '';
let filter = 'all';
let latestSignature = '';
let pendingRefresh = false;

export function backupUpdated(context) {
  if (context.state.page !== 'backups') return;
  const signature = JSON.stringify((context.state.overview.jobs || []).map(j => [j.id, j.state, j.step]));
  if (signature === latestSignature || pendingRefresh || document.activeElement?.matches('.recovery-workspace input, .recovery-workspace select')) return;
  latestSignature = signature;
  pendingRefresh = true;
  renderBackups(context).catch(() => {}).finally(() => { pendingRefresh = false; });
}

export async function renderBackups(c) {
  const { el, button, field, input, select, heading, api, operation, state, can, showJob } = c;
  const generation = state.generation;
  const data = await api('/backups');
  if (state.page !== 'backups' || generation !== state.generation) return;
  const root = el('div', 'recovery-workspace');
  const points = data.backups;
  const policy = data.policy;
  const current = points[0];
  const active = data.jobs.find(j => ['queued', 'running', 'interrupted'].includes(j.state));
  const overdue = policy.enabled && data.next_run && Date.now()/1000 > data.next_run + 1800;
  const idle = data.health?.last_skipped > (current?.created || 0);
  const status = !data.ready ? 'Não instalado' : overdue ? 'Rotina atrasada' : policy.enabled ? idle ? 'Sem alterações' : 'Automático' : 'Pausado';
  const tag = (label, kind = '') => el('span', 'recovery-tag ' + kind, label);
  const metric = (label, value) => el('div', 'recovery-metric', el('span', '', label), el('strong', '', value));
  const create = el('div', 'recovery-create');
  create.hidden = true;
  const name = input('');
  name.placeholder = 'Nome opcional';
  name.maxLength = 80;
  name.setAttribute('aria-label', 'Nome do backup');
  const createForm = el('form', 'recovery-create-form', name);
  const createButton = el('button', 'button primary', 'Criar');
  createButton.type = 'submit';
  createForm.append(createButton, button('Cancelar', () => { create.hidden = true; createToggle.focus(); }));
  createForm.addEventListener('submit', async e => {
    e.preventDefault(); createButton.disabled = true;
    try { await operation('backup', { name: name.value.trim() || 'Manual' }); create.hidden = true; }
    catch (error) { c.toast(error.message); }
    finally { createButton.disabled = false; }
  });
  create.append(createForm);
  const createToggle = button('Criar backup', () => { create.hidden = !create.hidden; if (!create.hidden) name.focus(); }, 'button primary', 'plus');
  root.append(heading('Backups', null,
    button('Atualizar', () => renderBackups(c), 'button'), can('backup') && createToggle), create);
  root.append(el('div', 'recovery-summary',
    el('div', 'recovery-mode', tag(status, overdue ? 'warning' : policy.enabled ? 'good' : ''), el('small', '', 'Oracle · local')),
    metric('Último ponto', current ? ago(current.created) : 'Sem ponto'),
    metric('Próxima avaliação', policy.enabled ? (data.next_run && !overdue ? time(data.next_run, true) : 'Na próxima execução') : 'Pausado'),
    metric('Mais antigo', points.length ? time(points.at(-1).created, true) : 'Sem backup')));
  if (active) root.append(button(active.label + ' · ' + (active.step || JOB_STATES[active.state]), () => showJob(active.id), 'recovery-running', 'activity'));
  if (data.recovery_pending && can('recover_restore')) root.append(button('Reverter restauração interrompida', () => operation('recover_restore'), 'button danger'));
  if (data.health?.check_failed) root.append(el('p', 'notice', 'A última verificação falhou. Cada restauração verifica os arquivos selecionados antes de substituir o mundo.'));
  if (data.health?.capacity_limited) root.append(el('p', 'notice', 'O backup mais recente excede o orçamento. Aumente o espaço para manter histórico.'));
  const workspace = el('div', 'recovery-columns');
  const history = el('section', 'recovery-history');
  history.setAttribute('aria-label', 'Pontos de recuperação');
  const search = input(query, 'search');
  search.placeholder = 'Buscar pontos'; search.setAttribute('aria-label', 'Buscar pontos');
  const filters = select({ all: 'Todos', pinned: 'Favoritos' }, filter);
  filters.setAttribute('aria-label', 'Filtrar pontos');
  const list = el('div', 'recovery-list');
  const details = el('section', 'recovery-detail');
  details.setAttribute('aria-label', 'Detalhes do ponto');
  const detailsHeading = el('h2'); detailsHeading.tabIndex = -1;
  function choose(p, focus = false) {
    selected = p.id;
    for (const item of list.children) item.setAttribute('aria-pressed', String(item.dataset.point === selected));
    details.replaceChildren();
    detailsHeading.textContent = p.name;
    details.append(el('div', 'recovery-detail-heading', detailsHeading, p.pinned ? tag('Favorito') : p === current ? tag('Mais recente') : null));
    details.append(el('p', 'recovery-date', time(p.created, true)));
    const facts = el('dl', 'recovery-facts');
    for (const [label, value] of Object.entries({
      'Minecraft': p.manifest?.version || p.manifest?.versions?.join(', ') || 'Não registrado',
      'Dados': bytes(p.bytes), 'Adicionado': p.added_bytes != null ? bytes(p.added_bytes) : 'Arquivo completo',
      'Duração': p.duration != null ? `${Math.round(p.duration)} s` : 'Não registrada',
    })) facts.append(el('div', '', el('dt', '', label), el('dd', '', value)));
    details.append(facts);
    const checks = el('ol', 'recovery-checks');
    for (const [label, checked, value] of [
      ['Índice e integridade', p.integrity, p.integrity ? 'Conferidos' : 'Pendente'],
      ['Extração', p.restoration?.level, p.restoration?.level ? time(p.restoration.at, true) : 'Pendente'],
      ['Inicialização isolada', p.restoration?.playable_boot_tested, p.restoration?.playable_boot_tested ? `${Math.round(p.restoration.boot_seconds || 0)} s · ${time(p.restoration.at, true)}` : 'Pendente'],
    ]) checks.append(el('li', '', el('span', 'recovery-check-mark ' + (checked ? 'good' : ''), checked ? '✓' : '·'), el('span', '', label), el('small', '', value)));
    const verification = el('details', 'recovery-disclosure', el('summary', '', 'Verificações'), checks);
    if (p.verification_failed) details.append(el('p', 'recovery-caution', `A última verificação falhou em ${time(p.verification_failed.at, true)}. Consulte a operação.`));
    const actions = el('div', 'recovery-actions');
    if (can('verify_backup')) {
      verification.append(button('Verificar arquivos', () => operation('verify_backup', { backup: p.id, boot: false }), 'button small', 'check'));
      if (p.manifest?.includes_runtime || p.source === 'oak') verification.append(button('Testar inicialização', () => operation('verify_backup', { backup: p.id, boot: true }), 'button small', 'flask'));
    }
    if (can('restore_backup') && p.restorable) actions.append(button('Restaurar', () => operation('restore_backup', { backup: p.id, fingerprint: p.fingerprint }), 'button danger small'));
    details.append(actions);
    details.append(verification);
    if (can('restore_backup') && !p.restorable) details.append(el('p', 'recovery-caution', 'Este arquivo não contém o servidor completo para restauração pelo painel.'));
    const more = el('details', 'recovery-disclosure', el('summary', '', 'Gerenciar ponto'));
    if (can('backup_edit') && p.source === 'repository') {
      const rename = input(p.name); rename.maxLength = 80;
      more.append(field('Nome', rename), el('div', 'recovery-actions',
        button('Salvar nome', () => operation('backup_edit', { backup: p.id, name: rename.value }), 'button small'),
        button(p.pinned ? 'Remover favorito' : 'Favoritar', () => operation('backup_edit', { backup: p.id, pinned: !p.pinned }), 'button small')));
      if (can('backup_delete')) more.append(button('Excluir backup', () => operation('backup_delete', { backup: p.id }), 'button danger small'));
      if (p.pinned) more.append(el('p', 'caption', 'Favoritos seguem a mesma limpeza automática.'));
    }
    more.append(el('p', 'recovery-id', p.id), el('p', 'caption', 'Mundo, mods, configurações e runtime. Serviços externos e mapa ficam fora da restauração.'));
    details.append(more);
    if (focus && matchMedia('(max-width: 900px)').matches) { details.scrollIntoView({ behavior: 'instant', block: 'start' }); detailsHeading.focus({ preventScroll: true }); }
  }
  function paintList() {
    const visible = points.filter(p => (p.name + ' ' + (p.manifest?.versions || []).join(' ')).toLocaleLowerCase('pt-BR').includes(query.toLocaleLowerCase('pt-BR')) && (filter === 'all' || filter === 'pinned' && p.pinned || filter === 'tested' && p.restoration?.playable_boot_tested));
    list.replaceChildren();
    for (const p of visible) {
      const date = new Date(p.created * 1000);
      const item = button('', () => choose(p, true), 'recovery-point');
      item.dataset.point = p.id;
      item.append(el('span', 'recovery-point-date', el('strong', '', date.toLocaleDateString('pt-BR', { day: '2-digit' })), el('small', '', date.toLocaleDateString('pt-BR', { month: 'short' }).replace('.', ''))),
        el('span', 'recovery-point-name', el('strong', '', p.name), el('small', '', `${time(p.created)} · ${bytes(p.added_bytes ?? p.bytes)}${p.added_bytes != null ? ' novos' : ''}`)),
        p.pinned ? tag('Favorito') : el('span'));
      list.append(item);
    }
    if (!visible.length) { list.append(el('p', 'recovery-empty', points.length ? 'Nenhum ponto encontrado.' : 'Crie o primeiro ponto de recuperação.')); details.replaceChildren(); return; }
    choose(visible.find(p => p.id === selected) || visible[0]);
  }
  search.addEventListener('input', () => { query = search.value; paintList(); });
  filters.addEventListener('change', () => { filter = filters.value; paintList(); });
  history.append(el('div', 'recovery-toolbar', search, filters), list);
  workspace.append(history, details); root.append(workspace); paintList();

  const bottom = el('div', 'recovery-bottom');
  const storage = el('section', 'recovery-storage', el('h2', '', 'Armazenamento'));
  const used = data.bytes || 0, budget = policy.budget_gib * 1024 ** 3;
  const meter = el('meter'); meter.min = 0; meter.max = budget; meter.value = used; meter.setAttribute('aria-label', 'Uso do orçamento do repositório');
  storage.append(el('div', 'recovery-storage-total', el('span', '', `${bytes(used)} / ${policy.budget_gib} GiB`), el('small', '', `${points.length} pontos`)), meter,
    el('p', 'caption', `${bytes(data.free_bytes)} livres no servidor`));
  if (data.logical_bytes > used && used > 0) storage.append(el('p', 'caption', `${Math.round((1-used/data.logical_bytes)*100)}% menos espaço com deduplicação e compressão.`));
  const storageMore = el('details', 'recovery-disclosure', el('summary', '', 'Verificação e limpeza'));
  storageMore.append(el('p', 'caption', data.health?.data_checked ? `Dados verificados em ${time(data.health.data_checked, true)}.` : 'Verificação completa pendente.'), el('div', 'recovery-actions',
    can('backup_check') && button('Verificar dados', () => operation('backup_check'), 'button small'),
    can('backup_compact') && button('Aplicar retenção', () => operation('backup_compact', { revision: policy.revision }), 'button small')),
    el('p', 'caption', 'Cópia externa não configurada.'));
  storage.append(storageMore); bottom.append(storage);
  const policySection = el('section', 'recovery-policy', el('h2', '', 'Rotina'));
  policySection.append(el('p', '', policy.enabled ? `A cada ${policy.interval_minutes >= 60 && policy.interval_minutes % 60 === 0 ? policy.interval_minutes / 60 + ' h' : policy.interval_minutes + ' min'}` : 'Pausada'),
    el('p', 'caption', 'Somente com alterações. Os mais antigos saem quando faltar espaço.'));
  if (can('backup_policy')) {
    const settings = el('details', 'recovery-disclosure', el('summary', '', 'Configurar'));
    const policyForm = el('form', 'recovery-policy-form');
    const enabled = select({ true: 'Ativa', false: 'Pausada' }, String(policy.enabled));
    const fields = {};
    policyForm.append(field('Automação', enabled));
    for (const [key, label, min, max] of [['interval_minutes','Intervalo (min)',5,525600],['budget_gib','Espaço (GiB)',1,100000],['check_days','Verificar dados (dias)',1,365],['boot_days','Testar recuperação (dias)',1,365]]) {
      fields[key] = input(policy[key], 'number'); fields[key].min = min; fields[key].max = max; fields[key].required = true;
      policyForm.append(field(label, fields[key]));
    }
    const save = el('button', 'button primary small', 'Salvar rotina'); save.type = 'submit'; policyForm.append(save);
    policyForm.addEventListener('submit', async event => {
      event.preventDefault(); save.disabled = true;
      try { await operation('backup_policy', { revision: policy.revision, policy: { enabled: enabled.value === 'true', ...Object.fromEntries(Object.entries(fields).map(([k,v]) => [k, Number(v.value)])) } }); settings.open = false; }
      catch (error) { c.toast(error.message); } finally { save.disabled = false; }
    });
    settings.append(policyForm); policySection.append(settings);
  }
  bottom.append(policySection);
  root.append(bottom);
  if (data.jobs.length) {
    const activity = el('details', 'recovery-disclosure recovery-activity', el('summary', '', 'Atividade recente'));
    for (const job of data.jobs.slice(0, 8)) activity.append(button(`${job.label} · ${JOB_STATES[job.state]} · ${time(job.created, true)}`, () => showJob(job.id), 'recovery-job'));
    root.append(activity);
  }
  document.querySelector('#view').replaceChildren(root);
}
