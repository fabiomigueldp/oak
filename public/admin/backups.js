import { bytes, time, ago, JOB_STATES } from './model.js';

let query = '';
let latestSignature = '';
let pendingRefresh = false;

export function externalCopyStatus(data, now = Date.now() / 1000) {
  const at = data.external_copy_verified_at;
  const valid = Number.isFinite(at) && at > 0 && at <= now;
  const recent = valid && data.external_copy === true && now - at < 48 * 3600;
  return { at: valid ? at : null, kind: recent ? 'good' : 'warning',
    label: recent ? 'Confirmada nas últimas 48 h' : valid ? 'Sem confirmação recente' : 'Sem confirmação' };
}

export function backupUpdated(context) {
  if (context.state.page !== 'backups') return;
  const signature = JSON.stringify((context.state.overview.jobs || []).map(j => [j.id, j.state, j.step]));
  if (signature === latestSignature || pendingRefresh || document.activeElement?.matches('.recovery-workspace input')) return;
  latestSignature = signature;
  pendingRefresh = true;
  renderBackups(context).catch(() => {}).finally(() => { pendingRefresh = false; });
}

export async function renderBackups(c) {
  const { el, button, field, input, select, heading, api, operation, state, can, showJob, drawer } = c;
  const generation = state.generation;
  const data = await api('/backups');
  if (state.page !== 'backups' || generation !== state.generation) return;
  const root = el('div', 'recovery-workspace');
  const points = data.backups, policy = data.policy, current = points[0];
  const active = data.jobs.find(j => ['queued', 'running', 'interrupted'].includes(j.state));
  const overdue = policy.enabled && data.next_run && Date.now() / 1000 > data.next_run + 1800;
  const idle = data.health?.last_skipped > (current?.created || 0);
  const interval = policy.interval_minutes % 60 === 0 ? `${policy.interval_minutes / 60} h` : `${policy.interval_minutes} min`;
  const tag = (label, kind = '') => el('span', 'recovery-tag ' + kind, label);
  const external = externalCopyStatus(data);

  function openBackup(p) {
    const detail = el('div', 'recovery-detail', el('h2', '', time(p.created, true)));
    if (!['Manual', 'Automático'].includes(p.name)) detail.append(el('p', 'recovery-date', p.name));
    if (p.verification_failed) detail.append(el('p', 'notice', 'A última verificação deste backup falhou. Consulte as verificações abaixo.'));
    const facts = el('dl', 'recovery-facts');
    for (const [label, value] of Object.entries({
      'Minecraft': p.manifest?.version || p.manifest?.versions?.join(', ') || 'Não registrado',
      'Conteúdo': bytes(p.bytes),
      'Duração': p.duration != null ? `${Math.round(p.duration)} s` : 'Não registrada',
    })) facts.append(el('div', '', el('dt', '', label), el('dd', '', value)));
    detail.append(facts);
    if (can('restore_backup') && p.restorable) detail.append(button('Restaurar backup', () => operation('restore_backup', { backup: p.id, fingerprint: p.fingerprint }), 'button danger'));
    if (can('restore_backup') && !p.restorable) detail.append(el('p', 'caption', 'Este backup não contém o servidor completo para restauração pelo painel.'));
    if (can('backup_edit') && p.source === 'repository') {
      const manage = el('details', 'recovery-disclosure', el('summary', '', 'Nome e exclusão'));
      const name = input(p.name); name.maxLength = 80;
      manage.append(field('Nome', name), el('div', 'recovery-actions',
        button('Salvar nome', () => operation('backup_edit', { backup: p.id, name: name.value }), 'button small'),
        can('backup_delete') && button('Excluir backup', () => operation('backup_delete', { backup: p.id }), 'button danger small')));
      detail.append(manage);
    }
    const checks = el('details', 'recovery-disclosure', el('summary', '', 'Verificações'));
    const results = el('dl', 'recovery-checks');
    for (const [label, value] of [
      ['Integridade', p.integrity ? 'Conferida' : 'Sem registro'],
      ['Extração', p.restoration?.level ? time(p.restoration.at, true) : 'Não executada'],
      ['Inicialização', p.restoration?.playable_boot_tested ? time(p.restoration.at, true) : 'Não executada'],
    ]) results.append(el('div', '', el('dt', '', label), el('dd', '', value)));
    checks.append(results);
    if (can('verify_backup')) checks.append(el('div', 'recovery-actions',
      button('Verificar arquivos', () => operation('verify_backup', { backup: p.id, boot: false }), 'button small'),
      (p.manifest?.includes_runtime || p.source === 'oak') && button('Testar inicialização', () => operation('verify_backup', { backup: p.id, boot: true }), 'button small')));
    if (p.added_bytes != null) checks.append(el('p', 'caption', `${bytes(p.added_bytes)} adicionados ao armazenamento na criação. Arquivos compartilhados impedem prever o espaço liberado pela exclusão.`));
    checks.append(el('p', 'recovery-id', p.id));
    detail.append(checks);
    drawer('BACKUP', detail);
  }

  function openSettings() {
    const settings = el('div', 'recovery-detail', el('h2', '', 'Configurações'));
    if (can('backup_policy')) {
      const form = el('form', 'recovery-policy-form');
      const enabled = select({ true: 'Ativa', false: 'Pausada' }, String(policy.enabled));
      const hours = policy.interval_minutes % 60 === 0;
      const intervalInput = input(hours ? policy.interval_minutes / 60 : policy.interval_minutes, 'number');
      const unit = select({ 60: 'Horas', 1: 'Minutos' }, hours ? '60' : '1');
      unit.setAttribute('aria-label', 'Unidade do intervalo');
      intervalInput.setAttribute('aria-label', 'Intervalo'); intervalInput.required = true;
      const intervalBounds = () => { intervalInput.min = unit.value === '60' ? 1 : 5; intervalInput.max = Math.floor(525600 / Number(unit.value)); };
      unit.addEventListener('change', intervalBounds); intervalBounds();
      const budget = input(policy.budget_gib, 'number'); budget.min = 1; budget.max = 100000; budget.required = true;
      form.append(field('Automação', enabled), el('div', 'field', el('span', '', 'Intervalo'), el('div', 'recovery-interval', intervalInput, unit)), field('Armazenamento (GiB)', budget),
        el('p', 'caption', 'Sem alterações, o backup é dispensado. Ao atingir o limite, os mais antigos são removidos automaticamente.'));
      const advanced = el('details', 'recovery-disclosure', el('summary', '', 'Verificações automáticas'));
      const check = input(policy.check_days, 'number'), boot = input(policy.boot_days, 'number');
      for (const control of [check, boot]) { control.min = 1; control.max = 365; control.required = true; }
      advanced.append(field('Verificar dados a cada (dias)', check), field('Testar inicialização a cada (dias)', boot));
      const save = el('button', 'button primary', 'Salvar'); save.type = 'submit';
      form.append(advanced, save);
      form.addEventListener('submit', async event => {
        event.preventDefault(); save.disabled = true;
        try {
          await operation('backup_policy', { revision: policy.revision, policy: { enabled: enabled.value === 'true', interval_minutes: Number(intervalInput.value) * Number(unit.value), budget_gib: Number(budget.value), check_days: Number(check.value), boot_days: Number(boot.value) } });
        } catch (error) { c.toast(error.message); } finally { save.disabled = false; }
      });
      settings.append(form);
    }
    const diagnostics = el('details', 'recovery-disclosure', el('summary', '', 'Diagnóstico'));
    diagnostics.append(el('p', 'caption', data.health?.data_checked ? `Dados verificados em ${time(data.health.data_checked, true)}.` : 'Sem verificação completa registrada.'),
      el('p', 'caption', `${bytes(data.free_bytes)} livres no servidor`),
      el('div', 'recovery-actions',
        can('backup_check') && button('Verificar armazenamento', () => operation('backup_check'), 'button small'),
        data.health?.capacity_limited && can('backup_compact') && button('Liberar espaço agora', () => operation('backup_compact', { revision: policy.revision }), 'button small')));
    settings.append(diagnostics);
    drawer('BACKUPS', settings);
  }

  root.append(heading('Backups', null, button('Configurações', openSettings),
    can('backup') && button('Criar backup', () => operation('backup', { name: 'Manual' }), 'button primary', 'plus')));
  const status = !data.ready ? 'Não instalado' : overdue ? 'Rotina atrasada' : policy.enabled ? 'Automático' : 'Pausado';
  const mode = el('div', 'recovery-mode', el('div', 'recovery-mode-line', tag(status, overdue ? 'warning' : policy.enabled && data.ready ? 'good' : ''), policy.enabled && el('span', '', `A cada ${interval}`)),
    el('p', '', current ? `Último backup ${ago(current.created).toLocaleLowerCase('pt-BR')}` : 'Nenhum backup ainda'));
  if (idle) mode.append(el('small', '', 'Sem alterações desde o último backup'));
  const storage = el('div', 'recovery-storage');
  const meter = el('meter'); meter.min = 0; meter.max = policy.budget_gib * 1024 ** 3; meter.value = data.bytes || 0; meter.setAttribute('aria-label', 'Armazenamento dos backups');
  storage.append(el('div', 'recovery-storage-total', el('span', '', 'Armazenamento'), el('span', '', `${bytes(data.bytes || 0)} / ${policy.budget_gib} GiB`)), meter);
  root.append(el('div', 'recovery-summary', mode, storage));
  root.append(el('details', 'recovery-external',
    el('summary', '', el('span', '', 'Cópia externa'), tag(external.label, external.kind), external.at && el('time', 'muted', time(external.at, true))),
    el('p', 'caption', 'Confirmação do download verificado no computador do proprietário. Inclui o repositório de backups e dados de controle. Não é uma imagem completa da VM.')));
  if (active) root.append(button(active.label + ' · ' + (active.step || JOB_STATES[active.state]), () => showJob(active.id), 'recovery-running', 'activity'));
  if (data.recovery_pending && can('recover_restore')) root.append(button('Reverter restauração interrompida', () => operation('recover_restore'), 'button danger'));
  if (data.health?.check_failed) root.append(el('p', 'notice', 'A verificação do armazenamento falhou. Consulte o diagnóstico nas configurações.'));
  if (data.health?.capacity_limited) root.append(el('p', 'notice', 'O backup mais recente excede o limite. Aumente o armazenamento nas configurações para manter o histórico.'));

  const history = el('section', 'recovery-history'); history.setAttribute('aria-label', 'Histórico de backups');
  const toolbar = el('div', 'recovery-toolbar', el('h2', '', `${points.length} ${points.length === 1 ? 'backup' : 'backups'}`));
  const list = el('div', 'recovery-list');
  function paintList() {
    const visible = points.filter(p => `${p.name} ${time(p.created, true)}`.toLocaleLowerCase('pt-BR').includes(query.toLocaleLowerCase('pt-BR')));
    list.replaceChildren();
    const today = new Date(); const yesterday = new Date(); yesterday.setDate(today.getDate() - 1);
    let group = '';
    for (const p of visible) {
      const date = new Date(p.created * 1000), day = date.toDateString();
      if (day !== group) {
        group = day;
        list.append(el('h3', 'recovery-day', day === today.toDateString() ? 'Hoje' : day === yesterday.toDateString() ? 'Ontem' : date.toLocaleDateString('pt-BR', { day: 'numeric', month: 'long', ...(date.getFullYear() !== today.getFullYear() ? { year: 'numeric' } : {}) })));
      }
      const item = button('', () => openBackup(p), 'recovery-point');
      const label = ['Manual', 'Automático'].includes(p.name) ? '' : p.name;
      item.append(el('strong', 'recovery-point-time', time(p.created)), el('span', 'recovery-point-name', label),
        p.verification_failed ? tag('Verificação falhou', 'warning') : p === current ? tag('Mais recente') : el('span'),
        el('span', 'recovery-point-arrow', '›'));
      item.setAttribute('aria-label', `Backup de ${time(p.created, true)}${label ? ', ' + label : ''}. Abrir detalhes`);
      list.append(item);
    }
    if (!visible.length) list.append(el('p', 'recovery-empty', points.length ? 'Nenhum backup encontrado.' : 'Os backups aparecerão aqui. Você também pode criar um agora.'));
  }
  if (points.length > 10 || query) {
    const search = input(query, 'search'); search.placeholder = 'Buscar backups'; search.setAttribute('aria-label', 'Buscar backups');
    search.addEventListener('input', () => { query = search.value; paintList(); }); toolbar.append(search);
  }
  history.append(toolbar, list); root.append(history); paintList();
  if (data.jobs.length) {
    const activity = el('details', 'recovery-disclosure recovery-activity', el('summary', '', 'Atividade recente'));
    for (const job of data.jobs.slice(0, 8)) activity.append(button(`${job.label} · ${JOB_STATES[job.state]} · ${time(job.created, true)}`, () => showJob(job.id), 'recovery-job'));
    root.append(activity);
  }
  document.querySelector('#view').replaceChildren(root);
}
