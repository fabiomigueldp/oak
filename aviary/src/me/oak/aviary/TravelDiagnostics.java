package me.oak.aviary;

import com.google.gson.*;
import java.util.*;

/** Bounded, runtime-only evidence. Contains no player identities or client memory claims. */
final class TravelDiagnostics {
    private record Issue(double at,String destination,String message) {}
    private final ArrayDeque<Issue> issues=new ArrayDeque<>();
    private long calls,completed,failed,ticks,preparations;
    private double totalTickMs,maxTickMs,totalPreparationMs,maxPreparationMs;
    void called(){calls++;}
    void completed(){completed++;}
    void failed(String destination,String message){
        failed++;
        if(issues.size()==16)issues.removeFirst();
        issues.addLast(new Issue(System.currentTimeMillis()/1000.0,destination,message));
    }
    void tick(long nanos){double ms=nanos/1_000_000.0;ticks++;totalTickMs+=ms;maxTickMs=Math.max(maxTickMs,ms);}
    void prepared(long nanos){double ms=nanos/1_000_000.0;preparations++;totalPreparationMs+=ms;maxPreparationMs=Math.max(maxPreparationMs,ms);}
    JsonObject status(){
        var d=new JsonObject();d.addProperty("calls",calls);d.addProperty("completed",completed);d.addProperty("failed",failed);
        d.addProperty("samples",ticks);d.addProperty("meanTickMs",ticks==0?0:totalTickMs/ticks);d.addProperty("maxTickMs",maxTickMs);
        d.addProperty("preparations",preparations);d.addProperty("meanPreparationMs",preparations==0?0:totalPreparationMs/preparations);d.addProperty("maxPreparationMs",maxPreparationMs);
        d.add("issues",new Gson().toJsonTree(issues));return d;
    }
}
