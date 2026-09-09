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
    private int tick,stage;
    private final Map<String,Integer> packets=new HashMap<>();
    private int baseline;
    private final Set<Integer> visualPassengers=new HashSet<>();
    private int skinPackets,equipmentPackets,privateSeats;
    public void onInitialize(){
        if(!System.getProperty("oak.aviary.smoke","").equals("isolated"))throw new IllegalStateException("Smoke artifact must never run in production");
        ServerTickEvents.END_SERVER_TICK.register(server->{
            try {
                if(++tick>2000)throw new AssertionError("Flight timeout, stage="+stage);
                Aviary app=Aviary.instance;if(app.store==null)return;
                if(player==null) {
                    var level=server.overworld();
                    RenderRegression.check(level);
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
                    // packId was derived from the initially empty configuration.
                    Aviary.packResponse(profile.id(),new ServerboundResourcePackPacket(UUID.nameUUIDFromBytes("".getBytes(StandardCharsets.UTF_8)),ServerboundResourcePackPacket.Action.SUCCESSFULLY_LOADED));
                    player.setPos(.5,80,.5);level.addNewPlayer(player);player.getInventory().setItem(0,new ItemStack(Items.DIAMOND,7));
                    baseline=count(level);
                    if(app.menu(player)!=1||app.fly(player,"p48")!=1)throw new AssertionError("Could not begin short flight");
                    stage=1;System.out.println("AVIARY_SMOKE short flight started");return;
                }
                player.connection.chunkSender.sendNextChunks(player);
                player.connection.chunkSender.onChunkBatchReceivedByClient(64);
                if(!visualPassengers.isEmpty()) {
                    if(player.getVehicle()==null||player.getVehicle().getPassengers().size()!=1)throw new AssertionError("Cosmetic passenger changed gameplay riders");
                    for(var e:player.level().getAllEntities())if(e instanceof net.minecraft.world.entity.decoration.ArmorStand anchor&&anchor.entityTags().contains("oak_aviary_temporary")&&!anchor.isVehicle()) {
                        var target=player.getVehicle().position().add(0,2.0,0);
                        var aim=target.subtract(anchor.getEyePosition()).normalize();
                        var view=net.minecraft.world.phys.Vec3.directionFromRotation(anchor.getViewXRot(1),anchor.getViewYRot(1));
                        if(aim.dot(view)<.999)throw new AssertionError("Camera head rotation does not face the rider");
                        if(anchor.getAttributeValue(net.minecraft.world.entity.ai.attributes.Attributes.CAMERA_DISTANCE)<13)throw new AssertionError("Front F5 view would face away from the rider");
                        if(anchor.getEyePosition().add(view.scale(14)).y<player.getVehicle().getY()+.5)throw new AssertionError("Front F5 camera clips below the landing deck");
                    }
                }
                if(!app.journeys.containsKey(player.getUUID())&&!app.store.recoveries.containsKey(player.getUUID())) {
                    if(player.getInventory().getItem(0).getCount()!=7||!player.getInventory().getItem(0).is(Items.DIAMOND))throw new AssertionError("Inventory changed");
                    if(player.isPassenger()||!visualPassengers.isEmpty()||!player.getPostEffects().isEmpty()||Aviary.isTravelling(player.getUUID())||count(player.level())!=baseline)throw new AssertionError("Flight state leaked");
                    if(stage==1){if(Math.abs(player.getX()-48.5)>1)throw new AssertionError("Short flight did not arrive: "+player.position());if(app.fly(player,"p640")!=1)throw new AssertionError("Long flight rejected");stage=2;System.out.println("AVIARY_SMOKE long flight started");}
                    else if(stage==2){if(Math.abs(player.getX()-640.5)>1)throw new AssertionError("Long flight did not arrive");if(app.fly(player,"p0")!=1)throw new AssertionError("Return flight rejected");stage=3;}
                    else if(stage==3){if(Math.abs(player.getX()-640.5)>1)throw new AssertionError("Abort did not restore origin");if(skinPackets<3||equipmentPackets<3||privateSeats<3)throw new AssertionError("Passenger appearance missing");if(packets.getOrDefault("ClientboundSetCameraPacket",0)<4||packets.getOrDefault("ClientboundPostEffectsPacket",0)<10)throw new AssertionError("Camera/effect packets missing: "+packets);System.out.println("AVIARY_SMOKE PASS "+packets);server.halt(false);stage=4;}
                } else if(stage==3&&tick%30==0){app.journeys.get(player.getUUID()).abort("Test interruption");}
            }catch(Throwable e){e.printStackTrace();System.out.println("AVIARY_SMOKE FAIL");server.halt(false);}
        });
    }
    private static int count(ServerLevel level){int n=0;for(var e:level.getAllEntities())if(e.entityTags().contains("oak_aviary_temporary"))n++;return n;}
}
