const GUIDE_STEPS = [
  { title: "Start with a wider view of the truck.", instruction: "Walk around the truck at a steady pace." },
  { title: "Walk around the truck at a steady pace.", instruction: "Keep the body in frame as you move." },
  { title: "Show both sides if you can.", instruction: "A full exterior sweep helps the appraisal." },
  { title: "Get a closer view of the tires.", instruction: "A clear tire view improves the estimate." },
  { title: "Pause briefly on any visible damage.", instruction: "Hold steady if rust, dents, or missing parts are visible." },
  { title: "Hold steady for a clearer view.", instruction: "Give the camera a moment before you keep moving." },
  { title: "Looking good—capture a few more details or stop when ready.", instruction: "Broad exterior coverage matters more than a perfect set of angles." },
];

const MIN_PHOTOS = 3;
const TARGET_PHOTOS = 7;
const ANALYSIS_INTERVAL_MS = 600;
const AUTO_CAPTURE_DELAY_MS = 6000;
const MIN_VEHICLE_SCORE = 0.42;
const VEHICLE_CLASSES = new Set(["truck", "car", "bus"]);
const ANALYSIS_STAGES = [
  "Reviewing captured views",
  "Identifying vehicle details",
  "Checking visible condition",
  "Finding similar trucks",
  "Calculating your value range",
];

const preview = document.querySelector("#cameraPreview");
const detectionOverlay = document.querySelector("#detectionOverlay");
const captureCanvas = document.querySelector("#captureCanvas");
const cameraMessage = document.querySelector("#cameraMessage");
const cameraSelect = document.querySelector("#cameraSelect");
const startButton = document.querySelector("#startButton");
const pauseButton = document.querySelector("#pauseButton");
const stopButton = document.querySelector("#stopButton");
const captureButton = document.querySelector("#captureButton");
const autoCapture = document.querySelector("#autoCapture");
const resetButton = document.querySelector("#resetButton");
const submitButton = document.querySelector("#submitButton");
const continueButton = document.querySelector("#continueButton");
const retryCameraButton = document.querySelector("#retryCameraButton");
const actionMessage = document.querySelector("#actionMessage");
const frameStatus = document.querySelector("#frameStatus");
const frameStatusText = document.querySelector("#frameStatusText");
const captureFlash = document.querySelector("#captureFlash");
const captureToast = document.querySelector("#captureToast");
const guideOverlay = document.querySelector("#guideOverlay");
const guideOverlayStep = document.querySelector("#guideOverlayStep");
const guideOverlayTitle = document.querySelector("#guideOverlayTitle");
const guideOverlayInstruction = document.querySelector("#guideOverlayInstruction");
const recordingTime = document.querySelector("#recordingTime");
const recordingBadge = document.querySelector("#recordingBadge");
const sessionState = document.querySelector("#sessionState");
const captureGrid = document.querySelector("#captureGrid");
const captureStrip = document.querySelector("#captureStrip");
const captureCount = document.querySelector("#captureCount");
const coverageChip = document.querySelector("#coverageChip");
const reviewSummary = document.querySelector("#reviewSummary");
const coverageSummary = document.querySelector("#coverageSummary");
const submitStatus = document.querySelector("#submitStatus");
const submitStatusIcon = document.querySelector("#submitStatusIcon");
const submitHeading = document.querySelector("#submitHeading");
const resultPanel = document.querySelector("#resultScreen");
const sessionVideoReview = document.querySelector("#sessionVideoReview");
const sessionPlayback = document.querySelector("#sessionPlayback");
const sessionVideoMeta = document.querySelector("#sessionVideoMeta");
const permissionCard = document.querySelector("#permissionCard");
const readyCopy = document.querySelector("#readyCopy");
const appShell = document.querySelector(".app-shell");

const captures = [];
let cameraStream;
let activeCameraId = "";
let isOpeningCamera = false;
let isCapturing = false;
let isRecording = false;
let isPaused = false;
let pendingStart = false;
let detector;
let detectorMode = "loading";
let mediaRecorder;
let mediaChunks = [];
let sessionVideo;
let sessionVideoUrl;
let sessionDurationMs = 0;
let recordingStartedAt;
let elapsedBeforePause = 0;
let analysisTimer;
let clockTimer;
let toastTimer;
let analysisStageTimer;
let stableSince = 0;
let suggestionIndex = 0;
let forcedGuide = null;
let isSubmitting = false;
let discardingSession = false;
let cameraDenied = false;
let currentAssessment = { ready: false, type: "loading", message: "Loading framing assistant…", confidence: 0, quality: null };
let lastQuality = null;

function setScreen(name) {
  appShell.dataset.screen = name;
}

function coverageLabel(count = captures.length) {
  if (count <= 0) return "Just started";
  if (count < MIN_PHOTOS) return "Building coverage";
  if (count < 5) return "Good exterior coverage";
  return "Strong coverage";
}

function photoState(capture) {
  if (capture.blurry) return "Blurry / soft";
  if (capture.helpful) return "Helpful detail";
  if (capture.limited) return "Limited coverage";
  return "Ready";
}

function guideIndex() {
  return suggestionIndex;
}

function currentGuide() {
  return forcedGuide || GUIDE_STEPS[guideIndex()];
}

function setFrameStatus(type, message) {
  frameStatus.className = `frame-status is-${type}`;
  frameStatusText.textContent = message;
}

function showCameraMessage(message) {
  cameraMessage.textContent = message;
  cameraMessage.classList.remove("is-hidden");
}

function hideCameraMessage() {
  cameraMessage.classList.add("is-hidden");
}

function setPermissionState(denied) {
  cameraDenied = denied;
  permissionCard.hidden = !denied;
  readyCopy.hidden = denied;
}

function setSessionState(label, state = "ready") {
  sessionState.className = `session-state is-${state}`;
  sessionState.innerHTML = `<span class="state-dot"></span>${label}`;
}

function formatDuration(milliseconds) {
  const seconds = Math.floor(milliseconds / 1000);
  return `${String(Math.floor(seconds / 60)).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`;
}

function renderSessionControls() {
  pauseButton.textContent = isPaused ? "Resume" : "Pause";
  pauseButton.setAttribute("aria-pressed", String(isPaused));
}

function renderCurrentGuide() {
  const guide = currentGuide();
  guideOverlay.hidden = !isRecording;
  guideOverlay.classList.toggle("is-paused", isPaused);
  guideOverlayStep.textContent = "Guidance";
  guideOverlayTitle.textContent = guide.title;
  guideOverlayInstruction.textContent = guide.instruction;
}

function renderCaptureStrip() {
  captureStrip.replaceChildren();
  captures.forEach((capture, index) => {
    const thumb = document.createElement("div");
    thumb.className = `capture-thumb${capture.blurry ? " is-blurry" : ""}`;
    const image = new Image();
    image.src = capture.url;
    image.alt = `Captured view ${index + 1}`;
    thumb.append(image);
    if (capture.blurry) thumb.append(createElement("span", "capture-thumb-flag", "Soft"));
    captureStrip.append(thumb);
  });
}

function renderCaptures() {
  captureGrid.replaceChildren();
  captures.forEach((capture, index) => {
    const card = document.createElement("article");
    card.className = `capture-card${capture.blurry ? " is-blurry" : ""}`;
    const image = new Image();
    image.src = capture.url;
    image.alt = `Snapped truck photo ${index + 1}`;
    const label = document.createElement("span");
    label.className = "capture-card-label";
    label.textContent = `View ${index + 1} · ${photoState(capture)}`;
    const remove = document.createElement("button");
    remove.className = "remove-button";
    remove.type = "button";
    remove.textContent = "Remove";
    remove.addEventListener("click", () => removeCapture(capture.id));
    card.append(image, label, remove);
    captureGrid.append(card);
  });
}

function setSubmitStatus(tone, message) {
  submitStatus.className = `submit-status is-${tone}`;
  submitStatusIcon.textContent = tone === "ready" ? "✓" : tone === "alert" ? "!" : "•";
  submitHeading.textContent = message;
}

function renderCoverageSummary() {
  const usable = captures.filter((capture) => !capture.blurry).length;
  const sawVehicle = captures.some((capture) => capture.detectorConfidence >= MIN_VEHICLE_SCORE);
  const sawTires = captures.some((capture) => capture.helpful);
  coverageSummary.replaceChildren(
    createElement("h2", "", "Coverage summary"),
    createElement("p", "coverage-item is-ok", `${usable} usable exterior ${usable === 1 ? "view" : "views"}`),
    createElement("p", sawVehicle ? "coverage-item is-ok" : "coverage-item is-warn", sawVehicle ? "Vehicle details visible" : "Another wide exterior view may help identification"),
    createElement("p", sawTires ? "coverage-item is-ok" : "coverage-item is-warn", sawTires ? "Tire detail captured" : "A clear tire close-up may improve confidence"),
  );
}

function renderSubmitStatus(count, enoughPhotos, ready) {
  if (ready) setSubmitStatus("ready", "Ready to estimate from the captured views.");
  else if (sessionVideo && !enoughPhotos) setSubmitStatus("alert", "Continue capturing for broader exterior coverage.");
  else if (isPaused) setSubmitStatus("alert", "Unpause to keep capturing.");
  else if (isRecording) setSubmitStatus("neutral", enoughPhotos ? "Stop when you have useful exterior coverage." : "Keep walking — coverage is still building.");
  else setSubmitStatus("neutral", "Start a walkaround to begin.");
}

function renderReview() {
  const count = captures.length;
  const enoughPhotos = count >= MIN_PHOTOS;
  const ready = enoughPhotos && Boolean(sessionVideo);
  const label = coverageLabel(count);
  captureCount.textContent = `${count} ${count === 1 ? "view" : "views"}`;
  coverageChip.textContent = label;
  reviewSummary.textContent = `${label}. We’ll use the clearest exterior views to estimate value.`;
  renderSubmitStatus(count, enoughPhotos, ready);
  renderCoverageSummary();
  resetButton.disabled = !captures.length && !sessionVideo && !isRecording;
  submitButton.disabled = !ready || isRecording || isSubmitting;
  continueButton.disabled = isSubmitting;
  sessionVideoReview.hidden = !sessionVideoUrl;
  if (sessionVideoUrl) {
    if (sessionPlayback.src !== sessionVideoUrl) sessionPlayback.src = sessionVideoUrl;
    sessionVideoMeta.textContent = formatDuration(sessionDurationMs);
  }
  renderCaptures();
  renderCaptureStrip();
}

function renderAll() {
  renderSessionControls();
  renderCurrentGuide();
  renderReview();
}

function clearResult() {
  window.clearInterval(analysisStageTimer);
  resultPanel.className = "light-screen result-screen is-hidden";
  resultPanel.replaceChildren();
}

function createElement(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
}

function showCaptureToast(message = "View captured.") {
  captureToast.textContent = message;
  captureToast.hidden = false;
  window.clearTimeout(toastTimer);
  toastTimer = window.setTimeout(() => { captureToast.hidden = true; }, 1400);
}

function getActiveCameraId() {
  return cameraStream?.getVideoTracks()[0]?.getSettings().deviceId || "";
}

function hasLiveCamera(stream = cameraStream) {
  return Boolean(stream?.getVideoTracks().some((track) => track.readyState === "live"));
}

async function refreshCameraList(preferredCameraId = getActiveCameraId()) {
  if (!navigator.mediaDevices?.enumerateDevices) return cameraSelect.value;
  const cameras = (await navigator.mediaDevices.enumerateDevices()).filter((device) => device.kind === "videoinput");
  cameraSelect.replaceChildren();
  if (!cameras.length) {
    cameraSelect.add(new Option("No cameras found", ""));
    cameraSelect.disabled = true;
    return "";
  }
  cameras.forEach((camera, index) => cameraSelect.add(new Option(camera.label || `Camera ${index + 1}`, camera.deviceId)));
  const selected = cameras.some((camera) => camera.deviceId === preferredCameraId) ? preferredCameraId : cameras[0].deviceId;
  cameraSelect.value = selected;
  cameraSelect.disabled = isOpeningCamera || isRecording || cameras.length <= 1;
  return selected;
}

function stopAnalysisLoop() {
  window.clearTimeout(analysisTimer);
  analysisTimer = undefined;
  stableSince = 0;
}

function startAnalysisLoop() {
  stopAnalysisLoop();
  analyzeCurrentFrame();
}

function clearDetectionOverlay() {
  const context = detectionOverlay.getContext("2d");
  context.clearRect(0, 0, detectionOverlay.width, detectionOverlay.height);
}

function drawDetection(prediction) {
  const [x, y, width, height] = prediction.bbox;
  detectionOverlay.width = preview.videoWidth;
  detectionOverlay.height = preview.videoHeight;
  const context = detectionOverlay.getContext("2d");
  context.clearRect(0, 0, detectionOverlay.width, detectionOverlay.height);
  context.strokeStyle = "#69dbba";
  context.lineWidth = Math.max(4, preview.videoWidth / 300);
  context.setLineDash([18, 12]);
  context.strokeRect(x, y, width, height);
}

function measureFrameQuality() {
  const sample = document.createElement("canvas");
  const context = sample.getContext("2d", { willReadFrequently: true });
  sample.width = 160;
  sample.height = 90;
  context.drawImage(preview, 0, 0, sample.width, sample.height);
  const pixels = context.getImageData(0, 0, sample.width, sample.height).data;
  const grayscale = new Uint8Array(sample.width * sample.height);
  let brightness = 0;
  for (let pixel = 0, index = 0; pixel < pixels.length; pixel += 4, index += 1) {
    const value = Math.round(pixels[pixel] * 0.299 + pixels[pixel + 1] * 0.587 + pixels[pixel + 2] * 0.114);
    grayscale[index] = value;
    brightness += value;
  }
  let edges = 0;
  let comparisons = 0;
  for (let y = 1; y < sample.height; y += 1) {
    for (let x = 1; x < sample.width; x += 1) {
      const index = y * sample.width + x;
      edges += Math.abs(grayscale[index] - grayscale[index - 1]) + Math.abs(grayscale[index] - grayscale[index - sample.width]);
      comparisons += 2;
    }
  }
  brightness /= grayscale.length;
  return { isDark: brightness < 38, isBright: brightness > 236, isBlurry: edges / comparisons < 7 };
}

function assessFallbackFrame(quality) {
  if (quality.isDark) return { ready: false, type: "warning", message: "Too dark — add light or change angle", confidence: 0, quality };
  if (quality.isBright) return { ready: false, type: "warning", message: "Too much glare — change angle", confidence: 0, quality };
  if (quality.isBlurry) return { ready: false, type: "warning", message: "Hold steady — image is blurry", confidence: 0, quality };
  return { ready: true, type: "ready", message: "Hold steady for a clearer view", confidence: 0, quality };
}

function assessVehicleFrame(predictions, quality) {
  const vehicle = predictions.filter((item) => VEHICLE_CLASSES.has(item.class) && item.score >= MIN_VEHICLE_SCORE).sort((a, b) => b.score - a.score)[0];
  if (!vehicle) {
    clearDetectionOverlay();
    return { ready: false, type: "warning", message: "Keep the truck in frame", confidence: 0, quality };
  }
  drawDetection(vehicle);
  const [x, y, width, height] = vehicle.bbox;
  const frameWidth = preview.videoWidth;
  const frameHeight = preview.videoHeight;
  const coverage = (width * height) / (frameWidth * frameHeight);
  const clipped = x < frameWidth * 0.018 || y < frameHeight * 0.018 || x + width > frameWidth * 0.982 || y + height > frameHeight * 0.982;
  if (clipped || coverage > 0.84) return { ready: false, type: "warning", message: "Step back — part of the truck is cut off", confidence: vehicle.score, quality };
  if (coverage < 0.13) return { ready: false, type: "warning", message: "Move closer — the truck is too small", confidence: vehicle.score, quality };
  if (quality.isDark) return { ready: false, type: "warning", message: "Truck found, but the frame is too dark", confidence: vehicle.score, quality };
  if (quality.isBright) return { ready: false, type: "warning", message: "Truck found, but glare is hiding details", confidence: vehicle.score, quality };
  if (quality.isBlurry) return { ready: false, type: "warning", message: "Hold steady for a clearer view", confidence: vehicle.score, quality };
  return { ready: true, type: "ready", message: "Looking good — keep walking around the truck", confidence: vehicle.score, quality };
}

async function analyzeCurrentFrame() {
  if (!cameraStream || preview.readyState < HTMLMediaElement.HAVE_CURRENT_DATA || isCapturing) {
    analysisTimer = window.setTimeout(analyzeCurrentFrame, ANALYSIS_INTERVAL_MS);
    return;
  }
  try {
    const quality = measureFrameQuality();
    lastQuality = quality;
    if (detectorMode === "ready" && detector) currentAssessment = assessVehicleFrame(await detector.detect(preview, 10, 0.35), quality);
    else if (detectorMode === "loading") currentAssessment = { ready: false, type: "loading", message: "Loading truck detector…", confidence: 0, quality };
    else {
      clearDetectionOverlay();
      currentAssessment = assessFallbackFrame(quality);
    }
    updateCaptureReadiness();
  } catch {
    currentAssessment = { ready: false, type: "error", message: "Framing check paused — try again", confidence: 0, quality: lastQuality };
    updateCaptureReadiness();
  }
  analysisTimer = window.setTimeout(analyzeCurrentFrame, ANALYSIS_INTERVAL_MS);
}

function updateCaptureReadiness() {
  if (isPaused) {
    captureButton.disabled = true;
    stableSince = 0;
    setFrameStatus("warning", "Paused — resume to capture");
    return;
  }
  const canSnap = isRecording && currentAssessment.ready && captures.length < TARGET_PHOTOS && !isCapturing;
  captureButton.disabled = !canSnap;
  setFrameStatus(currentAssessment.type, currentAssessment.message);
  if (!canSnap) {
    stableSince = 0;
    return;
  }
  if (!stableSince) stableSince = Date.now();
  const stableFor = Date.now() - stableSince;
  if (autoCapture.checked && stableFor >= AUTO_CAPTURE_DELAY_MS) captureFrame({ automatic: true });
  else if (autoCapture.checked) setFrameStatus("ready", "Hold steady for a clearer view");
}

function chooseRecorderOptions() {
  const types = ["video/webm;codecs=vp9,opus", "video/webm;codecs=vp8,opus", "video/mp4"];
  const mimeType = types.find((type) => MediaRecorder.isTypeSupported(type));
  return mimeType ? { mimeType } : undefined;
}

function sessionVideoFilename() {
  return sessionVideo?.type.includes("mp4") ? "session.mp4" : "session.webm";
}

function sessionElapsedMs() {
  return elapsedBeforePause + (recordingStartedAt ? Date.now() - recordingStartedAt : 0);
}

function runClock() {
  window.clearInterval(clockTimer);
  clockTimer = window.setInterval(() => {
    recordingTime.textContent = formatDuration(sessionElapsedMs());
  }, 1000);
}

function startClock() {
  elapsedBeforePause = 0;
  recordingStartedAt = Date.now();
  recordingTime.textContent = "00:00";
  runClock();
}

function pauseClock() {
  elapsedBeforePause = sessionElapsedMs();
  recordingStartedAt = undefined;
  stopClock();
  recordingTime.textContent = formatDuration(elapsedBeforePause);
}

function resumeClock() {
  recordingStartedAt = Date.now();
  runClock();
}

function stopClock() {
  window.clearInterval(clockTimer);
  clockTimer = undefined;
}

async function openCamera() {
  if (!navigator.mediaDevices?.getUserMedia) {
    setPermissionState(true);
    showCameraMessage("This browser does not support webcam access.");
    setFrameStatus("error", "Camera API unavailable");
    return;
  }
  if (isOpeningCamera) return;
  isOpeningCamera = true;
  cameraSelect.disabled = true;
  showCameraMessage("Requesting camera access…");
  const requestedId = cameraSelect.value;
  const video = requestedId ? { deviceId: { exact: requestedId }, width: { ideal: 1920 }, height: { ideal: 1080 } } : { facingMode: { ideal: "environment" }, width: { ideal: 1920 }, height: { ideal: 1080 } };
  let nextStream;
  try {
    nextStream = await navigator.mediaDevices.getUserMedia({ video, audio: false });
  } catch (error) {
    if (hasLiveCamera()) {
      hideCameraMessage();
      setPermissionState(false);
      activeCameraId = getActiveCameraId();
      await refreshCameraList(activeCameraId).catch(() => activeCameraId);
      actionMessage.textContent = "That camera could not be selected, so the current camera is still active.";
      setFrameStatus("warning", "Camera switch failed — using the current camera");
      startButton.disabled = isRecording;
      isOpeningCamera = false;
      cameraSelect.disabled = isRecording || cameraSelect.options.length <= 1;
      return;
    }
    const denied = error.name === "NotAllowedError" || error.name === "SecurityError";
    setPermissionState(denied || true);
    hideCameraMessage();
    setFrameStatus("error", denied ? "Camera permission needed" : "Camera unavailable");
    startButton.disabled = false;
    isOpeningCamera = false;
    cameraSelect.disabled = cameraSelect.options.length <= 1;
    pendingStart = false;
    return;
  }

  const previousStream = cameraStream;
  cameraStream = nextStream;
  preview.srcObject = nextStream;
  previousStream?.getTracks().forEach((track) => track.stop());
  hideCameraMessage();
  setPermissionState(false);

  try {
    await preview.play();
  } catch {
    if (!hasLiveCamera(nextStream)) {
      showCameraMessage("The camera stopped before the preview could begin. Try opening it again.");
      setFrameStatus("error", "Camera stream ended");
      startButton.disabled = false;
      isOpeningCamera = false;
      cameraSelect.disabled = cameraSelect.options.length <= 1;
      return;
    }
  }

  activeCameraId = await refreshCameraList(requestedId || getActiveCameraId()).catch(() => requestedId || getActiveCameraId());
  hideCameraMessage();
  startButton.disabled = false;
  setSessionState("Camera ready", "ready");
  startAnalysisLoop();
  isOpeningCamera = false;
  cameraSelect.disabled = cameraSelect.options.length <= 1 || isRecording;
  if (pendingStart) {
    pendingStart = false;
    startSession();
  }
}

function startWalkaround() {
  if (isRecording) {
    setScreen("capturing");
    return;
  }
  if (!cameraStream) {
    pendingStart = true;
    openCamera();
    return;
  }
  startSession();
}

function startSession() {
  if (!cameraStream) {
    pendingStart = true;
    openCamera();
    return;
  }
  if (!window.MediaRecorder) {
    actionMessage.textContent = "This browser cannot record a session video. Try a current Safari or Chrome build.";
    return;
  }
  clearResult();
  mediaChunks = [];
  sessionVideo = undefined;
  sessionDurationMs = 0;
  if (sessionVideoUrl) URL.revokeObjectURL(sessionVideoUrl);
  sessionVideoUrl = undefined;
  try {
    mediaRecorder = new MediaRecorder(cameraStream, chooseRecorderOptions());
  } catch {
    actionMessage.textContent = "We could not start the session recorder with this camera.";
    return;
  }
  mediaRecorder.addEventListener("dataavailable", (event) => { if (event.data.size) mediaChunks.push(event.data); });
  mediaRecorder.addEventListener("stop", () => {
    isRecording = false;
    isPaused = false;
    stopClock();
    recordingBadge.classList.add("is-hidden");
    pauseButton.disabled = true;
    stopButton.disabled = true;
    cameraSelect.disabled = cameraSelect.options.length <= 1;
    if (discardingSession) {
      discardingSession = false;
      mediaChunks = [];
      sessionVideo = undefined;
      sessionDurationMs = 0;
      startButton.disabled = !cameraStream;
      return;
    }
    const type = mediaRecorder.mimeType || "video/webm";
    sessionDurationMs = sessionElapsedMs();
    sessionVideo = new Blob(mediaChunks, { type });
    sessionVideoUrl = URL.createObjectURL(sessionVideo);
    startButton.disabled = true;
    setSessionState("Walkaround saved", "complete");
    setScreen("review");
    actionMessage.textContent = "";
    renderAll();
  }, { once: true });
  mediaRecorder.start(1000);
  isRecording = true;
  isPaused = false;
  if (!forcedGuide) suggestionIndex = captures.length ? Math.min(captures.length, GUIDE_STEPS.length - 1) : 0;
  startButton.disabled = true;
  pauseButton.disabled = typeof mediaRecorder.pause !== "function";
  stopButton.disabled = false;
  cameraSelect.disabled = true;
  recordingBadge.classList.remove("is-hidden");
  setSessionState("Capturing", "recording");
  actionMessage.textContent = "";
  startClock();
  setScreen("capturing");
  renderAll();
}

function togglePause() {
  if (!isRecording || !mediaRecorder) return;
  if (isPaused) {
    if (mediaRecorder.state === "paused") mediaRecorder.resume();
    isPaused = false;
    resumeClock();
    recordingBadge.classList.remove("is-hidden");
    setSessionState("Capturing", "recording");
    actionMessage.textContent = "Keep moving around the truck.";
  } else {
    if (mediaRecorder.state !== "recording") return;
    mediaRecorder.pause();
    isPaused = true;
    pauseClock();
    stableSince = 0;
    recordingBadge.classList.add("is-hidden");
    setSessionState("Paused", "paused");
    actionMessage.textContent = "Paused. Resume when you are ready.";
  }
  renderAll();
  updateCaptureReadiness();
}

function stopSession() {
  if (mediaRecorder?.state === "recording" || mediaRecorder?.state === "paused") mediaRecorder.stop();
}

function canvasToBlob(canvas) {
  return new Promise((resolve, reject) => canvas.toBlob((blob) => blob ? resolve(blob) : reject(new Error("Could not encode frame")), "image/jpeg", 0.9));
}

async function captureFrame({ automatic = false } = {}) {
  if (!isRecording || isPaused || !currentAssessment.ready || captures.length >= TARGET_PHOTOS || isCapturing) return;
  isCapturing = true;
  stopAnalysisLoop();
  const scale = Math.min(1, 1920 / preview.videoWidth);
  captureCanvas.width = Math.round(preview.videoWidth * scale);
  captureCanvas.height = Math.round(preview.videoHeight * scale);
  captureCanvas.getContext("2d").drawImage(preview, 0, 0, captureCanvas.width, captureCanvas.height);
  try {
    const blob = await canvasToBlob(captureCanvas);
    const quality = currentAssessment.quality || lastQuality;
    const helpful = /tire/i.test(currentGuide().title + currentGuide().instruction);
    captures.push({
      id: crypto.randomUUID?.() || `${Date.now()}-${captures.length}`,
      blob,
      url: URL.createObjectURL(blob),
      capturedAt: new Date().toISOString(),
      width: captureCanvas.width,
      height: captureCanvas.height,
      detectorConfidence: currentAssessment.confidence,
      blurry: Boolean(quality?.isBlurry),
      limited: currentAssessment.confidence > 0 && currentAssessment.confidence < 0.55,
      helpful,
    });
    forcedGuide = null;
    suggestionIndex = Math.min(suggestionIndex + 1, GUIDE_STEPS.length - 1);
    captureFlash.classList.remove("is-active");
    void captureFlash.offsetWidth;
    captureFlash.classList.add("is-active");
    showCaptureToast(quality?.isBlurry ? "View captured — flagged as soft." : "View captured.");
    actionMessage.textContent = captures.length === TARGET_PHOTOS
      ? "Strong coverage. Stop when you are ready."
      : automatic ? "Keep walking for another useful view." : "";
    renderAll();
  } catch {
    actionMessage.textContent = "That frame could not be saved. Hold steady and try again.";
  } finally {
    isCapturing = false;
    stableSince = 0;
    startAnalysisLoop();
  }
}

function removeCapture(id) {
  const index = captures.findIndex((capture) => capture.id === id);
  if (index === -1) return;
  URL.revokeObjectURL(captures[index].url);
  captures.splice(index, 1);
  renderAll();
}

function buildManifest() {
  return {
    schema_version: "4.0",
    captured_at: new Date().toISOString(),
    photo_count: captures.length,
    session_video: { filename: sessionVideoFilename(), type: sessionVideo?.type, size: sessionVideo?.size, duration_ms: sessionDurationMs },
    photos: captures.map((capture, index) => ({ id: capture.id, filename: `capture-${index + 1}.jpg`, captured_at: capture.capturedAt, width: capture.width, height: capture.height, detector_confidence: capture.detectorConfidence })),
  };
}

function buildFormData() {
  if (!sessionVideo || captures.length < MIN_PHOTOS) return null;
  const manifest = buildManifest();
  const formData = new FormData();
  formData.append("manifest", new Blob([JSON.stringify(manifest)], { type: "application/json" }), "manifest.json");
  captures.forEach((capture, index) => formData.append("photos", capture.blob, `capture-${index + 1}.jpg`));
  formData.append("video", sessionVideo, sessionVideoFilename());
  return { formData, manifest };
}

function formatMoney(value) {
  if (value === null || value === undefined || value === "") return null;
  const amount = Number(value);
  return Number.isFinite(amount) ? new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 }).format(amount) : null;
}

function confidenceBand(confidence) {
  if (confidence >= 0.8) {
    return {
      label: "High confidence",
      copy: "We found usable exterior coverage and relevant comparable listings.",
    };
  }
  if (confidence >= 0.6) {
    return {
      label: "Moderate confidence",
      copy: "We found usable exterior coverage and relevant comparable listings, though some details remain uncertain.",
    };
  }
  return {
    label: "Limited confidence",
    copy: "The captured views support a range, but several details remain uncertain.",
  };
}

function titleCase(value) {
  return String(value).replace(/[_-]+/g, " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function needsInfoCopy(response) {
  const reason = String(response.reason || "").toLowerCase();
  const message = String(response.message || "").toLowerCase();
  const haystack = `${reason} ${message}`;
  if (haystack.includes("tire")) {
    return {
      headline: "We need one more view",
      lead: "We captured enough to begin the appraisal, but we need a clearer view before we can give you a reliable value range.",
      missing: "A clear view of the tires",
      why: "Tire wear can affect the truck’s condition and estimated value.",
      cta: "Capture tire close-up",
      guide: { title: "Get a closer view of the tires.", instruction: "Move in until tread, sidewall, and wheel condition are visible." },
    };
  }
  if (haystack.includes("blur") || haystack.includes("no usable") || haystack.includes("clear enough")) {
    return {
      headline: "We need clearer exterior views",
      lead: "We captured enough to begin the appraisal, but we need a clearer view before we can give you a reliable value range.",
      missing: "Clearer exterior photos",
      why: "Hold steady and keep more of the truck in frame.",
      cta: "Capture another exterior view",
      guide: { title: "Hold steady for a clearer view.", instruction: "Pause, then capture a wider exterior shot with the truck fully in frame." },
    };
  }
  if (haystack.includes("low confidence") || haystack.includes("identify")) {
    return {
      headline: "We need another truck angle",
      lead: "We captured enough to begin the appraisal, but we need a clearer view before we can give you a reliable value range.",
      missing: "A wider exterior view",
      why: "Capture a wider exterior view with the body, grille, or badges visible.",
      cta: "Capture another exterior view",
      guide: { title: "Start with a wider view of the truck.", instruction: "Show the body, grille, or badges so the truck is easier to identify." },
    };
  }
  if (haystack.includes("usable photo") || haystack.includes("only")) {
    return {
      headline: "We need more exterior coverage",
      lead: "We captured enough to begin the appraisal, but we need a clearer view before we can give you a reliable value range.",
      missing: "Broader exterior coverage",
      why: "Walk around the truck and capture another broad view.",
      cta: "Continue walkaround",
      guide: { title: "Walk around the truck at a steady pace.", instruction: "Capture another broad exterior view as you move." },
    };
  }
  return {
    headline: "We need one more view",
    lead: "We captured enough to begin the appraisal, but we need a clearer view before we can give you a reliable value range.",
    missing: response.reason || "Another useful exterior view",
    why: response.message || "Capture the missing view, then estimate again.",
    cta: "Capture another exterior view",
    guide: { title: "Capture another useful exterior view.", instruction: response.message || "Keep the truck in frame and hold steady." },
  };
}

function appendResultActions(container, buttons) {
  const actions = createElement("div", "result-actions");
  buttons.forEach(([label, className, handler]) => {
    const button = createElement("button", className, label);
    button.type = "button";
    button.addEventListener("click", handler);
    actions.append(button);
  });
  container.append(actions);
}

function beginFocusedRecapture(guide) {
  resetSession({ confirmUser: false, followupMessage: "" });
  forcedGuide = guide;
  startWalkaround();
}

function renderPricedResult(response) {
  const range = Array.isArray(response.price_range) ? response.price_range : [];
  const low = formatMoney(range[0]);
  const high = formatMoney(range[1]);
  if (!low || !high) {
    renderErrorResult("The appraisal response was missing a usable value range.");
    return;
  }
  const confidence = Math.max(0, Math.min(1, Number(response.confidence) || 0));
  const band = confidenceBand(confidence);
  const breakdown = response.breakdown || {};
  const visual = breakdown.visual_comps || {};
  const views = breakdown.views_used ?? captures.length;
  setScreen("result");
  resultPanel.className = "light-screen result-screen is-priced";
  resultPanel.replaceChildren();

  const hero = createElement("section", "price-hero");
  hero.append(
    createElement("h2", "", "Estimated market value"),
    createElement("p", "price-range", `${low}–${high}`),
    createElement("p", "confidence-label", band.label),
    createElement("p", "confidence-copy", `Based on ${views} usable exterior views and similar market listings. ${band.copy}`),
  );
  resultPanel.append(hero);

  const identity = createElement("section", "evidence-card");
  identity.append(createElement("h2", "", "Vehicle identified"));
  const unknownIdentity = !breakdown.make || String(breakdown.make).toLowerCase() === "unknown";
  if (unknownIdentity && (!breakdown.model || String(breakdown.model).toLowerCase() === "unknown")) {
    identity.append(createElement("p", "result-copy", "Vehicle details could not be confidently identified."));
  } else {
    identity.append(createElement("p", "identity-name", [breakdown.make, breakdown.model].filter(Boolean).join(" ")));
    identity.append(createElement("p", "result-copy", breakdown.year_estimate ? `Estimated year: ${breakdown.year_estimate}` : "Year could not be confidently identified."));
  }
  resultPanel.append(identity);

  const condition = createElement("section", "evidence-card");
  condition.append(createElement("h2", "", "Visible condition"));
  condition.append(createElement("p", "identity-name", breakdown.condition ? `${titleCase(breakdown.condition)} exterior condition` : "Exterior condition could not be confidently assessed."));
  const tire = createElement("p", "result-copy", breakdown.tire_condition ? `Tires · ${titleCase(breakdown.tire_condition)}` : "Tire condition could not be confidently assessed.");
  condition.append(tire);
  const damage = Array.isArray(breakdown.damage)
    ? breakdown.damage.map((item) => typeof item === "string" ? item : item?.description).filter(Boolean)
    : [];
  condition.append(createElement("p", "", "Damage observed"));
  if (damage.length) {
    const list = createElement("ul", "damage-list");
    damage.forEach((item) => list.append(createElement("li", "", item)));
    condition.append(list);
  } else {
    condition.append(createElement("p", "result-copy", "No visible exterior damage identified in the captured views."));
  }
  condition.append(createElement("p", "result-copy", `Evidence used · ${views} usable exterior views`));
  resultPanel.append(condition);

  const comps = createElement("section", "comp-card");
  comps.append(createElement("h2", "", "Comparable market value"));
  const visualBase = formatMoney(visual.visual_base_price) || formatMoney(breakdown.base_price);
  comps.append(createElement("p", "result-copy", visualBase
    ? `Fleetworth compared your truck with visually similar real listings. Those listings suggested a starting market value around ${visualBase} before visible-condition adjustments.`
    : "Fleetworth compared your truck with visually similar real listings, then accounted for visible condition."));
  resultPanel.append(comps);

  const method = createElement("details", "method-card");
  method.append(createElement("summary", "", "How this estimate works"));
  const steps = createElement("ol", "condition-list");
  [
    "We review the clearest exterior views from your walkaround.",
    "We identify visible vehicle and condition signals.",
    "We compare the truck with similar real market listings.",
    "We account for visible wear, tire condition, and damage.",
    "We show a range because photo-based appraisal includes uncertainty.",
  ].forEach((step) => steps.append(createElement("li", "", step)));
  method.append(steps);
  resultPanel.append(method);

  const actions = [
    ["Start another appraisal", "button button-primary", () => resetSession({ confirmUser: false, followupMessage: "" })],
    ["Review captured views", "button button-secondary", () => setScreen("review")],
  ];
  if (confidence < 0.8) {
    actions.push(["Improve this estimate", "button button-quiet", () => beginFocusedRecapture(GUIDE_STEPS[3])]);
  }
  appendResultActions(resultPanel, actions);
  resultPanel.scrollIntoView({ behavior: "smooth", block: "start" });
}

function renderNeedsMoreInfo(response) {
  const copy = needsInfoCopy(response);
  setScreen("result");
  resultPanel.className = "light-screen result-screen is-needs-info";
  resultPanel.replaceChildren(
    createElement("p", "result-kicker", "More information needed"),
    createElement("h1", "", copy.headline),
    createElement("p", "light-lead", copy.lead),
  );
  const card = createElement("section", "reason-card");
  card.append(createElement("h2", "", "What’s missing"), createElement("p", "identity-name", copy.missing));
  card.append(createElement("h2", "", "Why it matters"), createElement("p", "result-copy", copy.why));
  resultPanel.append(card);
  appendResultActions(resultPanel, [
    [copy.cta, "button button-amber button-xl", () => beginFocusedRecapture(copy.guide)],
    ["Continue walkaround", "button button-secondary", () => beginFocusedRecapture(copy.guide)],
    ["Start over", "button button-quiet", () => resetSession({ confirmUser: false, followupMessage: "" })],
  ]);
  resultPanel.scrollIntoView({ behavior: "smooth", block: "start" });
}

function renderNotATruck(response) {
  setScreen("result");
  resultPanel.className = "light-screen result-screen is-needs-info";
  resultPanel.replaceChildren(
    createElement("p", "result-kicker", "Unable to assess"),
    createElement("h1", "", "We couldn’t identify a truck"),
    createElement("p", "light-lead", "Make sure the truck is clearly in frame, then try again."),
  );
  const card = createElement("section", "reason-card");
  card.append(createElement("h2", "", "What’s missing"), createElement("p", "identity-name", "A recognizable truck in the captured views"));
  card.append(createElement("p", "result-copy", response.message || "Fleetworth will not return a value unless a truck is clearly visible."));
  resultPanel.append(card);
  appendResultActions(resultPanel, [
    ["Start new capture", "button button-amber button-xl", () => beginFocusedRecapture(GUIDE_STEPS[0])],
    ["Start over", "button button-quiet", () => resetSession({ confirmUser: false, followupMessage: "" })],
  ]);
}

function renderErrorResult(message) {
  setScreen("result");
  resultPanel.className = "light-screen result-screen is-error";
  resultPanel.replaceChildren(
    createElement("p", "result-kicker", "Submission error"),
    createElement("h1", "", "We couldn’t submit this walkaround"),
    createElement("p", "light-lead", "Your images were captured, but the appraisal request did not complete."),
  );
  const card = createElement("section", "reason-card");
  card.append(createElement("p", "result-copy", message || "Check the connection and try again."));
  resultPanel.append(card);
  appendResultActions(resultPanel, [
    ["Try again", "button button-primary", submitSession],
    ["Return to review", "button button-secondary", () => setScreen("review")],
  ]);
}

function renderAnalysisState() {
  setScreen("result");
  resultPanel.className = "light-screen result-screen is-loading";
  resultPanel.setAttribute("aria-busy", "true");
  const list = createElement("ol", "analysis-list");
  ANALYSIS_STAGES.forEach((stage, index) => {
    const item = createElement("li", index === 0 ? "is-active" : "", stage);
    list.append(item);
  });
  resultPanel.replaceChildren(
    createElement("p", "result-kicker", "Appraisal in progress"),
    createElement("h1", "", "Building your appraisal"),
    createElement("p", "light-lead", "We’re reviewing visible condition and comparing this truck with similar market listings."),
    createElement("div", "spinner"),
    list,
    createElement("p", "result-copy analysis-wait", ""),
  );
  let stage = 0;
  const startedAt = Date.now();
  window.clearInterval(analysisStageTimer);
  analysisStageTimer = window.setInterval(() => {
    const items = [...list.children];
    if (stage < items.length) {
      items[stage].className = "is-done";
      if (items[stage + 1]) items[stage + 1].className = "is-active";
      stage += 1;
    }
    if (Date.now() - startedAt > 12000) {
      const wait = resultPanel.querySelector(".analysis-wait");
      if (wait) wait.textContent = "This is taking a little longer than usual. Your walkaround is still being analyzed.";
    }
  }, 1100);
}

function renderBackendResponse(response = {}) {
  window.clearInterval(analysisStageTimer);
  resultPanel.removeAttribute("aria-busy");
  if (response.status === "priced") renderPricedResult(response);
  else if (response.status === "needs_more_info") renderNeedsMoreInfo(response);
  else if (response.status === "not_a_truck") renderNotATruck(response);
  else renderErrorResult(response.message || response.detail || "The appraisal could not be completed.");
}

async function submitSession() {
  const payload = buildFormData();
  if (!payload) {
    setScreen("review");
    setSubmitStatus("alert", "Continue capturing until coverage is strong enough to estimate.");
    return;
  }
  const endpoint = document.body.dataset.predictEndpoint;
  window.dispatchEvent(new CustomEvent("fleetworth:capture-ready", { detail: { manifest: payload.manifest } }));
  if (!endpoint) {
    setScreen("result");
    resultPanel.className = "light-screen result-screen";
    resultPanel.replaceChildren(
      createElement("h1", "", "Evidence package ready"),
      createElement("p", "light-lead", `${captures.length} exterior photos and the session recording are ready. Connect the appraisal service to estimate value.`),
    );
    return;
  }
  isSubmitting = true;
  renderReview();
  renderAnalysisState();
  try {
    const response = await fetch(endpoint, { method: "POST", body: payload.formData });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.message || data.detail || `The analysis service returned ${response.status}.`);
    renderBackendResponse(data);
  } catch (error) {
    renderErrorResult(error.message || "Your evidence remains in this browser. Check the backend connection and try again.");
  } finally {
    isSubmitting = false;
    renderReview();
  }
}

function resetSession({ confirmUser = true, followupMessage = "Session discarded." } = {}) {
  if (confirmUser && !window.confirm("Start over and discard this walkaround?")) return;
  captures.forEach((capture) => URL.revokeObjectURL(capture.url));
  captures.splice(0, captures.length);
  if (isRecording) {
    discardingSession = true;
    stopSession();
  }
  if (sessionVideoUrl) URL.revokeObjectURL(sessionVideoUrl);
  sessionVideo = undefined;
  sessionVideoUrl = undefined;
  sessionDurationMs = 0;
  mediaChunks = [];
  sessionPlayback.pause();
  sessionPlayback.removeAttribute("src");
  sessionPlayback.load();
  sessionVideoReview.hidden = true;
  suggestionIndex = 0;
  forcedGuide = null;
  isPaused = false;
  elapsedBeforePause = 0;
  recordingStartedAt = undefined;
  recordingTime.textContent = "00:00";
  startButton.disabled = !cameraStream;
  pauseButton.disabled = true;
  stopButton.disabled = true;
  clearResult();
  setSessionState(cameraStream ? "Camera ready" : "Ready to capture", "ready");
  actionMessage.textContent = followupMessage;
  setScreen("ready");
  renderAll();
}

async function loadDetector() {
  if (!window.cocoSsd) {
    detectorMode = "fallback";
    autoCapture.checked = false;
    autoCapture.disabled = true;
    setFrameStatus("warning", "Auto-snap unavailable — tap the shutter");
    return;
  }
  try {
    detector = await window.cocoSsd.load({ base: "lite_mobilenet_v2" });
    detectorMode = "ready";
  } catch {
    detectorMode = "fallback";
    autoCapture.checked = false;
    autoCapture.disabled = true;
    setFrameStatus("warning", "Auto-snap unavailable — tap the shutter");
  }
}

cameraSelect.addEventListener("change", () => { if (!isRecording && cameraSelect.value !== activeCameraId) openCamera(); });
startButton.addEventListener("click", startWalkaround);
retryCameraButton.addEventListener("click", () => { pendingStart = false; openCamera(); });
pauseButton.addEventListener("click", togglePause);
stopButton.addEventListener("click", stopSession);
captureButton.addEventListener("click", () => captureFrame());
continueButton.addEventListener("click", startWalkaround);
resetButton.addEventListener("click", () => resetSession());
submitButton.addEventListener("click", submitSession);
autoCapture.addEventListener("change", () => { stableSince = 0; });
navigator.mediaDevices?.addEventListener("devicechange", () => refreshCameraList().catch(() => {}));
window.addEventListener("beforeunload", () => {
  if (mediaRecorder?.state === "recording" || mediaRecorder?.state === "paused") mediaRecorder.stop();
  stopAnalysisLoop();
  stopClock();
  window.clearInterval(analysisStageTimer);
  cameraStream?.getTracks().forEach((track) => track.stop());
  captures.forEach((capture) => URL.revokeObjectURL(capture.url));
  if (sessionVideoUrl) URL.revokeObjectURL(sessionVideoUrl);
});

window.FleetworthCapture = { buildFormData, getManifest: () => buildFormData()?.manifest || null, showResponse: renderBackendResponse };

renderAll();
loadDetector();
openCamera();
