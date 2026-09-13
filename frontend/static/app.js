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
const demoBadge = document.querySelector("#demoBadge");
const demoHelper = document.querySelector("#demoHelper");
const previewResultsButton = document.querySelector("#previewResultsButton");
const demoPreviewSlot = document.querySelector("#demoPreviewSlot");
const uploadPhotosButton = document.querySelector("#uploadPhotosButton");
const uploadVideoButton = document.querySelector("#uploadVideoButton");
const photoFileInput = document.querySelector("#photoFileInput");
const videoFileInput = document.querySelector("#videoFileInput");

const DEMO_MODE = document.body.dataset.demoMode === "true";
const DEMO_PRICED = {
  status: "priced",
  price_range: [16200, 21900],
  confidence: 0.72,
  breakdown: {
    make: "Ford",
    model: "F-150",
    year_estimate: "2018–2020",
    condition: "fair",
    tire_condition: "worn",
    damage: ["Rust on rear fender", "Cracked side mirror"],
    views_used: 5,
    base_price: 26400,
    visual_comps: { visual_base_price: 26400, neighbors_used: 12, top_similarity: 0.91 },
  },
};
const DEMO_NEEDS_INFO = {
  status: "needs_more_info",
  reason: "no clear view of tires",
  message: "Can't assess tire condition — please add a close-up photo of the tires.",
};
const DEMO_NOT_A_TRUCK = {
  status: "not_a_truck",
  message: "This doesn't look like a real photo of a truck.",
};

const captures = [];
let cameraStream;
let activeCameraId = "";
let isOpeningCamera = false;
let isCapturing = false;
let isRecording = false;
let isFinalizing = false;
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
let analysisGeneration = 0;
let captureGeneration = 0;
let clockTimer;
let toastTimer;
let analysisStageTimer;
let stableSince = 0;
let suggestionIndex = 0;
let forcedGuide = null;
let isSubmitting = false;
let discardingSession = false;
let cameraDenied = false;
let captureSource = "live"; // live | photos | video
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
  if (isFinalizing) setSubmitStatus("neutral", "Saving your recording…");
  else if (captureSource === "photos" && !enoughPhotos) setSubmitStatus("neutral", `Add ${MIN_PHOTOS - count} more exterior ${MIN_PHOTOS - count === 1 ? "photo" : "photos"} to estimate. You can select several at once.`);
  else if (ready) setSubmitStatus("ready", "Ready to estimate from the captured views.");
  else if (sessionVideo && !enoughPhotos) setSubmitStatus("alert", "Continue capturing for broader exterior coverage.");
  else if (isPaused) setSubmitStatus("alert", "Unpause to keep capturing.");
  else if (isRecording) setSubmitStatus("neutral", enoughPhotos ? "Stop when you have useful exterior coverage." : "Keep walking — coverage is still building.");
  else setSubmitStatus("neutral", "Start a walkaround to begin.");
}

function renderReview() {
  const count = captures.length;
  const enoughPhotos = count >= MIN_PHOTOS;
  const ready = captureSource === "photos"
    ? enoughPhotos
    : enoughPhotos && Boolean(sessionVideo?.size);
  const label = coverageLabel(count);
  captureCount.textContent = `${count} ${count === 1 ? "view" : "views"}`;
  coverageChip.textContent = label;
  reviewSummary.textContent = captureSource === "photos"
    ? `${label}. Uploaded exterior photos will be used to estimate value.`
    : `${label}. We’ll use the clearest exterior views to estimate value.`;
  renderSubmitStatus(count, enoughPhotos, ready);
  renderCoverageSummary();
  resetButton.disabled = !captures.length && !sessionVideo && !isRecording;
  submitButton.disabled = !ready || isRecording || isFinalizing || isSubmitting;
  continueButton.disabled = isSubmitting || isFinalizing || (captureSource === "photos" && count >= TARGET_PHOTOS);
  continueButton.hidden = false;
  continueButton.textContent = captureSource === "photos" ? "Add photos" : "Continue capturing";
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
  analysisGeneration += 1;
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
    const fallback = assessFallbackFrame(quality);
    return { ...fallback, ready: false, manualReady: fallback.ready, type: "warning", message: fallback.ready ? "Truck not detected — tap the shutter to capture manually" : fallback.message, confidence: 0, quality };
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

async function analyzeCurrentFrame(generation = analysisGeneration) {
  if (generation !== analysisGeneration) return;
  if (!cameraStream || preview.readyState < HTMLMediaElement.HAVE_CURRENT_DATA || isCapturing) {
    analysisTimer = window.setTimeout(() => analyzeCurrentFrame(generation), ANALYSIS_INTERVAL_MS);
    return;
  }
  try {
    const quality = measureFrameQuality();
    lastQuality = quality;
    if (detectorMode === "ready" && detector) {
      const predictions = await detector.detect(preview, 10, 0.35);
      if (generation !== analysisGeneration) return;
      currentAssessment = assessVehicleFrame(predictions, quality);
    }
    else if (detectorMode === "loading") currentAssessment = { ready: false, type: "loading", message: "Loading truck detector…", confidence: 0, quality };
    else {
      clearDetectionOverlay();
      currentAssessment = assessFallbackFrame(quality);
    }
    updateCaptureReadiness();
  } catch {
    if (generation !== analysisGeneration) return;
    detectorMode = "fallback";
    autoCapture.checked = false;
    autoCapture.disabled = true;
    const fallback = lastQuality ? assessFallbackFrame(lastQuality) : { ready: false };
    currentAssessment = { ...fallback, type: "warning", message: fallback.ready ? "Auto-snap unavailable — tap the shutter" : "Framing check unavailable — hold steady in good lighting", confidence: 0, quality: lastQuality };
    updateCaptureReadiness();
  }
  if (generation === analysisGeneration) analysisTimer = window.setTimeout(() => analyzeCurrentFrame(generation), ANALYSIS_INTERVAL_MS);
}

function updateCaptureReadiness() {
  if (isPaused) {
    captureButton.disabled = true;
    stableSince = 0;
    setFrameStatus("warning", "Paused — resume to capture");
    return;
  }
  const hasRoom = isRecording && captures.length < TARGET_PHOTOS && !isCapturing;
  const canSnap = hasRoom && currentAssessment.ready;
  captureButton.disabled = !(hasRoom && (currentAssessment.ready || currentAssessment.manualReady));
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
  if (isFinalizing) return;
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
    isFinalizing = false;
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
  captureSource = "live";
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
  startAnalysisLoop();
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
  if (isFinalizing) return;
  pauseClock();
  isRecording = false;
  isPaused = false;
  stopAnalysisLoop();
  captureButton.disabled = true;
  pauseButton.disabled = true;
  stopButton.disabled = true;
  isFinalizing = mediaRecorder?.state === "recording" || mediaRecorder?.state === "paused";
  if (!discardingSession) {
    setScreen("review");
    renderAll();
  }
  if (isFinalizing) mediaRecorder.stop();
}

function canvasToBlob(canvas) {
  return new Promise((resolve, reject) => canvas.toBlob((blob) => blob ? resolve(blob) : reject(new Error("Could not encode frame")), "image/jpeg", 0.9));
}

async function captureFrame({ automatic = false } = {}) {
  const ready = currentAssessment.ready || (!automatic && currentAssessment.manualReady);
  if (!isRecording || isPaused || !ready || captures.length >= TARGET_PHOTOS || isCapturing) return;
  const generation = captureGeneration;
  isCapturing = true;
  stopAnalysisLoop();
  const scale = Math.min(1, 1920 / preview.videoWidth);
  captureCanvas.width = Math.round(preview.videoWidth * scale);
  captureCanvas.height = Math.round(preview.videoHeight * scale);
  captureCanvas.getContext("2d").drawImage(preview, 0, 0, captureCanvas.width, captureCanvas.height);
  try {
    const blob = await canvasToBlob(captureCanvas);
    if (generation !== captureGeneration) return;
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
      limited: Boolean(currentAssessment.manualReady) || (currentAssessment.confidence > 0 && currentAssessment.confidence < 0.55),
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
    if (isRecording) startAnalysisLoop();
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
  if (isRecording || isFinalizing) return null;
  if (captures.length < MIN_PHOTOS) return null;
  if (captureSource !== "photos" && !sessionVideo?.size) return null;
  const manifest = buildManifest();
  const formData = new FormData();
  formData.append("manifest", new Blob([JSON.stringify(manifest)], { type: "application/json" }), "manifest.json");
  captures.forEach((capture, index) => formData.append("photos", capture.blob, `capture-${index + 1}.jpg`));
  if (sessionVideo) formData.append("video", sessionVideo, sessionVideoFilename());
  return { formData, manifest };
}

function apiOrigin() {
  const endpoint = document.body.dataset.predictEndpoint || "";
  try {
    return endpoint ? new URL(endpoint).origin : "";
  } catch {
    return "";
  }
}

function evidenceImageUrl(item) {
  if (!item) return "";
  if (item.url) return item.url;
  const path = item.path || "";
  if (!path) return "";
  if (/^https?:\/\//i.test(path)) return path;
  const origin = apiOrigin();
  return origin ? `${origin}${path.startsWith("/") ? path : `/${path}`}` : path;
}

function renderEvidenceGallery(response) {
  const items = Array.isArray(response.evidence) ? response.evidence : [];
  if (!items.length) return null;

  const section = createElement("section", "evidence-card evidence-gallery");
  section.append(createElement("h2", "", "What the model saw"));
  section.append(createElement(
    "p",
    "result-copy",
    response.source === "video_keyframes"
      ? "These are the key frames pulled from your video. Red boxes mark damage the model localized on that frame."
      : "These are the exterior views used for the appraisal. Red boxes mark damage the model localized on that frame.",
  ));

  const grid = createElement("div", "evidence-grid");
  items.forEach((item, index) => {
    const card = createElement("article", "evidence-frame");
    const url = evidenceImageUrl(item);
    if (url) {
      const img = document.createElement("img");
      img.src = url;
      img.alt = `Evidence frame ${index + 1}`;
      img.loading = "lazy";
      card.append(img);
    }

    const meta = createElement("div", "evidence-frame-meta");
    const identity = [item.make, item.model].filter(Boolean).join(" ");
    const title = identity || (item.is_truck === false ? "Not identified as a truck" : `Frame ${index + 1}`);
    meta.append(createElement("p", "evidence-frame-title", title));
    if (item.year_estimate) {
      meta.append(createElement("p", "evidence-frame-copy", `Year · ${item.year_estimate}`));
    }
    if (item.condition) {
      meta.append(createElement("p", "evidence-frame-copy", `Condition · ${titleCase(item.condition)}`));
    }
    const damage = Array.isArray(item.damage) ? item.damage.filter(Boolean) : [];
    if (damage.length) {
      const list = createElement("ul", "damage-list");
      damage.forEach((d) => list.append(createElement("li", "", d)));
      meta.append(list);
    } else {
      meta.append(createElement("p", "evidence-frame-copy", item.annotated ? "Damage boxed on frame." : "No localized damage on this frame."));
    }
    card.append(meta);
    grid.append(card);
  });

  section.append(grid);
  return section;
}

function formatMoney(value) {
  if (value === null || value === undefined || value === "") return null;
  const amount = Number(value);
  return Number.isFinite(amount) ? new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 }).format(amount) : null;
}

const IMPROVE_ESTIMATE_THRESHOLD = 0.8; // below this, the results screen offers a focused tire recapture

function confidenceBand(confidence) {
  if (confidence >= IMPROVE_ESTIMATE_THRESHOLD) return { label: "High confidence", tone: "success" };
  if (confidence >= 0.6) return { label: "Moderate confidence", tone: "warning" };
  return { label: "Limited confidence", tone: "danger" };
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

function appendDemoPreviewBar(container) {
  if (!DEMO_MODE) return;
  const bar = createElement("div", "demo-preview-bar");
  bar.append(createElement("span", "", "Designer preview — jump between result screens"));
  [
    ["Priced result", () => renderBackendResponse(DEMO_PRICED)],
    ["Needs more info", () => renderBackendResponse(DEMO_NEEDS_INFO)],
    ["Unable to assess", () => renderBackendResponse(DEMO_NOT_A_TRUCK)],
    ["Technical error", () => renderErrorResult("Your images were captured, but the appraisal request did not complete.")],
  ].forEach(([label, handler]) => {
    const button = createElement("button", "button", label);
    button.type = "button";
    button.addEventListener("click", handler);
    bar.append(button);
  });
  container.append(bar);
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
  appendDemoPreviewBar(container);
}

function beginFocusedRecapture(guide) {
  forcedGuide = guide;
  clearResult();
  if (captureSource === "photos" || captures.length >= TARGET_PHOTOS) {
    setScreen("review");
    renderAll();
    setSubmitStatus("neutral", captures.length >= TARGET_PHOTOS ? "Remove a less useful view to make room for the requested detail." : "Add the requested exterior photos, then estimate again.");
  } else startWalkaround();
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
  );
  hero.append(renderConfidenceMeter(confidence, band));
  hero.append(createElement("p", "result-copy", `Based on ${views} usable exterior view${views === 1 ? "" : "s"}.`));
  if (confidence < IMPROVE_ESTIMATE_THRESHOLD) {
    hero.append(renderImproveEstimateAction());
  }
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
  resultPanel.append(condition);

  const evidenceSection = renderEvidenceGallery(response);
  if (evidenceSection) resultPanel.append(evidenceSection);

  const comps = createElement("section", "comp-card");
  comps.append(createElement("h2", "", "Comparable market value"));
  const visualBase = formatMoney(visual.visual_base_price) || formatMoney(breakdown.base_price);
  const neighborCount = Number(visual.neighbors_used) || 0;
  comps.append(createElement("p", "result-copy", visualBase
    ? (neighborCount
      ? `${neighborCount} visually similar real listing${neighborCount === 1 ? "" : "s"} averaged ${visualBase} before condition adjustments.`
      : `Comparable listings averaged ${visualBase} before condition adjustments.`)
    : "No comparable listings could be matched with enough confidence to report a starting value."));
  resultPanel.append(comps);

  const actions = [
    ["Start another appraisal", "button button-primary", () => resetSession({ confirmUser: false, followupMessage: "" })],
    ["Review captured views", "button button-secondary", () => setScreen("review")],
  ];
  appendResultActions(resultPanel, actions);
  resultPanel.scrollIntoView({ behavior: "smooth", block: "start" });
}

function renderConfidenceMeter(confidence, band) {
  const wrap = createElement("div", "confidence-meter");
  const row = createElement("div", "confidence-row");
  row.append(
    createElement("span", "confidence-label", band.label),
    createElement("span", "confidence-value", `${Math.round(confidence * 100)}%`),
  );
  wrap.append(row);
  const track = createElement("div", "confidence-track");
  const fill = createElement("div", `confidence-fill confidence-fill--${band.tone}`);
  fill.style.width = `${Math.round(confidence * 100)}%`;
  track.append(fill);
  wrap.append(track);
  return wrap;
}

function renderImproveEstimateAction() {
  const wrap = createElement("div", "improve-estimate");
  const button = createElement("button", "button button-quiet", "Improve this estimate");
  button.type = "button";
  button.addEventListener("click", () => beginFocusedRecapture(GUIDE_STEPS[3]));
  wrap.append(button);
  const info = createElement("span", "info-icon");
  info.setAttribute("tabindex", "0");
  info.setAttribute("role", "button");
  info.setAttribute("aria-label", "Why am I seeing this?");
  info.append(
    createElement("span", "info-icon-glyph", "i"),
    createElement("span", "info-tooltip", `This appears because confidence is below ${Math.round(IMPROVE_ESTIMATE_THRESHOLD * 100)}%. A clear close-up of the tires is the single view most likely to sharpen the estimate.`),
  );
  wrap.append(info);
  return wrap;
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
    ["Start new capture", "button button-amber button-xl", () => {
      resetSession({ confirmUser: false, followupMessage: "" });
      forcedGuide = GUIDE_STEPS[0];
      startWalkaround();
    }],
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
  resultPanel.replaceChildren(
    createElement("p", "result-kicker", "Appraisal in progress"),
    createElement("h1", "", "Building your appraisal"),
    createElement("p", "light-lead", "Comparing this truck with similar market listings — this usually takes a few seconds."),
    createElement("div", "spinner"),
    createElement("p", "result-copy analysis-wait", ""),
  );
  const startedAt = Date.now();
  window.clearInterval(analysisStageTimer);
  analysisStageTimer = window.setInterval(() => {
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

async function postPredict(formData) {
  const endpoint = document.body.dataset.predictEndpoint;
  if (DEMO_MODE || !endpoint) {
    renderAnalysisState();
    await new Promise((resolve) => window.setTimeout(resolve, 3500));
    renderBackendResponse(DEMO_PRICED);
    return;
  }
  renderAnalysisState();
  const response = await fetch(endpoint, { method: "POST", body: formData });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = data.detail;
    const detailText = typeof detail === "string"
      ? detail
      : Array.isArray(detail)
        ? detail.map((item) => `${item.loc?.slice(1).join(".") || "Submission"}: ${item.msg || "Invalid value"}`).join(" ")
        : "";
    throw new Error(detailText || data.message || `The analysis service returned ${response.status}.`);
  }
  renderBackendResponse(data);
}

async function submitSession() {
  const payload = buildFormData();
  if (!payload) {
    setScreen("review");
    setSubmitStatus("alert", "Continue capturing until coverage is strong enough to estimate.");
    return;
  }
  window.dispatchEvent(new CustomEvent("fleetworth:capture-ready", { detail: { manifest: payload.manifest } }));
  isSubmitting = true;
  renderReview();
  try {
    await postPredict(payload.formData);
  } catch (error) {
    renderErrorResult(error.message || "Your evidence remains in this browser. Check the backend connection and try again.");
  } finally {
    isSubmitting = false;
    renderReview();
  }
}

function clearCaptures() {
  captureGeneration += 1;
  captures.forEach((capture) => URL.revokeObjectURL(capture.url));
  captures.splice(0, captures.length);
}

function clearSessionVideo() {
  if (sessionVideoUrl) URL.revokeObjectURL(sessionVideoUrl);
  sessionVideo = undefined;
  sessionVideoUrl = undefined;
  sessionDurationMs = 0;
  mediaChunks = [];
  sessionPlayback.pause();
  sessionPlayback.removeAttribute("src");
  sessionPlayback.load();
  sessionVideoReview.hidden = true;
}

async function handlePhotoUpload(fileList) {
  const files = [...fileList].filter((file) => file.type.startsWith("image/") || /\.(jpe?g|png|webp)$/i.test(file.name));
  if (!files.length) {
    actionMessage.textContent = "Choose one or more exterior truck photos.";
    return;
  }
  if (files.length > TARGET_PHOTOS) {
    actionMessage.textContent = `Using the first ${TARGET_PHOTOS} photos for this appraisal.`;
  }
  if (captureSource !== "photos") {
    clearCaptures();
    clearSessionVideo();
  }
  captureSource = "photos";
  const selected = files.slice(0, TARGET_PHOTOS - captures.length);
  for (const file of selected) {
    const blob = file.type ? file : new Blob([await file.arrayBuffer()], { type: "image/jpeg" });
    captures.push({
      id: crypto.randomUUID?.() || `${Date.now()}-${captures.length}`,
      blob,
      url: URL.createObjectURL(blob),
      capturedAt: new Date(file.lastModified || Date.now()).toISOString(),
      width: 0,
      height: 0,
      detectorConfidence: 0,
      blurry: false,
      limited: false,
      helpful: /tire/i.test(file.name),
    });
  }
  clearResult();
  setSessionState("Photos ready", "complete");
  setScreen("review");
  renderAll();
}

async function handleVideoUpload(fileList) {
  const file = fileList?.[0];
  if (!file) return;
  const looksVideo = file.type.startsWith("video/") || /\.(mp4|webm|mov)$/i.test(file.name);
  if (!looksVideo) {
    actionMessage.textContent = "Choose an MP4, WebM, or MOV walkaround video.";
    return;
  }
  clearCaptures();
  clearSessionVideo();
  captureSource = "video";
  clearResult();
  isSubmitting = true;
  setSessionState("Processing video", "complete");
  setScreen("result");
  renderAnalysisState();
  const analysisLead = resultPanel.querySelector(".light-lead");
  if (analysisLead) {
    analysisLead.textContent = "We’re pulling clear key frames from your video, then comparing them with similar market listings.";
  }
  try {
    const formData = new FormData();
    formData.append("video", file, file.name || "walkaround.mp4");
    await postPredict(formData);
  } catch (error) {
    renderErrorResult(error.message || "We could not process that video. Try another file or use live capture.");
  } finally {
    isSubmitting = false;
  }
}

function resetSession({ confirmUser = true, followupMessage = "Session discarded." } = {}) {
  if (confirmUser && !window.confirm("Start over and discard this walkaround?")) return;
  clearCaptures();
  if (isRecording || isFinalizing) {
    discardingSession = true;
    stopSession();
  }
  clearSessionVideo();
  suggestionIndex = 0;
  forcedGuide = null;
  captureSource = "live";
  continueButton.hidden = false;
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
previewResultsButton?.addEventListener("click", () => renderBackendResponse(DEMO_PRICED));
uploadPhotosButton?.addEventListener("click", () => photoFileInput?.click());
uploadVideoButton?.addEventListener("click", () => videoFileInput?.click());
photoFileInput?.addEventListener("change", async () => {
  const files = photoFileInput.files;
  if (files?.length) await handlePhotoUpload(files);
  photoFileInput.value = "";
});
videoFileInput?.addEventListener("change", async () => {
  const files = videoFileInput.files;
  if (files?.length) await handleVideoUpload(files);
  videoFileInput.value = "";
});
pauseButton.addEventListener("click", togglePause);
stopButton.addEventListener("click", stopSession);
captureButton.addEventListener("click", () => captureFrame());
continueButton.addEventListener("click", () => {
  if (captureSource === "photos") photoFileInput?.click();
  else startWalkaround();
});
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

if (DEMO_MODE) {
  if (demoBadge) demoBadge.hidden = false;
  if (demoHelper) demoHelper.hidden = false;
  if (demoPreviewSlot) demoPreviewSlot.hidden = false;
  if (previewResultsButton) previewResultsButton.hidden = false;
}

renderAll();
loadDetector();
openCamera();
