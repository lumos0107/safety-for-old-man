// 화면과 무관한 순수 함수 — app.js가 쓰고 web_tests/lib.test.mjs가 검증한다.

export const CLOSE = { BAD_TOKEN: 4001, BAD_ORIGIN: 4003, AUTH_TIMEOUT: 4008, EVICTED: 4009, REPLACED: 4010 };

export const STATUS = {
  IDLE: "대기",
  STOPPED: "정지",
  NEED_SETUP: "설정 필요",
  CONNECTING: "연결 중",
  CONNECTED: "연결됨",
  BAD_TOKEN: "토큰 확인",
  BAD_ORIGIN: "허용되지 않은 주소",
  REPLACED: "다른 기기에서 사용 중",
  OFFLINE: "서버 꺼짐",
};

// 4001·4003·4010은 다시 붙어도 같은 결과(또는 두 탭이 서로 뺏는 반복)라 재연결하지 않는다.
export function closePolicy(code) {
  if (code === CLOSE.BAD_TOKEN) return { retry: false, status: STATUS.BAD_TOKEN };
  if (code === CLOSE.BAD_ORIGIN) return { retry: false, status: STATUS.BAD_ORIGIN };
  if (code === CLOSE.REPLACED) return { retry: false, status: STATUS.REPLACED };
  return { retry: true, status: STATUS.OFFLINE };
}

// 연속 실패가 이만큼이면 멈춘다 — 서버가 없을 때 카메라·화면 켜짐이 밤새 유지되지 않게 (약 3분)
export const MAX_RETRIES = 12;

export function shouldGiveUp(attempt) {
  return attempt >= MAX_RETRIES;
}

export function retryDelayMs(attempt) {
  return Math.min(3000 * 2 ** attempt, 10000);
}

const LOCAL_HOSTS = new Set(["localhost", "127.0.0.1", "[::1]"]);

// 호스트만, https 주소, /ws가 붙은 주소 모두 받아 WebSocket 주소로 바꾼다.
// 암호화되지 않은 http/ws는 이 PC 안(localhost)일 때만 허용한다 — 토큰이 평문으로 원격에 가지 않게.
export function normalizeServerUrl(input) {
  let s = String(input ?? "").trim();
  if (!s) return null;
  if (!/^[a-z][a-z0-9+.-]*:\/\//i.test(s)) s = `https://${s}`;
  let url;
  try {
    url = new URL(s);
  } catch {
    return null;
  }
  const scheme = { "https:": "wss:", "wss:": "wss:", "http:": "ws:", "ws:": "ws:" }[url.protocol];
  if (!scheme || !url.host) return null;
  if (scheme === "ws:" && !LOCAL_HOSTS.has(url.hostname)) return null;
  return `${scheme}//${url.host}/ws`;
}

export function cleanToken(input) {
  return String(input ?? "").trim();
}

// object-fit: contain으로 표시된 영상이 상자 안에서 차지하는 영역
export function fitContain(srcW, srcH, boxW, boxH) {
  if (!(srcW > 0 && srcH > 0 && boxW > 0 && boxH > 0)) return null;
  const scale = Math.min(boxW / srcW, boxH / srcH);
  const w = srcW * scale;
  const h = srcH * scale;
  return { x: (boxW - w) / 2, y: (boxH - h) / 2, w, h };
}

export function scaleToLongSide(w, h, maxSide = 640) {
  const scale = Math.min(1, maxSide / Math.max(w, h));
  return { w: Math.round(w * scale), h: Math.round(h * scale) };
}

// COCO 17점: 0 코, 1·2 눈, 3·4 귀, 5·6 어깨, 7·8 팔꿈치, 9·10 손목, 11·12 골반, 13·14 무릎, 15·16 발목
export const SKELETON = [
  [15, 13], [13, 11], [16, 14], [14, 12], [11, 12], [5, 11], [6, 12], [5, 6], [5, 7], [6, 8],
  [7, 9], [8, 10], [1, 2], [0, 1], [0, 2], [1, 3], [2, 4], [3, 5], [4, 6],
];

// 부위별 색. 좌우는 사람 기준 (COCO에서 홀수 번호 = 왼쪽). 왼쪽은 파랑 계열, 오른쪽은 주황 계열.
export const COLORS = {
  face: "#f472b6",
  trunk: "#facc15",
  leftArm: "#38bdf8",
  leftLeg: "#2563eb",
  rightArm: "#fb923c",
  rightLeg: "#ea580c",
};

const TRUNK_EDGES = new Set(["5-6", "11-12", "5-11", "6-12"]);

export function pointColor(i) {
  if (i <= 4) return COLORS.face;
  const left = i % 2 === 1;
  if (i <= 10) return left ? COLORS.leftArm : COLORS.rightArm;
  return left ? COLORS.leftLeg : COLORS.rightLeg;
}

export function segmentColor(a, b) {
  const [lo, hi] = a < b ? [a, b] : [b, a];
  if (lo <= 4) return COLORS.face; // 얼굴 선과 귀-어깨 선
  if (TRUNK_EDGES.has(`${lo}-${hi}`)) return COLORS.trunk;
  return pointColor(hi); // 팔·다리 선은 먼 쪽 관절의 색
}

export function toCanvas([nx, ny], rect) {
  return [rect.x + nx * rect.w, rect.y + ny * rect.h];
}

// [x1, y1, x2, y2, 색]
export function visibleSegments(kpts, rect, minConf) {
  const out = [];
  for (const [a, b] of SKELETON) {
    if (kpts[a][2] >= minConf && kpts[b][2] >= minConf) {
      out.push([...toCanvas(kpts[a], rect), ...toCanvas(kpts[b], rect), segmentColor(a, b)]);
    }
  }
  return out;
}

// [x, y, 색]
export function visiblePoints(kpts, rect, minConf) {
  const out = [];
  kpts.forEach((k, i) => {
    if (k[2] >= minConf) out.push([...toCanvas(k, rect), pointColor(i)]);
  });
  return out;
}

const FRAME_ERRORS = {
  too_large: "보낸 프레임이 너무 큽니다.",
  bad_image: "서버가 프레임을 읽지 못했습니다.",
  bad_message: "서버가 메시지를 이해하지 못했습니다.",
  server_error: "서버 계산 중 오류가 났습니다.",
};

export function frameErrorText(code) {
  return FRAME_ERRORS[code] ?? `프레임 오류 (${code})`;
}

export function ema(prev, sample, alpha = 0.2) {
  return prev == null ? sample : prev + alpha * (sample - prev);
}

// MediaPipe 얼굴 메시 478점 중 윤곽(face oval) 36점. 이 순서로 이으면 닫힌 윤곽선이 된다 (이마 가운데 → 오른쪽 → 턱 → 왼쪽).
export const FACE_OVAL = [
  10, 338, 297, 332, 284, 251, 389, 356, 454, 323, 361, 288, 397, 365, 379, 378, 400, 377,
  152, 148, 176, 149, 150, 136, 172, 58, 132, 93, 234, 127, 162, 21, 54, 103, 67, 109,
];

// landmarks: MediaPipe 결과 {x, y, z}[] 478개 (0~1 비율) → 윤곽 36점의 [x, y]만. 나머지 점과 z는 버린다.
export function pickOutline(landmarks) {
  if (!Array.isArray(landmarks) || landmarks.length < 478) return [];
  return FACE_OVAL.map((i) => [landmarks[i].x, landmarks[i].y]);
}

// 화면 밖 좌표도 자르지 않는다 — 캔버스가 잘라 윤곽이 납작해지지 않게.
export function outlineToCanvas(outline, rect) {
  return outline.map((pt) => toCanvas(pt, rect));
}

export function faceOutline(landmarks, rect) {
  return outlineToCanvas(pickOutline(landmarks), rect);
}
