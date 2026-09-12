const GUIDE_STEPS = [
  { title: "Start wide", instruction: "Frame the whole truck from a few steps back.", tip: "Keep all body edges inside the guide." },
  { title: "Walk to the front", instruction: "Show the grille, headlights, bumper, and hood.", tip: "Pause briefly when the front is clear." },
  { title: "Sweep a side", instruction: "Walk along either side so the cab, bed, and wheels are visible.", tip: "Hold the camera parallel to reveal panel damage." },
  { title: "Circle the rear", instruction: "Show the tailgate, rear bumper, and bed corners.", tip: "Avoid cutting off the bumper or taillights." },
  { title: "Get the tires", instruction: "Move close enough to show tread, sidewall, and wheel condition.", tip: "A clear tire view is especially valuable." },
  { title: "Cover the other side", instruction: "Collect any useful exterior view not yet shown.", tip: "The guide is flexible—good coverage matters more than exact angles." },
];

const MIN_PHOTOS = 3;
const TARGET_PHOTOS = 7;
const ANALYSIS_INTERVAL_MS = 600;
const AUTO_CAPTURE_DELAY_MS = 10000;
const GUIDE_STEP_DURATION_MS = 12000;
const MIN_VEHICLE_SCORE = 0.42;
const VEHICLE_CLASSES = new Set(["truck", "car", "bus"]);

const preview = document.querySelector("#cameraPreview");
const detectionOverlay = document.querySelector("#detectionOverlay");
const captureCanvas = document.querySelector("#captureCanvas");
const cameraMessage = document.querySelector("#cameraMessage");
const cameraSelect = document.querySelector("#cameraSelect");
const startButton = document.querySelector("#startButton");
const stopButton = document.querySelector("#stopButton");
const captureButton = document.querySelector("#captureButton");
const autoCapture = document.querySelector("#autoCapture");
const resetButton = document.querySelector("#resetButton");
const submitButton = document.querySelector("#submitButton");
const actionMessage = document.querySelector("#actionMessage");
const frameStatus = document.querySelector("#frameStatus");
const frameStatusText = document.querySelector("#frameStatusText");
const captureFlash = document.querySelector("#captureFlash");
const guideList = document.querySelector("#guideList");
const stepLabel = document.querySelector("#stepLabel");
const captureTitle = document.querySelector("#captureTitle");
const captureInstruction = document.querySelector("#captureInstruction");
const shotTip = document.querySelector("#shotTip");
const recordingTime = document.querySelector("#recordingTime");
const recordingBadge = document.querySelector("#recordingBadge");
const sessionState = document.querySelector("#sessionState");
const captureGrid = document.querySelector("#captureGrid");
const reviewSummary = document.querySelector("#reviewSummary");
const submitHeading = document.querySelector("#submitHeading");
const submitDetail = document.querySelector("#submitDetail");
const resultPanel = document.querySelector("#resultPanel");
const sessionVideoReview = document.querySelector("#sessionVideoReview");
const sessionPlayback = document.querySelector("#sessionPlayback");
const sessionVideoMeta = document.querySelector("#sessionVideoMeta");
const interiorOption = document.querySelector("#interiorOption");
const interiorIncluded = document.querySelector("#interiorIncluded");
const interiorChoiceLabel = document.querySelector("#interiorChoiceLabel");
const interiorUpload = document.querySelector("#interiorUpload");
const interiorPhotoInput = document.querySelector("#interiorPhotoInput");
const interiorPreview = document.querySelector("#interiorPreview");
const interiorPreviewImage = document.querySelector("#interiorPreviewImage");
const interiorFileName = document.querySelector("#interiorFileName");
const interiorMessage = document.querySelector("#interiorMessage");
const removeInteriorButton = document.querySelector("#removeInteriorButton");

const captures = [];
let cameraStream;
let activeCameraId = "";
let isOpeningCamera = false;
let isCapturing = false;
let isRecording = false;
let detector;
let detectorMode = "loading";
let mediaRecorder;
let mediaChunks = [];
let sessionVideo;
let sessionVideoUrl;
let sessionDurationMs = 0;
let recordingStartedAt;
let analysisTimer;
let clockTimer;
let stableSince = 0;
let suggestionIndex = 0;
let interiorPhoto;
let isSubmitting = false;
let currentAssessment = { ready: false, type: "loading", message: "Loading framing assistant…", confidence: 0 };

function guideIndex() {
  return suggestionIndex;
}

function currentGuide() {
  return GUIDE_STEPS[guideIndex()];
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

function setSessionState(label, state = "ready") {
  sessionState.className = `session-state is-${state}`;
  sessionState.innerHTML = `<span class="state-dot"></span>${label}`;
}

function formatDuration(milliseconds) {
  const seconds = Math.floor(milliseconds / 1000);
  return `${String(Math.floor(seconds / 60)).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`;
}

function renderGuide() {
  guideList.replaceChildren();
  GUIDE_STEPS.forEach((step, index) => {
    const item = document.createElement("li");
    item.className = "guide-item";
    item.classList.toggle("is-current", index === guideIndex() && isRecording);
    item.innerHTML = `<span>${index + 1}</span><div><strong>${step.title}</strong><small>${step.instruction}</small></div>`;
    guideList.append(item);
  });
}

function renderCurrentGuide() {
  const guide = currentGuide();
  stepLabel.textContent = isRecording ? `Optional suggestion ${guideIndex() + 1} of ${GUIDE_STEPS.length} · ${captures.length} ${captures.length === 1 ? "photo" : "photos"}` : "Camera check";
  captureTitle.textContent = isRecording ? guide.title : "Start a live walkaround";
  captureInstruction.textContent = isRecording
    ? guide.instruction
    : "When you are beside the truck, start the session. We will guide you around it and snap clear frames.";
  shotTip.textContent = guide.tip;
  renderGuide();
}

function renderCaptures() {
  captureGrid.replaceChildren();
  captures.forEach((capture, index) => {
    const card = document.createElement("article");
    card.className = "capture-card";
    const image = new Image();
    image.src = capture.url;
    image.alt = `Snapped truck photo ${index + 1}`;
    const label = document.createElement("span");
    label.className = "capture-card-label";
    label.textContent = `Photo ${index + 1}`;
    const remove = document.createElement("button");
    remove.className = "remove-button";
    remove.type = "button";
    remove.textContent = "Remove";
    remove.addEventListener("click", () => removeCapture(capture.id));
    card.append(image, label, remove);
    captureGrid.append(card);
  });

  for (let index = captures.length; index < TARGET_PHOTOS; index += 1) {
    const card = document.createElement("article");
    card.className = "capture-card is-empty";
    const number = createElement("span", "capture-slot-number", String(index + 1));
    const label = createElement("strong", "capture-slot-label", index < MIN_PHOTOS ? "Required" : "Optional");
    const detail = createElement("small", "capture-slot-detail", index < MIN_PHOTOS ? "minimum" : "coverage");
    card.append(number, label, detail);
    captureGrid.append(card);
  }
}

function renderReview() {
  const count = captures.length;
  const enoughPhotos = count >= MIN_PHOTOS;
  const needsInteriorPhoto = interiorIncluded.checked && !interiorPhoto;
  const ready = enoughPhotos && sessionVideo && !needsInteriorPhoto;
  reviewSummary.textContent = isRecording
    ? `${count} snapped ${count === 1 ? "photo" : "photos"} so far. Keep walking for broad coverage.`
    : count
      ? `${count} ${count === 1 ? "photo" : "photos"} captured${sessionVideo ? " with a recorded session" : ""}${enoughPhotos ? " · minimum met" : ` · ${MIN_PHOTOS - count} more needed`}.`
      : "Start a session to collect 3–7 photos.";
  submitHeading.textContent = ready ? "Capture evidence is ready" : needsInteriorPhoto ? "Choose a cabin photo to continue" : sessionVideo ? "Restart to collect enough photos" : "Stop the session to finalize video";
  submitDetail.textContent = ready && !interiorIncluded.checked
    ? "Exterior photos feed visual extraction. Your estimate may be wider without a cabin photo."
    : "Photos feed visual extraction. The session video is kept separately as a live-capture record.";
  resetButton.disabled = !captures.length && !sessionVideo && !isRecording;
  submitButton.disabled = !ready || isRecording || isSubmitting;
  sessionVideoReview.hidden = !sessionVideoUrl;
  interiorOption.hidden = !sessionVideo;
  if (sessionVideoUrl) {
    if (sessionPlayback.src !== sessionVideoUrl) sessionPlayback.src = sessionVideoUrl;
    sessionVideoMeta.textContent = `${formatDuration(sessionDurationMs)} · ${(sessionVideo.size / 1024 / 1024).toFixed(1)} MB · kept separate from pricing analysis.`;
  }
  renderCaptures();
}

function renderAll() {
  renderCurrentGuide();
  renderReview();
}

function clearResult() {
  resultPanel.className = "result-panel is-hidden";
  resultPanel.replaceChildren();
}

function createElement(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
}

function showResult(type, title, message, content) {
  const labels = { priced: "Estimate ready", "needs-info": "More evidence needed", loading: "Analyzing evidence", ready: "Handoff ready", error: "Something went wrong" };
  resultPanel.className = `result-panel is-${type}`;
  resultPanel.setAttribute("aria-busy", String(type === "loading"));
  resultPanel.replaceChildren(
    createElement("p", "eyebrow", labels[type] || "Fleetworth"),
    createElement("h2", "", title),
    createElement("p", "result-intro", message),
  );
  if (content) resultPanel.append(content);
  resultPanel.scrollIntoView({ behavior: "smooth", block: "start" });
}

function getActiveCameraId() {
  return cameraStream?.getVideoTracks()[0]?.getSettings().deviceId || "";
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
  if (quality.isDark) return { ready: false, type: "warning", message: "Too dark — add light or change angle", confidence: 0 };
  if (quality.isBright) return { ready: false, type: "warning", message: "Too much glare — change angle", confidence: 0 };
  if (quality.isBlurry) return { ready: false, type: "warning", message: "Hold steady — image is blurry", confidence: 0 };
  return { ready: true, type: "ready", message: "Frame is clear — manually confirm the truck fits the guide", confidence: 0 };
}

function assessVehicleFrame(predictions, quality) {
  const vehicle = predictions.filter((item) => VEHICLE_CLASSES.has(item.class) && item.score >= MIN_VEHICLE_SCORE).sort((a, b) => b.score - a.score)[0];
  if (!vehicle) {
    clearDetectionOverlay();
    return { ready: false, type: "warning", message: "No truck detected — point the camera at the vehicle", confidence: 0 };
  }
  drawDetection(vehicle);
  const [x, y, width, height] = vehicle.bbox;
  const frameWidth = preview.videoWidth;
  const frameHeight = preview.videoHeight;
  const coverage = (width * height) / (frameWidth * frameHeight);
  const clipped = x < frameWidth * 0.018 || y < frameHeight * 0.018 || x + width > frameWidth * 0.982 || y + height > frameHeight * 0.982;
  if (clipped || coverage > 0.84) return { ready: false, type: "warning", message: "Step back — part of the truck is cut off", confidence: vehicle.score };
  if (coverage < 0.13) return { ready: false, type: "warning", message: "Move closer — the truck is too small", confidence: vehicle.score };
  if (quality.isDark) return { ready: false, type: "warning", message: "Truck found, but the frame is too dark", confidence: vehicle.score };
  if (quality.isBright) return { ready: false, type: "warning", message: "Truck found, but glare is hiding details", confidence: vehicle.score };
  if (quality.isBlurry) return { ready: false, type: "warning", message: "Truck found — hold the camera steady", confidence: vehicle.score };
  return { ready: true, type: "ready", message: `Truck detected · ${Math.round(vehicle.score * 100)}% confidence`, confidence: vehicle.score };
}

async function analyzeCurrentFrame() {
  if (!cameraStream || preview.readyState < HTMLMediaElement.HAVE_CURRENT_DATA || isCapturing) {
    analysisTimer = window.setTimeout(analyzeCurrentFrame, ANALYSIS_INTERVAL_MS);
    return;
  }
  try {
    const quality = measureFrameQuality();
    if (detectorMode === "ready" && detector) currentAssessment = assessVehicleFrame(await detector.detect(preview, 10, 0.35), quality);
    else if (detectorMode === "loading") currentAssessment = { ready: false, type: "loading", message: "Loading truck detector…", confidence: 0 };
    else {
      clearDetectionOverlay();
      currentAssessment = assessFallbackFrame(quality);
    }
    updateCaptureReadiness();
  } catch {
    currentAssessment = { ready: false, type: "error", message: "Framing check paused — try again", confidence: 0 };
    updateCaptureReadiness();
  }
  analysisTimer = window.setTimeout(analyzeCurrentFrame, ANALYSIS_INTERVAL_MS);
}

function updateCaptureReadiness() {
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
  else if (autoCapture.checked) setFrameStatus("ready", `Hold steady · auto-snapping in ${Math.max(1, Math.ceil((AUTO_CAPTURE_DELAY_MS - stableFor) / 1000))}`);
}

function chooseRecorderOptions() {
  const types = ["video/webm;codecs=vp9,opus", "video/webm;codecs=vp8,opus", "video/webm", "video/mp4"];
  const mimeType = types.find((type) => MediaRecorder.isTypeSupported(type));
  return mimeType ? { mimeType } : undefined;
}

function sessionVideoFilename() {
  return sessionVideo?.type.includes("mp4") ? "session.mp4" : "session.webm";
}

function interiorPhotoFilename() {
  const extension = { "image/png": "png", "image/webp": "webp" }[interiorPhoto?.blob.type] || "jpg";
  return `interior.${extension}`;
}

function startClock() {
  window.clearInterval(clockTimer);
  recordingStartedAt = Date.now();
  recordingTime.textContent = "00:00";
  clockTimer = window.setInterval(() => {
    const elapsed = Date.now() - recordingStartedAt;
    recordingTime.textContent = formatDuration(elapsed);
    const nextSuggestion = Math.min(Math.floor(elapsed / GUIDE_STEP_DURATION_MS), GUIDE_STEPS.length - 1);
    if (nextSuggestion !== suggestionIndex) {
      suggestionIndex = nextSuggestion;
      renderCurrentGuide();
    }
  }, 1000);
}

function stopClock() {
  window.clearInterval(clockTimer);
  clockTimer = undefined;
}

async function openCamera() {
  if (!navigator.mediaDevices?.getUserMedia) {
    showCameraMessage("This browser does not support webcam access.");
    setFrameStatus("error", "Camera API unavailable");
    return;
  }
  if (isOpeningCamera) return;
  isOpeningCamera = true;
  startButton.disabled = true;
  cameraSelect.disabled = true;
  showCameraMessage("Requesting camera access…");
  try {
    const requestedId = cameraSelect.value;
    const video = requestedId ? { deviceId: { exact: requestedId }, width: { ideal: 1920 }, height: { ideal: 1080 } } : { facingMode: { ideal: "environment" }, width: { ideal: 1920 }, height: { ideal: 1080 } };
    const nextStream = await navigator.mediaDevices.getUserMedia({ video, audio: false });
    cameraStream?.getTracks().forEach((track) => track.stop());
    cameraStream = nextStream;
    preview.srcObject = nextStream;
    await preview.play();
    activeCameraId = await refreshCameraList(requestedId || getActiveCameraId()).catch(() => requestedId || getActiveCameraId());
    hideCameraMessage();
    startButton.disabled = false;
    setSessionState("Camera ready", "ready");
    startAnalysisLoop();
  } catch (error) {
    const denied = error.name === "NotAllowedError" || error.name === "SecurityError";
    showCameraMessage(denied ? "Camera permission was not granted. Allow access, then try again." : "We could not open that camera.");
    setFrameStatus("error", denied ? "Camera permission needed" : "Camera unavailable");
    startButton.disabled = false;
  } finally {
    isOpeningCamera = false;
    cameraSelect.disabled = cameraSelect.options.length <= 1 || isRecording;
  }
}

function startSession() {
  if (!cameraStream) {
    openCamera();
    return;
  }
  if (!window.MediaRecorder) {
    actionMessage.textContent = "This browser cannot record a session video. Try a current Safari or Chrome build.";
    return;
  }
  clearResult();
  clearInteriorPhoto();
  interiorIncluded.checked = false;
  renderInteriorChoice();
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
    const type = mediaRecorder.mimeType || "video/webm";
    sessionDurationMs = Date.now() - recordingStartedAt;
    sessionVideo = new Blob(mediaChunks, { type });
    sessionVideoUrl = URL.createObjectURL(sessionVideo);
    isRecording = false;
    stopClock();
    recordingBadge.classList.add("is-hidden");
    startButton.disabled = true;
    stopButton.disabled = true;
    cameraSelect.disabled = cameraSelect.options.length <= 1;
    setSessionState("Session recorded", "complete");
    actionMessage.textContent = captures.length >= MIN_PHOTOS ? "Session video saved locally. Choose whether to add a cabin photo, then submit when ready." : `Session saved without enough photos. Discard it and restart to collect ${MIN_PHOTOS} clear photos in one continuous session.`;
    renderAll();
  }, { once: true });
  mediaRecorder.start(1000);
  isRecording = true;
  suggestionIndex = 0;
  startButton.disabled = true;
  stopButton.disabled = false;
  cameraSelect.disabled = true;
  recordingBadge.classList.remove("is-hidden");
  setSessionState("Recording live session", "recording");
  actionMessage.textContent = "Walk around the truck. Clear frames will snap automatically.";
  startClock();
  renderAll();
}

function stopSession() {
  if (mediaRecorder?.state === "recording") mediaRecorder.stop();
}

function canvasToBlob(canvas) {
  return new Promise((resolve, reject) => canvas.toBlob((blob) => blob ? resolve(blob) : reject(new Error("Could not encode frame")), "image/jpeg", 0.9));
}

async function captureFrame({ automatic = false } = {}) {
  if (!isRecording || !currentAssessment.ready || captures.length >= TARGET_PHOTOS || isCapturing) return;
  isCapturing = true;
  stopAnalysisLoop();
  const scale = Math.min(1, 1920 / preview.videoWidth);
  captureCanvas.width = Math.round(preview.videoWidth * scale);
  captureCanvas.height = Math.round(preview.videoHeight * scale);
  captureCanvas.getContext("2d").drawImage(preview, 0, 0, captureCanvas.width, captureCanvas.height);
  try {
    const blob = await canvasToBlob(captureCanvas);
    captures.push({ id: crypto.randomUUID?.() || `${Date.now()}-${captures.length}`, blob, url: URL.createObjectURL(blob), capturedAt: new Date().toISOString(), width: captureCanvas.width, height: captureCanvas.height, detectorConfidence: currentAssessment.confidence });
    captureFlash.classList.remove("is-active");
    void captureFlash.offsetWidth;
    captureFlash.classList.add("is-active");
    actionMessage.textContent = captures.length === TARGET_PHOTOS
      ? `Photo ${captures.length} snapped${automatic ? " automatically" : ""}. Maximum reached—finish the walkaround, then stop the session.`
      : `Photo ${captures.length} snapped${automatic ? " automatically" : ""}. Move to another useful view.`;
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
  actionMessage.textContent = isRecording ? "Photo removed. Continue the live session for another frame." : "Photo removed. If fewer than three remain, discard this session and record a new walkaround.";
  renderAll();
}

function clearInteriorPhoto() {
  if (interiorPhoto?.url) URL.revokeObjectURL(interiorPhoto.url);
  interiorPhoto = undefined;
  interiorPhotoInput.value = "";
  interiorPreviewImage.removeAttribute("src");
  interiorPreview.hidden = true;
  interiorFileName.textContent = "";
  interiorMessage.textContent = "";
}

function renderInteriorChoice() {
  const included = interiorIncluded.checked;
  interiorChoiceLabel.textContent = included ? "Yes, add a cabin photo" : "No, continue without one";
  interiorUpload.hidden = !included;
  if (!included) clearInteriorPhoto();
  renderReview();
}

function readImageDimensions(url) {
  return new Promise((resolve, reject) => {
    const image = new Image();
    image.addEventListener("load", () => resolve({ width: image.naturalWidth, height: image.naturalHeight }), { once: true });
    image.addEventListener("error", reject, { once: true });
    image.src = url;
  });
}

async function selectInteriorPhoto() {
  const [file] = interiorPhotoInput.files;
  if (!file) return;
  if (!new Set(["image/jpeg", "image/png", "image/webp"]).has(file.type)) {
    clearInteriorPhoto();
    interiorMessage.textContent = "Choose a JPEG, PNG, or WebP image.";
    renderReview();
    return;
  }
  const url = URL.createObjectURL(file);
  try {
    const dimensions = await readImageDimensions(url);
    clearInteriorPhoto();
    interiorPhoto = { blob: file, url, filename: file.name || "interior.jpg", capturedAt: new Date().toISOString(), ...dimensions };
    interiorPreviewImage.src = url;
    interiorFileName.textContent = interiorPhoto.filename;
    interiorPreview.hidden = false;
    interiorMessage.textContent = "Cabin photo ready to include.";
  } catch {
    URL.revokeObjectURL(url);
    clearInteriorPhoto();
    interiorMessage.textContent = "That image could not be read. Choose another file.";
  }
  renderReview();
}

function buildManifest() {
  const hasInterior = interiorIncluded.checked && Boolean(interiorPhoto);
  return {
    schema_version: "3.0",
    captured_at: new Date().toISOString(),
    photo_count: captures.length,
    total_photo_count: captures.length + (hasInterior ? 1 : 0),
    interior_included: hasInterior,
    interior_photo: hasInterior ? { filename: interiorPhotoFilename(), original_filename: interiorPhoto.filename, type: interiorPhoto.blob.type, size: interiorPhoto.blob.size, captured_at: interiorPhoto.capturedAt, width: interiorPhoto.width, height: interiorPhoto.height } : null,
    session_video: { filename: sessionVideoFilename(), type: sessionVideo?.type, size: sessionVideo?.size, duration_ms: sessionDurationMs },
    photos: captures.map((capture, index) => ({ id: capture.id, filename: `capture-${index + 1}.jpg`, captured_at: capture.capturedAt, width: capture.width, height: capture.height, detector_confidence: capture.detectorConfidence })),
  };
}

function buildFormData() {
  if (!sessionVideo || captures.length < MIN_PHOTOS || (interiorIncluded.checked && !interiorPhoto)) return null;
  const manifest = buildManifest();
  const formData = new FormData();
  formData.append("manifest", new Blob([JSON.stringify(manifest)], { type: "application/json" }), "manifest.json");
  captures.forEach((capture, index) => formData.append("photos", capture.blob, `capture-${index + 1}.jpg`));
  formData.append("interior_included", String(manifest.interior_included));
  if (manifest.interior_included) formData.append("interior_photo", interiorPhoto.blob, interiorPhotoFilename());
  formData.append("session_video", sessionVideo, sessionVideoFilename());
  return { formData, manifest };
}

function formatMoney(value) {
  if (value === null || value === undefined || value === "") return "—";
  const amount = Number(value);
  return Number.isFinite(amount) ? new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 }).format(amount) : "—";
}

function appendValuationStep(container, label, value, isFinal = false) {
  const step = createElement("div", `valuation-step${isFinal ? " is-final" : ""}`);
  step.append(createElement("small", "", label), createElement("strong", "", value));
  container.append(step);
}

function renderPricedResult(response) {
  const [low, high] = Array.isArray(response.price_range) ? response.price_range : [];
  const confidence = Math.max(0, Math.min(1, Number(response.confidence) || 0));
  const breakdown = response.breakdown || {};
  const content = createElement("div", "priced-content");
  content.append(createElement("div", "price-range", `${formatMoney(low)} – ${formatMoney(high)}`));

  const confidenceRow = createElement("div", "confidence-row");
  confidenceRow.append(createElement("span", "", `${Math.round(confidence * 100)}% confidence`));
  const meter = createElement("span", "confidence-meter");
  const meterFill = createElement("span");
  meterFill.style.width = `${Math.round(confidence * 100)}%`;
  meter.append(meterFill);
  confidenceRow.append(meter);
  content.append(confidenceRow);

  const notes = Array.isArray(response.notes) ? response.notes.filter((note) => typeof note === "string" && note.trim()) : [];
  if (notes.length) {
    const noteList = createElement("div", "result-notes");
    notes.forEach((note) => noteList.append(createElement("p", "result-note", note)));
    content.append(noteList);
  }

  const flow = createElement("div", "valuation-flow");
  appendValuationStep(flow, "Comparable baseline", formatMoney(breakdown.base_price));
  flow.append(createElement("span", "flow-arrow", "→"));
  appendValuationStep(flow, "Visual adjustments", `${breakdown.condition || "Unknown"} condition · ${breakdown.tire_condition || "Unknown"} tires`);
  flow.append(createElement("span", "flow-arrow", "→"));
  appendValuationStep(flow, "Supported range", `${formatMoney(low)} – ${formatMoney(high)}`, true);
  content.append(flow, createElement("h3", "breakdown-title", "What the photos showed"));

  const details = [
    ["Vehicle", [breakdown.year_estimate, breakdown.make, breakdown.model, breakdown.trim].filter(Boolean).join(" ") || "Not reported"],
    ["Condition", breakdown.condition || "Not reported"],
    ["Tires", breakdown.tire_condition || "Not reported"],
    ["Views analyzed", breakdown.views_used ?? "Not reported"],
    ["Cabin photo", breakdown.interior_included === true ? "Included" : breakdown.interior_included === false ? "Not included" : "Not reported"],
  ];
  const detailList = createElement("dl", "breakdown-grid");
  details.forEach(([label, value]) => {
    const item = createElement("div", "breakdown-item");
    item.append(createElement("dt", "", label), createElement("dd", "", String(value)));
    detailList.append(item);
  });
  content.append(detailList);

  const damage = Array.isArray(breakdown.damage)
    ? breakdown.damage.map((item) => typeof item === "string" ? item : item?.description).filter(Boolean)
    : [];
  content.append(createElement("p", "damage-summary", damage.length ? `Visible damage considered: ${damage.join("; ")}.` : "No visible damage was reported in the analyzed views."));
  showResult("priced", "Estimated truck value", "A comps-backed range adjusted using facts extracted from the submitted photos.", content);
}

function renderNeedsMoreInfo(response) {
  const content = createElement("div");
  if (response.reason) content.append(createElement("p", "result-reason", `Gap identified: ${response.reason}`));
  const actions = createElement("div", "result-actions");
  const retryButton = createElement("button", "button button-primary", "Start a focused recapture");
  retryButton.type = "button";
  retryButton.addEventListener("click", () => {
    const guidance = response.message || response.reason || "Capture the requested missing view.";
    resetSession({ confirmUser: false, followupMessage: `${guidance} Start a new live session when ready.` });
    document.querySelector(".capture-panel").scrollIntoView({ behavior: "smooth", block: "start" });
  });
  actions.append(retryButton);
  content.append(actions);
  showResult("needs-info", "We need one more useful view", response.message || "The current evidence cannot support a confident estimate.", content);
}

function renderErrorResult(message) {
  const actions = createElement("div", "result-actions");
  const retryButton = createElement("button", "button button-secondary", "Try submitting again");
  retryButton.type = "button";
  retryButton.addEventListener("click", submitSession);
  actions.append(retryButton);
  showResult("error", "We could not complete the estimate", message || "Please try again.", actions);
}

function renderBackendResponse(response = {}) {
  if (response.status === "priced") renderPricedResult(response);
  else if (response.status === "needs_more_info") renderNeedsMoreInfo(response);
  else renderErrorResult(response.message);
}

async function submitSession() {
  const payload = buildFormData();
  if (!payload) {
    actionMessage.textContent = "Record a session and collect at least three clear photos before submitting.";
    return;
  }
  const endpoint = document.body.dataset.predictEndpoint;
  window.dispatchEvent(new CustomEvent("fleetworth:capture-ready", { detail: { manifest: payload.manifest } }));
  if (!endpoint) {
    const cabinCopy = payload.manifest.interior_included ? " plus one cabin photo" : " without a cabin photo";
    showResult("ready", "Evidence package ready", `${captures.length} exterior photos${cabinCopy} and the session video are ready for the FastAPI /predict endpoint.`);
    return;
  }
  isSubmitting = true;
  renderReview();
  showResult("loading", "Analyzing truck evidence", "Extracting visible truck facts and comparing them with market data…");
  try {
    const response = await fetch(endpoint, { method: "POST", body: payload.formData });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.message || `The analysis service returned ${response.status}.`);
    renderBackendResponse(data);
  } catch (error) {
    renderErrorResult(error.message || "Your evidence remains in this browser. Check the backend connection and try again.");
  } finally {
    isSubmitting = false;
    renderReview();
  }
}

function resetSession({ confirmUser = true, followupMessage = "Session discarded." } = {}) {
  if (confirmUser && !window.confirm("Discard the recorded session and all snapped photos?")) return;
  captures.forEach((capture) => URL.revokeObjectURL(capture.url));
  captures.splice(0, captures.length);
  clearInteriorPhoto();
  interiorIncluded.checked = false;
  renderInteriorChoice();
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
  recordingTime.textContent = "00:00";
  startButton.disabled = !cameraStream;
  clearResult();
  setSessionState(cameraStream ? "Camera ready" : "Ready to capture", "ready");
  actionMessage.textContent = followupMessage;
  renderAll();
}

async function loadDetector() {
  if (!window.cocoSsd) {
    detectorMode = "fallback";
    autoCapture.checked = false;
    autoCapture.disabled = true;
    setFrameStatus("warning", "Detector unavailable — manual snapping enabled");
    return;
  }
  try {
    detector = await window.cocoSsd.load({ base: "lite_mobilenet_v2" });
    detectorMode = "ready";
  } catch {
    detectorMode = "fallback";
    autoCapture.checked = false;
    autoCapture.disabled = true;
    setFrameStatus("warning", "Detector unavailable — manual snapping enabled");
  }
}

cameraSelect.addEventListener("change", () => { if (!isRecording && cameraSelect.value !== activeCameraId) openCamera(); });
startButton.addEventListener("click", startSession);
stopButton.addEventListener("click", stopSession);
captureButton.addEventListener("click", () => captureFrame());
resetButton.addEventListener("click", () => resetSession());
submitButton.addEventListener("click", submitSession);
autoCapture.addEventListener("change", () => { stableSince = 0; });
interiorIncluded.addEventListener("change", renderInteriorChoice);
interiorPhotoInput.addEventListener("change", selectInteriorPhoto);
removeInteriorButton.addEventListener("click", () => {
  clearInteriorPhoto();
  interiorMessage.textContent = "Choose a cabin photo, or turn the option off to continue without one.";
  renderReview();
});
navigator.mediaDevices?.addEventListener("devicechange", () => refreshCameraList().catch(() => {}));
window.addEventListener("beforeunload", () => {
  if (mediaRecorder?.state === "recording") mediaRecorder.stop();
  stopAnalysisLoop();
  stopClock();
  cameraStream?.getTracks().forEach((track) => track.stop());
  captures.forEach((capture) => URL.revokeObjectURL(capture.url));
  if (sessionVideoUrl) URL.revokeObjectURL(sessionVideoUrl);
  if (interiorPhoto?.url) URL.revokeObjectURL(interiorPhoto.url);
});

window.FleetworthCapture = { buildFormData, getManifest: () => buildFormData()?.manifest || null, showResponse: renderBackendResponse };

renderAll();
loadDetector();
openCamera();
