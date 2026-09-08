const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const code = fs.readFileSync(
  require("node:path").join(__dirname, "../public/map-admin-bridge.js"),
  "utf8",
);

test("ordinary public map does not activate the private overlay", () => {
  const window = {};
  window.parent = window;
  vm.runInNewContext(code, { window });
});

test("private players survive BlueMap file refreshes and expire when updates stop", () => {
  const handlers = {};
  let tick, now = 100000;
  class MarkerSet {
    constructor(id) { this.data = {id}; this.children = []; this.isMarkerSet = true; }
    add(marker) { this.children.push(marker); marker.parent = this; }
    remove(marker) { this.children = this.children.filter(m => m !== marker); marker.parent = null; marker.dispose(); }
    dispose() { for (const m of this.children) m.dispose(); }
    refreshFile() { for (const m of [...this.children]) if (m.isMarkerSet) this.remove(m); }
  }
  class HtmlMarker {
    constructor(id) {
      this.data = {id}; this.disposals = 0;
      this.element = {replaceChildren() {}};
      this.anchor = {set() {}}; this.position = {set() {}};
    }
    dispose() { this.disposals++; }
  }
  const root = new MarkerSet('bm-root');
  const parent = {location: {origin: 'https://oak.test', pathname: '/admin/'}};
  const window = {parent, BlueMap: {MarkerSet, HtmlMarker}, bluemap: {
    maps: [{data: {id: 'overworld'}}], mapViewer: {map: {data: {id: 'overworld'}}, markers: root}
  }};
  vm.runInNewContext(code, {window, parent, location: parent.location,
    Date: {now: () => now}, document: {createElement: () => ({addEventListener() {}})},
    addEventListener: (name, fn) => handlers[name] = fn,
    setInterval: fn => { tick = fn; return 1; }, clearInterval() {}
  });
  const update = () => handlers.message({origin: parent.location.origin, source: parent,
    data: {type: 'oak-admin-players', players: [{name: 'Player', position: [0,64,0], dimension: 'minecraft:overworld', sampled_at: now / 1000}]}});
  update();
  const marker = root.children[0];
  for (let cycle = 0; cycle < 3; cycle++) {
    now += 10000; update(); root.refreshFile(); tick();
    assert.equal(root.children.length, 1);
    assert.equal(root.children[0], marker);
    assert.equal(marker.disposals, 0);
  }
  now += 16000; tick();
  assert.equal(root.children.length, 0);
  assert.equal(marker.disposals, 1);
});
test("an unrelated embedding page cannot activate the overlay", () => {
  const parent = {
    location: { origin: "https://oak.test", pathname: "/public/" },
  };
  vm.runInNewContext(code, {
    window: { parent },
    parent,
    location: { origin: "https://oak.test" },
  });
});
test("private overlay rejects messages from any other source or origin", () => {
  const handlers = {};
  let additions = 0;
  const parent = {
    location: { origin: "https://oak.test", pathname: "/admin/" },
  };
  class MarkerSet {
    constructor() {
      this.data = {};
    }
    add() {
      additions++;
    }
    clear() {}
  }
  const window = {
    parent,
    BlueMap: { MarkerSet, HtmlMarker: class {} },
    bluemap: {
      mapViewer: { map: { data: { id: "overworld" } }, markers: { add() {} } },
      maps: [{ data: { id: "overworld" } }],
    },
  };
  vm.runInNewContext(code, {
    window,
    parent,
    location: { origin: "https://oak.test" },
    addEventListener: (name, fn) => (handlers[name] = fn),
    setInterval: () => 1,
    clearInterval() {},
  });
  const payload = {
    type: "oak-admin-players",
    players: [
      {
        name: "Player",
        position: [0, 64, 0],
        sampled_at: Date.now() / 1000,
        dimension: "minecraft:overworld",
      },
    ],
  };
  handlers.message({
    origin: "https://evil.test",
    source: parent,
    data: payload,
  });
  handlers.message({ origin: "https://oak.test", source: {}, data: payload });
  assert.equal(additions, 0);
});
