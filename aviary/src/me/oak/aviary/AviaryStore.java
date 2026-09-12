package me.oak.aviary;

import com.google.gson.*;
import java.nio.ByteBuffer;
import java.nio.channels.FileChannel;
import java.nio.file.*;
import java.util.*;
import java.util.concurrent.*;

/** Runtime-only policy and recovery records. Never persist entities or inventory copies. */
public final class AviaryStore implements AutoCloseable {
    public record Port(String id,String name,String dimension,double x,double y,double z,float yaw,String owner,boolean shared,Float departureYaw,Float arrivalYaw) {
        public Port(String id,String name,String dimension,double x,double y,double z,float yaw,String owner,boolean shared){this(id,name,dimension,x,y,z,yaw,owner,shared,null,null);}
    }
    public record Preferences(Set<String> favorites,boolean quick,boolean freeCamera,Set<String> discovered) {
        public Preferences(Set<String> favorites,boolean quick,boolean freeCamera){this(favorites,quick,freeCamera,Set.of());}
        public Preferences {favorites=Set.copyOf(favorites==null?Set.of():favorites);discovered=Set.copyOf(discovered==null?Set.of():discovered);}
    }
    /** A placed object is separate from its address and the bird's landing position. */
    public record PerchData(int x,int y,int z,String color,String style,String birdName,Set<String> guests,boolean hub,boolean active) {
        public PerchData(int x,int y,int z,String color,boolean active){this(x,y,z,color,"oak","Condor",Set.of(),false,active);}
        public PerchData {guests=Set.copyOf(guests==null?Set.of():guests);}
    }
    public record NetworkPolicy(boolean fieldPickup,boolean discoverPublic,int maxOwnedPerches) {}
    public static final Set<String> COLORS=Set.of("white","orange","magenta","light_blue","yellow","lime","pink","gray","light_gray","cyan","purple","blue","brown","green","red","black");
    public static final Set<String> STYLES=Set.of("oak","spruce","birch");
    public record Recovery(String player,String originDimension,double x,double y,double z,float yaw,
                           String destinationDimension,double dx,double dy,double dz,float dyaw,boolean transferred) {}
    public record Settings(boolean enabled,String packUrl,String packSha1,double shortcutDistance,int maxFlights) {}
    private record Data(long revision,Settings settings,List<Port> ports,Map<String,PerchData> perches,NetworkPolicy network) {}
    private static final Gson GSON=new GsonBuilder().setPrettyPrinting().create();
    private final Path root=Path.of(System.getProperty("oak.aviary.state","config/oak-aviary"));
    private final ThreadPoolExecutor writer=new ThreadPoolExecutor(1,1,0,TimeUnit.SECONDS,new ArrayBlockingQueue<>(32),r->Thread.ofPlatform().daemon().name("oak-aviary-store").unstarted(r),new ThreadPoolExecutor.AbortPolicy());
    public final Map<String,Port> ports=new LinkedHashMap<>();
    public final Map<String,PerchData> perches=new LinkedHashMap<>();
    public final Map<UUID,Recovery> recoveries=new ConcurrentHashMap<>();
    public Settings settings=new Settings(false,"","",500,2);
    public NetworkPolicy network=new NetworkPolicy(true,true,4);
    public long revision=0;
    public String error="";
    private final Map<UUID,Preferences> preferences=new HashMap<>();

    public Preferences preferences(UUID player) {
        return preferences.computeIfAbsent(player,id->{
            try {
                Path path=root.resolve("players").resolve(id+".json");
                if(Files.exists(path)){
                    Preferences value=GSON.fromJson(read(path),Preferences.class);
                    if(value!=null&&value.favorites().size()<=128&&value.discovered().size()<=128)return value;
                }
            }catch(Exception e){System.err.println("Aviary preferences unavailable; using defaults");}
            return new Preferences(Set.of(),false,false);
        });
    }
    public void preferences(UUID player,Preferences value)throws Exception {
        if(value.favorites().size()>128||value.discovered().size()>128)throw new IllegalArgumentException("Too many destinations");
        Files.createDirectories(root.resolve("players"));
        atomic(root.resolve("players").resolve(player+".json"),GSON.toJson(value));
        preferences.put(player,value);
    }
    void forget(UUID player){preferences.remove(player);}

    public AviaryStore() {
        try {
            Files.createDirectories(root.resolve("journeys"));
            Path file=root.resolve("settings.json");
            if(Files.exists(file)) {
                Data data=GSON.fromJson(read(file),Data.class);
                validate(data.settings());settings=data.settings();revision=data.revision();
                if(data.ports().size()>128)throw new IllegalArgumentException("Too many ports");
                for(Port port:data.ports()) { validate(port);if(ports.put(port.id(),port)!=null)throw new IllegalArgumentException("Duplicate port"); }
                if(data.network()!=null){validate(data.network());network=data.network();}
                if(data.perches()!=null){
                    if(data.perches().size()>128)throw new IllegalArgumentException("Too many perches");
                    for(var entry:data.perches().entrySet()){
                        if(!ports.containsKey(entry.getKey()))throw new IllegalArgumentException("Perch address missing");
                        validate(entry.getValue());perches.put(entry.getKey(),entry.getValue());
                    }
                }
            }
            try(var files=Files.list(root.resolve("journeys"))) {
                var records=files.filter(p->p.toString().endsWith(".json")).limit(129).toList();
                if(records.size()>128)throw new IllegalArgumentException("Too many recovery records");
                for(Path path:records) {
                    Recovery value=GSON.fromJson(read(path),Recovery.class);
                    validate(new Port("recovery","Recovery",value.originDimension(),value.x(),value.y(),value.z(),value.yaw(),value.player(),false));
                    validate(new Port("recovery","Recovery",value.destinationDimension(),value.dx(),value.dy(),value.dz(),value.dyaw(),value.player(),false));
                    if(!path.getFileName().toString().equals(value.player()+".json"))throw new IllegalArgumentException("Recovery identity mismatch");
                    recoveries.put(UUID.fromString(value.player()),value);
                }
            }
        } catch(Exception e) {error="Aviary storage needs repair. Travel is disabled.";}
    }
    private static String read(Path path)throws Exception {if(Files.size(path)>262144)throw new IllegalArgumentException("State limit");return Files.readString(path);}
    public static void validate(Settings s) {
        if(s==null||s.packUrl()==null||s.packSha1()==null||!Double.isFinite(s.shortcutDistance())||s.shortcutDistance()<100||s.shortcutDistance()>500||s.maxFlights()<1||s.maxFlights()>4)throw new IllegalArgumentException("Invalid settings");
        if(s.enabled()&&(!s.packUrl().startsWith("https://")||!s.packSha1().matches("[a-f0-9]{40}")))throw new IllegalArgumentException("A published HTTPS resource pack is required");
    }
    public static void validate(Port p) {
        if(p==null||p.id()==null||!p.id().matches("[a-z0-9_-]{1,32}")||!label(p.name(),48)||!"minecraft:overworld".equals(p.dimension())||!Double.isFinite(p.x())||!Double.isFinite(p.y())||!Double.isFinite(p.z())||!Float.isFinite(p.yaw())||Math.abs(p.x())>29999000||Math.abs(p.z())>29999000||p.y()<-60||p.y()>290)throw new IllegalArgumentException("Invalid port");
        UUID.fromString(p.owner());
        for(Float heading:new Float[]{p.departureYaw(),p.arrivalYaw()})if(heading!=null&&(!Float.isFinite(heading)||heading< -180||heading>180))throw new IllegalArgumentException("Invalid approach heading");
    }
    private static boolean label(String text,int length){return text!=null&&!text.isBlank()&&text.length()<=length&&text.codePoints().noneMatch(Character::isISOControl);}
    public static void validate(NetworkPolicy p){if(p==null||p.maxOwnedPerches()<1||p.maxOwnedPerches()>16)throw new IllegalArgumentException("Per-player perch limit must be between 1 and 16.");}
    public static void validate(PerchData p){
        if(p==null||Math.abs((long)p.x())>29999000||Math.abs((long)p.z())>29999000||p.y()<-61||p.y()>290||!COLORS.contains(p.color())||!STYLES.contains(p.style())||!label(p.birdName(),32)||p.guests().size()>64)throw new IllegalArgumentException("Invalid perch");
        for(String guest:p.guests())UUID.fromString(guest);
    }
    private static void atomic(Path path,String text)throws Exception {
        Path pending=path.resolveSibling(path.getFileName()+".pending");
        try(var channel=FileChannel.open(pending,StandardOpenOption.CREATE,StandardOpenOption.TRUNCATE_EXISTING,StandardOpenOption.WRITE)) {
            var bytes=ByteBuffer.wrap(text.getBytes(java.nio.charset.StandardCharsets.UTF_8));while(bytes.hasRemaining())channel.write(bytes);channel.force(true);
        }
        Files.move(pending,path,StandardCopyOption.ATOMIC_MOVE,StandardCopyOption.REPLACE_EXISTING);
        try(var parent=FileChannel.open(path.getParent(),StandardOpenOption.READ)){parent.force(true);}
    }
    public void savePolicy()throws Exception {
        if(!error.isEmpty())throw new IllegalStateException(error);
        validate(settings);validate(network);
        if(ports.size()>128||perches.size()>ports.size())throw new IllegalArgumentException("Perch limit");
        for(var port:ports.values())validate(port);
        for(var entry:perches.entrySet()){if(!ports.containsKey(entry.getKey()))throw new IllegalArgumentException("Perch address missing");validate(entry.getValue());}
        atomic(root.resolve("settings.json"),GSON.toJson(new Data(revision+1,settings,List.copyOf(ports.values()),Map.copyOf(perches),network)));revision++;
    }
    public CompletableFuture<Void> journal(Recovery recovery) {
        return CompletableFuture.runAsync(()->{
            try {atomic(root.resolve("journeys").resolve(recovery.player()+".json"),GSON.toJson(recovery));recoveries.put(UUID.fromString(recovery.player()),recovery);}
            catch(Exception e){throw new CompletionException(e);}
        },writer);
    }
    public CompletableFuture<Void> complete(UUID player) {
        return CompletableFuture.runAsync(()->{
            try {Files.deleteIfExists(root.resolve("journeys").resolve(player+".json"));try(var parent=FileChannel.open(root.resolve("journeys"),StandardOpenOption.READ)){parent.force(true);}recoveries.remove(player);}
            catch(Exception e){throw new CompletionException(e);}
        },writer);
    }
    public void close(){writer.shutdown();try{writer.awaitTermination(10,TimeUnit.SECONDS);}catch(InterruptedException e){Thread.currentThread().interrupt();}}
}
