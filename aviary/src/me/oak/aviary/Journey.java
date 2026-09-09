package me.oak.aviary;

import net.minecraft.core.BlockPos;
import net.minecraft.network.protocol.game.ClientboundSetCameraPacket;
import net.minecraft.resources.Identifier;
import net.minecraft.server.level.*;
import net.minecraft.world.entity.*;
import net.minecraft.world.entity.decoration.ArmorStand;
import net.minecraft.world.entity.player.Input;
import net.minecraft.world.level.ChunkPos;
import net.minecraft.world.phys.*;
import java.util.*;
import java.util.concurrent.*;

/** A bounded flight with write-ahead recovery and no changes to player abilities. */
final class Journey {
    private static final TicketType TICKET=new TicketType(0,TicketType.FLAG_LOADING|TicketType.FLAG_SIMULATION);
    // Multiple flights may share chunks. A ticket is released only by its last user.
    private static final Map<ChunkPos,Integer> REFERENCES=new HashMap<>();
    final ServerPlayer player;
    private final Aviary app;
    private final AviaryStore.Port origin,destination;
    private final ServerLevel level;
    private final Set<ChunkPos> tickets=new HashSet<>();
    private final List<CompletableFuture<?>> loads=new ArrayList<>();
    private CompletableFuture<Void> journal;
    private BirdRig bird;
    private ArmorStand carrier,camera;
    private PassengerVisual passenger;
    private int age,phaseTick,fade;
    private String phase="prepare";
    private boolean transferred,closed,shortcut;
    boolean skip;
    private Vec3 current;
    private final float yaw;
    private int cruiseTicks;

    Journey(Aviary app,ServerPlayer player,AviaryStore.Port origin,AviaryStore.Port destination) {
        this.app=app;this.player=player;this.origin=origin;this.destination=destination;level=player.level();current=position(origin);
        yaw=(float)Math.toDegrees(Math.atan2(destination.z()-origin.z(),destination.x()-origin.x()))-90;
        try {hold(chunk(position(origin)),2);hold(chunk(position(destination)),2);journal=app.store.journal(record(false));}
        catch(RuntimeException e){releaseTickets();throw e;}
    }
    private static ChunkPos chunk(Vec3 p){return new ChunkPos(((int)Math.floor(p.x))>>4,((int)Math.floor(p.z))>>4);}
    static Vec3 position(AviaryStore.Port p){return new Vec3(p.x(),p.y(),p.z());}
    static Vec3 safeLanding(ServerLevel level,double x,double y,double z) {
        // Recovery may run after an administrator changed a port. Search nearby
        // loaded terrain without changing blocks, inventory, or player abilities.
        for(int radius=0;radius<=12;radius++)for(int dx=-radius;dx<=radius;dx++)for(int dz=-radius;dz<=radius;dz++) {
            if(Math.max(Math.abs(dx),Math.abs(dz))!=radius)continue;
            for(int dy=0;dy<=32;dy++)for(int sign:new int[]{1,-1}) {
                BlockPos feet=BlockPos.containing(x+dx,y+dy*sign,z+dz);
                if(!level.hasChunkAt(feet)||!level.getWorldBorder().isWithinBounds(feet))continue;
                var floor=feet.below();
                if(level.getBlockState(floor).isCollisionShapeFullBlock(level,floor)&&level.getBlockState(feet).isAir()&&level.getBlockState(feet.above()).isAir()&&level.getFluidState(feet).isEmpty())return new Vec3(feet.getX()+.5,feet.getY(),feet.getZ()+.5);
            }
        }
        return null;
    }
    boolean uses(String id){return origin.id().equals(id)||destination.id().equals(id);}
    private AviaryStore.Recovery record(boolean destinationCommitted){return new AviaryStore.Recovery(player.getUUID().toString(),origin.dimension(),origin.x(),origin.y(),origin.z(),origin.yaw(),destination.dimension(),destination.x(),destination.y(),destination.z(),destination.yaw(),destinationCommitted);}
    private void hold(ChunkPos pos,int radius) {
        for(int x=pos.x()-radius;x<=pos.x()+radius;x++)for(int z=pos.z()-radius;z<=pos.z()+radius;z++) {
            ChunkPos chunk=new ChunkPos(x,z);if(!tickets.add(chunk))continue;
            REFERENCES.merge(chunk,1,Integer::sum);loads.add(level.getChunkSource().addTicketAndLoadWithRadius(TICKET,chunk,0));
        }
    }
    private void releaseTickets(){for(var chunk:tickets){int count=REFERENCES.getOrDefault(chunk,1)-1;if(count==0){REFERENCES.remove(chunk);level.getChunkSource().removeTicketWithRadius(TICKET,chunk,0);}else REFERENCES.put(chunk,count);}tickets.clear();}
    static boolean clearPort(ServerLevel level,AviaryStore.Port p) {
        BlockPos base=BlockPos.containing(p.x(),p.y()-.05,p.z());
        if(!level.getWorldBorder().isWithinBounds(base))return false;
        for(int x=-1;x<=1;x++)for(int z=-1;z<=1;z++) {
            BlockPos floor=base.offset(x,0,z);
            if(!level.hasChunkAt(floor)||!level.getBlockState(floor).isCollisionShapeFullBlock(level,floor))return false;
        }
        // Include wings, rider, and the complete vertical departure/arrival corridor.
        for(int x=-3;x<=3;x++)for(int z=-3;z<=3;z++)for(int y=0;y<24;y++) {
            BlockPos pos=BlockPos.containing(p.x()+x,p.y()+y+.05,p.z()+z);
            if(!level.hasChunkAt(pos)||!level.getBlockState(pos).getCollisionShape(level,pos).isEmpty()||!level.getFluidState(pos).isEmpty())return false;
        }
        return true;
    }
    private ArmorStand anchor(Vec3 pos) {
        ArmorStand entity=new ArmorStand(EntityTypes.ARMOR_STAND,level);
        entity.addTag("oak_aviary_temporary");entity.setInvisible(true);entity.setNoGravity(true);entity.setSilent(true);entity.setNoBasePlate(true);entity.setPermanentlyInvulnerable(true);entity.setRequiresPrecisePosition(true);entity.setPos(pos);
        if(!level.addFreshEntity(entity))throw new IllegalStateException("Could not create flight anchor");return entity;
    }
    private void attachCamera() {
        var distance=java.util.Objects.requireNonNull(camera.getAttribute(net.minecraft.world.entity.ai.attributes.Attributes.CAMERA_DISTANCE));
        // Front-view F5 reverses the view and moves backwards along it. Place
        // that camera beyond the subject, so both third-person views face it.
        distance.setBaseValue(14);
        player.connection.send(new net.minecraft.network.protocol.game.ClientboundUpdateAttributesPacket(camera.getId(),List.of(distance)));
        passenger=new PassengerVisual(player,carrier,yaw);
        player.connection.send(new net.minecraft.network.protocol.game.ClientboundRotateHeadPacket(camera,(byte)(camera.getYHeadRot()*256/360)));
        player.connection.send(new ClientboundSetCameraPacket(camera));
    }
    private void next(String value){phase=value;phaseTick=0;}
    private Vec3 route(double t) {
        Vec3 a=position(origin).add(0,18,0),b=position(destination).add(0,18,0);
        return a.lerp(b,t).add(0,Math.sin(Math.PI*t)*20,0);
    }
    private boolean planFullRoute() {
        double distance=Math.hypot(origin.x()-destination.x(),origin.z()-destination.z());
        if(distance>app.store.settings.shortcutDistance())return false;
        // Never generate a corridor through unexplored terrain. A distant or obstructed
        // corridor gets the same cinematic cut as a long route.
        for(int i=0;i<=Math.ceil(distance/3);i++) {
            Vec3 p=route(i/Math.max(1,Math.ceil(distance/3)));
            for(int x=-4;x<=4;x+=2)for(int z=-4;z<=4;z+=2) {
                BlockPos at=BlockPos.containing(p.x+x,p.y,p.z+z);
                if(!level.hasChunkAt(at))return false;
            }
            if(!level.noBlockCollision(null,new AABB(p.x-4,p.y,p.z-4,p.x+4,p.y+4,p.z+4)))return false;
        }
        for(int i=0;i<=Math.ceil(distance/16);i++)hold(chunk(route(i/Math.max(1,Math.ceil(distance/16)))),1);
        cruiseTicks=FlightPath.cruiseTicks(distance);return true;
    }
    void tick() {
        if(closed)return;
        if(++age>1800) {abort("Flight timed out. Returning to a safe aviport.");return;}
        if(player.hasDisconnected()||!player.isAlive()||player.level()!=level){abort("Flight interrupted.");return;}
        ++phaseTick;player.resetFallDistance();player.setLastClientInput(Input.EMPTY);player.setDeltaMovement(Vec3.ZERO);
        if(phase.equals("prepare")) {
            if(age>400)throw new IllegalStateException("Aviport preparation timed out");
            if(!journal.isDone()||loads.stream().anyMatch(f->!f.isDone()))return;
            journal.join();for(var f:loads)f.join();
            if(!clearPort(level,origin)||!clearPort(level,destination))throw new IllegalStateException("A landing area is obstructed");
            shortcut=!planFullRoute();bird=new BirdRig(level,current.add(0,12,0));carrier=anchor(current);camera=anchor(current.add(7,3,7));
            next("call");
        } else if(phase.equals("call")) {
            if(player.position().distanceTo(position(origin))>6){abort("Flight cancelled. Stay at the aviport to board.");return;}
            if(age%2==0)bird.animate(position(origin).add(0,12*(1-FlightPath.ease(phaseTick/50.0)),0),yaw,age,"call");
            if(phaseTick<50&&!skip)return;
            current=position(origin);
            player.teleportTo(level,current.x,current.y,current.z,Set.of(),yaw,0,true);
            if(!player.startRiding(carrier,true,true))throw new IllegalStateException("Could not board");
            next("board");
        } else if(phase.equals("board")) {
            move(position(origin));
            if(phaseTick==12)attachCamera();
            if(phaseTick>=40)next("depart");
        } else if(phase.equals("depart")) {
            move(position(origin).add(0,18*FlightPath.ease(phaseTick/80.0),0));
            if(phaseTick>=80)next(shortcut||skip?"fade-out":"cruise");
        } else if(phase.equals("cruise")) {
            Vec3 target=route(FlightPath.ease(phaseTick/(double)cruiseTicks));
            if(!level.noBlockCollision(null,new AABB(target.x-4,target.y,target.z-4,target.x+4,target.y+4,target.z+4))){next("fade-out");return;}
            move(target);
            if(skip)next("fade-out");else if(phaseTick>=cruiseTicks){journal=app.store.journal(record(true));next("commit-arrival");}
        } else if(phase.equals("fade-out")) {
            move(current);setFade(Math.min(16,(phaseTick+1)/2));
            if(phaseTick>=32){journal=app.store.journal(record(true));next("transfer");}
        } else if(phase.equals("transfer")) {
            if(!journal.isDone())return;journal.join();
            if(phaseTick%5==0&&!clearPort(level,destination))throw new IllegalStateException("Arrival area changed");
            transferred=true;player.connection.send(new ClientboundSetCameraPacket(player));
            if(passenger!=null){passenger.close();passenger=null;}
            player.stopRiding();carrier.discard();camera.discard();bird.close();
            current=position(destination).add(0,18,0);
            player.teleportTo(level,current.x,current.y,current.z,Set.of(),yaw,0,true);
            carrier=anchor(current);camera=anchor(current.add(7,3,7));bird=new BirdRig(level,current);
            if(!player.startRiding(carrier,true,true))throw new IllegalStateException("Could not resume flight");next("arrival-load");
        } else if(phase.equals("arrival-load")) {
            move(current);
            if(phaseTick==15)attachCamera();
            boolean pending=false;
            var center=chunk(current);
            for(int x=-1;x<=1;x++)for(int z=-1;z<=1;z++)pending|=player.connection.chunkSender.isPending(new ChunkPos(center.x()+x,center.z()+z).pack());
            if(phaseTick>=60&&!pending)next("fade-in");
            if(phaseTick>240)throw new IllegalStateException("Destination chunk delivery timed out");
        } else if(phase.equals("fade-in")) {
            move(current);setFade(Math.max(0,16-phaseTick/2));if(phaseTick>=32)next("arrive");
        } else if(phase.equals("commit-arrival")) {
            move(current);if(journal.isDone()){journal.join();transferred=true;next("arrive");}
        } else if(phase.equals("arrive")) {
            if(!clearPort(level,destination))throw new IllegalStateException("Arrival area changed");
            move(position(destination).add(0,18*(1-FlightPath.ease(phaseTick/80.0)),0));
            if(phaseTick>=80)finish(destination,"Arrived at "+destination.name()+".");
        }
    }
    private void move(Vec3 target) {
        current=target;carrier.setPos(target);carrier.setYRot(yaw);carrier.positionRider(player);
        if(!player.isPassenger())player.startRiding(carrier,true,true);
        double angle=Math.toRadians(yaw+135),distance=6.5;
        // A shallow pitch keeps the mirrored F5 camera above the landing deck.
        Vec3 eye=target.add(Math.sin(angle)*distance,2.6,Math.cos(angle)*distance);
        Vec3 aim=target.add(0,2.0,0).subtract(eye);
        camera.setPos(eye.subtract(0,camera.getEyeHeight(),0));camera.setYRot((float)(Math.toDegrees(Math.atan2(aim.z,aim.x))-90));camera.setXRot((float)-Math.toDegrees(Math.atan2(aim.y,Math.hypot(aim.x,aim.z))));
        camera.setYHeadRot(camera.getYRot());
        if(age%2==0)bird.animate(target,yaw,age,phase);
        if(passenger!=null&&age%10==0)passenger.updateEquipment();
        if(age%40==0&&!phase.equals("board"))level.playSound(null,carrier.blockPosition(),net.minecraft.sounds.SoundEvents.ENDER_DRAGON_FLAP,net.minecraft.sounds.SoundSource.NEUTRAL,.18f,1.6f);
    }
    private void setFade(int value){setFade(value,false);}
    private void setFade(int value,boolean force) {
        if(value==fade&&!force)return;
        if(fade>0)player.removePostEffect(Identifier.parse("oak_aviary:fade_"+fade));
        fade=value;if(fade>0)player.addPostEffect(Identifier.parse("oak_aviary:fade_"+fade));player.sendPostEffects();
    }
    void abort(String message){if(!closed)finish(transferred?destination:origin,message);}
    private void finish(AviaryStore.Port port,String message) {
        closed=true;Aviary.restoreCamera(player);player.stopRiding();
        if(passenger!=null){passenger.close();passenger=null;}
        // The origin remains ticketed throughout travel, including recovery writes.
        Vec3 safe=safeLanding(level,port.x(),port.y(),port.z());
        if(safe==null)safe=safeLanding(level,origin.x(),origin.y(),origin.z());
        if(safe!=null)player.teleportTo(level,safe.x,safe.y,safe.z,Set.of(),port.yaw(),0,true);
        player.resetFallDistance();player.setLastClientInput(Input.EMPTY);
        if(bird!=null)bird.close();if(carrier!=null)carrier.discard();if(camera!=null)camera.discard();
        if(safe==null){Aviary.tell(player,"No safe landing area is available. Rejoin after an administrator clears an aviport.");player.connection.disconnect(net.minecraft.network.chat.Component.literal("No safe landing area. Ask an administrator to clear an aviport before rejoining."));app.journeys.remove(player.getUUID());releaseTickets();return;}
        app.store.complete(player.getUUID()).whenComplete((v,e)->app.server.execute(()->{releaseTickets();if(e==null)app.released(player.getUUID());else {app.journeys.remove(player.getUUID());System.err.println("Oak Aviary recovery cleanup failed: "+e);}}));
        if(!player.hasDisconnected())Aviary.tell(player,message);
    }
}
