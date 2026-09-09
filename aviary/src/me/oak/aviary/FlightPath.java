package me.oak.aviary;

/** Pure path functions; no world access, allocation-heavy search, or tick-rate changes. */
public final class FlightPath {
    private FlightPath() {}
    public static double ease(double t) { t=Math.max(0,Math.min(1,t));return t*t*(3-2*t); }
    public static double mix(double a,double b,double t) { return a+(b-a)*ease(t); }
    public static double distance(double x,double z,double a,double b) { return Math.hypot(a-x,b-z); }
    public static int cruiseTicks(double distance) { return Math.max(20,Math.min(800,(int)Math.ceil(distance/24*20))); }
    public static double arc(double start,double finish,double ceiling,double t) {
        double base=mix(start,finish,t);
        return base+Math.sin(Math.PI*Math.max(0,Math.min(1,t)))*Math.max(0,ceiling-Math.max(start,finish));
    }
}
