// 얼굴 윤곽: 브라우저에서 MediaPipe Face Landmarker로 계산한다 (설계 design/2026-09-30-face-outline-design.md).
// 얼굴 좌표는 이 기기 안에서만 쓰고 서버로 보내거나 저장하지 않는다.

const BASE = new URL("./vendor/mediapipe/", import.meta.url);
const LOAD_TIMEOUT_MS = 30000;
const MIN_INTERVAL_MS = 1000 / 15; // 초당 계산 상한 15회 — detectForVideo는 동기라 메인 스레드를 막는다

async function loadLandmarker() {
  const { FaceLandmarker, FilesetResolver } = await import("./vendor/mediapipe/vision_bundle.mjs");
  // 비SIMD 파일은 넣지 않았다. 라이브러리가 스스로 고르면 없는 파일을 찾으므로 직접 확인하고 경로를 고정한다.
  if (!(await FilesetResolver.isSimdSupported())) throw new Error("unsupported");
  const fileset = {
    wasmLoaderPath: new URL("wasm/vision_wasm_internal.js", BASE).href,
    wasmBinaryPath: new URL("wasm/vision_wasm_internal.wasm", BASE).href,
  };
  const options = (delegate) => ({
    baseOptions: { modelAssetPath: new URL("face_landmarker.task", BASE).href, delegate },
    runningMode: "VIDEO",
    numFaces: 3,
    minFaceDetectionConfidence: 0.5,
    outputFaceBlendshapes: false, // 표정은 계산하지 않는다
    outputFacialTransformationMatrixes: false,
  });
  try {
    return await FaceLandmarker.createFromOptions(fileset, options("GPU"));
  } catch {
    return await FaceLandmarker.createFromOptions(fileset, options("CPU")); // WebGL이 없거나 실패하면
  }
}

function withTimeout(promise, ms) {
  let timer;
  const timeout = new Promise((_, reject) => {
    timer = setTimeout(() => reject(new Error("timeout")), ms);
  });
  return Promise.race([promise, timeout]).finally(() => clearTimeout(timer));
}

function safeClose(landmarker) {
  try {
    landmarker?.close();
  } catch {
    /* 이미 닫힘 */
  }
}

// onUpdate(kind): "loading" | "ready" | "result" | "off"
// onError(code): "unsupported" | "timeout" | "load" | "runtime"
export function createFaceTracker({ onUpdate, onError }) {
  let enabled = false;
  let landmarker = null;
  let loading = null; // 불러오기 약속은 하나만 — 켜기·끄기를 반복해도 모델을 두 번 만들지 않는다
  let request = 0; // 마지막 켜기 요청만 오류를 알린다
  let recreated = false;
  let source = null; // 실시간 표시에서 계산할 video (동기 표시에서는 null)
  let loop = null; // { kind: "rvfc" | "raf", id }
  let lastRun = 0;
  let lastFrameTime = -1;
  let lastTs = 0;
  let live = null; // 실시간 표시의 최근 결과 (얼굴마다 {x, y, z}[])

  async function setEnabled(on) {
    const my = ++request;
    enabled = on;
    if (!on) {
      stopLoop();
      safeClose(landmarker);
      landmarker = null;
      live = null;
      onUpdate("off");
      return;
    }
    if (landmarker) {
      startLoop();
      return;
    }
    onUpdate("loading");
    if (!loading) loading = loadLandmarker();
    const pending = loading;
    let made;
    try {
      made = await withTimeout(pending, LOAD_TIMEOUT_MS);
    } catch (e) {
      if (loading === pending) loading = null;
      pending.then((late) => { if (late !== landmarker) safeClose(late); }, () => {}); // 시간 초과 뒤 늦게 만들어지면 닫는다
      if (my === request && enabled) {
        enabled = false;
        onError(["unsupported", "timeout"].includes(e.message) ? e.message : "load");
      }
      return;
    }
    if (loading === pending) loading = null;
    if (!enabled) {
      safeClose(made); // 불러오는 동안 꺼졌다
      return;
    }
    if (!landmarker) {
      landmarker = made;
      onUpdate("ready");
      startLoop();
    } else if (made !== landmarker) {
      safeClose(made);
    }
  }

  function nextTimestamp() {
    // VIDEO 모드의 시간값은 계속 늘어나야 한다 (실시간·동기 모두 이 하나로)
    lastTs = Math.max(performance.now(), lastTs + 1);
    return lastTs;
  }

  function detect(image) {
    if (!enabled || !landmarker) return null;
    try {
      return landmarker.detectForVideo(image, nextTimestamp()).faceLandmarks ?? [];
    } catch {
      // 아이폰이 앱 전환 뒤 WebGL 컨텍스트를 잃는 경우 등: 한 번은 다시 만들고, 또 실패하면 끈다
      safeClose(landmarker);
      landmarker = null;
      stopLoop();
      if (!recreated) {
        recreated = true;
        setEnabled(true);
      } else {
        enabled = false;
        onError("runtime");
      }
      return null;
    }
  }

  function tick() {
    loop = null;
    if (!enabled || !landmarker || !source) return;
    const now = performance.now();
    const fresh = source.currentTime !== lastFrameTime; // 새 영상 프레임일 때만
    if (!document.hidden && source.readyState >= 2 && source.videoWidth && fresh && now - lastRun >= MIN_INTERVAL_MS) {
      lastRun = now;
      lastFrameTime = source.currentTime;
      const faces = detect(source);
      if (faces) {
        live = faces;
        onUpdate("result");
      }
    }
    if (enabled && landmarker && source) schedule();
  }

  function schedule() {
    if (source.requestVideoFrameCallback) loop = { kind: "rvfc", id: source.requestVideoFrameCallback(tick) };
    else loop = { kind: "raf", id: requestAnimationFrame(tick) };
  }

  function startLoop() {
    if (!loop && enabled && landmarker && source) schedule();
  }

  function stopLoop() {
    if (!loop) return;
    if (loop.kind === "rvfc") source?.cancelVideoFrameCallback?.(loop.id);
    else cancelAnimationFrame(loop.id);
    loop = null;
  }

  return {
    setEnabled,
    // 실시간 표시면 video, 동기 표시면 null (그때는 보낸 프레임을 detectNow로 계산)
    setSource(video) {
      stopLoop();
      source = video;
      live = null;
      lastFrameTime = -1;
      startLoop();
    },
    detectNow: detect,
    get live() { return live; },
    get enabled() { return enabled; },
    get ready() { return enabled && landmarker !== null; },
  };
}
