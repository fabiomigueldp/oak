package me.oak.aviary;

import com.mojang.datafixers.util.Pair;
import io.netty.buffer.Unpooled;
import net.minecraft.core.component.DataComponents;
import net.minecraft.network.FriendlyByteBuf;
import net.minecraft.network.protocol.game.*;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.entity.EntityTypes;
import net.minecraft.world.entity.EquipmentSlot;
import net.minecraft.world.entity.decoration.Mannequin;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.item.component.ResolvableProfile;
import me.oak.aviary.mixin.MannequinAccess;
import java.util.*;

/** Owner-only rendering proxy: vanilla hides LocalPlayer with a detached camera.
 * Never added to the world, player list, persistence, or any gameplay inventory.
 */
final class PassengerVisual implements AutoCloseable {
    private final ServerPlayer owner;
    private final Mannequin avatar;
    private final Map<EquipmentSlot,ItemStack> equipment=new EnumMap<>(EquipmentSlot.class);
    private byte heading;

    PassengerVisual(ServerPlayer owner,Entity carrier,float yaw) {
        this.owner=owner;
        avatar=new Mannequin(EntityTypes.MANNEQUIN,owner.level());
        avatar.setComponent(DataComponents.PROFILE,ResolvableProfile.createResolved(owner.getGameProfile()));
        avatar.setMainArm(owner.getMainArm());
        ((MannequinAccess)avatar).aviary$hideDescription(true);
        avatar.setNoGravity(true);avatar.setSilent(true);
        avatar.setPos(owner.position());avatar.setYRot(yaw);avatar.setYHeadRot(yaw);
        heading=(byte)(yaw*256/360);
        owner.connection.send(new ClientboundAddEntityPacket(avatar.getId(),avatar.getUUID(),avatar.getX(),avatar.getY(),avatar.getZ(),0,yaw,EntityTypes.MANNEQUIN,0,net.minecraft.world.phys.Vec3.ZERO,yaw));
        var data=avatar.getEntityData().getNonDefaultValues();
        if(data!=null)owner.connection.send(new ClientboundSetEntityDataPacket(avatar.getId(),data));
        updateEquipment();
        // Use the native codec to construct a private passenger list. The real
        // carrier retains only the actual player on the server and for observers.
        var bytes=new FriendlyByteBuf(Unpooled.buffer());
        try {
            bytes.writeVarInt(carrier.getId());bytes.writeVarIntArray(new int[]{owner.getId(),avatar.getId()});
            owner.connection.send(ClientboundSetPassengersPacket.STREAM_CODEC.decode(bytes));
        } finally {bytes.release();}
    }
    void updateHeading(float yaw) {
        byte next=(byte)(yaw*256/360);if(next==heading)return;heading=next;
        avatar.setYRot(yaw);avatar.setYHeadRot(yaw);
        owner.connection.send(new ClientboundMoveEntityPacket.Rot(avatar.getId(),next,(byte)0,false));
        owner.connection.send(new ClientboundRotateHeadPacket(avatar,next));
    }
    void updateEquipment() {
        List<Pair<EquipmentSlot,ItemStack>> changes=new ArrayList<>();
        for(var slot:List.of(EquipmentSlot.HEAD,EquipmentSlot.CHEST,EquipmentSlot.LEGS,EquipmentSlot.FEET,EquipmentSlot.MAINHAND,EquipmentSlot.OFFHAND)) {
            ItemStack item=owner.getItemBySlot(slot);
            if(!equipment.containsKey(slot)||!ItemStack.matches(equipment.get(slot),item)) {
                var copy=item.copy();equipment.put(slot,copy);changes.add(Pair.of(slot,copy));
            }
        }
        if(!changes.isEmpty())owner.connection.send(new ClientboundSetEquipmentPacket(avatar.getId(),changes));
    }
    int id(){return avatar.getId();}
    public void close(){owner.connection.send(new ClientboundRemoveEntitiesPacket(avatar.getId()));}
}
