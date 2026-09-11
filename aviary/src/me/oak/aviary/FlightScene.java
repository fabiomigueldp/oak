package me.oak.aviary;

import net.minecraft.world.phys.Vec3;

/** A connected shot path. Only the endpoints ease to rest, never its flight beats. */
final class FlightScene {
    final Vec3 a,b,c,d;
    final int ticks;
    private final Vec3[] samples;
    FlightScene(Vec3 a,Vec3 b,Vec3 c,Vec3 d,int duration){
        this.a=a;this.b=b;this.c=c;this.d=d;
        var points=new java.util.ArrayList<Vec3>();points.add(a);
        var motor=new FlightMotor(a);
        for(int tick=1;tick<=duration+160;tick++) {
            double t=smooth(tick/(double)duration),u=1-t;
            Vec3 target=a.scale(u*u*u).add(b.scale(3*u*u*t)).add(c.scale(3*u*t*t)).add(d.scale(t*t*t));
            points.add(motor.step(target));
            if(tick>=duration&&motor.settled(d)){points.set(points.size()-1,d);break;}
        }
        if(points.getLast().distanceTo(d)>.001)throw new IllegalArgumentException("Flight could not settle safely");
        samples=points.toArray(Vec3[]::new);ticks=samples.length-1;
    }
    static double smooth(double t){t=Math.clamp(t,0,1);return t*t*t*(10+t*(-15+6*t));}
    Vec3 at(double tick) {
        double value=Math.clamp(tick,0,ticks);int index=(int)value;
        return index==ticks?d:samples[index].lerp(samples[index+1],value-index);
    }
    float heading(double tick,float fallback) {
        Vec3 velocity=at(Math.min(ticks,tick+2)).subtract(at(Math.max(0,tick-2)));
        return velocity.horizontalDistanceSqr()<1e-9?fallback:(float)Math.toDegrees(Math.atan2(-velocity.x,velocity.z));
    }
    static FlightScene route(Vec3 a,Vec3 d,double bow) {
        return route(a,d,bow,null,null);
    }
    static FlightScene route(Vec3 a,Vec3 d,double bow,Float departure,Float arrival) {
        Vec3 forward=new Vec3(d.x-a.x,0,d.z-a.z).normalize(),side=new Vec3(-forward.z,0,forward.x);
        double distance=a.distanceTo(d),reach=Math.min(35,Math.hypot(d.x-a.x,d.z-a.z)*.24);
        double ceiling=Math.max(a.y,d.y)+Math.min(34,Math.max(9,distance*.13));
        Vec3 b=a.add((departure==null?forward:Vec3.directionFromRotation(0,departure)).scale(reach)).add(side.scale(bow));b=new Vec3(b.x,ceiling,b.z);
        Vec3 c=d.subtract((arrival==null?forward:Vec3.directionFromRotation(0,arrival)).scale(reach)).add(side.scale(bow));c=new Vec3(c.x,ceiling,c.z);
        return new FlightScene(a,b,c,d,Math.clamp((int)Math.ceil(distance/24*20)+85,110,850));
    }
    static FlightScene exit(Vec3 port,float yaw,double reach) {
        Vec3 forward=Vec3.directionFromRotation(0,yaw);
        return new FlightScene(port,port.add(0,5,0).add(forward.scale(reach*.15)),
            port.add(0,11,0).add(forward.scale(reach*.7)),port.add(0,14,0).add(forward.scale(reach)),92);
    }
    static FlightScene entry(Vec3 port,float yaw,double reach) {
        Vec3 forward=Vec3.directionFromRotation(0,yaw);
        return new FlightScene(port.add(0,14,0).subtract(forward.scale(reach)),
            port.add(0,9,0).subtract(forward.scale(reach*.7)),port.add(0,3,0).subtract(forward.scale(reach*.15)),port,92);
    }
}
