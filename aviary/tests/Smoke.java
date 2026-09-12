package me.oak.aviary;

import com.mojang.authlib.GameProfile;
import net.fabricmc.api.ModInitializer;
import net.fabricmc.fabric.api.event.lifecycle.v1.ServerTickEvents;
import net.minecraft.core.BlockPos;
import net.minecraft.network.Connection;
import net.minecraft.network.protocol.*;
import net.minecraft.network.protocol.common.ServerboundResourcePackPacket;
import net.minecraft.server.level.*;
import net.minecraft.server.network.*;
import net.minecraft.world.level.block.Blocks;
import net.minecraft.world.item.*;
import java.util.*;
import java.nio.charset.StandardCharsets;

/** Compiled only with --smoke, then run in a disposable private network namespace. */
public final class Smoke implements ModInitializer {
    private ServerPlayer player;
    private ServerPlayer companion;
    private int tick,stage;
    private final Map<String,Integer> packets=new HashMap<>();
    private int baseline;
    private final Set<Integer> visualPassengers=new HashSet<>();
    private int skinPackets,equipmentPackets,privateSeats;
    private boolean continuousFlight,movingFade;
    private net.minecraft.world.phys.Vec3 lastFade;
    private int waitingTicks;
    private int interruptTicks;
    private boolean staggered;
    public void onInitialize(){
        if(!System.getProperty("oak.aviary.smoke","").equals("isolated"))throw new IllegalStateException("Smoke artifact must never run in production");
        ServerTickEvents.END_SERVER_TICK.register(server->{
            try {
                if(++tick>4600)throw new AssertionError("Flight timeout, stage="+stage);
                Aviary app=Aviary.instance;if(app.store==null)return;
                if(player==null) {
                    var level=server.overworld();
                    RenderRegression.check(level);
                    PerchRegression.check(level);BirdTraitsRegression.check();
                    var profile=new GameProfile(UUID.randomUUID(),"AviaryTest");
                    player=new ServerPlayer(server,level,profile,ClientInformation.createDefault()) {public boolean hasDisconnected(){return false;}};
                    Connection connection=new Connection(PacketFlow.SERVERBOUND) {
                        public boolean isConnected(){return true;}
                        public boolean isMemoryConnection(){return true;}
                        public void send(Packet<?> packet){record(packet);}
                        public void send(Packet<?> packet,io.netty.channel.ChannelFutureListener listener){record(packet);}
                        public void send(Packet<?> packet,io.netty.channel.ChannelFutureListener listener,boolean flush){record(packet);}
                        private void record(Packet<?> packet){
                            packets.merge(packet.getClass().getSimpleName(),1,Integer::sum);
                            if(packet instanceof net.minecraft.network.protocol.game.ClientboundAddEntityPacket add&&add.getType()==net.minecraft.world.entity.EntityTypes.MANNEQUIN)visualPassengers.add(add.getId());
                            if(packet instanceof net.minecraft.network.protocol.game.ClientboundSetEntityDataPacket data&&visualPassengers.contains(data.id()))for(var value:data.packedItems())if(value.value() instanceof net.minecraft.world.item.component.ResolvableProfile skin&&skin.partialProfile().id().equals(profile.id()))skinPackets++;
                            if(packet instanceof net.minecraft.network.protocol.game.ClientboundSetEquipmentPacket gear&&visualPassengers.contains(gear.getEntity()))equipmentPackets++;
                            if(packet instanceof net.minecraft.network.protocol.game.ClientboundSetPassengersPacket seats&&java.util.Arrays.stream(seats.getPassengers()).anyMatch(visualPassengers::contains)) {
                                if(seats.getPassengers().length!=2||seats.getPassengers()[0]!=player.getId())throw new AssertionError("Wrong private passenger order");privateSeats++;
                            }
                            if(packet instanceof net.minecraft.network.protocol.game.ClientboundRemoveEntitiesPacket remove)for(int id:remove.entityIds())visualPassengers.remove(id);
                        }
                    };
                    player.connection=new ServerGamePacketListenerImpl(server,connection,player,CommonListenerCookie.createInitial(profile,false));
                    for(int center:new int[]{0,48,640}) {
                        for(int x=center-8;x<=center+8;x++)for(int z=-8;z<=8;z++)level.setBlock(new BlockPos(x,79,z),Blocks.STONE.defaultBlockState(),3);
                        var p=new AviaryStore.Port("p"+center,"Port "+center,"minecraft:overworld",center+.5,80,.5,0,profile.id().toString(),true);app.store.ports.put(p.id(),p);
                    }
                    // Ensure the short route is loaded; long routes must not load the intervening terrain.
                    for(int x=-1;x<=4;x++)for(int z=-1;z<=1;z++)level.getChunkSource().getChunk(x,z,true);
                    app.store.settings=new AviaryStore.Settings(true,"https://example.invalid/pack.zip","0".repeat(40),500,2);
                    var control=new AviaryControl(app);
                    var edit=new com.google.gson.JsonObject();
                    edit.addProperty("action","edit");edit.addProperty("revision",app.store.revision);edit.addProperty("port","p0");edit.addProperty("name","Port 0");edit.addProperty("shared",true);
                    edit.addProperty("departureYaw",-90);edit.add("arrivalYaw",com.google.gson.JsonNull.INSTANCE);
                    control.execute(edit);
                    try{control.execute(edit);throw new AssertionError("Stale aviport edit accepted");}catch(IllegalArgumentException expected){}
                    app.store.preferences(profile.id(),new AviaryStore.Preferences(Set.of("p48"),false,false));
                    app.store.forget(profile.id());
                    if(!app.store.preferences(profile.id()).favorites().contains("p48"))throw new AssertionError("Favorite did not persist");
                    for(double distance:new double[]{40,500}) {
                        var scene=FlightScene.route(new net.minecraft.world.phys.Vec3(0,80,0),new net.minecraft.world.phys.Vec3(distance,100,0),5);
                        var previousVelocity=net.minecraft.world.phys.Vec3.ZERO;
                        for(int t=1;t<=scene.ticks;t++) {
                            var velocity=scene.at(t).subtract(scene.at(t-1));
                            if(velocity.subtract(previousVelocity).length()>.067)throw new AssertionError("Unbounded flight acceleration");
                            previousVelocity=velocity;
                        }
                        if(previousVelocity.length()>.002)throw new AssertionError("Flight snapped to rest");
                    }
                    System.out.println("AVIARY_SMOKE controller, revision and preferences passed");
                    // packId was derived from the initially empty configuration.
                    Aviary.packResponse(profile.id(),new ServerboundResourcePackPacket(UUID.nameUUIDFromBytes("".getBytes(StandardCharsets.UTF_8)),ServerboundResourcePackPacket.Action.SUCCESSFULLY_LOADED));
                    player.setPos(.5,80,.5);level.addNewPlayer(player);player.getInventory().setItem(0,new ItemStack(Items.DIAMOND,7));
                    NetworkRegression.run(app,player);
                    PerchRegression.lifecycle(app,player);
                    app.journeys.put(player.getUUID(),new Journey(app,player,app.store.ports.get("p0"),app.store.ports.get("p48")));
                    if(Aviary.isTravelling(player.getUUID())||app.store.recoveries.containsKey(player.getUUID()))throw new AssertionError("Waiting call changed protection/recovery state");
                    var beforeCancel=player.position();app.journeys.get(player.getUUID()).abort("Test cancelled call");
                    if(!player.position().equals(beforeCancel)||app.journeys.containsKey(player.getUUID()))throw new AssertionError("Unboarded cancellation moved the player");
                    baseline=count(level);
                    if(app.menu(player)!=1||app.fly(player,"p48")!=1)throw new AssertionError("Could not begin short flight");
                    stage=1;System.out.println("AVIARY_SMOKE short flight started");return;
                }
                player.connection.chunkSender.sendNextChunks(player);
                player.connection.chunkSender.onChunkBatchReceivedByClient(64);
                Journey active=app.journeys.get(player.getUUID());
                if(active!=null) {
                    var phaseField=Journey.class.getDeclaredField("phase");phaseField.setAccessible(true);
                    String phase=(String)phaseField.get(active);
                    if(phase.equals("wait")) {
                        if(player.isPassenger()||Aviary.isTravelling(player.getUUID())||app.store.recoveries.containsKey(player.getUUID()))throw new AssertionError("Waiting bird automatically boarded or protected its player");
                        if(++waitingTicks>=12){if(!active.board())throw new AssertionError("Voluntary boarding rejected");waitingTicks=0;}
                    }
                    if(stage==1&&phase.equals("flight"))continuousFlight=true;
                    if(stage==2&&phase.equals("fade-out")) {
                        var position=player.position();
                        if(lastFade!=null&&position.distanceTo(lastFade)>.03)movingFade=true;
                        lastFade=position;
                    }
                }
                if(companion!=null) {
                    companion.connection.chunkSender.sendNextChunks(companion);companion.connection.chunkSender.onChunkBatchReceivedByClient(64);
                    Journey second=app.journeys.get(companion.getUUID());
                    if(second!=null) {
                        var phaseField=Journey.class.getDeclaredField("phase");phaseField.setAccessible(true);String phase=(String)phaseField.get(second);
                        if(phase.equals("prepare")&&active!=null&&!active.hasClearedOrigin())staggered=true;
                        if(phase.equals("wait")&&!second.board())throw new AssertionError("Companion boarding rejected");
                    }
                }
                if(!visualPassengers.isEmpty()) {
                    if(player.getVehicle()==null||player.getVehicle().getPassengers().size()!=1)throw new AssertionError("Cosmetic passenger changed gameplay riders");
                    for(var e:player.level().getAllEntities())if(e instanceof net.minecraft.world.entity.decoration.ArmorStand anchor&&anchor.entityTags().contains("oak_aviary_temporary")&&!anchor.isVehicle()) {
                        var target=player.getVehicle().position().add(0,2.0,0);
                        var aim=target.subtract(anchor.getEyePosition()).normalize();
                        var view=net.minecraft.world.phys.Vec3.directionFromRotation(anchor.getViewXRot(1),anchor.getViewYRot(1));
                        if(aim.dot(view)<.99)throw new AssertionError("Camera framing lost the rider");
                        if(anchor.getAttributeValue(net.minecraft.world.entity.ai.attributes.Attributes.CAMERA_DISTANCE)<13)throw new AssertionError("Front F5 view would face away from the rider");
                        if(anchor.getEyePosition().add(view.scale(14)).y<player.getVehicle().getY()+.5)throw new AssertionError("Front F5 camera clips below the landing deck");
                    }
                }
                if(!app.journeys.containsKey(player.getUUID())&&!app.store.recoveries.containsKey(player.getUUID())&&(companion==null||!app.journeys.containsKey(companion.getUUID()))) {
                    if(player.getInventory().getItem(0).getCount()!=7||!player.getInventory().getItem(0).is(Items.DIAMOND))throw new AssertionError("Inventory changed");
                    if(player.isPassenger()||!visualPassengers.isEmpty()||!player.getPostEffects().isEmpty()||Aviary.isTravelling(player.getUUID())||count(player.level())!=baseline)throw new AssertionError("Flight state leaked");
                    if(stage==1){if(!continuousFlight)throw new AssertionError("Short flight used a cut instead of its continuous route");if(Math.abs(player.getX()-48.5)>3)throw new AssertionError("Short flight did not arrive: "+player.position());if(app.fly(player,"p640")!=1)throw new AssertionError("Long flight rejected");stage=2;System.out.println("AVIARY_SMOKE long flight started");}
                    else if(stage==2){if(!movingFade)throw new AssertionError("Long flight froze before its cut");if(Math.abs(player.getX()-640.5)>3)throw new AssertionError("Long flight did not arrive");if(app.fly(player,"p0")!=1)throw new AssertionError("Return flight rejected");stage=3;}
                    else if(stage==3){if(Math.abs(player.getX()-640.5)>3)throw new AssertionError("Abort did not restore origin");if(skinPackets<3||equipmentPackets<3||privateSeats<3)throw new AssertionError("Passenger appearance missing");if(packets.getOrDefault("ClientboundSetCameraPacket",0)<4||packets.getOrDefault("ClientboundPostEffectsPacket",0)<10)throw new AssertionError("Camera/effect packets missing: "+packets);app.store.preferences(player.getUUID(),new AviaryStore.Preferences(Set.of("p0"),true,true));if(app.fly(player,"p0")!=1)throw new AssertionError("Quick free-camera flight rejected");stage=4;}
                    else if(stage==4){
                        if(Math.abs(player.getX()-.5)>3)throw new AssertionError("Quick free-camera flight did not arrive");
                        var level=player.level();for(int x=94;x<=96;x++)level.setBlock(new BlockPos(x,79,0),Blocks.STONE.defaultBlockState(),3);
                        var perch=new AviaryStore.Port("compact","Compact","minecraft:overworld",96.5,80.75,.5,0,player.getUUID().toString(),true);
                        app.store.ports.put(perch.id(),perch);app.store.perches.put(perch.id(),new AviaryStore.PerchData(96,79,0,"green",true));
                        if(!Journey.placementIssue(level,perch).isEmpty())throw new AssertionError("Compact supported perch rejected: "+Journey.placementIssue(level,perch));
                        if(app.fly(player,perch.id())!=1)throw new AssertionError("Compact perch flight rejected");stage=5;System.out.println("AVIARY_SMOKE compact perch started");
                    } else if(stage==5) {
                        if(Math.abs(player.getX()-96.5)>3)throw new AssertionError("Compact perch did not arrive");
                        var profile=new GameProfile(UUID.randomUUID(),"AviaryCompanion");
                        companion=new ServerPlayer(server,player.level(),profile,ClientInformation.createDefault()){public boolean hasDisconnected(){return false;}};
                        var connection=new Connection(PacketFlow.SERVERBOUND){public boolean isConnected(){return true;}public boolean isMemoryConnection(){return true;}public void send(Packet<?> packet){}public void send(Packet<?> packet,io.netty.channel.ChannelFutureListener listener){}public void send(Packet<?> packet,io.netty.channel.ChannelFutureListener listener,boolean flush){}};
                        companion.connection=new ServerGamePacketListenerImpl(server,connection,companion,CommonListenerCookie.createInitial(profile,false));companion.setPos(player.position());player.level().addNewPlayer(companion);
                        var origin=app.store.ports.get("compact");var destination=app.store.ports.get("p0");
                        app.journeys.put(player.getUUID(),new Journey(app,player,origin,destination,"group-test",true));
                        app.journeys.put(companion.getUUID(),new Journey(app,companion,origin,destination,"group-test",true));
                        stage=6;System.out.println("AVIARY_SMOKE companion trip started");
                    } else if(stage==6) {
                        if(!staggered||Math.abs(player.getX()-.5)>3||Math.abs(companion.getX()-.5)>3||companion.isPassenger()||Aviary.isTravelling(companion.getUUID())||app.store.recoveries.containsKey(companion.getUUID()))throw new AssertionError("Companion trip did not finish cleanly");
                        player.setPos(200.5,80,.5);player.setOnGround(true);var level=player.level();for(int x=195;x<=206;x++)for(int z=-6;z<=6;z++)level.setBlock(new BlockPos(x,79,z),Blocks.STONE.defaultBlockState(),3);
                        var field=Journey.fieldOrigin(player);if(field==null||field.id().equals("p0")||app.store.ports.containsKey(field.id()))throw new AssertionError("Field pickup registered a permanent destination");
                        app.journeys.put(player.getUUID(),new Journey(app,player,field,app.store.ports.get("p0")));companion=null;stage=7;System.out.println("AVIARY_SMOKE field pickup started");
                    } else if(stage==7){if(Math.abs(player.getX()-.5)>3)throw new AssertionError("Field pickup did not arrive");System.out.println("AVIARY_SMOKE PASS "+packets);server.halt(false);stage=8;}
                } else if(stage==3&&++interruptTicks>=45){app.journeys.get(player.getUUID()).abort("Test interruption");}
                if(stage==4&&!visualPassengers.isEmpty())throw new AssertionError("Free camera received a duplicate passenger");
            }catch(Throwable e){e.printStackTrace();System.out.println("AVIARY_SMOKE FAIL");server.halt(false);}
        });
    }
    private static int count(ServerLevel level){int n=0;for(var e:level.getAllEntities())if(e.entityTags().contains("oak_aviary_temporary")&&!e.entityTags().contains("oak_aviary_perch"))n++;return n;}
}
