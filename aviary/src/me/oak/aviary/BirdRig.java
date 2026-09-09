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

/** Eight rigid native item displays, exported from the authored Blender scene. */
public final class BirdRig {
    private record Part(String name,Vec3 pivot,Display.ItemDisplay entity) {}
    private final List<Part> parts=new ArrayList<>();
    public BirdRig(ServerLevel level,Vec3 position) {
        try(var reader=new InputStreamReader(Objects.requireNonNull(getClass().getResourceAsStream("/condor-rig.json")),java.nio.charset.StandardCharsets.UTF_8)) {
            var json=JsonParser.parseReader(reader).getAsJsonObject();
            for(var entry:json.entrySet()) {
                var values=entry.getValue().getAsJsonObject().getAsJsonArray("pivot");
                Vec3 pivot=new Vec3(values.get(0).getAsDouble(),values.get(1).getAsDouble(),values.get(2).getAsDouble());
                var entity=new Display.ItemDisplay(EntityTypes.ITEM_DISPLAY,level);
                entity.addTag("oak_aviary_temporary");entity.setNoGravity(true);entity.setSilent(true);
                entity.setPos(position.x,position.y,position.z);
                ItemStack item=new ItemStack(Items.PAPER);
                item.set(DataComponents.ITEM_MODEL,Identifier.parse("oak_aviary:"+entry.getKey()));
                entity.getSlot(0).set(item);
                var access=(DisplayAccess)entity;access.aviary$duration(3);access.aviary$positionDuration(3);
                if(!level.addFreshEntity(entity))throw new IllegalStateException("Could not create bird model");
                parts.add(new Part(entry.getKey(),pivot,entity));
            }
            pose(position,0,0,0);
        } catch(Exception e){close();throw new IllegalStateException("Bird rig unavailable",e);}
    }
    public void pose(Vec3 origin,float yaw,double wing,double bank) {
        Quaternionf direction=new Quaternionf().rotationY((float)Math.toRadians(180-yaw));
        for(Part part:parts) {
            Quaternionf joint=new Quaternionf();
            int sign=part.name().startsWith("left")?-1:1;
            if(part.name().contains("wing"))joint.rotationZ((float)(sign*wing));
            if(part.name().contains("tip"))joint.rotationZ((float)(sign*wing*1.18));
            if(part.name().equals("tail"))joint.rotationX((float)(-.05+wing*.10));
            Vector3f local=new Vector3f((float)part.pivot().x,(float)part.pivot().y,(float)part.pivot().z);
            if(part.name().contains("tip")) {
                Part upper=parts.stream().filter(p->p.name().equals(part.name().replace("tip","wing"))).findFirst().orElseThrow();
                Vector3f hinge=new Vector3f((float)upper.pivot().x,(float)upper.pivot().y,(float)upper.pivot().z);
                local.sub(hinge).rotate(new Quaternionf().rotationZ((float)(sign*wing))).add(hinge);
            }
            Quaternionf body=new Quaternionf(direction).rotateZ((float)bank);
            Vector3f offset=body.transform(local);
            Quaternionf rotation=new Quaternionf(body).mul(joint);
            var transform=new Transformation(offset,rotation,new Vector3f(4),new Quaternionf());
            var access=(DisplayAccess)part.entity();access.aviary$transform(transform);access.aviary$delay(0);
            part.entity().setPos(origin.x,origin.y,origin.z);
        }
    }
    public void close(){for(Part part:parts)part.entity().discard();parts.clear();}
}
