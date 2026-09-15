package me.oak.aviary;

import net.minecraft.server.level.ServerPlayer;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.entity.player.Input;
import java.util.*;

/** Lifecycle and native menus; a saved bird never keeps an offline area loaded. */
final class Companions {
    private final Aviary app;
    private final Map<UUID,RoamingBird> birds=new LinkedHashMap<>();
    private final Map<UUID,Long> retry=new HashMap<>();
    private long tick;
    Companions(Aviary app){this.app=app;}
    int mountedCount(){return (int)birds.values().stream().filter(RoamingBird::mounted).count();}
    boolean mounted(UUID id){var b=birds.get(id);return b!=null&&b.mounted();}
    boolean active(UUID id){return birds.containsKey(id);}
    boolean active(){return !birds.isEmpty();}
    void input(UUID id,Input value){var bird=birds.get(id);if(bird!=null)bird.input(value);}
    boolean interact(ServerPlayer player,Entity entity){var bird=birds.get(player.getUUID());if(bird==null||!bird.owns(entity))return false;bird.board();return true;}
    void remove(UUID id,RoamingBird bird){birds.remove(id,bird);}
    void cooldown(UUID id){retry.put(id,tick+600);}
    void suspend(UUID id){var bird=birds.get(id);if(bird!=null&&!bird.mounted())bird.close();}
    void disconnect(UUID id){var bird=birds.get(id);if(bird!=null)bird.abort("");retry.remove(id);}
    void stop(){for(var bird:List.copyOf(birds.values()))bird.abort("Travel stopped. The server is restarting.");}
    void land(UUID id){var bird=birds.get(id);if(bird!=null)bird.requestLanding();}
    void hurt(UUID id){var bird=birds.get(id);if(bird!=null){cooldown(id);bird.abort(bird.mounted()?"Flight interrupted.":"Your bird left. Call again when you are safe.");}}
    int menu(ServerPlayer p){
        if(!app.javaPlayer(p))return 0;
        if(mounted(p.getUUID()))return app.dialog(p,"Your bird","",List.of(app.action("Land","Find a safe landing","/aviary bird land")));
        var buttons=new ArrayList<net.minecraft.server.dialog.ActionButton>();
        String mode=app.store.companion(p.getUUID()).mode();
        buttons.add(app.action("Call bird","Bring the saddle nearby","/aviary bird call"));
        buttons.add(app.action(mode.equals("follow")?"Following":"Follow","Stay nearby as you explore","/aviary bird follow"));
        buttons.add(app.action(mode.equals("stay")?"Waiting":"Stay","Wait at a safe landing","/aviary bird stay"));
        buttons.add(app.action("Return","Return to home or the last landing","/aviary bird return"));
        buttons.add(app.action("Back","Destinations","/aviary"));
        return app.dialog(p,"Companion","",buttons);
    }
    int command(ServerPlayer p,String action){
        try {
            if(!Set.of("call","follow","stay","return","land").contains(action))return 0;
            if(!app.javaPlayer(p)||!app.loaded(p))throw new IllegalArgumentException("Load the resource pack to call your bird.");
            if(!app.store.settings.enabled()||!app.store.error.isEmpty())throw new IllegalArgumentException("Aviary is unavailable.");
            if(app.journeys.containsKey(p.getUUID())||app.store.recoveries.containsKey(p.getUUID())&&!active(p.getUUID()))throw new IllegalArgumentException("Finish your current trip first.");
            if(p.level()!=app.server.overworld()||p.isSpectator()||p.isSleeping()||!p.isAlive())throw new IllegalArgumentException("Call your bird from the Overworld.");
            if(mounted(p.getUUID())&&!action.equals("land"))throw new IllegalArgumentException("Land before changing companion behavior.");
            var bird=birds.get(p.getUUID());
            if(bird!=null){bird.command(action);return 1;}
            if(action.equals("land"))return 0;
            if(action.equals("return")){var old=app.store.companion(p.getUUID());app.store.companion(p.getUUID(),new AviaryStore.Companion("off",old.x(),old.y(),old.z(),old.yaw()));return 1;}
            if(!p.onGround()||p.isPassenger())throw new IllegalArgumentException("Stand on open ground to call your bird.");
            var profile=new AviaryStore.Companion(action.equals("stay")?"stay":"follow",p.getX(),p.getY(),p.getZ(),p.getYRot());
            app.store.companion(p.getUUID(),profile);retry.remove(p.getUUID());birds.put(p.getUUID(),new RoamingBird(app,this,p,profile));
            p.sendOverlayMessage(net.minecraft.network.chat.Component.literal("Your bird is on its way"));return 1;
        }catch(Exception e){Aviary.tell(p,e instanceof IllegalArgumentException?e.getMessage():"Could not call your bird. Try again.");return 0;}
    }
    void tick(){
        tick++;
        for(var bird:List.copyOf(birds.values())){
            if(app.journeys.containsKey(bird.player.getUUID())&&!bird.mounted()){bird.close();continue;}
            try{bird.tick();}catch(Exception e){app.diagnostics.failed("companion",e.getMessage());cooldown(bird.player.getUUID());bird.abort(bird.mounted()?"Flight stopped. Returning to safe ground.":"Your bird needs more space. Call again from open ground.");System.err.println("Oak Aviary companion failed: "+e);}
        }
        if(tick%20!=0||!app.store.settings.enabled()||!app.store.error.isEmpty())return;
        for(var p:app.server.getPlayerList().getPlayers()){
            var id=p.getUUID();
            if(active(id)||app.journeys.containsKey(id)||app.queued(id)||app.store.recoveries.containsKey(id)||retry.getOrDefault(id,0L)>tick||!app.javaPlayer(p)||!app.loaded(p)||!p.isAlive()||p.isSpectator()||p.isPassenger()||p.level()!=app.server.overworld())continue;
            var profile=app.store.companion(id);if(profile.mode().equals("off"))continue;
            if(profile.mode().equals("follow")){
                if(!p.onGround()||!p.level().canSeeSky(p.blockPosition().above()))continue;
                profile=new AviaryStore.Companion("follow",p.getX(),p.getY(),p.getZ(),p.getYRot());
            }else if(p.position().distanceTo(new net.minecraft.world.phys.Vec3(profile.x(),profile.y(),profile.z()))>64)continue;
            birds.put(id,new RoamingBird(app,this,p,profile));
        }
    }
    com.google.gson.JsonArray status(){
        var result=new com.google.gson.JsonArray();for(var bird:birds.values()){
            var value=new com.google.gson.JsonObject();value.addProperty("player",bird.player.getGameProfile().name());value.addProperty("phase",bird.phase());value.addProperty("riding",bird.mounted());value.addProperty("x",bird.position().x);value.addProperty("y",bird.position().y);value.addProperty("z",bird.position().z);result.add(value);
        }return result;
    }
}
