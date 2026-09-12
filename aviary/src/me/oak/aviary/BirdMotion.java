package me.oak.aviary;

import com.google.gson.JsonParser;
import java.io.InputStreamReader;
import java.nio.charset.StandardCharsets;
import java.util.Objects;

/** Shared Blender samples, blended without restarting the wing cycle. */
final class BirdMotion {
    static final int CHANNELS=6;
    private static final double[][][] CLIPS=new double[3][][];
    private static final int PERIOD;
    static {
        try(var reader=new InputStreamReader(Objects.requireNonNull(BirdMotion.class.getResourceAsStream("/condor-motion.json")),StandardCharsets.UTF_8)) {
            var data=JsonParser.parseReader(reader).getAsJsonObject();
            PERIOD=data.get("period").getAsInt();
            String[] names={"rest","glide","power"};
            for(int clip=0;clip<names.length;clip++) {
                var rows=data.getAsJsonObject("clips").getAsJsonArray(names[clip]);
                CLIPS[clip]=new double[rows.size()][CHANNELS];
                for(int row=0;row<rows.size();row++)for(int c=0;c<CHANNELS;c++)
                    CLIPS[clip][row][c]=rows.get(row).getAsJsonArray().get(c).getAsDouble();
            }
        } catch(Exception e){throw new ExceptionInInitializerError(e);}
    }
    final double[] angles=new double[CHANNELS];
    double heave,bank,pitch,headYaw,sweep,fold,contact,tailYaw,brake,tuck;
    private double airborne,power,cycle,heaveVelocity;
    private int previousTick=-1;
    private String previousAction="";

    boolean update(int tick,String phase,double clearance) {
        return update(tick,phase,clearance,.6,0,0,0);
    }
    boolean update(int tick,String phase,double clearance,double speed,double climb,double turn,double progress) {
        progress=Math.clamp(progress,0,1);
        boolean grounded=phase.equals("board")||phase.equals("settle")||phase.equals("greet");
        double airTarget=grounded?0:Math.max(FlightPath.ease(clearance/2.2),phase.equals("depart")?.8:0);
        if(previousAction.equals("board")&&phase.equals("depart")){cycle=Math.ceil(cycle);airborne=1;}
        // Quiet glides alternate with complete effort bouts, never random joint noise.
        double demand=phase.equals("cruise")?Math.clamp(Math.max(0,climb)*4+Math.max(0,.35-speed)*.35,0,1)
            :phase.equals("arrive")||phase.equals("call")?.35+.25*(1-progress):1;
        if(phase.equals("cruise")&&demand<.28)demand=0;
        int elapsed=previousTick<0?1:Math.clamp(tick-previousTick,0,4);
        double blend=1-Math.exp(-elapsed/8.0);
        double tuckTarget=phase.equals("board")?1-.25*FlightPath.ease((progress-.55)/.45)
            :phase.equals("settle")||phase.equals("greet")?1
            :phase.equals("depart")?.75*(1-FlightPath.ease(clearance/2.5))
            :phase.equals("arrive")||phase.equals("call")?1-FlightPath.ease(clearance/2.5):0;
        tuck+=(tuckTarget-tuck)*blend;
        if(previousTick<0){airborne=airTarget;power=demand;}
        else {airborne+=(airTarget-airborne)*blend;power+=(demand-power)*blend;}
        double oldCycle=cycle;
        cycle+=elapsed/(52-14*power)*(1+.045*Math.sin(tick*.073));
        for(int c=0;c<CHANNELS;c++) {
            double flying=sample(1,cycle*PERIOD,c)*(1-power)+sample(2,cycle*PERIOD,c)*power;
            angles[c]=sample(0,cycle*PERIOD,c)*(1-airborne)+flying*airborne;
        }
        contact=1-FlightPath.ease(clearance/.65);
        angles[4]*=FlightPath.ease(clearance/2.3);
        if(phase.equals("board")) {
            double prepare=FlightPath.ease((progress-.68)/.32);
            angles[0]+=(.58-angles[0])*prepare;angles[1]+=(.12-angles[1])*prepare;
        }
        double targetBank=Math.clamp(-turn*.09,-.14,.14)*airborne;
        double targetPitch=Math.clamp(climb*.5,-.12,.16)*airborne;
        if(phase.equals("arrive"))targetPitch+=.13*Math.sin(Math.PI*progress)*airborne;
        bank+=(targetBank-bank)*blend;pitch+=(targetPitch-pitch)*blend;
        double look=grounded?.28*Math.sin(Math.PI*FlightPath.ease(Math.min(1,progress/.7))):Math.clamp(-turn*.16,-.28,.28);
        if(phase.equals("greet"))look=Math.clamp(Math.toRadians(-turn),-.45,.45);
        headYaw+=(look-headYaw)*(1-Math.exp(-elapsed/3.5));
        tailYaw+=(Math.clamp(turn*.075,-.15,.15)-tailYaw)*(1-Math.exp(-elapsed/13.0));
        double braking=(phase.equals("arrive")||phase.equals("call"))?Math.sin(Math.PI*progress):0;
        brake+=(braking-brake)*blend;
        angles[2]-=pitch*.65;
        angles[3]+=-pitch*.4+bank*.2+brake*.16;
        fold=FlightPath.ease((Math.sin((cycle%1-.38)*Math.PI/.62)))*power*airborne;
        sweep=.22*fold;
        boolean downstroke=Math.floor(oldCycle-.16)!=Math.floor(cycle-.16)&&airborne>.4&&power>.35;
        double crouch=phase.equals("board")?-.045*Math.sin(Math.PI*Math.min(1,progress/.35))-.11*FlightPath.ease((progress-.55)/.45)
            :phase.equals("settle")?-.12*Math.sin(Math.min(1,progress/.45)*Math.PI)*Math.exp(-progress*2)
            :phase.equals("depart")?.065*Math.sin(Math.PI*Math.min(1,progress/.16)):0;
        for(int i=0;i<elapsed;i++) {
            if(i==0&&downstroke)heaveVelocity+=.025*power;
            heaveVelocity+=(crouch-heave)*.08-heaveVelocity*.30;
            heave=Math.clamp(heave+heaveVelocity,-.14,.16);
        }
        previousTick=tick;
        previousAction=phase;
        return downstroke;
    }
    private static double sample(int clip,double tick,int channel) {
        var frames=CLIPS[clip];double frame=(tick%PERIOD)*frames.length/PERIOD;
        int a=(int)frame,b=(a+1)%frames.length;double t=frame-a;
        return frames[a][channel]+(frames[b][channel]-frames[a][channel])*t;
    }
}
