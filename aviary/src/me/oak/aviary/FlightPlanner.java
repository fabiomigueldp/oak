package me.oak.aviary;

import net.minecraft.server.level.ServerLevel;
import net.minecraft.world.phys.Vec3;

/** Bounded candidate search spread across server ticks, with no asynchronous world access. */
final class FlightPlanner {
    private static final float[] ANGLES={0,45,-45,90,-90,135,-135,180};
    private final ServerLevel level;
    private final AviaryStore.Port origin,destination;
    private final boolean quick;
    private final double bow;
    private int stage,index;
    private FlightSpace.Check check;
    private FlightScene candidate;
    FlightScene route,exit,entry,call;

    FlightPlanner(ServerLevel level,AviaryStore.Port origin,AviaryStore.Port destination,double threshold,boolean quick){
        this.level=level;this.origin=origin;this.destination=destination;this.quick=quick;
        double distance=Math.hypot(destination.x()-origin.x(),destination.z()-origin.z());bow=Math.min(5,distance*.1);
        if(distance>threshold)stage=1;
    }
    boolean step(long deadline){
        while(stage<4&&System.nanoTime()<deadline){
            if(check==null){
                if(index>=(stage==0?3:17)){
                    if(stage==0){stage++;index=0;continue;}
                    var port=stage==2?destination:origin;
                    throw new IllegalStateException("Clear an approach above or beside "+port.name()+".");
                }
                candidate=candidate();
                check=new FlightSpace.Check(level,candidate,stage==0?"flight":stage==1?"depart":stage==2?"arrive":"call");
            }
            if(!check.step(deadline))return false;
            if(check.valid()){
                switch(stage){case 0->route=candidate;case 1->exit=candidate;case 2->entry=candidate;case 3->call=candidate;default->throw new IllegalStateException("Invalid planning stage");}
                stage++;index=0;
            }else index++;
            check=null;candidate=null;
        }
        return stage==4;
    }
    private FlightScene candidate(){
        if(stage==0)return FlightScene.route(position(origin),position(destination),index==0?bow:index==1?-bow:0,origin.departureYaw(),destination.arrivalYaw());
        var port=stage==2?destination:origin;boolean arriving=stage!=1;
        Float preferred=arriving?port.arrivalYaw():port.departureYaw();float yaw=(preferred==null?port.yaw():preferred)+(index<16?ANGLES[index%8]:0);
        double reach=index<8?12:index<16?7:0;
        var scene=arriving?FlightScene.entry(position(port),yaw,reach):FlightScene.exit(position(port),yaw,reach);
        return stage==3?new FlightScene(scene.a,scene.b,scene.c,scene.d,quick?38:58):scene;
    }
    private static Vec3 position(AviaryStore.Port p){return new Vec3(p.x(),p.y(),p.z());}
}
