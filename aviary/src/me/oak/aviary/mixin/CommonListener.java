package me.oak.aviary.mixin;

import me.oak.aviary.Aviary;
import net.minecraft.server.network.ServerCommonPacketListenerImpl;
import net.minecraft.network.protocol.common.ServerboundResourcePackPacket;
import net.minecraft.network.protocol.common.ServerboundCustomClickActionPacket;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

@Mixin(ServerCommonPacketListenerImpl.class)
public abstract class CommonListener {
    @Inject(method="handleResourcePackResponse", at=@At("TAIL"))
    private void aviary$pack(ServerboundResourcePackPacket packet, CallbackInfo ci) {
        var self=(ServerCommonPacketListenerImpl)(Object)this;
        Aviary.packResponse(self.getOwner().id(),packet);
    }
    @Inject(method="handleCustomClickAction", at=@At("TAIL"))
    private void aviary$dialog(ServerboundCustomClickActionPacket packet, CallbackInfo ci) {
        var self=(ServerCommonPacketListenerImpl)(Object)this;
        Aviary.dialogResponse(self.getOwner().id(),packet);
    }
}
