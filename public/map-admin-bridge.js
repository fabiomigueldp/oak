/* Private, ephemeral player overlay for BlueMap 5.23. No data is fetched here. */
(() => {
  "use strict";
  function decoratePlayerPin(button, name) {
    button.classList.add("oak-player-pin");
    button.setAttribute("aria-label", name);
    let hash = 0;
    for (const char of name.toLowerCase()) hash = (hash * 31 + char.charCodeAt(0)) >>> 0;
    button.dataset.tone = String(hash % 6);
    const label = document.createElement("span");
    label.className = "oak-player-name";
    label.textContent = name;
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", "0 0 40 30");
    svg.setAttribute("aria-hidden", "true");
    for (const layer of ["edge", "halo", "color"]) {
      const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
      path.setAttribute("d", "M8 10 20 26 32 10");
      path.setAttribute("class", "oak-chevron-" + layer);
      svg.append(path);
    }
    button.replaceChildren(label, svg);
  }

  if (window.parent === window) return;
  try {
    if (
      parent.location.origin !== location.origin ||
      !parent.location.pathname.startsWith("/admin/")
    )
      return;
  } catch {
    return;
  }
  const dimensions = {
    "minecraft:overworld": "overworld",
    "minecraft:the_nether": "nether",
    "minecraft:the_end": "end",
  };
  let set,
    received = 0,
    players = [],
    follow = null,
    focusing = false,
    history = false;
  const markers = new Map();
  const valid = (p) =>
    Array.isArray(p) &&
    p.length === 3 &&
    p.every((v) => Number.isFinite(v) && Math.abs(v) <= 30000000);
  function availableMap(dimension) {
    const app = window.bluemap;
    const id = dimensions[dimension];
    return app?.maps?.find((m) => m.data.id === id || m.data.id === "oak-" + id)
      ?.data.id;
  }
  async function focus(player) {
    if (focusing || !valid(player.position)) return;
    const app = window.bluemap,
      id = availableMap(player.dimension);
    if (!id) {
      parent.postMessage(
        { type: "oak-admin-map-unavailable" },
        location.origin,
      );
      return;
    }
    focusing = true;
    try {
      if (app.mapViewer.map?.data.id !== id) await app.switchMap(id);
      app.mapViewer.controlsManager.position.set(...player.position);
      app.mapViewer.controlsManager.distance = Math.max(
        120,
        app.mapViewer.controlsManager.distance,
      );
      app.updatePageAddress();
      app.mapViewer.updateLoadedMapArea();
    } finally {
      focusing = false;
    }
  }
  function update() {
    const app = window.bluemap,
      api = window.BlueMap;
    if (!app?.mapViewer?.map || !api?.HtmlMarker) return;
    if (set !== app.mapViewer.markers) {
      for (const marker of markers.values()) set?.remove(marker);
      markers.clear();
      set = app.mapViewer.markers;
    }
    const live = Date.now() - received < 15000 ? players : [];
    const present = new Set();
    for (const p of live) {
      if (
        typeof p.name !== "string" ||
        !/^[.]?[A-Za-z0-9_]{1,16}$/.test(p.name) ||
        !valid(p.position) ||
        (!history && Date.now() / 1000 - p.sampled_at > 30) ||
        availableMap(p.dimension) !== app.mapViewer.map.data.id
      )
        continue;
      present.add(p.name);
      let marker = markers.get(p.name);
      // NormalMarkerManager replaces root marker sets every ten seconds.
      // Private leaf markers stay outside that file-managed collection.
      if (marker && marker.parent !== set) {
        markers.delete(p.name);
        marker = null;
      }
      if (!marker) {
        marker = new api.HtmlMarker(
          "oak-private-" + markers.size + "-" + Date.now(),
        );
        const label = document.createElement("button");
        label.type = "button";
        label.className = "oak-player-pin";
        decoratePlayerPin(label, p.name);
        label.addEventListener("click", () =>
          parent.postMessage(
            { type: "oak-admin-select", name: p.name },
            location.origin,
          ),
        );
        marker.element.replaceChildren(label);
        marker.data.listed = false;
        marker.anchor.set(0.5, 1);
        set.add(marker);
        markers.set(p.name, marker);
      }
      marker.position.set(...p.position);
    }
    for (const [name, marker] of markers)
      if (!present.has(name)) {
        set.remove(marker);
        markers.delete(name);
      }
    const target = live.find((p) => p.name === follow);
    if (target)
      focus(target).catch(() => {
        follow = null;
      });
  }
  addEventListener("message", (event) => {
    if (event.origin !== location.origin || event.source !== parent) return;
    const data = event.data;
    if (data?.type === "oak-admin-players" && Array.isArray(data.players)) {
      players = data.players.slice(0, 100);
      received = Date.now();
      follow = data.follow;
      history = data.history === true;
      update();
    } else if (data?.type === "oak-admin-focus") focus(data).catch(() => {});
  });
  const timer = setInterval(update, 1000);
  addEventListener("pagehide", () => {
    clearInterval(timer);
    for (const marker of markers.values()) set?.remove(marker);
    markers.clear();
  });
})();
