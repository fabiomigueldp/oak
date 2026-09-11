package me.oak.aviary;

import net.minecraft.world.phys.Vec3;

/** Bounded acceleration and damped pursuit. The planner validates every resulting step. */
final class FlightMotor {
    private Vec3 position,velocity=Vec3.ZERO;
    FlightMotor(Vec3 position){this.position=position;}
    Vec3 step(Vec3 target) {
        Vec3 acceleration=target.subtract(position).scale(.14).subtract(velocity.scale(.68));
        double limit=.065;
        if(acceleration.length()>limit)acceleration=acceleration.normalize().scale(limit);
        velocity=velocity.add(acceleration);
        position=position.add(velocity);
        return position;
    }
    boolean settled(Vec3 target){return position.distanceTo(target)<.0005&&velocity.length()<.0005;}
}
