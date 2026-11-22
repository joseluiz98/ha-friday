import test from "node:test";
import assert from "node:assert/strict";
import { causeText, rowsFromHistory, fmt, activationEntities, satelliteLabel, playNote } from "../../custom_components/friday/frontend/activations-format.js";

test("rows from history, newest first, old events keep only word/score", () => {
  const resp = { "event.friday_sat_8295e1_activation": [
    { s: "2026-11-03T12:39:20.245+00:00", a: { event_type: "conversation", word: "friday", score: 0.62 }, lu: 1 },
    { s: "2026-11-03T12:43:22.000+00:00", a: { event_type: "rejected", word: "friday", score: 0.86, cause: "no_voice",
      threshold: 0.35, voice: 0.52, voice_threshold: 0.6, recording: "sem_voz_20261103_094322.wav" }, lu: 2 } ] };
  const rows = rowsFromHistory(resp, ["event.friday_sat_8295e1_activation"]);
  assert.equal(rows[0].type, "rejected"); assert.equal(rows[0].recording, "sem_voz_20261103_094322.wav");
  assert.equal(rows[1].voice, null); assert.equal(fmt(rows[1].voice), "—");
});
test("a state restored after unavailable (entry reload, HA restart) is the same event: one row", () => {
  const ev = { s: "2026-11-22T07:18:40.906+00:00", a: { event_type: "conversation", word: "friday", score: 0.7 } };
  const resp = { "event.friday_sat_8295e1_activation": [
    { ...ev, lu: 1 }, { s: "unavailable", a: {}, lu: 2 }, { ...ev, lu: 3 }, { s: "unavailable", a: {}, lu: 4 },
    { ...ev, lu: 5 },
    { s: "2026-11-22T08:00:00.000+00:00", a: { event_type: "rejected", score: 0.4 }, lu: 6 } ],
    "event.friday_sat_173bbf_activation": [{ ...ev, lu: 7 }] };
  const rows = rowsFromHistory(resp, ["event.friday_sat_8295e1_activation", "event.friday_sat_173bbf_activation"]);
  assert.equal(rows.length, 3);  // two events of MK1, one of MK2 at the same instant is another satellite's event
  assert.equal(rows.filter((r) => r.entityId === "event.friday_sat_8295e1_activation").length, 2);
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

test("playNote names what the proxy answered", () => {
  assert.equal(playNote(404), "recording not ready");
  assert.equal(playNote(403), "satellite refused");
  assert.equal(playNote(502), "satellite offline");
  assert.equal(playNote(500), "error 500");
  assert.equal(playNote(200), "");
});
