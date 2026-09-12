import test from 'node:test';
import assert from 'node:assert/strict';
import { aviaryValues, aviaryAnchorLabel, aviaryEditState } from '../public/admin/aviary.js';

function fixture() {
  return { revision:12, error:'', maxFlights:2, shortcutDistance:500,
    network:{ fieldPickup:true, discoverPublic:true, maxOwnedPerches:4 },
    ports:[{ id:'home', name:'Home', shared:false, departureYaw:null, arrivalYaw:90, busy:false, kind:'perch', anchorStatus:'active',
      owner:'player-id', perch:{ x:10, y:80, z:20, active:true, color:'green', style:'oak', birdName:'Fern', hub:false, guests:['friend-id'] } },
    { id:'legacy', name:'Harbor', shared:true, arrivalYaw:null, kind:'legacy' }], flights:[] };
}

test('physical and legacy edits send configuration without anchor or invitation authority', () => {
  const data = fixture();
  const physical = aviaryValues(data, 'home');
  assert.deepEqual(Object.keys(physical).sort(), ['arrivalYaw','birdName','color','departureYaw','hub','name','shared','style']);
  physical.name = 'Draft';
  assert.equal(data.ports[0].name, 'Home');
  assert.deepEqual(aviaryValues(data, 'legacy'), { name:'Harbor', shared:true, departureYaw:null, arrivalYaw:null });
  assert.equal(aviaryValues(data, 'removed'), null);
});

test('network drafts copy current settings and tolerate the previous socket schema', () => {
  const data = fixture();
  assert.deepEqual(aviaryValues(data, '@network'), { fieldPickup:true, discoverPublic:true, maxOwnedPerches:4, maxFlights:2, shortcutDistance:500 });
  delete data.shortcutDistance;
  assert.equal(Object.hasOwn(aviaryValues(data, '@network'), 'maxFlights'), false);
  delete data.network;
  assert.equal(aviaryValues(data, '@network'), null);
});

test('unloaded anchor status is never advertised as a missing support or validated landing', () => {
  const p = fixture().ports[0];
  assert.equal(aviaryAnchorLabel({ ...p, anchorStatus:'unloaded' }), 'Área não carregada');
  assert.equal(aviaryAnchorLabel({ ...p, anchorStatus:'missing' }), 'Suporte ausente');
  assert.equal(aviaryAnchorLabel(p), 'Poleiro instalado');
  assert.equal(aviaryAnchorLabel({ ...p, anchorStatus:'inactive', active:false }), 'Em mudança');
  assert.equal(aviaryAnchorLabel(fixture().ports[1]), 'Destino existente');
});

test('new world revisions, removed destinations, errors and pending jobs all block stale drafts', () => {
  const data = fixture();
  const edit = { data, id:'home', revision:12, dirty:true, pending:null, editable:true };
  assert.equal(aviaryEditState(edit).canSave, true);
  assert.equal(aviaryEditState({ ...edit, revision:11 }).canSave, false);
  assert.match(aviaryEditState({ ...edit, revision:11 }).message, /mudaram/);
  assert.equal(aviaryEditState({ ...edit, id:'removed' }).canSave, false);
  assert.equal(aviaryEditState({ ...edit, editable:false }).canSave, false);
  assert.equal(aviaryEditState({ ...edit, dirty:false }).canSave, false);
  assert.equal(aviaryEditState({ ...edit, pending:'job' }).canDiscard, false);
  assert.equal(aviaryEditState({ ...edit, pending:true }).canSave, false);
  data.error = 'Storage unavailable';
  assert.equal(aviaryEditState(edit).canSave, false);
  assert.match(aviaryEditState(edit).message, /Não foi possível confirmar/);
});

test('busy destinations remain protected while policy changes can govern future requests', () => {
  const data = fixture(); data.ports[0].busy = true;
  const edit = { data, revision:12, dirty:true, editable:true };
  assert.equal(aviaryEditState({ ...edit, id:'home' }).canSave, false);
  assert.equal(aviaryEditState({ ...edit, id:'@network' }).canSave, true);
});
