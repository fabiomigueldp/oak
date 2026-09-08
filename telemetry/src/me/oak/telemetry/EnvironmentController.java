package me.oak.telemetry;

import com.google.gson.*;
import java.net.*;
import java.nio.*;
import java.nio.channels.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.nio.file.attribute.PosixFilePermissions;
import java.util.*;
import java.util.concurrent.*;
import java.util.concurrent.atomic.AtomicReference;
import net.minecraft.core.Holder;
import net.minecraft.core.registries.Registries;
import net.minecraft.resources.Identifier;
import net.minecraft.server.MinecraftServer;
import net.minecraft.world.clock.WorldClock;
import net.minecraft.world.clock.WorldClocks;
import net.minecraft.world.level.gamerules.GameRule;
import net.minecraft.world.level.gamerules.GameRules;

/** Bounded world policy controller. Native mutations run exclusively on the game thread. */
public final class EnvironmentController {

  private static final Path SOCKET = Path.of(
    System.getProperty(
      "oak.environment.socket",
      "/run/oak-telemetry/control.sock"
    )
  );
  private static final Path FILE = Path.of("config/oak-environment.json");
  private static final Gson GSON = new Gson();
  private static final Map<String, GameRule<Boolean>> BOOL_RULES = Map.of(
    "keep_inventory",
    GameRules.KEEP_INVENTORY,
    "pvp",
    GameRules.PVP,
    "spawn_monsters",
    GameRules.SPAWN_MONSTERS,
    "spawn_phantoms",
    GameRules.SPAWN_PHANTOMS,
    "spawn_patrols",
    GameRules.SPAWN_PATROLS,
    "raids",
    GameRules.RAIDS,
    "mob_griefing",
    GameRules.MOB_GRIEFING
  );
  private static final Map<String, GameRule<Integer>> INT_RULES = Map.of(
    "players_sleeping_percentage",
    GameRules.PLAYERS_SLEEPING_PERCENTAGE,
    "random_tick_speed",
    GameRules.RANDOM_TICK_SPEED
  );
  private final MinecraftServer server;
  private final Holder<WorldClock> clock;
  private JsonObject state;
  private volatile JsonObject observed;
  private volatile boolean running = true;
  private ServerSocketChannel listener;
  private final Semaphore readers = new Semaphore(4),
    mutation = new Semaphore(1);
  private final AtomicReference<JsonObject> pending = new AtomicReference<>();
  private final ScheduledExecutorService io =
    Executors.newSingleThreadScheduledExecutor(r ->
      Thread.ofPlatform().daemon().name("oak-environment-store").unstarted(r)
    );
  private long persistedRevision = -1,
    lastTick = 0;
  private float expectedRate = Float.NaN;
  private boolean expectedPaused, expectedAdvance, expectedRain, expectedThunder, weatherOwned;
  private volatile String storageError = "";
  private boolean validState = true;

  public EnvironmentController(MinecraftServer server) {
    this.server = server;
    clock = server
      .registryAccess()
      .lookupOrThrow(Registries.WORLD_CLOCK)
      .getOrThrow(WorldClocks.OVERWORLD);
    var timeline = server
      .registryAccess()
      .lookupOrThrow(Registries.TIMELINE)
      .getValue(Identifier.withDefaultNamespace("day"));
    if (
      timeline == null || timeline.periodTicks().orElse(0) != 24000
    ) throw new IllegalStateException("Unsupported daylight timeline");
    state = fresh();
    // Loading happens once during startup, never inside a tick callback.
    try {
      if (Files.exists(FILE)) {
        if (Files.size(FILE) > 65536) throw new IllegalStateException(
          "Environment state exceeds limit"
        );
        state = JsonParser.parseString(
          Files.readString(FILE)
        ).getAsJsonObject();
        validatePolicy(state.getAsJsonObject("policy"));
        persistedRevision = state.get("revision").getAsLong();
      }
    } catch (Exception e) {
      state = fresh();
      state.addProperty("drift", true);
      validState = false;
      storageError =
        "Estado persistido inválido. Controle automático desativado.";
    }
    if (storageError.isEmpty()) applyRules(
      state.getAsJsonObject("policy").getAsJsonObject("rules")
    );
    publish();
    io.scheduleWithFixedDelay(
      () -> {
        var value = pending.getAndSet(null);
        if (value != null) try {
          persist(value);
        } catch (Exception e) {
          storageError = "Não foi possível persistir o ambiente.";
        }
      },
      1,
      1,
      TimeUnit.SECONDS
    );
    Thread.ofVirtual().name("oak-environment-listener").start(this::listen);
  }

  private static JsonObject fresh() {
    JsonObject value = JsonParser.parseString(
      "{\"revision\":0,\"policy\":{\"cycle\":\"native\",\"day\":10,\"dusk\":1,\"night\":8,\"dawn\":1,\"weather\":\"native\",\"clear_min\":30,\"clear_max\":60,\"rain_min\":3,\"rain_max\":8,\"storm_chance\":15,\"storm_max\":3,\"storm_gap\":60,\"rules\":{}},\"receipts\":[],\"drift\":false}"
    ).getAsJsonObject();
    return value;
  }

  private static String text(JsonObject o, String key) {
    return o.get(key).getAsString();
  }

  private static double number(JsonObject o, String key) {
    return o.get(key).getAsDouble();
  }

  private static long optionalLong(JsonObject o, String key) {
    return o.has(key) ? o.get(key).getAsLong() : 0;
  }

  private static boolean bool(JsonObject o, String key) {
    return o.has(key) && o.get(key).getAsBoolean();
  }

  public static float cycleRate(
    long time,
    double day,
    double dusk,
    double night,
    double dawn
  ) {
    long t = Math.floorMod(time, 24000);
    return (float) (
      t < 12000
        ? 12000 / (day * 1200)
        : t < 13000
          ? 1000 / (dusk * 1200)
          : t < 23000
            ? 10000 / (night * 1200)
            : 1000 / (dawn * 1200)
    );
  }

  private static void validatePolicy(JsonObject p) {
    if (
      !p.keySet().equals(fresh().getAsJsonObject("policy").keySet())
    ) throw new IllegalArgumentException("Política incompleta.");
    if (
      !Set.of("native", "custom", "paused").contains(text(p, "cycle")) ||
      !Set.of("native", "managed").contains(text(p, "weather"))
    ) throw new IllegalArgumentException("Modo inválido.");
    for (String k : List.of(
      "day",
      "dusk",
      "night",
      "dawn",
      "clear_min",
      "clear_max",
      "rain_min",
      "rain_max",
      "storm_max",
      "storm_gap"
    )) {
      if (
        !p.get(k).getAsJsonPrimitive().isNumber()
      ) throw new IllegalArgumentException("Duração inválida.");
      double n = number(p, k);
      if (
        !Double.isFinite(n) || n < .25 || n > 240
      ) throw new IllegalArgumentException("Duração fora do limite.");
    }
    double chance = number(p, "storm_chance");
    if (
      !Double.isFinite(chance) ||
      chance < 0 ||
      chance > 100 ||
      chance != Math.floor(chance)
    ) throw new IllegalArgumentException("Probabilidade inválida.");
    if (
      number(p, "clear_min") > number(p, "clear_max") ||
      number(p, "rain_min") > number(p, "rain_max")
    ) throw new IllegalArgumentException("Intervalo inválido.");
    for (var e : p.getAsJsonObject("rules").entrySet()) {
      if (BOOL_RULES.containsKey(e.getKey())) {
        if (
          !e.getValue().getAsJsonPrimitive().isBoolean()
        ) throw new IllegalArgumentException("Regra inválida.");
      } else if (INT_RULES.containsKey(e.getKey())) {
        double n = e.getValue().getAsDouble();
        if (
          !Double.isFinite(n) ||
          n != Math.floor(n) ||
          n < 0 ||
          n > (e.getKey().equals("random_tick_speed") ? 12 : 100)
        ) throw new IllegalArgumentException("Regra fora do limite.");
      } else throw new IllegalArgumentException("Regra não suportada.");
    }
  }

  private synchronized void persist(JsonObject value) throws Exception {
    if (!validState) return;
    if (value.get("revision").getAsLong() < persistedRevision) return;
    Files.createDirectories(FILE.getParent());
    Path tmp = FILE.resolveSibling("oak-environment.json.pending");
    byte[] bytes = GSON.toJson(value).getBytes(StandardCharsets.UTF_8);
    try (
      var channel = FileChannel.open(
        tmp,
        StandardOpenOption.CREATE,
        StandardOpenOption.TRUNCATE_EXISTING,
        StandardOpenOption.WRITE
      )
    ) {
      ByteBuffer buffer = ByteBuffer.wrap(bytes);
      while (buffer.hasRemaining()) channel.write(buffer);
      channel.force(true);
    }
    Files.move(
      tmp,
      FILE,
      StandardCopyOption.ATOMIC_MOVE,
      StandardCopyOption.REPLACE_EXISTING
    );
    try (
      var channel = FileChannel.open(FILE.getParent(), StandardOpenOption.READ)
    ) {
      channel.force(true);
    }
    persistedRevision = value.get("revision").getAsLong();
  }

  private JsonObject prepare(JsonObject request) {
    String action = text(request, "action"),
      id = text(request, "id");
    UUID.fromString(id);
    for (var receipt : state.getAsJsonArray("receipts"))
      if (text(receipt.getAsJsonObject(), "id").equals(id)) {
        if (
          !receipt.getAsJsonObject().get("request").equals(request)
        ) throw new IllegalArgumentException("Identificador já utilizado.");
        return null;
      }
    if (
      request.get("revision").getAsLong() != state.get("revision").getAsLong()
    ) throw new IllegalArgumentException(
      "O ambiente mudou. Atualize antes de aplicar."
    );
    if (!storageError.isEmpty()) throw new IllegalArgumentException(
      storageError
    );
    JsonObject next = state.deepCopy();
    if (action.equals("configure")) {
      validatePolicy(request.getAsJsonObject("policy"));
      next.add("policy", request.get("policy").deepCopy());
    } else if (action.equals("override")) {
      String weather = text(request, "weather");
      double minutes = number(request, "minutes");
      if (
        !Set.of("clear", "rain", "thunder").contains(weather) ||
        minutes < 1 ||
        minutes > 120 ||
        minutes != Math.floor(minutes)
      ) throw new IllegalArgumentException("Intervenção inválida.");
      JsonObject override = new JsonObject();
      override.addProperty("weather", weather);
      override.addProperty(
        "expires",
        System.currentTimeMillis() / 1000L + (long) minutes * 60
      );
      next.add("override", override);
    } else if (action.equals("release")) next.remove("override");
    else throw new IllegalArgumentException("Ação não suportada.");
    if (
      !next.has("clock_baseline") &&
      !text(next.getAsJsonObject("policy"), "cycle").equals("native")
    ) {
      var instance = server.clockManager().getInstance(clock);
      JsonObject baseline = new JsonObject();
      baseline.addProperty("rate", instance.rate());
      baseline.addProperty("paused", instance.isPaused());
      baseline.addProperty(
        "advance",
        server.getGameRules().get(GameRules.ADVANCE_TIME)
      );
      next.add("clock_baseline", baseline);
    }
    if (
      !next.has("weather_baseline") &&
      (!text(next.getAsJsonObject("policy"), "weather").equals("native") ||
        next.has("override"))
    ) {
      var w = server.getWeatherData();
      JsonObject baseline = new JsonObject();
      baseline.addProperty(
        "advance",
        server.getGameRules().get(GameRules.ADVANCE_WEATHER)
      );
      baseline.addProperty("clear", w.getClearWeatherTime());
      baseline.addProperty("rain", w.getRainTime());
      baseline.addProperty("thunder", w.getThunderTime());
      baseline.addProperty("raining", w.isRaining());
      baseline.addProperty("thundering", w.isThundering());
      next.add("weather_baseline", baseline);
    }
    next.addProperty("revision", state.get("revision").getAsLong() + 1);
    next.addProperty("drift", false);
    var receipts = next.getAsJsonArray("receipts");
    JsonObject receipt = new JsonObject();
    receipt.addProperty("id", id);
    receipt.add("request", request.deepCopy());
    receipts.add(receipt);
    while (receipts.size() > 16) receipts.remove(0);
    return next;
  }

  private void commit(JsonObject next) {
    state = next;
    expectedRate = Float.NaN;
    weatherOwned = false;
    state.remove("next_weather");
    var rules = state.getAsJsonObject("policy").getAsJsonObject("rules");
    applyRules(rules);
    apply();
    publish();
    pending.set(state.deepCopy());
  }

  private void applyRules(JsonObject rules) {
    for (var e : rules.entrySet()) {
      if (BOOL_RULES.containsKey(e.getKey())) server
        .getGameRules()
        .set(BOOL_RULES.get(e.getKey()), e.getValue().getAsBoolean(), server);
      else server
        .getGameRules()
        .set(INT_RULES.get(e.getKey()), e.getValue().getAsInt(), server);
    }
  }

  public void fail() {
    if (!validState) {
      publish();
      return;
    }
    try {
      if (state.has("clock_baseline")) {
        var b = state.getAsJsonObject("clock_baseline");
        server.clockManager().setRate(clock, (float) number(b, "rate"));
        server.clockManager().setPaused(clock, bool(b, "paused"));
        server
          .getGameRules()
          .set(GameRules.ADVANCE_TIME, bool(b, "advance"), server);
      }
      if (state.has("weather_baseline")) server
        .getGameRules()
        .set(
          GameRules.ADVANCE_WEATHER,
          bool(state.getAsJsonObject("weather_baseline"), "advance"),
          server
        );
    } catch (Exception ignored) {}
    storageError =
      "O controlador foi suspenso após uma falha. Verifique o servidor.";
    state.addProperty("drift", true);
    pending.set(state.deepCopy());
    publish();
  }

  public void tick() {
    if (!running) return;
    long now = server.overworld().getGameTime();
    if (now == lastTick) return;
    lastTick = now;
    if (!storageError.isEmpty()) {
      if (!bool(state, "drift")) fail();
      if (now % 20 == 0) publish();
      return;
    }
    if (!bool(state, "drift")) {
      var actual = server.clockManager().getInstance(clock);
      if (
        !Float.isNaN(expectedRate) &&
        (Math.abs(actual.rate() - expectedRate) > .0001 ||
          actual.isPaused() != expectedPaused ||
          server.getGameRules().get(GameRules.ADVANCE_TIME) != expectedAdvance)
      ) drift();
      if (
        weatherOwned &&
        (server.getGameRules().get(GameRules.ADVANCE_WEATHER) ||
          server.getWeatherData().isRaining() != expectedRain ||
          server.getWeatherData().isThundering() != expectedThunder)
      ) {
        // Native sleep clears weather; accept a forward jump to the wake-up phase.
        long phase = Math.floorMod(actual.totalTicks(), 24000);
        if (phase < 100 && !server.getWeatherData().isRaining()) {
          state.remove("next_weather");
          weatherOwned = false;
        } else drift();
      }
      if (!bool(state, "drift")) apply();
    }
    if (now % 20 == 0) publish();
    if (now % 1200 == 0) pending.set(state.deepCopy());
  }

  private void drift() {
    // Release only values still owned by Oak; preserve the external change.
    var actual = server.clockManager().getInstance(clock);
    if (state.has("clock_baseline")) {
      var b = state.getAsJsonObject("clock_baseline");
      if (actual.rate() == expectedRate) server
        .clockManager()
        .setRate(clock, (float) number(b, "rate"));
      if (actual.isPaused() == expectedPaused) server
        .clockManager()
        .setPaused(clock, bool(b, "paused"));
      if (
        server.getGameRules().get(GameRules.ADVANCE_TIME) == expectedAdvance
      ) server
        .getGameRules()
        .set(GameRules.ADVANCE_TIME, bool(b, "advance"), server);
      state.remove("clock_baseline");
    }
    if (state.has("weather_baseline")) {
      if (!server.getGameRules().get(GameRules.ADVANCE_WEATHER)) server
        .getGameRules()
        .set(
          GameRules.ADVANCE_WEATHER,
          bool(state.getAsJsonObject("weather_baseline"), "advance"),
          server
        );
      state.remove("weather_baseline");
    }
    state.remove("override");
    state.remove("next_weather");
    weatherOwned = false;
    expectedRate = Float.NaN;
    state.addProperty("revision", state.get("revision").getAsLong() + 1);
    state.addProperty("drift", true);
    pending.set(state.deepCopy());
    publish();
  }

  private void apply() {
    JsonObject p = state.getAsJsonObject("policy");
    String cycle = text(p, "cycle");
    var manager = server.clockManager();
    var instance = manager.getInstance(clock);
    if (!cycle.equals("native")) {
      float rate = cycleRate(
        instance.totalTicks(),
        number(p, "day"),
        number(p, "dusk"),
        number(p, "night"),
        number(p, "dawn")
      );
      expectedPaused = cycle.equals("paused");
      expectedRate = rate;
      expectedAdvance = true;
      if (instance.rate() != rate) manager.setRate(clock, rate);
      if (instance.isPaused() != expectedPaused) manager.setPaused(
        clock,
        expectedPaused
      );
      if (!server.getGameRules().get(GameRules.ADVANCE_TIME)) server
        .getGameRules()
        .set(GameRules.ADVANCE_TIME, true, server);
    } else if (state.has("clock_baseline")) {
      var baseline = state.getAsJsonObject("clock_baseline");
      manager.setRate(clock, (float) number(baseline, "rate"));
      manager.setPaused(clock, bool(baseline, "paused"));
      server
        .getGameRules()
        .set(GameRules.ADVANCE_TIME, bool(baseline, "advance"), server);
      state.remove("clock_baseline");
      expectedRate = Float.NaN;
      pending.set(state.deepCopy());
    }
    if (
      state.has("override") &&
      optionalLong(state.getAsJsonObject("override"), "expires") <=
        System.currentTimeMillis() / 1000L
    ) {
      state.remove("override");
      state.remove("next_weather");
      weatherOwned = false;
      pending.set(state.deepCopy());
    }
    if (state.has("override")) {
      String weather = text(state.getAsJsonObject("override"), "weather");
      if (!weatherOwned) weather(weather, 1200);
      return;
    }
    if (text(p, "weather").equals("native")) {
      if (state.has("weather_baseline")) {
        var b = state.getAsJsonObject("weather_baseline");
        var w = server.getWeatherData();
        w.setClearWeatherTime(b.get("clear").getAsInt());
        w.setRainTime(b.get("rain").getAsInt());
        w.setThunderTime(b.get("thunder").getAsInt());
        w.setRaining(bool(b, "raining"));
        w.setThundering(bool(b, "thundering"));
        server
          .getGameRules()
          .set(GameRules.ADVANCE_WEATHER, bool(b, "advance"), server);
        state.remove("weather_baseline");
        pending.set(state.deepCopy());
      }
      weatherOwned = false;
      return;
    }
    long now = server.overworld().getGameTime();
    if (
      !state.has("next_weather") || now >= optionalLong(state, "next_weather")
    ) {
      String previous = state.has("weather_state")
        ? text(state, "weather_state")
        : "rain";
      String next = previous.equals("clear") ? "rain" : "clear";
      double minutes;
      if (next.equals("rain")) {
        minutes = random(number(p, "rain_min"), number(p, "rain_max"));
        if (
          now - optionalLong(state, "last_storm") >=
            number(p, "storm_gap") * 1200 &&
          ThreadLocalRandom.current().nextDouble(100) <
            number(p, "storm_chance")
        ) {
          next = "thunder";
          minutes = Math.min(minutes, number(p, "storm_max"));
          state.addProperty("last_storm", now);
        }
      } else minutes = random(number(p, "clear_min"), number(p, "clear_max"));
      int ticks = (int) Math.ceil(minutes * 1200);
      weather(next, ticks);
      state.addProperty("weather_state", next);
      state.addProperty("next_weather", now + ticks);
      pending.set(state.deepCopy());
    } else if (!weatherOwned) weather(
      text(state, "weather_state"),
      (int) Math.max(1, optionalLong(state, "next_weather") - now)
    );
  }

  private static double random(double min, double max) {
    return min == max ? min : ThreadLocalRandom.current().nextDouble(min, max);
  }

  private void weather(String weather, int duration) {
    server.getGameRules().set(GameRules.ADVANCE_WEATHER, false, server);
    expectedRain = !weather.equals("clear");
    expectedThunder = weather.equals("thunder");
    server.setWeatherParameters(
      expectedRain ? 0 : duration,
      duration,
      expectedRain,
      expectedThunder
    );
    weatherOwned = true;
  }

  private void publish() {
    JsonObject value = new JsonObject();
    value.addProperty("available", true);
    value.add("policy", state.get("policy").deepCopy());
    value.add("revision", state.get("revision"));
    value.addProperty("drift", bool(state, "drift"));
    value.addProperty("error", storageError);
    value.addProperty("sampled_at", System.currentTimeMillis() / 1000.0);
    var instance = server.clockManager().getInstance(clock);
    value.addProperty("clock", instance.totalTicks());
    value.addProperty("rate", instance.rate());
    value.addProperty(
      "paused",
      instance.isPaused() || !server.getGameRules().get(GameRules.ADVANCE_TIME)
    );
    value.addProperty(
      "weather",
      server.getWeatherData().isThundering()
        ? "thunder"
        : server.getWeatherData().isRaining()
          ? "rain"
          : "clear"
    );
    value.addProperty(
      "next_weather_seconds",
      Math.max(
        0,
        (optionalLong(state, "next_weather") -
          server.overworld().getGameTime()) /
          20
      )
    );
    if (state.has("override")) value.add(
      "override",
      state.get("override").deepCopy()
    );
    JsonObject rules = new JsonObject();
    BOOL_RULES.forEach((k, v) ->
      rules.addProperty(k, server.getGameRules().get(v))
    );
    INT_RULES.forEach((k, v) ->
      rules.addProperty(k, server.getGameRules().get(v))
    );
    value.add("rules", rules);
    observed = value;
  }

  private void listen() {
    try {
      Files.deleteIfExists(SOCKET);
      listener = ServerSocketChannel.open(StandardProtocolFamily.UNIX);
      listener.bind(UnixDomainSocketAddress.of(SOCKET));
      Files.setPosixFilePermissions(
        SOCKET,
        PosixFilePermissions.fromString("rw-------")
      );
      while (running) {
        var client = listener.accept();
        if (!readers.tryAcquire()) {
          client.close();
          continue;
        }
        Thread.ofVirtual().start(() -> handle(client));
      }
    } catch (Exception e) {
      if (running) storageError = "Canal de controle indisponível.";
    }
  }

  private void handle(SocketChannel client) {
    boolean locked = false;
    try {
      client.configureBlocking(false);
      ByteBuffer buffer = ByteBuffer.allocate(16384);
      long deadline = System.nanoTime() + 5_000_000_000L;
      while (true) {
        int n = client.read(buffer);
        if (n < 0) throw new IllegalArgumentException("Request interrupted");
        if (
          buffer.position() > 0 && buffer.get(buffer.position() - 1) == '\n'
        ) break;
        if (
          !buffer.hasRemaining() || System.nanoTime() > deadline
        ) throw new IllegalArgumentException("Request limit");
        Thread.sleep(5);
      }
      buffer.flip();
      JsonObject request = JsonParser.parseString(
        StandardCharsets.UTF_8.decode(buffer).toString()
      ).getAsJsonObject();
      JsonObject result;
      if (text(request, "action").equals("status")) result = observed;
      else {
        if (!mutation.tryAcquire()) throw new IllegalArgumentException(
          "Outra alteração está em andamento."
        );
        locked = true;
        JsonObject next = server
          .submit(() -> prepare(request))
          .get(5, TimeUnit.SECONDS);
        if (next != null) {
          persist(next);
          server
            .submit(() -> {
              commit(next);
              return true;
            })
            .get(5, TimeUnit.SECONDS);
        }
        result = observed;
      }
      write(client, result);
    } catch (Exception e) {
      // A committed receipt remains durable even when its acknowledgement is lost.
      try {
        JsonObject error = new JsonObject();
        Throwable cause = e.getCause() == null ? e : e.getCause();
        error.addProperty(
          "error",
          cause instanceof IllegalArgumentException
            ? cause.getMessage()
            : "Não foi possível concluir. Atualize para verificar o estado."
        );
        if (client.isOpen()) write(client, error);
      } catch (Exception ignored) {}
    } finally {
      try {
        client.close();
      } catch (Exception ignored) {}
      if (locked) mutation.release();
      readers.release();
    }
  }

  private static void write(SocketChannel client, JsonObject value)
    throws Exception {
    ByteBuffer bytes = ByteBuffer.wrap(
      (GSON.toJson(value) + "\n").getBytes(StandardCharsets.UTF_8)
    );
    long deadline = System.nanoTime() + 2_000_000_000L;
    while (bytes.hasRemaining()) {
      client.write(bytes);
      if (System.nanoTime() > deadline) break;
      if (bytes.hasRemaining()) Thread.sleep(5);
    }
  }

  public void close() {
    running = false;
    try {
      if (listener != null) listener.close();
    } catch (Exception ignored) {}
    pending.set(state.deepCopy());
    io.shutdown();
    try {
      persist(state.deepCopy());
      Files.deleteIfExists(SOCKET);
    } catch (Exception ignored) {}
  }
}
