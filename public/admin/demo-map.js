/* Original code-drawn terrain for localhost demonstration, never live map data. */
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

  const canvas = document.querySelector("#terrain"),
    ctx = canvas.getContext("2d");
  let width = 0,
    height = 0,
    zoom = 1,
    pan = [0, 0],
    drag = null,
    players = [],
    follow = null,
    received = 0,
    dimension = "minecraft:overworld";
  const labels = {
    "minecraft:overworld": "Superfície",
    "minecraft:the_nether": "Nether",
    "minecraft:the_end": "End",
  };
  const valid = (p) =>
    Array.isArray(p) && p.length === 3 && p.every(Number.isFinite);
  const altitude = (x, z) =>
    Math.max(
      0,
      Math.sin(x * 0.13) * Math.cos(z * 0.12) * 4 + Math.sin(z * 0.27) * 1.3,
    );
  function project(x, z, y = 0) {
    const scale = Math.min(width / 140, 6) * zoom;
    return [
      width / 2 + (x - z) * scale + pan[0],
      height * 0.5 + (x + z) * scale * 0.48 - y * scale * 0.9 + pan[1],
    ];
  }
  function polygon(points, fill) {
    ctx.fillStyle = fill;
    ctx.beginPath();
    points.forEach(([x, y], i) => (i ? ctx.lineTo(x, y) : ctx.moveTo(x, y)));
    ctx.closePath();
    ctx.fill();
  }
  function paint() {
    ctx.clearRect(0, 0, width, height);
    const nether = dimension === "minecraft:the_nether",
      end = dimension === "minecraft:the_end";
    for (let sum = -64; sum <= 64; sum++)
      for (let x = -32; x <= 32; x++) {
        const z = sum - x;
        if (z < -32 || z > 32) continue;
        const river = Math.abs(x - 9 * Math.sin(z * 0.12)) < 3,
          high = altitude(x, z),
          h = river ? 0 : high;
        const hue = nether ? 20 : end ? 72 : 125 + high * 2,
          light = nether ? 25 + high * 2 : end ? 48 + high : 28 + high * 3;
        const color = river
          ? nether
            ? "#b96639"
            : end
              ? "#252331"
              : "#3c7775"
          : `hsl(${hue} ${nether ? 35 : 23}% ${light}%)`;
        polygon(
          [
            project(x, z, h),
            project(x + 1, z, h),
            project(x + 1, z + 1, h),
            project(x, z + 1, h),
          ],
          color,
        );
        if (!river && high > 1 && (x * 17 + z * 13) % 23 === 0 && !end) {
          const top = project(x + 0.5, z + 0.5, h + 2.4),
            a = project(x - 0.4, z + 0.5, h),
            b = project(x + 1.4, z + 0.5, h);
          polygon([top, a, b], nether ? "#925747" : "#274c37");
        }
        if (
          !river &&
          (x === -12 || x === -16 || x === 18) &&
          (z === 12 || z === 16)
        ) {
          polygon(
            [
              project(x, z, h),
              project(x + 3, z, h),
              project(x + 3, z + 3, h),
              project(x, z + 3, h),
            ],
            "#b39b70",
          );
          polygon(
            [
              project(x - 0.3, z - 0.3, h + 1.4),
              project(x + 3.3, z - 0.3, h + 1.4),
              project(x + 3.3, z + 3.3, h + 1.4),
              project(x - 0.3, z + 3.3, h + 1.4),
            ],
            "#8c674c",
          );
        }
      }
    const pins = document.querySelector("#pins");
    pins.replaceChildren();
    for (const p of players) {
      if (!valid(p.position) || p.dimension !== dimension) continue;
      const [x, y] = project(
        p.position[0] / 8,
        p.position[2] / 8,
        altitude(p.position[0] / 8, p.position[2] / 8) + 2,
      );
      const b = document.createElement("button");
      b.className = "pin";
      decoratePlayerPin(b, p.name);
      b.style.left = x + "px";
      b.style.top = y + "px";
      b.addEventListener("click", () =>
        parent.postMessage(
          { type: "oak-admin-select", name: p.name },
          location.origin,
        ),
      );
      pins.append(b);
    }
    document.querySelector("#dimension").textContent = labels[dimension];
  }
  function focus(p) {
    if (!valid(p.position) || !labels[p.dimension]) return;
    dimension = p.dimension;
    pan = [0, 0];
    const point = project(p.position[0] / 8, p.position[2] / 8, 0);
    pan = [width / 2 - point[0], height / 2 - point[1]];
    paint();
  }
  function resize() {
    width = innerWidth;
    height = innerHeight;
    const ratio = Math.min(devicePixelRatio || 1, 1.5);
    canvas.width = width * ratio;
    canvas.height = height * ratio;
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    paint();
  }
  function scale(delta) {
    zoom = Math.max(0.5, Math.min(3, zoom + delta));
    paint();
  }
  addEventListener("resize", resize);
  resize();
  document.querySelector("#zoom-in").onclick = () => scale(0.2);
  document.querySelector("#zoom-out").onclick = () => scale(-0.2);
  document.querySelector("#home").onclick = () => {
    pan = [0, 0];
    zoom = 1;
    follow = null;
    paint();
  };
  canvas.addEventListener(
    "wheel",
    (e) => {
      e.preventDefault();
      scale(e.deltaY > 0 ? -0.1 : 0.1);
    },
    { passive: false },
  );
  canvas.addEventListener("pointerdown", (e) => {
    drag = [e.clientX, e.clientY, ...pan];
    canvas.setPointerCapture(e.pointerId);
  });
  canvas.addEventListener("pointermove", (e) => {
    if (drag) {
      pan = [drag[2] + e.clientX - drag[0], drag[3] + e.clientY - drag[1]];
      paint();
    }
  });
  canvas.addEventListener("pointerup", () => {
    drag = null;
  });
  canvas.addEventListener("pointercancel", () => {
    drag = null;
  });
  canvas.addEventListener("keydown", (e) => {
    if (e.key === "+" || e.key === "=") scale(0.2);
    else if (e.key === "-") scale(-0.2);
    else if (e.key.startsWith("Arrow")) {
      e.preventDefault();
      pan[0] += e.key === "ArrowLeft" ? 30 : e.key === "ArrowRight" ? -30 : 0;
      pan[1] += e.key === "ArrowUp" ? 30 : e.key === "ArrowDown" ? -30 : 0;
      paint();
    }
  });
  addEventListener("message", (e) => {
    if (e.source !== parent || e.origin !== location.origin) return;
    const d = e.data;
    if (d?.type === "oak-admin-players" && Array.isArray(d.players)) {
      players = d.players.slice(0, 100);
      received = Date.now();
      follow = d.follow;
      const target = players.find((p) => p.name === follow);
      if (target) focus(target);
      else paint();
    } else if (d?.type === "oak-admin-focus") focus(d);
  });
  setInterval(() => {
    if (players.length && Date.now() - received > 15000) {
      players = [];
      paint();
    }
  }, 3000);
})();
