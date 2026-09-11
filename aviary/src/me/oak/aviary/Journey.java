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
    private FlightScene routeScene,callScene,exitScene,entryScene,activeScene;
    private int sceneTick;
    private boolean arrivalJournal;
    private float facing;
    private Vec3 previousBase,cameraFocus;
    private final boolean freeCamera,quick;
    private double shotDistance=6.3,shotAngle=135;


    Journey(Aviary app,ServerPlayer player,AviaryStore.Port origin,AviaryStore.Port destination) {
        this.app=app;this.player=player;this.origin=origin;this.destination=destination;level=player.level();current=position(origin);
        yaw=(float)Math.toDegrees(Math.atan2(destination.z()-origin.z(),destination.x()-origin.x()))-90;facing=yaw;
        var preferences=app.store.preferences(player.getUUID());freeCamera=preferences.freeCamera();quick=preferences.quick();skip=quick;
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
    com.google.gson.JsonObject status(){var value=new com.google.gson.JsonObject();value.addProperty("origin",origin.id());value.addProperty("destination",destination.id());value.addProperty("phase",phase);value.addProperty("seconds",age/20);return value;}
    private AviaryStore.Recovery record(boolean destinationCommitted){return new AviaryStore.Recovery(player.getUUID().toString(),origin.dimension(),origin.x(),origin.y(),origin.z(),origin.yaw(),destination.dimension(),destination.x(),destination.y(),destination.z(),destination.yaw(),destinationCommitted);}
    private void hold(ChunkPos pos,int radius) {
        for(int x=pos.x()-radius;x<=pos.x()+radius;x++)for(int z=pos.z()-radius;z<=pos.z()+radius;z++) {
            ChunkPos chunk=new ChunkPos(x,z);if(!tickets.add(chunk))continue;
            REFERENCES.merge(chunk,1,Integer::sum);loads.add(level.getChunkSource().addTicketAndLoadWithRadius(TICKET,chunk,0));
        }
    }
    private void releaseTickets(){for(var chunk:tickets){int count=REFERENCES.getOrDefault(chunk,1)-1;if(count==0){REFERENCES.remove(chunk);level.getChunkSource().removeTicketWithRadius(TICKET,chunk,0);}else REFERENCES.put(chunk,count);}tickets.clear();}
    static boolean clearPort(ServerLevel level,AviaryStore.Port p) {
        return portIssue(level,p).isEmpty();
    }
    static String portIssue(ServerLevel level,AviaryStore.Port p) {
        BlockPos base=BlockPos.containing(p.x(),p.y()-.05,p.z());
        if(!level.getWorldBorder().isWithinBounds(base))return "Outside the world border";
        for(int x=-1;x<=1;x++)for(int z=-1;z<=1;z++) {
            BlockPos floor=base.offset(x,0,z);
            if(!level.hasChunkAt(floor))return "Area not loaded. Visit the aviport and check again.";
            if(!level.getBlockState(floor).isCollisionShapeFullBlock(level,floor))return "Solid floor required at "+floor.getX()+", "+floor.getY()+", "+floor.getZ();
        }
        // Include wings, rider, and the complete vertical departure/arrival corridor.
        for(int x=-3;x<=3;x++)for(int z=-3;z<=3;z++)for(int y=0;y<24;y++) {
            BlockPos pos=BlockPos.containing(p.x()+x,p.y()+y+.05,p.z()+z);
            if(!level.hasChunkAt(pos))return "Area not loaded. Visit the aviport and check again.";
            if(!level.getBlockState(pos).getCollisionShape(level,pos).isEmpty()||!level.getFluidState(pos).isEmpty())return "Clear the landing area at "+pos.getX()+", "+pos.getY()+", "+pos.getZ();
        }
        return "";
    }
    private ArmorStand anchor(Vec3 pos) {
        ArmorStand entity=new ArmorStand(EntityTypes.ARMOR_STAND,level);
        entity.addTag("oak_aviary_temporary");entity.setInvisible(true);entity.setNoGravity(true);entity.setSilent(true);entity.setNoBasePlate(true);entity.setPermanentlyInvulnerable(true);entity.setRequiresPrecisePosition(true);entity.setPos(pos);
        if(!level.addFreshEntity(entity))throw new IllegalStateException("Could not create flight anchor");return entity;
    }
    private void attachCamera() {
        if(freeCamera)return;
        var distance=java.util.Objects.requireNonNull(camera.getAttribute(net.minecraft.world.entity.ai.attributes.Attributes.CAMERA_DISTANCE));
        // Front-view F5 reverses the view and moves backwards along it. Place
        // that camera beyond the subject, so both third-person views face it.
        distance.setBaseValue(14);
        player.connection.send(new net.minecraft.network.protocol.game.ClientboundUpdateAttributesPacket(camera.getId(),List.of(distance)));
        passenger=new PassengerVisual(player,carrier,facing);
        player.connection.send(new net.minecraft.network.protocol.game.ClientboundRotateHeadPacket(camera,(byte)(camera.getYHeadRot()*256/360)));
        player.connection.send(new ClientboundSetCameraPacket(camera));
    }
    private void next(String value){phase=value;phaseTick=0;}
    private boolean clearSweep(Vec3 from,Vec3 to) {
        AABB box=new AABB(Math.min(from.x,to.x)-3.7,Math.min(from.y,to.y)+.01,Math.min(from.z,to.z)-3.7,
            Math.max(from.x,to.x)+3.7,Math.max(from.y,to.y)+4.3,Math.max(from.z,to.z)+3.7);
        for(double x=box.minX;x<=box.maxX+.01;x+=Math.max(.5,(box.maxX-box.minX)/2))
            for(double z=box.minZ;z<=box.maxZ+.01;z+=Math.max(.5,(box.maxZ-box.minZ)/2)) {
                BlockPos pos=BlockPos.containing(x,box.minY,z);
                if(!level.hasChunkAt(pos)||!level.getWorldBorder().isWithinBounds(pos))return false;
            }
        for(var shape:level.getBlockAndLiquidCollisions(null,box))if(!shape.isEmpty())return false;
        return true;
    }
    private boolean valid(FlightScene scene) {
        Vec3 last=scene.a;
        for(int tick=0;tick<=scene.ticks;tick++) {
            Vec3 target=scene.at(tick);
            if(!clearSweep(last,target))return false;
            last=target;
        }
        return true;
    }
    private FlightScene approach(AviaryStore.Port definition,boolean arriving) {
        Vec3 port=position(definition);Float preferred=arriving?definition.arrivalYaw():definition.departureYaw();
        for(double reach:new double[]{12,7,0})for(float angle:new float[]{0,45,-45,90,-90}) {
            if(reach==0&&angle!=0)continue;
            FlightScene path=arriving?FlightScene.entry(port,(preferred==null?yaw:preferred)+angle,reach):FlightScene.exit(port,(preferred==null?yaw:preferred)+angle,reach);
            if(valid(path))return path;
        }
        throw new IllegalStateException("The aviport needs a clear approach for the bird and rider.");
    }
    private void planScene() {
        Vec3 a=position(origin),d=position(destination);
        double distance=Math.hypot(a.x-d.x,a.z-d.z);
        if(distance<=app.store.settings.shortcutDistance())for(double bow:new double[]{Math.min(5,distance*.1),-Math.min(5,distance*.1),0}) {
            FlightScene candidate=FlightScene.route(a,d,bow,origin.departureYaw(),destination.arrivalYaw());
            if(valid(candidate)){routeScene=candidate;break;}
        }
        exitScene=approach(origin,false);entryScene=approach(destination,true);
        FlightScene call=approach(origin,true);callScene=new FlightScene(call.a,call.b,call.c,call.d,quick?38:58);
        shortcut=routeScene==null;
        if(routeScene!=null)for(int tick=0;tick<=routeScene.ticks;tick+=8)hold(chunk(routeScene.at(tick)),1);
    }
    private void use(FlightScene scene){activeScene=scene;sceneTick=0;previousBase=null;}
    private boolean advance(String action) {
        sceneTick=Math.min(activeScene.ticks,sceneTick+1);
        Vec3 target=activeScene.at(sceneTick);
        if(!clearSweep(previousBase==null?target:previousBase,target))return false;
        double t=sceneTick/(double)activeScene.ticks;
        String beat=action.equals("flight")?(t<.2?"depart":t>.73?"arrive":"cruise"):action;
        double beatProgress=action.equals("flight")?(t<.2?t/.2:t>.73?(t-.73)/.27:(t-.2)/.53):t;
        move(target,beat,beatProgress,activeScene.heading(sceneTick+5,facing));return true;
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
            planScene();bird=new BirdRig(level,callScene.a);carrier=anchor(current);camera=anchor(current.add(7,3,7));
            use(callScene);facing=callScene.heading(2,yaw);
            bird.animate(callScene.a,facing,age,"call",14);
            next("call");
        } else if(phase.equals("call")) {
            if(player.position().distanceTo(position(origin))>6){abort("Flight cancelled. Stay at the aviport to board.");return;}
            sceneTick=Math.min(callScene.ticks,sceneTick+1);
            Vec3 target=callScene.at(sceneTick);
            if(!clearSweep(previousBase==null?target:previousBase,target))throw new IllegalStateException("The bird's approach is obstructed.");
            facing=callScene.heading(sceneTick,facing);
            bird.animate(target,facing,age,"call",Math.max(0,target.y-origin.y()),.3,-.08,0,sceneTick/(double)callScene.ticks);
            previousBase=target;
            if(sceneTick<callScene.ticks)return;
            current=position(origin);previousBase=null;
            next("greet");
        } else if(phase.equals("greet")) {
            if(player.position().distanceTo(position(origin))>6){abort("Flight cancelled. Stay at the aviport to board.");return;}
            Vec3 look=player.position().subtract(current);
            float attention=net.minecraft.util.Mth.wrapDegrees((float)Math.toDegrees(Math.atan2(-look.x,look.z))-facing);
            bird.animate(current,facing,age,"greet",0,0,0,attention,phaseTick/24.0);
            if(phaseTick<(quick?12:24))return;
            player.teleportTo(level,current.x,current.y,current.z,Set.of(),facing,0,true);
            if(!player.startRiding(carrier,true,true))throw new IllegalStateException("Could not board");
            FlightSound.play(player,"saddle",current,.35f,1);
            next("board");
        } else if(phase.equals("board")) {
            int duration=quick?36:52;
            move(position(origin),"board",phaseTick/(double)duration,(routeScene!=null?routeScene:exitScene).heading(3,yaw));
            if(phaseTick==12)attachCamera();
            if(phaseTick>=duration){use(shortcut||skip?exitScene:routeScene);next(shortcut||skip?"depart":"flight");}
        } else if(phase.equals("depart")) {
            if(!advance("depart"))throw new IllegalStateException("Departure became obstructed.");
            if(sceneTick>=56)next("fade-out");
        } else if(phase.equals("flight")) {
            if(!advance("flight")){next("fade-out");return;}
            if(sceneTick>activeScene.ticks*.65&&!arrivalJournal){journal=app.store.journal(record(true));arrivalJournal=true;}
            if(arrivalJournal&&journal.isDone()){journal.join();transferred=true;}
            if(skip)next("fade-out");else if(sceneTick>=activeScene.ticks)next("settle");
        } else if(phase.equals("fade-out")) {
            if(!advance("depart"))move(current,"hold",0,facing);
            setFade(Math.min(16,(phaseTick+1)/2));
            if(phaseTick>=32){journal=app.store.journal(record(true));next("transfer");}
        } else if(phase.equals("transfer")) {
            if(!journal.isDone())return;journal.join();
            if(!clearPort(level,destination)||!valid(entryScene))throw new IllegalStateException("Arrival area changed");
            transferred=true;player.connection.send(new ClientboundSetCameraPacket(player));
            if(passenger!=null){passenger.close();passenger=null;}
            player.stopRiding();carrier.discard();camera.discard();bird.close();
            use(entryScene);current=entryScene.a;cameraFocus=null;facing=entryScene.heading(3,yaw);
            player.teleportTo(level,current.x,current.y,current.z,Set.of(),facing,0,true);
            carrier=anchor(current);camera=anchor(current.add(7,3,7));bird=new BirdRig(level,current);
            bird.animate(current,facing,age,"arrive",14);
            if(!player.startRiding(carrier,true,true))throw new IllegalStateException("Could not resume flight");next("arrival-load");
        } else if(phase.equals("arrival-load")) {
            move(entryScene.a,"hold",0,facing);
            if(phaseTick==15)attachCamera();
            boolean pending=false;
            var center=chunk(current);
            for(int x=-1;x<=1;x++)for(int z=-1;z<=1;z++)pending|=player.connection.chunkSender.isPending(new ChunkPos(center.x()+x,center.z()+z).pack());
            if(phaseTick>=60&&!pending)next("fade-in");
            if(phaseTick>240)throw new IllegalStateException("Destination chunk delivery timed out");
        } else if(phase.equals("fade-in")) {
            if(!advance("arrive"))throw new IllegalStateException("Arrival approach became obstructed.");
            setFade(Math.max(0,16-phaseTick/2));if(phaseTick>=32)next("arrive");
        } else if(phase.equals("arrive")) {
            if(!advance("arrive"))throw new IllegalStateException("Arrival approach became obstructed.");
            if(sceneTick>=activeScene.ticks)next("settle");
        } else if(phase.equals("settle")) {
            if(!clearPort(level,destination))throw new IllegalStateException("Arrival area changed");
            int duration=quick?32:48;
            if(phaseTick==1)FlightSound.play(player,"land",current,.42f,1);
            move(position(destination),"settle",Math.min(1,phaseTick/(double)duration),facing);
            if(phaseTick>=duration&&journal.isDone()){journal.join();transferred=true;finish(destination,"Arrived at "+destination.name()+".");}
        }
    }
    private void move(Vec3 target,String action,double progress,float desiredYaw) {
        Vec3 velocity=previousBase==null?Vec3.ZERO:target.subtract(previousBase);
        previousBase=target;current=target;
        float turn=net.minecraft.util.Mth.wrapDegrees(desiredYaw-facing);
        turn=(float)Math.clamp(turn*.22,-1.8,1.8);facing+=turn;
        double clearance=action.equals("board")||action.equals("settle")?0:
            action.equals("arrive")?Math.max(0,target.y-destination.y()):action.equals("depart")?Math.max(0,target.y-origin.y()):14;
        BirdRig.Frame frame=bird.animate(target,facing,age,action,clearance,velocity.horizontalDistance(),velocity.y,turn,progress);
        Vec3 root=frame.position();carrier.setPos(root);carrier.setYRot(facing);carrier.setYHeadRot(facing);carrier.positionRider(player);
        if(!player.isPassenger())player.startRiding(carrier,true,true);
        // Let the subject lead the shot. Bound lag so both mirrored F5 views
        // still face the rider; keep deck-relative pitch shallow and horizon level.
        Vec3 desiredFocus=target;
        cameraFocus=cameraFocus==null?desiredFocus:cameraFocus.lerp(desiredFocus,.16);
        Vec3 lag=new Vec3(cameraFocus.x-root.x,0,cameraFocus.z-root.z);
        if(lag.length()>1.15)lag=lag.normalize().scale(1.15);
        double desiredDistance=action.equals("board")?6.1:action.equals("settle")?6.3:action.equals("arrive")?6.8:7.0;
        shotDistance+=(desiredDistance-shotDistance)*.045;
        shotAngle+=((action.equals("arrive")?130:135)-shotAngle)*.035;
        double angle=Math.toRadians(facing+shotAngle),distance=shotDistance;
        double cameraLift=Math.clamp(cameraFocus.y-root.y,-.18,.18);
        Vec3 eye=root.add(lag).add(Math.sin(angle)*distance,2.6+cameraLift,Math.cos(angle)*distance);
        Vec3 aim=root.add(lag.scale(.45)).add(0,2.0+cameraLift,0).subtract(eye);
        camera.setPos(eye.subtract(0,camera.getEyeHeight(),0));camera.setYRot((float)(Math.toDegrees(Math.atan2(aim.z,aim.x))-90));camera.setXRot((float)-Math.toDegrees(Math.atan2(aim.y,Math.hypot(aim.x,aim.z))));
        camera.setYHeadRot(camera.getYRot());
        if(passenger!=null){passenger.updateHeading(facing);if(age%10==0)passenger.updateEquipment();}
        if(frame.downstroke())FlightSound.play(player,action.equals("depart")?"wing_power":"wing_glide",root,action.equals("depart")?.5f:.30f,1);
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
