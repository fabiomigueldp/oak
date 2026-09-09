package me.oak.aviary.mixin;

import me.oak.aviary.Aviary;
import net.minecraft.server.network.ServerGamePacketListenerImpl;
import net.minecraft.server.level.ServerPlayer;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.Shadow;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

@Mixin(ServerGamePacketListenerImpl.class)
public abstract class GameListener {
    @Shadow public ServerPlayer player;
    // Flight is server-authoritative; gameplay interactions resume on every exit path.
    @Inject(method={"handlePlayerAction","handleUseItemOn","handleUseItem","handleInteract","handleAttack","handlePunch","handleMoveVehicle","handlePlayerAbilities"},at=@At("HEAD"),cancellable=true)
    private void aviary$protect(CallbackInfo ci) {
        if(Aviary.isTravelling(player.getUUID()))ci.cancel();
    }
    @Inject(method="handlePlayerInput",at=@At("HEAD"),cancellable=true)
    private void aviary$input(net.minecraft.network.protocol.game.ServerboundPlayerInputPacket packet,CallbackInfo ci) {
        if(Aviary.isTravelling(player.getUUID())) {
            if(packet.input().shift())Aviary.requestSkip(player.getUUID());
            ci.cancel();
        }
    }
}
