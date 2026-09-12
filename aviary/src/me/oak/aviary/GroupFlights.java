package me.oak.aviary;

import java.util.*;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.server.dialog.ActionButton;

/** Two consenting riders share a destination; each keeps their own bird and camera. */
final class GroupFlights {
    private record Invitation(UUID token,UUID leader,UUID friend,String origin,String destination,long expires) {}
    private final Aviary app;
    private final Map<UUID,Invitation> invitations=new LinkedHashMap<>();
    GroupFlights(Aviary app){this.app=app;}
    private Invitation pending(UUID player){return invitations.values().stream().filter(i->i.leader().equals(player)||i.friend().equals(player)).findFirst().orElse(null);}
    boolean pending(ServerPlayer p){return pending(p.getUUID())!=null;}
    int menu(ServerPlayer p,String destination){
        try{
            var origin=app.nearby(p);var port=app.store.ports.get(destination);
            if(origin==null||port==null||!Aviary.visible(port,p)||origin.id().equals(destination))throw new IllegalArgumentException("Stand near a perch and choose another destination.");
            var buttons=new ArrayList<ActionButton>();
            for(var friend:app.server.getPlayerList().getPlayers())if(friend!=p&&eligible(friend,origin,port)&&pending(friend.getUUID())==null)buttons.add(app.action(friend.getGameProfile().name(),"Invite to "+port.name(),"/aviary companion "+destination+" "+friend.getUUID()));
            boolean empty=buttons.isEmpty();buttons.add(app.action("Back","Destination","/aviary destination "+destination));
            return app.dialog(p,"Fly with a friend",empty?"A friend with the resource pack must stand at this perch.":"Each rider has a bird. Takeoff and landing are staggered.",buttons);
        }catch(Exception e){Aviary.tell(p,e.getMessage());return 0;}
    }
    private boolean eligible(ServerPlayer p,AviaryStore.Port origin,AviaryStore.Port destination){
        return app.javaPlayer(p)&&app.loaded(p)&&p.isAlive()&&!p.isPassenger()&&!p.isSleeping()&&!p.isSpectator()&&p.level()==app.server.overworld()&&p.position().distanceTo(Journey.position(origin))<6&&app.accessible(origin,p)&&app.accessible(destination,p)&&!app.journeys.containsKey(p.getUUID())&&!app.store.recoveries.containsKey(p.getUUID())&&!app.queued(p.getUUID());
    }
    int invite(ServerPlayer leader,String id,String friendId){
        try{
            if(!app.store.settings.enabled()||!app.store.error.isEmpty())throw new IllegalArgumentException("Aviary is unavailable.");
            var origin=app.nearby(leader);var destination=app.store.ports.get(id);var friend=app.server.getPlayerList().getPlayer(UUID.fromString(friendId));
            if(origin==null||destination==null||friend==null||friend==leader||origin.id().equals(id)||!Aviary.visible(destination,leader)||!eligible(leader,origin,destination)||!eligible(friend,origin,destination))throw new IllegalArgumentException("Both riders must stand at this perch and have access to the destination.");
            if(app.store.settings.maxFlights()<2)throw new IllegalArgumentException("Friend trips need at least two available birds.");
            if(app.busyPort(origin.id())||app.busyPort(id)||app.journeys.size()+2>app.store.settings.maxFlights())throw new IllegalArgumentException("Wait until two birds and both perches are available.");
            if(pending(leader.getUUID())!=null||pending(friend.getUUID())!=null)throw new IllegalArgumentException("Finish or cancel the current invitation first.");
            if(invitations.size()>=16)throw new IllegalArgumentException("Try again shortly.");
            var invitation=new Invitation(UUID.randomUUID(),leader.getUUID(),friend.getUUID(),origin.id(),id,System.nanoTime()+60_000_000_000L);invitations.put(invitation.token(),invitation);
            pendingMenu(friend);return pendingMenu(leader);
        }catch(Exception e){Aviary.tell(leader,e instanceof IllegalArgumentException?e.getMessage():"Could not invite your friend.");return 0;}
    }
    int pendingMenu(ServerPlayer p){
        var invitation=pending(p.getUUID());if(invitation==null)return app.menu(p);
        var destination=app.store.ports.get(invitation.destination());var leader=app.server.getPlayerList().getPlayer(invitation.leader());var friend=app.server.getPlayerList().getPlayer(invitation.friend());
        if(destination==null||leader==null||friend==null){invitations.remove(invitation.token());return app.menu(p);}
        if(invitation.friend().equals(p.getUUID()))return app.dialog(p,"Fly together",leader.getGameProfile().name()+" invites you to "+destination.name()+".",List.of(app.action("Accept","Call both birds","/aviary accept "+invitation.token()),app.action("Decline","Stay here","/aviary decline "+invitation.token())));
        return app.dialog(p,"Invitation sent","Waiting for "+friend.getGameProfile().name()+". Stay near the perch.",List.of(app.action("Cancel","Cancel invitation","/aviary decline "+invitation.token())));
    }
    int accept(ServerPlayer friend,String token){
        Invitation invitation;
        try{invitation=invitations.get(UUID.fromString(token));}catch(Exception e){return 0;}
        if(invitation==null||!invitation.friend().equals(friend.getUUID()))return 0;
        invitations.remove(invitation.token());var leader=app.server.getPlayerList().getPlayer(invitation.leader());
        try{
            if(System.nanoTime()>invitation.expires()||leader==null)throw new IllegalArgumentException("The invitation expired.");
            if(!app.store.settings.enabled()||!app.store.error.isEmpty())throw new IllegalArgumentException("Aviary is unavailable.");
            var origin=app.store.ports.get(invitation.origin());var destination=app.store.ports.get(invitation.destination());
            if(origin==null||destination==null||!eligible(leader,origin,destination)||!eligible(friend,origin,destination)||!Aviary.visible(destination,leader))throw new IllegalArgumentException("Both riders must stay near the perch and have access to the destination.");
            var anchor=app.store.perches.get(origin.id());var target=app.store.perches.get(destination.id());
            if((anchor!=null&&!anchor.active())||(target!=null&&!target.active()))throw new IllegalArgumentException("A perch was moved. Call again after it is placed.");
            if(app.journeys.size()+2>app.store.settings.maxFlights()||app.busyPort(origin.id())||app.busyPort(destination.id()))throw new IllegalArgumentException("The route is busy. Invite your friend again when two birds are available.");
            boolean quick=app.store.preferences(leader.getUUID()).quick();String group=invitation.token().toString();
            Journey first=new Journey(app,leader,origin,destination,group,quick);app.journeys.put(leader.getUUID(),first);
            try{Journey second=new Journey(app,friend,origin,destination,group,quick);app.journeys.put(friend.getUUID(),second);}
            catch(Exception e){first.abort("Could not call both birds. Try again.");throw e;}
            app.discover(friend,destination);Aviary.tell(leader,"Your birds are on their way. Use the saddle to board.");Aviary.tell(friend,"Your birds are on their way. Your bird follows after the first takeoff.");return 1;
        }catch(Exception e){String message=e instanceof IllegalArgumentException?e.getMessage():"Could not call both birds. Try again.";Aviary.tell(friend,message);if(leader!=null)Aviary.tell(leader,message);return 0;}
    }
    int decline(ServerPlayer p,String token){
        try{var invitation=invitations.get(UUID.fromString(token));if(invitation==null||(!invitation.leader().equals(p.getUUID())&&!invitation.friend().equals(p.getUUID())))return 0;
            invitations.remove(invitation.token());notifyOther(invitation,p.getUUID(),"Invitation cancelled.");return 1;
        }catch(Exception e){return 0;}
    }
    void cancel(UUID player){var invitation=pending(player);if(invitation!=null){invitations.remove(invitation.token());notifyOther(invitation,player,"Invitation cancelled.");}}
    private void notifyOther(Invitation invitation,UUID player,String text){UUID other=invitation.leader().equals(player)?invitation.friend():invitation.leader();var p=app.server.getPlayerList().getPlayer(other);if(p!=null)Aviary.tell(p,text);}
    void tick(){
        for(var invitation:List.copyOf(invitations.values())){
            var leader=app.server.getPlayerList().getPlayer(invitation.leader());var friend=app.server.getPlayerList().getPlayer(invitation.friend());var origin=app.store.ports.get(invitation.origin());var destination=app.store.ports.get(invitation.destination());
            if(System.nanoTime()>invitation.expires()||leader==null||friend==null||origin==null||destination==null||!eligible(leader,origin,destination)||!eligible(friend,origin,destination)){
                invitations.remove(invitation.token());if(leader!=null)Aviary.tell(leader,"Invitation ended. Call again when you are both ready.");if(friend!=null)Aviary.tell(friend,"Invitation ended.");
            }
        }
    }
}
