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

export function toCanvas([nx, ny], rect) {
  return [rect.x + nx * rect.w, rect.y + ny * rect.h];
}

export function visibleSegments(kpts, rect, minConf) {
  const out = [];
  for (const [a, b] of SKELETON) {
    if (kpts[a][2] >= minConf && kpts[b][2] >= minConf) {
      out.push([...toCanvas(kpts[a], rect), ...toCanvas(kpts[b], rect)]);
    }
  }
  return out;
}

export function visiblePoints(kpts, rect, minConf) {
  return kpts.filter((k) => k[2] >= minConf).map((k) => toCanvas(k, rect));
}

export function ema(prev, sample, alpha = 0.2) {
  return prev == null ? sample : prev + alpha * (sample - prev);
}
