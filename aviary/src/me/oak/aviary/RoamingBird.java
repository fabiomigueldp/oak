package me.oak.aviary;

import net.minecraft.core.BlockPos;
import net.minecraft.network.chat.Component;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.world.entity.*;
import net.minecraft.world.entity.decoration.ArmorStand;
import net.minecraft.world.entity.player.Input;
import net.minecraft.world.phys.*;
import net.minecraft.util.Mth;
import me.oak.aviary.mixin.InteractionAccess;
import java.util.*;
import java.util.concurrent.CompletableFuture;

/** One owner's bird, from ground companion to manual flight. No detached camera. */
final class RoamingBird implements AutoCloseable {
    final ServerPlayer player;
    private final Aviary app;
    private final Companions manager;
    private BirdRig bird;
    private ArmorStand carrier;
    private Interaction saddle;
    private SteeringMotor motor;
    private LandingSearch initial;
    private AirLandingSearch landingSearch;
    private AviaryStore.Port ground,origin;
    private FlightScene scene;
    private FlightSpace.Check check;
    private RoamingTickets tickets;
    private CompletableFuture<Void> journal;
    private Map<String,AABB> previous=Map.of();
    private Input input=Input.EMPTY;
    private String phase="search",afterScene="rest",mode;
    private int age,phaseAge,sceneTick,shiftTicks,idleTicks,searchAfter,blockedTicks;
    private Vec3 lastOwner,followTarget;
    private boolean closed,protectedRide,boarded,landingRequested,landCommitted,summoned;
    private float facing,visualHeading;

    RoamingBird(Aviary app,Companions manager,ServerPlayer player,AviaryStore.Companion profile){
        this.app=app;this.manager=manager;this.player=player;mode=profile.mode();facing=profile.yaw();lastOwner=player.position();
        var center=new AviaryStore.Port("roaming","Companion","minecraft:overworld",profile.x(),profile.y(),profile.z(),facing,player.getUUID().toString(),false);
        if(mode.equals("stay")&&player.position().distanceTo(Journey.position(center))>2&&Journey.placementIssue(player.level(),center).isEmpty())ground=center;
        else initial=new LandingSearch(player.level(),center);
    }
    boolean mounted(){return protectedRide;}
    boolean resting(){return phase.equals("rest");}
    boolean owns(Entity target){return target==saddle||target==carrier;}
    String phase(){return phase;}
    Vec3 position(){return motor==null?player.position():motor.position;}
    void input(Input value){input=value;}
    void command(String value){
        if(protectedRide){if(value.equals("land"))requestLanding();return;}
        if(value.equals("call")){
            summoned=true;landingRequested=true;
            if(resting()&&player.position().distanceTo(position())>4.5){removeSaddle();plan(FlightScene.exit(position(),facing,7),"depart","follow");}
            else if(resting())hint("Use the saddle to board");return;
        }
        mode=value;
        if(value.equals("stay")){summoned=false;landingRequested=true;save("stay",ground==null?position():Journey.position(ground));hint(resting()?"Your bird will wait here":"Finding a place to wait");}
        if(value.equals("follow")){save("follow",ground==null?player.position():Journey.position(ground));if(!resting())landingRequested=false;hint("Your bird will follow");}
        if(value.equals("return")){
            var home=app.store.ports.get(app.store.preferences(player.getUUID()).home());
            Vec3 target=home!=null&&app.accessible(home,player)?Journey.position(home):ground==null?position():Journey.position(ground);
            save("stay",target);afterScene="dismiss";
            if(resting()&&position().distanceTo(target)<4){mode="stay";rest();hint("Your bird will wait here");return;}
            if(bird==null){close();return;}
            if(resting())plan(FlightScene.exit(position(),facing,7),"depart","dismiss");else next("dismiss");
            hint("Your bird is returning");
        }
    }
    boolean board(){
        if(!resting()||player.position().distanceTo(position())>4.5||player.isPassenger()||player.isSleeping()||player.isSpectator()||!player.isAlive())return false;
        if(!app.loaded(player)||!app.store.settings.enabled()||!app.store.error.isEmpty())return false;
        if(app.activeFlights()>=app.store.settings.maxFlights()){hint("Other birds are flying. Try again shortly.");return false;}
        if(!Journey.clearPort(player.level(),ground)){hint("Clear space beside the bird before boarding");return false;}
        origin=ground;landCommitted=false;protectedRide=true;app.protect(player.getUUID());
        try {
            tickets=new RoamingTickets(player.level(),Journey.position(origin));tickets.update(position(),position());
            journal=app.store.journal(recovery(origin,false));next("securing");app.diagnostics.called();return true;
        }catch(Exception e){abort("Could not prepare the flight. Try again.");return false;}
    }
    void requestLanding(){
        if(!boarded||Set.of("landing","release","land-journal","land-align","land-check","land-verify").contains(phase))return;
        landingRequested=true;searchAfter=0;hint("Finding a landing spot");
    }
    private void next(String value){phase=value;phaseAge=0;}
    private void plan(FlightScene path,String action,String next){scene=path;check=new FlightSpace.Check(player.level(),path,action);afterScene=next;this.next(action.equals("arrive")?"land-check":"depart-check");}
    void tick(){
        if(closed)return;age++;phaseAge++;
        if(player.hasDisconnected()||!player.isAlive()||player.level()!=app.server.overworld()||!app.loaded(player)||!app.store.settings.enabled()){abort("");return;}
        if(protectedRide){player.resetFallDistance();player.setLastClientInput(Input.EMPTY);if(input.shift()){if(++shiftTicks==7)requestLanding();}else shiftTicks=0;}
        if(!protectedRide&&position().distanceTo(player.position())>96){close();return;}
        if(phase.equals("search")){
            if(phaseAge>200){hint("No space nearby. Call from open ground.");manager.cooldown(player.getUUID());close();return;}
            if(ground==null){if(!initial.step(app.planningDeadline))return;ground=initial.result();if(ground==null){hint("No space nearby. Call from open ground.");manager.cooldown(player.getUUID());close();return;}}
            if(check==null){scene=FlightScene.entry(Journey.position(ground),ground.yaw(),7);check=new FlightSpace.Check(player.level(),scene,"call");}
            if(!check.step(app.planningDeadline))return;
            if(!check.valid()){ground=null;check=null;if(initial==null)initial=new LandingSearch(player.level(),port(player.position()));return;}
            motor=new SteeringMotor(scene.a,facing);bird=new BirdRig(player.level(),scene.a);
            bird.personality(new BirdTraits(player.getUUID().toString(),"companion","Condor"));
            bird.setPlumage(switch(Math.floorMod(player.getUUID().toString().hashCode(),3)){case 1->"ash";case 2->"amber";default->"default";});
            sceneTick=0;previous=Map.of();next("approach");FlightSound.play(player,"reply",position(),.5f,1);return;
        }
        if(bird==null)return;
        if(tickets!=null&&age%10==0)tickets.update(position(),position().add(Vec3.directionFromRotation(0,facing).scale(32)));
        if(phase.equals("approach")||phase.equals("landing")||phase.equals("depart")){
            String action=phase.equals("depart")?"depart":phase.equals("approach")?"call":"arrive";
            sceneTick=Math.min(scene.ticks,sceneTick+1);Vec3 target=scene.at(sceneTick);
            facing=FlightSpace.turn(facing,scene.heading(sceneTick+5,facing));
            double height=action.equals("depart")?Math.max(0,target.y-scene.a.y):Math.max(0,target.y-scene.d.y);
            animate(target,action,height,sceneTick/(double)scene.ticks,true);
            if(sceneTick==scene.ticks){
                motor.stop();previous=Map.of();
                if(phase.equals("landing")&&boarded){release();return;}
                if(phase.equals("depart")){next(afterScene);return;}
                rest();
            }
            return;
        }
        if(phase.equals("rest")){
            if(age%5==0)animate(position(),"greet",0,1,true);
            if(age%20==0&&player.position().distanceTo(position())>3)marker();
            if(player.position().distanceTo(lastOwner)<.15)idleTicks++;else idleTicks=0;
            lastOwner=player.position();
            if(age>=searchAfter&&mode.equals("follow")&&player.position().distanceTo(position())>12&&outside()){
                removeSaddle();plan(FlightScene.exit(position(),facing,7),"depart","follow");
            }
            return;
        }
        if(phase.equals("securing")){
            if(phaseAge>100)throw new IllegalStateException("Boarding timed out");
            if(!journal.isDone())return;journal.join();
            carrier=new ArmorStand(EntityTypes.ARMOR_STAND,player.level());carrier.addTag("oak_aviary_temporary");carrier.setInvisible(true);carrier.setNoGravity(true);carrier.setSilent(true);carrier.setNoBasePlate(true);carrier.setPermanentlyInvulnerable(true);carrier.setRequiresPrecisePosition(true);carrier.setPos(position());
            if(!player.level().addFreshEntity(carrier))throw new IllegalStateException("Could not create saddle");
            player.teleportTo(player.level(),position().x,position().y,position().z,Set.of(),facing,0,true);
            if(!player.startRiding(carrier,true,true))throw new IllegalStateException("Could not board");
            boarded=true;removeSaddle();landingRequested=false;Aviary.restoreCamera(player);
            player.sendOverlayMessage(Component.literal("Steer with your view · ").append(Component.keybind("key.forward")).append(" speed · ").append(Component.keybind("key.jump")).append(" climb · ").append(Component.keybind("key.sneak")).append(" land"));
            FlightSound.play(player,"saddle",position(),.55f,1);next("mounted");return;
        }
        if(phase.equals("mounted")){
            animate(position(),"greet",0,1,true);
            if(phaseAge>10&&(input.jump()||input.forward()))plan(FlightScene.exit(position(),facing,7),"depart","pilot");
            if(input.shift()&&phaseAge>10)release();return;
        }
        if(phase.equals("depart-check")||phase.equals("land-check")){
            if(!check.step(app.planningDeadline))return;
            if(!check.valid()){
                if(boarded&&phase.equals("depart-check")){hint("Clear space above the bird to take off");next("mounted");}
                else if(phase.equals("depart-check")){if(afterScene.equals("dismiss")){close();return;}rest();searchAfter=age+100;}
                else {landingSearch=null;searchAfter=age+60;next(boarded?"pilot":"follow");}
                return;
            }
            sceneTick=0;previous=Map.of();
            if(phase.equals("land-check"))next("land-align");else next("depart");return;
        }
        if(phase.equals("land-journal")){
            if(phaseAge>100)throw new IllegalStateException("Landing preparation timed out");
            if(!journal.isDone())return;journal.join();landCommitted=true;sceneTick=0;previous=Map.of();next("landing");return;
        }
        if(phase.equals("land-verify")){
            if(!check.step(app.planningDeadline))return;
            if(!check.valid()){landingSearch=null;searchAfter=age+60;next(boarded?"pilot":"follow");return;}
            if(boarded){journal=app.store.journal(recovery(ground,true));next("land-journal");}else next("landing");
            return;
        }
        if(phase.equals("release")){
            if(phaseAge>100)throw new IllegalStateException("Landing completion timed out");
            if(!journal.isDone())return;journal.join();app.unprotect(player.getUUID());protectedRide=false;tickets.close();tickets=null;origin=null;rest();return;
        }
        if(phase.equals("land-align")){
            if(phaseAge>400){landingRequested=false;landingSearch=null;searchAfter=age+100;next(boarded?"pilot":"follow");hint("Approach blocked. Choose another landing area.");return;}
            move(motor.pursue(scene.a,.5),true);
            if(position().distanceTo(scene.a)<.2&&motor.velocity.length()<.08){
                // Terrain may have changed while navigating to the approach.
                motor.position=scene.a;motor.stop();sceneTick=0;previous=Map.of();
                check=new FlightSpace.Check(player.level(),scene,"arrive");next("land-verify");
            }
            return;
        }
        if(phase.equals("dismiss")){
            move(motor.steer(Vec3.directionFromRotation(-20,facing).scale(.55)),true);
            if(phaseAge>80||position().distanceTo(player.position())>40)close();return;
        }
        if(!phase.equals("pilot")&&!phase.equals("follow"))return;
        if(phase.equals("follow")){
            if(player.position().distanceTo(lastOwner)<.12)idleTicks++;else idleTicks=0;
            lastOwner=player.position();
            if(idleTicks>60||mode.equals("stay"))landingRequested=true;
            if(age%10==0&&outside()){
                Vec3 forward=Vec3.directionFromRotation(0,player.getYRot());
                Vec3 desired=player.position().subtract(forward.scale(7)).add(forward.z*6,9,-forward.x*6);
                if(followTarget==null||followTarget.distanceTo(desired)>4)followTarget=desired;
            }
            move(motor.pursue(followTarget==null?position():followTarget,.6),true);
        }else {
            float yaw=player.getYRot()+(input.left()?-22:0)+(input.right()?22:0),pitch=Math.clamp(player.getXRot(),-45,50);
            double speed=input.backward()?.13:input.forward()?(input.sprint()?1.0:.75):.38;
            Vec3 desired=Vec3.directionFromRotation(pitch,yaw).scale(speed);
            if(input.jump())desired=new Vec3(desired.x,.32,desired.z);
            if(landingRequested)desired=desired.scale(.3);
            move(motor.steer(desired),false);
        }
        if(landingRequested&&age>=searchAfter){
            if(landingSearch==null)landingSearch=new AirLandingSearch(player.level(),boarded?position().add(motor.velocity.scale(12)):mode.equals("stay")&&!summoned?position():player.position(),facing);
            if(landingSearch.step(app.planningDeadline)){
                if(landingSearch.result!=null){ground=landingSearch.result;scene=landingSearch.scene;landingSearch=null;next("land-align");if(boarded)hint("Landing");}
                else {landingSearch=null;searchAfter=age+100;if(boarded){landingRequested=false;hint("No safe landing below. Fly toward open ground.");}}
            }
        }
    }
    private boolean outside(){return player.level().canSeeSky(player.blockPosition().above());}
    private boolean clear(Vec3 from,Vec3 to){
        // Includes articulated wing tips, bank, heave and the native rider.
        AABB box=new AABB(from.x-4,from.y-.4,from.z-4,from.x+4,from.y+5,from.z+4);
        return FlightSpace.clear(player.level(),Map.of("body",box),Map.of("body",box.move(to.subtract(from))),Math.min(from.y,to.y));
    }
    private void move(Vec3 velocity,boolean avoid){
        double braking=velocity.lengthSqr()/(2*.035)+velocity.length()*5+1;
        Vec3 ahead=velocity.length()<.001?position():position().add(velocity.normalize().scale(braking));
        if(!clear(position(),ahead)){
            velocity=motor.steer(Vec3.ZERO);blockedTicks++;
            if(avoid&&blockedTicks>8){
                for(float turn:new float[]{45,-45,90,-90,180}){
                    Vec3 escape=Vec3.directionFromRotation(-25,facing+turn).scale(.25);
                    if(clear(position(),position().add(escape.scale(20)))){velocity=motor.steer(escape);break;}
                }
            }
            if(boarded&&blockedTicks==10)hint("Slowing for terrain");
        }else blockedTicks=0;
        if(!clear(position(),position().add(velocity))){motor.stop();velocity=Vec3.ZERO;}
        motor.accept(velocity);facing=motor.yaw;
        animate(position(),velocity.y>.08?"depart":"cruise",14,1,false);
    }
    private void animate(Vec3 target,String action,double height,double progress,boolean checked){
        Vec3 velocity=target.subtract(motor.position);if(!checked)velocity=motor.velocity;
        float turn=Mth.wrapDegrees(facing-visualHeading);visualHeading=facing;
        var frame=bird.animate(target,facing,age,action,height,velocity.horizontalDistance(),velocity.y,turn,progress);
        if(checked){var bounds=bird.bounds(target);if(!FlightSpace.clear(player.level(),previous,bounds,target.y))throw new IllegalStateException("The bird's path became obstructed");previous=bounds;}
        motor.position=target;motor.yaw=facing;
        if(carrier!=null){carrier.setPos(frame.position());carrier.setYRot(facing);carrier.setYHeadRot(facing);if(boarded){carrier.positionRider(player);if(!player.isPassenger()&&!player.startRiding(carrier,true,true))throw new IllegalStateException("Saddle detached");}}
        if(frame.downstroke()&&boarded)FlightSound.play(player,velocity.y>.08?"wing_power":"wing_glide",target,.25f,1);
    }
    private void rest(){
        next("rest");landingRequested=false;summoned=false;idleTicks=0;motor.stop();previous=Map.of();
        save(mode.equals("follow")?"follow":"stay",position());
        if(saddle==null){saddle=new Interaction(EntityTypes.INTERACTION,player.level());saddle.addTag("oak_aviary_temporary");saddle.setNoGravity(true);saddle.setPos(position().add(0,1.1,0));var access=(InteractionAccess)saddle;access.aviary$width(1.7f);access.aviary$height(1.1f);access.aviary$response(false);if(!player.level().addFreshEntity(saddle))throw new IllegalStateException("Could not create saddle interaction");}
        marker();
    }
    private void release(){
        Vec3 safe=Journey.boardingSpot(player.level(),ground);
        if(safe==null)throw new IllegalStateException("Landing ground changed");
        player.stopRiding();Aviary.restoreCamera(player);player.teleportTo(player.level(),safe.x,safe.y,safe.z,Set.of(),player.getYRot(),player.getXRot(),true);player.resetFallDistance();boarded=false;
        if(carrier!=null){carrier.discard();carrier=null;}
        journal=app.store.complete(player.getUUID());next("release");app.diagnostics.completed();hint("");
    }
    private AviaryStore.Recovery recovery(AviaryStore.Port destination,boolean committed){return new AviaryStore.Recovery(player.getUUID().toString(),"minecraft:overworld",origin.x(),origin.y(),origin.z(),origin.yaw(),"minecraft:overworld",destination.x(),destination.y(),destination.z(),destination.yaw(),committed);}
    private AviaryStore.Port port(Vec3 at){return new AviaryStore.Port("roaming","Companion","minecraft:overworld",at.x,at.y,at.z,facing,player.getUUID().toString(),false);}
    private void save(String mode,Vec3 at){try{app.store.companion(player.getUUID(),new AviaryStore.Companion(mode,at.x,at.y,at.z,facing));}catch(Exception e){throw new IllegalStateException("Could not save companion state",e);}}
    private void marker(){
        if(ground==null)return;Vec3 at=Journey.boardingSpot(player.level(),ground);if(at==null)return;
        for(int i=0;i<8;i++){double a=i*Math.PI/4;player.level().sendParticles(player,new net.minecraft.core.particles.DustParticleOptions(0xb6d6a3,.65f),false,false,at.x+Math.cos(a)*.4,at.y+.08,at.z+Math.sin(a)*.4,1,0,0,0,0);}
    }
    private void removeSaddle(){if(saddle!=null){saddle.discard();saddle=null;}}
    private void hint(String text){if(!player.hasDisconnected())player.sendOverlayMessage(Component.literal(text));}
    void abort(String message){
        if(closed)return;
        if(protectedRide&&origin!=null&&!player.hasDisconnected()){
            var level=app.server.overworld();var target=landCommitted?ground:origin;Vec3 safe=Journey.safeLanding(level,target.x(),target.y(),target.z());
            player.stopRiding();Aviary.restoreCamera(player);
            if(safe!=null){player.teleportTo(level,safe.x,safe.y,safe.z,Set.of(),player.getYRot(),0,true);player.resetFallDistance();app.store.complete(player.getUUID()).whenComplete((v,e)->{if(e!=null)System.err.println("Aviary companion recovery retained: "+e);});}
            else player.connection.disconnect(Component.literal("No safe landing. Ask an administrator to clear the departure area."));
        }
        hint(message);close();
    }
    public void close(){
        if(closed)return;closed=true;removeSaddle();if(bird!=null)bird.close();if(carrier!=null)carrier.discard();if(tickets!=null)tickets.close();
        if(protectedRide)app.unprotect(player.getUUID());manager.remove(player.getUUID(),this);
    }
}
