package me.oak.aviary;

import com.mojang.authlib.GameProfile;
import net.fabricmc.api.ModInitializer;
import net.fabricmc.fabric.api.event.lifecycle.v1.ServerTickEvents;
import net.minecraft.core.BlockPos;
import net.minecraft.network.Connection;
import net.minecraft.network.protocol.*;
import net.minecraft.network.protocol.common.ServerboundResourcePackPacket;
import net.minecraft.network.protocol.game.ServerboundPlayerInputPacket;
import net.minecraft.server.level.*;
import net.minecraft.server.network.*;
import net.minecraft.world.entity.player.Input;
import net.minecraft.world.level.block.Blocks;
import net.minecraft.world.phys.Vec3;
import java.util.*;

/** Real native input/mount/journal checks in an isolated disposable world. */
public final class RoamingSmoke implements ModInitializer {
    private ServerPlayer player;
    private RoamingBird bird;
    private int ticks,stage,phaseTicks;
    private Vec3 start;
    private boolean disconnected;
    public void onInitialize(){
        if(!"isolated".equals(System.getProperty("oak.aviary.smoke")))throw new IllegalStateException("Isolated server required");
        ServerTickEvents.END_SERVER_TICK.register(server->{
            try{
                Aviary app=Aviary.instance;if(app.store==null)return;
                if(++ticks>2500)throw new AssertionError("Roaming timeout at "+stage+" / "+(bird==null?"none":bird.phase()));
                if(player==null){
                    var level=server.overworld();
                    // A fake connection has no real client view-distance subscription.
                    level.getChunkSource().addTicketAndLoadWithRadius(new TicketType(0,TicketType.FLAG_LOADING|TicketType.FLAG_SIMULATION),new net.minecraft.world.level.ChunkPos(0,0),3);
                    for(int x=-3;x<=3;x++)for(int z=-3;z<=3;z++)level.getChunkSource().getChunk(x,z,true);
                    for(int x=-40;x<=40;x++)for(int z=-40;z<=40;z++)level.setBlock(new BlockPos(x,79,z),Blocks.STONE.defaultBlockState(),3);
                    var profile=new GameProfile(UUID.randomUUID(),"AviaryPilot");
                    player=new ServerPlayer(server,level,profile,ClientInformation.createDefault()){public boolean hasDisconnected(){return disconnected;}};
                    var connection=new Connection(PacketFlow.SERVERBOUND){
                        public boolean isConnected(){return true;}public boolean isMemoryConnection(){return true;}
                        public void send(Packet<?> p){if(p.getClass().getSimpleName().contains("Chat")||p.getClass().getSimpleName().contains("ActionBar"))System.out.println("AVIARY_SMOKE message "+p);}public void send(Packet<?> p,io.netty.channel.ChannelFutureListener listener){send(p);}public void send(Packet<?> p,io.netty.channel.ChannelFutureListener listener,boolean flush){send(p);}
                    };
                    player.connection=new ServerGamePacketListenerImpl(server,connection,player,CommonListenerCookie.createInitial(profile,false));
                    player.setPos(.5,80,.5);player.setOnGround(true);level.addNewPlayer(player);
                    app.store.settings=new AviaryStore.Settings(true,"https://example.invalid/pack.zip","0".repeat(40),500,2);
                    Aviary.packResponse(profile.id(),new ServerboundResourcePackPacket(UUID.nameUUIDFromBytes("".getBytes(java.nio.charset.StandardCharsets.UTF_8)),ServerboundResourcePackPacket.Action.SUCCESSFULLY_LOADED));
                    require(app.companions.command(player,"call")==1,"Call rejected");
                    var field=Companions.class.getDeclaredField("birds");field.setAccessible(true);
                    bird=((Map<UUID,RoamingBird>)field.get(app.companions)).get(profile.id());
                    require(bird!=null,"No companion");System.out.println("AVIARY_SMOKE roaming call started");return;
                }
                player.connection.chunkSender.sendNextChunks(player);player.connection.chunkSender.onChunkBatchReceivedByClient(64);
                if(stage<10)require(app.companions.active(player.getUUID()),"Companion closed at "+stage+" / "+bird.phase()+" / "+app.diagnostics.status());
                if(ticks%100==0)System.out.println("AVIARY_SMOKE stage "+stage+" / "+bird.phase()+" / "+bird.position());
                phaseTicks++;
                if(stage==0&&bird.resting()){
                    require(!player.isPassenger()&&!Aviary.isTravelling(player.getUUID()),"Call mounted player");
                    player.setPos(bird.position().add(2,0,0));require(bird.board(),"Board rejected");stage=1;phaseTicks=0;
                }else if(stage==1&&bird.phase().equals("mounted")){
                    require(player.isPassenger()&&app.store.recoveries.containsKey(player.getUUID()),"Mount preceded recovery record");
                    send(true,false,true,false);stage=2;phaseTicks=0;
                }else if(stage==2&&bird.phase().equals("pilot")){
                    send(true,false,false,false);start=bird.position();stage=3;phaseTicks=0;System.out.println("AVIARY_SMOKE manual pilot started");
                }else if(stage==3&&phaseTicks==35){
                    require(bird.position().distanceTo(start)>8,"Forward input did not move bird");player.setYRot(90);start=bird.position();
                }else if(stage==3&&phaseTicks==75){
                    require(bird.position().x<start.x-4,"View direction did not steer bird");start=bird.position();send(false,true,false,false);stage=4;phaseTicks=0;
                }else if(stage==4&&phaseTicks==40){
                    require(bird.position().distanceTo(start)<12,"Brake input did not slow the bird");
                    start=bird.position();send(false,false,false,true);stage=5;phaseTicks=0;System.out.println("AVIARY_SMOKE assisted landing requested");
                }else if(stage==5&&bird.resting()){
                    send(false,false,false,false);
                    require(!player.isPassenger()&&!Aviary.isTravelling(player.getUUID())&&!app.store.recoveries.containsKey(player.getUUID()),"Landing left mount/recovery state");
                    require(app.diagnostics.status().get("completed").getAsInt()==1&&app.diagnostics.status().get("failed").getAsInt()==0,"Pilot recovered instead of landing");
                    require(app.companions.command(player,"stay")==1,"Stay rejected");
                    var stored=app.store.companion(player.getUUID());app.store.forget(player.getUUID());require(app.store.companion(player.getUUID()).equals(stored),"Companion state not durable");
                    start=bird.position();player.setPos(start.add(18,0,0));stage=6;phaseTicks=0;
                }else if(stage==6&&phaseTicks==80){
                    require(bird.position().distanceTo(start)<.1,"Waiting bird followed without permission");app.companions.command(player,"follow");stage=7;phaseTicks=0;
                }else if(stage==7&&!bird.resting()&&phaseTicks>100&&bird.position().distanceTo(player.position())<16){
                    require(bird.position().distanceTo(start)>5,"Companion did not follow");app.companions.command(player,"call");stage=8;phaseTicks=0;
                }else if(stage==8&&bird.resting()){
                    player.setPos(bird.position().add(2,0,0));require(bird.board(),"Second boarding rejected");stage=9;phaseTicks=0;
                }else if(stage==9&&bird.phase().equals("mounted")){
                    disconnected=true;app.companions.disconnect(player.getUUID());stage=10;phaseTicks=0;
                }else if(stage==10&&phaseTicks==10){
                    require(!app.companions.active(player.getUUID())&&app.store.recoveries.containsKey(player.getUUID()),"Disconnect discarded recovery");
                    for(var e:player.level().getAllEntities())require(!e.entityTags().contains("oak_aviary_temporary"),"Companion entity leaked");
                    System.out.println("AVIARY_SMOKE PASS roaming native input, steering, landing, stay, follow and disconnect recovery");server.halt(false);stage=11;
                }
            }catch(Throwable e){e.printStackTrace();System.out.println("AVIARY_SMOKE FAIL");server.halt(false);}
        });
    }
    private void send(boolean forward,boolean backward,boolean jump,boolean shift){player.connection.handlePlayerInput(new ServerboundPlayerInputPacket(new Input(forward,backward,false,false,jump,shift,false)));}
    private static void require(boolean value,String message){if(!value)throw new AssertionError(message);}
}
