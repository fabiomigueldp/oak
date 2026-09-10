package me.oak.aviary;

import com.google.gson.*;
import com.mojang.math.Transformation;
import me.oak.aviary.mixin.DisplayAccess;
import net.minecraft.core.component.DataComponents;
import net.minecraft.resources.Identifier;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.world.entity.Display;
import net.minecraft.world.entity.EntityTypes;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.item.Items;
import net.minecraft.world.phys.Vec3;
import org.joml.Quaternionf;
import org.joml.Vector3f;
import java.io.InputStreamReader;
import java.util.*;

/** Twelve articulated native item displays, exported from the authored Blender scene. */
public final class BirdRig {
    private record Part(String name,Vec3 pivot,String parent,Display.ItemDisplay entity) {}
    private final List<Part> parts=new ArrayList<>();
    private final Map<String,Part> byName=new HashMap<>();
    private final BirdMotion motion=new BirdMotion();
    private boolean actingPose;
    public BirdRig(ServerLevel level,Vec3 position) {
        try(var reader=new InputStreamReader(Objects.requireNonNull(getClass().getResourceAsStream("/condor-rig.json")),java.nio.charset.StandardCharsets.UTF_8)) {
            var json=JsonParser.parseReader(reader).getAsJsonObject();
            for(var entry:json.entrySet()) {
                var values=entry.getValue().getAsJsonObject().getAsJsonArray("pivot");
                Vec3 pivot=new Vec3(values.get(0).getAsDouble(),values.get(1).getAsDouble(),values.get(2).getAsDouble());
                var entity=new Display.ItemDisplay(EntityTypes.ITEM_DISPLAY,level);
                entity.addTag("oak_aviary_temporary");entity.setNoGravity(true);entity.setSilent(true);entity.setRequiresPrecisePosition(true);
                entity.setPos(position.x,position.y,position.z);
                ItemStack item=new ItemStack(Items.PAPER);
                item.set(DataComponents.ITEM_MODEL,Identifier.parse("oak_aviary:"+entry.getKey()));
                entity.getSlot(0).set(item);
                var access=(DisplayAccess)entity;access.aviary$duration(2);access.aviary$positionDuration(2);
                if(!level.addFreshEntity(entity))throw new IllegalStateException("Could not create bird model");
                var value=entry.getValue().getAsJsonObject();
                String parent=value.has("parent")&&!value.get("parent").isJsonNull()?value.get("parent").getAsString():null;
                Part part=new Part(entry.getKey(),pivot,parent,entity);parts.add(part);byName.put(part.name(),part);
            }
            pose(position,0,0,0);
        } catch(Exception e){close();throw new IllegalStateException("Bird rig unavailable",e);}
    }
    private record Joint(Vector3f position,Quaternionf rotation) {}
    record Frame(Vec3 position,boolean downstroke) {}
    public void pose(Vec3 origin,float yaw,double wing,double bank){pose(origin,yaw,wing,wing*.18,bank);}
    public void pose(Vec3 origin,float yaw,double wing,double tip,double bank) {
        pose(origin,yaw,wing,tip,bank,0,-.05+wing*.10,0,false);
    }
    private void pose(Vec3 origin,float yaw,double wing,double tip,double bank,double head,double tail,double feet,boolean acting) {
        Quaternionf direction=new Quaternionf().rotationY((float)Math.toRadians(180-yaw));
        Quaternionf rollPitch=new Quaternionf().rotationZ((float)bank).rotateX((float)(acting?motion.pitch:0));
        Vector3f seat=new Vector3f(0,1.47f,.18f);
        Map<String,Joint> joints=new HashMap<>();
        for(Part part:parts) {
            int sign=part.name().startsWith("left")?-1:1;
            Quaternionf joint=new Quaternionf();
            double steering=acting?motion.bank*.35:0,sweep=acting?motion.sweep:0;
            if(part.name().endsWith("_wing"))joint.rotateZ((float)(sign*wing+steering)).rotateY((float)(-sign*sweep*.35));
            if(part.name().endsWith("_elbow"))joint.rotateZ((float)(sign*tip*.55)).rotateY((float)(-sign*sweep));
            if(part.name().endsWith("_tip"))joint.rotateZ((float)(sign*tip*.45)).rotateY((float)(-sign*sweep*.45));
            if(part.name().equals("neck"))joint.rotateY((float)(acting?motion.headYaw*.45:0)).rotateX((float)(head*.3));
            if(part.name().equals("head"))joint.rotateY((float)(acting?motion.headYaw*.55:0)).rotateX((float)(head*.7));
            if(part.name().equals("tail"))joint.rotationX((float)tail);
            if(part.name().endsWith("_foot"))joint.rotationX((float)(feet*(acting?1-motion.contact:1)*(sign<0?.93:1)));
            Vector3f local=new Vector3f((float)part.pivot().x,(float)part.pivot().y,(float)part.pivot().z);
            Quaternionf rotation;
            Vector3f offset;
            if(part.parent()!=null) {
                Part parent=byName.get(part.parent());Joint frame=joints.get(part.parent());
                local.sub((float)parent.pivot().x,(float)parent.pivot().y,(float)parent.pivot().z);
                offset=local.rotate(frame.rotation()).add(frame.position());
                rotation=new Quaternionf(frame.rotation()).mul(joint);
            } else {
                offset=new Vector3f(local).sub(seat).rotate(rollPitch).add(seat).rotate(direction);
                rotation=new Quaternionf(direction).mul(rollPitch).mul(joint);
                if(acting&&part.name().endsWith("_foot")) {
                    Vector3f planted=new Vector3f(local).rotate(direction).add(0,(float)-motion.heave,0);
                    offset.lerp(planted,(float)motion.contact);
                    rotation.slerp(direction,(float)motion.contact);
                }
            }
            joints.put(part.name(),new Joint(new Vector3f(offset),new Quaternionf(rotation)));
            // Cancel the native ItemDisplayRenderer Y(pi) without changing pivots.
            var transform=new Transformation(offset,rotation,new Vector3f(4),new Quaternionf().rotationY((float)Math.PI));
            var access=(DisplayAccess)part.entity();access.aviary$transform(transform);access.aviary$delay(0);
            part.entity().setPos(origin.x,origin.y,origin.z);
        }
    }
    boolean animate(Vec3 origin,float yaw,int tick,String phase,double clearance) {
        return animate(origin,yaw,tick,phase,clearance,.6,0,0,0).downstroke();
    }
    Frame animate(Vec3 base,float yaw,int tick,String phase,double clearance,double speed,double climb,double turn,double progress) {
        boolean downstroke=motion.update(tick,phase,clearance,speed,climb,turn,progress);
        Vec3 origin=base.add(0,motion.heave,0);
        double[] a=motion.angles;
        if(!actingPose||tick%2==0){pose(origin,yaw,a[0],a[1],motion.bank,a[2],a[3],a[4],true);actingPose=true;}
        // Root translation follows the carrier every tick; joint metadata stays at 10 Hz.
        for(Part part:parts)part.entity().setPos(origin.x,origin.y,origin.z);
        return new Frame(origin,downstroke);
    }
    public void close(){for(Part part:parts)part.entity().discard();parts.clear();byName.clear();}
}
