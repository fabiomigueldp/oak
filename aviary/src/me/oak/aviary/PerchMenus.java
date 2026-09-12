package me.oak.aviary;

import java.util.*;
import net.minecraft.core.Holder;
import net.minecraft.nbt.CompoundTag;
import net.minecraft.network.chat.Component;
import net.minecraft.network.protocol.common.ServerboundCustomClickActionPacket;
import net.minecraft.resources.Identifier;
import net.minecraft.server.dialog.*;
import net.minecraft.server.dialog.action.CustomAll;
import net.minecraft.server.dialog.input.TextInput;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.server.permissions.Permissions;
import net.minecraft.core.component.DataComponents;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.item.component.CustomData;
import net.minecraft.server.dialog.body.PlainMessage;

/** Native, owner-checked interactions. Client fields never confer ownership or identity. */
final class PerchMenus {
    private record Form(String port,String nonce,long revision,long expires) {}
    private final Aviary app;
    private final Map<UUID,Form> forms=new HashMap<>();
    private final Map<UUID,Form> removals=new HashMap<>();
    PerchMenus(Aviary app){this.app=app;}
    void forget(UUID player){forms.remove(player);removals.remove(player);}
    int packed(ServerPlayer p){
        if(!app.javaPlayer(p))return 0;var buttons=new ArrayList<ActionButton>();
        for(var entry:app.store.perches.entrySet()){
            var port=app.store.ports.get(entry.getKey());
            if(!entry.getValue().active()&&port.owner().equals(p.getUUID().toString()))buttons.add(app.action(port.name(),"Recover or remove this destination","/aviary manage "+port.id()+" packed"));
        }
        buttons.add(app.action("Back","Destinations","/aviary"));return app.dialog(p,"Packed perches","Use a new perch kit to replace a packed or damaged perch.",buttons);
    }
    private boolean owner(ServerPlayer p,AviaryStore.Port port){return port!=null&&(port.owner().equals(p.getUUID().toString())||p.permissions().hasPermission(Permissions.COMMANDS_GAMEMASTER));}
    private AviaryStore.Port editable(ServerPlayer p,String id){
        var port=app.store.ports.get(id);
        if(!app.javaPlayer(p)||!owner(p,port))throw new IllegalArgumentException("Only the owner can change this perch.");
        if(!p.level().dimension().identifier().toString().equals(port.dimension())||p.position().distanceTo(Journey.position(port))>8)throw new IllegalArgumentException("Stand near the perch to change it.");
        if(app.busyPort(id)||app.journeys.containsKey(p.getUUID()))throw new IllegalArgumentException("Wait until the flight has finished.");
        return port;
    }
    int open(ServerPlayer p,String id){
        var port=app.store.ports.get(id);if(port==null||!app.javaPlayer(p))return 0;
        if(p.position().distanceTo(Journey.position(port))>8||!p.level().dimension().identifier().toString().equals(port.dimension()))return 0;
        if(!app.accessible(port,p)){Aviary.tell(p,"This perch is private.");return 0;}
        app.discover(p,port);
        var buttons=new ArrayList<ActionButton>();
        buttons.add(app.action("Destinations","Choose where to fly","/aviary"));
        if(owner(p,port)){
            buttons.add(app.action("Rename","Perch and bird names","/aviary manage "+id+" rename"));
            buttons.add(app.action("Access: "+(port.shared()?"Public":"Private"),"Choose who can visit","/aviary manage "+id+" access"));
            if(app.store.perches.containsKey(id)){
                buttons.add(app.action("Guests","Invite players or remove access","/aviary manage "+id+" guests"));
                buttons.add(app.action("Appearance","Wood and cloth","/aviary manage "+id+" appearance"));
                buttons.add(app.action("Move","Pack this perch and keep its address","/aviary manage "+id+" move"));
                buttons.add(app.action("Remove","Remove this destination","/aviary manage "+id+" remove"));
            }
        }
        return app.dialog(p,port.name(),app.busyPort(id)?"A bird is using this perch.":port.shared()?"Public perch":"Private perch",buttons);
    }
    int manage(ServerPlayer p,String id,String action,String value){
        try{
            if(action.equals("remove"))return removal(p,id);
            if(action.equals("packed")){
                var port=removable(p,id);var perch=app.store.perches.get(id);if(perch.active())return open(p,id);
                return app.dialog(p,port.name(),"This perch is packed or needs a new support.",List.of(app.action("Recover kit","Use a new perch kit","/aviary recover "+id),app.action("Remove","Remove this destination","/aviary manage "+id+" remove"),app.action("Back","Packed perches","/aviary packed")));
            }
            var port=editable(p,id);var perch=app.store.perches.get(id);
            switch(action){
                case "rename": return rename(p,port,perch);
                case "access":return app.dialog(p,"Access",port.shared()?"Anyone can discover this perch.":"Only you and invited players can visit.",List.of(
                    app.action(port.shared()?"Make private":"Make public","Change access","/aviary manage "+id+" share "+(!port.shared())),
                    app.action("Back","Perch","/aviary perch "+id)));
                case "share":{
                    if(!Set.of("true","false").contains(value))return 0;
                    var next=new AviaryStore.Port(port.id(),port.name(),port.dimension(),port.x(),port.y(),port.z(),port.yaw(),port.owner(),Boolean.parseBoolean(value),port.departureYaw(),port.arrivalYaw());
                    app.store.ports.put(id,next);try{app.store.savePolicy();}catch(Exception e){app.store.ports.put(id,port);throw e;}
                    return open(p,id);
                }
                case "guests":return guests(p,port,perch,0);
                case "guestpage":return guests(p,port,perch,Integer.parseInt(value));
                case "invite":case "revoke":{
                    if(perch==null)return 0;String uuid=UUID.fromString(value).toString();
                    if(uuid.equals(port.owner()))return 0;var guests=new HashSet<>(perch.guests());
                    if(action.equals("invite")){
                        if(app.server.getPlayerList().getPlayer(UUID.fromString(uuid))==null)throw new IllegalArgumentException("That player is offline. Try again when they join.");
                        if(guests.size()>=64)throw new IllegalArgumentException("This perch has reached its guest limit.");guests.add(uuid);
                    }else guests.remove(uuid);
                    save(id,new AviaryStore.PerchData(perch.x(),perch.y(),perch.z(),perch.color(),perch.style(),perch.birdName(),guests,perch.hub(),perch.active()));
                    return guests(p,port,app.store.perches.get(id),0);
                }
                case "appearance":{
                    if(perch==null)return 0;
                    return app.dialog(p,"Appearance","Choose the wood and cloth.",List.of(app.action("Wood: "+title(perch.style()),"Wood style","/aviary manage "+id+" woods"),app.action("Cloth: "+title(perch.color()),"Cloth color","/aviary manage "+id+" colors"),app.action("Back","Perch","/aviary perch "+id)));
                }
                case "woods":case "colors":{
                    if(perch==null)return 0;boolean color=action.equals("colors");var buttons=new ArrayList<ActionButton>();
                    for(String option:(color?AviaryStore.COLORS:AviaryStore.STYLES).stream().sorted().toList())buttons.add(app.action(title(option),"Choose "+title(option),"/aviary manage "+id+(color?" color ":" style ")+option));
                    buttons.add(app.action("Back","Appearance","/aviary manage "+id+" appearance"));return app.dialog(p,color?"Cloth":"Wood","",buttons);
                }
                case "color":case "style":{
                    if(perch==null)return 0;
                    save(id,new AviaryStore.PerchData(perch.x(),perch.y(),perch.z(),action.equals("color")?value:perch.color(),action.equals("style")?value:perch.style(),perch.birdName(),perch.guests(),perch.hub(),perch.active()));return manage(p,id,"appearance","");
                }
                case "move":return app.dialog(p,"Move perch","Its name, guests and favorites will follow it. Place the packed perch at its new location.",List.of(app.action("Pack perch","Stop new arrivals and collect the perch","/aviary manage "+id+" pack"),app.action("Back","Keep it here","/aviary perch "+id)));
                case "pack":return app.perches.move(p,id)?1:0;
                default:return open(p,id);
            }
        }catch(Exception e){Aviary.tell(p,e instanceof IllegalArgumentException?e.getMessage():"Could not save the perch. Try again.");return 0;}
    }
    private int guests(ServerPlayer p,AviaryStore.Port port,AviaryStore.PerchData perch,int page){
        if(perch==null)return 0;var options=new ArrayList<ActionButton>();var listed=new HashSet<String>();
        for(var player:app.server.getPlayerList().getPlayers()){
            String uuid=player.getUUID().toString();if(uuid.equals(port.owner())||!app.javaPlayer(player))continue;listed.add(uuid);
            boolean invited=perch.guests().contains(uuid);options.add(app.action((invited?"Remove ":"Invite ")+player.getGameProfile().name(),invited?"Remove private access":"Allow a first visit","/aviary manage "+port.id()+(invited?" revoke ":" invite ")+uuid));
        }
        for(String uuid:perch.guests().stream().sorted().toList())if(!listed.contains(uuid))options.add(app.action("Remove "+uuid.substring(0,8),"Offline guest: "+uuid,"/aviary manage "+port.id()+" revoke "+uuid));
        page=Math.clamp(page,0,Math.max(0,(options.size()-1)/10));var buttons=new ArrayList<>(options.subList(page*10,Math.min(options.size(),page*10+10)));
        if(page>0)buttons.add(app.action("Previous","Previous guests","/aviary manage "+port.id()+" guestpage "+(page-1)));
        if((page+1)*10<options.size())buttons.add(app.action("Next","More guests","/aviary manage "+port.id()+" guestpage "+(page+1)));
        buttons.add(app.action("Back","Perch","/aviary perch "+port.id()));
        return app.dialog(p,"Guests",options.isEmpty()?"Other players will appear here when they join.":"Invited players can visit without discovering the perch first.",buttons);
    }
    private int rename(ServerPlayer p,AviaryStore.Port port,AviaryStore.PerchData perch){
        String nonce=UUID.randomUUID().toString();forms.put(p.getUUID(),new Form(port.id(),nonce,app.store.revision,System.nanoTime()+120_000_000_000L));
        var fields=new ArrayList<Input>();fields.add(new Input("name",new TextInput(300,Component.literal("Perch name"),true,port.name(),48,Optional.empty())));
        if(perch!=null)fields.add(new Input("bird",new TextInput(300,Component.literal("Bird name"),true,perch.birdName(),32,Optional.empty())));
        var extra=new CompoundTag();extra.putString("nonce",nonce);
        var common=new CommonDialogData(Component.literal("Rename"),Optional.empty(),true,false,DialogAction.CLOSE,List.of(),fields);
        var save=new ActionButton(new CommonButtonData(Component.literal("Save"),Optional.empty(),150),Optional.of(new CustomAll(Identifier.fromNamespaceAndPath("oak_aviary","perch_edit"),Optional.of(extra))));
        p.openDialog(Holder.direct(new MultiActionDialog(common,List.of(save,app.action("Cancel","Perch","/aviary perch "+port.id())),Optional.empty(),2)));return 1;
    }
    void response(ServerPlayer p,ServerboundCustomClickActionPacket packet){
        if(packet.id().equals(Identifier.fromNamespaceAndPath("oak_aviary","perch_remove"))){remove(p,packet);return;}
        if(!packet.id().equals(Identifier.fromNamespaceAndPath("oak_aviary","perch_edit")))return;
        var form=forms.remove(p.getUUID());if(form==null)return;
        try{
            if(System.nanoTime()>form.expires())throw new IllegalArgumentException("This form expired. Open the perch again.");
            if(!(packet.payload().orElse(null) instanceof CompoundTag data)||!data.getStringOr("nonce","").equals(form.nonce()))return;
            var old=editable(p,form.port());var before=app.store.perches.get(form.port());
            if(form.revision()!=app.store.revision)throw new IllegalArgumentException("Perches changed. Open the perch again.");
            var next=new AviaryStore.Port(old.id(),data.getStringOr("name","").strip(),old.dimension(),old.x(),old.y(),old.z(),old.yaw(),old.owner(),old.shared(),old.departureYaw(),old.arrivalYaw());AviaryStore.validate(next);
            AviaryStore.PerchData updated=before==null?null:new AviaryStore.PerchData(before.x(),before.y(),before.z(),before.color(),before.style(),data.getStringOr("bird","").strip(),before.guests(),before.hub(),before.active());
            if(updated!=null)AviaryStore.validate(updated);app.store.ports.put(old.id(),next);if(updated!=null)app.store.perches.put(old.id(),updated);
            try{app.store.savePolicy();}catch(Exception e){app.store.ports.put(old.id(),old);if(before!=null)app.store.perches.put(old.id(),before);throw e;}
            app.perches.update(old.id());open(p,old.id());
        }catch(Exception e){Aviary.tell(p,e instanceof IllegalArgumentException?e.getMessage():"Could not save the perch. Try again.");}
    }
    private AviaryStore.Port removable(ServerPlayer p,String id){
        var port=app.store.ports.get(id);var perch=app.store.perches.get(id);
        if(!app.javaPlayer(p)||!owner(p,port)||perch==null||p.isSpectator()||!p.isAlive())throw new IllegalArgumentException("Only the owner can remove this perch.");
        if(app.busyPort(id)||app.journeys.containsKey(p.getUUID()))throw new IllegalArgumentException("Wait until the flight has finished.");
        if(perch.active()&&(!p.level().dimension().identifier().toString().equals(port.dimension())||p.position().distanceTo(Journey.position(port))>8))throw new IllegalArgumentException("Stand near the perch to remove it.");
        return port;
    }
    private static ItemStack pointer(ServerPlayer p,String id){
        for(int slot=0;slot<p.getInventory().getContainerSize();slot++){
            var stack=p.getInventory().getItem(slot);
            if(Perches.kind(stack).equals("perch")&&stack.getOrDefault(DataComponents.CUSTOM_DATA,CustomData.EMPTY).copyTag().getStringOr("port","").equals(id))return stack;
        }
        return null;
    }
    private int removal(ServerPlayer p,String id){
        var port=removable(p,id);var perch=app.store.perches.get(id);boolean refund=perch.active()||pointer(p,id)!=null;
        String nonce=UUID.randomUUID().toString();removals.put(p.getUUID(),new Form(id,nonce,app.store.revision,System.nanoTime()+120_000_000_000L));
        var data=new CompoundTag();data.putString("nonce",nonce);
        var confirm=new ActionButton(new CommonButtonData(Component.literal("Remove"),Optional.empty(),150),Optional.of(new CustomAll(Identifier.fromNamespaceAndPath("oak_aviary","perch_remove"),Optional.of(data))));
        var common=new CommonDialogData(Component.literal("Remove "+port.name()+"?"),Optional.empty(),true,false,DialogAction.CLOSE,List.of(new PlainMessage(Component.literal("This destination will disappear from travel lists. "+(refund?"You will receive one reusable perch kit.":"The missing kit cannot be returned.")),310)),List.of());
        p.openDialog(Holder.direct(new MultiActionDialog(common,List.of(confirm,app.action("Cancel","Keep the destination","/aviary manage "+id+(perch.active()?" open":" packed"))),Optional.empty(),2)));return 1;
    }
    private void remove(ServerPlayer p,ServerboundCustomClickActionPacket packet){
        var form=removals.remove(p.getUUID());if(form==null)return;
        try{
            if(!(packet.payload().orElse(null) instanceof CompoundTag data)||!data.getStringOr("nonce","").equals(form.nonce()))return;
            if(System.nanoTime()>form.expires()||form.revision()!=app.store.revision)throw new IllegalArgumentException("The perch changed. Open it again before removing it.");
            var port=removable(p,form.port());var perch=app.store.perches.get(form.port());var kit=pointer(p,form.port());
            if(perch.active()&&kit==null&&p.getInventory().getFreeSlot()<0)throw new IllegalArgumentException("Leave one inventory slot free for the perch kit.");
            app.store.ports.remove(form.port());app.store.perches.remove(form.port());
            try{app.store.savePolicy();}catch(Exception e){app.store.ports.put(form.port(),port);app.store.perches.put(form.port(),perch);throw e;}
            if(kit!=null){var tag=kit.getOrDefault(DataComponents.CUSTOM_DATA,CustomData.EMPTY).copyTag();tag.remove("port");kit.set(DataComponents.CUSTOM_DATA,CustomData.of(tag));}
            else if(perch.active())p.addItem(Perches.item("perch",null));
            app.perches.update(form.port());Aviary.tell(p,"Perch removed.");
        }catch(Exception e){Aviary.tell(p,e instanceof IllegalArgumentException?e.getMessage():"Could not remove the perch. Open it again to check its state.");}
    }
    private void save(String id,AviaryStore.PerchData value)throws Exception{
        AviaryStore.validate(value);var old=app.store.perches.put(id,value);
        try{app.store.savePolicy();}catch(Exception e){app.store.perches.put(id,old);throw e;}app.perches.update(id);
    }
    private static String title(String value){String text=value.replace('_',' ');return Character.toUpperCase(text.charAt(0))+text.substring(1);}
}
