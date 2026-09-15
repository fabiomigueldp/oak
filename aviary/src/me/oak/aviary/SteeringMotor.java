package me.oak.aviary;

import net.minecraft.util.Mth;
import net.minecraft.world.phys.Vec3;

/** Continuous steering in blocks/tick. Inputs set intent; acceleration remains bounded. */
final class SteeringMotor {
    Vec3 position,velocity=Vec3.ZERO;
    float yaw;
    SteeringMotor(Vec3 position,float yaw){this.position=position;this.yaw=yaw;}
    Vec3 steer(Vec3 desired){
        Vec3 acceleration=desired.subtract(velocity).scale(.16);
        if(acceleration.length()>.045)acceleration=acceleration.normalize().scale(.045);
        return velocity.add(acceleration);
    }
    Vec3 pursue(Vec3 target,double speed){
        Vec3 delta=target.subtract(position);
        return steer(delta.length()<.04?Vec3.ZERO:delta.normalize().scale(Math.min(speed,delta.length()*.12)));
    }
    void accept(Vec3 next){
        velocity=next;position=position.add(next);
        if(next.horizontalDistanceSqr()>.0001){float desired=(float)Math.toDegrees(Math.atan2(-next.x,next.z));yaw+=Math.clamp(Mth.wrapDegrees(desired-yaw)*.22f,-3f,3f);}
    }
    void stop(){velocity=Vec3.ZERO;}
}
