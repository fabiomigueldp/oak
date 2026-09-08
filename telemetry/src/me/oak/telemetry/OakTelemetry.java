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
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.atomic.AtomicReference;

/** Read-only tick snapshots. All serialization and socket I/O run off-thread. */
public final class OakTelemetry implements ModInitializer {
    private static final Path PATH = Path.of("/run/oak-telemetry/positions.sock");
    private record Player(String uuid, String name, List<Double> position, String dimension, float yaw) {}
    private record Frame(int version, long tick, double sampled_at, List<Player> players) {}
    private final AtomicReference<Frame> latest = new AtomicReference<>();
    private final Set<SocketChannel> clients = ConcurrentHashMap.newKeySet();
    private volatile boolean running;
    private ServerSocketChannel listener;
    private boolean ownsSocket;
    private long tick;

    @Override public void onInitialize() {
        ServerLifecycleEvents.SERVER_STARTED.register(server -> start());
        ServerLifecycleEvents.SERVER_STOPPING.register(server -> close());
        ServerTickEvents.END_SERVER_TICK.register(server -> {
            if (++tick % (clients.isEmpty() ? 20 : 2) != 0) return;
            var players = server.getPlayerList().getPlayers().stream().limit(100).map(p ->
                new Player(p.getUUID().toString(), p.getName().getString(),
                    List.of(p.getX(), p.getY(), p.getZ()),
                    p.level().dimension().identifier().toString(), p.getYRot())).toList();
            latest.set(new Frame(1, tick, System.currentTimeMillis() / 1000.0, players));
        });
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
        try (client) {
            while (running) {
                Frame frame = latest.get();
                long now = System.nanoTime();
                if (frame != null && frame != previous && (previous == null || !frame.players().equals(previous.players()) || now - sent >= 1_000_000_000L)) {
                    byte[] bytes = (gson.toJson(frame) + "\n").getBytes(StandardCharsets.UTF_8);
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
