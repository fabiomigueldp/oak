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
    history = false,
    realtime = false,
    animation = null,
    followedAt = 0;
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
        (!history && Date.now() / 1000 - p.sampled_at > (realtime ? 3 : 30)) ||
        availableMap(p.dimension) !== app.mapViewer.map.data.id
      )
        continue;
      const key = p.uuid || p.name;
      present.add(key);
      let marker = markers.get(key);
      // NormalMarkerManager replaces root marker sets every ten seconds.
      // Private leaf markers stay outside that file-managed collection.
      if (marker && marker.parent !== set) {
        markers.delete(key);
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
        markers.set(key, marker);
      }
      if (!realtime || history) {
        marker.oakSamples = null;
        marker.position.set(...p.position);
      } else {
        marker.oakName = p.name;
        const samples = marker.oakSamples || [];
        const last = samples.at(-1);
        if (!last || p.sampled_at > last.stamp) {
          // Never animate a teleport, dimension change, or resumed stale stream.
          const reset = !last || p.dimension !== last.dimension ||
            p.sampled_at - last.stamp > 1.5 ||
            Math.hypot(...p.position.map((v, i) => v - last.position[i])) > 24;
          const sample = {position: p.position.slice(), dimension: p.dimension,
            stamp: p.sampled_at, at: Date.now()};
          marker.oakSamples = reset ? [sample] : [...samples.slice(-4), sample];
          if (reset) marker.position.set(...p.position);
        }
      }
    }
    for (const [name, marker] of markers)
      if (!present.has(name)) {
        set.remove(marker);
        markers.delete(name);
      }
    const target = live.find((p) => p.name === follow);
    if (target && (!realtime || availableMap(target.dimension) !== app.mapViewer.map.data.id))
      focus(target).catch(() => {
        follow = null;
      });
  }
  function animate() {
    animation = null;
    if (!realtime || history || document.hidden || Date.now() - received > 3000) return;
    const time = Date.now() - 150;
    let pending = false;
    for (const marker of markers.values()) {
      const samples = marker.oakSamples;
      if (!samples?.length) continue;
      let a = samples[0], b = a;
      for (const sample of samples) {
        b = sample;
        if (sample.at >= time) break;
        a = sample;
      }
      const ratio = a === b ? 1 : Math.max(0, Math.min(1, (time - a.at) / (b.at - a.at)));
      const position = a.position.map((v, i) => v + (b.position[i] - v) * ratio);
      if (samples.at(-1).at > time && samples.length > 1 &&
          samples.at(-1).position.some((v, i) => v !== samples.at(-2).position[i])) pending = true;
      marker.position.set(...position);
      if (marker.oakName === follow) {
        const controls = window.bluemap?.mapViewer?.controlsManager;
        controls?.position.set(...position);
        if (Date.now() - followedAt > 250) {
          window.bluemap.mapViewer.updateLoadedMapArea();
          followedAt = Date.now();
        }
      }
    }
    if (pending) animation = requestAnimationFrame(animate);
  }
  addEventListener("message", (event) => {
    if (event.origin !== location.origin || event.source !== parent) return;
    const data = event.data;
    if (data?.type === "oak-admin-players" && Array.isArray(data.players)) {
      players = data.players.slice(0, 100);
      received = Date.now();
      follow = data.follow;
      history = data.history === true;
      realtime = data.realtime === true;
      update();
      if (realtime && animation === null && !document.hidden) animation = requestAnimationFrame(animate);
    } else if (data?.type === "oak-admin-focus") focus(data).catch(() => {});
  });
  const timer = setInterval(update, 1000);
  addEventListener("pagehide", () => {
    clearInterval(timer);
    if (animation !== null) cancelAnimationFrame(animation);
    for (const marker of markers.values()) set?.remove(marker);
    markers.clear();
  });
})();
