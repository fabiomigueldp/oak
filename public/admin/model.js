/* Pure presentation helpers, also exercised by the JavaScript regression suite. */
export const PAGES = {
  now: "Agora",
  world: "Mundo",
  players: "Jogadores",
  backups: "Backups",
  operations: "Operações",
  server: "Servidor",
  access: "Acesso",
};
export const ROLES = {
  owner: "Proprietário",
  administrator: "Administrador",
  moderator: "Moderador",
  observer: "Observador",
};
export const JOB_STATES = {
  queued: "Na fila",
  running: "Em andamento",
  completed: "Concluída",
  failed: "Falhou",
  interrupted: "Verificar resultado",
  cancelled: "Cancelada",
};
export const DIMENSIONS = {
  "minecraft:overworld": "Superfície",
  "minecraft:the_nether": "Nether",
  "minecraft:the_end": "End",
};

export function bytes(value) {
  if (!Number.isFinite(value)) return "Indisponível";
  const unit = value >= 1024 ** 3 ? "GiB" : value >= 1024 ** 2 ? "MiB" : "KiB";
  const divisor =
    unit === "GiB" ? 1024 ** 3 : unit === "MiB" ? 1024 ** 2 : 1024;
  return (
    new Intl.NumberFormat("pt-BR", { maximumFractionDigits: 1 }).format(
      value / divisor,
    ) +
    " " +
    unit
  );
}

export function timestamp(value) {
  if (!value || value === "n/a") return null;
  const date =
    typeof value === "number" ? new Date(value * 1000) : new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

export function time(value, full = false) {
  const date = timestamp(value);
  return date
    ? new Intl.DateTimeFormat(
        "pt-BR",
        full
          ? { dateStyle: "short", timeStyle: "short" }
          : { hour: "2-digit", minute: "2-digit" },
      ).format(date)
    : "Ainda sem registro";
}

export function ago(value, now = Date.now()) {
  const date = timestamp(value);
  if (!date) return "Sem registro";
  const seconds = Math.max(0, Math.floor((now - date.getTime()) / 1000));
  if (seconds < 10) return "Agora";
  if (seconds < 60) return `Há ${seconds} s`;
  if (seconds < 3600) return `Há ${Math.floor(seconds / 60)} min`;
  if (seconds < 86400) return `Há ${Math.floor(seconds / 3600)} h`;
  return `Há ${Math.floor(seconds / 86400)} dias`;
}

export function coords(position) {
  return Array.isArray(position) &&
    position.length === 3 &&
    position.every(Number.isFinite)
    ? position.map((v) => Math.round(v)).join(", ")
    : "Posição indisponível";
}

export function health(snapshot) {
  if (!snapshot || !snapshot.fresh)
    return {
      state: "unknown",
      label: "Aguardando dados",
      description: "O estado atual ainda não foi confirmado.",
    };
  if (!snapshot.online)
    return {
      state: "offline",
      label: "Servidor offline",
      description:
        "O mundo está preservado. O servidor não está respondendo ao jogo.",
    };
  if (snapshot.warnings?.length)
    return {
      state: "warning",
      label: "Precisa de atenção",
      description: snapshot.warnings[0],
    };
  return {
    state: "online",
    label: "Servidor online",
    description: "O mundo está disponível para jogar.",
  };
}

export function proof(point) {
  if (point.restoration?.playable_boot_tested)
    return {
      label: "Restauração testada",
      state: "good",
      icon: "shield-check",
    };
  if (point.restoration?.level === "extraction")
    return { label: "Extração verificada", state: "good", icon: "check" };
  if (point.integrity)
    return { label: "Integridade verificada", state: "neutral", icon: "check" };
  return { label: "Aguardando verificação", state: "warning", icon: "clock" };
}

export function mapHash(position, dimension = "minecraft:overworld", mapId) {
  if (
    !Array.isArray(position) ||
    position.length !== 3 ||
    !position.every(Number.isFinite)
  )
    return "";
  const id =
    mapId ||
    {
      "minecraft:overworld": "overworld",
      "minecraft:the_nether": "nether",
      "minecraft:the_end": "end",
    }[dimension];
  if (!id || !/^[A-Za-z0-9_-]+$/.test(id)) return "";
  return `#${id}:${position.map((v) => Math.round(v * 10) / 10).join(":")}:180:0:0.6:0:0:perspective`;
}

export function serializeCredential(credential) {
  const encode = (buffer) => {
    if (!buffer) return null;
    return btoa(String.fromCharCode(...new Uint8Array(buffer)))
      .replaceAll("+", "-")
      .replaceAll("/", "_")
      .replaceAll("=", "");
  };
  const response = {};
  for (const key of [
    "clientDataJSON",
    "attestationObject",
    "authenticatorData",
    "signature",
    "userHandle",
  ]) {
    if (credential.response[key] != null)
      response[key] = encode(credential.response[key]);
  }
  if (credential.response.getTransports)
    response.transports = credential.response.getTransports();
  return {
    id: credential.id,
    rawId: encode(credential.rawId),
    type: credential.type,
    response,
    clientExtensionResults: credential.getClientExtensionResults(),
  };
}

export function decodeOptions(options) {
  const decode = (input) =>
    Uint8Array.from(
      atob(
        input.replaceAll("-", "+").replaceAll("_", "/") +
          "=".repeat((4 - (input.length % 4)) % 4),
      ),
      (c) => c.charCodeAt(0),
    );
  const result = { ...options, challenge: decode(options.challenge) };
  if (options.user)
    result.user = { ...options.user, id: decode(options.user.id) };
  for (const field of ["allowCredentials", "excludeCredentials"])
    if (options[field])
      result[field] = options[field].map((item) => ({
        ...item,
        id: decode(item.id),
      }));
  return result;
}
