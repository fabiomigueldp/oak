package me.oak.aviary.mixin;

import me.oak.aviary.Perches;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.item.crafting.Ingredient;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfoReturnable;

/** The native carrier must never turn a travel tool into a vanilla ingredient. */
@Mixin(Ingredient.class)
public abstract class IngredientGuard {
    @Inject(method="test(Lnet/minecraft/world/item/ItemStack;)Z",at=@At("HEAD"),cancellable=true)
    private void aviary$tool(ItemStack item,CallbackInfoReturnable<Boolean> result) {
        if(Perches.reserved(item))result.setReturnValue(false);
    }
}
