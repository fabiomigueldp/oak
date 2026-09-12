package me.oak.aviary;

import com.mojang.math.Transformation;
import me.oak.aviary.mixin.DisplayAccess;
import me.oak.aviary.mixin.InteractionAccess;
import net.fabricmc.fabric.api.event.player.*;
import net.minecraft.core.BlockPos;
import net.minecraft.core.Holder;
import net.minecraft.core.Direction;
import net.minecraft.core.component.DataComponents;
import net.minecraft.core.particles.DustParticleOptions;
import net.minecraft.core.registries.Registries;
import net.minecraft.nbt.CompoundTag;
import net.minecraft.network.chat.Component;
import net.minecraft.network.protocol.game.ClientboundSoundPacket;
import net.minecraft.resources.Identifier;
import net.minecraft.resources.ResourceKey;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.server.permissions.Permissions;
import net.minecraft.sounds.SoundEvents;
import net.minecraft.sounds.SoundEvent;
import net.minecraft.sounds.SoundSource;
import net.minecraft.world.InteractionHand;
import net.minecraft.world.InteractionResult;
import net.minecraft.world.entity.Display;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.entity.EntityTypes;
import net.minecraft.world.entity.Interaction;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.item.Items;
import net.minecraft.world.item.component.CustomData;
import net.minecraft.world.level.block.Blocks;
import net.minecraft.world.phys.BlockHitResult;
import net.minecraft.world.phys.AABB;
import net.minecraft.world.phys.HitResult;
import net.minecraft.world.phys.Vec3;
import org.joml.Quaternionf;
import org.joml.Vector3f;
import java.util.*;

/** Crafted vanilla items and bounded, rebuildable destination scenery. */
public final class Perches {
    private static final String MARKER="oak_aviary";
    private final Aviary app;
    private final Map<String,Scenery> scenery=new HashMap<>();
    private final Map<UUID,String> targets=new HashMap<>();
    private final Map<UUID,Long> used=new HashMap<>();
    private final Map<UUID,String> previewMessages=new HashMap<>();
    private long ticks;
    private long nextSupportSave;
    private long supportErrorNotice=-1200;
    private record Scenery(List<Entity> entities,String signature) {}

    Perches(Aviary app){this.app=app;}
    public static boolean reserved(ItemStack item){var data=item.get(DataComponents.CUSTOM_DATA);return data!=null&&data.copyTag().contains(MARKER);}
    static String kind(ItemStack item){return item.getItem()==Items.DISC_FRAGMENT_5?item.getOrDefault(DataComponents.CUSTOM_DATA,CustomData.EMPTY).copyTag().getStringOr(MARKER,""):"";}
    static ItemStack item(String kind,String port){
        ItemStack result=new ItemStack(Items.DISC_FRAGMENT_5);
        CompoundTag tag=new CompoundTag();tag.putString(MARKER,kind);if(port!=null)tag.putString("port",port);
        result.set(DataComponents.CUSTOM_DATA,CustomData.of(tag));result.set(DataComponents.MAX_STACK_SIZE,1);
        result.set(DataComponents.ITEM_MODEL,Identifier.parse("oak_aviary:"+kind));
        result.set(DataComponents.ITEM_NAME,Component.literal(kind.equals("perch")?"Perch":"Whistle"));return result;
    }
    void install(){
        UseItemCallback.EVENT.register((actor,world,hand)->{
            if(!(actor instanceof ServerPlayer p)||!reserved(p.getItemInHand(hand)))return InteractionResult.PASS;
            if(!ready(p))return InteractionResult.FAIL;
            if(kind(p.getItemInHand(hand)).equals("whistle")){whistle(p);return InteractionResult.SUCCESS;}
            if(kind(p.getItemInHand(hand)).equals("perch"))hint(p,"Use the top of a solid block.");
            return InteractionResult.FAIL;
        });
        UseBlockCallback.EVENT.register((actor,world,hand,hit)->{
            if(!(actor instanceof ServerPlayer p)||app.store==null)return InteractionResult.PASS;
            ItemStack stack=p.getItemInHand(hand);String kind=kind(stack);
            if(!kind.isEmpty()){
                if(!ready(p))return InteractionResult.FAIL;
                if(kind.equals("whistle")){whistle(p);return InteractionResult.SUCCESS;}
                if(hand!=InteractionHand.MAIN_HAND)return InteractionResult.FAIL;
                return place(p,stack,hit)?InteractionResult.SUCCESS:InteractionResult.FAIL;
            }
            String port=at(p.level(),hit.getBlockPos());
            if(port!=null&&ready(p)){interact(p,port,stack);return InteractionResult.SUCCESS;}
            return InteractionResult.PASS;
        });
        UseEntityCallback.EVENT.register((actor,world,hand,target,hit)->{
            if(!(actor instanceof ServerPlayer p)||app.store==null)return InteractionResult.PASS;
            String id=targets.get(target.getUUID());
            if(id!=null){if(ready(p))interact(p,id,p.getItemInHand(hand));return InteractionResult.SUCCESS;}
            Journey flight=app.journeys.get(p.getUUID());
            if(flight!=null&&flight.acceptsBoarding(target)){
                var held=p.getItemInHand(hand);
                if(held.is(Items.COOKED_CHICKEN))flight.feed(held);else flight.board();
                return InteractionResult.SUCCESS;
            }
            if(reserved(p.getItemInHand(hand))){if(ready(p)&&kind(p.getItemInHand(hand)).equals("whistle"))whistle(p);return InteractionResult.FAIL;}
            return InteractionResult.PASS;
        });
        AttackEntityCallback.EVENT.register((actor,world,hand,target,hit)->{
            if(!(actor instanceof ServerPlayer p))return InteractionResult.PASS;
            String id=targets.get(target.getUUID());
            if(id==null)return InteractionResult.PASS;
            if(ready(p)){hint(p,"Use the perch to manage it.");app.perchMenu(p,id);}return InteractionResult.FAIL;
        });
        PlayerBlockBreakEvents.BEFORE.register((world,actor,pos,state,entity)->{
            if(!(actor instanceof ServerPlayer p)||app.store==null)return true;
            String id=at(p.level(),pos);
            if(id==null||!app.busyPort(id))return true;
            hint(p,"A bird is using this perch.");return false;
        });
    }
    private boolean ready(ServerPlayer p){
        if(app.store==null||!app.store.settings.enabled()||!app.store.error.isEmpty()){hint(p,"Aviary is unavailable.");return false;}
        if(!app.javaPlayer(p)){hint(p,"Aviary requires Java Edition.");return false;}
        if(!app.loaded(p)){app.menu(p);return false;}
        if(p.isSpectator()){hint(p,"Return to the game to use Aviary.");return false;}
        return true;
    }
    void joined(ServerPlayer player){
        if(!app.javaPlayer(player))return;
        player.awardRecipesByKey(List.of(ResourceKey.create(Registries.RECIPE,Identifier.parse("oak_aviary:perch")),ResourceKey.create(Registries.RECIPE,Identifier.parse("oak_aviary:whistle"))));
    }
    private void whistle(ServerPlayer p){
        if(used.getOrDefault(p.getUUID(),-100L)+10>ticks)return;
        used.put(p.getUUID(),ticks);
        FlightSound.play(p,"whistle",p.position(),.6f,1);app.menu(p);
    }
    private static boolean owner(ServerPlayer player,AviaryStore.Port port){return port.owner().equals(player.getUUID().toString())||player.permissions().hasPermission(Permissions.COMMANDS_GAMEMASTER);}
    private void interact(ServerPlayer p,String id,ItemStack held){
        var port=app.store.ports.get(id);if(port==null)return;
        var perch=app.store.perches.get(id);var dye=held.get(DataComponents.DYE);
        if(dye!=null&&perch!=null&&owner(p,port)){
            if(app.busyPort(id)){hint(p,"A bird is using this perch.");return;}
            var next=new AviaryStore.PerchData(perch.x(),perch.y(),perch.z(),dye.getName(),perch.style(),perch.birdName(),perch.guests(),perch.hub(),perch.active());
            app.store.perches.put(id,next);
            try{app.store.savePolicy();}
            catch(Exception e){app.store.perches.put(id,perch);hint(p,"Could not save the color.");return;}
            held.consume(1,p);update(id);sound(p,SoundEvents.DYE_USE,.6f,1f);
            return;
        }
        app.perchMenu(p,id);
    }
    private AviaryStore.Port candidate(ServerPlayer p,BlockPos support,String id,AviaryStore.Port old){
        float heading=Math.round(p.getYRot()/45f)*45f;heading=((heading+180)%360+360)%360-180;
        return new AviaryStore.Port(id,old==null?"New perch":old.name(),"minecraft:overworld",support.getX()+.5,support.getY()+1.75,support.getZ()+.5,heading,old==null?p.getUUID().toString():old.owner(),old!=null&&old.shared(),old==null?null:old.departureYaw(),old==null?null:old.arrivalYaw());
    }
    private boolean place(ServerPlayer p,ItemStack stack,BlockHitResult hit){
        if(app.journeys.containsKey(p.getUUID())){hint(p,"Finish this trip first.");return false;}
        if(hit.getDirection()!=Direction.UP){hint(p,"Use the top of a solid block.");return false;}
        if(!p.level().dimension().identifier().toString().equals("minecraft:overworld")){hint(p,"Place perches in the Overworld.");return false;}
        var block=hit.getBlockPos();String existingId=stack.getOrDefault(DataComponents.CUSTOM_DATA,CustomData.EMPTY).copyTag().getStringOr("port","");
        String permission=placementPermission(p,block);if(!permission.isEmpty()){hint(p,permission);return false;}
        AviaryStore.Port old=null;
        if(!existingId.isEmpty()){
            old=app.store.ports.get(existingId);var metadata=app.store.perches.get(existingId);
            if(old==null||!owner(p,old)||metadata==null||metadata.active()){hint(p,"This perch has already been placed or belongs to someone else.");return false;}
        }else{
            // Attaching a kit is the only action that relocates a legacy destination.
            old=app.store.ports.values().stream().filter(q->!app.store.perches.containsKey(q.id())&&owner(p,q)&&Journey.position(q).distanceTo(Vec3.atCenterOf(block))<6).findFirst().orElse(null);
        }
        String id=old==null?"p_"+UUID.randomUUID().toString().replace("-","").substring(0,24):old.id();
        if(app.busyPort(id)){hint(p,"A bird is using this perch.");return false;}
        if(old==null&&(app.store.ports.size()>=128||app.store.ports.values().stream().filter(q->q.owner().equals(p.getUUID().toString())).count()>=app.store.network.maxOwnedPerches())){hint(p,"You have reached the perch limit.");return false;}
        var port=candidate(p,block,id,old);
        if(app.store.ports.values().stream().anyMatch(q->!q.id().equals(id)&&q.dimension().equals(port.dimension())&&Journey.position(q).distanceTo(Journey.position(port))<8)){hint(p,"Leave 8 blocks between perches.");return false;}
        String issue=Journey.placementIssue(p.level(),port);if(!issue.isEmpty()){hint(p,issue);return false;}
        var previous=app.store.perches.get(id);
        var metadata=previous==null?new AviaryStore.PerchData(block.getX(),block.getY(),block.getZ(),"green",true):new AviaryStore.PerchData(block.getX(),block.getY(),block.getZ(),previous.color(),previous.style(),previous.birdName(),previous.guests(),previous.hub(),true);
        try{
            AviaryStore.validate(port);app.store.ports.put(id,port);app.store.perches.put(id,metadata);
            app.store.savePolicy();
        }catch(Exception e){
            if(old==null)app.store.ports.remove(id);else app.store.ports.put(id,old);
            if(previous==null)app.store.perches.remove(id);else app.store.perches.put(id,previous);
            hint(p,"Could not place the perch.");return false;
        }
        stack.consume(1,p);update(id);sound(p,SoundEvents.WOOD_PLACE,.6f,.9f);app.perchMenu(p,id);return true;
    }
    boolean move(ServerPlayer p,String id){
        var port=app.store.ports.get(id);var before=app.store.perches.get(id);
        if(port==null||before==null||!owner(p,port))return false;
        if(app.busyPort(id)){hint(p,"A bird is using this perch.");return false;}
        if(!before.active()){hint(p,"Use the packed perch to place it again.");return false;}
        if(p.getInventory().getFreeSlot()<0){hint(p,"Leave one inventory slot free.");return false;}
        // Invalidate the old anchor durably before issuing a pointer. A copied item
        // can never activate the same destination twice: placement requires inactive.
        var after=new AviaryStore.PerchData(before.x(),before.y(),before.z(),before.color(),before.style(),before.birdName(),before.guests(),before.hub(),false);
        app.store.perches.put(id,after);
        try{app.store.savePolicy();}catch(Exception e){app.store.perches.put(id,before);hint(p,"Could not pack the perch.");return false;}
        p.addItem(item("perch",id));update(id);hint(p,"Place the packed perch on a solid block.");return true;
    }
    boolean recover(ServerPlayer p,String id){
        var port=app.store.ports.get(id);var data=app.store.perches.get(id);
        if(port==null||data==null||data.active()||!owner(p,port))return false;
        ItemStack replacement=null;
        for(int slot=0;slot<p.getInventory().getContainerSize();slot++){
            var held=p.getInventory().getItem(slot);if(!kind(held).equals("perch"))continue;
            String pointer=held.getOrDefault(DataComponents.CUSTOM_DATA,CustomData.EMPTY).copyTag().getStringOr("port","");
            if(pointer.equals(id)){hint(p,"The packed perch is already in your inventory.");return false;}
            if(pointer.isEmpty())replacement=held;
        }
        if(replacement==null){hint(p,"Craft a new perch to replace the lost kit.");return false;}
        // Turn one owned generic kit into a relocation pointer in its existing slot.
        var tag=replacement.getOrDefault(DataComponents.CUSTOM_DATA,CustomData.EMPTY).copyTag();tag.putString("port",id);
        replacement.set(DataComponents.CUSTOM_DATA,CustomData.of(tag));hint(p,"Place the packed perch on a solid block.");return true;
    }
    String status(String id){
        var data=app.store.perches.get(id);if(data==null)return "legacy";if(!data.active())return "inactive";
        var level=app.server.overworld();BlockPos support=new BlockPos(data.x(),data.y(),data.z());
        if(!level.hasChunkAt(support))return "unloaded";
        return supportValid(level,support)?"active":"missing";
    }
    static boolean supportValid(ServerLevel level,BlockPos support){
        if(!level.hasChunkAt(support))return false;
        var state=level.getBlockState(support);
        return state.isCollisionShapeFullBlock(level,support)&&level.getFluidState(support).isEmpty()&&!state.is(Blocks.MAGMA_BLOCK);
    }
    private String placementPermission(ServerPlayer player,BlockPos support){
        if(!player.mayBuild())return "You cannot build in this game mode.";
        if(app.server.isUnderSpawnProtection(player.level(),support,player)||!player.mayInteract(player.level(),support)||!player.mayInteract(player.level(),support.above()))return "You cannot build here.";
        return "";
    }
    private String at(ServerLevel level,BlockPos pos){
        if(!level.dimension().identifier().toString().equals("minecraft:overworld"))return null;
        for(var entry:app.store.perches.entrySet()){var d=entry.getValue();if(d.active()&&d.x()==pos.getX()&&d.y()==pos.getY()&&d.z()==pos.getZ())return entry.getKey();}return null;
    }
    void update(String id){remove(id);}
    void tick(){
        ticks++;if(app.store==null)return;
        if(ticks%10==0&&app.store.settings.enabled())for(var p:app.server.getPlayerList().getPlayers())preview(p);
        if(ticks%20!=0)return;
        used.keySet().removeIf(id->app.server.getPlayerList().getPlayer(id)==null);previewMessages.keySet().removeIf(id->app.server.getPlayerList().getPlayer(id)==null);
        retireMissingSupports();
        for(String id:List.copyOf(scenery.keySet()))if(!app.store.perches.containsKey(id)||!app.store.ports.containsKey(id))remove(id);
        for(var entry:app.store.perches.entrySet()){
            String id=entry.getKey();var data=entry.getValue();var port=app.store.ports.get(id);var level=app.server.overworld();
            boolean visible=port!=null&&app.store.settings.enabled()&&status(id).equals("active")&&level.players().stream().anyMatch(p->p.position().distanceToSqr(Journey.position(port))<72*72);
            if(!visible){remove(id);continue;}
            String signature=data.toString()+port.yaw();var old=scenery.get(id);
            if(old!=null&&old.signature().equals(signature)&&old.entities().stream().noneMatch(Entity::isRemoved))continue;
            remove(id);spawn(id,port,data);
        }
    }
    private void retireMissingSupports(){
        if(ticks<nextSupportSave)return;
        var level=app.server.overworld();var previous=new LinkedHashMap<String,AviaryStore.PerchData>();
        for(var entry:app.store.perches.entrySet()){
            var data=entry.getValue();if(!data.active())continue;
            var support=new BlockPos(data.x(),data.y(),data.z());
            // Unloaded terrain says nothing about support. Never load a chunk just
            // to maintain scenery, and never retire a healthy unloaded destination.
            if(!level.hasChunkAt(support)||supportValid(level,support))continue;
            previous.put(entry.getKey(),data);remove(entry.getKey());
        }
        if(previous.isEmpty())return;
        previous.forEach((id,data)->app.store.perches.put(id,new AviaryStore.PerchData(data.x(),data.y(),data.z(),data.color(),data.style(),data.birdName(),data.guests(),data.hub(),false)));
        try{app.store.savePolicy();}
        catch(Exception e){
            app.store.perches.putAll(previous);nextSupportSave=ticks+100;
            if(ticks-supportErrorNotice>=1200){System.err.println("Aviary could not save damaged perch state; retrying");supportErrorNotice=ticks;}
            return;
        }
        var owners=new HashSet<UUID>();
        for(String id:previous.keySet()){
            var port=app.store.ports.get(id);if(port!=null)owners.add(UUID.fromString(port.owner()));
        }
        for(UUID id:owners){var player=app.server.getPlayerList().getPlayer(id);if(player!=null)Aviary.tell(player,"A perch lost its support. Use the whistle to replace it.");}
    }
    private void spawn(String id,AviaryStore.Port port,AviaryStore.PerchData data){
        var level=app.server.overworld();var entities=new ArrayList<Entity>();
        try{
            Vec3 base=new Vec3(data.x()+.5,data.y()+1,data.z()+.5);
            for(String model:List.of("perch_"+data.style(),"perch_cloth_"+data.color())){
                var display=new Display.ItemDisplay(EntityTypes.ITEM_DISPLAY,level);display.addTag("oak_aviary_temporary");display.addTag("oak_aviary_perch");display.setNoGravity(true);display.setSilent(true);display.setPos(base.x,base.y+.65,base.z);
                ItemStack modelItem=new ItemStack(Items.DISC_FRAGMENT_5);modelItem.set(DataComponents.ITEM_MODEL,Identifier.parse("oak_aviary:"+model));display.getSlot(0).set(modelItem);
                var transform=new Transformation(new Vector3f(0,-.65f,0),new Quaternionf().rotationY((float)Math.toRadians(180-port.yaw())),new Vector3f(1),new Quaternionf().rotationY((float)Math.PI));
                ((DisplayAccess)display).aviary$transform(transform);if(!level.addFreshEntity(display))throw new IllegalStateException("Display unavailable");entities.add(display);
            }
            var click=new Interaction(EntityTypes.INTERACTION,level);click.addTag("oak_aviary_temporary");click.addTag("oak_aviary_perch");click.setPos(base);((InteractionAccess)click).aviary$width(1.5f);((InteractionAccess)click).aviary$height(.85f);((InteractionAccess)click).aviary$response(true);
            if(!level.addFreshEntity(click))throw new IllegalStateException("Perch interaction unavailable");entities.add(click);targets.put(click.getUUID(),id);scenery.put(id,new Scenery(entities,data.toString()+port.yaw()));
        }catch(RuntimeException e){for(var entity:entities)entity.discard();System.err.println("Aviary perch scenery unavailable");}
    }
    private void remove(String id){var old=scenery.remove(id);if(old!=null)for(var entity:old.entities()){targets.remove(entity.getUUID());entity.discard();}}
    private void preview(ServerPlayer p){
        if(!kind(p.getMainHandItem()).equals("perch")||!app.javaPlayer(p)||!app.loaded(p)||p.isSpectator()||app.journeys.containsKey(p.getUUID())){previewMessages.remove(p.getUUID());return;}
        if(!p.level().dimension().identifier().toString().equals("minecraft:overworld"))return;
        HitResult result=p.pick(5,0,false);if(!(result instanceof BlockHitResult hit)||result.getType()!=HitResult.Type.BLOCK||hit.getDirection()!=Direction.UP)return;
        var port=candidate(p,hit.getBlockPos(),"preview",null);String issue=placementPermission(p,hit.getBlockPos());
        if(issue.isEmpty())issue=Journey.placementIssue(p.level(),port);
        int color=issue.isEmpty()?0x9ccf9b:0xe7ac79;var dust=new DustParticleOptions(color,.55f);double y=port.y(),angle=Math.toRadians(-port.yaw());
        for(int n=0;n<4;n++){
            double a=n*Math.PI/2,x=Math.cos(a)*.7,z=Math.sin(a)*.45;
            p.level().sendParticles(p,dust,false,false,port.x()+x*Math.cos(angle)-z*Math.sin(angle),y,port.z()+x*Math.sin(angle)+z*Math.cos(angle),1,0,0,0,0);
        }
        for(int n=1;n<=3;n++)p.level().sendParticles(p,dust,false,false,port.x()-Math.sin(Math.toRadians(port.yaw()))*n*.45,y+.15,port.z()+Math.cos(Math.toRadians(port.yaw()))*n*.45,1,0,0,0,0);
        // Show the actual folded animal's occupied envelope, not just the small
        // furniture footprint. Eight corner hints avoid a wall of particles.
        AABB occupied=null;
        for(var bounds:FlightSpace.restBounds(Journey.position(port),port.yaw()).values())occupied=occupied==null?bounds:occupied.minmax(bounds);
        if(occupied!=null){
            var outline=new DustParticleOptions(issue.isEmpty()?0xb2cbbb:0xe7ac79,.40f);
            for(double x:new double[]{occupied.minX-.24,occupied.maxX+.24})for(double z:new double[]{occupied.minZ-.24,occupied.maxZ+.24})for(double h:new double[]{Math.max(port.y()+.005,occupied.minY-.08),occupied.maxY+.18})
                p.level().sendParticles(p,outline,false,false,x,h,z,1,0,0,0,0);
        }
        Vec3 boarding=Journey.boardingSpot(p.level(),port);
        if(boarding!=null){
            var landing=new DustParticleOptions(0xe0c28c,.55f);
            for(double x:new double[]{-.28,.28})for(double z:new double[]{-.28,.28})
                p.level().sendParticles(p,landing,false,false,boarding.x+x,boarding.y+.06,boarding.z+z,1,0,0,0,0);
        }
        String message=issue.isEmpty()?"Place perch":issue;
        if(!message.equals(previewMessages.put(p.getUUID(),message))||ticks%40==0)hint(p,message);
    }
    private static void sound(ServerPlayer p,SoundEvent event,float volume,float pitch){p.connection.send(new ClientboundSoundPacket(Holder.direct(event),SoundSource.PLAYERS,p.getX(),p.getY(),p.getZ(),volume,pitch,p.getRandom().nextLong()));}
    private static void hint(ServerPlayer p,String text){p.sendSystemMessage(Component.literal(text),true);}
    void close(){for(String id:List.copyOf(scenery.keySet()))remove(id);used.clear();previewMessages.clear();}
}
