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
import me.oak.aviary.mixin.InteractionAccess;
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
    private Interaction saddle;
    private PassengerVisual passenger;
    private int age,phaseTick,fade;
    private String phase="prepare";
    private boolean transferred,closed,shortcut,boarded,delivered;
    private BirdTraits traits;
    boolean skip;
    private Vec3 current;
    private final float yaw;
    private FlightScene routeScene,callScene,exitScene,entryScene,activeScene;
    private FlightPlanner planner;
    private FlightSpace.Check arrivalCheck,farewellCheck;
    private FlightScene farewellScene;
    private int sceneTick;
    private boolean arrivalJournal;
    private float facing;
    private Vec3 previousBase,cameraFocus;
    private Map<String,AABB> previousBounds=Map.of();
    private final boolean freeCamera,quick;
    private final String group;
    private final double shortcutDistance;
    private double shotDistance=6.3,shotAngle=135;


    Journey(Aviary app,ServerPlayer player,AviaryStore.Port origin,AviaryStore.Port destination) {
        this(app,player,origin,destination,null,app.store.preferences(player.getUUID()).quick());
    }
    Journey(Aviary app,ServerPlayer player,AviaryStore.Port origin,AviaryStore.Port destination,String group,boolean quick) {
        this.app=app;this.player=player;this.origin=origin;this.destination=destination;level=player.level();current=position(origin);
        this.group=group;shortcutDistance=app.store.settings.shortcutDistance();
        yaw=(float)Math.toDegrees(Math.atan2(destination.z()-origin.z(),destination.x()-origin.x()))-90;facing=yaw;
        var preferences=app.store.preferences(player.getUUID());freeCamera=preferences.freeCamera();this.quick=quick;skip=quick;
        try {hold(chunk(position(origin)),2);hold(chunk(position(destination)),2);}
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
                if(Perches.supportValid(level,floor)&&level.getBlockState(feet).isAir()&&level.getBlockState(feet.above()).isAir()&&level.getFluidState(feet).isEmpty())return new Vec3(feet.getX()+.5,feet.getY(),feet.getZ()+.5);
            }
        }
        return null;
    }
    boolean uses(String id){return origin.id().equals(id)||destination.id().equals(id);}
    boolean waiting(){return !closed&&!boarded&&!delivered&&!phase.equals("securing");}
    boolean boarded(){return boarded;}
    String group(){return group;}
    boolean hasClearedOrigin(){return closed||transferred||(boarded&&current.distanceTo(position(origin))>=16);}
    private boolean companionPending(boolean arrival) {
        if(group==null)return false;
        for(var other:app.journeys.values()) {
            if(other==this)break;
            if(group.equals(other.group)&&!other.closed&&(arrival||!other.hasClearedOrigin()))return true;
        }
        return false;
    }
    boolean acceptsBoarding(Entity entity){return phase.equals("wait")&&(entity==saddle||entity==carrier);}
    boolean feed(net.minecraft.world.item.ItemStack food) {
        if(!phase.equals("wait")||traits==null||!food.is(net.minecraft.world.item.Items.COOKED_CHICKEN)||player.position().distanceTo(current)>4.5||!traits.feed(age))return false;
        if(!player.isCreative())food.shrink(1);FlightSound.play(player,"reply",current,.35f,1.12f);return true;
    }
    private BirdRig createBird(Vec3 position) {
        BirdRig value=new BirdRig(level,position);
        var perch=app.store.perches.get(origin.id());String name=perch==null?"Condor":perch.birdName();
        if(traits==null)traits=new BirdTraits(origin.id(),destination.id(),name);
        value.personality(traits);
        value.setPlumage(switch(Math.floorMod(origin.owner().hashCode(),3)){case 1->"ash";case 2->"amber";default->"default";});
        return value;
    }
    boolean board() {
        if(!phase.equals("wait")||player.position().distanceTo(current)>4.5||player.isPassenger()||player.isSleeping()||player.isSpectator()||!player.isAlive())return false;
        if(!clearPort(level,origin)||!clearPort(level,destination)){abort("The landing area changed. Call again after clearing it.");return false;}
        // Waiting never suppresses movement or damage and has no recovery record.
        app.protect(player.getUUID());
        try {journal=app.store.journal(record(false));next("securing");return true;}
        catch(RuntimeException e){abort("Could not prepare the flight. Please try again.");return false;}
    }
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
        var metadata=Aviary.instance==null||Aviary.instance.store==null?null:Aviary.instance.store.perches.get(p.id());
        if(metadata!=null&&!metadata.active())return "Place this perch before travelling";
        return placementIssue(level,p);
    }
    static String placementIssue(ServerLevel level,AviaryStore.Port p) {
        // Perch beams sit 0.75 blocks above the support; legacy and field roots
        // rest directly on their floor. The item preview precedes registration.
        boolean elevated=Math.abs(p.y()-Math.floor(p.y())-.75)<.001;
        BlockPos base=BlockPos.containing(p.x(),p.y()-(elevated?.76:.05),p.z());
        if(!level.getWorldBorder().isWithinBounds(base))return "Outside the world border";
        if(!level.hasChunkAt(base))return "Visit this perch to load the area";
        if(!Perches.supportValid(level,base))return "Place the perch on a safe solid block";
        if(boardingSpot(level,p)==null)return "Leave a safe place to stand beside the perch";
        if(!FlightSpace.resting(level,position(p),p.yaw()))return "Clear this space or turn the perch";
        return "";
    }
    static Vec3 boardingSpot(ServerLevel level,AviaryStore.Port p) {
        for(int radius:new int[]{2,1,3,0})for(int dx=-radius;dx<=radius;dx++)for(int dz=-radius;dz<=radius;dz++) {
            if(Math.max(Math.abs(dx),Math.abs(dz))!=radius)continue;
            for(int dy:new int[]{0,-1,1,-2,2}) {
                BlockPos feet=BlockPos.containing(p.x()+dx,Math.floor(p.y())+dy,p.z()+dz),floor=feet.below();
                if(!level.hasChunkAt(feet)||!level.getWorldBorder().isWithinBounds(feet))continue;
                if(Perches.supportValid(level,floor)&&level.getBlockState(feet).isAir()&&level.getBlockState(feet.above()).isAir())return new Vec3(feet.getX()+.5,feet.getY(),feet.getZ()+.5);
            }
        }
        return null;
    }
    static AviaryStore.Port fieldOrigin(ServerPlayer p) {
        if(!p.level().dimension().equals(net.minecraft.world.level.Level.OVERWORLD)||!p.onGround())return null;
        int attempts=0;
        for(int radius:new int[]{3,5})for(int direction=0;direction<8;direction++)for(int dy:new int[]{0,-1,1,-2,2}) {
            double angle=direction*Math.PI/4;BlockPos feet=BlockPos.containing(p.getX()+Math.sin(angle)*radius,p.getY()+dy,p.getZ()+Math.cos(angle)*radius);
            if(!p.level().hasChunkAt(feet)||!p.level().canSeeSky(feet))continue;
            var floor=feet.below();if(!p.level().getBlockState(floor).isCollisionShapeFullBlock(p.level(),floor)||!p.level().getBlockState(feet).isAir()||!p.level().getBlockState(feet.above()).isAir())continue;
            var port=new AviaryStore.Port("field-"+p.getUUID().toString().replace("-", "").substring(0,20),"Field pickup","minecraft:overworld",feet.getX()+.5,feet.getY(),feet.getZ()+.5,p.getYRot(),p.getUUID().toString(),false);
            if(placementIssue(p.level(),port).isEmpty())return port;
            if(++attempts>=12)return null;
        }
        return null;
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
    private boolean planScene() {
        if(planner==null)planner=new FlightPlanner(level,origin,destination,shortcutDistance,quick);
        if(!planner.step(app.planningDeadline))return false;
        routeScene=planner.route;exitScene=planner.exit;entryScene=planner.entry;callScene=planner.call;
        shortcut=routeScene==null;
        if(routeScene!=null)for(int tick=0;tick<=routeScene.ticks;tick+=8)hold(chunk(routeScene.at(tick)),1);
        return true;
    }
    private void use(FlightScene scene){activeScene=scene;sceneTick=0;previousBase=null;previousBounds=Map.of();}
    private boolean advance(String action) {
        sceneTick=Math.min(activeScene.ticks,sceneTick+1);
        Vec3 target=activeScene.at(sceneTick);
        double t=sceneTick/(double)activeScene.ticks;
        String beat=action.equals("flight")?(t<.2?"depart":t>.73?"arrive":"cruise"):action;
        double beatProgress=action.equals("flight")?(t<.2?t/.2:t>.73?(t-.73)/.27:(t-.2)/.53):t;
        move(target,beat,beatProgress,activeScene.heading(sceneTick+5,facing));return true;
    }
    void tick() {
        if(closed)return;
        if(++age>2800) {abort("Flight timed out. Returning to a safe perch.");return;}
        if(player.hasDisconnected()||!player.isAlive()||player.level()!=level){abort("Flight interrupted.");return;}
        if(waiting()&&player.position().distanceTo(position(origin))>9){abort("Call cancelled.");return;}
        ++phaseTick;
        if(boarded){player.resetFallDistance();player.setLastClientInput(Input.EMPTY);player.setDeltaMovement(Vec3.ZERO);}
        if(phase.equals("prepare")) {
            if(companionPending(false)){if(age%80==1)hint("Your companion departs first");phaseTick=0;return;}
            if(phaseTick>400)throw new IllegalStateException("Perch preparation timed out");
            if(loads.stream().anyMatch(f->!f.isDone()))return;
            for(var f:loads)f.join();
            String originIssue=portIssue(level,origin),destinationIssue=portIssue(level,destination);
            if(!originIssue.isEmpty()||!destinationIssue.isEmpty())throw new IllegalStateException(!originIssue.isEmpty()?origin.name()+": "+originIssue:destination.name()+": "+destinationIssue);
            if(!planScene())return;
            bird=createBird(callScene.a);carrier=anchor(current);camera=anchor(current.add(7,3,7));
            use(callScene);facing=callScene.heading(2,yaw);
            bird.animate(callScene.a,facing,age,"call",14);
            FlightSound.play(player,"reply",callScene.a,.65f,1);
            next("call");
        } else if(phase.equals("call")) {
            if(player.position().distanceTo(position(origin))>9){abort("Call cancelled.");return;}
            sceneTick=Math.min(callScene.ticks,sceneTick+1);
            Vec3 target=callScene.at(sceneTick);
            facing=FlightSpace.turn(facing,callScene.heading(sceneTick+5,facing));
            bird.animate(target,facing,age,"call",Math.max(0,target.y-origin.y()),.3,-.08,0,sceneTick/(double)callScene.ticks);
            checkPose(target);previousBase=target;
            if(sceneTick<callScene.ticks)return;
            current=position(origin);previousBase=null;
            next("greet");
        } else if(phase.equals("greet")) {
            if(player.position().distanceTo(position(origin))>9){abort("Call cancelled.");return;}
            Vec3 look=player.position().subtract(current);
            float attention=net.minecraft.util.Mth.wrapDegrees((float)Math.toDegrees(Math.atan2(-look.x,look.z))-facing);
            bird.animate(current,facing,age,"greet",0,0,0,attention+Math.toDegrees(traits.lookOffset(age)),phaseTick/24.0);
            checkPose(current);
            if(phaseTick<(quick?12:24))return;
            saddle=new Interaction(EntityTypes.INTERACTION,level);saddle.addTag("oak_aviary_temporary");saddle.setPos(current.add(0,.45,0));
            var hit=(InteractionAccess)saddle;hit.aviary$width(2);hit.aviary$height(1.6f);hit.aviary$response(true);
            if(!level.addFreshEntity(saddle))throw new IllegalStateException("Could not create saddle interaction");
            hint(traits.identityName()+" · Use the saddle to board");next("wait");
        } else if(phase.equals("wait")) {
            if(phaseTick>600||player.position().distanceTo(current)>9){abort("Call cancelled.");return;}
            Vec3 look=player.position().subtract(current);float attention=net.minecraft.util.Mth.wrapDegrees((float)Math.toDegrees(Math.atan2(-look.x,look.z))-facing);
            bird.animate(current,facing,age,"greet",0,0,0,attention+Math.toDegrees(traits.lookOffset(age)),1);checkPose(current);
            if(phaseTick%80==0)hint(traits.identityName()+" · Use the saddle to board");
        } else if(phase.equals("securing")) {
            if(phaseTick>100)throw new IllegalStateException("Flight preparation timed out");
            if(!journal.isDone())return;journal.join();
            if(saddle!=null){saddle.discard();saddle=null;}
            player.teleportTo(level,current.x,current.y,current.z,Set.of(),facing,0,true);
            if(!player.startRiding(carrier,true,true))throw new IllegalStateException("Could not board");
            boarded=true;hint("Hold Shift to skip");
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
            if(sceneTick>activeScene.ticks*.60&&companionPending(true)){next("fade-out");return;}
            if(sceneTick>activeScene.ticks*.65&&!arrivalJournal){journal=app.store.journal(record(true));arrivalJournal=true;}
            if(arrivalJournal&&journal.isDone()){journal.join();transferred=true;}
            if(skip)next("fade-out");else if(sceneTick>=activeScene.ticks)next("settle");
        } else if(phase.equals("fade-out")) {
            if(!advance("depart"))move(current,"hold",0,facing);
            setFade(Math.min(16,(phaseTick+1)/2));
            if(phaseTick>=32){journal=app.store.journal(record(true));next("transfer");}
        } else if(phase.equals("transfer")) {
            if(companionPending(true))return;
            if(!journal.isDone())return;journal.join();
            if(arrivalCheck==null)arrivalCheck=new FlightSpace.Check(level,entryScene,"arrive");
            if(!arrivalCheck.step(app.planningDeadline))return;
            if(!clearPort(level,destination)||!arrivalCheck.valid())throw new IllegalStateException("Arrival area changed");
            transferred=true;player.connection.send(new ClientboundSetCameraPacket(player));
            if(passenger!=null){passenger.close();passenger=null;}
            player.stopRiding();carrier.discard();camera.discard();bird.close();
            use(entryScene);current=entryScene.a;cameraFocus=null;facing=entryScene.heading(3,yaw);
            player.teleportTo(level,current.x,current.y,current.z,Set.of(),facing,0,true);
            carrier=anchor(current);camera=anchor(current.add(7,3,7));bird=createBird(current);
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
            if(phaseTick>=duration&&journal.isDone()){journal.join();transferred=true;land();}
        } else if(phase.equals("farewell")) {
            bird.animate(current,facing,age,"greet",0,0,0,Math.toDegrees(traits.lookOffset(age)),1);checkPose(current);
            if(phaseTick>=(quick?20:48)) {
                if(farewellCheck==null){farewellScene=FlightScene.exit(position(destination),facing,28);farewellCheck=new FlightSpace.Check(level,farewellScene,"depart");}
                if(!farewellCheck.step(app.planningDeadline))return;
                if(farewellCheck.valid()){use(farewellScene);next("leave");}
                else finish(destination,"");
            }
        } else if(phase.equals("leave")) {
            sceneTick=Math.min(activeScene.ticks,sceneTick+1);Vec3 target=activeScene.at(sceneTick),velocity=target.subtract(current);
            facing=FlightSpace.turn(facing,activeScene.heading(sceneTick+5,facing));
            bird.animate(target,facing,age,"depart",Math.max(0,target.y-destination.y()),velocity.horizontalDistance(),velocity.y,0,sceneTick/(double)activeScene.ticks);
            checkPose(target);previousBase=target;current=target;
            if(sceneTick>=activeScene.ticks)finish(destination,"");
        }
    }
    private void land() {
        Vec3 safe=boardingSpot(level,destination);if(safe==null)throw new IllegalStateException("The dismount space became obstructed");
        Aviary.restoreCamera(player);player.stopRiding();if(passenger!=null){passenger.close();passenger=null;}
        player.teleportTo(level,safe.x,safe.y,safe.z,Set.of(),facing,0,true);player.resetFallDistance();hint("");
        if(carrier!=null){carrier.discard();carrier=null;}if(camera!=null){camera.discard();camera=null;}
        boarded=false;delivered=true;app.unprotect(player.getUUID());
        // Keep the perch reserved for the farewell, while the player is already free.
        journal=app.store.complete(player.getUUID());
        Aviary.tell(player,"Arrived at "+destination.name()+".");next("farewell");
    }
    private void move(Vec3 target,String action,double progress,float desiredYaw) {
        Vec3 velocity=previousBase==null?Vec3.ZERO:target.subtract(previousBase);
        current=target;
        float turn=net.minecraft.util.Mth.wrapDegrees(desiredYaw-facing);
        turn=(float)Math.clamp(turn*.22,-1.8,1.8);facing+=turn;
        double clearance=action.equals("board")||action.equals("settle")?0:
            action.equals("arrive")?Math.max(0,target.y-destination.y()):action.equals("depart")?Math.max(0,target.y-origin.y()):14;
        BirdRig.Frame frame=bird.animate(target,facing,age,action,clearance,velocity.horizontalDistance(),velocity.y,turn,progress);
        checkPose(target);
        previousBase=target;
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
    private void hint(String message){player.sendOverlayMessage(net.minecraft.network.chat.Component.literal(message));}
    private void checkPose(Vec3 target) {
        var bounds=bird.bounds(target);double floor=previousBase==null?target.y:Math.min(previousBase.y,target.y);
        if(!FlightSpace.clear(level,previousBounds,bounds,floor))throw new IllegalStateException("The bird's path became obstructed");
        previousBounds=bounds;
    }
    private void setFade(int value){setFade(value,false);}
    private void setFade(int value,boolean force) {
        if(value==fade&&!force)return;
        if(fade>0)player.removePostEffect(Identifier.parse("oak_aviary:fade_"+fade));
        fade=value;if(fade>0)player.addPostEffect(Identifier.parse("oak_aviary:fade_"+fade));player.sendPostEffects();
    }
    void abort(String message){if(!closed)finish(transferred?destination:origin,message);}
    private void finish(AviaryStore.Port port,String message) {
        closed=true;
        if(saddle!=null){saddle.discard();saddle=null;}
        if(delivered) {
            if(bird!=null)bird.close();
            journal.whenComplete((v,e)->app.server.execute(()->{releaseTickets();if(e==null)app.released(player.getUUID());else {app.journeys.remove(player.getUUID());System.err.println("Oak Aviary recovery cleanup failed: "+e);}}));return;
        }
        if(!boarded) {
            if(bird!=null)bird.close();if(carrier!=null)carrier.discard();if(camera!=null)camera.discard();
            if(journal==null){releaseTickets();app.released(player.getUUID());}
            else app.store.complete(player.getUUID()).whenComplete((v,e)->app.server.execute(()->{releaseTickets();if(e==null)app.released(player.getUUID());else {app.journeys.remove(player.getUUID());System.err.println("Oak Aviary recovery cleanup failed: "+e);}}));
            if(!player.hasDisconnected()){hint("");Aviary.tell(player,message);}return;
        }
        Aviary.restoreCamera(player);player.stopRiding();
        if(passenger!=null){passenger.close();passenger=null;}
        // The origin remains ticketed throughout travel, including recovery writes.
        Vec3 safe=boardingSpot(level,port);if(safe==null)safe=safeLanding(level,port.x(),port.y(),port.z());
        if(safe==null)safe=safeLanding(level,origin.x(),origin.y(),origin.z());
        if(safe!=null)player.teleportTo(level,safe.x,safe.y,safe.z,Set.of(),port.yaw(),0,true);
        player.resetFallDistance();player.setLastClientInput(Input.EMPTY);
        if(bird!=null)bird.close();if(carrier!=null)carrier.discard();if(camera!=null)camera.discard();
        if(safe==null){Aviary.tell(player,"No safe landing area is available. Rejoin after an administrator clears an aviport.");player.connection.disconnect(net.minecraft.network.chat.Component.literal("No safe landing area. Ask an administrator to clear an aviport before rejoining."));app.journeys.remove(player.getUUID());releaseTickets();return;}
        app.store.complete(player.getUUID()).whenComplete((v,e)->app.server.execute(()->{releaseTickets();if(e==null)app.released(player.getUUID());else {app.journeys.remove(player.getUUID());System.err.println("Oak Aviary recovery cleanup failed: "+e);}}));
        if(!player.hasDisconnected())Aviary.tell(player,message);
    }
}
