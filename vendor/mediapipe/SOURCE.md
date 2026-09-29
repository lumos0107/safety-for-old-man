# 포함한 외부 파일의 출처

얼굴 윤곽 표시(설계 `design/2026-09-30-face-outline-design.md`)에 쓰는 MediaPipe 파일이다. 실행 중에 외부에서 받지 않도록 저장소에 함께 둔다.
라이선스: Apache License 2.0 — 사본은 같은 폴더의 `LICENSE` (출처 https://github.com/google-ai-edge/mediapipe/blob/master/LICENSE). 파일은 수정하지 않았다.
얼굴 모델(`face_landmarker.task` = 얼굴 탐지 BlazeFace short-range + Face Mesh V2)의 라이선스 근거는 모델 카드다 — 두 카드 모두 "LICENSED UNDER Apache License, Version 2.0" (2026-09-30 확인):
[Face Mesh V2 모델 카드](https://storage.googleapis.com/mediapipe-assets/Model%20Card%20MediaPipe%20Face%20Mesh%20V2.pdf) · [BlazeFace short-range 모델 카드](https://storage.googleapis.com/mediapipe-assets/MediaPipe%20BlazeFace%20Model%20Card%20(Short%20Range).pdf)

| 파일 | 출처 | 크기(바이트) | SHA-256 |
|---|---|---|---|
| `vision_bundle.mjs` | npm `@mediapipe/tasks-vision@1.0.1` (https://registry.npmjs.org/@mediapipe/tasks-vision/-/tasks-vision-1.0.1.tgz) `package/vision_bundle.mjs` | 155439 | `d885630c297c0b20b1fe86096cb06291c4c8080876f27852e724f24ac603713f` |
| `wasm/vision_wasm_internal.js` | 같은 패키지 `package/wasm/vision_wasm_internal.js` (WebAssembly SIMD 판 로더) | 323377 | `e170ee67dd4e16c1a6fcd8840a206687e5a59b22c20e4a902bc445b095454d73` |
| `wasm/vision_wasm_internal.wasm` | 같은 패키지 `package/wasm/vision_wasm_internal.wasm` (SIMD 판) | 11756954 | `8da277a733926eacd0474b8704b36742d6ec3231c57a860c5b889dff8f1df886` |
| `face_landmarker.task` | https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task | 3758596 | `64184e229b263107bc2b804c6625db1341ff2bb731874b0bcc2fe6544e0bc9ff` |
| `LICENSE` | https://raw.githubusercontent.com/google-ai-edge/mediapipe/master/LICENSE | 12331 | `8707eef0533987efc5b155d64761eeb6e20793f50b9bd1a68dad1cf4719d0ed8` |

비SIMD 판(`vision_wasm_nosimd_internal.*`)은 넣지 않았다. SIMD를 지원하지 않는 브라우저에서는 얼굴 윤곽만 쓸 수 없다고 안내한다.

확인 (저장소 루트에서):
```
cd vendor/mediapipe && sha256sum -c SHA256SUMS
```
