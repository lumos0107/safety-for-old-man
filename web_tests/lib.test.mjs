import { test } from "node:test";
import assert from "node:assert/strict";
import {
  CLOSE, STATUS, closePolicy, retryDelayMs, normalizeServerUrl, cleanToken,
  fitContain, scaleToLongSide, SKELETON, toCanvas, visibleSegments, visiblePoints, ema,
  COLORS, pointColor, segmentColor,
} from "../lib.js";

test("재연결하지 않는 종료 코드", () => {
  assert.deepEqual(closePolicy(CLOSE.BAD_TOKEN), { retry: false, status: STATUS.BAD_TOKEN });
  assert.deepEqual(closePolicy(CLOSE.BAD_ORIGIN), { retry: false, status: STATUS.BAD_ORIGIN });
  assert.deepEqual(closePolicy(CLOSE.REPLACED), { retry: false, status: STATUS.REPLACED });
});

test("재연결하는 종료 코드", () => {
  for (const code of [CLOSE.AUTH_TIMEOUT, CLOSE.EVICTED, 1006, 1000, 1011, 1009]) {
    assert.deepEqual(closePolicy(code), { retry: true, status: STATUS.OFFLINE });
  }
});

test("재연결 간격 3초부터 최대 10초", () => {
  assert.deepEqual([0, 1, 2, 3, 9].map(retryDelayMs), [3000, 6000, 10000, 10000, 10000]);
});

test("서버 주소 정규화", () => {
  const cases = [
    ["mypc.tailnet.ts.net", "wss://mypc.tailnet.ts.net/ws"],
    ["https://mypc.tailnet.ts.net", "wss://mypc.tailnet.ts.net/ws"],
    ["https://mypc.tailnet.ts.net/", "wss://mypc.tailnet.ts.net/ws"],
    ["https://mypc.tailnet.ts.net/ws", "wss://mypc.tailnet.ts.net/ws"],
    ["  wss://h.ts.net/ws  ", "wss://h.ts.net/ws"],
    ["http://127.0.0.1:8000", "ws://127.0.0.1:8000/ws"],
    ["ws://localhost:8000/ws", "ws://localhost:8000/ws"],
    ["http://[::1]:8000", "ws://[::1]:8000/ws"],
  ];
  for (const [input, want] of cases) assert.equal(normalizeServerUrl(input), want, input);
  const bads = ["", "   ", "ftp://x.com", "http://", null, undefined,
    "http://mypc.tailnet.ts.net", "ws://example.com/ws", "http://192.168.0.10:8000"]; // 원격 평문 거부
  for (const bad of bads) {
    assert.equal(normalizeServerUrl(bad), null, String(bad));
  }
});

test("토큰 앞뒤 공백·줄바꿈 제거", () => {
  assert.equal(cleanToken("  abc-DEF_123\n"), "abc-DEF_123");
  assert.equal(cleanToken(undefined), "");
});

test("fitContain: 가로 영상을 세로 화면에", () => {
  assert.deepEqual(fitContain(640, 360, 360, 640), { x: 0, y: 218.75, w: 360, h: 202.5 });
});

test("fitContain: 세로 영상을 가로 화면에", () => {
  assert.deepEqual(fitContain(480, 640, 800, 600), { x: 175, y: 0, w: 450, h: 600 });
});

test("fitContain: 크기 0이면 null", () => {
  assert.equal(fitContain(0, 480, 100, 100), null);
  assert.equal(fitContain(640, 480, 0, 100), null);
});

test("긴 변 640으로 축소, 확대는 하지 않음", () => {
  assert.deepEqual(scaleToLongSide(1280, 720), { w: 640, h: 360 });
  assert.deepEqual(scaleToLongSide(720, 1280), { w: 360, h: 640 });
  assert.deepEqual(scaleToLongSide(320, 240), { w: 320, h: 240 });
});

test("뼈대 연결 19개, 인덱스 0~16", () => {
  assert.equal(SKELETON.length, 19);
  assert.ok(SKELETON.flat().every((i) => Number.isInteger(i) && i >= 0 && i < 17));
});

test("좌표 변환과 신뢰도 필터", () => {
  const rect = { x: 10, y: 20, w: 100, h: 200 };
  assert.deepEqual(toCanvas([0.5, 0.25], rect), [60, 70]);
  const kpts = Array.from({ length: 17 }, () => [0, 0, 0.1]);
  kpts[5] = [0.2, 0.3, 0.9];  // 왼쪽 어깨
  kpts[6] = [0.4, 0.3, 0.8];  // 오른쪽 어깨
  kpts[7] = [0.2, 0.5, 0.4];  // 왼쪽 팔꿈치 (신뢰도 낮음)
  assert.deepEqual(visibleSegments(kpts, rect, 0.5), [[30, 80, 50, 80, COLORS.trunk]]);
  assert.deepEqual(visiblePoints(kpts, rect, 0.5), [[30, 80, COLORS.leftArm], [50, 80, COLORS.rightArm]]);
});

test("점 색: 얼굴·좌우 팔·좌우 다리 (COCO 홀수 = 왼쪽)", () => {
  for (const i of [0, 1, 2, 3, 4]) assert.equal(pointColor(i), COLORS.face, `점 ${i}`);
  for (const i of [5, 7, 9]) assert.equal(pointColor(i), COLORS.leftArm, `점 ${i}`);
  for (const i of [6, 8, 10]) assert.equal(pointColor(i), COLORS.rightArm, `점 ${i}`);
  for (const i of [11, 13, 15]) assert.equal(pointColor(i), COLORS.leftLeg, `점 ${i}`);
  for (const i of [12, 14, 16]) assert.equal(pointColor(i), COLORS.rightLeg, `점 ${i}`);
});

test("선 색: 몸통 4개는 노랑, 얼굴·귀-어깨는 분홍, 팔다리는 좌우 색", () => {
  for (const [a, b] of [[5, 6], [11, 12], [5, 11], [6, 12]]) assert.equal(segmentColor(a, b), COLORS.trunk);
  for (const [a, b] of [[1, 2], [0, 1], [0, 2], [1, 3], [2, 4], [3, 5], [4, 6]]) {
    assert.equal(segmentColor(a, b), COLORS.face, `${a}-${b}`);
  }
  assert.equal(segmentColor(5, 7), COLORS.leftArm);
  assert.equal(segmentColor(7, 9), COLORS.leftArm);
  assert.equal(segmentColor(6, 8), COLORS.rightArm);
  assert.equal(segmentColor(8, 10), COLORS.rightArm);
  assert.equal(segmentColor(15, 13), COLORS.leftLeg);
  assert.equal(segmentColor(13, 11), COLORS.leftLeg);
  assert.equal(segmentColor(16, 14), COLORS.rightLeg);
  assert.equal(segmentColor(14, 12), COLORS.rightLeg);
});

test("모든 뼈대 선에 색이 있고, 좌우 색은 서로 구분된다", () => {
  const palette = new Set(Object.values(COLORS));
  assert.equal(palette.size, 6);
  for (const [a, b] of SKELETON) assert.ok(palette.has(segmentColor(a, b)), `${a}-${b}`);
});

test("ema", () => {
  assert.equal(ema(null, 10), 10);
  assert.equal(ema(10, 20, 0.5), 15);
});
