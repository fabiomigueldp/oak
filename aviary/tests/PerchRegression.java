package me.oak.aviary;

import net.minecraft.core.component.DataComponents;
import net.minecraft.core.BlockPos;
import net.minecraft.core.registries.Registries;
import net.minecraft.resources.Identifier;
import net.minecraft.resources.ResourceKey;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.world.level.block.Blocks;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.item.Items;
import net.minecraft.world.item.crafting.CraftingInput;
import net.minecraft.world.item.crafting.Ingredient;
import net.minecraft.world.item.crafting.ShapedRecipe;
import java.util.List;
import java.util.LinkedHashMap;

/** Runs against actual recipe codecs and loaded mixins in the isolated server. */
final class PerchRegression {
    static void lifecycle(Aviary app,ServerPlayer player)throws Exception{
        if(!"isolated".equals(System.getProperty("oak.aviary.smoke")))throw new IllegalStateException("Isolated server required");
        if(!app.journeys.isEmpty())throw new IllegalStateException("Run support checks before flights");
        var level=player.level();var a=new BlockPos(20,79,20);var b=new BlockPos(28,79,20);
        var blockA=level.getBlockState(a);var blockB=level.getBlockState(b);
        var ports=new LinkedHashMap<>(app.store.ports);var perches=new LinkedHashMap<>(app.store.perches);String error=app.store.error;
        var lifecycle=new Perches(app);
        try{
            for(var support:List.of(a,b)){
                level.setBlock(support,Blocks.STONE.defaultBlockState(),3);
                if(!Perches.supportValid(level,support))throw new AssertionError("Safe perch support rejected");
                String id=support.equals(a)?"support_a":"support_b";
                app.store.ports.put(id,new AviaryStore.Port(id,id,"minecraft:overworld",support.getX()+.5,support.getY()+1.75,support.getZ()+.5,0,player.getUUID().toString(),false));
                app.store.perches.put(id,new AviaryStore.PerchData(support.getX(),support.getY(),support.getZ(),"green",true));
            }
            app.store.savePolicy();long revision=app.store.revision;
            level.setBlock(a,Blocks.MAGMA_BLOCK.defaultBlockState(),3);
            if(Perches.supportValid(level,a))throw new AssertionError("Magma accepted as safe perch support");
            level.setBlock(b,Blocks.AIR.defaultBlockState(),3);
            // Inject a persistence refusal before the write. Both addresses must
            // retain their in-memory state until one durable batch succeeds.
            app.store.error="Injected support-state persistence refusal";
            for(int i=0;i<20;i++)lifecycle.tick();
            if(!app.store.perches.get("support_a").active()||!app.store.perches.get("support_b").active()||app.store.revision!=revision)throw new AssertionError("Support-state failure did not roll back the batch");
            app.store.error=error;
            for(int i=0;i<100;i++)lifecycle.tick();
            if(app.store.perches.get("support_a").active()||app.store.perches.get("support_b").active())throw new AssertionError("Damaged perches were not retired for recovery");
            if(app.store.revision!=revision+1)throw new AssertionError("Support losses were not saved in one batch");
            try(var reloaded=new AviaryStore()){
                if(!reloaded.error.isEmpty()||reloaded.perches.get("support_a").active()||reloaded.perches.get("support_b").active())throw new AssertionError("Damaged perch state was not durable");
            }
            System.out.println("AVIARY_SMOKE support hazards, batch rollback and durable recovery passed");
        }finally{
            lifecycle.close();app.store.error=error;app.store.ports.clear();app.store.ports.putAll(ports);app.store.perches.clear();app.store.perches.putAll(perches);
            level.setBlock(a,blockA,3);level.setBlock(b,blockB,3);app.store.savePolicy();
        }
    }
    static void check(ServerLevel level){
        var copper=new ItemStack(Items.COPPER_INGOT);var wood=new ItemStack(Items.OAK_PLANKS);
        checkRecipe(level,"perch",CraftingInput.of(3,3,List.of(copper,new ItemStack(Items.LEATHER),copper,ItemStack.EMPTY,new ItemStack(Items.STICK),ItemStack.EMPTY,wood,wood,wood)));
        checkRecipe(level,"whistle",CraftingInput.of(2,2,List.of(new ItemStack(Items.BONE),copper,ItemStack.EMPTY,new ItemStack(Items.STRING))));
        var vanilla=Ingredient.of(Items.DISC_FRAGMENT_5);
        if(!vanilla.test(new ItemStack(Items.DISC_FRAGMENT_5)))throw new AssertionError("Ordinary ingredients changed");
        if(vanilla.test(Perches.item("perch",null))||vanilla.test(Perches.item("whistle",null)))throw new AssertionError("Aviary tools can be consumed by vanilla recipes");
        System.out.println("AVIARY_SMOKE native recipes and reserved-item guards passed");
    }
    private static void checkRecipe(ServerLevel level,String kind,CraftingInput input){
        var key=ResourceKey.create(Registries.RECIPE,Identifier.parse("oak_aviary:"+kind));
        var holder=level.recipeAccess().byKey(key).orElseThrow(()->new AssertionError("Missing Aviary recipe: "+kind));
        if(!(holder.value() instanceof ShapedRecipe recipe)||!recipe.matches(input,level))throw new AssertionError("Recipe does not match: "+kind);
        var item=recipe.assemble(input);
        if(!Perches.kind(item).equals(kind)||item.getCount()!=1||!Integer.valueOf(1).equals(item.get(DataComponents.MAX_STACK_SIZE))||!Identifier.parse("oak_aviary:"+kind).equals(item.get(DataComponents.ITEM_MODEL)))throw new AssertionError("Invalid crafted travel item: "+kind);
    }
}
