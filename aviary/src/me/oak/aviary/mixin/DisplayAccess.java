package me.oak.aviary.mixin;

import com.mojang.math.Transformation;
import net.minecraft.world.entity.Display;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.gen.Invoker;

@Mixin(Display.class)
public interface DisplayAccess {
    @Invoker("setTransformation") void aviary$transform(Transformation transform);
    @Invoker("setTransformationInterpolationDuration") void aviary$duration(int ticks);
    @Invoker("setTransformationInterpolationDelay") void aviary$delay(int ticks);
    @Invoker("setPosRotInterpolationDuration") void aviary$positionDuration(int ticks);
}
