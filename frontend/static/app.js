const preview = document.querySelector("#cameraPreview");
const cameraMessage = document.querySelector("#cameraMessage");
const cameraSelect = document.querySelector("#cameraSelect");
const startButton = document.querySelector("#startButton");
const stopButton = document.querySelector("#stopButton");
const submitButton = document.querySelector("#submitButton");
const actionMessage = document.querySelector("#actionMessage");

let cameraStream;
let isOpeningCamera = false;
let activeCameraId = "";

function showCameraMessage(message) {
  cameraMessage.textContent = message;
  cameraMessage.classList.remove("is-hidden");
}

function setCameraReady() {
  cameraMessage.classList.add("is-hidden");
  actionMessage.textContent = "";
  startButton.disabled = true;
  stopButton.disabled = false;
  cameraSelect.disabled = cameraSelect.options.length <= 1;
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
    cameraSelect.add(new Option("No webcams found", ""));
    cameraSelect.disabled = true;
    return "";
  }

  cameras.forEach((camera, index) => {
    cameraSelect.add(new Option(camera.label || `Webcam ${index + 1}`, camera.deviceId));
  });

  const selectedId = cameras.some((camera) => camera.deviceId === preferredCameraId)
    ? preferredCameraId
    : cameras[0].deviceId;
  cameraSelect.value = selectedId;
  cameraSelect.disabled = isOpeningCamera || cameras.length <= 1;
  return selectedId;
}

async function openCamera() {
  if (!navigator.mediaDevices?.getUserMedia) {
    showCameraMessage("This browser does not support webcam access.");
    startButton.disabled = true;
    return;
  }

  if (isOpeningCamera) {
    return;
  }

  isOpeningCamera = true;
  startButton.disabled = true;
  stopButton.disabled = true;
  cameraSelect.disabled = true;
  showCameraMessage("Requesting camera access…");

  try {
    const requestedCameraId = cameraSelect.value;
    const nextStream = await navigator.mediaDevices.getUserMedia({
      video: requestedCameraId ? { deviceId: { exact: requestedCameraId } } : true,
      audio: false,
    });
    const previousStream = cameraStream;

    cameraStream = nextStream;
    preview.srcObject = nextStream;
    previousStream?.getTracks().forEach((track) => track.stop());
    activeCameraId = await refreshCameraList(requestedCameraId || getActiveCameraId()).catch(
      () => requestedCameraId || getActiveCameraId()
    );
    setCameraReady();
  } catch (error) {
    const denied = error.name === "NotAllowedError" || error.name === "SecurityError";
    const failureMessage = denied
      ? "Camera permission was not granted. Allow access in your browser, then select Start."
      : "We could not open that camera. Check that it is connected, then select Start.";

    if (cameraStream) {
      cameraMessage.classList.add("is-hidden");
      actionMessage.textContent = `${failureMessage} Your current preview is still open.`;
      startButton.disabled = true;
      stopButton.disabled = false;
    } else {
      showCameraMessage(failureMessage);
      startButton.disabled = false;
      stopButton.disabled = true;
    }
    activeCameraId = await refreshCameraList(getActiveCameraId()).catch(() => getActiveCameraId());
  } finally {
    isOpeningCamera = false;
    cameraSelect.disabled = cameraSelect.options.length <= 1;
  }
}

function closeCamera() {
  cameraStream?.getTracks().forEach((track) => track.stop());
  cameraStream = undefined;
  activeCameraId = "";
  preview.srcObject = null;
  showCameraMessage("Camera preview stopped. Select Start to reopen it.");
  startButton.disabled = false;
  stopButton.disabled = true;
  cameraSelect.disabled = cameraSelect.options.length <= 1;
}

startButton.addEventListener("click", openCamera);
stopButton.addEventListener("click", closeCamera);
cameraSelect.addEventListener("change", () => {
  if (cameraStream && cameraSelect.value !== activeCameraId) {
    actionMessage.textContent = "Switching webcam…";
    openCamera();
  }
});
submitButton.addEventListener("click", () => {
  actionMessage.textContent = "Submit is a placeholder — no recording or backend has been connected yet.";
});

window.addEventListener("beforeunload", closeCamera);
navigator.mediaDevices?.addEventListener("devicechange", () => {
  refreshCameraList().catch(() => {});
});
openCamera();
