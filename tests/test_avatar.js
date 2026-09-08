/* Geometry-only smoke checks against the same Three.js release as BlueMap. No GPU. */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const threePath = process.env.OAK_TEST_THREE;
test('avatar geometry, equipment replacement, culling and disposal', {skip: !threePath}, async () => {
  const Three = {...require(threePath)};
  Three.TextureLoader = class { load(url, done) {
    const texture = new Three.Texture({width:64,height:64});
    queueMicrotask(() => done(texture));
  }};
  const window = {BlueMap:{Three},matchMedia:()=>({matches:false})};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../public/map-player-avatar.js'),'utf8'), {
    window,document:{hidden:false},parent:{postMessage(){}},location:{origin:'https://oak.test'},
    Date,Float32Array,Map,Set,Math,Promise
  });
  const marker = new Three.Group();
  const camera = new Three.PerspectiveCamera(75,1,.1,10000);
  camera.position.set(0,2,10);camera.lookAt(0,1,0);camera.updateMatrixWorld();
  const avatar = new window.OakPlayerAvatar(marker,{camera,redraw(){}});
  const player = {name:'Synthetic',yaw:0,body_yaw:0,pitch:0,pose:'STANDING',grounded:true,swing_id:0,
    appearance:{slim:true,equipment:{head:{id:'minecraft:diamond_helmet',asset:'minecraft:diamond'},mainhand:{id:'minecraft:diamond_sword'}}}};
  avatar.update(player);
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(marker.children.length,1);
  avatar.render([0,0,0],player,player,1,false);
  assert.equal(avatar.root.visible,true);
  let meshes=0;
  avatar.root.traverse(object => {if(object.isMesh){meshes++;assert.ok(object.geometry.attributes.position.count>0);}});
  assert.ok(meshes>=14);
  player.appearance.equipment={};avatar.update(player);
  let unequipped=0;avatar.root.traverse(o=>{if(o.isMesh)unequipped++;});
  assert.equal(unequipped,12);
  camera.position.set(0,2,500);camera.updateMatrixWorld();
  avatar.render([0,0,0],player,player,1,false);
  assert.equal(avatar.root.visible,false);
  avatar.dispose();assert.equal(marker.children.length,0);
});
