/* Environment editor. Drafts remain local until the existing review/job flow accepts them. */
export async function renderEnvironment(ctx) {
  const {
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
  } = ctx;
  const generation = state.generation;
  let current = await api("/environment");
  if (generation !== state.generation) return;
  const root = document.querySelector("#view");
  root.replaceChildren(
    heading(
      "Ambiente",
      null,
      button("Abrir mapa", () => navigate("world"), "button", "map"),
    ),
  );
  if (!current.available) {
    root.append(
      el("p", "notice", current.message || "O controlador está indisponível."),
    );
    return;
  }
  let draft = structuredClone(current.policy);
  let revision = current.revision;
  let baseline = JSON.stringify(draft);
  const editable = can("environment_apply");
  const controls = new Map();
  const weatherNames = {
    clear: "Céu limpo",
    rain: "Chuva",
    thunder: "Tempestade",
  };
  const phaseNames = ["Dia", "Entardecer", "Noite", "Amanhecer"];
  const phases = ["day", "dusk", "night", "dawn"];
  const live = el("div", "environment-live");
  const notice = el("p", "notice");
  notice.setAttribute("role", "status");
  const editor = el("form", "environment-editor");
  const total = el("span", "environment-total");
  const timeline = el("div", "environment-timeline");
  const segments = phases.map((key, index) => {
    const segment = el(
      "span",
      `environment-phase phase-${key}`,
      phaseNames[index],
    );
    timeline.append(segment);
    return segment;
  });
  const save = el("button", "button primary", "Revisar alterações");
  save.type = "submit";
  const reset = button(
    "Descartar",
    () => {
      draft = structuredClone(current.policy);
      revision = current.revision;
      baseline = JSON.stringify(draft);
      sync();
    },
    "button ghost",
  );
  function sync() {
    for (const [key, control] of controls) {
      const rule = key.startsWith("rules.");
      const value = rule
        ? (draft.rules[key.slice(6)] ?? current.rules[key.slice(6)])
        : draft[key];
      if (control.type === "checkbox") control.checked = value;
      else control.value = value;
      control.disabled =
        !editable ||
        !!current.error ||
        (phases.includes(key) && draft.cycle !== "custom") ||
        ([
          "clear_min",
          "clear_max",
          "rain_min",
          "rain_max",
          "storm_chance",
          "storm_max",
          "storm_gap",
        ].includes(key) &&
          draft.weather !== "managed");
    }
    const sum = phases.reduce((n, key) => n + Number(draft[key]), 0);
    total.textContent =
      draft.cycle === "custom"
        ? `${sum.toLocaleString("pt-BR")} min por ciclo`
        : draft.cycle === "paused"
          ? "Horário congelado"
          : "Ritmo do Minecraft";
    segments.forEach((segment, index) => {
      segment.style.flexGrow =
        draft.cycle === "custom" ? draft[phases[index]] : [12, 1, 10, 1][index];
      segment.title = `${phaseNames[index]} · ${draft[phases[index]]} min`;
    });
    state.dirty = JSON.stringify(draft) !== baseline;
    save.disabled =
      !editable ||
      !!current.error ||
      current.revision !== revision ||
      (!state.dirty && !current.drift);
    save.textContent = current.drift
      ? "Revisar e retomar controle"
      : "Revisar alterações";
    reset.disabled = !state.dirty && revision === current.revision;
  }
  function control(key, label, options, description, bounds = {}) {
    const rule = key.startsWith("rules.");
    const value = rule
      ? (draft.rules[key.slice(6)] ?? current.rules[key.slice(6)])
      : draft[key];
    const node = options
      ? select(options, value)
      : input(value, typeof value === "boolean" ? "checkbox" : "number");
    if (node.type === "number")
      Object.assign(node, { min: 0.25, max: 240, step: 0.25, ...bounds });
    if (node.type === "checkbox") node.checked = value;
    node.addEventListener("change", () => {
      if (node.type === "number" && !node.reportValidity()) return;
      const next =
        node.type === "checkbox"
          ? node.checked
          : node.type === "number"
            ? Number(node.value)
            : node.value;
      if (rule) draft.rules[key.slice(6)] = next;
      else draft[key] = next;
      sync();
    });
    controls.set(key, node);
    const result = field(label, node, description);
    if (node.type === "checkbox") result.classList.add("environment-toggle");
    return result;
  }
  function panel(title, ...children) {
    return el("section", "environment-panel", el("h2", "", title), ...children);
  }
  const cycle = panel(
    "O ritmo do mundo",
    el(
      "div",
      "environment-cycle-top",
      control("cycle", "Ciclo", {
        native: "Minecraft",
        custom: "Personalizado",
        paused: "Pausado",
      }),
      total,
    ),
    timeline,
    el(
      "div",
      "environment-phases",
      phases.map((key, i) => control(key, `${phaseNames[i]} · min`)),
    ),
    el(
      "p",
      "muted environment-note",
      "Durações em minutos de simulação a 20 TPS. Dormir pode avançar até o amanhecer.",
    ),
  );
  const weather = panel(
    "Clima",
    control("weather", "Funcionamento", {
      native: "Minecraft",
      managed: "Controlado pelo Oak",
    }),
    el(
      "div",
      "environment-pairs",
      control("clear_min", "Céu limpo · mínimo", null, "Minutos"),
      control("clear_max", "Céu limpo · máximo", null, "Minutos"),
      control("rain_min", "Chuva · mínimo", null, "Minutos"),
      control("rain_max", "Chuva · máximo", null, "Minutos"),
    ),
    el(
      "div",
      "environment-pairs",
      control(
        "storm_chance",
        "Chance de tempestade",
        null,
        "% dos períodos de chuva",
        { min: 0, max: 100, step: 1 },
      ),
      control(
        "storm_max",
        "Tempestade · limite",
        null,
        "Minutos por ocorrência",
      ),
      control(
        "storm_gap",
        "Intervalo entre tempestades",
        null,
        "Mínimo em minutos",
      ),
    ),
  );
  const ruleDetails = el(
    "details",
    "environment-panel environment-rules",
    el("summary", "", "Regras do mundo"),
  );
  const ruleGrid = el("div", "environment-rule-grid");
  for (const [key, spec] of Object.entries(current.fields || {})) {
    ruleGrid.append(
      control(`rules.${key}`, spec.label, null, spec.description || spec.unit, {
        min: spec.min,
        max: spec.max,
        step: 1,
      }),
    );
  }
  ruleDetails.append(
    ruleGrid,
    el(
      "p",
      "muted environment-note",
      "As regras editadas são mantidas após reiniciar. Retornar o ciclo ou o clima ao Minecraft não desfaz essas regras.",
    ),
  );
  const footer = el(
    "div",
    "environment-save",
    el("span", "muted", "Alterações entram em vigor sem reiniciar."),
    el("div", "page-actions", reset, save),
  );
  editor.append(cycle, weather, ruleDetails, footer);
  editor.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (save.disabled || !editor.reportValidity()) return;
    if (draft.clear_min > draft.clear_max || draft.rain_min > draft.rain_max) {
      notice.textContent = "O mínimo deve ser menor ou igual ao máximo.";
      notice.hidden = false;
      return;
    }
    save.disabled = true;
    try {
      await operation("environment_apply", {
        action: "configure",
        revision,
        policy: structuredClone(draft),
      });
    } catch (error) {
      notice.textContent = error.message;
      notice.hidden = false;
    } finally {
      save.disabled = false;
    }
  });
  const profiles = panel("Pontos de partida");
  const presets = [
    [
      "Dias longos",
      "Mais tempo para explorar e construir.",
      { cycle: "custom", day: 20, dusk: 2, night: 6, dawn: 2 },
    ],
    [
      "Noites intensas",
      "A noite ocupa metade do ciclo.",
      { cycle: "custom", day: 10, dusk: 2, night: 14, dawn: 2 },
    ],
    [
      "Clima tranquilo",
      "Chuvas breves, sem tempestades.",
      {
        weather: "managed",
        clear_min: 45,
        clear_max: 90,
        rain_min: 2,
        rain_max: 4,
        storm_chance: 0,
      },
    ],
    [
      "Minecraft",
      "Devolver ciclo e clima ao jogo.",
      { cycle: "native", weather: "native" },
    ],
  ];
  for (const [name, description, values] of presets) {
    const b = button(
      "",
      () => {
        Object.assign(draft, values);
        sync();
      },
      "environment-preset",
    );
    b.append(el("strong", "", name), el("small", "", description));
    b.disabled = !editable;
    profiles.append(b);
  }
  const overrideWeather = select(weatherNames, "clear");
  const duration = select(
    { 5: "5 minutos", 15: "15 minutos", 30: "30 minutos", 60: "1 hora" },
    15,
  );
  const intervene = button("Revisar intervenção", () =>
    operation("environment_apply", {
      action: "override",
      revision: current.revision,
      weather: overrideWeather.value,
      minutes: Number(duration.value),
    }),
  );
  const release = button(
    "Encerrar intervenção",
    () =>
      operation("environment_apply", {
        action: "release",
        revision: current.revision,
      }),
    "button ghost",
  );
  const intervention = panel(
    "Só por um momento",
    field("Clima temporário", overrideWeather),
    field("Duração", duration),
    el(
      "p",
      "muted environment-note",
      "Prazo em minutos corridos. Ao terminar, o clima retorna à configuração ativa.",
    ),
    intervene,
    release,
  );
  const aside = el("aside", "environment-aside", profiles, intervention);
  root.append(live, notice, el("div", "environment-layout", editor, aside));
  function status() {
    const phase = ((current.clock % 24000) + 24000) % 24000;
    const name =
      phase < 12000
        ? "Dia"
        : phase < 13000
          ? "Entardecer"
          : phase < 23000
            ? "Noite"
            : "Amanhecer";
    live.replaceChildren(
      el("strong", "", current.paused ? `${name} · pausado` : name),
      el("span", "", weatherNames[current.weather]),
      el(
        "span",
        "muted",
        current.override
          ? `Intervenção · ${Math.max(0, Math.ceil((current.override.expires - Date.now() / 1000) / 60))} min restantes`
          : current.next_weather_seconds > 0 && !current.drift
            ? `Próxima mudança em ~${Math.ceil(current.next_weather_seconds / 60)} min de simulação`
            : "Clima atual do mundo",
      ),
    );
    notice.textContent =
      current.error ||
      (current.revision !== revision
        ? "O ambiente mudou. Descarte o rascunho para carregar a configuração atual."
        : current.drift
          ? "Uma alteração externa suspendeu o controle automático e encerrou intervenções. Revise a configuração para retomar."
          : "");
    notice.hidden = !notice.textContent;
    intervene.disabled =
      release.disabled =
      overrideWeather.disabled =
      duration.disabled =
        !editable || !!current.error || current.drift;
    release.hidden = !current.override;
  }
  sync();
  status();
  async function poll() {
    if (generation !== state.generation || !state.session) return;
    try {
      const next = await api("/environment");
      if (generation !== state.generation) return;
      if (!next.available)
        throw new Error(next.message || "Controlador indisponível.");
      current = next;
      if (!state.dirty) {
        if (revision !== current.revision) {
          draft = structuredClone(current.policy);
          baseline = JSON.stringify(draft);
          revision = current.revision;
          sync();
        }
      }
      save.disabled =
        !editable ||
        !!current.error ||
        current.revision !== revision ||
        (!state.dirty && !current.drift);
      status();
    } catch (error) {
      if (generation !== state.generation) return;
      notice.textContent = `Sem atualização do ambiente: ${error.message}`;
      notice.hidden = false;
      save.disabled = intervene.disabled = release.disabled = true;
    }
    if (generation === state.generation) setTimeout(poll, 3000);
  }
  setTimeout(poll, 3000);
}
