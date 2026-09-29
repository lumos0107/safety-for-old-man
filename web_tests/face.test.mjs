// face.js 상태 머신 — 모델 불러오기를 가짜로 주입해 Node에서 검증한다 (MediaPipe 없이).
import { test } from "node:test";
import assert from "node:assert/strict";
import { createFaceTracker } from "../face.js";

globalThis.document = { hidden: false };
const settle = () => new Promise((r) => setTimeout(r, 0));
const wait = (ms) => new Promise((r) => setTimeout(r, ms));

// script: 호출 순서대로 "ok" | "throw" (없으면 ok)
function fakeLandmarker(script = []) {
  let n = 0;
  return {
    closed: false,
    detectForVideo() {
      if (script[n++] === "throw") throw new Error("WebGL context lost");
      return { faceLandmarks: [Array.from({ length: 478 }, (_, i) => ({ x: i / 1000, y: i / 2000, z: 0.3 }))] };
    },
    close() { this.closed = true; },
  };
}

function fakeVideo() {
  return {
    currentTime: 0, readyState: 4, videoWidth: 640, cb: null,
    requestVideoFrameCallback(cb) { this.cb = cb; return 1; },
    cancelVideoFrameCallback() { this.cb = null; },
    frame(t) { this.currentTime = t; const cb = this.cb; this.cb = null; cb?.(); },
  };
}

test("계산이 실패할 때마다 다시 만들고, 성공하면 다시 만들기 기회가 돌아온다", async () => {
  const made = [];
  const errors = [];
  const scripts = [["throw"], ["ok", "throw"], []];
  const t = createFaceTracker({
    onUpdate() {}, onError: (c) => errors.push(c),
    load: async () => { const lm = fakeLandmarker(scripts[made.length]); made.push(lm); return lm; },
  });
  await t.setEnabled(true);
  assert.equal(t.detectNow({}), null); // 1번째 실패 → 다시 만듦
  await settle(); await settle();
  assert.equal(made.length, 2);
  assert.ok(t.detectNow({}), "다시 만든 모델로 계산 성공");
  assert.equal(t.detectNow({}), null); // 2번째 실패 (아이폰 앱 전환 두 번째)
  await settle(); await settle();
  assert.deepEqual(errors, [], "성공 뒤의 실패는 다시 복구돼야 한다");
  assert.equal(made.length, 3);
  assert.ok(t.ready);
});

test("다시 만든 직후에도 바로 실패하면 끄고 runtime 오류를 알린다", async () => {
  const errors = [];
  const t = createFaceTracker({
    onUpdate() {}, onError: (c) => errors.push(c),
    load: async () => fakeLandmarker(["throw"]),
  });
  await t.setEnabled(true);
  t.detectNow({});
  await settle(); await settle();
  t.detectNow({});
  await settle();
  assert.deepEqual(errors, ["runtime"]);
  assert.equal(t.enabled, false);
});

test("시간 초과 뒤 늦게 끝난 모델은 같은 불러오기를 기다리던 새 켜기 요청이 쓴다 (닫지 않음)", async () => {
  let resolve;
  const lm = fakeLandmarker();
  const errors = [];
  const t = createFaceTracker({
    onUpdate() {}, onError: (c) => errors.push(c), timeoutMs: 200,
    load: () => new Promise((r) => { resolve = r; }),
  });
  t.setEnabled(true); // A: 0ms 시작, 200ms에 시간 초과
  await wait(100);
  t.setEnabled(false);
  const c = t.setEnabled(true); // C: 같은 불러오기를 기다림, 300ms에 시간 초과
  await wait(150); // A 시간 초과 지남
  resolve(lm); // 250ms에 완료
  await c;
  await settle();
  assert.equal(lm.closed, false, "새 요청이 쓸 모델을 닫으면 안 된다");
  assert.ok(t.ready);
  assert.deepEqual(errors, []);
});

test("실시간 결과는 윤곽 36점(x, y)만 들고, 다시 만드는 동안에는 지운다", async () => {
  const video = fakeVideo();
  let n = 0;
  const t = createFaceTracker({
    onUpdate() {}, onError() {},
    load: async () => fakeLandmarker(n++ === 0 ? ["ok", "throw"] : []),
  });
  t.setSource(video);
  await t.setEnabled(true);
  video.frame(1);
  assert.equal(t.live.length, 1);
  assert.equal(t.live[0].length, 36, "478점 전체를 들고 있지 않는다");
  assert.deepEqual(t.live[0][0], [0.01, 0.005]); // 10번 점, z 없음
  await wait(80); // 초당 15회 상한
  video.frame(2); // 실패 → 다시 만듦
  assert.equal(t.live, null, "멈춘 옛 윤곽이 남으면 안 된다");
});

test("끄면 모델을 닫고 결과를 지운다", async () => {
  const lm = fakeLandmarker();
  const t = createFaceTracker({ onUpdate() {}, onError() {}, load: async () => lm });
  await t.setEnabled(true);
  await t.setEnabled(false);
  assert.ok(lm.closed);
  assert.equal(t.live, null);
  assert.equal(t.detectNow({}), null);
});
