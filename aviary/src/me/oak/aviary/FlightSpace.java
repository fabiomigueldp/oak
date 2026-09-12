package me.oak.aviary;

import net.minecraft.core.BlockPos;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.world.phys.AABB;
import net.minecraft.world.phys.Vec3;
import java.util.*;

/** Loaded-terrain collision queries shared by placement, planning and movement. */
final class FlightSpace {
    private static final Map<Integer,Map<String,AABB>> REST=new LinkedHashMap<>();
    private FlightSpace() {}
    static boolean clear(ServerLevel level,Map<String,AABB> previous,Map<String,AABB> current,double floor) {
        for(var entry:current.entrySet()) {
            AABB bounds=entry.getValue();var old=previous.get(entry.getKey());if(old!=null)bounds=bounds.minmax(old);
            // Include native transform interpolation and small heading differences.
            // Feet may contact the support plane; the rest of the volume stays above it.
            // Native foot geometry has a 3.5 cm contact tolerance, including the
            // small IK/interpolation overlap with its support. Never apply it to wings.
            double minY=bounds.minY+(entry.getKey().equals("feet")?.04:-.08);
            AABB box=new AABB(bounds.minX-.24,minY,bounds.minZ-.24,
                bounds.maxX+.24,bounds.maxY+.18,bounds.maxZ+.24);
            if(!loaded(level,box))return false;
            for(var shape:level.getBlockAndLiquidCollisions(null,box))if(!shape.isEmpty())return false;
        }
        return true;
    }
    private static boolean loaded(ServerLevel level,AABB box) {
        if(box.minY<level.getMinY()||box.maxY>=level.getMaxY())return false;
        for(int x=(int)Math.floor(box.minX)>>4;x<=((int)Math.floor(box.maxX)>>4);x++)
            for(int z=(int)Math.floor(box.minZ)>>4;z<=((int)Math.floor(box.maxZ)>>4);z++)
                if(!level.hasChunk(x,z))return false;
        return level.getWorldBorder().isWithinBounds(BlockPos.containing(box.minX,box.minY,box.minZ))
            &&level.getWorldBorder().isWithinBounds(BlockPos.containing(box.maxX,box.maxY,box.maxZ));
    }
    static boolean resting(ServerLevel level,Vec3 position,float yaw) {
        return clear(level,Map.of(),restBounds(position,yaw),position.y);
    }
    static Map<String,AABB> restBounds(Vec3 position,float yaw) {
        int key=Math.round(net.minecraft.util.Mth.wrapDegrees(yaw)*10);
        var geometry=REST.get(key);
        if(geometry==null) {
            var rig=BirdRig.preview();for(int t=0;t<60;t++)rig.animate(Vec3.ZERO,yaw,t,"greet",0,0,0,0,1);
            geometry=rig.bounds(Vec3.ZERO);if(REST.size()>=32)REST.remove(REST.keySet().iterator().next());REST.put(key,geometry);
        }
        var placed=new LinkedHashMap<String,AABB>();for(var entry:geometry.entrySet())placed.put(entry.getKey(),entry.getValue().move(position));return placed;
    }
    static float turn(float facing,float desired){return facing+(float)Math.clamp(net.minecraft.util.Mth.wrapDegrees(desired-facing)*.22,-1.8,1.8);}
    static boolean valid(ServerLevel level,FlightScene scene,String action) {
        var check=new Check(level,scene,action);check.step(Long.MAX_VALUE);return check.valid();
    }
    /** A resumable pose sweep. Each sample remains atomic on the server thread. */
    static final class Check {
        private final ServerLevel level;
        private final FlightScene scene;
        private final String action;
        private final BirdRig rig;
        private Map<String,AABB> previous=Map.of();
        private Vec3 last;
        private float facing;
        private int warmup,tick;
        private boolean done,passed;
        Check(ServerLevel level,FlightScene scene,String action){
            this.level=level;this.scene=scene;this.action=action;rig=BirdRig.preview();facing=scene.heading(0,0);last=scene.a;
            warmup=action.equals("arrive")||action.equals("call")?60:0;
        }
        boolean step(long deadline){
            while(!done&&System.nanoTime()<deadline){
                if(warmup<60){rig.animate(scene.a,facing,warmup,"board",0,0,0,0,warmup/60.0);warmup++;continue;}
                Vec3 target=scene.at(tick),velocity=target.subtract(last);double progress=tick/(double)scene.ticks;
                String beat=action.equals("flight")?(progress<.2?"depart":progress>.73?"arrive":"cruise"):action;
                double local=action.equals("flight")?(progress<.2?progress/.2:progress>.73?(progress-.73)/.27:(progress-.2)/.53):progress;
                double height=beat.equals("arrive")||beat.equals("call")?Math.max(0,target.y-scene.d.y):beat.equals("depart")?Math.max(0,target.y-scene.a.y):14;
                float next=turn(facing,scene.heading(tick+5,facing)),delta=net.minecraft.util.Mth.wrapDegrees(next-facing);facing=next;
                rig.animate(target,facing,tick+60,beat,height,velocity.horizontalDistance(),velocity.y,delta,local);
                var bounds=rig.bounds(target);
                if(!clear(level,previous,bounds,Math.min(last.y,target.y))){done=true;rig.close();return true;}
                previous=bounds;last=target;
                if(++tick>scene.ticks){passed=true;done=true;rig.close();}
            }
            return done;
        }
        boolean valid(){return done&&passed;}
    }
    static FlightScene approach(ServerLevel level,Vec3 position,float yaw,boolean arriving) {
        for(double reach:new double[]{12,7,0})for(float angle:new float[]{0,45,-45,90,-90,135,-135,180}) {
            if(reach==0&&angle!=0)continue;
            FlightScene path=arriving?FlightScene.entry(position,yaw+angle,reach):FlightScene.exit(position,yaw+angle,reach);
            if(valid(level,path,arriving?"arrive":"depart"))return path;
        }
        return null;
    }
}
