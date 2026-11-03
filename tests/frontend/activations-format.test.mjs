import test from "node:test";
import assert from "node:assert/strict";
import { causeText, rowsFromHistory, fmt } from "../../custom_components/friday/frontend/activations-format.js";

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
