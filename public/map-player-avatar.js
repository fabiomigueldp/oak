/* Oak player geometry uses BlueMap's Three.js instance and existing render pass. */
(() => {
  'use strict';
  const cache = new Map();
  const endpoint = '/admin/api/avatar/';
  const rad = Math.PI / 180;
  const angle = (a, b, t) => a + ((b - a + 540) % 360 - 180) * t;
  function texture(T, url) {
    if (cache.has(url)) return cache.get(url);
    const entry = {refs: 0, texture: null, promise: null};
    entry.promise = new Promise(resolve => {
      new T.TextureLoader().load(url, value => {
        value.magFilter = value.minFilter = T.NearestFilter;
        value.generateMipmaps = false;
        if (T.SRGBColorSpace) value.colorSpace = T.SRGBColorSpace;
        else value.encoding = T.sRGBEncoding;
        entry.texture = value;
        resolve(value);
      }, undefined, () => resolve(null));
    });
    cache.set(url, entry);
    return entry;
  }
  function trimCache() {
    for (const [key, entry] of cache) {
      if (cache.size <= 48) break;
      if (!entry.refs) {
        entry.promise.then(value => value?.dispose());
        cache.delete(key);
      }
    }
  }
  class Avatar {
    constructor(marker, viewer) {
      this.T = window.BlueMap.Three;
      this.viewer = viewer;
      this.root = new this.T.Group();
      this.root.scale.setScalar(1.8 / 32);
      marker.add(this.root);
      this.root.onClick = () => {
        parent.postMessage({type: 'oak-admin-select', name: this.name}, location.origin);
        return true;
      };
      this.refs = new Set();
      this.parts = {};
      this.disposed = false;
      this.phase = 0;
      this.lastPosition = null;
      this.appearanceKey = '';
      this.lastSwing = undefined;
      this.swingAt = 0;
      this.build({});
    }
    material(url, color = 0xffffff) {
      const T = this.T;
      const material = new T.MeshBasicMaterial({color, alphaTest: .12, side: T.FrontSide});
      const entry = texture(T, url);
      if (!this.refs.has(entry)) { entry.refs++; this.refs.add(entry); }
      entry.promise.then(value => {
        if (this.disposed || !value || material.userData.disposed) return;
        material.map = value;
        material.needsUpdate = true;
        this.viewer.redraw();
      });
      return material;
    }
    box(parent, size, center, uv, material, atlas = [64, 64], inflate = 0) {
      const T = this.T, [w, h, d] = size;
      const geometry = new T.BoxGeometry(w + inflate * 2, h + inflate * 2, d + inflate * 2);
      const [x, y] = uv;
      const faces = [[x+d+w,y+d,d,h], [x,y+d,d,h], [x+d,y,w,d],
        [x+d+w,y,w,d], [x+d,y+d,w,h], [x+d+w+d,y+d,w,h]];
      const coords = geometry.attributes.uv;
      faces.forEach(([u,v,width,height], face) => {
        const values = [[u,v], [u+width,v], [u,v+height], [u+width,v+height]];
        values.forEach(([a,b], i) => coords.setXY(face*4+i, a/atlas[0], 1-b/atlas[1]));
      });
      // Subtle face shading needs no per-player light or shadow pass.
      const colors = new Float32Array(24 * 3);
      [.86,.86,1,.65,.96,.78].forEach((shade, face) => {
        for (let i=0;i<12;i++) colors[face*12+i] = shade;
      });
      geometry.setAttribute('color', new T.BufferAttribute(colors, 3));
      material.vertexColors = true;
      const mesh = new T.Mesh(geometry, material);
      mesh.position.set(...center);
      parent.add(mesh);
      return mesh;
    }
    pivot(name, x, y, z) {
      const group = new this.T.Group();
      group.position.set(x,y,z);
      this.body.add(group);
      this.parts[name] = group;
      return group;
    }
    clear() {
      this.root.traverse(object => {
        object.geometry?.dispose();
        if (object.material) {
          object.material.userData.disposed = true;
          object.material.dispose();
        }
      });
      this.root.clear();
      for (const entry of this.refs) entry.refs--;
      this.refs.clear();
      trimCache();
    }
    build(appearance) {
      this.clear();
      this.body = new this.T.Group();
      this.root.add(this.body);
      this.parts = {};
      const url = appearance.skin ? endpoint+'skin/'+appearance.skin : endpoint+'texture/entity/player/wide/steve.png';
      const slim = appearance.slim === true, arm = slim ? 3 : 4;
      const skin = () => this.material(url);
      const head = this.pivot('head',0,24,0);
      this.box(head,[8,8,8],[0,4,0],[0,0],skin());
      this.box(head,[8,8,8],[0,4,0],[32,0],skin(),[64,64],.25);
      const torso = this.pivot('torso',0,24,0);
      this.box(torso,[8,12,4],[0,-6,0],[16,16],skin());
      this.box(torso,[8,12,4],[0,-6,0],[16,32],skin(),[64,64],.125);
      for (const [name,x,uv,overlay] of [['rightArm',-4-arm/2,[40,16],[40,32]],['leftArm',4+arm/2,[32,48],[48,48]]]) {
        const group = this.pivot(name,x,24,0);
        this.box(group,[arm,12,4],[0,-6,0],uv,skin());
        this.box(group,[arm,12,4],[0,-6,0],overlay,skin(),[64,64],.125);
      }
      for (const [name,x,uv,overlay] of [['rightLeg',-2,[0,16],[0,32]],['leftLeg',2,[16,48],[0,48]]]) {
        const group = this.pivot(name,x,12,0);
        this.box(group,[4,12,4],[0,-6,0],uv,skin());
        this.box(group,[4,12,4],[0,-6,0],overlay,skin(),[64,64],.125);
      }
      this.equipment(appearance.equipment || {});
      if (appearance.cape && appearance.equipment?.chest?.id !== 'minecraft:elytra') {
        const cape = this.pivot('cape',0,24,-2.5);
        cape.rotation.x = -.1;
        this.box(cape,[10,16,1],[0,-8,0],[0,0],this.material(endpoint+'skin/'+appearance.cape),[64,32]);
      }
      // Normalize classic 64x32 skins without network requests or another WebGL context.
      texture(this.T, url).promise.then(value => {
        if (!value || this.disposed || value.image.height !== value.image.width/2) return;
        const old = value.image, canvas = document.createElement('canvas');
        canvas.width = old.width; canvas.height = old.width;
        const ctx = canvas.getContext('2d');
        ctx.drawImage(old,0,0);
        const s = old.width/64;
        ctx.drawImage(old,0,16*s,16*s,16*s,16*s,48*s,16*s,16*s);
        ctx.drawImage(old,40*s,16*s,16*s,16*s,32*s,48*s,16*s,16*s);
        value.image = canvas; value.needsUpdate = true;
      });
    }
    equipment(items) {
      if (items.chest?.id === 'minecraft:elytra') {
        for (const side of [-1,1]) {
          const wing = this.pivot(side < 0 ? 'rightWing' : 'leftWing',side*2,24,-3);
          wing.rotation.z = side*.3;
          this.box(wing,[10,20,2],[side*4,-9,0],[22,0],
            this.material(endpoint+'texture/entity/equipment/wings/elytra.png'),[64,32]);
        }
      }
      const pieces = {
        head: [['head',[8,8,8],[0,4,0],[0,0]]],
        chest: [['torso',[8,12,4],[0,-6,0],[16,16]],['rightArm',[4,12,4],[0,-6,0],[40,16]],['leftArm',[4,12,4],[0,-6,0],[40,16]]],
        legs: [['torso',[8,12,4],[0,-6,0],[16,16]],['rightLeg',[4,12,4],[0,-6,0],[0,16]],['leftLeg',[4,12,4],[0,-6,0],[0,16]]],
        feet: [['rightLeg',[4,12,4],[0,-6,0],[0,16]],['leftLeg',[4,12,4],[0,-6,0],[0,16]]]
      };
      for (const [slot, item] of Object.entries(items)) {
        if (pieces[slot] && /^minecraft:[a-z0-9_]+$/.test(item.asset)) {
          const asset = item.asset.split(':')[1];
          const folder = slot === 'legs' ? 'humanoid_leggings' : 'humanoid';
          if (!['leather','chainmail','iron','gold','diamond','netherite','turtle_scute','copper'].includes(asset)) continue;
          for (const [part,size,center,uv] of pieces[slot]) {
            const material = this.material(endpoint+'texture/entity/equipment/'+folder+'/'+asset+'.png', asset === 'leather' ? item.color : 0xffffff);
            this.box(this.parts[part],size,center,uv,material,[64,32],slot === 'legs' ? .25 : .5);
            if (asset === 'leather') this.box(this.parts[part],size,center,uv,
              this.material(endpoint+'texture/entity/equipment/'+folder+'/leather_overlay.png'),[64,32],slot === 'legs' ? .26 : .51);
          }
        }
      }
      const leftMain = this.appearance?.main_arm === 'LEFT';
      for (const slot of ['mainhand','offhand']) {
        const item = items[slot];
        if (!item || !/^minecraft:[a-z0-9_]+$/.test(item.id)) continue;
        const name = item.id.split(':')[1], group = new this.T.Group();
        const left = slot === 'mainhand' ? leftMain : !leftMain;
        this.parts[left ? 'leftArm' : 'rightArm'].add(group);
        group.position.set(0,-10,1);
        group.rotation.x = -.3;
        const url = endpoint+'texture/item/'+name+'.png';
        const material = this.material(url);
        material.side = this.T.DoubleSide;
        const mesh = new this.T.Mesh(new this.T.PlaneGeometry(9,9),material);
        mesh.position.set(0,-1,3);
        mesh.rotation.y = Math.PI/2;
        group.add(mesh);
        // Block items use their actual block texture when no item sprite exists.
        texture(this.T,url).promise.then(value => {
          if (value || this.disposed || material.userData.disposed) return;
          const blockUrl = endpoint+'texture/block/'+name+'.png';
          const entry = texture(this.T,blockUrl);
          if (!this.refs.has(entry)) {entry.refs++;this.refs.add(entry);}
          entry.promise.then(block => {
            if (this.disposed || material.userData.disposed) return;
            if (!block) {mesh.visible=false;return;}
            mesh.geometry.dispose(); mesh.geometry=new this.T.BoxGeometry(5,5,5);
            material.map=block;material.needsUpdate=true;
          });
        });
      }
    }
    update(player) {
      this.name = player.name;
      const appearance = player.appearance || {};
      const key = JSON.stringify(appearance);
      if (key !== this.appearanceKey) {
        this.appearanceKey = key; this.appearance = appearance;
        this.build(appearance);
      }
      if (this.lastSwing !== player.swing_id) {
        if (this.lastSwing !== undefined && player.swing_id) this.swingAt = Date.now();
        this.lastSwing = player.swing_id;
      }
      this.state = player;
      this.receivedAt = Date.now();
    }
    render(position, a, b, ratio, selected) {
      if (this.disposed) return false;
      const viewer = this.viewer, p = this.state || {}, now = Date.now();
      const distance = viewer.camera.position.distanceTo(this.root.parent.position);
      const screen = this.root.parent.position.clone().project(viewer.camera);
      const visible = !document.hidden && Math.abs(screen.x)<1.15 && Math.abs(screen.y)<1.15 && screen.z>-1 && screen.z<1 && distance < (selected ? 180 : 100);
      this.root.visible = visible;
      if (!visible) {this.lastPosition=null;return false;}
      if (this.lastPosition) this.phase += Math.min(2, Math.hypot(position[0]-this.lastPosition[0],position[2]-this.lastPosition[2])) * 5;
      const speed = this.lastPosition ? Math.hypot(position[0]-this.lastPosition[0],position[2]-this.lastPosition[2]) : 0;
      this.lastPosition = position.slice();
      const bodyYaw = angle(a?.body_yaw ?? p.body_yaw ?? p.yaw, b?.body_yaw ?? p.body_yaw ?? p.yaw, ratio);
      const headYaw = angle(a?.yaw ?? p.yaw, b?.yaw ?? p.yaw, ratio);
      this.root.rotation.y = -bodyYaw * rad;
      this.parts.head.rotation.set((p.pitch || 0)*rad,-((headYaw-bodyYaw+540)%360-180)*rad,0);
      const walking = speed > .0001 && p.grounded !== false;
      const reduced = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
      const stride = walking && !reduced ? Math.sin(this.phase) * (p.sprinting ? .85 : .6) : 0;
      this.parts.rightLeg.rotation.x = stride;
      this.parts.leftLeg.rotation.x = -stride;
      this.parts.rightArm.rotation.set(-stride,0,0);
      this.parts.leftArm.rotation.set(stride,0,0);
      this.body.rotation.x = p.pose === 'CROUCHING' ? .35 : 0;
      this.body.position.y = p.pose === 'CROUCHING' ? -3 : 0;
      if (['SWIMMING','FALL_FLYING','SPIN_ATTACK'].includes(p.pose)) {
        this.body.rotation.x = Math.PI/2;
        this.body.position.set(0,10,-16);
        this.parts.rightArm.rotation.x = this.parts.leftArm.rotation.x = Math.PI;
      } else this.body.position.z = 0;
      if (p.pose === 'SLEEPING') {this.body.rotation.x=-Math.PI/2;this.body.position.y=2;}
      if (this.parts.leftWing) {
        const spread = p.pose === 'FALL_FLYING' ? 1.1 : .3;
        this.parts.leftWing.rotation.z = spread;
        this.parts.rightWing.rotation.z = -spread;
      }
      const left = this.appearance?.main_arm === 'LEFT';
      const main = this.parts[left ? 'leftArm' : 'rightArm'];
      if (p.use && p.use !== 'NONE') {
        main.rotation.x = -1.3;
        if (!reduced && ['EAT','DRINK'].includes(p.use)) main.rotation.x += Math.sin(now/90)*.12;
        if (['BOW','CROSSBOW','SPEAR'].includes(p.use)) {
          this.parts.leftArm.rotation.x = this.parts.rightArm.rotation.x = -1.5;
        }
      }
      const swing = (now-this.swingAt)/300;
      if (swing>=0 && swing<1) {
        const hand = p.swing_hand === 'OFF_HAND' ? this.parts[left ? 'rightArm' : 'leftArm'] : main;
        hand.rotation.x = -Math.sin(swing*Math.PI)*1.8;
      }
      return now-this.receivedAt<1000 && (walking || swing<1 || (p.use && p.use!=='NONE'));
    }
    dispose() {
      this.disposed=true;
      this.clear();
      this.root.removeFromParent();
    }
  }
  window.OakPlayerAvatar = Avatar;
})();
