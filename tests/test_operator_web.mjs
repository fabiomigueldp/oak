import test from 'node:test';
import assert from 'node:assert/strict';
import { makeTrigger, parseObject, isJobActive, createEventFeed } from '../public/admin/operator.js';
import { registerOakTools, operatorReadPath } from '../public/admin/webmcp.js';
import { externalCopyStatus } from '../public/admin/backups.js';

test('routine inputs distinguish clock time, intervals and event names', () => {
  assert.deepEqual(makeTrigger('interval', '30'), { type: 'interval', seconds: 30 });
  assert.deepEqual(makeTrigger('once', '2026-09-12T15:30:00Z'), { type: 'once', at: 1789227000 });
  assert.deepEqual(makeTrigger('event', ' minecraft.player.join '), { type: 'event', name: 'minecraft.player.join' });
  for (const value of ['', '-1', 'NaN']) assert.throws(() => makeTrigger('interval', value));
  assert.throws(() => makeTrigger('once', 'invalid'));
  assert.throws(() => makeTrigger('event', ' '));
});

test('activity pages past the first 100 events and keeps a rolling cursor across refreshes', async () => {
  const records = Array.from({ length: 251 }, (_, i) => ({ id: i + 1, name: 'minecraft.player.join' }));
  const offsets = [];
  const feed = createEventFeed(async path => {
    const after = Number(new URL(path, 'https://oak.example').searchParams.get('after'));
    offsets.push(after);
    const events = records.filter(event => event.id > after).slice(0, 100);
    return { events, next_cursor: events.at(-1)?.id ?? after, has_more: events.length === 100 };
  });
  const first = feed.refresh();
  assert.equal(feed.refresh(), first);
  await first;
  assert.deepEqual(offsets, [0, 100, 200]);
  assert.equal(feed.cursor, 251);
  assert.equal(feed.events.length, 200);
  assert.equal(feed.events[0].id, 52);
  records.push({ id: 252, name: 'minecraft.player.quit' });
  await feed.refresh();
  assert.equal(offsets.at(-1), 251);
  assert.equal(feed.events.at(-1).id, 252);
  assert.equal(new Set(feed.events.map(event => event.id)).size, 200);
});

test('activity resumes after a failed page without replaying or dropping earlier events', async () => {
  const offsets = [];
  let fail = true;
  const feed = createEventFeed(async path => {
    const after = Number(new URL(path, 'https://oak.example').searchParams.get('after'));
    offsets.push(after);
    if (after === 1 && fail) throw new Error('Offline');
    return after === 0 ? { events: [{ id: 1 }], next_cursor: 1, has_more: true }
      : { events: [{ id: 2 }], next_cursor: 2, has_more: false };
  });
  await assert.rejects(feed.refresh(), /Offline/);
  assert.equal(feed.cursor, 1);
  fail = false;
  await feed.refresh();
  assert.deepEqual(offsets, [0, 1, 1]);
  assert.deepEqual(feed.events.map(event => event.id), [1, 2]);
  const stalled = createEventFeed(async () => ({ events: [], next_cursor: 0, has_more: true }));
  await assert.rejects(stalled.refresh(), /Resposta de eventos inválida/);
});

test('editor JSON rejects arrays and primitives without interpreting source', () => {
  assert.deepEqual(parseObject('{"main.py":"print(1)"}', 'Files'), { 'main.py': 'print(1)' });
  for (const value of ['[]', 'null', 'false', '1', 'invalid']) assert.throws(() => parseObject(value, 'Files'));
  assert.equal(isJobActive({ state: 'running' }), true);
  assert.equal(isJobActive({ state: 'failed' }), false);
});

test('read tools encode IDs and reject unknown paths and invalid output offsets', () => {
  assert.equal(operatorReadPath({ resource: 'job', id: '../cancel?x', offset: 17 }), '/jobs/..%2Fcancel%3Fx?offset=17');
  assert.equal(operatorReadPath({ resource: 'notebooks' }), '/notebooks');
  assert.throws(() => operatorReadPath({ resource: '../jobs' }));
  assert.throws(() => operatorReadPath({ resource: 'job', id: 'j', offset: -1 }));
  assert.throws(() => operatorReadPath({ resource: 'job', id: 'j', offset: 1.5 }));
});

function fixture(role = 'owner') {
  const registered = new Map(), calls = [];
  const state = { session: { user: { role, id: 'owner-1' }, csrf: 'private' }, page: 'operator', overview: { snapshot: { online: true } } };
  const ctx = { state, pages: { operator: 'Operador', world: 'Mundo' },
    api: async (...args) => { calls.push(args); return { id: 'job-1' }; },
    navigate: async page => { state.page = page; }, focusMap: value => { state.mapSelection = value; } };
  const model = { registerTool: async tool => { registered.set(tool.name, tool); }, unregisterTool: name => registered.delete(name) };
  return { ctx, model, registered, calls, state };
}

test('site tools are absent for non-owner sessions and unsupported browsers', async () => {
  const f = fixture('administrator');
  const stop = await registerOakTools(f.ctx, f.model);
  assert.equal(f.registered.size, 0); stop();
  assert.equal(typeof await registerOakTools(f.ctx, {}), 'function');
});

test('execution preserves source and shares authenticated portal APIs', async () => {
  const f = fixture();
  const stop = await registerOakTools(f.ctx, f.model);
  const execute = f.registered.get('oak_execute');
  assert.equal(execute.annotations.readOnlyHint, false);
  assert.equal(execute.annotations.destructiveHint, true);
  const payload = { kind: 'shell', source: 'printf "<script>untrusted</script>"', timeout_seconds: null, resources: ['world:arena'] };
  assert.deepEqual(await execute.execute(payload), { id: 'job-1' });
  assert.deepEqual(f.calls[0], ['/operator/jobs', payload]);
  const context = await f.registered.get('oak_context').execute({});
  assert.equal(JSON.stringify(context).includes('private'), false);
  assert.equal(context.page, 'operator');
  const generic = f.registered.get('oak_operator_call');
  assert.equal(generic.annotations.readOnlyHint, false);
  await generic.execute({ method: 'services.control', data: { name: 'arena', action: 'restart' } });
  assert.deepEqual(f.calls[1], ['/operator/call', { method: 'services.control', data: { name: 'arena', action: 'restart' } }]);
  stop(); assert.equal(f.registered.size, 0);
  await assert.rejects(execute.execute(payload), /active owner session/);
});

test('retained handlers cannot run after logout or under another owner session', async () => {
  const f = fixture();
  await registerOakTools(f.ctx, f.model);
  const tool = f.registered.get('oak_execute');
  f.state.session = null;
  await assert.rejects(tool.execute({ kind: 'command', source: 'list' }), /active owner session/);
  f.state.session = { user: { role: 'owner', id: 'owner-2' } };
  await assert.rejects(tool.execute({ kind: 'command', source: 'list' }), /active owner session/);
  assert.equal(f.calls.length, 0);
});

test('map tools validate coordinates and update the same portal selection', async () => {
  const f = fixture(); await registerOakTools(f.ctx, f.model);
  const tool = f.registered.get('oak_map_focus');
  await assert.rejects(tool.execute({ position: [1, NaN, 2], dimension: 'minecraft:overworld' }));
  await assert.rejects(tool.execute({ position: [1, 2, 3], dimension: 'unknown' }));
  await tool.execute({ position: [1, 2, 3], dimension: 'minecraft:the_nether' });
  assert.equal(f.state.page, 'world');
  assert.deepEqual(f.state.mapSelection, { position: [1, 2, 3], dimension: 'minecraft:the_nether' });
  assert.equal(f.calls.length, 0);
});

test('partial registration is removed if browser registration fails', async () => {
  const f = fixture(); let count = 0;
  const original = f.model.registerTool;
  f.model.registerTool = async tool => { if (++count === 3) throw new Error('Unavailable'); await original(tool); };
  await registerOakTools(f.ctx, f.model);
  assert.equal(f.registered.size, 0);
});

test('clients without unregister can authenticate again without duplicate registration', async () => {
  const f = fixture(); delete f.model.unregisterTool;
  const stop = await registerOakTools(f.ctx, f.model);
  const retained = f.registered.get('oak_context');
  stop(); f.state.session = null;
  await assert.rejects(retained.execute({}), /active owner session/);
  f.model.registerTool = () => { throw new Error('Duplicate registration'); };
  f.state.session = { user: { role: 'owner', id: 'owner-2' }, demo: true };
  await registerOakTools(f.ctx, f.model);
  assert.equal((await retained.execute({})).demo, true);
  assert.equal(f.registered.size, 7);
});

test('backup badge requires a recent timestamp and download acknowledgement', () => {
  const now = 1789185404;
  assert.equal(externalCopyStatus({ external_copy: true, external_copy_verified_at: now - 60 }, now).kind, 'good');
  for (const data of [
    {}, { external_copy: true }, { external_copy: false, external_copy_verified_at: now - 60 },
    { external_copy: true, external_copy_verified_at: now - 48 * 3600 },
    { external_copy: true, external_copy_verified_at: now + 1 },
  ]) assert.equal(externalCopyStatus(data, now).kind, 'warning');
});
