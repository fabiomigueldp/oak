/* Pure, bounded timeline calculations shared by the browser and Node tests. */
const DAY = 86400;
const OFFSET = 3 * 3600;
const MAX_TIME = 253402300799;
const MAX_SHARDS = 96;
const MAX_PLAYERS = 2048;
const MAX_SESSIONS = 50000;
const MAX_PLAYER_SESSIONS = 4096;
const MAX_COVERAGE = 20000;
const MAX_BINS = 192;
const UUID = /^[0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{12}$/i;
const collator = new Intl.Collator("pt-BR", { sensitivity: "base", numeric: true });

function timestamp(value) {
  return Number.isSafeInteger(value) && value >= 0 && value <= MAX_TIME;
}

function nowValue(value) {
  if (!Number.isFinite(value) || value < 0 || value > MAX_TIME) {
    throw new RangeError("Invalid current timestamp");
  }
  return Math.floor(value);
}

function dayStart(day) {
  if (typeof day !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(day)) return null;
  const value = Date.parse(`${day}T00:00:00Z`);
  if (!Number.isFinite(value) || new Date(value).toISOString().slice(0, 10) !== day) return null;
  return value / 1000;
}

function integer(value, fallback, min, max) {
  return Number.isSafeInteger(value) ? Math.min(max, Math.max(min, value)) : fallback;
}

function utcKey(value) {
  return new Date(value * 1000).toISOString().slice(0, 10);
}

/** A calendar date in Brasília (UTC−03), independent of the browser timezone. */
export function dateKey(nowSeconds = Date.now() / 1000) {
  return utcKey(nowValue(nowSeconds) - OFFSET);
}

export function shiftDate(day, deltaDays) {
  const start = dayStart(day);
  if (start === null || !Number.isSafeInteger(deltaDays) || Math.abs(deltaDays) > 36600) {
    throw new RangeError("Invalid calendar date or offset");
  }
  const result = utcKey(start + deltaDays * DAY);
  if (dayStart(result) === null) throw new RangeError("Calendar date is out of range");
  return result;
}

/** Calendar views or the preceding 24 hours, including the current observation. */
export function rangeWindow(day, range = "day", now = Date.now() / 1000) {
  const midnight = dayStart(day);
  if (midnight === null || !["day", "week", "recent"].includes(range)) {
    throw new RangeError("Invalid timeline window");
  }
  if (range === "recent") {
    now = nowValue(now);
    if (now < DAY || now >= MAX_TIME) throw new RangeError("Timeline window is out of range");
    return { day: dateKey(now), range, start: now - DAY, end: now + 1 };
  }
  const end = midnight + OFFSET + DAY;
  const start = end - (range === "week" ? 7 : 1) * DAY;
  if (start < 0 || end > MAX_TIME) throw new RangeError("Timeline window is out of range");
  return { day, range, start, end };
}

/** UTC files covering a half-open window; the ending midnight is not included. */
export function shardKeys(start, end) {
  if (!timestamp(start) || !timestamp(end) || end < start || end - start > 90 * DAY) {
    throw new RangeError("Invalid shard window");
  }
  const keys = [];
  for (let cursor = Math.floor(start / DAY) * DAY; cursor < end; cursor += DAY) {
    if (end > start) keys.push(utcKey(cursor));
  }
  return keys;
}

/** Reject an invalid manifest; sanitize optional values and bounded file keys. */
export function normalizeIndex(raw, nowSeconds = Date.now() / 1000) {
  const now = nowValue(nowSeconds);
  if (!raw || typeof raw !== "object" || raw.version !== 1 ||
      !timestamp(raw.updated) || !Array.isArray(raw.days)) return null;
  const retentionDays = integer(raw.retentionDays, 90, 1, 90);
  const oldest = Math.floor(now / DAY) * DAY - retentionDays * DAY;
  const days = [...new Set(raw.days.slice(0, MAX_SHARDS).filter((day) => {
    const start = dayStart(day);
    return start !== null && start >= oldest && start <= now;
  }))].sort();
  const updated = Math.min(now, raw.updated);
  const sampleSeconds = integer(raw.sampleSeconds, 5, 1, 300);
  const gapSeconds = integer(raw.gapSeconds, 20, sampleSeconds, 3600);
  const freshSeconds = Math.max(90, sampleSeconds * 3);
  return {
    version: 1, updated,
    observedSince: timestamp(raw.observedSince) && raw.observedSince <= updated ? raw.observedSince : null,
    sampleSeconds, gapSeconds, retentionDays,
    latestDay: days.includes(raw.latestDay) ? raw.latestDay : days.at(-1) || null,
    days,
    collecting: raw.collecting === true && raw.updated <= now + gapSeconds && now - updated <= freshSeconds,
  };
}

function union(intervals) {
  const ordered = intervals.filter(([start, end]) => end > start)
    .map(([start, end]) => [start, end]).sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  const merged = [];
  for (const interval of ordered) {
    const previous = merged.at(-1);
    if (previous && interval[0] <= previous[1]) previous[1] = Math.max(previous[1], interval[1]);
    else merged.push(interval);
  }
  return merged;
}

function* intersect(start, end, coverage) {
  // Binary search avoids scanning all historical gaps for every session.
  let low = 0;
  let high = coverage.length;
  while (low < high) {
    const middle = (low + high) >>> 1;
    if (coverage[middle][1] <= start) low = middle + 1;
    else high = middle;
  }
  for (let index = low; index < coverage.length && coverage[index][0] < end; index += 1) {
    const left = Math.max(start, coverage[index][0]);
    const right = Math.min(end, coverage[index][1]);
    if (right > left) yield [left, right];
  }
}

function coveredPoint(time, coverage, points) {
  if (points.has(time)) return true;
  let low = 0;
  let high = coverage.length;
  while (low < high) {
    const middle = (low + high) >>> 1;
    if (coverage[middle][1] < time) low = middle + 1;
    else high = middle;
  }
  return low < coverage.length && coverage[low][0] <= time;
}

function metadata(raw) {
  if (!raw || typeof raw !== "object" || typeof raw.id !== "string" || raw.id.length > 100 ||
      typeof raw.name !== "string" || !Array.isArray(raw.sessions)) return null;
  if (!(raw.id.startsWith("name:") && raw.id.length > 5) &&
      !(raw.id.startsWith("uuid:") && UUID.test(raw.id.slice(5)))) return null;
  const name = raw.name.replace(/[\u0000-\u001f\u007f]/g, "").trim().slice(0, 64);
  if (!name) return null;
  return {
    id: raw.id,
    uuid: typeof raw.uuid === "string" && UUID.test(raw.uuid) ? raw.uuid.toLowerCase() : null,
    name,
    skin: typeof raw.skin === "string" && /^[0-9a-f]{32,128}$/i.test(raw.skin) ? raw.skin.toLowerCase() : "",
  };
}

function mergeSessions(sessions) {
  sessions.sort((a, b) => a.id - b.id || a.start - b.start || a.end - b.end);
  const merged = [];
  for (const session of sessions) {
    const previous = merged.at(-1);
    if (!previous || previous.id !== session.id || session.start > previous.end) {
      merged.push({ ...session });
      continue;
    }
    if (session.start === previous.start) previous.continuedBefore ||= session.continuedBefore;
    if (session.end > previous.end) {
      previous.end = session.end;
      previous.open = session.open;
      previous.continuesAfter = session.continuesAfter;
      previous.endReason = session.endReason;
    } else if (session.end === previous.end) {
      previous.open ||= session.open;
      previous.continuesAfter ||= session.continuesAfter;
      previous.endReason ||= session.endReason;
    }
  }
  return merged.sort((a, b) => a.start - b.start || a.end - b.end || a.id - b.id);
}

function histogram(intervals, coverage, start, end, requestedBins, points) {
  const count = integer(requestedBins, 96, 1, MAX_BINS);
  const size = (end - start) / count;
  const bins = Array.from({ length: count }, (_, index) => ({
    start: start + index * size, end: start + (index + 1) * size,
    peak: 0, playerSeconds: 0, coverageSeconds: 0,
  }));
  const addDuration = (left, right, property, multiplier = 1, peak = 0) => {
    const first = Math.max(0, Math.floor((left - start) / size));
    const last = Math.min(count - 1, Math.ceil((right - start) / size) - 1);
    for (let index = first; index <= last; index += 1) {
      const overlap = Math.max(0, Math.min(right, bins[index].end) - Math.max(left, bins[index].start));
      bins[index][property] += overlap * multiplier;
      if (overlap > 0) bins[index].peak = Math.max(bins[index].peak, peak);
    }
  };
  for (const [left, right] of coverage) addDuration(left, right, "coverageSeconds");
  const events = [...intervals.flatMap(([left, right]) => [[left, 1, 0], [right, -1, 0]]),
    ...points.map((time) => [time, 0, 1])]
    .sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  let active = 0;
  let peak = 0;
  let previous = start;
  for (let index = 0; index < events.length;) {
    const time = events[index][0];
    if (time > previous && active > 0) addDuration(previous, time, "playerSeconds", active, active);
    let pointCount = 0;
    while (index < events.length && events[index][0] === time) {
      active += events[index][1];
      pointCount += events[index][2];
      index += 1;
    }
    peak = Math.max(peak, active + pointCount);
    if (pointCount > 0 && time < end) {
      const bin = bins[Math.max(0, Math.min(count - 1, Math.floor((time - start) / size)))];
      bin.peak = Math.max(bin.peak, active + pointCount);
    }
    previous = time;
  }
  return { bins, peak };
}

/** Merge shard fragments, retaining only observed time and exact player totals. */
export function buildTimeline({ index, shards = [], day, range = "day", now = Date.now() / 1000, bins = 96 } = {}) {
  now = nowValue(now);
  const window = rangeWindow(day || dateKey(now), range, now);
  window.effectiveEnd = Math.max(window.start, Math.min(window.end, now));
  const manifest = normalizeIndex(index, now);
  const cutoff = Math.min(window.effectiveEnd, manifest?.updated ?? window.start);
  const wanted = new Set(shardKeys(window.start, window.end));
  const selected = new Map();
  let truncated = Array.isArray(shards) && shards.length > MAX_SHARDS;
  for (const raw of manifest && Array.isArray(shards) ? shards.slice(0, MAX_SHARDS) : []) {
    if (!raw || raw.version !== 1 || !wanted.has(raw.day) || !timestamp(raw.updated) ||
        !timestamp(raw.start) || !timestamp(raw.end) || raw.end < raw.start ||
        !Array.isArray(raw.coverage) || !Array.isArray(raw.players)) continue;
    const utcStart = dayStart(raw.day);
    if (raw.start < utcStart || raw.end > utcStart + DAY) continue;
    if (!selected.has(raw.day) || selected.get(raw.day).updated < raw.updated) selected.set(raw.day, raw);
  }
  const collectedCoverage = [];
  const playersById = new Map();
  let sessionCount = 0;
  let fragmentCount = 0;
  for (const raw of selected.values()) {
    const start = Math.max(window.start, raw.start);
    const end = Math.min(cutoff, raw.end, raw.updated);
    if (end < start) continue;
    truncated ||= raw.truncated === true || raw.coverage.length > MAX_COVERAGE || raw.players.length > MAX_PLAYERS;
    const validCoverage = raw.coverage.slice(0, MAX_COVERAGE).filter((pair) =>
      Array.isArray(pair) && timestamp(pair[0]) && timestamp(pair[1]) && pair[1] >= pair[0])
      .map(([left, right]) => [Math.max(start, left), Math.min(end, right)]);
    const coverage = union(validCoverage);
    const coveragePoints = new Set(validCoverage.filter(([left, right]) => left === right).map(([left]) => left));
    collectedCoverage.push(...coverage);
    for (const rawPlayer of raw.players.slice(0, MAX_PLAYERS)) {
      const meta = metadata(rawPlayer);
      if (!meta) continue;
      let player = playersById.get(meta.id);
      if (!player && playersById.size >= MAX_PLAYERS) { truncated = true; continue; }
      if (!player) {
        player = { ...meta, sessions: [], metadataUpdated: raw.updated };
        playersById.set(meta.id, player);
      } else if (raw.updated >= player.metadataUpdated) {
        Object.assign(player, meta, { metadataUpdated: raw.updated });
      }
      truncated ||= rawPlayer.sessions.length > MAX_PLAYER_SESSIONS;
      for (const session of rawPlayer.sessions.slice(0, MAX_PLAYER_SESSIONS)) {
        if (sessionCount >= MAX_SESSIONS || fragmentCount >= MAX_SESSIONS ||
            player.sessions.length >= MAX_PLAYER_SESSIONS) { truncated = true; break; }
        sessionCount += 1;
        if (!session || !Number.isSafeInteger(session.id) || session.id < 0 ||
            !timestamp(session.start) || !timestamp(session.end) || session.end < session.start) continue;
        const left = Math.max(start, session.start);
        const right = Math.min(end, session.end);
        const isPoint = left === right && (session.start === session.end || session.open === true) && left < window.end &&
          left >= window.start && coveredPoint(left, coverage, coveragePoints);
        const pieces = isPoint ? [[left, right]] : intersect(left, right, coverage);
        for (const [pieceStart, pieceEnd] of pieces) {
          if (player.sessions.length >= MAX_PLAYER_SESSIONS || fragmentCount >= MAX_SESSIONS) { truncated = true; break; }
          fragmentCount += 1;
          player.sessions.push({
            id: session.id, start: pieceStart, end: pieceEnd,
            open: session.open === true && pieceEnd === right && right >= (manifest?.updated ?? now) - (manifest?.gapSeconds ?? 20),
            continuedBefore: session.continuedBefore === true || pieceStart > session.start,
            continuesAfter: session.continuesAfter === true || pieceEnd < session.end,
            endReason: pieceEnd === session.end && (session.endReason === "left" || session.endReason === "unknown") ? session.endReason : null,
          });
        }
      }
    }
  }
  const coverage = union(collectedCoverage);
  const gaps = [];
  let cursor = window.start;
  for (const [start, end] of coverage) {
    if (cursor < start) gaps.push([cursor, start]);
    cursor = Math.max(cursor, end);
  }
  if (cursor < window.effectiveEnd) gaps.push([cursor, window.effectiveEnd]);
  const allIntervals = [];
  const allPoints = [];
  const players = [];
  const currentWindow = now >= window.start && now < window.end;
  for (const player of playersById.values()) {
    const sessions = mergeSessions(player.sessions);
    if (!sessions.length) continue;
    const intervals = union(sessions.map(({ start, end }) => [start, end]));
    const points = [...new Set(sessions.filter((session) => session.start === session.end).map((session) => session.start))];
    let intervalIndex = 0;
    for (const point of points) {
      while (intervalIndex < intervals.length && intervals[intervalIndex][1] <= point) intervalIndex += 1;
      if (intervalIndex === intervals.length || intervals[intervalIndex][0] > point) allPoints.push(point);
    }
    allIntervals.push(...intervals);
    const online = !!manifest?.collecting && currentWindow && sessions.some((session) => session.open &&
      session.end >= manifest.updated - manifest.gapSeconds);
    const { metadataUpdated, ...result } = player;
    players.push({ ...result, sessions,
      totalSeconds: intervals.reduce((total, [start, end]) => total + end - start, 0),
      online, lastSeen: Math.max(...sessions.map((session) => session.end)),
    });
  }
  const overview = histogram(allIntervals, coverage, window.start, window.end, bins, allPoints);
  return {
    window, players: filterPlayers(players), coverage, gaps, bins: overview.bins,
    totalSeconds: players.reduce((total, player) => total + player.totalSeconds, 0),
    peak: overview.peak, onlineCount: players.filter((player) => player.online).length,
    updated: manifest?.updated ?? null, observedSince: manifest?.observedSince ?? null,
    collecting: !!manifest?.collecting, truncated,
  };
}

export function filterPlayers(players, query = "", sort = "recent") {
  const needle = typeof query === "string" ? query.trim().toLocaleLowerCase("pt-BR").slice(0, 100) : "";
  return (Array.isArray(players) ? players : []).filter((player) =>
    !needle || player.name.toLocaleLowerCase("pt-BR").includes(needle)).sort((a, b) => {
    if (sort === "total") return b.totalSeconds - a.totalSeconds || collator.compare(a.name, b.name);
    if (sort === "name") return collator.compare(a.name, b.name);
    return Number(b.online) - Number(a.online) || b.lastSeen - a.lastSeen || collator.compare(a.name, b.name);
  });
}

/** Display envelopes only: totalSeconds and originals never include their gaps. */
export function groupSessions(sessions, start, end, maxGroups = 120) {
  if (!timestamp(start) || !timestamp(end) || end <= start) return [];
  const limit = integer(maxGroups, 120, 1, 512);
  const groups = new Map();
  const width = (end - start) / limit;
  for (const session of (Array.isArray(sessions) ? sessions : []).slice(0, MAX_PLAYER_SESSIONS)) {
    if (!session || !timestamp(session.start) || !timestamp(session.end) || session.end < session.start) continue;
    const left = Math.max(start, session.start);
    const right = Math.min(end, session.end);
    if (right < left || left >= end || right < start) continue;
    const bucket = Math.min(limit - 1, Math.floor((left - start) / width));
    let group = groups.get(bucket);
    if (!group) {
      group = { start: left, end: right, sessions: [], totalSeconds: 0, count: 0, clustered: false };
      groups.set(bucket, group);
    }
    group.start = Math.min(group.start, left);
    group.end = Math.max(group.end, right);
    group.sessions.push(session);
    group.totalSeconds += right - left;
    group.count += 1;
    group.clustered = group.count > 1;
  }
  return [...groups.values()].sort((a, b) => a.start - b.start);
}
