import { dateKey, shiftDate, rangeWindow, shardKeys, normalizeIndex, buildTimeline, groupSessions } from './activity-model.mjs?v=2';

const el = id => document.getElementById(id);
const node = (tag, className, text) => {
  const value = document.createElement(tag);
  if (className) value.className = className;
  if (text !== undefined) value.textContent = text;
  return value;
};
const clockFormat = new Intl.DateTimeFormat('pt-BR', { timeZone: 'America/Sao_Paulo', hour: '2-digit', minute: '2-digit' });
const dateFormat = new Intl.DateTimeFormat('pt-BR', { timeZone: 'America/Sao_Paulo', day: 'numeric', month: 'long' });
const shortDate = new Intl.DateTimeFormat('pt-BR', { timeZone: 'America/Sao_Paulo', day: '2-digit', month: '2-digit' });
const weekday = new Intl.DateTimeFormat('pt-BR', { timeZone: 'America/Sao_Paulo', weekday: 'short', day: '2-digit' });
const dayName = new Intl.DateTimeFormat('pt-BR', { timeZone: 'America/Sao_Paulo', weekday: 'short' });
const compact = matchMedia('(max-width: 600px)');
const params = new URLSearchParams(location.search);
let day = dateKey(), range = params.get('period') === 'week' ? 'week' : params.has('date') ? 'day' : 'recent';
try { if (params.has('date')) { rangeWindow(params.get('date')); day = params.get('date') > day ? day : params.get('date'); } } catch { /* Ignore malformed share links. */ }
if (range !== 'week' && day === dateKey()) range = 'recent';
let followToday = day === dateKey(), ribbonEnd = day < shiftDate(dateKey(), -6) ? day : dateKey();
let firstDay = shiftDate(dateKey(), -89), ribbonKey = '';
let manifest, timeline, selected, generation = 0, controller, timer, rowsLimit = 40;
let fingerprint = '', lastUpdated = 0;
const shardCache = new Map(), skinCache = new Map(), skinQueue = [];
let activeSkins = 0;
const duration = seconds => {
  if (seconds === 0) return '0 min';
  if (seconds < 60) return '< 1 min';
  const minutes = Math.floor(seconds / 60), hours = Math.floor(minutes / 60);
  return hours ? `${hours} h${minutes % 60 ? ` ${minutes % 60} min` : ''}` : `${minutes} min`;
};
const time = seconds => clockFormat.format(seconds * 1000);
const fullTime = seconds => `${shortDate.format(seconds * 1000)}, ${time(seconds)}`;
const fallbackColor = id => {
  let hash = 0;
  for (const char of id) hash = (Math.imul(hash, 31) + char.codePointAt(0)) | 0;
  return `oklch(76% .09 ${[145, 78, 235, 35, 305, 185, 105][Math.abs(hash) % 7]})`;
};
const appearance = player => skinCache.get(player.skin)?.result || { color: fallbackColor(player.id) };

function avatar(player) {
  const face = node('span', 'player-avatar', player.name.replace(/^\./, '').slice(0, 2).toUpperCase());
  face.dataset.playerId = player.id;
  face.dataset.skin = player.skin;
  face.setAttribute('aria-hidden', 'true');
  const asset = appearance(player);
  face.style.setProperty('--player-color', asset.color);
  if (asset.face) {
    const img = node('img');
    img.alt = ''; img.width = 8; img.height = 8; img.src = asset.face;
    face.append(img);
  }
  requestSkin(player);
  return face;
}

function requestSkin(player) {
  if (!/^[a-f0-9]{32,64}$/.test(player.skin)) return;
  const prior = skinCache.get(player.skin);
  if (prior && (prior.result || Date.now() - prior.at < 300000)) return;
  if (skinQueue.length >= 100) return;
  skinCache.set(player.skin, { at: Date.now() });
  skinQueue.push({ ...player });
  drainSkins();
}

function drainSkins() {
  if (document.hidden) return;
  while (activeSkins < 3 && skinQueue.length) {
    const player = skinQueue.shift();
    activeSkins++;
    const img = new Image();
    let settled = false;
    const timeout = setTimeout(() => { finish(); img.src = ''; }, 8000);
    const finish = () => {
      if (settled) return;
      settled = true; clearTimeout(timeout); activeSkins--; drainSkins();
    };
    img.onload = () => {
      if (settled) return;
      try {
        if (img.naturalWidth !== 64 || ![32, 64].includes(img.naturalHeight)) return;
        const canvas = document.createElement('canvas'); canvas.width = 8; canvas.height = 8;
        const ctx = canvas.getContext('2d', { willReadFrequently: true });
        ctx.imageSmoothingEnabled = false;
        ctx.drawImage(img, 8, 8, 8, 8, 0, 0, 8, 8);
        ctx.drawImage(img, 40, 8, 8, 8, 0, 0, 8, 8);
        const face = canvas.toDataURL('image/png');
        // A single 8×8 torso sample yields an accent without a per-frame canvas.
        ctx.clearRect(0, 0, 8, 8);
        ctx.drawImage(img, 20, 20, 8, 12, 0, 0, 8, 8);
        const pixels = ctx.getImageData(0, 0, 8, 8).data, buckets = new Map();
        for (let i = 0; i < pixels.length; i += 4) {
          const [r, g, b, a] = pixels.slice(i, i + 4);
          const high = Math.max(r, g, b), low = Math.min(r, g, b);
          if (a < 128 || high < 35 || high - low < 18) continue;
          const key = `${r >> 5},${g >> 5},${b >> 5}`;
          const bucket = buckets.get(key) || { n: 0, r: 0, g: 0, b: 0 };
          bucket.n++; bucket.r += r; bucket.g += g; bucket.b += b; buckets.set(key, bucket);
        }
        const chosen = [...buckets.values()].sort((a, b) => b.n - a.n)[0];
        let color = fallbackColor(player.id);
        if (chosen) {
          const r = chosen.r / chosen.n, g = chosen.g / chosen.n, b = chosen.b / chosen.n;
          const high = Math.max(r, g, b), delta = high - Math.min(r, g, b);
          const hue = high === r ? ((g - b) / delta) % 6 : high === g ? (b - r) / delta + 2 : (r - g) / delta + 4;
          color = `oklch(76% .095 ${(hue * 60 + 360) % 360})`;
        }
        skinCache.set(player.skin, { at: Date.now(), result: { face, color } });
        // Update appearance only; preserve focus and session selection.
        for (const root of document.querySelectorAll('[data-skin]')) {
          if (root.dataset.skin !== player.skin) continue;
          root.style.setProperty('--player-color', color);
          if (root.classList.contains('player-avatar')) {
            const head = node('img'); head.alt = ''; head.width = 8; head.height = 8; head.src = face;
            root.replaceChildren(head);
          }
        }
      } catch { /* Keep the deterministic fallback if decoding is unavailable. */ }
      finally { finish(); }
    };
    img.onerror = finish;
    img.src = `/data/activity/skins/${player.skin}.png`;
  }
  while (skinCache.size > 512) skinCache.delete(skinCache.keys().next().value);
}

function feedback(message = '', retry = false) {
  el('activity-feedback').hidden = !message;
  el('feedback-text').textContent = message;
  el('retry').hidden = !retry;
}

function syncControls() {
  if (manifest?.days.length) {
    firstDay = dateKey(Math.max(manifest.observedSince || 0, Date.parse(`${manifest.days[0]}T00:00:00Z`) / 1000));
  }
  el('previous').disabled = shiftDate(ribbonEnd, -6) <= firstDay;
  el('next').disabled = ribbonEnd >= dateKey();
  el('today').hidden = range === 'recent';
  el('week-view').setAttribute('aria-pressed', String(range === 'week'));
  const window = rangeWindow(day, range);
  el('period-title').textContent = range === 'recent' ? 'Últimas 24 horas' : range === 'week'
    ? `${shortDate.format(window.start * 1000)} a ${shortDate.format((window.end - 1) * 1000)}`
    : dateFormat.format(window.start * 1000);
  const key = [ribbonEnd, firstDay, day, range].join('/');
  if (key !== ribbonKey) {
    ribbonKey = key;
    const focusedDay = document.activeElement?.dataset?.day;
    const strip = el('day-strip'); strip.replaceChildren(); strip.classList.toggle('is-week', range === 'week');
    for (let i = -6; i <= 0; i++) {
      const date = shiftDate(ribbonEnd, i), stamp = Date.parse(`${date}T12:00:00-03:00`);
      const button = node('button', 'day-button'); button.type = 'button'; button.dataset.day = date;
      const today = date === dateKey();
      button.append(node('span', 'day-name', today ? 'Agora' : dayName.format(stamp).replace('.', '')), node('span', 'day-number', String(Number(date.slice(-2)))));
      button.setAttribute('aria-label', today ? 'Últimas 24 horas' : dateFormat.format(stamp));
      button.setAttribute('aria-pressed', String(range === 'week' || date === day));
      button.disabled = date < firstDay;
      button.onclick = () => navigate(date, today ? 'recent' : 'day');
      strip.append(button);
      if (date === focusedDay) button.focus({ preventScroll: true });
    }
  }
}

async function readJSON(path, signal, limit = 3000000) {
  const response = await fetch(path, { signal, cache: 'no-cache' });
  if (!response.ok) throw new Error(String(response.status));
  if (Number(response.headers.get('Content-Length')) > limit) throw new Error('Oversized response');
  // Bound streamed bodies as well as declared Content-Length.
  const reader = response.body.getReader(), chunks = [];
  let size = 0;
  while (true) {
    const part = await reader.read();
    if (part.done) break;
    size += part.value.byteLength;
    if (size > limit) { await reader.cancel(); throw new Error('Oversized response'); }
    chunks.push(part.value);
  }
  const bytes = new Uint8Array(size); let offset = 0;
  for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.length; }
  return JSON.parse(new TextDecoder().decode(bytes));
}

async function load({ quiet = false } = {}) {
  clearTimeout(timer);
  if (document.hidden) return;
  const current = ++generation;
  controller?.abort(); controller = new AbortController();
  const signal = controller.signal, timeout = setTimeout(() => controller?.signal === signal && controller.abort(), 12000);
  if (followToday && day !== dateKey()) {
    day = dateKey(); ribbonEnd = day; timeline = null; fingerprint = ''; closeDetail(false);
    el('player-rows').replaceChildren(); el('concurrency-plot').replaceChildren();
    el('player-count').textContent = 'Jogadores'; el('timeline-empty').hidden = true;
  }
  syncControls();
  if (!quiet) el('timeline-scroll').setAttribute('aria-busy', 'true');
  try {
    const raw = await readJSON('/data/activity/index.json', signal, 32000);
    const index = normalizeIndex(raw);
    if (!index) throw new Error('Invalid index');
    const window = rangeWindow(day, range);
    const keys = shardKeys(window.start, window.end).filter(key => index.days.includes(key));
    const oldLatest = manifest?.latestDay;
    if (oldLatest && oldLatest !== index.latestDay) shardCache.delete(oldLatest);
    const results = await Promise.allSettled(keys.map(async key => {
      const cached = shardCache.get(key);
      if (quiet && cached && Date.now() - cached.at < 300000 && key !== index.latestDay && key !== oldLatest) return cached.data;
      const data = await readJSON(`/data/activity/${key}.json`, signal);
      if (data.version !== 1 || data.day !== key || !Array.isArray(data.players) || !Array.isArray(data.coverage)) throw new Error('Invalid shard');
      shardCache.set(key, { data, at: Date.now() });
      return data;
    }));
    if (current !== generation) return;
    manifest = index;
    const failures = results.filter(result => result.status === 'rejected').length;
    const shards = results.filter(result => result.status === 'fulfilled').map(result => result.value);
    timeline = buildTimeline({ index: raw, shards, day, range });
    lastUpdated = Date.now();
    while (shardCache.size > 24) shardCache.delete(shardCache.keys().next().value);
    syncControls();
    updateFreshness();
    const nextFingerprint = JSON.stringify([day, range, shards.map(shard => [shard.day, shard.updated]), timeline.collecting]);
    if (nextFingerprint !== fingerprint || !quiet) {
      fingerprint = nextFingerprint;
      renderTimeline();
    }
    feedback(failures ? 'Alguns registros não carregaram. Este período está incompleto.' : timeline.truncated ? 'Este período excedeu o limite de registros. Os totais estão incompletos.' : '', failures > 0);
    const since = index.observedSince ? dateFormat.format(index.observedSince * 1000) : 'o início da coleta';
    el('history-note').textContent = `Registros desde ${since}, disponíveis por ${index.retentionDays} dias. Os horários são aproximados, com observações a cada ${index.sampleSeconds} segundos. Trechos hachurados não têm coleta e não entram no tempo jogado.`;
  } catch (error) {
    if (current !== generation || document.hidden) return;
    updateFreshness(true);
    if (timeline) {
      timeline.collecting = false;
      for (const player of timeline.players) player.online = false;
      renderPlayers();
    }
    feedback(timeline ? 'Sem atualização. Exibindo os últimos registros.' : 'Os registros ainda não estão disponíveis.', true);
    if (!timeline) {
      el('player-rows').replaceChildren();
      el('concurrency-row').hidden = true;
      el('player-count').textContent = 'Jogadores';
      showEmpty('Aguardando os primeiros registros', 'As próximas sessões aparecerão aqui.');
    }
  } finally {
    clearTimeout(timeout);
    if (current === generation) {
      el('timeline-scroll').setAttribute('aria-busy', 'false');
      if (!document.hidden) timer = setTimeout(() => load({ quiet: true }), 30000);
    }
  }
}

function updateFreshness(error = false) {
  const fresh = !error && timeline?.collecting && Date.now() / 1000 - timeline.updated < 90;
  const state = el('collection-state');
  state.dataset.state = error ? 'error' : 'stale';
  state.hidden = fresh;
  state.textContent = fresh ? '' : timeline?.updated ? `Atualizado às ${time(timeline.updated)}` : 'Sem registros';
}

function place(element, start, end) {
  const width = timeline.window.end - timeline.window.start;
  element.style.left = `${Math.max(0, (start - timeline.window.start) / width * 100)}%`;
  if (end !== undefined) element.style.width = `${Math.max(0, Math.min(end, timeline.window.end) - start) / width * 100}%`;
}

function decorateTrack(track) {
  // One SVG path covers all gaps, independent of player and interruption counts.
  const ns = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(ns, 'svg');
  svg.classList.add('coverage-gaps'); svg.setAttribute('viewBox', '0 0 1000 100');
  svg.setAttribute('preserveAspectRatio', 'none'); svg.setAttribute('aria-hidden', 'true');
  const path = document.createElementNS(ns, 'path');
  path.setAttribute('d', timeline.gaps.map(([start, end]) => {
    const x = (start - timeline.window.start) / (timeline.window.end - timeline.window.start) * 1000;
    const width = (end - start) / (timeline.window.end - timeline.window.start) * 1000;
    return `M${x.toFixed(3)} 0h${width.toFixed(3)}v100h-${width.toFixed(3)}z`;
  }).join(''));
  path.setAttribute('fill', 'url(#gap-pattern)'); svg.append(path); track.append(svg);
  if (timeline.window.effectiveEnd > timeline.window.start && timeline.window.effectiveEnd < timeline.window.end) {
    const marker = node('span', 'now-marker'); marker.setAttribute('aria-hidden', 'true');
    place(marker, timeline.window.effectiveEnd); track.append(marker);
  }
}

function showEmpty(title, description) {
  el('timeline-empty').hidden = false;
  el('empty-title').textContent = title;
  el('empty-description').textContent = description || '';
  el('empty-description').hidden = !description;
}

function renderAxis() {
  if (!timeline) return;
  const width = timeline.window.end - timeline.window.start;
  const interval = range === 'week' ? 86400 * (compact.matches ? 2 : 1) : (compact.matches ? 6 : 3) * 3600;
  const base = range === 'recent' ? Math.ceil(timeline.window.start / interval) * interval : timeline.window.start;
  const steps = width / interval;
  el('timeline-grid').style.setProperty('--chart-steps', steps);
  // CSS background percentages refer to the leftover space after one grid tile.
  el('timeline-grid').style.setProperty('--grid-offset', `${(base - timeline.window.start) / (width - interval) * 100}%`);
  const axis = el('time-axis'); axis.replaceChildren();
  for (let stamp = base; stamp <= timeline.window.end; stamp += interval) {
    const ratio = (stamp - timeline.window.start) / width;
    if (range === 'recent' && ratio > .86) continue;
    if (range === 'week' && ratio >= .99) continue;
    const label = range === 'week' ? compact.matches ? shortDate.format(stamp * 1000) : weekday.format(stamp * 1000).replace('.', '') : ratio >= .99 ? '24h' : time(stamp).replace(':00', 'h');
    const tick = node('span', `axis-tick${ratio < .03 ? ' is-first' : ratio > .97 ? ' is-last' : ''}`, label);
    tick.style.left = `${ratio * 100}%`; axis.append(tick);
  }
  if (range === 'recent') axis.append(node('span', 'axis-now', 'Agora'));
}

function renderTimeline() {
  renderAxis();
  el('concurrency-row').hidden = timeline.players.length === 0;
  el('player-count').textContent = `${timeline.players.length} ${timeline.players.length === 1 ? 'jogador' : 'jogadores'}`;
  el('peak-caption').textContent = timeline.peak > 1 ? `${timeline.peak} juntos no pico` : '';
  el('gap-legend').hidden = !timeline.gaps.some(([start, end]) => end - start > 90);
  const plot = el('concurrency-plot'); plot.replaceChildren(); decorateTrack(plot);
  for (const bin of timeline.bins) {
    const bar = node('span', 'concurrency-bin');
    bar.style.setProperty('--height', `${bin.peak / Math.max(1, timeline.peak) * 100}%`);
    bar.title = `${fullTime(bin.start)}: pico de ${bin.peak} ${bin.peak === 1 ? 'jogador' : 'jogadores'}${bin.coverageSeconds < bin.end - bin.start ? ' (coleta parcial)' : ''}`;
    if (!bin.peak) bar.dataset.empty = '';
    if (!bin.coverageSeconds && !bin.peak) bar.dataset.unknown = '';
    plot.append(bar);
  }
  renderPlayers();
}

function renderPlayers() {
  if (!timeline) return;
  const focused = document.activeElement?.dataset?.sessionKey;
  const players = timeline.players;
  const fragment = document.createDocumentFragment();
  for (const player of players.slice(0, rowsLimit)) {
    const row = node('div', 'player-row'); row.dataset.playerId = player.id;
    row.dataset.skin = player.skin;
    row.style.setProperty('--player-color', appearance(player).color);
    const label = node('button', 'player-label'); label.type = 'button'; label.append(avatar(player));
    label.setAttribute('aria-label', `Ver todas as sessões de ${player.name}`);
    label.dataset.sessionKey = `${player.id}/all`;
    label.setAttribute('aria-pressed', String(selected?.key === label.dataset.sessionKey));
    label.onclick = () => selectSession(player, allSessions(player), label, true);
    const identity = node('div', 'player-identity');
    const name = node('span', 'player-name', player.name); name.title = player.name;
    const total = node('span', 'player-time', duration(player.totalSeconds));
    if (player.online) { const dot = node('i', 'online-dot'); dot.setAttribute('aria-hidden', 'true'); total.append(dot); label.setAttribute('aria-label', `${player.name}, no jogo. Ver sessões`); }
    identity.append(name, total); label.append(identity);
    const track = node('div', 'player-track');
    const groups = groupSessions(player.sessions, timeline.window.start, timeline.window.end, range === 'week' ? 96 : 120);
    for (const [index, group] of groups.entries()) {
      const open = player.online && group.sessions.some(session => session.open);
      const button = node('button', `session-bar${open ? ' is-open' : ''}${group.clustered ? ' is-group' : ''}`);
      button.type = 'button'; button.tabIndex = index === 0 ? 0 : -1;
      button.dataset.sessionKey = `${player.id}/${group.sessions[0].id}/${group.start}`;
      const description = `${player.name}, ${group.clustered ? `${group.count} sessões próximas, ` : ''}${fullTime(group.start)} até ${time(group.end)}, ${duration(group.totalSeconds)}${open ? ', no jogo' : ''}`;
      button.setAttribute('aria-label', description); button.title = description;
      button.setAttribute('aria-pressed', String(selected?.key === button.dataset.sessionKey));
      place(button, group.start, group.end);
      // Labels appear only where they fit. The full value is always accessible.
      const percent = (group.end - group.start) / (timeline.window.end - timeline.window.start);
      if (percent > .045) {
        const content = node('span', 'bar-content'); content.append(avatar(player));
        if (percent > .13) content.append(node('span', 'bar-duration', group.clustered ? `${group.count} sessões` : duration(group.totalSeconds)));
        button.append(content);
      }
      button.onclick = () => selectSession(player, group, button);
      track.append(button);
    }
    row.append(label, track); fragment.append(row);
  }
  const overlay = node('div', 'timeline-overlay'); decorateTrack(overlay); fragment.append(overlay);
  el('player-rows').replaceChildren(fragment);
  el('timeline-empty').hidden = players.length > 0;
  if (!players.length) {
    showEmpty(timeline.coverage.length ? 'Ninguém passou por aqui' : 'Sem registros neste período',
      timeline.coverage.length ? '' : 'A coleta ainda não estava disponível.');
  }
  el('more-players').hidden = players.length <= rowsLimit;
  if (selected) {
    const player = timeline.players.find(value => value.id === selected.player.id);
    const group = player && (selected.all ? allSessions(player) : groupSessions(player.sessions, timeline.window.start, timeline.window.end, range === 'week' ? 96 : 120).find(value => `${player.id}/${value.sessions[0].id}/${value.start}` === selected.key));
    if (group) { selected.player = player; selected.group = group; renderDetail(); }
    else closeDetail(false);
  }
  if (focused) {
    for (const button of el('player-rows').querySelectorAll('button')) {
      if (button.dataset.sessionKey === focused) {
        if (button.classList.contains('session-bar')) for (const sibling of button.parentElement.querySelectorAll('.session-bar')) sibling.tabIndex = -1;
        button.tabIndex = 0; button.focus({ preventScroll: true }); break;
      }
    }
  }
}

function allSessions(player) {
  return { start: player.sessions[0].start, end: player.sessions.at(-1).end, sessions: player.sessions, count: player.sessions.length, totalSeconds: player.totalSeconds, clustered: player.sessions.length > 1 };
}

function selectSession(player, group, button, all = false) {
  for (const previous of el('player-rows').querySelectorAll('[aria-pressed="true"]')) previous.setAttribute('aria-pressed', 'false');
  selected = { player, group, key: button.dataset.sessionKey, all, limit: 30 };
  button.setAttribute('aria-pressed', 'true');
  renderDetail();
  el('session-detail').focus({ preventScroll: true });
  el('session-detail').scrollIntoView({ block: 'nearest', behavior: 'instant' });
}

function renderDetail() {
  if (!selected) return;
  const { player, group } = selected;
  el('session-detail').hidden = false;
  const identity = el('detail-identity'); identity.replaceChildren(avatar(player));
  const title = node('div'); const h = node('h3', '', player.name); h.id = 'detail-title';
  title.append(h, node('p', 'detail-subtitle', group.count > 1 ? `${group.count} sessões · ${duration(group.totalSeconds)}` : duration(group.totalSeconds))); identity.append(title);
  const content = el('detail-content'); content.replaceChildren();
  const facts = node('dl', 'detail-facts');
  const first = group.sessions[0], last = group.sessions.at(-1);
  for (const [term, value] of [
    [first.continuedBefore ? 'Desde antes de' : 'Entrada', fullTime(group.start)],
    [player.online && last.open ? 'No jogo até' : last.endReason === 'unknown' || last.open ? 'Último registro' : last.continuesAfter ? 'Continua após' : 'Saída', fullTime(group.end)]
  ]) { const pair = node('div'); pair.append(node('dt', '', term), node('dd', '', value)); facts.append(pair); }
  content.append(facts);
  if (group.clustered) {
    if (!selected.all) content.append(node('p', 'detail-note', 'Sessões próximas, agrupadas. As pausas não entram na duração.'));
    const list = node('ul', 'detail-list');
    for (const session of group.sessions.slice(0, selected.limit)) list.append(node('li', '', `${range === 'day' ? time(session.start) : fullTime(session.start)} → ${dateKey(session.start) === dateKey(session.end) ? time(session.end) : fullTime(session.end)} · ${duration(session.end - session.start)}${session.endReason === 'unknown' ? ' · sem coleta após' : ''}`));
    content.append(list);
    if (group.count > selected.limit) {
      const more = node('button', 'detail-more', 'Mostrar mais sessões'); more.type = 'button';
      more.onclick = () => { selected.limit += 30; renderDetail(); el('session-detail').focus({ preventScroll: true }); };
      content.append(more);
    }
  } else if (last.endReason === 'unknown' || (last.open && !player.online)) {
    content.append(node('p', 'detail-note', 'A coleta foi interrompida. A saída não foi confirmada.'));
  } else if (first.continuedBefore || last.continuesAfter) {
    content.append(node('p', 'detail-note', 'Duração apenas do trecho visível.'));
  }
}

function closeDetail(restore = true) {
  const key = selected?.key;
  selected = null; el('session-detail').hidden = true;
  for (const button of el('player-rows').querySelectorAll('button')) {
    button.setAttribute('aria-pressed', 'false');
    if (restore && button.dataset.sessionKey === key) button.focus({ preventScroll: true });
  }
}

function navigate(nextDay = day, nextRange = range) {
  if (nextDay > dateKey()) nextDay = dateKey();
  if (nextDay < firstDay) nextDay = firstDay;
  day = nextDay; range = nextRange; followToday = day === dateKey();
  if (range === 'recent' && !followToday) range = 'day';
  if (day > ribbonEnd || day < shiftDate(ribbonEnd, -6)) ribbonEnd = day;
  rowsLimit = 40; closeDetail(false); fingerprint = '';
  const url = new URL(location.href);
  if (range === 'recent') url.searchParams.delete('date'); else url.searchParams.set('date', day);
  if (range === 'week') url.searchParams.set('period', 'week'); else url.searchParams.delete('period');
  history.replaceState(null, '', url);
  // Never show the previous period under the newly selected date.
  timeline = null; el('player-rows').replaceChildren(); el('player-count').textContent = 'Jogadores'; el('peak-caption').textContent = '';
  el('timeline-empty').hidden = true; el('concurrency-plot').replaceChildren();
  el('time-axis').replaceChildren(); el('gap-legend').hidden = true;
  load();
}

el('week-view').onclick = () => navigate(ribbonEnd, range === 'week' ? ribbonEnd === dateKey() ? 'recent' : 'day' : 'week');
el('previous').onclick = () => { ribbonEnd = shiftDate(ribbonEnd, -7); navigate(ribbonEnd, 'week'); };
el('next').onclick = () => { ribbonEnd = [shiftDate(ribbonEnd, 7), dateKey()].sort()[0]; navigate(ribbonEnd, 'week'); };
el('today').onclick = () => { ribbonEnd = dateKey(); navigate(ribbonEnd, 'recent'); };
compact.addEventListener('change', renderAxis);
el('day-strip').addEventListener('keydown', event => {
  if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
  const buttons = [...el('day-strip').querySelectorAll('button:not(:disabled)')];
  const index = buttons.indexOf(event.target);
  if (index < 0) return;
  const target = event.key === 'Home' ? buttons[0] : event.key === 'End' ? buttons.at(-1) : buttons[index + (event.key === 'ArrowRight' ? 1 : -1)];
  event.preventDefault(); target?.focus();
});
document.addEventListener('click', event => {
  const info = document.querySelector('.history-info');
  if (!info.contains(event.target)) info.open = false;
});
document.querySelector('.history-info').onkeydown = event => {
  if (event.key === 'Escape') { event.currentTarget.open = false; event.currentTarget.querySelector('summary').focus(); }
};
el('more-players').onclick = () => { rowsLimit += 40; renderPlayers(); };
el('close-detail').onclick = () => closeDetail();
el('retry').onclick = () => load();
document.addEventListener('keydown', event => {
  if (event.key === 'Escape' && selected) { closeDetail(); return; }
  if (!event.target.matches('.session-bar') || !['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Home', 'End'].includes(event.key)) return;
  const buttons = [...event.target.parentElement.querySelectorAll('.session-bar')];
  let target;
  if (event.key === 'Home') target = buttons[0];
  else if (event.key === 'End') target = buttons.at(-1);
  else if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') target = buttons[buttons.indexOf(event.target) + (event.key === 'ArrowRight' ? 1 : -1)];
  else {
    const row = event.target.closest('.player-row');
    target = (event.key === 'ArrowDown' ? row.nextElementSibling : row.previousElementSibling)?.querySelector('.session-bar');
  }
  event.preventDefault();
  if (target) { event.target.tabIndex = -1; target.tabIndex = 0; target.focus(); }
});
document.addEventListener('visibilitychange', () => {
  if (document.hidden) { clearTimeout(timer); controller?.abort(); }
  else { drainSkins(); if (Date.now() - lastUpdated > 15000) load({ quiet: true }); else timer = setTimeout(() => load({ quiet: true }), 15000); }
});
window.addEventListener('pagehide', () => { clearTimeout(timer); controller?.abort(); });
window.addEventListener('pageshow', event => { if (event.persisted) load({ quiet: true }); });
load();
