// Exercise the shipped script with DOM/media boundaries stubbed. No camera,
// backend, CDN, or paid API is needed: node --test frontend/tests/*.test.cjs
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function app() {
  class Element {
    constructor() {
      this.dataset = {}; this.style = {}; this.events = {}; this.children = [];
      this.options = []; this.readyState = 2; this.videoWidth = 1280; this.videoHeight = 720;
      this.classList = { add() {}, remove() {}, toggle() {} };
      this.checked = false; this.disabled = false;
    }
    addEventListener(type, fn) { this.events[type] = fn; }
    click() { return this.events.click?.(); }
    append(...children) { this.children.push(...children); }
    replaceChildren(...children) { this.children = children; }
    setAttribute(key, value) { this[key] = value; }
    removeAttribute(key) { delete this[key]; }
    scrollIntoView() {}
    pause() {}
    load() {}
    play() { return Promise.resolve(); }
    querySelector() { return new Element(); }
    getContext() {
      return {
        clearRect() {}, drawImage() {}, setLineDash() {}, strokeRect() {},
        getImageData() {
          const data = new Uint8ClampedArray(160 * 90 * 4);
          for (let i = 0; i < data.length; i++) data[i] = Math.floor(i / 4) % 2 ? 200 : 60;
          return { data };
        },
      };
    }
    toBlob(callback) { callback(new Blob(['frame'], { type: 'image/jpeg' })); }
  }
  class Recorder extends Element {
    constructor() { super(); this.state = 'inactive'; this.mimeType = 'video/webm'; }
    static isTypeSupported() { return true; }
    start() { this.state = 'recording'; }
    pause() { this.state = 'paused'; }
    resume() { this.state = 'recording'; }
    stop() { this.state = 'inactive'; } // Deliberately defer final events.
    finish() {
      this.events.dataavailable({ data: new Blob(['recording']) });
      this.events.stop();
    }
  }
  const elements = new Map();
  const element = (selector) => {
    if (!elements.has(selector)) elements.set(selector, new Element());
    return elements.get(selector);
  };
  const revoked = [];
  const sandbox = {
    document: { querySelector: element, createElement: () => new Element(), body: { dataset: { predictEndpoint: '/predict' } } },
    navigator: {}, Image: Element, Option: Element, HTMLMediaElement: { HAVE_CURRENT_DATA: 2 },
    MediaRecorder: Recorder, Blob, File, FormData, Date, crypto,
    URL: { createObjectURL: () => `blob:${crypto.randomUUID()}`, revokeObjectURL: (url) => revoked.push(url) },
    console, CustomEvent: class {},
    setTimeout: () => 1, clearTimeout() {}, setInterval: () => 1, clearInterval() {},
    addEventListener() {}, dispatchEvent() {}, confirm: () => true,
  };
  sandbox.window = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../static/app.js'), 'utf8'), sandbox);
  return { element, sandbox, revoked, run: (code) => vm.runInContext(code, sandbox) };
}

function photos(count) {
  return Array.from({ length: count }, (_, i) => new File(['image'], `${i}.jpg`, { type: 'image/jpeg' }));
}

async function selectPhotos(a, count) {
  const input = a.element('#photoFileInput');
  input.files = photos(count);
  await input.events.change();
}

test('one photo then two more accumulates and builds a photo-only payload', async () => {
  const a = app();
  await selectPhotos(a, 1);
  assert.equal(a.element('#submitButton').disabled, true);
  assert.equal(a.element('#continueButton').textContent, 'Add photos');
  assert.equal(a.element('#continueButton').hidden, false);
  let opened = false;
  a.element('#photoFileInput').events.click = () => { opened = true; };
  a.element('#continueButton').click();
  assert.equal(opened, true);
  await selectPhotos(a, 2);
  assert.equal(a.element('#submitButton').disabled, false);
  const payload = a.run('buildFormData()');
  assert.equal(payload.formData.getAll('photos').length, 3);
  assert.equal(payload.formData.has('video'), false);
  assert.equal(a.revoked.length, 0);
});

test('photo ceiling, removal and coverage recovery preserve existing images', async () => {
  const a = app();
  await selectPhotos(a, 3);
  a.run('beginFocusedRecapture(GUIDE_STEPS[3])');
  assert.equal(a.run('captures.length'), 3);
  await selectPhotos(a, 7);
  assert.equal(a.run('captures.length'), 7);
  assert.equal(a.element('#continueButton').disabled, true);
  a.run('removeCapture(captures[0].id)');
  assert.equal(a.element('#continueButton').disabled, false);
  assert.equal(a.revoked.length, 1);
});

for (const paused of [false, true]) {
  test(`Stop opens review before final video event (${paused ? 'paused' : 'recording'})`, async () => {
    const a = app();
    await selectPhotos(a, 3);
    a.run('cameraStream = {}; startSession()');
    if (paused) a.element('#pauseButton').click();
    a.element('#stopButton').click();
    assert.equal(a.element('.app-shell').dataset.screen, 'review');
    assert.equal(a.element('#submitButton').disabled, true);
    assert.equal(a.run('buildFormData()'), null);
    a.run('mediaRecorder.finish()');
    assert.equal(a.element('#submitButton').disabled, false);
    assert.equal(a.run('buildFormData().formData.get("video").size'), 9);
    a.run('beginFocusedRecapture(GUIDE_STEPS[3])');
    assert.equal(a.run('captures.length'), 3);
    assert.equal(a.element('.app-shell').dataset.screen, 'capturing');
    assert.equal(a.run('analysisTimer !== undefined'), true);
  });
}

test('Stop with zero photos advances, reset ignores a late recorder event', () => {
  const a = app();
  a.run('cameraStream = {}; startSession(); stopSession()');
  assert.equal(a.element('.app-shell').dataset.screen, 'review');
  a.run('resetSession({confirmUser:false}); mediaRecorder.finish()');
  assert.equal(a.element('.app-shell').dataset.screen, 'ready');
  assert.equal(a.run('sessionVideo'), undefined);
});

test('missed detection permits explicit manual capture, never auto-snap or blurry capture', async () => {
  const a = app();
  a.run('isRecording = true; currentAssessment = assessVehicleFrame([], {isDark:false,isBright:false,isBlurry:false}); updateCaptureReadiness()');
  assert.equal(a.element('#captureButton').disabled, false);
  await a.run('captureFrame({automatic:true})');
  assert.equal(a.run('captures.length'), 0);
  await a.element('#captureButton').click();
  assert.equal(a.run('captures.length'), 1);
  assert.equal(a.run('captures[0].limited'), true);
  a.run('currentAssessment = assessVehicleFrame([], {isDark:false,isBright:false,isBlurry:true}); updateCaptureReadiness()');
  assert.equal(a.element('#captureButton').disabled, true);
});

test('existing refusal rendering and HTTP detail handling remain correct', async () => {
  const a = app();
  a.run('renderBackendResponse({status:"not_a_truck", message:"Use real photos"})');
  assert.equal(a.element('#resultScreen').children[0].textContent, 'Unable to assess');
  assert.doesNotMatch(a.element('#resultScreen').className, /is-error/);
  a.sandbox.fetch = async () => ({ ok: false, status: 422, json: async () => ({ detail: [{ loc: ['body', 'photos'], msg: 'Field required' }] }) });
  await assert.rejects(a.run('postPredict(new FormData())'), /photos: Field required/);
});

test('six photos and a recording above the old 100 MiB cap fit the new allowance', () => {
  const a = app();
  assert.equal(a.run('uploadSizeError(Array.from({length:6}, () => ({size:2*MIB})), {size:150*MIB})'), '');
  assert.equal(a.run('uploadSizeError(Array.from({length:7}, () => ({size:MAX_PHOTO_BYTES})), {size:MAX_VIDEO_BYTES})'), '');
  assert.match(a.run('uploadSizeError([{size:MAX_PHOTO_BYTES+1}], null)'), /Photo 1.*limit/);
  assert.match(a.run('uploadSizeError([], {size:MAX_VIDEO_BYTES+1})'), /Session recording.*Continue capturing/);
});

test('recorder requests a controlled bitrate, including the default-codec fallback', () => {
  const a = app();
  assert.equal(a.run('chooseRecorderOptions().videoBitsPerSecond'), 1_500_000);
  a.run('MediaRecorder.isTypeSupported = () => false');
  assert.equal(a.run('chooseRecorderOptions().videoBitsPerSecond'), 1_500_000);
  assert.equal(a.run('chooseRecorderOptions().mimeType'), undefined);
});

for (const limit of ['recordedBytes = RECORDING_STOP_BYTES - 1', 'recordingStartedAt = Date.now() - MAX_RECORDING_MS']) {
  test(`recording budget stops and preserves all photos: ${limit}`, async () => {
    const a = app();
    await selectPhotos(a, 6);
    a.run('cameraStream = {}; startSession()');
    a.run(limit);
    a.run('mediaRecorder.events.dataavailable({data:new Blob(["chunk"])}); checkRecordingBudget()');
    assert.equal(a.element('.app-shell').dataset.screen, 'review');
    assert.equal(a.element('#submitButton').disabled, true);
    a.run('mediaRecorder.finish()');
    assert.equal(a.element('#submitButton').disabled, false);
    assert.equal(a.run('captures.length'), 6);
    assert.equal(a.run('sessionVideo.size'), 14); // retains every chunk, including final data
    assert.match(a.element('#sessionVideoMeta').textContent, /MiB/);
    assert.match(a.element('#submitHeading').textContent, /session limit/);
  });
}

test('oversized evidence is blocked before a network request', async () => {
  const a = app();
  a.sandbox.fetch = () => assert.fail('Must not upload oversized evidence');
  await assert.rejects(a.run('postPredict({getAll: () => [], get: () => ({size:MAX_VIDEO_BYTES+1})})'), /Session recording/);
});

test('re-recording an oversized video keeps the six existing photos', async () => {
  const a = app();
  await selectPhotos(a, 6);
  a.run('captureSource = "live"; sessionVideo = {size:MAX_VIDEO_BYTES+1}; renderReview()');
  assert.equal(a.element('#submitButton').disabled, true);
  assert.match(a.element('#submitHeading').textContent, /Session recording/);
  a.run('cameraStream = {}; startSession(); stopSession(); mediaRecorder.finish()');
  assert.equal(a.run('captures.length'), 6);
  assert.equal(a.element('#submitButton').disabled, false);
});
