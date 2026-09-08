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
