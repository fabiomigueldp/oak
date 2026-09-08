package me.oak.telemetry;

import com.google.gson.Gson;
import net.fabricmc.api.ModInitializer;
import net.fabricmc.fabric.api.event.lifecycle.v1.ServerTickEvents;
import net.fabricmc.fabric.api.event.lifecycle.v1.ServerLifecycleEvents;
import java.net.StandardProtocolFamily;
import java.net.UnixDomainSocketAddress;
import java.nio.ByteBuffer;
import java.nio.channels.ServerSocketChannel;
import java.nio.channels.SocketChannel;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.attribute.PosixFilePermissions;
import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.Set;
import java.util.Map;
import java.util.HashMap;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.world.entity.EquipmentSlot;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.core.component.DataComponents;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.atomic.AtomicReference;

/** Read-only tick snapshots. All serialization and socket I/O run off-thread. */
public final class OakTelemetry implements ModInitializer {
    private static final Path PATH = Path.of("/run/oak-telemetry/positions.sock");
    private record Equipment(String id, String asset, int color, boolean enchanted) {}
    private record Appearance(String textures, Map<String, Equipment> equipment, String main_arm) {}
    private record Player(String uuid, String name, List<Double> position, String dimension, float yaw,
        float body_yaw, float pitch, String pose, boolean sprinting, boolean grounded,
        String use, String swing_hand, long swing_id, float swing, Appearance appearance) {}
    private record Frame(int version, long tick, double sampled_at, List<Player> players) {}
    private final AtomicReference<Frame> latest = new AtomicReference<>();
    private final Set<SocketChannel> clients = ConcurrentHashMap.newKeySet();
    private volatile boolean running;
    private ServerSocketChannel listener;
    private boolean ownsSocket;
    private long tick;
    private final Map<String, Appearance> appearances = new HashMap<>();
    private final Map<String, Float> swings = new HashMap<>();
    private final Map<String, Long> swingIds = new HashMap<>();

    @Override public void onInitialize() {
        ServerLifecycleEvents.SERVER_STARTED.register(server -> start());
        ServerLifecycleEvents.SERVER_STOPPING.register(server -> close());
        ServerTickEvents.END_SERVER_TICK.register(server -> {
            ++tick;
            // Observe short arm actions each tick; only publish at the bounded rate.
            for (var p : server.getPlayerList().getPlayers()) {
                String id = p.getUUID().toString();
                float progress = p.isSwinging() ? p.getSwingAnimation(1) : -1;
                float previous = swings.getOrDefault(id, -1f);
                if (progress >= 0 && (previous < 0 || progress < previous)) swingIds.put(id, tick);
                swings.put(id, progress);
            }
            if (tick % (clients.isEmpty() ? 20 : 2) != 0) return;
            var players = server.getPlayerList().getPlayers().stream().limit(100).map(this::capture).toList();
            if (tick % 20 == 0) {
                Set<String> online = players.stream().map(Player::uuid).collect(java.util.stream.Collectors.toSet());
                appearances.keySet().retainAll(online); swings.keySet().retainAll(online); swingIds.keySet().retainAll(online);
            }
            latest.set(new Frame(1, tick, System.currentTimeMillis() / 1000.0, players));
        });
    }

    private Player capture(ServerPlayer p) {
        String id = p.getUUID().toString();
        if (!appearances.containsKey(id) || tick % 10 == 0) {
            Map<String, Equipment> equipment = new HashMap<>();
            for (var slot : List.of(EquipmentSlot.HEAD, EquipmentSlot.CHEST, EquipmentSlot.LEGS, EquipmentSlot.FEET, EquipmentSlot.MAINHAND, EquipmentSlot.OFFHAND)) {
                var stack = p.getItemBySlot(slot);
                if (stack.isEmpty()) continue;
                var wearable = stack.get(DataComponents.EQUIPPABLE);
                var dye = stack.get(DataComponents.DYED_COLOR);
                equipment.put(slot.getName(), new Equipment(BuiltInRegistries.ITEM.getKey(stack.getItem()).toString(),
                    wearable == null ? "" : wearable.assetId().map(a -> a.identifier().toString()).orElse(""),
                    dye == null ? 0xA06540 : dye.rgb(), stack.hasFoil()));
            }
            String textures = p.getGameProfile().properties().get("textures").stream().findFirst().map(v -> v.value()).orElse("");
            appearances.put(id, new Appearance(textures.length() <= 8192 ? textures : "", Map.copyOf(equipment), p.getMainArm().name()));
        }
        var swing = p.getCurrentSwing();
        return new Player(id, p.getName().getString(), List.of(p.getX(), p.getY(), p.getZ()),
            p.level().dimension().identifier().toString(), p.getYHeadRot(), p.yBodyRot, p.getXRot(),
            p.getPose().name(), p.isSprinting(), p.onGround(),
            p.isUsingItem() ? p.getUseItem().getUseAnimation().name() : "NONE",
            swing == null ? "MAIN_HAND" : swing.hand().name(), swingIds.getOrDefault(id, 0L),
            p.isSwinging() ? p.getSwingAnimation(1) : 0, appearances.get(id));
    }

    private void start() {
        try {
            if (Files.exists(PATH)) {
                try (SocketChannel probe = SocketChannel.open(StandardProtocolFamily.UNIX)) {
                    probe.connect(UnixDomainSocketAddress.of(PATH));
                    System.err.println("Oak telemetry socket is already active.");
                    return;
                } catch (java.io.IOException stale) {
                    // Only remove an unreachable socket in the private runtime directory.
                }
            }
            Files.deleteIfExists(PATH);
            listener = ServerSocketChannel.open(StandardProtocolFamily.UNIX);
            listener.bind(UnixDomainSocketAddress.of(PATH));
            ownsSocket = true;
            Files.setPosixFilePermissions(PATH, PosixFilePermissions.fromString("rw-rw----"));
            running = true;
            Thread.ofVirtual().name("oak-telemetry-accept").start(() -> {
                while (running) {
                    try {
                        SocketChannel client = listener.accept();
                        if (clients.size() >= 4) { client.close(); continue; }
                        client.configureBlocking(false);
                        clients.add(client);
                        Thread.ofVirtual().name("oak-telemetry-client").start(() -> send(client));
                    } catch (Exception e) {
                        if (running) System.err.println("Oak telemetry listener stopped: " + e.getClass().getSimpleName());
                        break;
                    }
                }
            });
        } catch (Exception e) {
            close();
            System.err.println("Oak telemetry unavailable; check the private runtime directory: " + e.getClass().getSimpleName());
        }
    }

    private void send(SocketChannel client) {
        Gson gson = new Gson();
        Frame previous = null;
        long sent = 0;
        Map<String, Appearance> sentAppearance = new HashMap<>();
        try (client) {
            while (running) {
                Frame frame = latest.get();
                long now = System.nanoTime();
                if (frame != null && frame != previous && (previous == null || !frame.players().equals(previous.players()) || now - sent >= 1_000_000_000L)) {
                    var json = gson.toJsonTree(frame).getAsJsonObject();
                    for (var element : json.getAsJsonArray("players")) {
                        var player = element.getAsJsonObject();
                        String id = player.get("uuid").getAsString();
                        Appearance appearance = frame.players().stream().filter(p -> p.uuid().equals(id)).findFirst().orElseThrow().appearance();
                        if (appearance.equals(sentAppearance.put(id, appearance))) player.remove("appearance");
                    }
                    sentAppearance.keySet().retainAll(frame.players().stream().map(Player::uuid).collect(java.util.stream.Collectors.toSet()));
                    byte[] bytes = (gson.toJson(json) + "\n").getBytes(StandardCharsets.UTF_8);
                    if (bytes.length > 65536) break;
                    ByteBuffer buffer = ByteBuffer.wrap(bytes);
                    long deadline = now + 500_000_000L;
                    while (buffer.hasRemaining()) {
                        client.write(buffer);
                        if (System.nanoTime() > deadline) throw new java.io.IOException("Slow telemetry consumer");
                        if (buffer.hasRemaining()) Thread.sleep(5);
                    }
                    previous = frame;
                    sent = now;
                }
                Thread.sleep(20);
            }
        } catch (Exception ignored) {
            // Disconnected/slow readers cannot delay game ticks or build a backlog.
        } finally {
            clients.remove(client);
        }
    }

    private void close() {
        running = false;
        try { if (listener != null) listener.close(); } catch (Exception ignored) {}
        for (SocketChannel client : clients) try { client.close(); } catch (Exception ignored) {}
        clients.clear();
        latest.set(null);
        try { if (ownsSocket) Files.deleteIfExists(PATH); } catch (Exception ignored) {}
        ownsSocket = false;
    }
}
