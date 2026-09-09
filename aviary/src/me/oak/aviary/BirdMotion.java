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
    private double airborne,power;
    private int previousTick=-1;

    boolean update(int tick,String phase,double clearance) {
        double airTarget=phase.equals("board")?0:FlightPath.ease(clearance/2.5);
        // Two strong cycles followed by a longer glide, with gradual crossfades.
        double envelope=phase.equals("cruise")?FlightPath.ease((Math.cos(tick*Math.PI/144)+.15)/.65):1;
        int elapsed=previousTick<0?0:Math.max(0,tick-previousTick);
        double blend=1-Math.exp(-elapsed/9.0);
        if(previousTick<0){airborne=airTarget;power=envelope;}
        else {airborne+=(airTarget-airborne)*blend;power+=(envelope-power)*blend;}
        for(int c=0;c<CHANNELS;c++) {
            double flying=sample(1,tick,c)*(1-power)+sample(2,tick,c)*power;
            angles[c]=sample(0,tick,c)*(1-airborne)+flying*airborne;
        }
        // Keep claws and wingtips clear of the deck while settling.
        angles[4]*=FlightPath.ease(clearance/1.8);
        boolean downstroke=previousTick>=0&&tick/PERIOD==previousTick/PERIOD
            &&previousTick%PERIOD<8&&tick%PERIOD>=8&&airborne>.55&&power>.4;
        previousTick=tick;
        return downstroke;
    }
    private static double sample(int clip,int tick,int channel) {
        var frames=CLIPS[clip];double frame=Math.floorMod(tick,PERIOD)*(double)frames.length/PERIOD;
        int a=(int)frame,b=(a+1)%frames.length;double t=frame-a;
        return frames[a][channel]+(frames[b][channel]-frames[a][channel])*t;
    }
}
