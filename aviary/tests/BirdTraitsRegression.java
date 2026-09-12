package me.oak.aviary;

/** Pure acting checks. This can also run without a Minecraft runtime. */
public final class BirdTraitsRegression {
    public static void main(String[] args){check();}
    static void check(){
        var one=new BirdTraits("home","harbor","Fern");
        var same=new BirdTraits("home","harbor","Fern");
        var other=new BirdTraits("ridge","harbor","Ash");
        if(!one.identityName().equals("Fern"))throw new AssertionError("Bird name lost");
        int quiet=0,different=0;
        double lastLook=one.lookOffset(0),lastNod=one.nod(0);
        for(int tick=0;tick<1200;tick++){
            double look=one.lookOffset(tick),nod=one.nod(tick);
            if(look!=same.lookOffset(tick)||nod!=same.nod(tick))throw new AssertionError("Idle acting is not repeatable");
            if(look!=other.lookOffset(tick))different++;
            if(look==0&&nod==0)quiet++;
            if(!Double.isFinite(look)||Math.abs(look)>.131||Math.abs(nod)>.019)throw new AssertionError("Idle acting exceeds bounds");
            if(Math.abs(look-lastLook)>.02||Math.abs(nod-lastNod)>.01)throw new AssertionError("Idle acting snaps");
            lastLook=look;lastNod=nod;
        }
        if(quiet<600||different<100)throw new AssertionError("Idle acting lacks pauses or identity");
        var bird=new BirdTraits(42);
        if(bird.feed(-1)||!bird.feed(100)||bird.feed(100)||bird.feed(99)||bird.feed(259))throw new AssertionError("Feeding cooldown failed");
        lastLook=bird.lookOffset(99);lastNod=bird.nod(99);
        for(int tick=100;tick<=160;tick++){
            double look=bird.lookOffset(tick),nod=bird.nod(tick);
            if(!Double.isFinite(nod)||Math.abs(nod)>.181||Math.abs(look)>.131)throw new AssertionError("Feeding acting exceeds bounds");
            if(Math.abs(nod-lastNod)>.035||Math.abs(look-lastLook)>.035)throw new AssertionError("Feeding acting snaps");
            if(bird.feeding(tick)!=(tick<152))throw new AssertionError("Feeding duration changed");
            lastLook=look;lastNod=nod;
        }
        if(!bird.feed(260)||!bird.feeding(260))throw new AssertionError("Feeding cooldown never recovers");
        if(bird.lookOffset(-1)!=0||bird.nod(-1)!=0)throw new AssertionError("Negative age produces acting");
        System.out.println("AVIARY_SMOKE bird identity, idle continuity and feeding cooldown passed");
    }
}
