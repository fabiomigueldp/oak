import { renderEnvironment } from "./environment.js";
import {
  PAGES,
  ROLES,
  JOB_STATES,
  DIMENSIONS,
  bytes,
  time,
  ago,
  coords,
  health,
  proof,
  mapHash,
  serializeCredential,
  decodeOptions,
} from "./model.js";

const $ = (selector) => document.querySelector(selector);
const state = {
  session: null,
  overview: {},
  page: "",
  places: [],
  generation: 0,
  dirty: false,
  stream: null,
  overviewReceived: 0,
};
const paths = {
  sun: "M12 3v2m0 14v2M3 12h2m14 0h2M5.6 5.6 7 7m10 10 1.4 1.4M5.6 18.4 7 17M17 7l1.4-1.4M16 12a4 4 0 1 1-8 0 4 4 0 0 1 8 0",
  map: "m3 5 6-2 6 2 6-2v16l-6 2-6-2-6 2V5m6-2v16m6-14v16",
  users:
    "M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2m20 0v-2a4 4 0 0 0-3-3.9M13 7a4 4 0 1 1-8 0 4 4 0 0 1 8 0m4-3a4 4 0 0 1 0 8",
  shield: "m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6l8-3",
  activity: "M2 12h4l3-9 6 18 3-9h4",
  server: "M4 3h16v7H4zM4 14h16v7H4zM7 6h.01M7 17h.01",
  key: "M14 7a5 5 0 1 1-3 8l-7 6H2v-4l7-7a5 5 0 0 1 5-3",
  external: "M14 3h7v7m0-7L10 14M9 3H3v18h18v-6",
  moon: "M20 15A9 9 0 0 1 9 3a9 9 0 1 0 11 12",
  logout: "M9 3H3v18h6m5-14 5 5-5 5m-7-5h14",
  menu: "M3 6h18M3 12h18M3 18h18",
  search: "M21 21l-5-5M18 10a8 8 0 1 1-16 0 8 8 0 0 1 16 0",
  terminal: "m4 5 6 7-6 7m9 0h7",
  flask: "M9 3h6m-5 0v6L4 19v2h16v-2L14 9V3M7 15h10",
  close: "m6 6 12 12M6 18 18 6",
  "arrow-right": "M4 12h16m-6-6 6 6-6 6",
  tree: "m12 2 7 8h-3l5 7h-8v5h-2v-5H3l5-7H5l7-8",
  check: "m5 12 4 4L19 6",
  clock: "M12 7v5l3 2M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0",
  plus: "M12 5v14M5 12h14",
};
paths["shield-check"] = paths.shield + "m8 11 3 3 5-5";
function icon(name) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("class", "icon");
  svg.setAttribute("aria-hidden", "true");
  const p = document.createElementNS(svg.namespaceURI, "path");
  p.setAttribute("d", paths[name] || paths.activity);
  svg.append(p);
  return svg;
}
function el(tag, className, ...children) {
  const n = document.createElement(tag);
  if (className) n.className = className;
  for (const child of children.flat())
    if (child != null && child !== false)
      n.append(
        child instanceof Node ? child : document.createTextNode(String(child)),
      );
  return n;
}
function button(label, action, style = "button", symbol) {
  const b = el("button", style, symbol && icon(symbol), label);
  b.type = "button";
  b.addEventListener("click", () => busy(b, action));
  return b;
}
async function busy(b, fn) {
  b.disabled = true;
  try {
    await fn();
  } catch (error) {
    toast(error.message);
  } finally {
    b.disabled = false;
  }
}
let toastTimer;
function toast(message) {
  $("#toast").textContent = message;
  $("#toast").hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    $("#toast").hidden = true;
  }, 6500);
}
async function api(
  path,
  data,
  method = data === undefined ? "GET" : "POST",
  extra = {},
) {
  const response = await fetch("/admin/api" + path, {
    method,
    credentials: "same-origin",
    headers: {
      ...(data === undefined
        ? {}
        : {
            "Content-Type": "application/json",
            "X-Oak-CSRF": state.session?.csrf || "",
          }),
      ...extra,
    },
    ...(data === undefined ? {} : { body: JSON.stringify(data) }),
  });
  const result = await response.json();
  if (!response.ok) {
    if (response.status === 401) showLogin();
    throw new Error(
      result.error ||
        result.detail ||
        "Não foi possível concluir. Tente novamente.",
    );
  }
  return result;
}
const level = () =>
  ({ owner: 3, administrator: 2, moderator: 1, observer: 0 })[
    state.session?.user?.role
  ] ?? -1;
const snapshot = () => state.overview.snapshot || {};
const can = (kind) => Boolean(state.overview.capabilities?.[kind]);
function badge(label, status = "neutral") {
  return el(
    "span",
    "status-badge " + status,
    el("span", "state-dot " + status),
    label,
  );
}
function empty(title, description) {
  return el(
    "div",
    "empty",
    icon("tree"),
    el("h3", "", title),
    el("p", "", description),
  );
}
function heading(title, description, ...actions) {
  return el(
    "div",
    "page-heading",
    el("div", "", el("h1", "", title), description && el("p", "", description)),
    el("div", "page-actions", ...actions),
  );
}
function section(title, ...content) {
  return el(
    "section",
    "section",
    el("div", "section-heading", el("h2", "", title)),
    ...content,
  );
}
function row(title, subtitle, action, tail, symbol = "activity") {
  const n = el(
    action ? "button" : "div",
    "list-row",
    icon(symbol),
    el(
      "div",
      "list-row-main",
      el("strong", "", title),
      el("small", "", subtitle),
    ),
    tail,
  );
  if (action) {
    n.type = "button";
    n.addEventListener("click", () => busy(n, action));
  }
  return n;
}
function replace(node, ...children) {
  node.replaceChildren(
    ...children.flat().filter((c) => c != null && c !== false),
  );
}
function field(label, input, description) {
  return el(
    "label",
    "field",
    el("span", "", label),
    input,
    description && el("small", "", description),
  );
}
function input(value = "", type = "text") {
  const n = el("input");
  n.type = type;
  n.value = value;
  return n;
}
function select(options, value) {
  const n = el("select");
  for (const [id, label] of Object.entries(options)) {
    const o = el("option", "", label);
    o.value = id;
    n.append(o);
  }
  if (value !== undefined) n.value = value;
  return n;
}
function facts(values) {
  return el(
    "dl",
    "detail-facts",
    ...Object.entries(values).flatMap(([k, v]) => [
      el("dt", "", k),
      el("dd", "", v),
    ]),
  );
}
let drawerFocus;
function drawer(kind, ...content) {
  if (kind !== "OPERAÇÃO") state.detailJob = null;
  if (!state.silentDrawer) drawerFocus = document.activeElement;
  $("#detail-kind").textContent = kind;
  replace($("#detail-body"), ...content);
  $("#detail").hidden = false;
  if (!state.silentDrawer) $("#detail-close").focus();
  state.silentDrawer = false;
}
function closeDrawer() {
  state.detailJob = null;
  $("#detail").hidden = true;
  drawerFocus?.focus();
}
function form(title, fields, label, action) {
  const f = el("form", "inline-form", title && el("h3", "", title), ...fields);
  const submit = el("button", "button primary", label);
  submit.type = "submit";
  f.append(submit);
  f.addEventListener("submit", (event) => {
    event.preventDefault();
    busy(submit, action);
  });
  return f;
}

async function operation(kind, params = {}, { onQueued } = {}) {
  const spec = state.overview.capabilities?.[kind];
  if (!spec) throw new Error("Sua conta não tem permissão para esta ação.");
  const key = crypto.randomUUID();
  const submit = async (review) => {
    const job = await api(
      "/jobs",
      { kind, params, ...(review ? { review } : {}) },
      "POST",
      { "Idempotency-Key": key },
    );
    if (kind === "settings_apply") state.dirty = false;
    onQueued?.(job);
    $("#review-dialog").close();
    toast("Operação adicionada à fila.");
    await showJob(job.id);
    await refresh();
  };
  if (!spec.review) return submit();
  const review = await api("/reviews", { kind, params });
  const content = $("#review-content");
  const confirm = button(
    kind === "environment_apply" ? "Aplicar no servidor" : "Confirmar e executar",
    () => submit(review.id),
    "button " + (kind === "restore_backup" ? "danger" : "primary"),
  );
  content.replaceChildren(
    el("h2", "", spec.label),
    el("p", "", review.impact),
    el(
      "ol",
      "review-steps",
      ...(review.steps || []).map((s) => el("li", "", s)),
    ),
  );
  content.querySelector("h2").id = "review-title";
  if (review.command)
    content.append(el("pre", "console-output", review.command));
  if (kind === "server_control")
    content.append(
      facts({
        Ação: {
          start: "Iniciar Minecraft",
          stop: "Parar Minecraft",
          restart: "Reiniciar Minecraft",
        }[params.action],
      }),
    );
  if (kind === "player_action")
    content.append(
      facts({
        Jogador: params.player,
        Ação: params.action,
        Motivo: params.reason,
        ...(params.target ? { Destino: params.target } : {}),
      }),
    );
  if (kind === "restore_backup")
    content.append(facts({ "Arquivo selecionado": params.backup }));
  if (review.changes)
    content.append(
      ...review.changes.map((c) => row(c.label, `${c.before} → ${c.after}`)),
    );
  if (kind === "restore_backup") {
    const confirmInput = input();
    confirm.disabled = true;
    confirmInput.addEventListener("input", () => {
      confirm.disabled = confirmInput.value !== "RESTAURAR";
    });
    content.append(
      field(
        "Digite RESTAURAR para confirmar a substituição do mundo",
        confirmInput,
      ),
    );
  }
  content.append(
    el(
      "div",
      "form-actions",
      button("Voltar", () => $("#review-dialog").close()),
      confirm,
    ),
  );
  $("#review-dialog").showModal();
}
async function showJob(id, background = false) {
  const job = await api("/jobs/" + encodeURIComponent(id));
  state.detailJob = { id, signature: job.state + job.step };
  state.silentDrawer = background;
  drawer(
    "OPERAÇÃO",
    el("h2", "", job.label),
    badge(JOB_STATES[job.state], job.state),
    facts({ Criada: time(job.created, true) }),
    el(
      "ol",
      "review-steps",
      ...(job.steps || []).map((s) =>
        el(
          "li",
          "",
          el(
            "div",
            "",
            el("strong", "", s.label || s.title || s.step),
            el("p", "caption", s.detail || s.message || ""),
          ),
        ),
      ),
    ),
    job.error && el("div", "notice warning", job.error),
    job.result &&
      el(
        "details",
        "technical-details",
        el("summary", "", "Detalhes técnicos"),
        el(
          "pre",
          "console-output",
          JSON.stringify({ id: job.id, ...job.result }, null, 2),
        ),
      ),
    el(
      "div",
      "detail-actions",
      button("Atualizar", () => showJob(id)),
      job.state === "queued" &&
        level() >= 2 &&
        button("Cancelar operação", async () => {
          await api("/jobs/" + id + "/cancel", {});
          await showJob(id);
        }),
      job.state === "interrupted" &&
        level() >= 2 &&
        button("Conferir resultado", async () => {
          await api("/jobs/" + id + "/reconcile", {});
          await showJob(id);
        }),
    ),
  );
}
function applyOverview(data) {
  if (!state.session) return;
  state.overviewReceived = Date.now();
  state.overview = data;
  if (state.detailJob) {
    const updated = data.jobs?.find((j) => j.id === state.detailJob.id);
    if (updated && updated.state + updated.step !== state.detailJob.signature) {
      state.detailJob.signature = updated.state + updated.step;
      showJob(updated.id, true).catch((e) => toast(e.message));
    }
  }
  const s = snapshot(),
    h = health(s);
  $("#sidebar-version").textContent =
    (s.loader || "Minecraft") + " · " + (s.version || "versão indisponível");
  $("#sidebar-dot").className = "state-dot " + h.state;
  $("#connection").replaceChildren(el("span", "state-dot " + h.state), h.label);
  $("#nav-players").textContent = s.fresh
    ? String(s.players?.length || 0)
    : "—";
  const active = (data.jobs || []).find((j) =>
    ["queued", "running", "interrupted"].includes(j.state),
  );
  $("#active-operation").hidden = !active;
  $("#nav-jobs").textContent = active ? "•" : "";
  if (active) {
    $("#active-operation-label").textContent = active.label;
    $("#active-operation-step").textContent = JOB_STATES[active.state];
    $("#active-operation").onclick = () =>
      showJob(active.id).catch((e) => toast(e.message));
  }
  $("#connection-notice").hidden = Boolean(s.fresh);
  $("#connection-notice").textContent =
    "Dados desatualizados. A conexão será retomada automaticamente.";
  if (state.page === "now" && !$("#view").contains(document.activeElement))
    renderNow();
  if (state.page === "world") updateWorld();
}
async function refresh() {
  applyOverview(await api("/overview"));
}
function renderNow() {
  const s = snapshot(),
    h = health(s),
    players = s.players || [],
    points = state.overview.backups || [];
  const cover = el("img");
  cover.src = "/admin/assets/world-cover.png";
  cover.alt = "Registro da floresta de Oak";
  cover.decoding = "async";
  const peek = el(
    "div",
    "atlas-peek",
    cover,
    el(
      "div",
      "peek-content",
      el(
        "div",
        "",
        el(
          "h2",
          "",
          !s.fresh
            ? "Mapa do mundo"
            : players.length
              ? "Jogadores no mundo"
              : "Mapa do mundo",
        ),
        el(
          "p",
          "",
          s.fresh
            ? `${players.length} jogadores conectados · limite ${s.max_players || "—"}`
            : `Última amostra: ${players.length} jogadores`,
        ),
      ),
      button(
        "Explorar mundo",
        () => navigate("world"),
        "button",
        "arrow-right",
      ),
    ),
  );
  const protection = el(
    "div",
    "panel protection-panel",
    icon("shield-check"),
    el("h2", "", points.length ? "Backups locais" : "Nenhum backup"),
    el(
      "p",
      "muted",
      points.length
        ? `${points.length} pontos disponíveis na Oracle. Último: ${ago(points[0].created).toLowerCase()}.`
        : "Um ponto de recuperação guarda o mundo e sua configuração.",
    ),
    points[0] && badge(proof(points[0]).label, proof(points[0]).state),
    can("backup") &&
      button("Criar backup", () => newBackup(), "button primary", "plus"),
    button(
      "Ver backups",
      () => navigate("backups"),
      "text-button",
      "arrow-right",
    ),
  );
  const resources = el(
    "div",
    "resource-strip",
    ...[
      [
        bytes(
          Number.isFinite(s.memory?.total) &&
            Number.isFinite(s.memory?.available)
            ? s.memory.total - s.memory.available
            : undefined,
        ),
        "Memória em uso",
      ],
      [bytes(s.disk?.free), "Livres na Oracle"],
      [s.load?.[0]?.toFixed(2) || "—", `Carga · ${s.cpu_count || "—"} CPUs`],
      [s.tps == null ? "Não medido" : String(s.tps), "TPS do jogo"],
    ].map(([value, label]) =>
      el("div", "resource", el("strong", "mono", value), el("span", "", label)),
    ),
  );
  replace(
    $("#view"),
    heading(
      "Agora",
      h.description,
      can("save") &&
        button("Salvar mundo", () => operation("save"), "button", "check"),
    ),
    el(
      "div",
      "columns",
      el(
        "div",
        "",
        peek,
        resources,
        section(
          "Jogadores conectados",
          players.length
            ? el("div", "list", ...players.map(playerRow))
            : empty(
                "Ninguém conectado",
                "Os jogadores aparecerão aqui quando entrarem.",
              ),
        ),
      ),
      el(
        "div",
        "",
        protection,
        section(
          "Últimos acontecimentos",
          ...(state.overview.events || [])
            .slice(0, 5)
            .map((e) =>
              row(eventLabel(e), time(e.created, true), null, null, "clock"),
            ),
        ),
      ),
    ),
  );
}
function eventLabel(e) {
  const labels = {
    "Player joined": "Jogador entrou",
    "Player left": "Jogador saiu",
    "Place added": "Lugar salvo",
    "Place removed": "Lugar removido",
    "Schedule created": "Rotina criada",
    "Schedule changed": "Rotina alterada",
    "Passkey registered": "Chave registrada",
    "Sign in": "Acesso ao painel",
    "Account permissions changed": "Permissões alteradas",
    "Job queued": "Operação na fila",
    "Job completed": "Operação concluída",
    "Job failed": "Operação falhou",
  };
  return labels[e.title] || e.title || e.kind;
}
function playerRow(p) {
  return row(
    p.name,
    `${p.platform === "bedrock" ? "Bedrock" : "Java"} · ${DIMENSIONS[p.dimension] || "Dimensão indisponível"} · ${coords(p.position)}`,
    () => playerDetail(p),
    badge(
      snapshot().fresh ? "Conectado" : "Última observação",
      snapshot().fresh ? "good" : "warning",
    ),
    "users",
  );
}
function playerDetail(p) {
  const actions = select({
    kick: "Desconectar",
    ban: "Banir",
    pardon: "Remover banimento",
    ...(level() >= 2
      ? {
          whitelist_add: "Autorizar acesso",
          whitelist_remove: "Remover da lista",
          teleport: "Teleportar até jogador",
        }
      : {}),
    ...(level() === 3
      ? { op: "Conceder operador", deop: "Remover operador" }
      : {}),
  });
  const reason = input("Ação administrativa"),
    target = input();
  const actionForm =
    level() >= 1 &&
    form(
      "Ação administrativa",
      [
        field("Ação", actions),
        field("Motivo", reason),
        field("Jogador de destino (somente teleporte)", target),
      ],
      "Revisar ação",
      () =>
        operation("player_action", {
          player: p.name,
          action: actions.value,
          reason: reason.value,
          ...(actions.value === "teleport" ? { target: target.value } : {}),
        }),
    );
  drawer(
    "JOGADOR",
    el("span", "avatar detail-avatar", p.name.slice(0, 2)),
    el("h2", "", p.name),
    facts({
      Plataforma: p.platform === "bedrock" ? "Bedrock" : "Java",
      Dimensão: DIMENSIONS[p.dimension] || "Indisponível",
      Coordenadas: coords(p.position),
      "Posição coletada": ago(p.sampled_at),
    }),
    el(
      "div",
      "detail-actions",
      p.position &&
        button("Ver no mundo", async () => {
          closeDrawer();
          await navigate("world");
          focusMap(p);
        }),
      p.position &&
        button("Acompanhar jogador", async () => {
          closeDrawer();
          await navigate("world");
          focusMap(p);
          following = p.name;
          updateWorld();
          toast(
            "Acompanhando " + p.name + ". Use Soltar câmera para explorar.",
          );
        }),
      p.position && level() >= 1 && button("Salvar lugar", () => placeForm(p)),
    ),
    actionForm,
  );
}
async function renderPlayers() {
  const data = await api("/players");
  if (state.page !== "players") return;
  const search = input();
  search.placeholder = "Buscar jogador";
  search.setAttribute("aria-label", "Buscar jogador");
  const list = el("div", "list");
  const draw = () =>
    list.replaceChildren(
      ...data.players
        .filter((p) =>
          p.name.toLowerCase().includes(search.value.toLowerCase()),
        )
        .map(playerRow),
    );
  search.addEventListener("input", draw);
  draw();
  replace(
    $("#view"),
    heading("Jogadores"),
    search,
    list,
    section(
      "Visitas recentes",
      ...(data.sessions || [])
        .slice(0, 30)
        .map((p) =>
          row(
            p.name || p.player,
            `${time(p.joined, true)} → ${p.left_at ? time(p.left_at, true) : "Conectado"}`,
          ),
        ),
    ),
    !data.players.length &&
      empty(
        "Ninguém conectado agora",
        "O histórico de visitas continuará disponível.",
      ),
  );
}
let worldFrame,
  following = null,
  historySamples = [];
async function renderWorld() {
  const root = $("#world-view");
  if (!worldFrame) {
    worldFrame = el("iframe", "world-frame");
    worldFrame.title = state.session.demo
      ? "Atlas isométrico de demonstração"
      : "Mapa tridimensional de Oak";
    worldFrame.referrerPolicy = "same-origin";
    worldFrame.src = state.session.demo
      ? "/admin/assets/demo-map.html"
      : "/map/";
    const inspector = el("aside", "world-inspector");
    inspector.id = "world-inspector";
    const status = el("div", "world-foot");
    status.id = "world-status";
    replace(
      root,
      heading(
        "Mundo",
        null,
        button("Ambiente", () => navigate("environment"), "button", "sun"),
        can("map_refresh") &&
          button(
            "Atualizar terreno",
            () => operation("map_refresh"),
            "button",
            "map",
          ),
      ),
      el(
        "div",
        "world-layout",
        el("div", "world-canvas", worldFrame),
        inspector,
      ),
      status,
    );
    root.append(timeline());
    worldFrame.addEventListener("load", updateWorld);
  }
  state.places = (await api("/places")).places;
  updateWorld();
}
function focusMap(p) {
  following = null;
  if (!p.position) return;
  worldFrame.contentWindow.postMessage(
    { type: "oak-admin-focus", position: p.position, dimension: p.dimension },
    location.origin,
  );
}
function updateWorld() {
  if (!worldFrame) return;
  const s = state.historySample || snapshot(),
    panel = $("#world-inspector");
  if (!panel.contains(document.activeElement)) {
    panel.replaceChildren(
      el("span", "eyebrow", "JOGADORES NO MUNDO"),
      ...(s.players || []).map((p) =>
        row(
          p.name,
          coords(p.position),
          () => {
            focusMap(p);
            playerDetail(p);
          },
          null,
          "users",
        ),
      ),
      section(
        "Lugares salvos",
        ...state.places.map((p) =>
          row(
            p.name,
            DIMENSIONS[p.dimension],
            () => placeDetail(p),
            null,
            "map",
          ),
        ),
        level() >= 1 &&
          button("Novo lugar", () => placeForm(), "text-button", "plus"),
      ),
      el(
        "p",
        "caption",
        "A posição de cada jogador é uma amostra. O terreno segue o ritmo da renderização.",
      ),
    );
  }
  $("#world-status").replaceChildren(
    el(
      "span",
      "",
      `${state.historySample ? "Histórico" : "Jogadores"}: ${state.historySample ? time(s.sampled_at, true) : ago(s.sampled_at)} · Terreno atual: ${ago(snapshot().map?.last_finished)}`,
    ),
    el(
      "span",
      "",
      state.session.demo
        ? "Atlas ilustrativo"
        : s.map?.running
          ? "Renderizando terreno…"
          : "BlueMap · visão 3D",
    ),
  );
  sendWorldPlayers();
}
let livePositions = null, positionStream = null, hadLivePositions = false;
function syncPositionStream() {
  const active = state.session && !state.session.demo && state.page === "world" && !state.historySample && !document.hidden;
  if (!active) {
    positionStream?.close();
    positionStream = null;
    livePositions = null;
  } else if (!positionStream) {
    positionStream = new EventSource("/admin/api/positions/stream");
    positionStream.onmessage = (event) => {
      try {
        const frame = JSON.parse(event.data);
        livePositions = frame.fresh ? frame : null;
        if (frame.fresh) hadLivePositions = true;
        sendWorldPlayers();
      } catch { livePositions = null; }
    };
    positionStream.onerror = () => { livePositions = null; };
    positionStream.addEventListener("session-ended", showLogin);
  }
}
setInterval(syncPositionStream, 1000);
addEventListener("visibilitychange", syncPositionStream);
addEventListener("pagehide", () => positionStream?.close());
function sendWorldPlayers() {
  if (!worldFrame || !state.session || state.page !== "world") return;
  const live = livePositions && Date.now() / 1000 - livePositions.sampled_at < 3 ? livePositions : null;
  const s = state.historySample || live || (hadLivePositions ? {fresh: false, players: []} : snapshot());
  const fresh =
    s.fresh && (state.historySample || Date.now() / 1000 - s.sampled_at < 30);
  worldFrame.contentWindow.postMessage(
    {
      type: "oak-admin-players",
      players: fresh ? s.players : [],
      sampled_at: s.sampled_at,
      follow: following,
      history: Boolean(state.historySample),
      realtime: !state.historySample && Boolean(live),
    },
    location.origin,
  );
}
let worldRefreshPending = false;
setInterval(async () => {
  if (!state.session || state.page !== "world") return;
  // Keep the iframe lease alive independently of SSE event frequency.
  sendWorldPlayers();
  if (
    worldRefreshPending ||
    (snapshot().fresh && Date.now() - state.overviewReceived < 10000)
  )
    return;
  worldRefreshPending = true;
  try {
    await refresh();
  } catch {
    // The existing stale-state notice covers connection loss; never renew old samples.
  } finally {
    worldRefreshPending = false;
  }
}, 5000);
function timeline() {
  const range = input(0, "range");
  range.min = 0;
  range.max = 0;
  range.disabled = true;
  const label = el("span", "caption", "Posições ao vivo"),
    period = select({
      1: "Última hora",
      6: "Últimas 6 horas",
      24: "Últimas 24 horas",
    });
  period.setAttribute("aria-label", "Período do histórico");
  range.setAttribute("aria-label", "Instante do histórico de jogadores");
  range.addEventListener("input", () => {
    state.historySample = historySamples[Number(range.value)];
    following = null;
    label.textContent =
      "Posições de " +
      time(state.historySample?.at, true) +
      " · o terreno não volta no tempo";
    updateWorld();
  });
  return el(
    "div",
    "timeline-control",
    el(
      "div",
      "timeline-label",
      label,
      el(
        "div",
        "page-actions",
        period,
        button("Carregar histórico", async () => {
          historySamples = (await api("/samples?hours=" + period.value))
            .samples;
          range.max = Math.max(0, historySamples.length - 1);
          range.value = range.max;
          range.disabled = !historySamples.length;
          label.textContent = historySamples.length + " amostras disponíveis";
        }),
        button("Ao vivo", () => {
          state.historySample = null;
          label.textContent = "Posições ao vivo";
          updateWorld();
        }),
        button("Soltar câmera", () => {
          following = null;
          toast("Câmera livre.");
        }),
      ),
    ),
    range,
  );
}
function placeForm(p = {}) {
  const name = input(),
    note = el("textarea"),
    dimension = select(DIMENSIONS, p.dimension),
    xyz = (p.position || [0, 64, 0]).map((v) => input(v, "number"));
  drawer(
    "LUGAR",
    el("h2", "", "Guardar uma descoberta."),
    form(
      "Novo lugar",
      [
        field("Nome", name),
        field("Dimensão", dimension),
        ...xyz.map((n, i) => field(["X", "Y", "Z"][i], n)),
        field("Anotações", note),
      ],
      "Salvar lugar",
      async () => {
        await api("/places", {
          name: name.value,
          note: note.value,
          dimension: dimension.value,
          x: Number(xyz[0].value),
          y: Number(xyz[1].value),
          z: Number(xyz[2].value),
        });
        state.places = (await api("/places")).places;
        closeDrawer();
        updateWorld();
        toast("Lugar salvo.");
      },
    ),
  );
}
function placeDetail(p) {
  drawer(
    "LUGAR SALVO",
    el("h2", "", p.name),
    el("p", "", p.note),
    facts({
      Dimensão: DIMENSIONS[p.dimension],
      Coordenadas: coords([p.x, p.y, p.z]),
    }),
    button("Ir até o lugar", () =>
      focusMap({ position: [p.x, p.y, p.z], dimension: p.dimension }),
    ),
    level() >= 1 &&
      button(
        "Remover lugar",
        async () => {
          await api("/places/" + p.id, {}, "DELETE");
          state.places = (await api("/places")).places;
          closeDrawer();
          updateWorld();
        },
        "button danger",
      ),
  );
}
function newBackup() {
  const name = input("Backup manual");
  name.maxLength = 80;
  drawer(
    "BACKUP",
    el("h2", "", "Criar backup"),
    el(
      "p",
      "",
      "O mundo será salvo, copiado e verificado. O arquivo ficará na Oracle.",
    ),
    form(null, [field("Nome do ponto", name)], "Criar backup", () =>
      operation("backup", { name: name.value }),
    ),
  );
}
async function renderBackups() {
  const data = await api("/backups");
  if (state.page !== "backups") return;
  replace(
    $("#view"),
    heading(
      "Backups",
      null,
      can("backup") &&
        button("Criar backup", newBackup, "button primary", "plus"),
    ),
    el(
      "div",
      "backup-layout",
      el(
        "div",
        "list",
        ...data.backups.map((p) =>
          row(
            p.name || p.id,
            `${time(p.created, true)} · ${bytes(p.bytes)} · ${p.source === "existing" ? "Rotina existente" : "Oak"}`,
            () => backupDetail(p),
            badge(proof(p).label, proof(p).state),
            "shield",
          ),
        ),
        !data.backups.length &&
          empty(
            "Nenhum ponto disponível",
            "Crie o primeiro backup antes de fazer mudanças importantes.",
          ),
      ),
      el(
        "aside",
        "panel",
        el("h2", "", "Armazenamento local"),
        el(
          "p",
          "muted",
          "Até 14 pontos do painel e orçamento de 20 GiB. A rotina diária existente mantém sua própria retenção.",
        ),
        facts({
          "Reserva de disco": "20 GiB",
          "Cópia externa": "Não configurada",
          "Rotina existente": "Diária, às 05:00",
        }),
        el(
          "p",
          "caption",
          "Integridade confirma o arquivo. Extração verifica os dados recuperados. O teste de inicialização abre a cópia em um ambiente isolado.",
        ),
      ),
    ),
  );
}
function backupDetail(p) {
  drawer(
    "PONTO DE RECUPERAÇÃO",
    el("h2", "", p.name || "Backup da rotina existente"),
    badge(proof(p).label, proof(p).state),
    facts({
      Criado: time(p.created, true),
      Tamanho: bytes(p.bytes),
      Local: "Oracle VM",
      "Inicialização testada": p.restoration?.playable_boot_tested
        ? "Sim"
        : "Ainda não",
    }),
    el("p", "caption", p.id),
    el(
      "div",
      "detail-actions",
      can("verify_backup") &&
        button("Verificar extração", () =>
          operation("verify_backup", { backup: p.id, boot: false }),
        ),
      can("verify_backup") &&
        button("Testar inicialização isolada", () =>
          operation("verify_backup", { backup: p.id, boot: true }),
        ),
      can("restore_backup") &&
        p.fingerprint &&
        button(
          "Restaurar este ponto",
          () =>
            operation("restore_backup", {
              backup: p.id,
              fingerprint: p.fingerprint,
            }),
          "button danger",
        ),
    ),
    !p.fingerprint &&
      el(
        "div",
        "notice",
        "Verifique este arquivo antes de preparar uma restauração.",
      ),
  );
}
async function renderOperations() {
  const data = await api("/jobs");
  if (state.page !== "operations") return;
  replace(
    $("#view"),
    heading("Operações"),
    el(
      "div",
      "list",
      ...data.jobs.map((j) =>
        row(
          j.label,
          time(j.created, true),
          () => showJob(j.id),
          badge(JOB_STATES[j.state], j.state),
        ),
      ),
    ),
    !data.jobs.length &&
      empty(
        "Nenhuma operação por enquanto",
        "As ações do painel terão seus resultados registrados aqui.",
      ),
  );
  if (level() >= 2) {
    const schedules = await api("/schedules");
    if (state.page !== "operations") return;
    const kind = select({
        backup: "Criar backup",
        save: "Salvar mundo",
        map_refresh: "Atualizar mapa",
      }),
      minutes = input(360, "number");
    minutes.min = 15;
    minutes.max = 10080;
    const label = input("Rotina de cuidado");
    $("#view").append(
      section(
        "Rotinas",
        el(
          "p",
          "caption",
          "O backup diário existente continua às 05:00 (São Paulo). As rotinas abaixo são adicionais.",
        ),
        ...schedules.schedules.map((s) =>
          row(
            s.label,
            `A cada ${s.interval_minutes} minutos · próxima: ${time(s.next_run, true)}`,
            null,
            el(
              "div",
              "page-actions",
              button(
                s.enabled ? "Pausar" : "Ativar",
                async () => {
                  await api(
                    "/schedules/" + s.id,
                    { enabled: !s.enabled },
                    "PATCH",
                  );
                  await renderOperations();
                },
                "button small",
              ),
              button(
                "Remover",
                async () => {
                  await api("/schedules/" + s.id, {}, "DELETE");
                  await renderOperations();
                },
                "button small",
              ),
            ),
          ),
        ),
        form(
          "Nova rotina",
          [
            field("Nome", label),
            field("Ação", kind),
            field("Intervalo em minutos", minutes),
          ],
          "Criar rotina",
          async () => {
            await api("/schedules", {
              label: label.value,
              kind: kind.value,
              params: kind.value === "backup" ? { name: label.value } : {},
              interval_minutes: Number(minutes.value),
            });
            await renderOperations();
          },
        ),
      ),
    );
  }
  $("#view").append(
    section(
      "Registro de atividade",
      ...(state.overview.events || []).map((e) =>
        row(eventLabel(e), time(e.created, true)),
      ),
    ),
  );
}
async function renderServer() {
  if (level() < 2) {
    replace(
      $("#view"),
      heading(
        "Servidor",
        "Sua conta pode acompanhar a visão geral. A configuração exige acesso de administrador.",
      ),
    );
    return;
  }
  const config = await api("/configuration");
  if (state.page !== "server") return;
  const controls = {};
  const fields = Object.entries(config.fields).map(([key, f]) => {
    let n =
      f.type === "select"
        ? select(
            Object.fromEntries(
              f.options.map((v) => [
                v,
                {
                  peaceful: "Pacífica",
                  easy: "Fácil",
                  normal: "Normal",
                  hard: "Difícil",
                  survival: "Sobrevivência",
                  creative: "Criativo",
                  adventure: "Aventura",
                  spectator: "Espectador",
                }[v] || v,
              ]),
            ),
            config.values[key],
          )
        : input(
            config.values[key] ?? "",
            f.type === "number"
              ? "number"
              : f.type === "boolean"
                ? "checkbox"
                : "text",
          );
    if (f.type === "boolean") n.checked = Boolean(config.values[key]);
    if (f.min != null) n.min = f.min;
    if (f.max != null && f.type === "number") n.max = f.max;
    if (f.type === "text") n.maxLength = f.max;
    n.addEventListener("input", () => {
      state.dirty = true;
    });
    controls[key] = n;
    return field(f.label, n, f.description);
  });
  const edit = form(
    "Configuração do jogo",
    [
      config.pending_restart &&
        el(
          "div",
          "notice warning",
          "Configuração gravada. O painel ainda não confirmou um reinício para aplicá-la.",
        ),
      el("div", "form-grid", ...fields),
      el(
        "p",
        "caption",
        "Gravar não reinicia o jogo. Revise e reinicie em uma operação separada para aplicar.",
      ),
    ],
    "Revisar alterações",
    async () => {
      const changes = {};
      for (const [key, n] of Object.entries(controls)) {
        const value =
          config.fields[key].type === "boolean"
            ? n.checked
            : config.fields[key].type === "number"
              ? Number(n.value)
              : n.value;
        if (value !== config.values[key]) changes[key] = value;
      }
      if (!Object.keys(changes).length)
        return toast("Nenhuma alteração para gravar.");
      await operation("settings_apply", { changes, revision: config.revision });
    },
  );
  replace(
    $("#view"),
    heading("Servidor"),
    el(
      "div",
      "page-actions",
      ...Object.entries({
        start: "Iniciar servidor",
        restart: "Reiniciar servidor",
        stop: "Parar servidor",
      }).map(([action, label]) =>
        button(
          label,
          () => operation("server_control", { action }),
          "button" + (action === "stop" ? " danger" : ""),
        ),
      ),
      button("Backup e reinício", () =>
        operation("maintenance", { restart: true }),
      ),
    ),
    edit,
    section(
      "Serviços",
      ...(snapshot().services || []).map((s) =>
        row(
          s.id,
          `${s.ActiveState} · ${s.SubState} · ${s.Result || ""}`,
          async () => {
            const logs = await api("/logs/" + encodeURIComponent(s.id));
            drawer(
              "REGISTRO DO SERVIÇO",
              el("h2", "", s.id),
              el("pre", "console-output", logs.lines.join("\n")),
            );
          },
          null,
          "server",
        ),
      ),
    ),
  );
}
async function passkey(enroll = false) {
  if (!navigator.credentials)
    throw new Error("Este navegador não oferece chaves de acesso.");
  const token = location.hash.startsWith("#enroll=")
    ? location.hash.slice(8)
    : null;
  const options = await api(
    enroll ? "/auth/enroll/options" : "/auth/options",
    enroll && token ? { token } : {},
  );
  const credential = await navigator.credentials[enroll ? "create" : "get"]({
    publicKey: decodeOptions(options.options),
  });
  if (!credential) throw new Error("A chave não foi confirmada.");
  const result = await api(enroll ? "/auth/enroll/verify" : "/auth/verify", {
    challenge_id: options.challenge_id,
    credential: serializeCredential(credential),
    ...(enroll ? { name: "Chave de acesso pessoal" } : {}),
  });
  if (token) history.replaceState(null, "", location.pathname + "#now");
  await enter(result);
}
async function renderAccess() {
  const data = await api("/access");
  if (state.page !== "access") return;
  replace(
    $("#view"),
    heading(
      "Acesso",
      null,
      button("Adicionar chave", () => passkey(true), "button primary", "key"),
    ),
    section(
      "Minhas chaves",
      ...data.credentials.map((c) =>
        row(
          c.name,
          time(c.created, true),
          null,
          data.credentials.length > 1 &&
            button(
              "Remover",
              async () => {
                await api(
                  "/access/credentials/" + encodeURIComponent(c.id),
                  {},
                  "DELETE",
                );
                await renderAccess();
              },
              "button small",
            ),
        ),
      ),
    ),
  );
  if (level() === 3) {
    const name = input(),
      role = select(ROLES, "observer");
    $("#view").append(
      section(
        "Pessoas e permissões",
        ...(data.users || []).map((u) =>
          row(
            u.name,
            `${ROLES[u.role]} · ${u.disabled ? "Acesso suspenso" : u.credential_count + " chave(s)"}`,
            u.id !== state.session.user.id
              ? () => {
                  const roles = select(ROLES, u.role),
                    disabled = input("", "checkbox");
                  disabled.checked = Boolean(u.disabled);
                  drawer(
                    "PERMISSÕES",
                    el("h2", "", u.name),
                    form(
                      "Alterar acesso",
                      [
                        field("Permissão", roles),
                        field("Suspender acesso", disabled),
                        el(
                          "p",
                          "caption",
                          "Salvar encerra as sessões desta pessoa, pausa suas rotinas e cancela suas operações ainda na fila.",
                        ),
                      ],
                      "Salvar e encerrar sessões",
                      async () => {
                        await api(
                          "/access/users/" + u.id,
                          { role: roles.value, disabled: disabled.checked },
                          "PATCH",
                        );
                        closeDrawer();
                        await renderAccess();
                      },
                    ),
                  );
                }
              : null,
          ),
        ),
      ),
      form(
        "Convidar uma pessoa",
        [field("Nome", name), field("Permissão", role)],
        "Gerar convite privado",
        async () => {
          const invite = await api("/access/invite", {
            name: name.value,
            role: role.value,
          });
          const link = input(invite.url);
          link.readOnly = true;
          drawer(
            "CONVITE PRIVADO",
            el("h2", "", "Pronto para compartilhar."),
            el(
              "p",
              "",
              "Este link permite registrar uma chave e expira em 15 minutos. Compartilhe apenas com a pessoa convidada.",
            ),
            field("Link de uso único", link),
            button("Copiar convite", async () => {
              await navigator.clipboard.writeText(invite.url);
              toast("Convite copiado.");
            }),
          );
        },
      ),
    );
  }
}
function consoleView() {
  const command = input();
  command.placeholder = "Ex.: list";
  command.autocomplete = "off";
  drawer(
    "CONSOLE AVANÇADO",
    el("h2", "", "Uma ação, com intenção."),
    el(
      "p",
      "",
      "Cada comando exige revisão e fica registrado. Use as ações do painel para salvar, parar ou reiniciar o servidor.",
    ),
    form(
      "Comando do Minecraft",
      [field("Comando", command)],
      "Revisar comando",
      () => operation("console", { command: command.value }),
    ),
  );
}
async function navigate(page) {
  if (!PAGES[page]) page = "now";
  if (
    state.dirty &&
    page !== state.page &&
    !window.confirm(
      "Descartar as alterações de configuração que ainda não foram gravadas?",
    )
  ) {
    history.replaceState(null, "", "#" + state.page);
    return;
  }
  state.dirty = false;
  state.page = page;
  const generation = ++state.generation;
  history.replaceState(null, "", "#" + page);
  $("#page-label").textContent = PAGES[page];
  document.title = `${PAGES[page]} · Oak`;
  for (const b of document.querySelectorAll("[data-page]")) {
    if (b.dataset.page === page) b.setAttribute("aria-current", "page");
    else b.removeAttribute("aria-current");
  }
  $("#sidebar").classList.remove("open");
  $("#mobile-menu").setAttribute("aria-expanded", "false");
  syncNavigation();
  $("#view").hidden = page === "world";
  $("#world-view").hidden = page !== "world";
  if (page !== "world") replace($("#view"), el("p", "muted", "Carregando…"));
  try {
    await {
      now: renderNow,
      world: renderWorld,
      environment: () =>
        renderEnvironment({
          el,
          button,
          field,
          input,
          select,
          heading,
          api,
          operation,
          state,
          navigate,
          can,
        }),
      players: renderPlayers,
      backups: renderBackups,
      operations: renderOperations,
      server: renderServer,
      access: renderAccess,
    }[page]();
  } catch (error) {
    if (generation === state.generation)
      replace(
        $("#view"),
        empty("Não foi possível carregar", error.message),
        button("Tentar novamente", () => navigate(page)),
      );
  }
}
async function enter(session) {
  state.session = session;
  $("#login").hidden = true;
  $("#app").hidden = false;
  $("#demo-banner").hidden = !session.demo;
  $("#account-name").textContent = session.user.name;
  $("#account-role").textContent = ROLES[session.user.role];
  $("#account-avatar").textContent = session.user.name.slice(0, 2);
  $("#console-open").hidden = level() < 3;
  await refresh();
  await navigate(location.hash.slice(1) || "now");
  state.stream?.close();
  state.stream = new EventSource("/admin/api/stream");
  state.stream.onmessage = (event) => {
    try {
      applyOverview(JSON.parse(event.data));
    } catch {
      toast("Não foi possível atualizar os dados.");
    }
  };
  state.stream.onerror = () => {
    state.overview.snapshot && (state.overview.snapshot.fresh = false);
    applyOverview(state.overview);
  };
  state.stream.addEventListener("session-ended", showLogin);
}
function showLogin() {
  state.stream?.close();
  positionStream?.close();
  positionStream = null;
  livePositions = null;
  hadLivePositions = false;
  if (state.session) $("#login-demo").hidden = !state.session.demo;
  state.session = null;
  state.overview = {};
  state.places = [];
  state.page = "";
  state.generation++;
  state.historySample = null;
  state.detailJob = null;
  state.dirty = false;
  following = null;
  worldFrame?.remove();
  worldFrame = null;
  $("#view").replaceChildren();
  $("#world-view").replaceChildren();
  $("#detail-body").replaceChildren();
  $("#detail").hidden = true;
  $("#review-dialog").close();
  $("#search-dialog").close();
  $("#app").hidden = true;
  $("#login").hidden = false;
  $("#login-status").textContent =
    "Entre com sua chave de acesso para continuar.";
}
function searchResults() {
  const query = $("#global-search").value.toLocaleLowerCase("pt-BR");
  const entries = [
    ...Object.entries(PAGES).map(([id, label]) => ({
      label,
      group: "Página",
      run: () => navigate(id),
    })),
    ...(snapshot().players || []).map((p) => ({
      label: p.name,
      group: "Jogador",
      run: () => playerDetail(p),
    })),
    ...state.places.map((p) => ({
      label: p.name,
      group: "Lugar",
      run: async () => {
        await navigate("world");
        placeDetail(p);
      },
    })),
    ...Object.entries(state.overview.capabilities || {})
      .filter(([k]) => ["save", "backup", "map_refresh"].includes(k))
      .map(([k, s]) => ({
        label: s.label,
        group: "Ação",
        run: () => (k === "backup" ? newBackup() : operation(k)),
      })),
  ];
  const matches = entries.filter((e) =>
    e.label.toLocaleLowerCase("pt-BR").includes(query),
  );
  $("#search-results").replaceChildren(
    ...matches.map((e) =>
      button(
        el(
          "span",
          "search-result-content",
          el("span", "search-result-label", e.label),
          el("small", "", e.group),
        ),
        async () => {
          $("#search-dialog").close();
          await e.run();
        },
        "search-result",
      ),
    ),
  );
  if (!matches.length) {
    const message = el("p", "search-empty", "Nenhum resultado");
    message.setAttribute("role", "status");
    $("#search-results").append(message);
  }
}
function openSearch() {
  searchResults();
  $("#search-dialog").showModal();
  $("#global-search").focus();
}
for (const n of document.querySelectorAll("[data-icon]"))
  n.replaceChildren(icon(n.dataset.icon));
for (const b of document.querySelectorAll("[data-page]"))
  b.addEventListener("click", () => navigate(b.dataset.page));
$("#detail-close").onclick = closeDrawer;
$("#console-open").onclick = consoleView;
$("#search-open").onclick = openSearch;
$("#global-search").addEventListener("input", searchResults);
$("#search-dialog").addEventListener("keydown", (e) => {
  const buttons = [...$("#search-results").querySelectorAll("button")],
    index = buttons.indexOf(document.activeElement);
  if (e.key === "ArrowDown" || e.key === "ArrowUp") {
    e.preventDefault();
    buttons[
      (index + (e.key === "ArrowDown" ? 1 : -1) + buttons.length) %
        buttons.length
    ]?.focus();
  }
  if (e.key === "Enter" && document.activeElement === $("#global-search")) {
    e.preventDefault();
    buttons[0]?.click();
  }
});
document.addEventListener("keydown", (e) => {
  if (
    (e.ctrlKey || e.metaKey) &&
    e.key.toLowerCase() === "k" &&
    state.session
  ) {
    e.preventDefault();
    openSearch();
  }
  if (
    e.key === "Escape" &&
    !$("#review-dialog").open &&
    !$("#search-dialog").open
  )
    closeDrawer();
});
$("#mobile-menu").onclick = () => {
  const opened = $("#sidebar").classList.toggle("open");
  $("#mobile-menu").setAttribute("aria-expanded", String(opened));
  syncNavigation();
};
const mobileLayout = matchMedia("(max-width: 700px)");
function syncNavigation() {
  $("#sidebar").inert =
    mobileLayout.matches && !$("#sidebar").classList.contains("open");
}
mobileLayout.addEventListener("change", syncNavigation);
syncNavigation();
$("#search-open").setAttribute("aria-label", "Buscar ou executar uma ação");
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    $("#sidebar").classList.remove("open");
    $("#mobile-menu").setAttribute("aria-expanded", "false");
    syncNavigation();
  }
});
$("#theme-toggle").onclick = () => {
  const theme =
    document.documentElement.dataset.theme === "night" ? "day" : "night";
  document.documentElement.dataset.theme = theme;
  try {
    localStorage.setItem("oak-control-theme", theme);
  } catch {}
};
try {
  const theme = localStorage.getItem("oak-control-theme");
  if (["day", "night"].includes(theme))
    document.documentElement.dataset.theme = theme;
} catch {}
$("#logout").onclick = () =>
  busy($("#logout"), async () => {
    await api("/logout", {});
    showLogin();
  });
$("#login-passkey").onclick = () => busy($("#login-passkey"), () => passkey());
$("#login-enroll").onclick = () =>
  busy($("#login-enroll"), () => passkey(true));
$("#login-demo").onclick = () =>
  busy($("#login-demo"), async () => enter(await api("/auth/demo", {})));
window.addEventListener("beforeunload", (e) => {
  if (state.dirty) {
    e.preventDefault();
    e.returnValue = "";
  }
});
window.addEventListener("hashchange", () => {
  if (state.session) navigate(location.hash.slice(1));
});
window.addEventListener("message", (e) => {
  if (e.origin !== location.origin || e.source !== worldFrame?.contentWindow)
    return;
  if (e.data?.type === "oak-admin-map-unavailable") {
    following = null;
    toast("Esta dimensão ainda não tem um mapa renderizado.");
  }
  if (e.data?.type === "oak-admin-select") {
    const p = snapshot().players?.find((p) => p.name === e.data.name);
    if (p) playerDetail(p);
  }
});
api("/session")
  .then(async (session) => {
    if (session.user) await enter(session);
    else {
      showLogin();
      $("#login-demo").hidden = !session.demo;
      const enroll = location.hash.startsWith("#enroll=");
      $("#login-enroll").hidden = !enroll;
      $("#login-passkey").hidden = enroll;
    }
  })
  .catch((error) => {
    $("#login-status").textContent = error.message;
  });
