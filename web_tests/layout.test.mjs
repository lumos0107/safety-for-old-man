// 관절 점 배치(coco17 / halpe26) — design/2026-09-30-body-detail-design.md 6.2·6.4·6.7
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { LAYOUTS, layoutOf, jointLabel, visibleSegments, visiblePoints, COLORS, SKELETON } from "../lib.js";

const bus = JSON.parse(readFileSync(new URL("./fixtures/bus_person.json", import.meta.url), "utf8"));
const rect = { x: 0, y: 0, w: 810, h: 1080 };
const H = LAYOUTS.halpe26;

test("선 개수와 번호 범위", () => {
  assert.equal(LAYOUTS.coco17.edges.length, 19);
  assert.equal(LAYOUTS.coco17.edges, SKELETON); // 17점은 지금 그대로
  assert.equal(H.edges.length, 26);
  assert.ok(H.edges.flat().every((i) => Number.isInteger(i) && i >= 0 && i < 26));
  assert.equal(new Set(H.edges.map(([a, b]) => `${Math.min(a, b)}-${Math.max(a, b)}`)).size, 26); // 겹치는 선 없음
  assert.deepEqual([LAYOUTS.coco17.count, H.count], [17, 26]);
  assert.deepEqual([LAYOUTS.coco17.minConf, H.minConf], [0.5, 0.4]);
});

test("halpe26 26개 점의 색을 하나씩 (20·22·24가 왼발 — 홀짝 규칙이 거꾸로)", () => {
  const expected = [
    COLORS.face, COLORS.face, COLORS.face, COLORS.face, COLORS.face, // 0~4 코·눈·귀
    COLORS.leftArm, COLORS.rightArm, COLORS.leftArm, COLORS.rightArm, COLORS.leftArm, COLORS.rightArm, // 5~10
    COLORS.leftLeg, COLORS.rightLeg, COLORS.leftLeg, COLORS.rightLeg, COLORS.leftLeg, COLORS.rightLeg, // 11~16
    COLORS.face, // 17 머리 꼭대기
    COLORS.trunk, COLORS.trunk, // 18 목, 19 골반 가운데
    COLORS.leftLeg, COLORS.rightLeg, // 20 왼엄지발가락, 21 오른엄지발가락
    COLORS.leftLeg, COLORS.rightLeg, // 22 왼새끼발가락, 23 오른새끼발가락
    COLORS.leftLeg, COLORS.rightLeg, // 24 왼뒤꿈치, 25 오른뒤꿈치
  ];
  assert.equal(expected.length, 26);
  expected.forEach((c, i) => assert.equal(H.pointColor(i), c, `점 ${i}`));
});

test("halpe26 선 색: 머리·목은 분홍, 몸통 사각형+척추는 노랑, 발은 같은 쪽 다리 색", () => {
  const color = (a, b) => H.segmentColor(a, b);
  for (const [a, b] of [[0, 1], [0, 2], [1, 2], [1, 3], [2, 4], [17, 18], [0, 18]]) assert.equal(color(a, b), COLORS.face, `${a}-${b}`);
  for (const [a, b] of [[5, 6], [11, 12], [5, 11], [6, 12], [18, 19]]) assert.equal(color(a, b), COLORS.trunk, `${a}-${b}`);
  for (const [a, b] of [[5, 7], [7, 9]]) assert.equal(color(a, b), COLORS.leftArm);
  for (const [a, b] of [[6, 8], [8, 10]]) assert.equal(color(a, b), COLORS.rightArm);
  for (const [a, b] of [[11, 13], [13, 15], [15, 20], [15, 22], [15, 24]]) assert.equal(color(a, b), COLORS.leftLeg, `${a}-${b}`);
  for (const [a, b] of [[12, 14], [14, 16], [16, 21], [16, 23], [16, 25]]) assert.equal(color(a, b), COLORS.rightLeg, `${a}-${b}`);
  assert.equal(color(18, 17), COLORS.face); // 순서가 바뀌어도 같은 색
  // 귀–어깨 선은 없다 (머리는 목으로 이어짐)
  assert.ok(!H.edges.some(([a, b]) => (a === 3 && b === 5) || (a === 4 && b === 6)));
});

test("모든 선·점에 색이 있다", () => {
  for (const L of [LAYOUTS.coco17, H]) {
    for (const [a, b] of L.edges) assert.match(L.segmentColor(a, b), /^#[0-9a-f]{6}$/);
    for (let i = 0; i < L.count; i++) assert.match(L.pointColor(i), /^#[0-9a-f]{6}$/);
  }
});

test("bus 한 사람: 26점이 17점보다 선이 많고 척추·발이 그려진다", () => {
  const s17 = visibleSegments(bus.coco17, rect, LAYOUTS.coco17.minConf, LAYOUTS.coco17);
  const s26 = visibleSegments(bus.halpe26, rect, H.minConf, H);
  assert.ok(s26.length > s17.length, `${s26.length} > ${s17.length}`);
  const spine = [...bus.halpe26[18].slice(0, 2), ...bus.halpe26[19].slice(0, 2)].map((v, i) => v * (i % 2 ? 1080 : 810));
  assert.ok(s26.some((s) => s.slice(0, 4).every((v, i) => Math.abs(v - spine[i]) < 1e-9) && s[4] === COLORS.trunk), "척추 선");
  const pts = visiblePoints(bus.halpe26, rect, H.minConf, H);
  assert.equal(pts.length, 26);
});

test("기본값은 coco17 (예전 호출 방식 그대로)", () => {
  assert.deepEqual(visibleSegments(bus.coco17, rect, 0.5), visibleSegments(bus.coco17, rect, 0.5, LAYOUTS.coco17));
});

test("layoutOf: layout이 없으면 coco17, 모르는 값이면 null", () => {
  assert.equal(layoutOf({ people: [] }), LAYOUTS.coco17);
  assert.equal(layoutOf({ layout: "coco17", people: [] }), LAYOUTS.coco17);
  assert.equal(layoutOf({ layout: "halpe26", people: [] }), H);
  assert.equal(layoutOf({ layout: "wholebody", people: [] }), null);
  assert.equal(layoutOf({ layout: "toString", people: [] }), null); // 프로토타입 이름에 속지 않는다
});

test('"관절" 칸 표시', () => {
  const p = (n) => ({ kpts: Array.from({ length: n }, () => [0.5, 0.5, 0.9]) });
  assert.equal(jointLabel({ layout: "halpe26", people: [p(26)] }), "26점");
  assert.equal(jointLabel({ layout: "halpe26", people: [] }), "26점");
  assert.equal(jointLabel({ layout: "coco17", people: [p(17)] }), "17점");
  assert.equal(jointLabel({ people: [p(17)] }), "17점"); // 예전 서버
  assert.equal(jointLabel({ layout: "wholebody", people: [] }), "?");
  assert.equal(jointLabel({ layout: "halpe26", people: [p(26), p(17)] }), "?"); // 개수 불일치
  assert.equal(jointLabel({ layout: "coco17", people: [{ kpts: null }] }), "?");
});

test("사람별로 그릴지: 개수가 맞는 사람만", async () => {
  const { drawablePeople } = await import("../lib.js");
  const p = (n) => ({ kpts: Array.from({ length: n }, () => [0.5, 0.5, 0.9]) });
  const r = { layout: "halpe26", people: [p(26), p(17), p(26)] };
  assert.deepEqual(drawablePeople(r).map((x) => x.kpts.length), [26, 26]);
  assert.deepEqual(drawablePeople({ layout: "zzz", people: [p(26)] }), []);
});
