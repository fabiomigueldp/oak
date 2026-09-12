import test from "node:test";
import assert from "node:assert/strict";
import {
  dateKey, shiftDate, rangeWindow, shardKeys, normalizeIndex,
  buildTimeline, filterPlayers, groupSessions,
} from "../public/activity-model.mjs";

const DAY = 86400;
const base = Date.parse("2026-09-12T03:00:00Z") / 1000;
const utcStart = base - 10800;

function index(now, extra = {}) {
  return {
    version: 1, updated: now, observedSince: base,
    sampleSeconds: 5, gapSeconds: 20, retentionDays: 90,
    latestDay: "2026-09-12", days: ["2026-09-12", "2026-09-13"],
    collecting: true, ...extra,
  };
}

function session(id, start, end, extra = {}) {
  return { id, start, end, open: false, continuedBefore: false, continuesAfter: false, endReason: "left", ...extra };
}

function player(name, sessions, extra = {}) {
  return { id: `name:${name}`, name, uuid: null, skin: "", sessions, ...extra };
}

function shard(now, players = [], coverage = [[base, now]], extra = {}) {
  return {
    version: 1, day: "2026-09-12", updated: now,
    start: utcStart, end: utcStart + DAY,
    coverage, players, truncated: false, ...extra,
  };
}

function timeline(now, players = [], coverage = [[base, now]], extra = {}) {
  return buildTimeline({ index: index(now), shards: [shard(now, players, coverage)], day: "2026-09-12", now, ...extra });
}

test("Brasília dates and UTC shard boundaries are independent of browser timezone", () => {
  assert.equal(dateKey(Date.parse("2026-01-01T02:59:59Z") / 1000), "2025-12-31");
  assert.equal(dateKey(Date.parse("2026-01-01T03:00:00Z") / 1000), "2026-01-01");
  assert.equal(dateKey(0), "1969-12-31");
  assert.equal(shiftDate("2024-02-28", 1), "2024-02-29");
  assert.equal(shiftDate("2026-01-01", -1), "2025-12-31");
  assert.deepEqual(rangeWindow("2026-09-12"), { day: "2026-09-12", range: "day", start: base, end: base + DAY });
  const week = rangeWindow("2026-01-03", "week");
  assert.equal(new Date(week.start * 1000).toISOString(), "2025-12-28T03:00:00.000Z");
  assert.equal(week.end - week.start, 7 * DAY);
  assert.deepEqual(shardKeys(base, base + DAY), ["2026-09-12", "2026-09-13"]);
  assert.deepEqual(shardKeys(utcStart, utcStart + DAY), ["2026-09-12"]);
  assert.deepEqual(shardKeys(base, base), []);
});

test("recent view includes the preceding day and the current point without future duration", () => {
  const now = base + 7200;
  const window = rangeWindow("2026-09-12", "recent", now);
  assert.equal(window.start, now - DAY);
  assert.equal(window.end, now + 1);
  const previous = shard(now, [player("Oak", [session(1, now - DAY - 300, utcStart)])], [[now - DAY - 300, utcStart]],
    { day: "2026-09-11", start: utcStart - DAY, end: utcStart });
  const current = shard(now, [player("Oak", [session(1, utcStart, now + 3600, { open: true })]),
    player("New", [session(2, now, now, { open: true })])], [[utcStart, now]]);
  const result = buildTimeline({ index: index(now, { days: ["2026-09-11", "2026-09-12"] }), shards: [previous, current], range: "recent", now });
  assert.equal(result.totalSeconds, DAY);
  assert.equal(result.onlineCount, 2);
  assert.equal(result.players.find(value => value.name === "New").totalSeconds, 0);
  assert.equal(result.players.find(value => value.name === "Oak").sessions.at(-1).end, now);
  assert.equal(result.gaps.length, 0);
});

test("recent view advances naturally across Brasília midnight", () => {
  const before = rangeWindow("2026-09-11", "recent", base - 1);
  const after = rangeWindow("2026-09-12", "recent", base + 1);
  assert.equal(after.start - before.start, 2);
  assert.equal(after.end - before.end, 2);
  assert.equal(after.day, "2026-09-12");
  assert.throws(() => rangeWindow("2026-09-12", "recent", -1), RangeError);
});

test("invalid dates and unsafe clocks never produce a NaN model", () => {
  for (const value of [NaN, Infinity, -1, Number.MAX_SAFE_INTEGER, "123"]) {
    assert.throws(() => dateKey(value), RangeError);
  }
  for (const day of ["2026-02-29", "2026-13-01", "2026-1-1", "javascript:alert(1)"]) {
    assert.throws(() => rangeWindow(day), RangeError);
  }
  assert.throws(() => rangeWindow("2026-09-12", "month"), RangeError);
  assert.throws(() => shiftDate("9999-12-31", 1), RangeError);
  assert.throws(() => shardKeys(base, base + 91 * DAY), RangeError);
});

test("manifest file keys are validated, bounded, sorted, and deduplicated", () => {
  assert.equal(normalizeIndex(null, base), null);
  assert.equal(normalizeIndex({ version: 1, updated: Infinity, days: [] }, base), null);
  const result = normalizeIndex(index(base + 100, {
    updated: base + 500, observedSince: base + 500, sampleSeconds: -5, gapSeconds: NaN,
    days: ["2026-09-12", "../secret", "2026-09-12", "2026-09-11", "2020-01-01", "2026-09-13"],
    latestDay: "../secret",
  }), base + 100);
  assert.deepEqual(result.days, ["2026-09-11", "2026-09-12"]);
  assert.equal(result.latestDay, "2026-09-12");
  assert.equal(result.updated, base + 100);
  assert.equal(result.observedSince, null);
  assert.equal(result.sampleSeconds, 1);
  assert.equal(result.gapSeconds, 20);
  assert.equal(result.collecting, false);
});

test("UTC midnight fragments of one session merge without double counting", () => {
  const midnight = utcStart + DAY;
  const now = midnight + 100;
  const result = buildTimeline({ index: index(now), day: "2026-09-12", now, shards: [
    shard(now, [player("Oak", [session(7, midnight - 50, midnight, { continuesAfter: true, endReason: null })])], [[midnight - 100, midnight]]),
    shard(now, [player("Oak", [session(7, midnight, midnight + 70, { continuedBefore: true })])], [[midnight, now]],
      { day: "2026-09-13", start: midnight, end: midnight + DAY }),
  ] });
  assert.equal(result.players.length, 1);
  assert.equal(result.players[0].sessions.length, 1);
  assert.deepEqual(result.players[0].sessions[0], session(7, midnight - 50, midnight + 70));
  assert.equal(result.totalSeconds, 120);
  assert.equal(result.peak, 1);
  assert.deepEqual(result.coverage, [[midnight - 100, now]]);
});

test("concurrency uses exact endpoints and ends precede starts at equal times", () => {
  const now = base + 100;
  const result = timeline(now, [
    player("Ana", [session(1, base, base + 30)]),
    player("Bia", [session(2, base + 30, base + 60)]),
    player("Caio", [session(3, base + 35, base + 45)]),
  ]);
  assert.equal(result.peak, 2);
  assert.equal(result.totalSeconds, 70);
  const touching = timeline(now, [player("Ana", [session(1, base, base + 30)]), player("Bia", [session(2, base + 30, base + 60)])]);
  assert.equal(touching.peak, 1);
  assert.equal(result.bins.reduce((total, bin) => total + bin.playerSeconds, 0), 70);
  assert.equal(result.bins.reduce((total, bin) => total + bin.coverageSeconds, 0), 100);
  assert.equal(Math.max(...result.bins.map((bin) => bin.peak)), 2);
});

test("repeated shards and overlapping duplicate sessions cannot inflate totals or peak", () => {
  const now = base + 100;
  const duplicate = shard(now, [player("Ana", [
    session(1, base, base + 60), session(1, base, base + 60), session(2, base + 40, base + 80),
  ])]);
  const result = buildTimeline({ index: index(now), now, day: "2026-09-12", shards: [duplicate, duplicate] });
  assert.equal(result.players[0].sessions.length, 2);
  assert.equal(result.players[0].totalSeconds, 80);
  assert.equal(result.peak, 1);
  assert.equal(result.bins.reduce((sum, bin) => sum + bin.playerSeconds, 0), 80);
});

test("unknown coverage splits sessions and contributes no player time", () => {
  const now = base + 400;
  const result = timeline(now, [player("Ana", [session(1, base + 50, base + 300, { endReason: "unknown" })])],
    [[base, base + 100], [base + 200, now], [base + 210, base + 350]]);
  assert.equal(result.totalSeconds, 150);
  assert.deepEqual(result.coverage, [[base, base + 100], [base + 200, now]]);
  assert.deepEqual(result.gaps, [[base + 100, base + 200]]);
  assert.deepEqual(result.players[0].sessions.map(({ start, end }) => [start, end]), [[base + 50, base + 100], [base + 200, base + 300]]);
  assert.equal(result.players[0].sessions[1].endReason, "unknown");
});

test("distinct recovered sessions remain separate even when their boundaries touch", () => {
  const result = timeline(base + 100, [player("Ana", [
    session(1, base, base + 50, { endReason: "unknown" }), session(2, base + 50, base + 100),
  ])]);
  assert.equal(result.players[0].sessions.length, 2);
  assert.equal(result.totalSeconds, 100);
  assert.equal(result.peak, 1);
});

test("future time and uncovered samples are never extrapolated", () => {
  const now = base + 100;
  const result = timeline(now, [player("Ana", [session(1, base, base + 500, { open: true, endReason: null })])], [[base, base + 500]]);
  assert.equal(result.totalSeconds, 100);
  assert.equal(result.players[0].sessions[0].end, now);
  assert.equal(result.players[0].sessions[0].continuesAfter, true);
  assert.equal(result.window.effectiveEnd, now);
  assert.equal(result.onlineCount, 1);
  const delayed = timeline(now, [player("Ana", [session(1, base, now)])], [[base, now]], { index: index(base + 80) });
  assert.equal(delayed.totalSeconds, 80);
  assert.deepEqual(delayed.gaps, [[base + 80, now]]);
  const uncovered = timeline(now, [player("Ana", [session(1, base, now)])], []);
  assert.equal(uncovered.totalSeconds, 0);
  assert.equal(uncovered.players.length, 0);
});

test("live status tolerates publication cadence but expires after ninety seconds", () => {
  const updated = base + 100;
  const data = shard(updated, [player("Ana", [session(1, base, updated, { open: true, endReason: null })])]);
  const build = (now) => buildTimeline({ index: index(updated), shards: [data], now, day: "2026-09-12" });
  assert.equal(build(updated + 60).onlineCount, 1);
  assert.equal(build(updated + 90).onlineCount, 1);
  assert.equal(build(updated + 91).onlineCount, 0);
  assert.equal(build(updated + 91).collecting, false);
  assert.equal(build(updated + 91).totalSeconds, 100);
});

test("zero-length first observations display markers and exact point peak without inventing duration", () => {
  const now = base + 100;
  const result = timeline(now, [
    player("Ana", [session(1, now, now, { open: true, endReason: null })]),
    player("Bia", [session(2, now, now, { open: true, endReason: null })]),
  ], [[now, now]]);
  assert.equal(result.players.length, 2);
  assert.equal(result.totalSeconds, 0);
  assert.equal(result.onlineCount, 2);
  assert.equal(result.peak, 2);
  assert.equal(result.players[0].lastSeen, now);
  assert.equal(result.bins.reduce((sum, bin) => sum + bin.playerSeconds, 0), 0);
  assert.equal(groupSessions(result.players[0].sessions, base, base + DAY)[0].totalSeconds, 0);
});

test("a player observed once remains visible after an unknown disconnect", () => {
  const now = base + 100;
  const result = timeline(now, [player("Ana", [session(1, base + 50, base + 50, { endReason: "unknown" })])], [[base + 50, base + 50]]);
  assert.equal(result.players.length, 1);
  assert.equal(result.totalSeconds, 0);
  assert.equal(result.onlineCount, 0);
  assert.equal(result.peak, 1);
  assert.equal(result.players[0].sessions[0].endReason, "unknown");
  assert.equal(groupSessions(result.players[0].sessions, base, base + DAY)[0].count, 1);
});

test("point observations do not count a player twice beside their own interval", () => {
  const now = base + 100;
  const result = timeline(now, [
    player("Ana", [session(1, base, base + 80), session(2, base + 40, base + 40)]),
    player("Bia", [session(3, base + 40, base + 40)]),
    player("Caio", [session(4, base + 80, now)]),
    player("Dani", [session(5, base + 80, base + 80)]),
  ]);
  assert.equal(result.totalSeconds, 100);
  assert.equal(result.peak, 2);
  assert.equal(result.bins.reduce((sum, bin) => sum + bin.playerSeconds, 0), 100);
});

test("sweep results match an independent per-second oracle across overlapping players and gaps", () => {
  let seed = 712367;
  const random = () => { seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0; return seed; };
  for (let trial = 0; trial < 30; trial += 1) {
    const players = Array.from({ length: 8 }, (_, playerId) => player(`P${playerId}`, Array.from({ length: 6 }, (_, id) => {
      const start = random() % 99;
      const end = start + 1 + random() % (100 - start);
      return session(id, base + start, base + end);
    })));
    const result = timeline(base + 100, players, [[base, base + 30], [base + 45, base + 100]], { bins: 37 });
    let total = 0;
    let peak = 0;
    for (let second = 0; second < 100; second += 1) {
      if (second >= 30 && second < 45) continue;
      const active = players.filter((p) => p.sessions.some((s) => s.start <= base + second && s.end > base + second)).length;
      total += active;
      peak = Math.max(peak, active);
    }
    assert.equal(result.totalSeconds, total);
    assert.equal(result.peak, peak);
    assert.ok(Math.abs(result.bins.reduce((sum, bin) => sum + bin.playerSeconds, 0) - total) < 0.0001);
  }
});

test("an empty observed interval differs from an unavailable interval", () => {
  const now = base + 100;
  const observed = timeline(now);
  assert.deepEqual(observed.players, []);
  assert.equal(observed.totalSeconds, 0);
  assert.equal(observed.peak, 0);
  assert.deepEqual(observed.gaps, []);
  assert.deepEqual(observed.coverage, [[base, now]]);
  const unavailable = buildTimeline({ day: "2026-09-12", now });
  assert.deepEqual(unavailable.coverage, []);
  assert.deepEqual(unavailable.gaps, [[base, now]]);
  assert.equal(unavailable.updated, null);
  assert.equal(unavailable.collecting, false);
  const noManifest = buildTimeline({ day: "2026-09-12", now, shards: [shard(now, [player("Ana", [session(1, base, now, { open: true })])])] });
  assert.deepEqual(noManifest.players, []);
  const future = timeline(now, [], [], { day: "2026-09-13" });
  assert.deepEqual(future.gaps, []);
  assert.equal(future.window.effectiveEnd, future.window.start);
});

test("newest shard revision wins and malformed player data is discarded", () => {
  const now = base + 100;
  const old = shard(now - 20, [player("Old", [session(1, base, now - 20)])]);
  const fresh = shard(now, [
    player("Ana\u0000", [session(2, base, now), session(3, NaN, Infinity), session(-1, base, now)], { skin: "../../secret" }),
    player("", [session(3, base, now)]), player("Invalid", [session(4, base, now)], { id: "arbitrary-id" }),
  ]);
  const result = buildTimeline({ index: index(now), now, day: "2026-09-12", shards: [fresh, old] });
  assert.equal(result.players.length, 1);
  assert.equal(result.players[0].name, "Ana");
  assert.equal(result.players[0].skin, "");
  assert.equal(result.players[0].sessions.length, 1);
  assert.equal(result.totalSeconds, 100);
});

test("input and histogram bounds produce explicit truncation", () => {
  const now = base + 5000;
  const sessions = Array.from({ length: 4200 }, (_, index) => session(index, base + index, base + index + 1));
  const result = timeline(now, [player("Ana", sessions)], [[base, now]], { bins: 1000000 });
  assert.equal(result.truncated, true);
  assert.equal(result.players[0].sessions.length, 4096);
  assert.equal(result.bins.length, 192);
  assert.equal(result.totalSeconds, 4096);
});

test("player search and sorting are stable and do not mutate source order", () => {
  const players = [
    { name: "Bia", totalSeconds: 40, online: false, lastSeen: 30 },
    { name: "ana", totalSeconds: 20, online: true, lastSeen: 10 },
    { name: "Caio", totalSeconds: 80, online: false, lastSeen: 50 },
  ];
  assert.deepEqual(filterPlayers(players, "  AN ").map((p) => p.name), ["ana"]);
  assert.deepEqual(filterPlayers(players, "", "name").map((p) => p.name), ["ana", "Bia", "Caio"]);
  assert.deepEqual(filterPlayers(players, "", "total").map((p) => p.name), ["Caio", "Bia", "ana"]);
  assert.deepEqual(filterPlayers(players).map((p) => p.name), ["ana", "Caio", "Bia"]);
  assert.equal(players[0].name, "Bia");
});

test("display clusters bound DOM work while preserving genuine gaps and original sessions", () => {
  const sessions = Array.from({ length: 1000 }, (_, id) => session(id, base + id * 4, base + id * 4 + 1));
  const groups = groupSessions(sessions, base, base + DAY, 120);
  assert.ok(groups.length <= 120);
  assert.equal(groups.reduce((sum, group) => sum + group.count, 0), 1000);
  assert.equal(groups.reduce((sum, group) => sum + group.totalSeconds, 0), 1000);
  assert.ok(groups.every((group) => group.clustered));
  assert.ok(groups[0].end - groups[0].start > groups[0].totalSeconds);
  assert.equal(groups[0].sessions[0], sessions[0]);
  assert.deepEqual(groupSessions(sessions, NaN, Infinity), []);
});
