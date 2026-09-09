package me.oak.aviary.mixin;

import net.minecraft.world.entity.Entity;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfoReturnable;

/** Cinematic entities are rebuilt from recovery state, never restored from chunks. */
@Mixin(Entity.class)
public abstract class TransientEntity {
    @Inject(method="shouldBeSaved",at=@At("HEAD"),cancellable=true)
    private void aviary$temporary(CallbackInfoReturnable<Boolean> ci) {
        if(((Entity)(Object)this).entityTags().contains("oak_aviary_temporary"))ci.setReturnValue(false);
    }
}
