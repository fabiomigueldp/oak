package me.oak.aviary;

import net.minecraft.server.level.ServerLevel;
import net.minecraft.core.BlockPos;
import net.minecraft.world.level.levelgen.Heightmap;
import net.minecraft.world.phys.Vec3;

/** Surface landing search, incrementally validating a complete final approach. */
final class AirLandingSearch {
    private final ServerLevel level;
    private final Vec3 center;
    private final float yaw;
    private int index;
    private FlightSpace.Check check;
    private AviaryStore.Port candidate;
    FlightScene scene;
    AviaryStore.Port result;
    AirLandingSearch(ServerLevel level,Vec3 center,float yaw){this.level=level;this.center=center;this.yaw=yaw;}
    boolean step(long deadline){
        while(System.nanoTime()<deadline){
            if(check!=null){if(!check.step(deadline))return false;if(check.valid()){result=candidate;return true;}check=null;}
            if(index>=65)return true;
            int n=index++;double radius=n==0?0:new int[]{4,8,12,20}[(n-1)/16],angle=(n-1)%16*Math.PI/8;
            int x=(int)Math.floor(center.x+Math.sin(angle)*radius),z=(int)Math.floor(center.z+Math.cos(angle)*radius);
            var column=new BlockPos(x,(int)center.y,z);
            if(!level.hasChunkAt(column)||!level.getWorldBorder().isWithinBounds(column))continue;
            int y=level.getHeight(Heightmap.Types.MOTION_BLOCKING_NO_LEAVES,x,z);
            if(y>Math.min(290,level.getMaxY()-20)||y<Math.max(-60,level.getMinY()+1))continue;
            candidate=new AviaryStore.Port("roaming","Landing","minecraft:overworld",x+.5,y,z+.5,yaw,"",false);
            if(!Journey.placementIssue(level,candidate).isEmpty())continue;
            scene=FlightScene.entry(Journey.position(candidate),yaw,7);
            check=new FlightSpace.Check(level,scene,"arrive");
        }
        return false;
    }
}
