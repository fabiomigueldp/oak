package me.oak.aviary;

import com.google.gson.*;
import java.net.*;
import java.nio.*;
import java.nio.channels.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.nio.file.attribute.PosixFilePermissions;
import java.util.*;
import java.util.concurrent.*;

/** Owner-only local transport. No arbitrary commands or world writes are accepted. */
final class AviaryControl implements AutoCloseable {
    private static final Gson JSON=new Gson();
    private final Aviary app;
    private final Path socket;
    private final Semaphore clients=new Semaphore(4);
    private final Map<String,JsonObject> checks=new HashMap<>();
    private volatile boolean running=true;
    private ServerSocketChannel listener;
    AviaryControl(Aviary app){
        this.app=app;
        socket=Path.of(System.getProperty("oak.aviary.socket","/run/oak-telemetry/aviary.sock"));
        if(!"isolated".equals(System.getProperty("oak.aviary.smoke")))Thread.ofVirtual().name("oak-aviary-control").start(this::listen);
    }
    private void listen(){
        try {
            Files.deleteIfExists(socket);listener=ServerSocketChannel.open(StandardProtocolFamily.UNIX);listener.bind(UnixDomainSocketAddress.of(socket));
            Files.setPosixFilePermissions(socket,PosixFilePermissions.fromString("rw-------"));
            while(running){var client=listener.accept();if(!clients.tryAcquire()){client.close();continue;}Thread.ofVirtual().start(()->handle(client));}
        }catch(Exception e){if(running)System.err.println("Aviary administrative socket unavailable: "+e.getClass().getSimpleName());}
    }
    private void handle(SocketChannel client){
        try(client){
            client.configureBlocking(false);ByteBuffer buffer=ByteBuffer.allocate(8192);long deadline=System.nanoTime()+5_000_000_000L;
            while(true){int n=client.read(buffer);if(n<0)throw new IllegalArgumentException("Request interrupted");if(buffer.position()>0&&buffer.get(buffer.position()-1)=='\n')break;
                if(!buffer.hasRemaining()||System.nanoTime()>deadline)throw new IllegalArgumentException("Request limit");Thread.sleep(5);}
            buffer.flip();var request=JsonParser.parseString(StandardCharsets.UTF_8.decode(buffer).toString()).getAsJsonObject();
            JsonObject result;
            try {long expires=System.nanoTime()+5_000_000_000L;result=app.server.submit(()->{if(!running||System.nanoTime()>expires)throw new IllegalArgumentException("Request expired. Refresh the current state.");return execute(request);}).get(6,TimeUnit.SECONDS);}
            catch(Exception e){result=new JsonObject();Throwable cause=e.getCause()==null?e:e.getCause();result.addProperty("error",cause instanceof IllegalArgumentException?cause.getMessage():"Could not complete. Refresh to check the current state.");}
            ByteBuffer response=ByteBuffer.wrap((JSON.toJson(result)+"\n").getBytes(StandardCharsets.UTF_8));deadline=System.nanoTime()+2_000_000_000L;
            while(response.hasRemaining()&&System.nanoTime()<deadline){client.write(response);if(response.hasRemaining())Thread.sleep(5);}
        }catch(Exception e){System.err.println("Aviary control request interrupted");}finally{clients.release();}
    }
    JsonObject execute(JsonObject request){
        String action=request.get("action").getAsString();
        if(action.equals("status"))return status();
        if(action.equals("policy")){
            if(request.get("revision").getAsLong()!=app.store.revision)throw new IllegalArgumentException("Aviary changed. Refresh before saving.");
            if(!request.keySet().stream().allMatch(Set.of("action","revision","fieldPickup","discoverPublic","maxOwnedPerches","maxFlights","shortcutDistance","id")::contains))throw new IllegalArgumentException("Unsupported fields.");
            var previous=app.store.network;var next=new AviaryStore.NetworkPolicy(request.get("fieldPickup").getAsBoolean(),request.get("discoverPublic").getAsBoolean(),request.get("maxOwnedPerches").getAsInt());
            var previousSettings=app.store.settings;
            var settings=new AviaryStore.Settings(previousSettings.enabled(),previousSettings.packUrl(),previousSettings.packSha1(),request.has("shortcutDistance")?request.get("shortcutDistance").getAsDouble():previousSettings.shortcutDistance(),request.has("maxFlights")?request.get("maxFlights").getAsInt():previousSettings.maxFlights());
            AviaryStore.validate(next);AviaryStore.validate(settings);app.store.network=next;app.store.settings=settings;
            try{app.store.savePolicy();}catch(Exception e){app.store.network=previous;app.store.settings=previousSettings;throw new IllegalArgumentException("Could not persist network settings. No changes applied.");}
            return status();
        }
        String id=request.get("port").getAsString();var old=app.store.ports.get(id);
        if(old==null)throw new IllegalArgumentException("Aviport no longer exists.");
        if(action.equals("check")){
            JsonObject check=new JsonObject();String reason=Journey.portIssue(app.server.overworld(),old);
            check.addProperty("clear",reason.isEmpty());check.addProperty("message",reason.isEmpty()?"Landing area clear":reason);check.addProperty("checked_at",System.currentTimeMillis()/1000.0);
            check.addProperty("revision",app.store.revision);
            checks.put(id,check);return status();
        }
        if(!action.equals("edit")||request.get("revision").getAsLong()!=app.store.revision)throw new IllegalArgumentException("Aviports changed. Refresh before saving.");
        if(app.busyPort(id))throw new IllegalArgumentException("This aviport has an active flight.");
        if(!request.keySet().stream().allMatch(Set.of("action","port","revision","name","shared","departureYaw","arrivalYaw","id","color","style","birdName","hub")::contains))throw new IllegalArgumentException("Unsupported fields.");
        var next=new AviaryStore.Port(old.id(),request.get("name").getAsString(),old.dimension(),old.x(),old.y(),old.z(),old.yaw(),old.owner(),request.get("shared").getAsBoolean(),heading(request,"departureYaw"),heading(request,"arrivalYaw"));
        var before=app.store.perches.get(id);AviaryStore.PerchData perch=null;
        if(before==null&&Set.of("color","style","birdName","hub").stream().anyMatch(request::has))throw new IllegalArgumentException("Attach a perch before changing its appearance.");
        if(before!=null){
            perch=new AviaryStore.PerchData(before.x(),before.y(),before.z(),request.has("color")?request.get("color").getAsString():before.color(),request.has("style")?request.get("style").getAsString():before.style(),request.has("birdName")?request.get("birdName").getAsString():before.birdName(),before.guests(),request.has("hub")?request.get("hub").getAsBoolean():before.hub(),before.active());
            AviaryStore.validate(perch);
        }
        AviaryStore.validate(next);app.store.ports.put(id,next);if(perch!=null)app.store.perches.put(id,perch);
        try{app.store.savePolicy();}catch(Exception e){app.store.ports.put(id,old);if(before!=null)app.store.perches.put(id,before);throw new IllegalArgumentException("Could not persist aviport. No changes applied.");}
        if(app.perches!=null)app.perches.update(id);
        checks.remove(id);return status();
    }
    private static Float heading(JsonObject request,String key){return request.get(key).isJsonNull()?null:request.get(key).getAsFloat();}
    JsonObject status(){
        JsonObject result=new JsonObject();result.addProperty("available",true);result.addProperty("enabled",app.store.settings.enabled());result.addProperty("revision",app.store.revision);result.addProperty("error",app.store.error);
        result.addProperty("sampled_at",System.currentTimeMillis()/1000.0);result.addProperty("maxFlights",app.store.settings.maxFlights());
        result.addProperty("shortcutDistance",app.store.settings.shortcutDistance());
        result.add("network",JSON.toJsonTree(app.store.network));result.addProperty("waitingCalls",app.waitingCalls());
        JsonArray ports=new JsonArray();for(var p:app.store.ports.values()){
            JsonObject port=JSON.toJsonTree(p).getAsJsonObject();port.addProperty("busy",app.busyPort(p.id()));
            var perch=app.store.perches.get(p.id());port.add("perch",JSON.toJsonTree(perch));port.addProperty("kind",perch==null?"legacy":"perch");port.addProperty("active",perch==null||perch.active());
            port.addProperty("anchorStatus",perch==null?"legacy":app.perches==null?"unloaded":app.perches.status(p.id()));
            var owner=app.server.getPlayerList().getPlayer(UUID.fromString(p.owner()));if(owner!=null)port.addProperty("ownerName",owner.getGameProfile().name());
            if(perch!=null){var names=new JsonObject();for(String guest:perch.guests()){var player=app.server.getPlayerList().getPlayer(UUID.fromString(guest));if(player!=null)names.addProperty(guest,player.getGameProfile().name());}port.add("guestNames",names);}
            if(checks.containsKey(p.id())&&checks.get(p.id()).get("revision").getAsLong()==app.store.revision)port.add("check",checks.get(p.id()).deepCopy());ports.add(port);
        }result.add("ports",ports);JsonArray flights=new JsonArray();for(var j:app.journeys.values())flights.add(j.status());result.add("flights",flights);return result;
    }
    public void close(){running=false;try{if(listener!=null){listener.close();Files.deleteIfExists(socket);}}catch(Exception ignored){}}
}
