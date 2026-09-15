package me.oak.aviary;

import net.minecraft.core.BlockPos;
import net.minecraft.server.level.ServerLevel;
import java.util.*;

/** Resumable search of nearby loaded ground. Never edits terrain or loads chunks. */
final class LandingSearch {
    private static final int[] RADII={2,3,5,8,12}, HEIGHTS={0,-1,1,-2,2};
    private final ServerLevel level;
    private final AviaryStore.Port center;
    private final Set<BlockPos> visited=new HashSet<>();
    private int index;
    private AviaryStore.Port result;
    LandingSearch(ServerLevel level,AviaryStore.Port center){this.level=level;this.center=center;}
    boolean step(long deadline){
        result=null;
        while(index<RADII.length*16*HEIGHTS.length&&System.nanoTime()<deadline){
            int current=index++,height=HEIGHTS[current%HEIGHTS.length],direction=current/HEIGHTS.length%16;
            double angle=direction*Math.PI/8,radius=RADII[current/(16*HEIGHTS.length)];
            BlockPos feet=BlockPos.containing(center.x()+Math.sin(angle)*radius,Math.floor(center.y())+height,center.z()+Math.cos(angle)*radius);
            if(!visited.add(feet)||!level.hasChunkAt(feet)||!level.canSeeSky(feet))continue;
            if(!Perches.supportValid(level,feet.below())||!level.getBlockState(feet).isAir()||!level.getBlockState(feet.above()).isAir())continue;
            var candidate=new AviaryStore.Port(center.id(),center.name(),center.dimension(),feet.getX()+.5,feet.getY(),feet.getZ()+.5,center.yaw(),center.owner(),center.shared(),center.departureYaw(),center.arrivalYaw());
            if(!Journey.placementIssue(level,candidate).isEmpty())continue;
            result=candidate;return true;
        }
        return index>=RADII.length*16*HEIGHTS.length;
    }
    AviaryStore.Port result(){return result;}
}
