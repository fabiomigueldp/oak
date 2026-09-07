// Run without a browser or network; exercise the actual pre-load integration.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../public/map-profile.js'), 'utf8');
const upstream = {version: '5.23', useCookies: true, hiresSliderDefault: 1600,
  hiresSliderMax: 1600, maps: ['overworld'], mapDataRoot: 'maps', scripts: [], clientDecompression: true};

function boot({nav = {}, coarse = false, dpr = 1, saved = {}, storageFails = false,
  href = 'https://oak.test/map/', response} = {}) {
  const store = new Map(Object.entries(saved));
  const requests = [];
  const listeners = {};
  const children = [];
  const document = {
    addEventListener: (name, fn) => { listeners[name] = fn; },
    createElement: () => ({setAttribute() {}, append(...nodes) { this.children = nodes; }, remove() { this.removed = true; }}),
    body: {append(node) { children.push(node); }}
  };
  const context = vm.createContext({URL, Request, Response, Headers, document,
    location: new URL(href), navigator: {userAgent: 'Desktop', hardwareConcurrency: 8, ...nav},
    matchMedia: () => ({matches: coarse}), devicePixelRatio: dpr,
    localStorage: {
      getItem(key) { if (storageFails) throw Error('blocked'); return store.get(key) ?? null; },
      setItem(key, value) { if (storageFails) throw Error('blocked'); store.set(key, value); }
    },
    fetch: async (...args) => {
      requests.push(args);
      return response || new Response(JSON.stringify(upstream), {headers: {'content-type': 'application/json', etag: 'old'}});
    }
  });
  context.window = context;
  vm.runInContext(source, context);
  return {context, store, requests, listeners, children,
    settings: async () => (await context.fetch('settings.json')).json()};
}

test('desktop starts at 250 before BlueMap reads settings; map metadata is preserved', async () => {
  const app = boot();
  const settings = await app.settings();
  assert.equal(settings.hiresSliderDefault, 250);
  assert.equal(settings.hiresSliderMax, 500);
  assert.equal(settings.resolutionDefault, 1);
  assert.deepEqual(settings.maps, upstream.maps);
  assert.equal(settings.clientDecompression, true);
  assert.equal(app.store.get('bluemap-hiresViewDistance'), '250');
});

for (const [name, options] of Object.entries({
  iPhone: {nav: {userAgent: 'iPhone', hardwareConcurrency: 6}, dpr: 3},
  iPad: {nav: {userAgent: 'Macintosh', platform: 'MacIntel', maxTouchPoints: 5}},
  Android: {nav: {userAgent: 'Android'}},
  touch: {coarse: true}, lowMemory: {nav: {deviceMemory: 4}},
  lowCPU: {nav: {hardwareConcurrency: 4}}, saveData: {nav: {connection: {saveData: true}}}
})) test(name + ' uses conservative startup limits', async () => {
  const app = boot(options);
  const settings = await app.settings();
  assert.equal(settings.hiresSliderDefault, 100);
  assert.equal(settings.hiresSliderMax, 200);
  assert.equal(settings.lowresSliderDefault, 1000);
  assert.equal(settings.resolutionDefault, 0.5);
  assert.equal(app.store.get('bluemap-pauseTileLoading'), 'true');
});

test('legacy 1600 preferences migrate on desktop and mobile, cheaper settings survive', async () => {
  for (const [coarse, expected] of [[false, '250'], [true, '100']]) {
    const app = boot({coarse, saved: {'bluemap-hiresViewDistance': '1600', 'bluemap-superSampling': '2'}});
    await app.settings();
    assert.equal(app.store.get('bluemap-hiresViewDistance'), expected);
    assert.equal(app.store.get('bluemap-superSampling'), coarse ? '0.5' : '1');
  }
  const app = boot({saved: {'bluemap-hiresViewDistance': '0', 'bluemap-superSampling': '0.5',
    'bluemap-lowresViewDistance': '500', 'oak-web-name': 'unchanged'}});
  await app.settings();
  assert.equal(app.store.get('bluemap-hiresViewDistance'), '0');
  assert.equal(app.store.get('bluemap-superSampling'), '0.5');
  assert.equal(app.store.get('bluemap-lowresViewDistance'), '500');
  assert.equal(app.store.get('oak-web-name'), 'unchanged');
});

test('malformed preferences and blocked storage do not prevent safe defaults', async () => {
  for (const value of ['broken', 'null', '"1600"', '-1', '{}']) {
    const app = boot({saved: {'bluemap-hiresViewDistance': value}});
    await app.settings();
    assert.equal(app.store.get('bluemap-hiresViewDistance'), '250');
  }
  const settings = await boot({coarse: true, storageFails: true}).settings();
  assert.equal(settings.useCookies, false);
  assert.equal(settings.hiresSliderDefault, 100);
});

test('only the same-origin GET settings response is transformed', async () => {
  const response = new Response('unchanged');
  const app = boot({response});
  for (const input of ['maps/overworld/settings.json', '/api/events', 'https://other.test/map/settings.json']) {
    assert.equal(await app.context.fetch(input), response);
  }
  const request = new Request('https://oak.test/map/settings.json', {method: 'POST', body: 'x'});
  assert.equal(await app.context.fetch(request), response);
  const failed = new Response('missing', {status: 404});
  assert.equal(await boot({response: failed}).context.fetch('settings.json'), failed);
});

test('Request inputs retain cancellation and stale entity headers are removed', async () => {
  const app = boot();
  const request = new Request('https://oak.test/map/settings.json?v=2');
  const init = {signal: new AbortController().signal};
  const response = await app.context.fetch(request, init);
  assert.equal(app.requests[0][0], request);
  assert.equal(app.requests[0][1], init);
  assert.equal(response.headers.get('etag'), null);
  assert.equal((await response.json()).hiresSliderDefault, 250);
});

test('GPU recovery is manual, keeps map coordinates, and reduces the next startup budget', async () => {
  const app = boot({href: 'https://oak.test/map/#overworld:0:110:0:430:0:0.6:0:0:perspective'});
  app.listeners.webglcontextlost();
  app.listeners.webglcontextlost();
  assert.equal(app.children.length, 1);
  assert.equal(app.context.oakMapProfile.contextLosses, 2);
  const href = app.children[0].children[1].href;
  assert.ok(href.includes('oak-quality=low'));
  assert.ok(href.includes('#overworld:'));
  assert.equal((await boot({href}).settings()).hiresSliderDefault, 50);
  const returning = boot({href, saved: {'bluemap-hiresViewDistance': '100', 'bluemap-lowresViewDistance': '2000'}});
  await returning.settings();
  assert.equal(returning.store.get('bluemap-hiresViewDistance'), '50');
  assert.equal(returning.store.get('bluemap-lowresViewDistance'), '1000');
  app.listeners.webglcontextrestored();
  assert.equal(app.children[0].removed, true);
});
