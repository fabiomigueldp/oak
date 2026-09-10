package me.oak.aviary;

import com.google.gson.JsonParser;
import com.mojang.math.Transformation;
import net.minecraft.core.component.DataComponents;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.world.entity.Display;
import net.minecraft.network.syncher.SynchedEntityData;
import net.minecraft.world.phys.Vec3;
import org.joml.Matrix4f;
import org.joml.Vector3f;
import java.io.InputStreamReader;

/** Exercise exported cube coordinates through native display metadata and the
 * additional Y(pi) applied by the exact client's ItemDisplayRenderer.
 */
final class RenderRegression {
    static void check(ServerLevel level)throws Exception {
        var read=Display.class.getDeclaredMethod("createTransformation",SynchedEntityData.class);
        read.setAccessible(true);
        try(var reader=new InputStreamReader(RenderRegression.class.getResourceAsStream("/condor-rig.json"),java.nio.charset.StandardCharsets.UTF_8)) {
            var rigData=JsonParser.parseReader(reader).getAsJsonObject();
            BirdRig rig=new BirdRig(level,new Vec3(0,80,0));
            try {
                rig.pose(new Vec3(0,80,0),180,0,0,0);
                int checked=0;
                var partsField=BirdRig.class.getDeclaredField("parts");partsField.setAccessible(true);
                for(var rigPart:(java.util.List<?>)partsField.get(rig)) {
                    var entityMethod=rigPart.getClass().getDeclaredMethod("entity");entityMethod.setAccessible(true);
                    var display=(Display.ItemDisplay)entityMethod.invoke(rigPart);
                    var id=display.getSlot(0).get().get(DataComponents.ITEM_MODEL);
                    if(id==null||!id.getNamespace().equals("oak_aviary")||id.getPath().equals("tail"))continue;
                    var part=rigData.getAsJsonObject(id.getPath());
                    var pivot=part.getAsJsonArray("pivot");
                    var transform=(Transformation)read.invoke(null,display.getEntityData());
                    var actual=new Matrix4f(transform.getMatrix()).rotateY((float)Math.PI);
                    for(var element:part.getAsJsonArray("cubes")) {
                        var cube=element.getAsJsonObject();var center=cube.getAsJsonArray("center");var size=cube.getAsJsonArray("size");
                        for(int corner=0;corner<8;corner++) {
                            float[] point=new float[3];
                            for(int axis=0;axis<3;axis++)point[axis]=center.get(axis).getAsFloat()+size.get(axis).getAsFloat()*(((corner>>axis)&1)==0?-.5f:.5f);
                            Vector3f nativeVertex=actual.transformPosition(new Vector3f(point[0]/4,point[1]/4,point[2]/4));
                            Vector3f expected=new Vector3f(point[0]+pivot.get(0).getAsFloat(),point[1]+pivot.get(1).getAsFloat(),point[2]+pivot.get(2).getAsFloat());
                            if(nativeVertex.distance(expected)>.0001)throw new AssertionError("Native item basis collapsed "+id+": "+nativeVertex+" != "+expected);
                            checked++;
                        }
                    }
                }
                if(checked<600)throw new AssertionError("Missing model vertices: "+checked);
                System.out.println("AVIARY_SMOKE native render basis: "+checked+" vertices passed");
                var displays=new java.util.HashMap<String,Display.ItemDisplay>();
                for(var part:(java.util.List<?>)partsField.get(rig)) {
                    var method=part.getClass().getDeclaredMethod("entity");method.setAccessible(true);
                    var display=(Display.ItemDisplay)method.invoke(part);
                    displays.put(display.getSlot(0).get().get(DataComponents.ITEM_MODEL).getPath(),display);
                }
                var previous=new java.util.HashMap<String,Vector3f>();
                int animated=0;
                for(int tick=0;tick<680;tick+=2) {
                    String phase=tick<80?"board":tick<160?"depart":tick<520?"cruise":tick<600?"arrive":"settle";
                    double height=phase.equals("depart")?18*FlightPath.ease((tick-80)/80.0)
                        :phase.equals("arrive")?18*(1-FlightPath.ease((tick-520)/80.0)):phase.equals("board")||phase.equals("settle")?0:18;
                    rig.animate(new Vec3(0,80+height,0),180,tick,phase,height,.6,phase.equals("depart")?.10:phase.equals("arrive")?-.1:0,phase.equals("cruise")?.6:0,phase.equals("board")?tick/80.0:phase.equals("settle")?(tick-600)/80.0:phase.equals("arrive")?(tick-520)/80.0:phase.equals("depart")?(tick-80)/80.0:0);
                    var matrices=new java.util.HashMap<String,Matrix4f>();
                    for(var entry:displays.entrySet()) {
                        var transform=(Transformation)read.invoke(null,entry.getValue().getEntityData());
                        matrices.put(entry.getKey(),new Matrix4f(transform.getMatrix()).rotateY((float)Math.PI));
                    }
                    var seat=matrices.get("body").transformPosition(new Vector3f(0,1.47f/4,.18f/4));
                    if(seat.distance(new Vector3f(0,1.47f,.18f))>.0001)throw new AssertionError("Saddle moved away from passenger");
                    for(var child:rigData.entrySet()) {
                        var definition=child.getValue().getAsJsonObject();
                        if(!definition.has("parent")||definition.get("parent").isJsonNull())continue;
                        String parentName=definition.get("parent").getAsString();
                        var upper=rigData.getAsJsonObject(parentName).getAsJsonArray("pivot");
                        var tip=definition.getAsJsonArray("pivot");
                        var wrist=new Vector3f();
                        for(int a=0;a<3;a++)wrist.setComponent(a,(tip.get(a).getAsFloat()-upper.get(a).getAsFloat())/4);
                        matrices.get(parentName).transformPosition(wrist);
                        if(wrist.distance(matrices.get(child.getKey()).transformPosition(new Vector3f()))>.0001)
                            throw new AssertionError("Detached joint: "+child.getKey());
                    }
                    for(var entry:matrices.entrySet()) {
                        int cubeIndex=0;
                        for(var element:rigData.getAsJsonObject(entry.getKey()).getAsJsonArray("cubes")) {
                            var cube=element.getAsJsonObject();var center=cube.getAsJsonArray("center");var size=cube.getAsJsonArray("size");
                            for(int corner=0;corner<8;corner++) {
                                var point=new Vector3f();
                                for(int a=0;a<3;a++)point.setComponent(a,(center.get(a).getAsFloat()+size.get(a).getAsFloat()*(((corner>>a)&1)==0?-.5f:.5f))/4);
                                entry.getValue().transformPosition(point);
                                if(!Float.isFinite(point.y)||point.y+displays.get(entry.getKey()).getY()-80<-.035||point.y>4.3||Math.hypot(point.x,point.z)>3.7)
                                    throw new AssertionError("Model left the clear flight envelope: "+entry.getKey()+" at "+tick+" "+point);
                                String key=entry.getKey()+":"+cubeIndex+":"+corner;
                                var last=previous.put(key,point);
                                if(last!=null&&last.distance(point)>.8)throw new AssertionError("Abrupt joint motion: "+key+" at "+tick);
                                animated++;
                            }
                            cubeIndex++;
                        }
                    }
                }
                System.out.println("AVIARY_SMOKE animation: "+animated+" vertices, attached wrists, fixed saddle and clear deck passed");
            } finally {rig.close();}
        }
    }
}
