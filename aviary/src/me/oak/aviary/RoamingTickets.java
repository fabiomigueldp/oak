package me.oak.aviary;

import net.minecraft.server.level.*;
import net.minecraft.world.level.ChunkPos;
import net.minecraft.world.phys.Vec3;
import java.util.*;

/** A fixed recovery area plus rolling current/look-ahead areas, at most 27 chunks. */
final class RoamingTickets implements AutoCloseable {
    private static final TicketType TYPE=new TicketType(0,TicketType.FLAG_LOADING|TicketType.FLAG_SIMULATION);
    private static final Map<ChunkPos,Integer> REFERENCES=new HashMap<>();
    private final ServerLevel level;
    private final Vec3 origin;
    private final Set<ChunkPos> held=new HashSet<>();
    private volatile Throwable failure;
    RoamingTickets(ServerLevel level,Vec3 origin){this.level=level;this.origin=origin;}
    void update(Vec3 current,Vec3 ahead){
        if(failure!=null)throw new IllegalStateException("Could not prepare terrain ahead",failure);
        Set<ChunkPos> wanted=new HashSet<>();area(wanted,origin);area(wanted,current);area(wanted,ahead);
        for(var p:wanted)if(held.add(p)){
            REFERENCES.merge(p,1,Integer::sum);
            level.getChunkSource().addTicketAndLoadWithRadius(TYPE,p,0).whenComplete((v,error)->{if(error!=null)failure=error;});
        }
        for(var p:List.copyOf(held))if(!wanted.contains(p)){release(p);held.remove(p);}
    }
    private static void area(Set<ChunkPos> set,Vec3 v){int x=(int)Math.floor(v.x)>>4,z=(int)Math.floor(v.z)>>4;for(int dx=-1;dx<=1;dx++)for(int dz=-1;dz<=1;dz++)set.add(new ChunkPos(x+dx,z+dz));}
    private void release(ChunkPos p){int count=REFERENCES.getOrDefault(p,1)-1;if(count==0){REFERENCES.remove(p);level.getChunkSource().removeTicketWithRadius(TYPE,p,0);}else REFERENCES.put(p,count);}
    public void close(){for(var p:held)release(p);held.clear();}
}
