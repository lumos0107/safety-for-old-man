import {
  STATUS, closePolicy, retryDelayMs, normalizeServerUrl, cleanToken,
  fitContain, scaleToLongSide, visibleSegments, visiblePoints, ema, COLORS, faceOutline,
} from "./lib.js";
import { createFaceTracker } from "./face.js";

const JPEG_QUALITY = 0.7;
const MAX_SIDE = 640;
const REPLY_TIMEOUT_MS = 5000;
const CONNECT_TIMEOUT_MS = 8000; // 접속~ready까지. 인터넷이 안 되는 와이파이에서 OS 기본(수 분)만큼 멈추지 않게
const KPT_MIN_CONF = 0.5;

const $ = (id) => document.getElementById(id);
const video = $("video");
const stage = $("stage");
const overlay = $("overlay");
const ctx = overlay.getContext("2d");
const capture = document.createElement("canvas"); // 전송할 프레임
const cctx = capture.getContext("2d");
const shown = document.createElement("canvas");   // 마지막 결과를 만든 프레임 (동기 표시용)
const sctx = shown.getContext("2d");
const full = document.createElement("canvas");    // 원본 크기 프레임 (동기 표시 + 얼굴 윤곽일 때만)
const fctx = full.getContext("2d");
const KEY_URL = "pose.serverUrl";
const KEY_TOKEN = "pose.token";
const KEY_FACE = "pose.face";
const FACE_LOADING = "얼굴 모델 불러오는 중…";
const FACE_ERRORS = {
  unsupported: "이 브라우저에서는 얼굴 윤곽을 쓸 수 없습니다. 뼈대는 그대로 동작합니다.",
  timeout: "얼굴 모델을 30초 안에 불러오지 못해 얼굴 윤곽을 껐습니다.",
  load: "얼굴 모델을 불러오지 못해 얼굴 윤곽을 껐습니다.",
  runtime: "얼굴 윤곽 계산이 멈춰 껐습니다.",
};

const state = {
  running: false, facing: "environment", mode: "live",
  ws: null, authed: false, waitingSeq: null, seq: 0, attempt: 0,
  replyTimer: null, retryTimer: null,
  lastResult: null, lastResultAt: null, fps: null,
  stream: null, wakeLock: null,
  starting: false, cameraRequest: 0,
  shownFaces: null, pendingFace: null, // 동기 표시: 보낸 프레임의 얼굴 결과와 그 seq
};

// ---------- 설정 (이 브라우저에만 저장) ----------
function loadSettings() {
  try {
    return { url: localStorage.getItem(KEY_URL) || "", token: localStorage.getItem(KEY_TOKEN) || "" };
  } catch {
    return { url: "", token: "" };
  }
}
function saveSettings(url, token) {
  try {
    localStorage.setItem(KEY_URL, url);
    localStorage.setItem(KEY_TOKEN, token);
  } catch {
    setNotice("브라우저 저장소를 쓸 수 없어 설정이 저장되지 않았습니다.");
  }
}
// 얼굴 윤곽은 기본 끔 (시연용). 켜도 얼굴 좌표는 이 기기에서만 쓴다.
function loadFace() {
  try {
    return localStorage.getItem(KEY_FACE) === "1";
  } catch {
    return false;
  }
}
function saveFace(on) {
  try {
    localStorage.setItem(KEY_FACE, on ? "1" : "0");
  } catch {
    /* 저장소를 못 쓰면 이번 화면에서만 적용 */
  }
}
function hasSettings() {
  const s = loadSettings();
  return Boolean(normalizeServerUrl(s.url) && s.token);
}

// ---------- 표시 ----------
function setStatus(text, kind) {
  $("status").textContent = text;
  $("status").dataset.kind = kind;
}
function setNotice(text) {
  $("notice").textContent = text || "";
}
function resetStats() {
  state.fps = null;
  state.lastResultAt = null;
  $("fps").textContent = "-";
  $("ms").textContent = "-";
  $("people").textContent = "-";
  $("faces").textContent = "-";
}

function draw() {
  const bw = stage.clientWidth;
  const bh = stage.clientHeight;
  const dpr = window.devicePixelRatio || 1;
  if (overlay.width !== Math.round(bw * dpr) || overlay.height !== Math.round(bh * dpr)) {
    overlay.width = Math.round(bw * dpr);
    overlay.height = Math.round(bh * dpr);
  }
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, bw, bh);
  const r = state.lastResult;
  // 윤곽은 뼈대 결과와 따로 그린다: 실시간 표시에서는 서버 연결 전·재연결 중에도 나온다.
  // 동기 표시에서는 보낸 프레임에 맞춰야 하므로 결과가 있을 때만 (서버가 끊기면 윤곽도 멈춤).
  const faces = !face.enabled ? null : state.mode === "sync" ? (r ? state.shownFaces : null) : face.live;
  $("faces").textContent = faces ? String(faces.length) : "-";
  const rect = fitContain(video.videoWidth, video.videoHeight, bw, bh);
  if (!rect) return;
  if (state.mode === "sync" && r) ctx.drawImage(shown, rect.x, rect.y, rect.w, rect.h);
  if (r) drawPeople(r, rect);
  if (faces) drawFaces(faces, rect);
}

function drawFaces(faces, rect) {
  ctx.strokeStyle = COLORS.face;
  ctx.lineWidth = 2;
  ctx.lineJoin = "round";
  for (const landmarks of faces) {
    const pts = faceOutline(landmarks, rect);
    if (!pts.length) continue;
    ctx.beginPath();
    ctx.moveTo(pts[0][0], pts[0][1]);
    for (const [x, y] of pts.slice(1)) ctx.lineTo(x, y);
    ctx.closePath();
    ctx.stroke();
  }
}

function drawPeople(r, rect) {
  for (const p of r.people) {
    const [x1, y1, x2, y2] = p.box;
    ctx.strokeStyle = "rgba(255, 255, 255, 0.55)"; // 좌우 색과 겹치지 않게 박스는 흰색
    ctx.lineWidth = 1.5;
    ctx.strokeRect(rect.x + x1 * rect.w, rect.y + y1 * rect.h, (x2 - x1) * rect.w, (y2 - y1) * rect.h);
    ctx.lineWidth = 3.5;
    ctx.lineCap = "round";
    for (const [ax, ay, bx, by, color] of visibleSegments(p.kpts, rect, KPT_MIN_CONF)) {
      ctx.strokeStyle = color;
      ctx.beginPath();
      ctx.moveTo(ax, ay);
      ctx.lineTo(bx, by);
      ctx.stroke();
    }
    ctx.lineWidth = 1.5;
    ctx.strokeStyle = "#ffffff"; // 어두운 배경에서도 보이게 흰 테두리
    for (const [x, y, color] of visiblePoints(p.kpts, rect, KPT_MIN_CONF)) {
      ctx.fillStyle = color;
      ctx.beginPath();
      ctx.arc(x, y, 4.5, 0, Math.PI * 2);
      ctx.fill();
      ctx.stroke();
    }
  }
}

function clearOverlay() {
  state.lastResult = null;
  state.shownFaces = null;
  state.pendingFace = null;
  draw();
}

// ---------- 얼굴 윤곽 (face.js) ----------
const face = createFaceTracker({
  onUpdate(kind) {
    if (kind === "loading") setNotice(FACE_LOADING);
    if (kind === "ready" && $("notice").textContent === FACE_LOADING) setNotice("");
    draw();
  },
  onError(code) {
    saveFace(false); // 체크박스도 끔으로 되돌린다
    setNotice(FACE_ERRORS[code] ?? FACE_ERRORS.load);
    draw();
  },
});

// 설정·실행 상태·표시 방식에 맞춰 얼굴 계산을 켜고 끈다. 카메라가 켜져 있을 때만 계산한다.
function applyFace() {
  const want = loadFace() && state.running;
  if (want) face.setSource(state.mode === "live" ? video : null);
  if (want !== face.enabled) face.setEnabled(want);
  if (!want) {
    state.shownFaces = null;
    state.pendingFace = null;
  }
  draw();
}

// ---------- 카메라 ----------
// 카메라 요청이 겹치면(버튼을 빠르게 두 번) 마지막 요청만 쓰고 앞선 요청이 받은 스트림은 바로 끈다.
// 앞선 요청은 true를 돌려준다: 실패가 아니라 더 새 요청이 카메라를 맡았다는 뜻이다.
async function startCamera() {
  const request = ++state.cameraRequest;
  stopCamera();
  if (!navigator.mediaDevices?.getUserMedia) {
    setNotice("이 브라우저(또는 https가 아닌 주소)에서는 카메라를 쓸 수 없습니다.");
    return false;
  }
  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({
      audio: false,
      video: { facingMode: state.facing, width: { ideal: 1280 }, height: { ideal: 720 } },
    });
  } catch (e) {
    if (request !== state.cameraRequest) return true;
    setNotice(
      e.name === "NotAllowedError"
        ? "카메라 권한이 거부됐습니다. 주소창 왼쪽의 사이트 설정(자물쇠)에서 카메라를 허용한 뒤 새로고침하세요."
        : e.name === "NotFoundError"
          ? "카메라를 찾을 수 없습니다."
          : `카메라를 켤 수 없습니다 (${e.name}).`,
    );
    return false;
  }
  if (request !== state.cameraRequest) {
    stream.getTracks().forEach((t) => t.stop());
    return true;
  }
  stopCamera(); // 겹친 요청 사이에 다른 스트림이 들어왔으면 끈다
  state.stream = stream;
  video.srcObject = state.stream;
  await video.play().catch(() => {});
  // 요청한 방향이 아니라 실제로 잡힌 카메라로 판단한다. 값이 없으면(노트북 웹캠 등) 전면으로 본다.
  const facing = state.stream.getVideoTracks()[0]?.getSettings().facingMode;
  stage.classList.toggle("mirror", (facing || "user") === "user");
  return true;
}

function stopCamera() {
  state.stream?.getTracks().forEach((t) => t.stop());
  state.stream = null;
  video.srcObject = null;
}

// ---------- 화면 꺼짐 방지 ----------
async function requestWakeLock() {
  try {
    state.wakeLock = (await navigator.wakeLock?.request("screen")) ?? null;
  } catch {
    state.wakeLock = null; // 지원하지 않거나 거부되면 무시
  }
}
function releaseWakeLock() {
  state.wakeLock?.release().catch(() => {});
  state.wakeLock = null;
}

// ---------- 연결 ----------
function detachSocket() {
  const ws = state.ws;
  state.ws = null;
  state.authed = false;
  state.waitingSeq = null;
  clearTimeout(state.replyTimer);
  if (ws) {
    ws.onopen = ws.onmessage = ws.onclose = ws.onerror = null;
    try { ws.close(1000); } catch { /* 이미 닫힘 */ }
  }
}

function connect() {
  clearTimeout(state.retryTimer);
  if (state.ws) detachSocket(); // 이전 소켓이 떠돌며 나중에 인증해 이 연결을 4010으로 밀어내지 않게
  const { url, token } = loadSettings();
  const wsUrl = normalizeServerUrl(url);
  if (!wsUrl || !token) {
    stop();
    setStatus(STATUS.NEED_SETUP, "bad");
    openSettings();
    return;
  }
  setStatus(STATUS.CONNECTING, "wait");
  let ws;
  try {
    ws = new WebSocket(wsUrl);
  } catch {
    stop();
    setStatus(STATUS.NEED_SETUP, "bad");
    setNotice("서버 주소로 연결할 수 없습니다. 설정을 확인하세요.");
    return;
  }
  state.ws = ws;
  state.authed = false;
  state.waitingSeq = null;
  ws.onopen = () => {
    if (ws === state.ws) ws.send(JSON.stringify({ type: "auth", token }));
  };
  ws.onmessage = (ev) => onMessage(ws, ev);
  ws.onclose = (ev) => onClose(ws, ev);
  clearTimeout(state.replyTimer);
  state.replyTimer = setTimeout(onReplyTimeout, CONNECT_TIMEOUT_MS); // ready를 받으면 해제
}

function scheduleReconnect() {
  const delay = retryDelayMs(state.attempt++);
  setNotice(`${delay / 1000}초 뒤 다시 연결합니다.`);
  clearTimeout(state.retryTimer);
  state.retryTimer = setTimeout(connect, delay);
}

function onMessage(ws, ev) {
  if (ws !== state.ws || typeof ev.data !== "string") return;
  let msg;
  try {
    msg = JSON.parse(ev.data);
  } catch {
    return;
  }
  if (msg.type === "ready") {
    clearTimeout(state.replyTimer); // 연결 단계 시간 제한 해제
    state.authed = true;
    state.attempt = 0;
    setStatus(STATUS.CONNECTED, "ok");
    setNotice("");
    pump();
    return;
  }
  if (msg.type !== "result" && msg.type !== "error") return;
  if (msg.seq !== undefined && msg.seq !== state.waitingSeq) return; // 이전 프레임의 늦은 응답
  clearTimeout(state.replyTimer);
  state.waitingSeq = null;
  if (msg.type === "result") onResult(msg);
  else setNotice(`프레임 오류: ${msg.code}`);
  pump();
}

function onClose(ws, ev) {
  if (ws !== state.ws) return;
  state.ws = null;
  state.authed = false;
  state.waitingSeq = null;
  clearTimeout(state.replyTimer);
  clearOverlay();
  const policy = closePolicy(ev.code);
  if (!state.running) return;
  if (policy.retry) {
    setStatus(policy.status, "wait");
    scheduleReconnect();
  } else {
    stop();
    setStatus(policy.status, "bad");
    setNotice(policy.status === STATUS.BAD_TOKEN ? "토큰이 맞지 않습니다. 설정에서 고친 뒤 다시 시작하세요." : "");
  }
}

// 연결이 8초 안에 ready에 이르지 못하거나 프레임 응답이 5초 안에 오지 않으면
// 연결이 멈춘 것으로 보고 새로 붙는다 (와이파이↔LTE 전환, 인터넷이 안 되는 와이파이 등)
function onReplyTimeout() {
  detachSocket();
  clearOverlay();
  setStatus(STATUS.OFFLINE, "wait");
  scheduleReconnect();
}

// ---------- 프레임 전송 (한 장 보내고 결과를 받은 뒤 다음 장) ----------
async function pump() {
  if (!state.running || !state.authed || state.waitingSeq !== null || document.hidden) return;
  if (!video.videoWidth) {
    setTimeout(pump, 100);
    return;
  }
  // 동기 표시에서 얼굴 윤곽을 계산할 때는 원본 크기 프레임을 한 번 그리고 거기서 전송 이미지를 만든다 (같은 순간)
  const syncFace = state.mode === "sync" && face.ready;
  let source = video;
  if (syncFace) {
    full.width = video.videoWidth;
    full.height = video.videoHeight;
    fctx.drawImage(video, 0, 0);
    source = full;
  }
  const { w, h } = scaleToLongSide(video.videoWidth, video.videoHeight, MAX_SIDE);
  capture.width = w;
  capture.height = h;
  cctx.drawImage(source, 0, 0, w, h);
  const seq = ++state.seq;
  state.waitingSeq = seq;
  const blob = await new Promise((resolve) => capture.toBlob(resolve, "image/jpeg", JPEG_QUALITY));
  const ws = state.ws;
  if (state.waitingSeq !== seq) return;
  if (!blob || !ws || ws.readyState !== WebSocket.OPEN) {
    state.waitingSeq = null;
    return;
  }
  ws.send(JSON.stringify({ type: "frame", seq }));
  ws.send(blob);
  state.replyTimer = setTimeout(onReplyTimeout, REPLY_TIMEOUT_MS);
  // 서버 응답을 기다리는 동안 같은 프레임의 얼굴을 계산해 둔다 (응답 뒤에 계산하면 표시가 그만큼 늦는다)
  state.pendingFace = syncFace ? { seq, faces: face.detectNow(full) } : null;
}

function onResult(r) {
  const now = performance.now();
  if (state.lastResultAt !== null) state.fps = ema(state.fps, 1000 / Math.max(1, now - state.lastResultAt));
  state.lastResultAt = now;
  state.lastResult = r;
  // 다음 pump가 capture를 덮어쓰기 전에 이 결과의 프레임을 보관한다 (동기 표시 재그리기용)
  shown.width = capture.width;
  shown.height = capture.height;
  sctx.drawImage(capture, 0, 0);
  state.shownFaces = state.pendingFace?.seq === r.seq ? state.pendingFace.faces : null;
  if ($("notice").textContent !== FACE_LOADING) setNotice(""); // 앞선 프레임 오류 안내 지우기
  $("fps").textContent = state.fps == null ? "-" : state.fps.toFixed(1);
  $("ms").textContent = r.infer_ms.toFixed(1);
  $("people").textContent = String(r.people.length);
  draw();
}

// ---------- 시작·정지 ----------
async function start() {
  if (state.starting) return; // 카메라를 켜는 동안 다시 누른 "시작"은 무시한다
  if (!hasSettings()) {
    setStatus(STATUS.NEED_SETUP, "bad");
    openSettings();
    return;
  }
  setNotice("");
  state.starting = true;
  try {
    if (!(await startCamera())) return;
  } finally {
    state.starting = false;
  }
  state.running = true;
  state.attempt = 0;
  $("start").textContent = "정지";
  resetStats();
  requestWakeLock();
  connect();
  applyFace();
}

function stop() {
  state.running = false;
  $("start").textContent = "시작";
  clearTimeout(state.retryTimer);
  detachSocket();
  stopCamera();
  releaseWakeLock();
  applyFace();
  clearOverlay();
  setStatus(STATUS.STOPPED, "wait");
}

// ---------- 설정 창 ----------
function openSettings() {
  const s = loadSettings();
  $("server-url").value = s.url;
  $("token").value = s.token;
  $("face-toggle").checked = loadFace();
  $("settings-error").textContent = "";
  if (!$("settings").open) $("settings").showModal();
}

// 취소는 submit 버튼이 아니어야 한다: 폼의 기본(첫) submit 버튼이 되면 키보드 Enter·"이동"이 취소로 처리된다
$("settings-cancel").addEventListener("click", () => $("settings").close());

$("settings-form").addEventListener("submit", (e) => {
  const url = $("server-url").value.trim();
  const token = cleanToken($("token").value);
  if (!normalizeServerUrl(url)) {
    e.preventDefault();
    $("settings-error").textContent = "서버 주소 형식이 올바르지 않습니다.";
    return;
  }
  if (!token) {
    e.preventDefault();
    $("settings-error").textContent = "토큰을 입력하세요.";
    return;
  }
  const before = loadSettings();
  saveSettings(url, token);
  saveFace($("face-toggle").checked);
  if (state.running) {
    if (before.url !== url || before.token !== token) { // 서버 설정이 바뀔 때만 다시 연결
      detachSocket();
      state.attempt = 0;
      connect();
    }
  } else {
    setStatus(STATUS.IDLE, "wait");
  }
  applyFace();
});

// ---------- 버튼·이벤트 ----------
$("start").addEventListener("click", () => (state.running ? stop() : start()));
$("settings-btn").addEventListener("click", openSettings);

$("flip").addEventListener("click", async () => {
  state.facing = state.facing === "user" ? "environment" : "user";
  if (state.running && !(await startCamera())) stop();
  else applyFace(); // 새 카메라 영상으로 얼굴 계산을 다시 건다
});

$("mode").addEventListener("click", () => {
  state.mode = state.mode === "live" ? "sync" : "live";
  $("mode").textContent = state.mode === "live" ? "표시: 실시간" : "표시: 동기";
  stage.classList.toggle("sync", state.mode === "sync");
  applyFace(); // 실시간이면 영상, 동기면 보낸 프레임을 계산
});

document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible" && state.running) {
    requestWakeLock(); // 탭이 가려지면 브라우저가 해제하므로 다시 요청
    pump();
  }
});

window.addEventListener("resize", draw);

setStatus(hasSettings() ? STATUS.IDLE : STATUS.NEED_SETUP, hasSettings() ? "wait" : "bad");
