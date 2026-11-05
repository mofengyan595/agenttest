import assert from "node:assert/strict";
import test from "node:test";
import {LoginSchedule} from "./login_acceptance.mjs";

const schedule = () => new LoginSchedule({source: {maxRequests: 3, windowSeconds: 60},
  account: {maxRequests: 2, windowSeconds: 300}});

test("source and account windows both apply to concentrated projected logins", () => {
  const s = schedule();
  s.observe(1000, "owner", 200);
  s.observe(2000, "owner", 200);
  s.observe(3000, "reader", 200);
  assert.equal(s.earliest(4000, {owner: 1}), 301000);
  assert.equal(s.earliest(4000, {reader: 1, other: 1}), 62000);
  assert.equal(s.earliest(4000, {other: 1}), 61000);
  assert.throws(() => s.earliest(4000, {owner: 3}), /exceeds/);
});

test("429 delays only future login positions, with a known-window fallback", () => {
  const s = schedule();
  s.observe(1000, "owner", 429, 15);
  assert.equal(s.earliest(2000, {other: 1}), 16000);
  assert.equal(s.earliest(2000, {}), 2000);
  s.observe(16000, "other", 429);
  assert.equal(s.earliest(17000, {reader: 1}), 316000);
});
