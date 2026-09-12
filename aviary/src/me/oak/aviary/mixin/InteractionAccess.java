package me.oak.aviary.mixin;

import net.minecraft.world.entity.Interaction;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.gen.Invoker;

/** Native click targets shared by perches and the boarding saddle. */
@Mixin(Interaction.class)
public interface InteractionAccess {
    @Invoker("setWidth") void aviary$width(float width);
    @Invoker("setHeight") void aviary$height(float height);
    @Invoker("setResponse") void aviary$response(boolean response);
}
