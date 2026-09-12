const preview = document.querySelector("#cameraPreview");
const cameraMessage = document.querySelector("#cameraMessage");
const startButton = document.querySelector("#startButton");
const stopButton = document.querySelector("#stopButton");
const submitButton = document.querySelector("#submitButton");
const actionMessage = document.querySelector("#actionMessage");

let cameraStream;

function showCameraMessage(message) {
  cameraMessage.textContent = message;
  cameraMessage.classList.remove("is-hidden");
}

function setCameraReady() {
  cameraMessage.classList.add("is-hidden");
  startButton.disabled = true;
  stopButton.disabled = false;
}

async function openCamera() {
  if (!navigator.mediaDevices?.getUserMedia) {
    showCameraMessage("This browser does not support webcam access.");
    startButton.disabled = true;
    return;
  }

  showCameraMessage("Requesting camera access…");

  try {
    cameraStream = await navigator.mediaDevices.getUserMedia({ video: true, audio: false });
    preview.srcObject = cameraStream;
    setCameraReady();
  } catch (error) {
    const denied = error.name === "NotAllowedError" || error.name === "SecurityError";
    showCameraMessage(
      denied
        ? "Camera permission was not granted. Allow access in your browser, then select Start."
        : "We could not open a camera. Check that one is connected, then select Start."
    );
    startButton.disabled = false;
    stopButton.disabled = true;
  }
}

function closeCamera() {
  cameraStream?.getTracks().forEach((track) => track.stop());
  cameraStream = undefined;
  preview.srcObject = null;
  showCameraMessage("Camera preview stopped. Select Start to reopen it.");
  startButton.disabled = false;
  stopButton.disabled = true;
}

startButton.addEventListener("click", openCamera);
stopButton.addEventListener("click", closeCamera);
submitButton.addEventListener("click", () => {
  actionMessage.textContent = "Submit is a placeholder — no recording or backend has been connected yet.";
});

window.addEventListener("beforeunload", closeCamera);
openCamera();
