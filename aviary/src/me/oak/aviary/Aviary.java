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
    Perches perches;
    private PerchMenus perchMenus;
    private GroupFlights groups;
    final Map<UUID,Journey> journeys=new LinkedHashMap<>();
    private final Set<UUID> protectedPlayers=ConcurrentHashMap.newKeySet();
    private final Set<UUID> loaded=ConcurrentHashMap.newKeySet();
    private final Set<UUID> skips=ConcurrentHashMap.newKeySet();
    private final Map<UUID,Integer> heldShift=new ConcurrentHashMap<>();
    private final Set<UUID> recovering=new HashSet<>();
    private volatile UUID packId;
    private long tick;
    long planningDeadline;
    private AviaryControl control;
    private record Call(String destination,net.minecraft.world.phys.Vec3 position,long expires) {}
    private final Map<UUID,Call> queue=new LinkedHashMap<>();
    private final Map<UUID,Long> callTimes=new HashMap<>();

    public void onInitialize() {
        instance=this;
        perches=new Perches(this);perches.install();perchMenus=new PerchMenus(this);groups=new GroupFlights(this);
        ServerLifecycleEvents.SERVER_STARTED.register(s->{server=s;store=new AviaryStore();packId=UUID.nameUUIDFromBytes(store.settings.packSha1().getBytes(StandardCharsets.UTF_8));System.out.println("Oak Aviary ready; enabled="+store.settings.enabled());});
        ServerLifecycleEvents.SERVER_STARTED.register(s->control=new AviaryControl(this));
        ServerTickEvents.END_SERVER_TICK.register(s->tick());
        ServerLifecycleEvents.SERVER_STOPPING.register(s->{if(control!=null)control.close();});
        net.fabricmc.fabric.api.event.player.UseBlockCallback.EVENT.register((actor,world,hand,hit)->{
            if(actor instanceof ServerPlayer p&&hand==net.minecraft.world.InteractionHand.MAIN_HAND&&store!=null&&store.settings.enabled()
                &&javaPlayer(p)&&!journeys.containsKey(p.getUUID())&&world.getBlockState(hit.getBlockPos()).is(net.minecraft.world.level.block.Blocks.BELL)) {
                var port=nearby(p);
                var pos=hit.getBlockPos();
                if(port!=null&&new net.minecraft.world.phys.Vec3(pos.getX()+.5,pos.getY()+.5,pos.getZ()+.5).distanceTo(Journey.position(port))<=6)menu(p);
            }
            return net.minecraft.world.InteractionResult.PASS;
        });
        ServerLifecycleEvents.SERVER_STOPPING.register(s->{queue.clear();for(var j:List.copyOf(journeys.values()))j.abort("Travel stopped. The server is restarting.");perches.close();if(store!=null)store.close();});
        ServerPlayConnectionEvents.JOIN.register((handler,sender,s)->{
            if(store!=null&&store.recoveries.containsKey(handler.player.getUUID()))protectedPlayers.add(handler.player.getUUID());
            var vehicle=handler.player.getRootVehicle();
            if(vehicle!=handler.player&&vehicle.entityTags().contains("oak_aviary_temporary")){handler.player.stopRiding();vehicle.discard();}
            restoreCamera(handler.player);sendPack(handler.player);perches.joined(handler.player);
        });
        ServerPlayConnectionEvents.DISCONNECT.register((handler,s)->{UUID id=handler.player.getUUID();Journey j=journeys.get(id);if(j!=null)j.abort("Travel interrupted.");queue.remove(id);groups.cancel(id);callTimes.remove(id);perchMenus.forget(id);loaded.remove(id);skips.remove(id);heldShift.remove(id);if(store!=null)store.forget(id);});
        ServerLivingEntityEvents.ALLOW_DAMAGE.register((entity,source,amount)->{
            UUID id=entity.getUUID();if(isTravelling(id))return false;
            if(amount>0){queue.remove(id);groups.cancel(id);Journey j=journeys.get(id);if(j!=null&&j.waiting())j.abort("Your bird left. Call again when you are safe.");}
            return true;
        });
        CommandRegistrationCallback.EVENT.register((dispatcher,access,environment)->dispatcher.register(Commands.literal("aviary")
            .executes(c->menu(c.getSource().getPlayerOrException()))
            .then(Commands.literal("pack").executes(c->{sendPack(c.getSource().getPlayerOrException());return 1;}))
            .then(Commands.literal("cancel").executes(c->cancel(c.getSource().getPlayerOrException())))
            .then(Commands.literal("friends").then(Commands.argument("port",StringArgumentType.word()).executes(c->groups.menu(c.getSource().getPlayerOrException(),StringArgumentType.getString(c,"port")))))
            .then(Commands.literal("companion").then(Commands.argument("port",StringArgumentType.word()).then(Commands.argument("player",StringArgumentType.word()).executes(c->groups.invite(c.getSource().getPlayerOrException(),StringArgumentType.getString(c,"port"),StringArgumentType.getString(c,"player"))))))
            .then(Commands.literal("accept").then(Commands.argument("invitation",StringArgumentType.word()).executes(c->groups.accept(c.getSource().getPlayerOrException(),StringArgumentType.getString(c,"invitation")))))
            .then(Commands.literal("decline").then(Commands.argument("invitation",StringArgumentType.word()).executes(c->groups.decline(c.getSource().getPlayerOrException(),StringArgumentType.getString(c,"invitation")))))
            .then(Commands.literal("packed").executes(c->perchMenus.packed(c.getSource().getPlayerOrException())))
            .then(Commands.literal("recover").then(Commands.argument("port",StringArgumentType.word()).executes(c->{var p=c.getSource().getPlayerOrException();return javaPlayer(p)&&loaded(p)&&perches.recover(p,StringArgumentType.getString(c,"port"))?1:0;})))
            .then(Commands.literal("board").executes(c->{var j=journeys.get(c.getSource().getPlayerOrException().getUUID());return j!=null&&j.board()?1:0;}))
            .then(Commands.literal("perch").then(Commands.argument("port",StringArgumentType.word()).executes(c->perchMenu(c.getSource().getPlayerOrException(),StringArgumentType.getString(c,"port")))))
            .then(Commands.literal("manage").then(Commands.argument("port",StringArgumentType.word()).then(Commands.argument("action",StringArgumentType.word())
                .executes(c->perchMenus.manage(c.getSource().getPlayerOrException(),StringArgumentType.getString(c,"port"),StringArgumentType.getString(c,"action"),""))
                .then(Commands.argument("value",StringArgumentType.word()).executes(c->perchMenus.manage(c.getSource().getPlayerOrException(),StringArgumentType.getString(c,"port"),StringArgumentType.getString(c,"action"),StringArgumentType.getString(c,"value")))))))
            .then(Commands.literal("page").then(Commands.argument("page",com.mojang.brigadier.arguments.IntegerArgumentType.integer(0,12)).executes(c->menu(c.getSource().getPlayerOrException(),com.mojang.brigadier.arguments.IntegerArgumentType.getInteger(c,"page")))))
            .then(Commands.literal("destination").then(Commands.argument("port",StringArgumentType.word()).executes(c->destinationMenu(c.getSource().getPlayerOrException(),StringArgumentType.getString(c,"port")))))
            .then(Commands.literal("favorite").then(Commands.argument("port",StringArgumentType.word()).executes(c->preference(c.getSource().getPlayerOrException(),"favorite",StringArgumentType.getString(c,"port")))))
            .then(Commands.literal("travel").then(Commands.argument("mode",StringArgumentType.word()).executes(c->preference(c.getSource().getPlayerOrException(),"travel",StringArgumentType.getString(c,"mode")))))
            .then(Commands.literal("camera").then(Commands.argument("mode",StringArgumentType.word()).executes(c->preference(c.getSource().getPlayerOrException(),"camera",StringArgumentType.getString(c,"mode")))))
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
    public static void shiftInput(UUID id,boolean pressed){if(!isTravelling(id))return;if(pressed)instance.heldShift.putIfAbsent(id,0);else instance.heldShift.remove(id);}
    public static void packResponse(UUID id,ServerboundResourcePackPacket packet) {
        Aviary a=instance;if(a==null||!packet.id().equals(a.packId))return;
        if(packet.action()==ServerboundResourcePackPacket.Action.SUCCESSFULLY_LOADED)a.loaded.add(id);
        else if(packet.action()!=ServerboundResourcePackPacket.Action.ACCEPTED&&packet.action()!=ServerboundResourcePackPacket.Action.DOWNLOADED){
            a.loaded.remove(id);requestSkip(id);
            if(a.server!=null)a.server.execute(()->{a.queue.remove(id);a.groups.cancel(id);var j=a.journeys.get(id);if(j!=null&&j.waiting())j.abort("Load the resource pack before calling your bird.");});
        }
    }
    boolean javaPlayer(ServerPlayer p) {
        if(!FabricLoader.getInstance().isModLoaded("floodgate"))return true;
        try {Class<?> api=Class.forName("org.geysermc.floodgate.api.FloodgateApi");Object value=api.getMethod("getInstance").invoke(null);return !(boolean)api.getMethod("isFloodgatePlayer",UUID.class).invoke(value,p.getUUID());}
        catch(ReflectiveOperationException e){return false;}
    }
    boolean loaded(ServerPlayer p){return loaded.contains(p.getUUID());}
    void protect(UUID id){protectedPlayers.add(id);}
    void unprotect(UUID id){protectedPlayers.remove(id);skips.remove(id);heldShift.remove(id);}
    int perchMenu(ServerPlayer p,String id){return store==null?0:perchMenus.open(p,id);}
    public static void dialogResponse(UUID id,ServerboundCustomClickActionPacket packet){
        Aviary a=instance;if(a==null||a.server==null||!packet.id().getNamespace().equals("oak_aviary"))return;
        a.server.execute(()->{var p=a.server.getPlayerList().getPlayer(id);if(p!=null&&a.store!=null)a.perchMenus.response(p,packet);});
    }
    private void sendPack(ServerPlayer p) {
        if(store==null||!store.settings.enabled()||!store.error.isEmpty()||!javaPlayer(p))return;
        p.connection.send(new ClientboundResourcePackPushPacket(packId,store.settings.packUrl(),store.settings.packSha1(),false,Optional.of(Component.literal("Birds, perches and travel effects. Optional."))));
    }
    boolean accessible(AviaryStore.Port port,ServerPlayer p){
        var perch=store.perches.get(port.id());
        return port.shared()||port.owner().equals(p.getUUID().toString())||p.permissions().hasPermission(Permissions.COMMANDS_GAMEMASTER)||(perch!=null&&perch.guests().contains(p.getUUID().toString()));
    }
    static boolean visible(AviaryStore.Port port,ServerPlayer p){
        Aviary a=instance;if(a==null||a.store==null||!a.accessible(port,p))return false;var perch=a.store.perches.get(port.id());
        if(perch==null)return true;
        if(!perch.active())return false;
        return !a.store.network.discoverPublic()||perch.hub()||!port.shared()||port.owner().equals(p.getUUID().toString())||perch.guests().contains(p.getUUID().toString())||p.permissions().hasPermission(Permissions.COMMANDS_GAMEMASTER)||a.store.preferences(p.getUUID()).discovered().contains(port.id());
    }
    void discover(ServerPlayer p,AviaryStore.Port port){
        if(!javaPlayer(p)||!accessible(port,p))return;var old=store.preferences(p.getUUID());if(old.discovered().contains(port.id()))return;
        var known=new HashSet<>(old.discovered());known.retainAll(store.ports.keySet());known.add(port.id());
        try{store.preferences(p.getUUID(),new AviaryStore.Preferences(old.favorites(),old.quick(),old.freeCamera(),known));}
        catch(Exception e){System.err.println("Aviary destination discovery could not be saved");}
    }
    AviaryStore.Port nearby(ServerPlayer p){return store.ports.values().stream().filter(q->accessible(q,p)&&(!store.perches.containsKey(q.id())||store.perches.get(q.id()).active())&&q.dimension().equals(p.level().dimension().identifier().toString())&&p.position().distanceTo(Journey.position(q))<6).min(Comparator.comparingDouble(q->p.position().distanceTo(Journey.position(q)))).orElse(null);}
    boolean busyPort(String id){return journeys.values().stream().anyMatch(j->j.uses(id));}
    ActionButton action(String label,String hint,String command){return new ActionButton(new CommonButtonData(Component.literal(label),Optional.of(Component.literal(hint)),150),command==null?Optional.empty():Optional.of(new StaticAction(new ClickEvent.RunCommand(command))));}
    int dialog(ServerPlayer p,String title,String body,List<ActionButton> buttons){
        var common=new CommonDialogData(Component.literal(title),Optional.empty(),true,false,DialogAction.CLOSE,body.isEmpty()?List.of():List.of(new PlainMessage(Component.literal(body),310)),List.of());
        p.openDialog(Holder.direct(new MultiActionDialog(common,buttons,Optional.of(action("Close","Return to the game",null)),2)));return 1;
    }
    int menu(ServerPlayer p){return menu(p,0);}
    int menu(ServerPlayer p,int page) {
        if(store==null)return 0;
        if(!javaPlayer(p)){tell(p,"Aviary requires Minecraft Java Edition.");return 0;}
        var active=journeys.get(p.getUUID());
        if(active!=null&&!active.waiting()&&!isTravelling(p.getUUID())){p.sendOverlayMessage(Component.literal("Your bird is leaving the perch."));return 1;}
        if(active!=null)return dialog(p,"Your bird",active.waiting()?"Use the saddle when your bird is ready.":"Your flight is in progress.",List.of(action(active.waiting()?"Cancel":"Skip",active.waiting()?"Dismiss your bird":"Finish the flight",active.waiting()?"/aviary cancel":"/aviary skip")));
        var waiting=queue.get(p.getUUID());
        if(waiting!=null){var destination=store.ports.get(waiting.destination());return dialog(p,"Waiting for a bird",destination==null?"Destination unavailable":destination.name()+" · Stay nearby",List.of(action("Cancel","Leave the queue","/aviary cancel")));}
        if(groups.pending(p))return groups.pendingMenu(p);
        var prefs=store.preferences(p.getUUID());var origin=nearby(p);
        if(origin!=null)discover(p,origin);
        var ports=store.ports.values().stream().filter(q->visible(q,p)&&(origin==null||!q.id().equals(origin.id())))
            .sorted(Comparator.<AviaryStore.Port,Boolean>comparing(q->!prefs.favorites().contains(q.id())).thenComparingDouble(q->p.position().distanceTo(Journey.position(q))).thenComparing(AviaryStore.Port::id)).toList();
        page=Math.clamp(page,0,Math.max(0,(ports.size()-1)/10));var buttons=new ArrayList<ActionButton>();
        for(var port:ports.subList(page*10,Math.min(ports.size(),page*10+10))) {
            long distance=Math.round(p.position().distanceTo(Journey.position(port)));
            buttons.add(action((prefs.favorites().contains(port.id())?"★ ":"")+port.name(),distance+" blocks"+(busyPort(port.id())?" · Busy":""),"/aviary destination "+port.id()));
        }
        if(page>0)buttons.add(action("Previous","Previous destinations","/aviary page "+(page-1)));
        if((page+1)*10<ports.size())buttons.add(action("Next","More destinations","/aviary page "+(page+1)));
        buttons.add(action(prefs.quick()?"Travel: Quick":"Travel: Full","Choose a shorter presentation for repeat trips","/aviary travel "+(prefs.quick()?"full":"quick")));
        buttons.add(action(prefs.freeCamera()?"Camera: Free":"Camera: Follow","Free uses your normal first-person or F5 camera","/aviary camera "+(prefs.freeCamera()?"follow":"free")));
        if(store.perches.entrySet().stream().anyMatch(e->!e.getValue().active()&&store.ports.get(e.getKey()).owner().equals(p.getUUID().toString())))buttons.add(action("Packed perches","Replace a lost packed perch","/aviary packed"));
        if(!loaded(p))buttons.add(action("Load resource pack","Models and sounds","/aviary pack"));
        return dialog(p,"Destinations",ports.isEmpty()?"Place a perch or visit a public one to discover destinations.":origin==null?(store.network.fieldPickup()?"Find open ground under the sky to call your bird.":"Stand near a perch to depart."):"From "+origin.name(),buttons);
    }
    int destinationMenu(ServerPlayer p,String id){
        if(store==null||!javaPlayer(p))return 0;var port=store.ports.get(id);if(port==null||!visible(port,p))return menu(p);
        var origin=nearby(p);boolean busy=busyPort(id)||(origin!=null&&busyPort(origin.id()));
        boolean ready=(origin!=null||store.network.fieldPickup())&&(origin==null||!origin.id().equals(id))&&store.settings.enabled()&&store.error.isEmpty()&&loaded.contains(p.getUUID());
        boolean waiting=busy||journeys.size()>=store.settings.maxFlights();
        var buttons=new ArrayList<ActionButton>();
        buttons.add(action(ready?(waiting?"Wait for a bird":"Call bird"):"Unavailable",ready?"Your bird will wait for you to board":"A destination and the resource pack are required",ready?"/aviary fly "+id:null));
        if(ready&&origin!=null&&store.settings.maxFlights()>=2)buttons.add(action("Fly with a friend","Invite a rider at this perch","/aviary friends "+id));
        buttons.add(action(store.preferences(p.getUUID()).favorites().contains(id)?"Unfavorite":"Favorite","Keep this destination at the top","/aviary favorite "+id));buttons.add(action("Back","All destinations","/aviary"));
        return dialog(p,port.name(),Math.round(p.position().distanceTo(Journey.position(port)))+" blocks · "+(waiting?"Waiting for a bird":!loaded.contains(p.getUUID())?"Resource pack required":origin==null?"Pickup in open ground":"From "+origin.name()),buttons);
    }
    int preference(ServerPlayer p,String kind,String value){
        if(store==null||!javaPlayer(p))return 0;var old=store.preferences(p.getUUID());var favorites=new HashSet<>(old.favorites());
        try {
            boolean quick=old.quick(),free=old.freeCamera();
            if(kind.equals("favorite")){var port=store.ports.get(value);if(port==null||!visible(port,p))return 0;if(!favorites.remove(value))favorites.add(value);favorites.retainAll(store.ports.keySet());}
            else if(kind.equals("travel")){if(!Set.of("quick","full").contains(value))throw new IllegalArgumentException();quick=value.equals("quick");}
            else {if(!Set.of("free","follow").contains(value))throw new IllegalArgumentException();free=value.equals("free");}
            store.preferences(p.getUUID(),new AviaryStore.Preferences(favorites,quick,free,old.discovered()));
            return kind.equals("favorite")?destinationMenu(p,value):menu(p);
        }catch(Exception e){tell(p,"Could not save travel preferences.");return 0;}
    }
    int addPort(ServerPlayer p,String id,boolean shared) {
        if(store==null||journeys.containsKey(p.getUUID()))return 0;
        if(!javaPlayer(p)){tell(p,"Aviary requires Minecraft Java Edition.");return 0;}
        if(!shared&&store.ports.values().stream().filter(q->q.owner().equals(p.getUUID().toString())).count()>=store.network.maxOwnedPerches()){tell(p,"You have reached your perch limit.");return 0;}
        if(store.ports.values().stream().anyMatch(q->q.dimension().equals(p.level().dimension().identifier().toString())&&p.position().distanceTo(Journey.position(q))<16)){tell(p,"Another aviport is too close. Leave at least 16 blocks between aviports.");return 0;}
        var port=new AviaryStore.Port(id,id.replace('_',' '),p.level().dimension().identifier().toString(),Math.floor(p.getX())+.5,p.getY(),Math.floor(p.getZ())+.5,p.getYRot(),p.getUUID().toString(),shared);
        try {AviaryStore.validate(port);if(store.ports.containsKey(id)||store.ports.size()>=128)throw new IllegalArgumentException("Port already exists or the port limit was reached.");String issue=Journey.portIssue(p.level(),port);if(!issue.isEmpty())throw new IllegalArgumentException(issue);store.ports.put(id,port);try{store.savePolicy();}catch(Exception e){store.ports.remove(id);throw e;}tell(p,"Aviport added: "+port.name());return 1;}
        catch(Exception e){tell(p,"Could not add aviport: "+e.getMessage());return 0;}
    }
    int editPort(ServerPlayer p,String id,String name,Boolean shared) {
        var old=store.ports.get(id);
        if(old==null||!(old.owner().equals(p.getUUID().toString())||p.permissions().hasPermission(Permissions.COMMANDS_GAMEMASTER))){tell(p,"You can only edit your own aviports.");return 0;}
        if(journeys.values().stream().anyMatch(j->j.uses(id))){tell(p,"This aviport has an active flight.");return 0;}
        var updated=new AviaryStore.Port(old.id(),name==null?old.name():name,old.dimension(),old.x(),old.y(),old.z(),old.yaw(),old.owner(),shared==null?old.shared():shared,old.departureYaw(),old.arrivalYaw());
        try {AviaryStore.validate(updated);store.ports.put(id,updated);try{store.savePolicy();}catch(Exception e){store.ports.put(id,old);throw e;}tell(p,"Aviport saved.");return 1;}
        catch(Exception e){tell(p,"Could not save aviport.");return 0;}
    }
    int removePort(ServerPlayer p,String id) {
        var owned=store.ports.get(id);
        if(owned==null||!(owned.owner().equals(p.getUUID().toString())||p.permissions().hasPermission(Permissions.COMMANDS_GAMEMASTER))){tell(p,"You can only remove your own aviports.");return 0;}
        if(journeys.values().stream().anyMatch(j->j.uses(id))){tell(p,"This aviport has an active flight.");return 0;}
        if(store.perches.containsKey(id)){tell(p,"Use the perch's Move action to pack it safely.");return 0;}
        var previous=store.ports.remove(id);if(previous==null)return 0;
        try {store.savePolicy();tell(p,"Aviport removed.");return 1;}catch(Exception e){store.ports.put(id,previous);tell(p,"Could not save aviports.");return 0;}
    }
    int fly(ServerPlayer p,String id) {
        Long last=callTimes.get(p.getUUID());if(last!=null&&tick-last<40){tell(p,"Wait a moment before calling again.");return 0;}
        callTimes.put(p.getUUID(),tick);return start(p,id,true);
    }
    private int start(ServerPlayer p,String id,boolean allowQueue) {
        try {
            if(store==null||!store.settings.enabled()||!store.error.isEmpty())throw new IllegalStateException("Aviary is unavailable.");
            if(!javaPlayer(p))throw new IllegalStateException("Aviary requires Minecraft Java Edition.");
            if(!loaded.contains(p.getUUID()))throw new IllegalStateException("Accept the Aviary resource pack first. Use /aviary pack to try again.");
            if(journeys.containsKey(p.getUUID())||store.recoveries.containsKey(p.getUUID()))throw new IllegalStateException("Your previous flight is still being completed.");
            if(p.isPassenger()||p.isSleeping()||p.isSpectator()||!p.isAlive()||p.level()!=server.overworld())throw new IllegalStateException("Stand on open ground in the Overworld to depart.");
            var destination=store.ports.get(id);
            if(destination==null||!visible(destination,p))throw new IllegalStateException("Aviport unavailable.");
            var origin=nearby(p);
            if(origin==null){
                if(!store.network.fieldPickup())throw new IllegalStateException("Stand near a perch to depart.");
                origin=Journey.fieldOrigin(p);
                if(origin==null)throw new IllegalStateException("Find open ground with room for your bird and sky above.");
            }
            if(origin.id().equals(id))throw new IllegalStateException("Choose another aviport.");
            if(journeys.size()>=store.settings.maxFlights()||busyPort(origin.id())||busyPort(id)){
                if(!allowQueue)return 0;
                if(queue.size()>=16&&!queue.containsKey(p.getUUID()))throw new IllegalStateException("The waiting list is full. Try again shortly.");
                queue.put(p.getUUID(),new Call(id,p.position(),tick+1200));menu(p);return 1;
            }
            groups.cancel(p.getUUID());queue.remove(p.getUUID());journeys.put(p.getUUID(),new Journey(this,p,origin,destination));
            tell(p,"Your bird is on its way. Use the saddle to board.");return 1;
        }catch(Exception e){tell(p,e.getMessage()==null?"Could not start flight.":e.getMessage());return 0;}
    }
    private int cancel(ServerPlayer p){
        if(groups.pending(p)){groups.cancel(p.getUUID());return 1;}
        if(queue.remove(p.getUUID())!=null){tell(p,"Call cancelled.");return 1;}
        var journey=journeys.get(p.getUUID());if(journey!=null&&journey.waiting()){journey.abort("Call cancelled.");return 1;}return 0;
    }
    static void restoreCamera(ServerPlayer p) {
        p.connection.send(new ClientboundSetCameraPacket(p));
        boolean changed=false;
        for(var effect:List.copyOf(p.getPostEffects()))if(effect.getNamespace().equals("oak_aviary"))changed|=p.removePostEffect(effect);
        if(changed)p.sendPostEffects();
    }
    void released(UUID id){journeys.remove(id);protectedPlayers.remove(id);skips.remove(id);heldShift.remove(id);}
    private void tick() {
        if(store==null)return;
        ++tick;
        perches.tick();
        if(tick%20==0)groups.tick();
        heldShift.replaceAll((id,ticks)->{if(ticks==7)skips.add(id);return Math.min(8,ticks+1);});
        planningDeadline=System.nanoTime()+2_000_000L;
        for(var j:List.copyOf(journeys.values()))try {if(skips.remove(j.player.getUUID()))j.skip=true;j.tick();}catch(Exception e){System.err.println("Oak Aviary flight failed: "+e);j.abort("Flight interrupted. Returning to a safe aviport.");}
        if(tick%20==0)for(var p:server.getPlayerList().getPlayers()) {
            UUID id=p.getUUID();var recovery=store.recoveries.get(id);
            if(store.settings.enabled()&&javaPlayer(p)&&!journeys.containsKey(id)){
                var nearest=nearby(p);if(nearest!=null)discover(p,nearest);
            }
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
        if(tick%20==0)for(var entry:List.copyOf(queue.entrySet())){
            var p=server.getPlayerList().getPlayer(entry.getKey());var call=entry.getValue();
            if(p==null){queue.remove(entry.getKey());continue;}
            if(tick>=call.expires()||p.level()!=server.overworld()||p.position().distanceTo(call.position())>6||!p.isAlive()){
                queue.remove(entry.getKey());tell(p,"Call cancelled. Use your whistle when you are ready.");continue;
            }
            if(journeys.size()>=store.settings.maxFlights()||busyPort(call.destination()))continue;
            var origin=nearby(p);if(origin!=null&&busyPort(origin.id()))continue;
            queue.remove(entry.getKey());start(p,call.destination(),false);
        }
    }
    int waitingCalls(){return queue.size();}
    boolean queued(UUID player){return queue.containsKey(player);}
}
