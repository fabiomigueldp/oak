package me.oak.telemetry;

import com.google.gson.*;
import java.io.ByteArrayOutputStream;
import java.net.*;
import java.nio.*;
import java.nio.channels.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.nio.file.attribute.PosixFilePermissions;
import java.security.MessageDigest;
import java.util.*;
import java.util.concurrent.*;
import java.util.concurrent.atomic.AtomicBoolean;
import net.minecraft.commands.CommandSource;
import net.minecraft.commands.arguments.blocks.BlockStateParser;
import net.minecraft.core.BlockPos;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.core.registries.Registries;
import net.minecraft.network.chat.Component;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.server.permissions.PermissionSet;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.entity.LivingEntity;
import net.minecraft.world.level.entity.EntityTypeTest;
import net.minecraft.world.phys.AABB;

/** Native operator API. Socket I/O stays off-thread; world access runs at end tick. */
public final class OperatorBridge implements AutoCloseable {
    private static final Gson GSON = new Gson();
    private static final JsonObject SCHEMAS = JsonParser.parseString("""
        {
          "discover":{"type":"object","properties":{}},
          "players":{"type":"object","properties":{"uuid":{"type":"string"},"inventory":{"type":"boolean","default":false}}},
          "region.inspect":{"type":"object","required":["min","max"],"properties":{"min":{"type":"array","items":{"type":"integer"},"minItems":3,"maxItems":3},"max":{"type":"array","items":{"type":"integer"},"minItems":3,"maxItems":3},"cursor":{"type":"integer","minimum":0,"default":0},"limit":{"type":"integer","minimum":1,"default":256}}},
          "blocks.apply":{"type":"object","required":["blocks"],"properties":{"blocks":{"type":"array","items":{"type":"object","required":["position","state"],"properties":{"position":{"type":"array","items":{"type":"integer"},"minItems":3,"maxItems":3},"state":{"type":"string"},"expected":{"type":"string"}}}},"cursor":{"type":"integer","minimum":0,"default":0},"flags":{"type":"integer","minimum":0,"maximum":1023,"default":3}}},
          "entities":{"type":"object","required":["min","max"],"properties":{"min":{"type":"array","items":{"type":"integer"},"minItems":3,"maxItems":3},"max":{"type":"array","items":{"type":"integer"},"minItems":3,"maxItems":3},"type":{"type":"string"},"limit":{"type":"integer","minimum":1,"default":256}}},
          "command":{"type":"object","required":["command"],"properties":{"command":{"type":"string"}}},
          "events":{"type":"object","properties":{"after":{"type":"integer","minimum":0,"default":0},"limit":{"type":"integer","minimum":1,"maximum":1024,"default":100}}},
          "receipt":{"type":"object","required":["idempotency"],"properties":{"idempotency":{"type":"string"}}}
        }
        """).getAsJsonObject();
    private static final Path SOCKET = Path.of(System.getProperty("oak.operator.socket", "/run/oak-telemetry/operator.sock"));
    private static final int MAX_BYTES = 2 * 1024 * 1024;
    private static final int MAX_BATCH = Integer.getInteger("oak.operator.maxBatch", 4096);
    private static final long BUDGET_NANOS = Long.getLong("oak.operator.budgetMicros", 2000L) * 1000;
    private final MinecraftServer server;
    private final ArrayBlockingQueue<Work> queue = new ArrayBlockingQueue<>(32);
    private final Semaphore connections = new Semaphore(32);
    private final Set<SocketChannel> clients = ConcurrentHashMap.newKeySet();
    private final ArrayDeque<JsonObject> events = new ArrayDeque<>();
    private final Map<String, JsonObject> previousPlayers = new HashMap<>();
    private final LinkedHashMap<String, Receipt> receipts = new LinkedHashMap<>();
    private final String epoch = UUID.randomUUID().toString();
    private long eventId, tick;
    private volatile boolean running = true;
    private volatile long lastTickNanos = System.nanoTime();
    private final AtomicBoolean wakePending = new AtomicBoolean();
    private volatile ServerSocketChannel listener;
    private volatile boolean ownsSocket;
    private record Work(JsonObject request, String signature, CompletableFuture<JsonObject> result) {}
    private record Receipt(String request, JsonObject result) {}

    public OperatorBridge(MinecraftServer server) {
        if (MAX_BATCH < 1 || BUDGET_NANOS < 1000) throw new IllegalArgumentException("Invalid operator batch budget");
        this.server = server;
        Thread.ofVirtual().name("oak-operator-listener").start(this::listen);
        Thread.ofVirtual().name("oak-operator-idle-wake").start(this::wakeIdleServer);
    }

    private void wakeIdleServer() {
        while (running) {
            try {
                Thread.sleep(50);
                // Vanilla's empty-server pause bypasses Fabric's end-tick callback.
                // The main-thread executor still runs; only wake it for queued work.
                if (!queue.isEmpty() && System.nanoTime() - lastTickNanos > 100_000_000L && wakePending.compareAndSet(false, true)) {
                    server.execute(() -> {
                        try { if (running) pump(); }
                        finally { wakePending.set(false); }
                    });
                }
            } catch (Exception e) { wakePending.set(false); }
        }
    }

    private void listen() {
        try {
            if (Files.exists(SOCKET, LinkOption.NOFOLLOW_LINKS)) {
                int mode = (int) Files.getAttribute(SOCKET, "unix:mode", LinkOption.NOFOLLOW_LINKS);
                if ((mode & 0170000) != 0140000) throw new IllegalStateException("Operator path is not a socket");
                try (SocketChannel probe = SocketChannel.open(StandardProtocolFamily.UNIX)) {
                    probe.connect(UnixDomainSocketAddress.of(SOCKET));
                    throw new IllegalStateException("Operator socket is already active");
                } catch (java.net.ConnectException stale) {
                    Files.delete(SOCKET);
                }
            }
            listener = ServerSocketChannel.open(StandardProtocolFamily.UNIX);
            listener.bind(UnixDomainSocketAddress.of(SOCKET));
            ownsSocket = true;
            Files.setPosixFilePermissions(SOCKET, PosixFilePermissions.fromString("rw-------"));
            while (running) {
                SocketChannel client = listener.accept();
                if (!connections.tryAcquire()) { client.close(); continue; }
                clients.add(client);
                Thread.ofVirtual().name("oak-operator-client").start(() -> exchange(client));
            }
        } catch (Exception e) {
            if (running) System.err.println("Oak operator socket unavailable: " + e.getClass().getSimpleName());
        } finally { running = false; rejectPending(); cleanup(); }
    }

    private void exchange(SocketChannel client) {
        try (client) {
            client.configureBlocking(false);
            ByteBuffer buffer = ByteBuffer.allocate(8192);
            ByteArrayOutputStream bytes = new ByteArrayOutputStream();
            long deadline = System.nanoTime() + TimeUnit.SECONDS.toNanos(10);
            boolean complete = false;
            while (running && !complete) {
                int count = client.read(buffer);
                if (count < 0) return;
                buffer.flip();
                while (buffer.hasRemaining()) {
                    byte value = buffer.get();
                    if (value == '\n') { complete = true; break; }
                    bytes.write(value);
                    if (bytes.size() > MAX_BYTES) throw new IllegalArgumentException("Request exceeds 2 MiB");
                }
                buffer.clear();
                if (System.nanoTime() > deadline) throw new TimeoutException("Request read expired");
                if (!complete) Thread.sleep(2);
            }
            JsonObject response;
            try {
                JsonObject request = JsonParser.parseString(bytes.toString(StandardCharsets.UTF_8)).getAsJsonObject();
                CompletableFuture<JsonObject> result = new CompletableFuture<>();
                String signature = HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(GSON.toJson(request).getBytes(StandardCharsets.UTF_8)));
                Work work = new Work(request, signature, result);
                if (!running || !queue.offer(work)) throw new IllegalStateException("Operator queue is full or stopping");
                try { response = result.get(30, TimeUnit.SECONDS); }
                catch (TimeoutException expired) {
                    if (queue.remove(work)) result.cancel(false);
                    throw new IllegalStateException("Result uncertain; inspect receipt before retrying a mutation");
                }
            } catch (Exception e) { response = error(e); }
            ByteBuffer output = StandardCharsets.UTF_8.encode(GSON.toJson(response) + "\n");
            deadline = System.nanoTime() + TimeUnit.SECONDS.toNanos(5);
            while (output.hasRemaining() && running) {
                client.write(output);
                if (System.nanoTime() > deadline) break;
                if (output.hasRemaining()) Thread.sleep(2);
            }
        } catch (Exception ignored) {
            // A slow or disconnected client never blocks the game thread.
        } finally { clients.remove(client); connections.release(); }
    }

    public void tick() {
        if (!running) return;
        lastTickNanos = System.nanoTime();
        tick = server.getTickCount();
        observePlayers();
        pump();
    }

    private void pump() {
        Work work = queue.poll();
        if (work == null || work.result().isCancelled()) return;
        try { work.result().complete(dispatch(work.request(), work.signature())); }
        catch (Exception e) { work.result().complete(error(e)); }
    }

    private JsonObject dispatch(JsonObject request, String signature) throws Exception {
        String method = text(request, "method");
        JsonObject data = request.has("data") ? request.getAsJsonObject("data") : new JsonObject();
        String key = request.has("idempotency") ? text(request, "idempotency") : "";
        if (key.length() > 256) throw new IllegalArgumentException("Idempotency key exceeds 256 characters");
        if (data.has("expected_epoch") && !text(data, "expected_epoch").equals(epoch)) throw new IllegalStateException("Minecraft epoch changed; inspect prior effects before continuing");
        if (!key.isEmpty() && receipts.containsKey(key)) {
            Receipt receipt = receipts.get(key);
            if (!receipt.request().equals(signature)) throw new IllegalArgumentException("Idempotency key has different input");
            return receipt.result().deepCopy();
        }
        JsonObject result = switch (method) {
            case "discover" -> discover();
            case "players" -> players(data);
            case "region.inspect" -> inspect(data);
            case "blocks.apply" -> apply(data);
            case "entities" -> entities(data);
            case "command" -> command(data);
            case "events" -> events(data);
            case "receipt" -> receipt(data);
            default -> throw new IllegalArgumentException("Unknown native method: " + method);
        };
        result.addProperty("epoch", epoch);
        result.addProperty("tick", tick);
        JsonObject response = new JsonObject();
        response.addProperty("ok", true);
        response.add("result", result);
        if (!key.isEmpty()) {
            receipts.put(key, new Receipt(signature, response.deepCopy()));
            while (receipts.size() > 128) receipts.remove(receipts.keySet().iterator().next());
        }
        return response;
    }

    private JsonObject discover() {
        JsonObject value = new JsonObject();
        value.addProperty("version", 1);
        value.addProperty("max_batch", MAX_BATCH);
        value.addProperty("budget_micros", BUDGET_NANOS / 1000);
        value.addProperty("loaded_chunks_only", true);
        value.addProperty("receipt_capacity", 128);
        value.add("methods", GSON.toJsonTree(List.of("discover", "players", "region.inspect", "blocks.apply", "entities", "command", "events", "receipt")));
        JsonObject schemas = SCHEMAS.deepCopy();
        for (var entry : schemas.entrySet()) {
            JsonObject properties = entry.getValue().getAsJsonObject().getAsJsonObject("properties");
            properties.add("expected_epoch", GSON.toJsonTree(Map.of("type", "string")));
            if (Set.of("region.inspect", "blocks.apply", "entities").contains(entry.getKey())) {
                properties.add("dimension", GSON.toJsonTree(Map.of("type", "string", "default", "minecraft:overworld")));
                if (properties.has("limit")) properties.getAsJsonObject("limit").addProperty("maximum", MAX_BATCH);
                if (properties.has("blocks")) properties.getAsJsonObject("blocks").addProperty("maxItems", MAX_BATCH);
            }
        }
        value.add("schemas", schemas);
        JsonArray dimensions = new JsonArray();
        for (ServerLevel level : server.getAllLevels()) {
            JsonObject dimension = new JsonObject();
            dimension.addProperty("id", level.dimension().identifier().toString());
            dimension.addProperty("min_y", level.getMinY());
            dimension.addProperty("max_y", level.getMaxY());
            dimensions.add(dimension);
        }
        value.add("dimensions", dimensions);
        return value;
    }

    private JsonObject player(ServerPlayer player, boolean inventory) {
        JsonObject value = entity(player);
        value.addProperty("dimension", player.level().dimension().identifier().toString());
        value.addProperty("health", player.getHealth());
        value.addProperty("food", player.getFoodData().getFoodLevel());
        if (inventory) {
            JsonArray items = new JsonArray();
            var container = player.getInventory();
            for (int slot = 0; slot < container.getContainerSize(); slot++) {
                var stack = container.getItem(slot);
                if (stack.isEmpty()) continue;
                JsonObject item = new JsonObject();
                item.addProperty("slot", slot);
                item.addProperty("id", BuiltInRegistries.ITEM.getKey(stack.getItem()).toString());
                item.addProperty("count", stack.getCount());
                item.addProperty("name", stack.getHoverName().getString());
                items.add(item);
            }
            value.add("inventory", items);
        }
        return value;
    }

    private JsonObject players(JsonObject data) {
        JsonArray players = new JsonArray();
        String uuid = data.has("uuid") ? text(data, "uuid") : "";
        for (ServerPlayer player : server.getPlayerList().getPlayers()) {
            if (uuid.isEmpty() || uuid.equals(player.getUUID().toString())) players.add(player(player, bool(data, "inventory")));
        }
        JsonObject result = new JsonObject(); result.add("players", players); return result;
    }

    private void observePlayers() {
        Set<String> online = new HashSet<>();
        for (ServerPlayer player : server.getPlayerList().getPlayers()) {
            String id = player.getUUID().toString();
            online.add(id);
            JsonObject now = player(player, false), before = previousPlayers.put(id, now);
            if (before == null) event("player.join", now);
            else {
                if (!before.get("dimension").equals(now.get("dimension"))) event("player.dimension", now);
                if (before.get("alive").getAsBoolean() && !now.get("alive").getAsBoolean()) event("player.death", now);
            }
        }
        for (String id : new ArrayList<>(previousPlayers.keySet())) {
            if (!online.contains(id)) event("player.leave", previousPlayers.remove(id));
        }
    }

    private void event(String name, JsonObject data) {
        JsonObject event = new JsonObject();
        event.addProperty("id", ++eventId); event.addProperty("name", name);
        event.addProperty("at", System.currentTimeMillis() / 1000.0);
        event.add("data", data.deepCopy()); events.add(event);
        while (events.size() > 4096) events.removeFirst();
    }

    private JsonObject events(JsonObject data) {
        long after = integer(data, "after", 0, Long.MAX_VALUE, 0);
        int limit = (int) integer(data, "limit", 1, 1024, 100);
        JsonArray values = new JsonArray(); long next = after;
        for (JsonObject event : events) {
            if (event.get("id").getAsLong() > after) { values.add(event.deepCopy()); next = event.get("id").getAsLong(); }
            if (values.size() >= limit) break;
        }
        JsonObject result = new JsonObject(); result.add("events", values);
        result.addProperty("next", next); result.addProperty("latest", eventId);
        result.addProperty("oldest", events.isEmpty() ? 0 : events.getFirst().get("id").getAsLong());
        return result;
    }

    private JsonObject receipt(JsonObject data) {
        Receipt receipt = receipts.get(text(data, "idempotency"));
        JsonObject value = new JsonObject(); value.addProperty("found", receipt != null);
        if (receipt != null) value.add("response", receipt.result().deepCopy());
        return value;
    }

    private ServerLevel level(JsonObject data) {
        String id = data.has("dimension") ? text(data, "dimension") : "minecraft:overworld";
        for (ServerLevel level : server.getAllLevels()) if (level.dimension().identifier().toString().equals(id)) return level;
        throw new IllegalArgumentException("Unknown dimension");
    }

    private JsonObject inspect(JsonObject data) {
        ServerLevel level = level(data);
        int[] min = vector(data.getAsJsonArray("min")), max = vector(data.getAsJsonArray("max"));
        long sx = (long)max[0] - min[0] + 1, sy = (long)max[1] - min[1] + 1, sz = (long)max[2] - min[2] + 1;
        if (sx < 1 || sy < 1 || sz < 1 || min[1] < level.getMinY() || max[1] > level.getMaxY()) throw new IllegalArgumentException("Invalid inclusive region bounds");
        long volume = Math.multiplyExact(Math.multiplyExact(sx, sy), sz);
        long cursor = integer(data, "cursor", 0, volume, 0);
        int limit = (int) integer(data, "limit", 1, MAX_BATCH, Math.min(256, MAX_BATCH));
        long deadline = System.nanoTime() + BUDGET_NANOS;
        JsonArray blocks = new JsonArray();
        while (cursor < volume && blocks.size() < limit && (blocks.isEmpty() || System.nanoTime() < deadline)) {
            BlockPos pos = new BlockPos((int)(min[0] + cursor % sx), (int)(min[1] + (cursor / sx) % sy), (int)(min[2] + cursor / (sx * sy)));
            JsonObject block = new JsonObject(); block.add("position", position(pos));
            boolean loaded = level.getChunkSource().hasChunk(pos.getX() >> 4, pos.getZ() >> 4);
            block.addProperty("loaded", loaded);
            if (loaded) block.addProperty("state", BlockStateParser.serialize(level.getBlockState(pos)));
            blocks.add(block); cursor++;
        }
        JsonObject result = new JsonObject(); result.add("blocks", blocks);
        result.addProperty("next_cursor", cursor); result.addProperty("volume", volume);
        result.addProperty("has_more", cursor < volume); return result;
    }

    private JsonObject apply(JsonObject data) {
        ServerLevel level = level(data);
        JsonArray blocks = data.getAsJsonArray("blocks");
        if (blocks == null || blocks.size() > MAX_BATCH) throw new IllegalArgumentException("Batch exceeds max_batch");
        int cursor = (int) integer(data, "cursor", 0, blocks.size(), 0);
        int flags = (int) integer(data, "flags", 0, 1023, 3);
        long deadline = System.nanoTime() + BUDGET_NANOS;
        JsonArray results = new JsonArray();
        while (cursor < blocks.size() && (results.isEmpty() || System.nanoTime() < deadline)) {
            JsonObject item = new JsonObject(); item.addProperty("index", cursor);
            try {
                JsonObject block = blocks.get(cursor).getAsJsonObject();
                int[] xyz = vector(block.getAsJsonArray("position"));
                BlockPos pos = new BlockPos(xyz[0], xyz[1], xyz[2]);
                if (xyz[1] < level.getMinY() || xyz[1] > level.getMaxY()) throw new IllegalArgumentException("Position outside build height");
                if (!level.getChunkSource().hasChunk(xyz[0] >> 4, xyz[2] >> 4)) throw new IllegalArgumentException("Chunk is not loaded");
                var state = BlockStateParser.parseForBlock(server.registryAccess().lookupOrThrow(Registries.BLOCK), text(block, "state"), false).blockState();
                String before = BlockStateParser.serialize(level.getBlockState(pos));
                if (block.has("expected") && !text(block, "expected").equals(before)) throw new IllegalArgumentException("Block state conflict");
                item.addProperty("before", before);
                item.addProperty("changed", level.setBlock(pos, state, flags, 512));
                item.addProperty("after", BlockStateParser.serialize(level.getBlockState(pos)));
            } catch (Exception e) { item.addProperty("error", String.valueOf(e.getMessage())); }
            results.add(item); cursor++;
        }
        JsonObject result = new JsonObject(); result.add("results", results);
        result.addProperty("next_cursor", cursor); result.addProperty("has_more", cursor < blocks.size());
        JsonObject summary = new JsonObject(); summary.addProperty("count", results.size()); summary.addProperty("next_cursor", cursor);
        summary.addProperty("dimension", level.dimension().identifier().toString());
        event("world.blocks", summary); return result;
    }

    private static JsonObject entity(Entity entity) {
        JsonObject value = new JsonObject();
        value.addProperty("uuid", entity.getUUID().toString());
        value.addProperty("name", entity.getName().getString());
        value.addProperty("type", BuiltInRegistries.ENTITY_TYPE.getKey(entity.getType()).toString());
        value.add("position", GSON.toJsonTree(List.of(entity.getX(), entity.getY(), entity.getZ())));
        value.addProperty("alive", entity.isAlive());
        if (entity instanceof LivingEntity living) value.addProperty("health", living.getHealth());
        return value;
    }

    private JsonObject entities(JsonObject data) {
        ServerLevel level = level(data);
        int[] min = vector(data.getAsJsonArray("min")), max = vector(data.getAsJsonArray("max"));
        for (int axis = 0; axis < 3; axis++) if (max[axis] < min[axis] || (long)max[axis] - min[axis] > 256) throw new IllegalArgumentException("Entity query sides must be at most 256 blocks; tile larger regions");
        int limit = (int) integer(data, "limit", 1, MAX_BATCH, Math.min(256, MAX_BATCH));
        List<Entity> values = new ArrayList<>();
        String type = data.has("type") ? text(data, "type") : "";
        level.getEntities(EntityTypeTest.forClass(Entity.class), new AABB(min[0], min[1], min[2], max[0] + 1.0, max[1] + 1.0, max[2] + 1.0),
            entity -> type.isEmpty() || BuiltInRegistries.ENTITY_TYPE.getKey(entity.getType()).toString().equals(type), values, limit);
        JsonArray entities = new JsonArray(); for (Entity entity : values) entities.add(entity(entity));
        JsonObject result = new JsonObject(); result.add("entities", entities);
        result.addProperty("limit_reached", values.size() >= limit); return result;
    }

    private JsonObject command(JsonObject data) {
        String command = text(data, "command");
        if (command.startsWith("/")) command = command.substring(1);
        JsonArray messages = new JsonArray(), callbacks = new JsonArray();
        int[] messageSize = {0};
        CommandSource output = new CommandSource() {
            public void sendSystemMessage(Component component) {
                String text = component.getString();
                if (messageSize[0] < 65536) { messages.add(text.substring(0, Math.min(text.length(), 65536 - messageSize[0]))); messageSize[0] += text.length(); }
            }
            public boolean acceptsSuccess() { return true; }
            public boolean acceptsFailure() { return true; }
            public boolean shouldInformAdmins() { return false; }
        };
        var source = server.createCommandSourceStack().withSource(output).withPermission(PermissionSet.ALL_PERMISSIONS)
            .withCallback((success, result) -> { if (callbacks.size() < 1024) { JsonObject value = new JsonObject(); value.addProperty("success", success); value.addProperty("result", result); callbacks.add(value); } });
        server.getCommands().performPrefixedCommand(source, command);
        JsonObject result = new JsonObject(); result.add("messages", messages.deepCopy()); result.add("results", callbacks.deepCopy());
        result.addProperty("output_truncated", messageSize[0] >= 65536);
        JsonObject summary = new JsonObject(); summary.addProperty("message_count", messages.size()); summary.addProperty("result_count", callbacks.size());
        event("world.command", summary); return result;
    }

    private static int[] vector(JsonArray values) {
        if (values == null || values.size() != 3) throw new IllegalArgumentException("Position needs three integers");
        int[] result = new int[3];
        for (int axis = 0; axis < 3; axis++) {
            JsonObject item = new JsonObject(); item.add("value", values.get(axis));
            result[axis] = (int) integer(item, "value", -30000000, 30000000, 0);
        }
        return result;
    }
    private static JsonArray position(BlockPos pos) { return GSON.toJsonTree(List.of(pos.getX(), pos.getY(), pos.getZ())).getAsJsonArray(); }
    private static String text(JsonObject data, String key) {
        if (!data.has(key) || !data.get(key).isJsonPrimitive() || !data.getAsJsonPrimitive(key).isString()) throw new IllegalArgumentException(key + " must be text");
        String value = data.get(key).getAsString();
        if (value.indexOf('\0') >= 0) throw new IllegalArgumentException("NUL is invalid");
        return value;
    }
    private static long integer(JsonObject data, String key, long min, long max, long fallback) {
        if (!data.has(key)) return fallback;
        if (!data.get(key).isJsonPrimitive() || !data.getAsJsonPrimitive(key).isNumber()) throw new IllegalArgumentException(key + " must be an integer");
        long value;
        try { value = data.get(key).getAsBigDecimal().longValueExact(); }
        catch (ArithmeticException e) { throw new IllegalArgumentException(key + " must be an integer"); }
        if (value < min || value > max) throw new IllegalArgumentException(key + " is outside its range");
        return value;
    }
    private static boolean bool(JsonObject data, String key) { return data.has(key) && data.get(key).getAsBoolean(); }
    private static JsonObject error(Exception e) {
        JsonObject response = new JsonObject(); response.addProperty("ok", false);
        response.addProperty("error", e.getClass().getSimpleName() + ": " + String.valueOf(e.getMessage())); return response;
    }

    @Override public void close() {
        running = false;
        rejectPending();
        Thread.ofVirtual().name("oak-operator-close").start(this::cleanup);
    }

    private void rejectPending() {
        Work work; while ((work = queue.poll()) != null) work.result().complete(error(new IllegalStateException("Server stopping")));
    }

    private synchronized void cleanup() {
        try { if (listener != null) listener.close(); } catch (Exception ignored) {}
        for (SocketChannel client : clients) try { client.close(); } catch (Exception ignored) {}
        try { if (ownsSocket) Files.deleteIfExists(SOCKET); } catch (Exception ignored) {}
        ownsSocket = false;
    }
}
