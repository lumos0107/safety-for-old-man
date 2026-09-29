import {
  STATUS, closePolicy, retryDelayMs, normalizeServerUrl, cleanToken,
  fitContain, scaleToLongSide, visibleSegments, visiblePoints, ema,
} from "./lib.js";

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
const KEY_URL = "pose.serverUrl";
const KEY_TOKEN = "pose.token";

const state = {
  running: false, facing: "environment", mode: "live",
  ws: null, authed: false, waitingSeq: null, seq: 0, attempt: 0,
  replyTimer: null, retryTimer: null,
  lastResult: null, lastResultAt: null, fps: null,
  stream: null, wakeLock: null,
  starting: false, cameraRequest: 0,
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
  const rect = fitContain(video.videoWidth, video.videoHeight, bw, bh);
  if (!r || !rect) return;
  if (state.mode === "sync") ctx.drawImage(shown, rect.x, rect.y, rect.w, rect.h);
  for (const p of r.people) {
    const [x1, y1, x2, y2] = p.box;
    ctx.strokeStyle = "#38bdf8";
    ctx.lineWidth = 2;
    ctx.strokeRect(rect.x + x1 * rect.w, rect.y + y1 * rect.h, (x2 - x1) * rect.w, (y2 - y1) * rect.h);
    ctx.strokeStyle = "#facc15";
    ctx.lineWidth = 3;
    for (const [ax, ay, bx, by] of visibleSegments(p.kpts, rect, KPT_MIN_CONF)) {
      ctx.beginPath();
      ctx.moveTo(ax, ay);
      ctx.lineTo(bx, by);
      ctx.stroke();
    }
    ctx.fillStyle = "#f43f5e";
    for (const [x, y] of visiblePoints(p.kpts, rect, KPT_MIN_CONF)) {
      ctx.beginPath();
      ctx.arc(x, y, 4, 0, Math.PI * 2);
      ctx.fill();
    }
  }
}

function clearOverlay() {
  state.lastResult = null;
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
  const { w, h } = scaleToLongSide(video.videoWidth, video.videoHeight, MAX_SIDE);
  capture.width = w;
  capture.height = h;
  cctx.drawImage(video, 0, 0, w, h);
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
  setNotice(""); // 앞선 프레임 오류 안내 지우기
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
}

function stop() {
  state.running = false;
  $("start").textContent = "시작";
  clearTimeout(state.retryTimer);
  detachSocket();
  stopCamera();
  releaseWakeLock();
  clearOverlay();
  setStatus(STATUS.STOPPED, "wait");
}

// ---------- 설정 창 ----------
function openSettings() {
  const s = loadSettings();
  $("server-url").value = s.url;
  $("token").value = s.token;
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
  saveSettings(url, token);
  if (state.running) {
    detachSocket();
    state.attempt = 0;
    connect();
  } else {
    setStatus(STATUS.IDLE, "wait");
  }
});

// ---------- 버튼·이벤트 ----------
$("start").addEventListener("click", () => (state.running ? stop() : start()));
$("settings-btn").addEventListener("click", openSettings);

$("flip").addEventListener("click", async () => {
  state.facing = state.facing === "user" ? "environment" : "user";
  if (state.running && !(await startCamera())) stop();
});

$("mode").addEventListener("click", () => {
  state.mode = state.mode === "live" ? "sync" : "live";
  $("mode").textContent = state.mode === "live" ? "표시: 실시간" : "표시: 동기";
  stage.classList.toggle("sync", state.mode === "sync");
  draw();
});

document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible" && state.running) {
    requestWakeLock(); // 탭이 가려지면 브라우저가 해제하므로 다시 요청
    pump();
  }
});

window.addEventListener("resize", draw);

setStatus(hasSettings() ? STATUS.IDLE : STATUS.NEED_SETUP, hasSettings() ? "wait" : "bad");
