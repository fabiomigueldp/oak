package me.oak.aviary;

import com.mojang.brigadier.arguments.StringArgumentType;
import net.fabricmc.api.ModInitializer;
import net.fabricmc.loader.api.FabricLoader;
import net.fabricmc.fabric.api.command.v2.CommandRegistrationCallback;
import net.fabricmc.fabric.api.event.lifecycle.v1.ServerLifecycleEvents;
import net.fabricmc.fabric.api.event.lifecycle.v1.ServerTickEvents;
import net.fabricmc.fabric.api.networking.v1.ServerPlayConnectionEvents;
import net.fabricmc.fabric.api.entity.event.v1.ServerLivingEntityEvents;
import net.minecraft.commands.Commands;
import net.minecraft.core.Holder;
import net.minecraft.network.chat.*;
import net.minecraft.network.protocol.common.*;
import net.minecraft.network.protocol.game.ClientboundSetCameraPacket;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.server.permissions.Permissions;
import net.minecraft.server.dialog.*;
import net.minecraft.server.dialog.action.StaticAction;
import net.minecraft.server.dialog.body.PlainMessage;
import java.nio.charset.StandardCharsets;
import java.util.*;
import java.util.concurrent.*;

/** Vanilla-client transportation. All world access is confined to the server thread. */
public final class Aviary implements ModInitializer {
    static Aviary instance;
    MinecraftServer server;
    AviaryStore store;
    final Map<UUID,Journey> journeys=new LinkedHashMap<>();
    private final Set<UUID> protectedPlayers=ConcurrentHashMap.newKeySet();
    private final Set<UUID> loaded=ConcurrentHashMap.newKeySet();
    private final Set<UUID> skips=ConcurrentHashMap.newKeySet();
    private final Set<UUID> recovering=new HashSet<>();
    private volatile UUID packId;
    private long tick;

    public void onInitialize() {
        instance=this;
        ServerLifecycleEvents.SERVER_STARTED.register(s->{server=s;store=new AviaryStore();packId=UUID.nameUUIDFromBytes(store.settings.packSha1().getBytes(StandardCharsets.UTF_8));System.out.println("Oak Aviary ready; enabled="+store.settings.enabled());});
        ServerTickEvents.END_SERVER_TICK.register(s->tick());
        ServerLifecycleEvents.SERVER_STOPPING.register(s->{for(var j:List.copyOf(journeys.values()))j.abort("Travel stopped. The server is restarting.");if(store!=null)store.close();});
        ServerPlayConnectionEvents.JOIN.register((handler,sender,s)->{
            if(store!=null&&store.recoveries.containsKey(handler.player.getUUID()))protectedPlayers.add(handler.player.getUUID());
            var vehicle=handler.player.getRootVehicle();
            if(vehicle!=handler.player&&vehicle.entityTags().contains("oak_aviary_temporary")){handler.player.stopRiding();vehicle.discard();}
            restoreCamera(handler.player);sendPack(handler.player);
        });
        ServerPlayConnectionEvents.DISCONNECT.register((handler,s)->{UUID id=handler.player.getUUID();Journey j=journeys.get(id);if(j!=null)j.abort("Travel interrupted.");loaded.remove(id);skips.remove(id);});
        ServerLivingEntityEvents.ALLOW_DAMAGE.register((entity,source,amount)->!isTravelling(entity.getUUID()));
        CommandRegistrationCallback.EVENT.register((dispatcher,access,environment)->dispatcher.register(Commands.literal("aviary")
            .executes(c->menu(c.getSource().getPlayerOrException()))
            .then(Commands.literal("pack").executes(c->{sendPack(c.getSource().getPlayerOrException());return 1;}))
            .then(Commands.literal("skip").executes(c->{requestSkip(c.getSource().getPlayerOrException().getUUID());return 1;}))
            .then(Commands.literal("claim").then(Commands.argument("id",StringArgumentType.word()).executes(c->addPort(c.getSource().getPlayerOrException(),StringArgumentType.getString(c,"id"),false))))
            .then(Commands.literal("unclaim").then(Commands.argument("id",StringArgumentType.word()).executes(c->removePort(c.getSource().getPlayerOrException(),StringArgumentType.getString(c,"id")))))
            .then(Commands.literal("name").then(Commands.argument("port",StringArgumentType.word()).then(Commands.argument("name",StringArgumentType.greedyString()).executes(c->editPort(c.getSource().getPlayerOrException(),StringArgumentType.getString(c,"port"),StringArgumentType.getString(c,"name"),null)))))
            .then(Commands.literal("share").then(Commands.argument("port",StringArgumentType.word()).then(Commands.argument("public",com.mojang.brigadier.arguments.BoolArgumentType.bool()).executes(c->editPort(c.getSource().getPlayerOrException(),StringArgumentType.getString(c,"port"),null,com.mojang.brigadier.arguments.BoolArgumentType.getBool(c,"public"))))))
            .then(Commands.literal("fly").then(Commands.argument("port",StringArgumentType.word()).suggests((c,b)->{if(store!=null)for(var p:store.ports.values())if(visible(p,c.getSource().getPlayerOrException()))b.suggest(p.id());return b.buildFuture();})
                .executes(c->fly(c.getSource().getPlayerOrException(),StringArgumentType.getString(c,"port")))))
            .then(Commands.literal("port").requires(c->c.permissions().hasPermission(Permissions.COMMANDS_GAMEMASTER))
                .then(Commands.literal("add").then(Commands.argument("id",StringArgumentType.word()).executes(c->addPort(c.getSource().getPlayerOrException(),StringArgumentType.getString(c,"id"),true))))
                .then(Commands.literal("remove").then(Commands.argument("id",StringArgumentType.word()).executes(c->removePort(c.getSource().getPlayerOrException(),StringArgumentType.getString(c,"id"))))))
            .then(Commands.literal("status").requires(c->c.permissions().hasPermission(Permissions.COMMANDS_GAMEMASTER)).executes(c->{c.getSource().sendSuccess(()->Component.literal(store==null?"Starting":("Aviary: "+(store.settings.enabled()?"enabled":"disabled")+"; ports="+store.ports.size()+"; flights="+journeys.size()+"; "+store.error)),false);return 1;}))));
    }
    static void tell(ServerPlayer p,String message){p.sendSystemMessage(Component.literal(message));}
    public static boolean isTravelling(UUID id){return instance!=null&&instance.protectedPlayers.contains(id);}
    public static void requestSkip(UUID id){if(isTravelling(id))instance.skips.add(id);}
    public static void packResponse(UUID id,ServerboundResourcePackPacket packet) {
        Aviary a=instance;if(a==null||!packet.id().equals(a.packId))return;
        if(packet.action()==ServerboundResourcePackPacket.Action.SUCCESSFULLY_LOADED)a.loaded.add(id);
        else if(packet.action()!=ServerboundResourcePackPacket.Action.ACCEPTED&&packet.action()!=ServerboundResourcePackPacket.Action.DOWNLOADED){a.loaded.remove(id);requestSkip(id);}
    }
    private boolean javaPlayer(ServerPlayer p) {
        if(!FabricLoader.getInstance().isModLoaded("floodgate"))return true;
        try {Class<?> api=Class.forName("org.geysermc.floodgate.api.FloodgateApi");Object value=api.getMethod("getInstance").invoke(null);return !(boolean)api.getMethod("isFloodgatePlayer",UUID.class).invoke(value,p.getUUID());}
        catch(ReflectiveOperationException e){return false;}
    }
    private void sendPack(ServerPlayer p) {
        if(store==null||!store.settings.enabled()||!store.error.isEmpty()||!javaPlayer(p))return;
        p.connection.send(new ClientboundResourcePackPushPacket(packId,store.settings.packUrl(),store.settings.packSha1(),false,Optional.of(Component.literal("Condor travel models and camera effects. Optional."))));
    }
    static boolean visible(AviaryStore.Port port,ServerPlayer p){return port.shared()||port.owner().equals(p.getUUID().toString())||p.permissions().hasPermission(Permissions.COMMANDS_GAMEMASTER);}
    int menu(ServerPlayer p) {
        if(store==null)return 0;
        if(!javaPlayer(p)){tell(p,"Aviary requires Minecraft Java Edition.");return 0;}
        var buttons=new ArrayList<ActionButton>();
        for(var port:store.ports.values())if(visible(port,p))buttons.add(new ActionButton(new CommonButtonData(Component.literal(port.name()),150),Optional.of(new StaticAction(new ClickEvent.RunCommand("/aviary fly "+port.id())))));
        var common=new CommonDialogData(Component.literal("Aviary"),Optional.empty(),true,false,DialogAction.CLOSE,List.of(new PlainMessage(Component.literal(buttons.isEmpty()?"No aviports available.":"Choose a destination. Depart from an aviport. Hold Shift to skip the flight."),310)),List.of());
        if(buttons.isEmpty())buttons.add(new ActionButton(new CommonButtonData(Component.literal("Close"),150),Optional.empty()));
        p.openDialog(Holder.direct(new MultiActionDialog(common,buttons,Optional.empty(),2)));return 1;
    }
    int addPort(ServerPlayer p,String id,boolean shared) {
        if(store==null||isTravelling(p.getUUID()))return 0;
        if(!javaPlayer(p)){tell(p,"Aviary requires Minecraft Java Edition.");return 0;}
        if(!shared&&store.ports.values().stream().filter(q->q.owner().equals(p.getUUID().toString())).count()>=2){tell(p,"You can claim up to two aviports.");return 0;}
        if(store.ports.values().stream().anyMatch(q->q.dimension().equals(p.level().dimension().identifier().toString())&&p.position().distanceTo(Journey.position(q))<16)){tell(p,"Another aviport is too close. Leave at least 16 blocks between aviports.");return 0;}
        var port=new AviaryStore.Port(id,id.replace('_',' '),p.level().dimension().identifier().toString(),Math.floor(p.getX())+.5,p.getY(),Math.floor(p.getZ())+.5,p.getYRot(),p.getUUID().toString(),shared);
        try {AviaryStore.validate(port);if(store.ports.containsKey(id)||store.ports.size()>=128)throw new IllegalArgumentException("Port already exists or the port limit was reached.");if(!Journey.clearPort(p.level(),port))throw new IllegalArgumentException("Use a clear landing area with a solid floor and 24 blocks of open air above it.");store.ports.put(id,port);try{store.savePolicy();}catch(Exception e){store.ports.remove(id);throw e;}tell(p,"Aviport added: "+port.name());return 1;}
        catch(Exception e){tell(p,"Could not add aviport: "+e.getMessage());return 0;}
    }
    int editPort(ServerPlayer p,String id,String name,Boolean shared) {
        var old=store.ports.get(id);
        if(old==null||!(old.owner().equals(p.getUUID().toString())||p.permissions().hasPermission(Permissions.COMMANDS_GAMEMASTER))){tell(p,"You can only edit your own aviports.");return 0;}
        if(journeys.values().stream().anyMatch(j->j.uses(id))){tell(p,"This aviport has an active flight.");return 0;}
        var updated=new AviaryStore.Port(old.id(),name==null?old.name():name,old.dimension(),old.x(),old.y(),old.z(),old.yaw(),old.owner(),shared==null?old.shared():shared);
        try {AviaryStore.validate(updated);store.ports.put(id,updated);try{store.savePolicy();}catch(Exception e){store.ports.put(id,old);throw e;}tell(p,"Aviport saved.");return 1;}
        catch(Exception e){tell(p,"Could not save aviport.");return 0;}
    }
    int removePort(ServerPlayer p,String id) {
        var owned=store.ports.get(id);
        if(owned==null||!(owned.owner().equals(p.getUUID().toString())||p.permissions().hasPermission(Permissions.COMMANDS_GAMEMASTER))){tell(p,"You can only remove your own aviports.");return 0;}
        if(journeys.values().stream().anyMatch(j->j.uses(id))){tell(p,"This aviport has an active flight.");return 0;}
        var previous=store.ports.remove(id);if(previous==null)return 0;
        try {store.savePolicy();tell(p,"Aviport removed.");return 1;}catch(Exception e){store.ports.put(id,previous);tell(p,"Could not save aviports.");return 0;}
    }
    int fly(ServerPlayer p,String id) {
        try {
            if(store==null||!store.settings.enabled()||!store.error.isEmpty())throw new IllegalStateException("Aviary is unavailable.");
            if(!javaPlayer(p))throw new IllegalStateException("Aviary requires Minecraft Java Edition.");
            if(!loaded.contains(p.getUUID()))throw new IllegalStateException("Accept the Aviary resource pack first. Use /aviary pack to try again.");
            if(isTravelling(p.getUUID())||store.recoveries.containsKey(p.getUUID()))throw new IllegalStateException("Your previous flight is still being completed.");
            if(p.isPassenger()||p.isSleeping()||p.isSpectator()||!p.isAlive())throw new IllegalStateException("Stand at an aviport to depart.");
            if(journeys.size()>=store.settings.maxFlights())throw new IllegalStateException("All birds are in flight. Try again shortly.");
            var destination=store.ports.get(id);
            if(destination==null||!visible(destination,p))throw new IllegalStateException("Aviport unavailable.");
            var origin=store.ports.values().stream().filter(q->visible(q,p)&&q.dimension().equals(p.level().dimension().identifier().toString())&&p.position().distanceTo(Journey.position(q))<4).findFirst().orElseThrow(()->new IllegalStateException("Stand at an aviport to depart."));
            if(origin.id().equals(id))throw new IllegalStateException("Choose another aviport.");
            if(journeys.values().stream().anyMatch(j->j.uses(origin.id())||j.uses(id)))throw new IllegalStateException("An aviport on this route is busy.");
            protectedPlayers.add(p.getUUID());
            try {journeys.put(p.getUUID(),new Journey(this,p,origin,destination));}catch(Exception e){protectedPlayers.remove(p.getUUID());throw e;}
            tell(p,"Your bird is on its way. Hold Shift to skip.");return 1;
        }catch(Exception e){tell(p,e.getMessage()==null?"Could not start flight.":e.getMessage());return 0;}
    }
    static void restoreCamera(ServerPlayer p) {
        p.connection.send(new ClientboundSetCameraPacket(p));
        boolean changed=false;
        for(var effect:List.copyOf(p.getPostEffects()))if(effect.getNamespace().equals("oak_aviary"))changed|=p.removePostEffect(effect);
        if(changed)p.sendPostEffects();
    }
    void released(UUID id){journeys.remove(id);protectedPlayers.remove(id);skips.remove(id);}
    private void tick() {
        if(store==null)return;
        ++tick;
        for(var j:List.copyOf(journeys.values()))try {if(skips.remove(j.player.getUUID()))j.skip=true;j.tick();}catch(Exception e){System.err.println("Oak Aviary flight failed: "+e);j.abort("Flight interrupted. Returning to a safe aviport.");}
        if(tick%20==0)for(var p:server.getPlayerList().getPlayers()) {
            UUID id=p.getUUID();var recovery=store.recoveries.get(id);
            if(recovery!=null&&!journeys.containsKey(id)&&recovering.add(id)) {
                protectedPlayers.add(id);
                // Recovery is local to registered overworld ports; no inventory or game-mode state is changed.
                var level=server.overworld();double x=recovery.transferred()?recovery.dx():recovery.x(), y=recovery.transferred()?recovery.dy():recovery.y(),z=recovery.transferred()?recovery.dz():recovery.z();
                level.getChunkSource().getChunk((int)Math.floor(x)>>4,(int)Math.floor(z)>>4,true);
                var safe=Journey.safeLanding(level,x,y,z);
                if(safe==null){recovering.remove(id);p.connection.disconnect(Component.literal("Your aviport needs a safe landing area. Ask an administrator to clear it before rejoining."));continue;}
                p.stopRiding();restoreCamera(p);p.teleportTo(level,safe.x,safe.y,safe.z,Set.of(),recovery.transferred()?recovery.dyaw():recovery.yaw(),0,true);p.resetFallDistance();
                store.complete(id).whenComplete((v,e)->server.execute(()->{recovering.remove(id);if(e==null)protectedPlayers.remove(id);}));
            }
        }
    }
}
