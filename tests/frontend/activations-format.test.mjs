import test from "node:test";
import assert from "node:assert/strict";
import { causeText, rowsFromHistory, fmt, activationEntities, satelliteLabel } from "../../custom_components/friday/frontend/activations-format.js";

test("rows from history, newest first, old events keep only word/score", () => {
  const resp = { "event.friday_sat_8295e1_activation": [
    { s: "2026-11-03T12:39:20.245+00:00", a: { event_type: "conversation", word: "friday", score: 0.62 }, lu: 1 },
    { s: "2026-11-03T12:43:22.000+00:00", a: { event_type: "rejected", word: "friday", score: 0.86, cause: "no_voice",
      threshold: 0.35, voice: 0.52, voice_threshold: 0.6, recording: "sem_voz_20261103_094322.wav" }, lu: 2 } ] };
  const rows = rowsFromHistory(resp, ["event.friday_sat_8295e1_activation"]);
  assert.equal(rows[0].type, "rejected"); assert.equal(rows[0].recording, "sem_voz_20261103_094322.wav");
  assert.equal(rows[1].voice, null); assert.equal(fmt(rows[1].voice), "—");
});
test("cause words", () => { assert.equal(causeText("no_voice"), "no human voice"); assert.equal(causeText(undefined), ""); });
test("skips non-timestamp states", () => {
  const rows = rowsFromHistory({ e: [{ s: "unknown", a: {}, lu: 1 }] }, ["e"]);
  assert.equal(rows.length, 0);
});

test("activationEntities finds every activation event, sorted, ignoring other events", () => {
  const states = {
    "event.friday_sat_1d2e3f_activation": {}, "event.friday_sat_0a1b2c_activation": {},
    "event.friday_sat_0a1b2c_timer": {}, "sensor.friday_sat_0a1b2c_activation": {},
  };
  assert.deepEqual(activationEntities(states), [
    "event.friday_sat_0a1b2c_activation", "event.friday_sat_1d2e3f_activation"]);
  assert.deepEqual(activationEntities(undefined), []);
});
test("satelliteLabel prefers name_by_user, then name, then the entity id", () => {
  const id = "event.friday_sat_0a1b2c_activation";
  const mk = (dev) => ({ entities: { [id]: { device_id: "d1" } }, devices: { d1: dev } });
  assert.equal(satelliteLabel(mk({ name_by_user: "Office", name: "Satellite 0a1b2c" }), id), "Office");
  assert.equal(satelliteLabel(mk({ name_by_user: null, name: "Satellite 0a1b2c" }), id), "Satellite 0a1b2c");
  assert.equal(satelliteLabel(mk({}), id), id);
  assert.equal(satelliteLabel({ entities: {}, devices: {} }, id), id);
  assert.equal(satelliteLabel({}, id), id);
});
