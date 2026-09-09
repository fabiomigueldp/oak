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
            } finally {rig.close();}
        }
    }
}
