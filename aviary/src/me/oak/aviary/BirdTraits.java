package me.oak.aviary;

/** Quiet, repeatable idle acting. All angles are radians and all ages are journey ticks.
 * Feeding is a short response, not hunger, health, or a prerequisite for travel.
 */
final class BirdTraits {
    static final int FEED_TICKS=52;
    static final int FEED_COOLDOWN=160;
    private final long seed;
    private final int glancePeriod,glanceOffset;
    private final double glanceSize;
    private final String name;
    private long fedAt=-1;

    BirdTraits(long seed){this(seed,"Condor");}
    private BirdTraits(long seed,String name){
        this.seed=mix(seed);
        glancePeriod=180+(int)Math.floorMod(this.seed,101);
        glanceOffset=(int)Math.floorMod(mix(this.seed+1),glancePeriod);
        glanceSize=.07+.06*unit(mix(this.seed+2));
        this.name=name==null||name.isBlank()?"Condor":name;
    }
    BirdTraits(String origin,String destination,String name){
        this(hash(String.valueOf(origin)+"\0"+String.valueOf(destination)),name);
    }

    String identityName(){return name;}

    /** Request a response only after the caller verifies proximity, stage and item. */
    boolean feed(long age){
        if(age<0||(fedAt>=0&&(age<fedAt||age-fedAt<FEED_COOLDOWN)))return false;
        fedAt=age;return true;
    }
    boolean feeding(long age){return fedAt>=0&&age>=fedAt&&age-fedAt<FEED_TICKS;}

    /** A brief look, a hold, and a slower return, separated by long neutral pauses. */
    double lookOffset(long age){
        if(age<0)return 0;
        long shifted=age+glanceOffset;
        long cycle=Math.floorDiv(shifted,glancePeriod);
        double phase=Math.floorMod(shifted,glancePeriod);
        double direction=(mix(seed+cycle)&1)==0?-1:1;
        double size=glanceSize*(.8+.2*unit(mix(seed+cycle+17)));
        double look=direction*size*pulse(phase,20,38,58,88);
        if(fedAt>=0&&age>=fedAt)look*=1-pulse(age-fedAt,0,10,40,FEED_TICKS);
        return look;
    }

    /** A small delayed head dip accompanies attention; a feeding dip has clear beats. */
    double nod(long age){
        if(age<0)return 0;
        double phase=Math.floorMod(age+glanceOffset,glancePeriod);
        double idle=.018*pulse(phase,38,49,56,75);
        if(fedAt<0||age<fedAt)return idle;
        double t=age-fedAt;
        if(t>=FEED_TICKS)return idle;
        double response;
        if(t<7)response=blend(0,-.025,t/7);
        else if(t<21)response=blend(-.025,.18,(t-7)/14);
        else if(t<30)response=blend(.18,.14,(t-21)/9);
        else if(t<43)response=blend(.14,-.012,(t-30)/13);
        else response=blend(-.012,0,(t-43)/9);
        return idle*(1-pulse(t,0,7,43,FEED_TICKS))+response;
    }

    private static double pulse(double t,double start,double in,double out,double end){
        if(t<=start||t>=end)return 0;
        if(t<in)return ease((t-start)/(in-start));
        if(t<=out)return 1;
        return 1-ease((t-out)/(end-out));
    }
    private static double blend(double a,double b,double t){return a+(b-a)*ease(t);}
    private static double ease(double t){t=Math.max(0,Math.min(1,t));return t*t*t*(t*(t*6-15)+10);}
    private static double unit(long bits){return (bits>>>11)*0x1.0p-53;}
    private static long hash(String value){
        long result=0xcbf29ce484222325L;
        for(int i=0;i<value.length();i++){result^=value.charAt(i);result*=0x100000001b3L;}
        return result;
    }
    private static long mix(long value){
        value=(value^(value>>>30))*0xbf58476d1ce4e5b9L;
        value=(value^(value>>>27))*0x94d049bb133111ebL;
        return value^(value>>>31);
    }
}
