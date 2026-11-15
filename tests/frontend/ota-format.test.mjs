import test from "node:test";
import assert from "node:assert/strict";
import { formatBytes, statusText, imageInfo } from "../../custom_components/friday/frontend/ota-format.js";

test("bytes", () => {
  assert.equal(formatBytes(512), "512 B");
  assert.equal(formatBytes(1536), "1.5 KB");
  assert.equal(formatBytes(2.4 * 1024 ** 3), "2.4 GB");
  assert.equal(formatBytes(null), "—");
});
test("status words", () => { assert.equal(statusText("ok"), "pronta"); assert.equal(statusText(null), "—"); });
test("image info joins the sensor and the served list", () => {
  const images = [{ file: "friday-satellite-v0.9.0.img.xz", tag: "v0.9.0", size: 1048576, mtime: 1760000000, sha256: null }];
  const i = imageInfo("v0.9.0", { built_at: "2026-11-12T10:00:00+00:00", status: "ok", error: "",
    download_url: "/api/friday/image/friday-satellite-v0.9.0.img.xz" }, images);
  assert.equal(i.size, 1048576); assert.equal(i.status, "ok");
  assert.equal(i.builtAt.toISOString(), "2026-11-12T10:00:00.000Z");
  assert.equal(i.downloadUrl, "/api/friday/image/friday-satellite-v0.9.0.img.xz");
});
test("no image yet", () => {
  const i = imageInfo("unknown", null, []);
  assert.equal(i.tag, null); assert.equal(i.downloadUrl, null); assert.equal(i.builtAt, null);
});
