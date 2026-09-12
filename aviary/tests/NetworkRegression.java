package me.oak.aviary;

import com.google.gson.*;
import java.nio.file.*;
import java.util.*;
import java.util.concurrent.atomic.AtomicReference;
import net.minecraft.nbt.CompoundTag;
import net.minecraft.network.Connection;
import net.minecraft.network.protocol.Packet;
import net.minecraft.network.protocol.PacketFlow;
import net.minecraft.network.protocol.common.*;
import net.minecraft.server.dialog.MultiActionDialog;
import net.minecraft.server.dialog.action.CustomAll;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.server.network.*;
import net.minecraft.server.permissions.Permissions;

/** Isolated migration, access and native-form regressions; never runs in the live mod. */
final class NetworkRegression {
    static void run(Aviary app,ServerPlayer player)throws Exception{
        if(!"isolated".equals(System.getProperty("oak.aviary.smoke")))throw new IllegalStateException("Isolated server required");
        if(!app.journeys.isEmpty())throw new IllegalStateException("Run network checks before flights");
        if(player.permissions().hasPermission(Permissions.COMMANDS_GAMEMASTER))throw new AssertionError("Access checks require an ordinary player");
        String property=System.getProperty("oak.aviary.state");var original=app.store;var connection=player.connection;
        Path root=Files.createTempDirectory("oak-aviary-network-");AviaryStore store=null;
        try{
            System.setProperty("oak.aviary.state",root.toString());Files.createDirectories(root.resolve("players"));
            var legacy=new AviaryStore.Port("legacy","Legacy home","minecraft:overworld",player.getX(),player.getY(),player.getZ(),0,UUID.randomUUID().toString(),true);
            var json=new JsonObject();json.addProperty("revision",7);json.add("settings",new Gson().toJsonTree(new AviaryStore.Settings(false,"","",500,2)));json.add("ports",new Gson().toJsonTree(List.of(legacy)));
            Files.writeString(root.resolve("settings.json"),json.toString());Files.writeString(root.resolve("players").resolve(player.getUUID()+".json"),"{\"favorites\":[\"legacy\"],\"quick\":true,\"freeCamera\":true}");
            store=new AviaryStore();app.store=store;
            require(store.error.isEmpty()&&store.ports.size()==1&&store.revision==7,"Legacy state failed to load");
            require(store.network.fieldPickup()&&store.network.discoverPublic()&&store.network.maxOwnedPerches()==4&&store.perches.isEmpty(),"Legacy network defaults changed");
            require(Aviary.visible(legacy,player),"Legacy public destination was hidden");
            var preferences=store.preferences(player.getUUID());require(preferences.favorites().contains("legacy")&&preferences.quick()&&preferences.freeCamera()&&preferences.discovered().isEmpty(),"Legacy preferences lost");
            var port=new AviaryStore.Port("network-perch","New perch","minecraft:overworld",player.getX(),player.getY(),player.getZ(),0,legacy.owner(),true);
            var perch=new AviaryStore.PerchData((int)Math.floor(player.getX()),(int)Math.floor(player.getY())-1,(int)Math.floor(player.getZ()),"green",true);
            store.ports.put(port.id(),port);store.perches.put(port.id(),perch);store.savePolicy();
            require(!Aviary.visible(port,player),"Undiscovered public perch leaked into destinations");
            app.discover(player,port);require(Aviary.visible(port,player),"Visited public perch not discovered");
            require(store.preferences(player.getUUID()).favorites().contains("legacy"),"Discovery erased favorites");
            port=copy(port,legacy.owner(),false);store.ports.put(port.id(),port);
            require(!app.accessible(port,player)&&!Aviary.visible(port,player),"Discovery bypassed private access");
            perch=copy(perch,Set.of(player.getUUID().toString()),true);store.perches.put(port.id(),perch);
            require(app.accessible(port,player)&&Aviary.visible(port,player),"Private invitation did not grant access");
            perch=copy(perch,Set.of(),true);store.perches.put(port.id(),perch);require(!Aviary.visible(port,player),"Revoked guest retained private travel access");
            port=copy(port,player.getUUID().toString(),false);store.ports.put(port.id(),port);require(Aviary.visible(port,player),"Owner could not access private perch");
            store.perches.put(port.id(),copy(perch,Set.of(),false));require(!Aviary.visible(port,player),"Packed perch remained a travel destination");
            store.savePolicy();try(var reloaded=new AviaryStore()){
                require(reloaded.error.isEmpty()&&!reloaded.perches.get(port.id()).active(),"Packed address did not survive reload");
                require(reloaded.preferences(player.getUUID()).favorites().contains("legacy")&&reloaded.preferences(player.getUUID()).discovered().contains(port.id()),"Preferences did not survive reload");
            }
            store.perches.put(port.id(),perch);store.savePolicy();
            var control=new AviaryControl(app);var request=new JsonObject();request.addProperty("action","policy");request.addProperty("revision",store.revision);request.addProperty("fieldPickup",false);request.addProperty("discoverPublic",false);request.addProperty("maxOwnedPerches",8);request.addProperty("maxFlights",3);request.addProperty("shortcutDistance",400);
            control.execute(request);require(!store.network.fieldPickup()&&store.settings.maxFlights()==3&&store.settings.shortcutDistance()==400,"Policy did not apply");
            rejected(()->control.execute(request),"Stale policy revision accepted");
            long revision=store.revision;request.addProperty("revision",revision);request.addProperty("maxOwnedPerches",17);rejected(()->control.execute(request),"Invalid policy limit accepted");require(store.revision==revision&&store.network.maxOwnedPerches()==8,"Rejected policy changed state");
            var edit=new JsonObject();edit.addProperty("action","edit");edit.addProperty("revision",store.revision);edit.addProperty("port",port.id());edit.addProperty("name",port.name());edit.addProperty("shared",false);edit.add("departureYaw",JsonNull.INSTANCE);edit.add("arrivalYaw",JsonNull.INSTANCE);edit.addProperty("color","ultraviolet");
            rejected(()->control.execute(edit),"Invalid cloth color accepted");require(store.revision==revision&&store.perches.get(port.id()).color().equals("green"),"Rejected appearance changed state");
            nativeForms(app,player,port.id());control.close();
            System.out.println("AVIARY_SMOKE network migration, access, policy and native forms passed");
        }finally{
            player.connection=connection;app.store=original;if(store!=null)store.close();if(property==null)System.clearProperty("oak.aviary.state");else System.setProperty("oak.aviary.state",property);
        }
    }
    private static void nativeForms(Aviary app,ServerPlayer player,String id)throws Exception{
        var captured=new AtomicReference<ClientboundShowDialogPacket>();
        var transport=new Connection(PacketFlow.SERVERBOUND){
            public boolean isConnected(){return true;}
            public boolean isMemoryConnection(){return true;}
            public void send(Packet<?> packet){capture(packet);}
            public void send(Packet<?> packet,io.netty.channel.ChannelFutureListener listener){capture(packet);}
            public void send(Packet<?> packet,io.netty.channel.ChannelFutureListener listener,boolean flush){capture(packet);}
            private void capture(Packet<?> packet){if(packet instanceof ClientboundShowDialogPacket dialog)captured.set(dialog);}
        };
        player.connection=new ServerGamePacketListenerImpl(app.server,transport,player,CommonListenerCookie.createInitial(player.getGameProfile(),false));
        var menus=new PerchMenus(app);require(menus.manage(player,id,"rename","")==1,"Rename form did not open");
        var bad=form(captured,"Nope","Nope");((CompoundTag)bad.payload().orElseThrow()).putString("nonce",UUID.randomUUID().toString());long revision=app.store.revision;
        menus.response(player,bad);require(app.store.revision==revision,"Wrong form nonce wrote state");
        menus.manage(player,id,"rename","");var stale=form(captured,"Stale","Stale");app.store.savePolicy();revision=app.store.revision;
        menus.response(player,stale);require(app.store.revision==revision&&!app.store.ports.get(id).name().equals("Stale"),"Stale form wrote state");
        menus.manage(player,id,"rename","");var valid=form(captured,"Cliff home","Juniper");menus.response(player,valid);
        require(app.store.ports.get(id).name().equals("Cliff home")&&app.store.perches.get(id).birdName().equals("Juniper"),"Native form did not save names");
        revision=app.store.revision;menus.response(player,valid);require(app.store.revision==revision,"Replayed form wrote state twice");
        var owned=app.store.ports.get(id);var metadata=app.store.perches.get(id);int free=player.getInventory().getFreeSlot();
        require(free>=0,"Removal test needs a free inventory slot");var before=player.getInventory().getItem(free).copy();
        try{
            app.store.ports.put(id,copy(owned,UUID.randomUUID().toString(),false));
            require(menus.manage(player,id,"remove","")==0&&app.store.ports.containsKey(id),"Non-owner removed perch");app.store.ports.put(id,owned);
            menus.manage(player,id,"remove","");var staleRemove=form(captured,"","");app.store.savePolicy();revision=app.store.revision;
            menus.response(player,staleRemove);require(app.store.ports.containsKey(id)&&app.store.revision==revision,"Stale removal deleted perch");
            // A packed item is converted once, preserving the existing inventory slot.
            app.store.perches.put(id,copy(metadata,Set.of(),false));app.store.savePolicy();player.getInventory().setItem(free,Perches.item("perch",id));
            menus.manage(player,id,"remove","");var remove=form(captured,"","");menus.response(player,remove);
            require(!app.store.ports.containsKey(id)&&!app.store.perches.containsKey(id),"Confirmed removal did not retire address");
            var returned=player.getInventory().getItem(free);require(Perches.kind(returned).equals("perch")&&!returned.getOrDefault(net.minecraft.core.component.DataComponents.CUSTOM_DATA,net.minecraft.world.item.component.CustomData.EMPTY).copyTag().contains("port"),"Packed item was not converted to a reusable kit");
            revision=app.store.revision;menus.response(player,remove);require(app.store.revision==revision&&player.getInventory().getItem(free).getCount()==1,"Removal replay duplicated a kit");
        }finally{player.getInventory().setItem(free,before);}
    }
    private static ServerboundCustomClickActionPacket form(AtomicReference<ClientboundShowDialogPacket> captured,String name,String bird){
        var packet=captured.getAndSet(null);require(packet!=null,"No native dialog packet");var dialog=(MultiActionDialog)packet.dialog().value();var action=(CustomAll)dialog.actions().getFirst().action().orElseThrow();
        var payload=action.additions().orElseThrow().copy();payload.putString("name",name);payload.putString("bird",bird);return new ServerboundCustomClickActionPacket(action.id(),Optional.of(payload));
    }
    private static AviaryStore.Port copy(AviaryStore.Port p,String owner,boolean shared){return new AviaryStore.Port(p.id(),p.name(),p.dimension(),p.x(),p.y(),p.z(),p.yaw(),owner,shared,p.departureYaw(),p.arrivalYaw());}
    private static AviaryStore.PerchData copy(AviaryStore.PerchData p,Set<String> guests,boolean active){return new AviaryStore.PerchData(p.x(),p.y(),p.z(),p.color(),p.style(),p.birdName(),guests,p.hub(),active);}
    private static void rejected(Runnable action,String message){try{action.run();throw new AssertionError(message);}catch(IllegalArgumentException expected){}}
    private static void require(boolean value,String message){if(!value)throw new AssertionError(message);}
}
