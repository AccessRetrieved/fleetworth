const WAYPOINTS = [
  {
    id: "front",
    label: "Front",
    title: "Show the front",
    instruction: "Center the grille, headlights, bumper, hood, and windshield in one clear frame.",
    tip: "Stand far enough back that neither corner of the bumper touches the frame.",
    validation: "vehicle",
  },
  {
    id: "driver_front",
    label: "Driver side · front",
    title: "Driver side — front half",
    instruction: "Capture the front wheel, driver door, mirror, and front body panels.",
    tip: "Keep the camera parallel to the truck to make dents and panel gaps easier to see.",
    validation: "vehicle",
  },
  {
    id: "driver_rear",
    label: "Driver side · rear",
    title: "Driver side — rear half",
    instruction: "Capture the rear door or cab edge, bed, rear wheel, and bedside panel.",
    tip: "Overlap part of the previous view so the two side frames can be fused reliably.",
    validation: "vehicle",
  },
  {
    id: "rear",
    label: "Rear",
    title: "Show the rear",
    instruction: "Center the tailgate, rear bumper, taillights, and bed opening.",
    tip: "Keep the full bumper visible and avoid standing at a steep angle.",
    validation: "vehicle",
  },
  {
    id: "passenger_rear",
    label: "Passenger side · rear",
    title: "Passenger side — rear half",
    instruction: "Capture the bed, rear wheel, bedside panel, and rear cab edge.",
    tip: "Move slowly and keep the truck filling roughly two-thirds of the guide.",
    validation: "vehicle",
  },
  {
    id: "passenger_front",
    label: "Passenger side · front",
    title: "Passenger side — front half",
    instruction: "Capture the passenger door, mirror, front wheel, and front body panels.",
    tip: "Include a small overlap with the rear-half shot and keep all panel edges sharp.",
    validation: "vehicle",
  },
  {
    id: "tires",
    label: "Tires & wheels",
    title: "Show the tires and wheels",
    instruction: "Fill the guide with one representative tire, its tread, sidewall, and wheel.",
    tip: "Get close enough to see tread depth, but keep the entire tire inside the corners.",
    validation: "detail",
  },
];

const VEHICLE_CLASSES = new Set(["truck", "car", "bus"]);
const ANALYSIS_INTERVAL_MS = 600;
const AUTO_CAPTURE_DELAY_MS = 1800;
const MIN_VEHICLE_SCORE = 0.42;

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
const waypointList = document.querySelector("#waypointList");
const captureGrid = document.querySelector("#captureGrid");
const stepLabel = document.querySelector("#stepLabel");
const captureTitle = document.querySelector("#captureTitle");
const captureInstruction = document.querySelector("#captureInstruction");
const shotTip = document.querySelector("#shotTip");
const progressBar = document.querySelector("#progressBar");
const progressPercent = document.querySelector("#progressPercent");
const headerProgress = document.querySelector("#headerProgress");
const reviewSummary = document.querySelector("#reviewSummary");
const submitHeading = document.querySelector("#submitHeading");

const captures = new Map();
let cameraStream;
let isOpeningCamera = false;
let isCapturing = false;
let activeCameraId = "";
let currentIndex = 0;
let detector;
let detectorMode = "loading";
let analysisTimer;
let stableSince = 0;
let currentAssessment = { ready: false, message: "Loading framing assistant…", confidence: 0 };

function currentWaypoint() {
  return WAYPOINTS[currentIndex];
}

function showCameraMessage(message) {
  cameraMessage.textContent = message;
  cameraMessage.classList.remove("is-hidden");
}

function hideCameraMessage() {
  cameraMessage.classList.add("is-hidden");
}

function setFrameStatus(type, message) {
  frameStatus.className = `frame-status is-${type}`;
  frameStatusText.textContent = message;
}

function renderWaypoints() {
  waypointList.replaceChildren();

  WAYPOINTS.forEach((waypoint, index) => {
    const item = document.createElement("li");
    const button = document.createElement("button");
    item.className = "waypoint-item";
    item.classList.toggle("is-current", index === currentIndex);
    item.classList.toggle("is-complete", captures.has(waypoint.id));
    button.className = "waypoint-button";
    button.type = "button";
    button.innerHTML = `
      <span class="step-number">${captures.has(waypoint.id) ? "✓" : index + 1}</span>
      <span class="waypoint-name">${waypoint.label}</span>
      <span class="waypoint-status" aria-hidden="true">✓</span>
    `;
    button.setAttribute("aria-label", `${captures.has(waypoint.id) ? "Retake" : "Go to"} ${waypoint.label}`);
    button.addEventListener("click", () => selectWaypoint(index));
    item.append(button);
    waypointList.append(item);
  });
}

function renderCaptureGrid() {
  captureGrid.replaceChildren();

  WAYPOINTS.forEach((waypoint, index) => {
    const card = document.createElement("article");
    const capture = captures.get(waypoint.id);
    card.className = "capture-card";

    if (capture) {
      const image = new Image();
      image.src = capture.url;
      image.alt = `${waypoint.label} captured keyframe`;
      const label = document.createElement("span");
      label.className = "capture-card-label";
      label.textContent = waypoint.label;
      const retake = document.createElement("button");
      retake.className = "retake-button";
      retake.type = "button";
      retake.textContent = "Retake";
      retake.addEventListener("click", () => selectWaypoint(index));
      card.append(image, label, retake);
    } else {
      const empty = document.createElement("div");
      empty.className = "capture-card-empty";
      empty.innerHTML = `<span>${index + 1}</span>${waypoint.label}`;
      card.append(empty);
    }

    captureGrid.append(card);
  });
}

function renderProgress() {
  const completed = captures.size;
  const percent = Math.round((completed / WAYPOINTS.length) * 100);
  const remaining = WAYPOINTS.length - completed;
  const allComplete = remaining === 0;

  progressBar.style.width = `${percent}%`;
  progressPercent.textContent = `${percent}%`;
  headerProgress.textContent = `${completed} of ${WAYPOINTS.length} views`;
  reviewSummary.textContent = allComplete
    ? "All required views are ready for analysis. Retake any frame that is unclear."
    : `${remaining} required ${remaining === 1 ? "view" : "views"} remaining.`;
  submitHeading.textContent = allComplete
    ? "Inspection set complete"
    : `${remaining} ${remaining === 1 ? "view" : "views"} remaining`;
  submitButton.disabled = !allComplete;
  resetButton.disabled = completed === 0;
}

function renderCurrentWaypoint() {
  const waypoint = currentWaypoint();
  stepLabel.textContent = `Step ${currentIndex + 1} of ${WAYPOINTS.length}`;
  captureTitle.textContent = waypoint.title;
  captureInstruction.textContent = waypoint.instruction;
  shotTip.textContent = waypoint.tip;
  captureButton.textContent = captures.has(waypoint.id) ? "Retake shot" : "Capture shot";
  stableSince = 0;
  clearDetectionOverlay();
  renderWaypoints();
}

function renderAll() {
  renderCurrentWaypoint();
  renderCaptureGrid();
  renderProgress();
}

function selectWaypoint(index) {
  currentIndex = index;
  actionMessage.textContent = captures.has(currentWaypoint().id)
    ? `Ready to retake ${currentWaypoint().label.toLowerCase()}.`
    : "";
  renderCurrentWaypoint();
  document.querySelector(".capture-panel").scrollIntoView({ behavior: "smooth", block: "start" });
}

function getActiveCameraId() {
  return cameraStream?.getVideoTracks()[0]?.getSettings().deviceId || "";
}

async function refreshCameraList(preferredCameraId = getActiveCameraId()) {
  if (!navigator.mediaDevices?.enumerateDevices) {
    return cameraSelect.value;
  }

  const devices = await navigator.mediaDevices.enumerateDevices();
  const cameras = devices.filter((device) => device.kind === "videoinput");
  cameraSelect.replaceChildren();

  if (cameras.length === 0) {
    cameraSelect.add(new Option("No cameras found", ""));
    cameraSelect.disabled = true;
    return "";
  }

  cameras.forEach((camera, index) => {
    cameraSelect.add(new Option(camera.label || `Camera ${index + 1}`, camera.deviceId));
  });

  const selectedId = cameras.some((camera) => camera.deviceId === preferredCameraId)
    ? preferredCameraId
    : cameras[0].deviceId;
  cameraSelect.value = selectedId;
  cameraSelect.disabled = isOpeningCamera || cameras.length <= 1;
  return selectedId;
}

function setCameraReady() {
  hideCameraMessage();
  actionMessage.textContent = "";
  startButton.disabled = true;
  stopButton.disabled = false;
  cameraSelect.disabled = cameraSelect.options.length <= 1;
  startAnalysisLoop();
}

async function openCamera() {
  if (!navigator.mediaDevices?.getUserMedia) {
    showCameraMessage("This browser does not support webcam access.");
    startButton.disabled = true;
    setFrameStatus("error", "Camera API unavailable");
    return;
  }

  if (isOpeningCamera) {
    return;
  }

  isOpeningCamera = true;
  stopAnalysisLoop();
  startButton.disabled = true;
  stopButton.disabled = true;
  captureButton.disabled = true;
  cameraSelect.disabled = true;
  showCameraMessage("Requesting camera access…");
  setFrameStatus("loading", "Opening camera…");

  try {
    const requestedCameraId = cameraSelect.value;
    const videoConstraints = requestedCameraId
      ? { deviceId: { exact: requestedCameraId }, width: { ideal: 1920 }, height: { ideal: 1080 } }
      : { facingMode: { ideal: "environment" }, width: { ideal: 1920 }, height: { ideal: 1080 } };
    const nextStream = await navigator.mediaDevices.getUserMedia({ video: videoConstraints, audio: false });
    const previousStream = cameraStream;
    cameraStream = nextStream;
    preview.srcObject = nextStream;
    previousStream?.getTracks().forEach((track) => track.stop());
    await preview.play();
    activeCameraId = await refreshCameraList(requestedCameraId || getActiveCameraId()).catch(
      () => requestedCameraId || getActiveCameraId()
    );
    setCameraReady();
  } catch (error) {
    const denied = error.name === "NotAllowedError" || error.name === "SecurityError";
    const message = denied
      ? "Camera permission was not granted. Allow access, then select Start camera."
      : "We could not open that camera. Check that it is connected and available.";
    showCameraMessage(message);
    setFrameStatus("error", denied ? "Camera permission needed" : "Camera unavailable");
    startButton.disabled = false;
    stopButton.disabled = !cameraStream;
    activeCameraId = await refreshCameraList(getActiveCameraId()).catch(() => getActiveCameraId());
    if (cameraStream) {
      hideCameraMessage();
      startAnalysisLoop();
    }
  } finally {
    isOpeningCamera = false;
    cameraSelect.disabled = cameraSelect.options.length <= 1;
  }
}

function closeCamera({ updateInterface = true } = {}) {
  stopAnalysisLoop();
  cameraStream?.getTracks().forEach((track) => track.stop());
  cameraStream = undefined;
  activeCameraId = "";
  preview.srcObject = null;
  clearDetectionOverlay();
  captureButton.disabled = true;
  if (updateInterface) {
    showCameraMessage("Camera paused. Select Start camera when you are ready.");
    setFrameStatus("loading", "Camera paused");
    startButton.disabled = false;
    stopButton.disabled = true;
    cameraSelect.disabled = cameraSelect.options.length <= 1;
  }
}

async function loadDetector() {
  if (!window.cocoSsd) {
    detectorMode = "fallback";
    autoCapture.checked = false;
    autoCapture.disabled = true;
    setFrameStatus("warning", "Detector unavailable — manual framing enabled");
    return;
  }

  setFrameStatus("loading", "Loading truck detector…");
  try {
    detector = await window.cocoSsd.load({ base: "lite_mobilenet_v2" });
    detectorMode = "ready";
    if (cameraStream) {
      setFrameStatus("loading", "Looking for the truck…");
    }
  } catch (error) {
    detectorMode = "fallback";
    autoCapture.checked = false;
    autoCapture.disabled = true;
    setFrameStatus("warning", "Detector unavailable — manual framing enabled");
  }
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

function measureFrameQuality() {
  const sample = document.createElement("canvas");
  const context = sample.getContext("2d", { willReadFrequently: true });
  sample.width = 160;
  sample.height = 90;
  context.drawImage(preview, 0, 0, sample.width, sample.height);
  const pixels = context.getImageData(0, 0, sample.width, sample.height).data;
  const gray = new Uint8Array(sample.width * sample.height);
  let brightnessTotal = 0;

  for (let pixel = 0, index = 0; pixel < pixels.length; pixel += 4, index += 1) {
    const value = Math.round(pixels[pixel] * 0.299 + pixels[pixel + 1] * 0.587 + pixels[pixel + 2] * 0.114);
    gray[index] = value;
    brightnessTotal += value;
  }

  let edgeTotal = 0;
  let comparisons = 0;
  for (let y = 1; y < sample.height; y += 1) {
    for (let x = 1; x < sample.width; x += 1) {
      const index = y * sample.width + x;
      edgeTotal += Math.abs(gray[index] - gray[index - 1]);
      edgeTotal += Math.abs(gray[index] - gray[index - sample.width]);
      comparisons += 2;
    }
  }

  const brightness = brightnessTotal / gray.length;
  const sharpness = edgeTotal / comparisons;
  return { brightness, sharpness, isDark: brightness < 38, isBright: brightness > 236, isBlurry: sharpness < 7 };
}

function assessDetailFrame(quality) {
  if (quality.isDark) {
    return { ready: false, type: "warning", message: "Too dark — add light or change angle", confidence: 0 };
  }
  if (quality.isBright) {
    return { ready: false, type: "warning", message: "Too much glare — change angle", confidence: 0 };
  }
  if (quality.isBlurry) {
    return { ready: false, type: "warning", message: "Hold steady — image is blurry", confidence: 0 };
  }
  return {
    ready: true,
    type: "ready",
    message: "Clear frame — confirm the full tire is visible",
    confidence: 1,
  };
}

function assessVehicleFrame(predictions, quality) {
  const vehicle = predictions
    .filter((prediction) => VEHICLE_CLASSES.has(prediction.class) && prediction.score >= MIN_VEHICLE_SCORE)
    .sort((left, right) => right.score - left.score)[0];

  if (!vehicle) {
    clearDetectionOverlay();
    return { ready: false, type: "warning", message: "No truck detected — point the camera at the vehicle", confidence: 0 };
  }

  drawDetection(vehicle);
  const [x, y, width, height] = vehicle.bbox;
  const frameWidth = preview.videoWidth;
  const frameHeight = preview.videoHeight;
  const coverage = (width * height) / (frameWidth * frameHeight);
  const marginX = frameWidth * 0.018;
  const marginY = frameHeight * 0.018;
  const clipped = x < marginX || y < marginY || x + width > frameWidth - marginX || y + height > frameHeight - marginY;

  if (clipped || coverage > 0.84) {
    return { ready: false, type: "warning", message: "Step back — part of the truck is cut off", confidence: vehicle.score };
  }
  if (coverage < 0.13) {
    return { ready: false, type: "warning", message: "Move closer — the truck is too small", confidence: vehicle.score };
  }
  if (quality.isDark) {
    return { ready: false, type: "warning", message: "Truck found, but the frame is too dark", confidence: vehicle.score };
  }
  if (quality.isBright) {
    return { ready: false, type: "warning", message: "Truck found, but glare is hiding details", confidence: vehicle.score };
  }
  if (quality.isBlurry) {
    return { ready: false, type: "warning", message: "Truck found — hold the camera steady", confidence: vehicle.score };
  }
  return { ready: true, type: "ready", message: `Truck detected · ${Math.round(vehicle.score * 100)}% confidence`, confidence: vehicle.score };
}

function assessFallbackFrame(quality) {
  const detailAssessment = assessDetailFrame(quality);
  if (!detailAssessment.ready) {
    return detailAssessment;
  }
  return { ready: true, type: "ready", message: "Frame is clear — manually confirm the truck fits the guide", confidence: 0 };
}

async function analyzeCurrentFrame() {
  if (!cameraStream || preview.readyState < HTMLMediaElement.HAVE_CURRENT_DATA || isCapturing) {
    analysisTimer = window.setTimeout(analyzeCurrentFrame, ANALYSIS_INTERVAL_MS);
    return;
  }

  try {
    const quality = measureFrameQuality();
    const waypoint = currentWaypoint();

    if (waypoint.validation === "detail") {
      clearDetectionOverlay();
      currentAssessment = assessDetailFrame(quality);
    } else if (detectorMode === "ready" && detector) {
      setFrameStatus("loading", "Checking truck position…");
      const predictions = await detector.detect(preview, 10, 0.35);
      currentAssessment = assessVehicleFrame(predictions, quality);
    } else if (detectorMode === "loading") {
      currentAssessment = { ready: false, type: "loading", message: "Loading truck detector…", confidence: 0 };
    } else {
      clearDetectionOverlay();
      currentAssessment = assessFallbackFrame(quality);
    }

    updateCaptureReadiness();
  } catch (error) {
    currentAssessment = { ready: false, type: "error", message: "Framing check paused — try again", confidence: 0 };
    updateCaptureReadiness();
  }
  analysisTimer = window.setTimeout(analyzeCurrentFrame, ANALYSIS_INTERVAL_MS);
}

function updateCaptureReadiness() {
  captureButton.disabled = !cameraStream || !currentAssessment.ready || isCapturing;
  setFrameStatus(currentAssessment.type, currentAssessment.message);

  if (!currentAssessment.ready) {
    stableSince = 0;
    return;
  }
  if (!stableSince) {
    stableSince = Date.now();
  }

  const stableFor = Date.now() - stableSince;
  const canAutoCapture = autoCapture.checked
    && currentWaypoint().validation === "vehicle"
    && !captures.has(currentWaypoint().id);
  if (canAutoCapture && stableFor >= AUTO_CAPTURE_DELAY_MS) {
    captureCurrentFrame({ automatic: true });
  } else if (canAutoCapture) {
    const seconds = Math.max(1, Math.ceil((AUTO_CAPTURE_DELAY_MS - stableFor) / 1000));
    setFrameStatus("ready", `Hold steady · auto-capturing in ${seconds}`);
  }
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

function canvasToBlob(canvas) {
  return new Promise((resolve, reject) => {
    canvas.toBlob((blob) => (blob ? resolve(blob) : reject(new Error("Could not encode frame"))), "image/jpeg", 0.9);
  });
}

async function captureCurrentFrame({ automatic = false } = {}) {
  if (!cameraStream || !currentAssessment.ready || isCapturing) {
    return;
  }

  isCapturing = true;
  captureButton.disabled = true;
  stopAnalysisLoop();
  const waypoint = currentWaypoint();
  const maxWidth = 1920;
  const scale = Math.min(1, maxWidth / preview.videoWidth);
  captureCanvas.width = Math.round(preview.videoWidth * scale);
  captureCanvas.height = Math.round(preview.videoHeight * scale);
  captureCanvas.getContext("2d").drawImage(preview, 0, 0, captureCanvas.width, captureCanvas.height);

  try {
    const blob = await canvasToBlob(captureCanvas);
    const previousCapture = captures.get(waypoint.id);
    if (previousCapture) {
      URL.revokeObjectURL(previousCapture.url);
    }
    captures.set(waypoint.id, {
      blob,
      url: URL.createObjectURL(blob),
      capturedAt: new Date().toISOString(),
      width: captureCanvas.width,
      height: captureCanvas.height,
      detectorConfidence: currentAssessment.confidence,
    });

    captureFlash.classList.remove("is-active");
    void captureFlash.offsetWidth;
    captureFlash.classList.add("is-active");
    actionMessage.textContent = `${waypoint.label} captured${automatic ? " automatically" : ""}.`;

    const nextIndex = WAYPOINTS.findIndex((candidate, index) => index > currentIndex && !captures.has(candidate.id));
    const firstMissingIndex = WAYPOINTS.findIndex((candidate) => !captures.has(candidate.id));
    if (nextIndex !== -1) {
      currentIndex = nextIndex;
    } else if (firstMissingIndex !== -1) {
      currentIndex = firstMissingIndex;
    }
    renderAll();
  } catch (error) {
    actionMessage.textContent = "That frame could not be saved. Hold steady and try again.";
  } finally {
    isCapturing = false;
    stableSince = 0;
    startAnalysisLoop();
  }
}

function buildCaptureManifest() {
  return {
    schema_version: "1.0",
    captured_at: new Date().toISOString(),
    waypoint_count: WAYPOINTS.length,
    waypoints: WAYPOINTS.map((waypoint) => {
      const capture = captures.get(waypoint.id);
      return {
        id: waypoint.id,
        label: waypoint.label,
        filename: `${waypoint.id}.jpg`,
        captured_at: capture.capturedAt,
        width: capture.width,
        height: capture.height,
        detector_confidence: capture.detectorConfidence,
      };
    }),
  };
}

function buildCaptureFormData() {
  const manifest = buildCaptureManifest();
  const formData = new FormData();
  formData.append("manifest", new Blob([JSON.stringify(manifest)], { type: "application/json" }), "manifest.json");
  WAYPOINTS.forEach((waypoint) => {
    formData.append("images", captures.get(waypoint.id).blob, `${waypoint.id}.jpg`);
  });
  return { formData, manifest };
}

function prepareCaptures() {
  if (captures.size !== WAYPOINTS.length) {
    actionMessage.textContent = "Complete every required view before analysis.";
    return;
  }

  const manifest = buildCaptureManifest();
  window.dispatchEvent(new CustomEvent("fleetworth:capture-ready", { detail: { manifest } }));
  actionMessage.textContent = `${WAYPOINTS.length} keyframes are ready for the future FastAPI integration.`;
  document.querySelector(".capture-panel").scrollIntoView({ behavior: "smooth", block: "end" });
}

function resetCaptureSession() {
  if (!window.confirm("Clear all captured truck views and start over?")) {
    return;
  }
  captures.forEach((capture) => URL.revokeObjectURL(capture.url));
  captures.clear();
  currentIndex = 0;
  actionMessage.textContent = "Capture session cleared.";
  renderAll();
}

startButton.addEventListener("click", openCamera);
stopButton.addEventListener("click", () => closeCamera());
captureButton.addEventListener("click", () => captureCurrentFrame());
resetButton.addEventListener("click", resetCaptureSession);
submitButton.addEventListener("click", prepareCaptures);
cameraSelect.addEventListener("change", () => {
  if (cameraStream && cameraSelect.value !== activeCameraId) {
    actionMessage.textContent = "Switching camera…";
    openCamera();
  }
});
autoCapture.addEventListener("change", () => {
  stableSince = 0;
  actionMessage.textContent = autoCapture.checked ? "Auto-capture enabled." : "Use Capture shot when the frame is ready.";
});
navigator.mediaDevices?.addEventListener("devicechange", () => {
  refreshCameraList().catch(() => {});
});
window.addEventListener("beforeunload", () => {
  closeCamera({ updateInterface: false });
  captures.forEach((capture) => URL.revokeObjectURL(capture.url));
});

window.FleetworthCapture = {
  buildFormData: () => (captures.size === WAYPOINTS.length ? buildCaptureFormData() : null),
  getManifest: () => (captures.size === WAYPOINTS.length ? buildCaptureManifest() : null),
};

renderAll();
loadDetector();
openCamera();
