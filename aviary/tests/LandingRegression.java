package me.oak.aviary;

import net.minecraft.core.BlockPos;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.world.level.block.Blocks;
import net.minecraft.world.level.block.state.BlockState;
import java.util.*;

/** Loaded terrain only, with a valid landing beyond the former five-block search. */
final class LandingRegression {
    static void check(ServerLevel level){
        var original=new HashMap<BlockPos,BlockState>();
        var center=new AviaryStore.Port("search-test","Search","minecraft:overworld",24.5,140,16.5,0,"",false);
        try {
            for(int x=22;x<=26;x++)for(int z=24;z<=28;z++){
                var block=new BlockPos(x,139,z);original.put(block,level.getBlockState(block));level.setBlock(block,Blocks.STONE.defaultBlockState(),3);
            }
            var search=new LandingSearch(level,center);
            if(search.step(0)||search.result()!=null)throw new AssertionError("Search ignored its deadline");
            for(int step=0;step<1000;step++)if(search.step(System.nanoTime()+2_000_000))break;
            var found=search.result();
            if(found==null||Journey.position(found).distanceTo(Journey.position(center))<8||!found.id().equals(center.id())||center.z()!=16.5)throw new AssertionError("Nearby landing search failed or changed destination identity");
            var unloaded=new AviaryStore.Port("unloaded","Unloaded","minecraft:overworld",100000.5,140,100000.5,0,"",false);
            var empty=new LandingSearch(level,unloaded);
            for(int step=0;step<1000;step++)if(empty.step(System.nanoTime()+2_000_000))break;
            if(empty.result()!=null||level.hasChunkAt(BlockPos.containing(unloaded.x(),unloaded.y(),unloaded.z())))throw new AssertionError("Search loaded remote terrain");
            System.out.println("AVIARY_SMOKE incremental nearby landing and unloaded terrain passed");
        } finally {original.forEach((pos,state)->level.setBlock(pos,state,3));}
    }
}
