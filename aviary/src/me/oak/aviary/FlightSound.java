package me.oak.aviary;

import net.minecraft.core.Holder;
import net.minecraft.network.protocol.game.ClientboundSoundPacket;
import net.minecraft.resources.Identifier;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.sounds.SoundEvent;
import net.minecraft.sounds.SoundSource;
import net.minecraft.world.phys.Vec3;

/** Original pack foley, scoped to its consenting passenger. */
final class FlightSound {
    static void play(ServerPlayer player,String name,Vec3 position,float volume,float pitch){
        player.connection.send(new ClientboundSoundPacket(Holder.direct(SoundEvent.createVariableRangeEvent(Identifier.parse("oak_aviary:"+name))),
            SoundSource.NEUTRAL,position.x,position.y,position.z,volume,pitch,player.getRandom().nextLong()));
    }
}
