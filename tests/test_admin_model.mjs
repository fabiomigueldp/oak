import test from "node:test";
import assert from "node:assert/strict";
import {
  health,
  proof,
  mapHash,
  decodeOptions,
  serializeCredential,
  bytes,
} from "../public/admin/model.js";

test("stale status never claims the game is online", () => {
  assert.equal(health({ fresh: false, online: true }).state, "unknown");
  assert.equal(health({ fresh: true, online: false }).state, "offline");
  assert.equal(
    health({ fresh: true, online: true, warnings: ["Disk low"] }).state,
    "warning",
  );
  assert.equal(bytes(undefined), "Indisponível");
});
test("backup evidence does not imply a boot test", () => {
  assert.equal(proof({ integrity: true }).label, "Integridade verificada");
  assert.equal(
    proof({ restoration: { level: "extraction" } }).label,
    "Extração verificada",
  );
  assert.equal(
    proof({ restoration: { playable_boot_tested: true } }).label,
    "Restauração testada",
  );
});
test("map links validate data and preserve dimension", () => {
  assert.equal(mapHash([1, 2, NaN]), "");
  assert.equal(mapHash([1, 2, 3], undefined, "javascript:alert(1)"), "");
  assert.match(mapHash([1, 2, 3], "minecraft:the_nether"), /^#nether:1:2:3:/);
});
test("passkey binary serialization round trips URL-safe bytes", () => {
  const raw = new Uint8Array([255, 254, 0, 1]).buffer;
  const serialized = serializeCredential({
    id: "id",
    rawId: raw,
    type: "public-key",
    response: { clientDataJSON: raw },
    getClientExtensionResults: () => ({}),
  });
  assert.equal(serialized.rawId, "__4AAQ");
  assert.deepEqual(
    decodeOptions({ challenge: serialized.rawId }).challenge,
    new Uint8Array(raw),
  );
});
