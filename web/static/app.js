"use strict";

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------
const state = {
  mediaRecorder: null,
  stream: null,          // kept open between segments so mic stays active
  audioChunks: [],
  sessionId: null,
  isRecording: false,
  timerInterval: null,
  timerSeconds: 0,
  audioCtx: null,
  analyser: null,
  animFrameId: null,
  // Silence-triggered real-time transcription
  silenceStart: null,
  isSegmentTranscribing: false,
  // VAD: true only if RMS exceeded SPEECH_THRESHOLD during this segment
  speechDetected: false,
  // Count of in-flight submitAudioSegment calls — auto-format fires only when 0
  pendingSegments: 0,
};

const SILENCE_THRESHOLD   = 0.01;   // RMS below this = silence
const SPEECH_THRESHOLD    = 0.015;  // RMS above this = speech (just above background hum)
const SILENCE_DURATION_MS = 800;    // 800 ms pause triggers segment
const MIN_SEGMENT_BYTES   = 12000;  // ignore blobs smaller than this (~600 ms of silence encodes to ~8-10 KB)

// ---------------------------------------------------------------------------
// UI helpers
// ---------------------------------------------------------------------------
const $ = (id) => document.getElementById(id);

function setStatus(msg, type = "") {
  const el = $("status");
  el.textContent = msg;
  el.className = type ? `${type}` : "";
}

function setUI(mode) {
  // mode: idle | recording | processing | transcribed | formatting | done
  $("btn-record").disabled      = !["idle", "transcribed", "done"].includes(mode);
  $("btn-stop").disabled        = mode !== "recording";
  $("btn-format").disabled      = !["transcribed", "done"].includes(mode);
  $("btn-copy").disabled        = mode !== "done";
  $("btn-edit-toggle").disabled = mode !== "done";

  const dot = $("rec-dot");
  if (dot) dot.style.display = mode === "recording" ? "inline-block" : "none";

  if (mode === "processing" || mode === "formatting") {
    const spinner = document.createElement("span");
    spinner.className = "spinner";
    spinner.id = "spinner-tmp";
    const existing = $("spinner-tmp");
    if (existing) existing.remove();
    $("status").prepend(spinner);
    $("status").prepend(" ");
  } else {
    const s = $("spinner-tmp");
    if (s) s.remove();
  }
}

// ---------------------------------------------------------------------------
// Timer
// ---------------------------------------------------------------------------
function startTimer() {
  state.timerSeconds = 0;
  $("timer").textContent = "0:00";
  state.timerInterval = setInterval(() => {
    state.timerSeconds++;
    const m = Math.floor(state.timerSeconds / 60);
    const s = String(state.timerSeconds % 60).padStart(2, "0");
    $("timer").textContent = `${m}:${s}`;
  }, 1000);
}

function stopTimer() {
  clearInterval(state.timerInterval);
  state.timerInterval = null;
}

// ---------------------------------------------------------------------------
// Waveform + silence detection (Web Audio API AnalyserNode)
// ---------------------------------------------------------------------------
function getRMS(dataArr) {
  let sum = 0;
  for (let i = 0; i < dataArr.length; i++) {
    const v = (dataArr[i] - 128) / 128.0;
    sum += v * v;
  }
  return Math.sqrt(sum / dataArr.length);
}

function startWaveform(stream) {
  const canvas = $("waveform");
  const ctx = canvas.getContext("2d");

  state.audioCtx = new (window.AudioContext || window.webkitAudioContext)();
  const source = state.audioCtx.createMediaStreamSource(stream);
  state.analyser = state.audioCtx.createAnalyser();
  state.analyser.fftSize = 256;
  source.connect(state.analyser);

  const bufLen = state.analyser.frequencyBinCount;
  const dataArr = new Uint8Array(bufLen);

  function draw() {
    state.animFrameId = requestAnimationFrame(draw);
    state.analyser.getByteTimeDomainData(dataArr);

    // ── VAD + Silence detection ────────────────────────────────────────────
    if (state.isRecording && !state.isSegmentTranscribing) {
      const rms = getRMS(dataArr);
      const now = Date.now();

      // Track whether real speech occurred in this segment
      if (rms >= SPEECH_THRESHOLD) {
        state.speechDetected = true;
      }
      if (rms >= SILENCE_THRESHOLD) {
        state.silenceStart = null;   // any audio above noise floor resets silence timer
      } else {  // rms < SILENCE_THRESHOLD
        if (!state.silenceStart) {
          state.silenceStart = now;
        } else if (now - state.silenceStart >= SILENCE_DURATION_MS) {
          // Only trigger segment if speech was actually detected AND blob is large enough
          const approxBytes = state.audioChunks.reduce((s, c) => s + c.size, 0);
          if (state.speechDetected && approxBytes >= MIN_SEGMENT_BYTES) {
            state.silenceStart = null;
            state.isSegmentTranscribing = true;
            // Stop recorder → onstop will restart it and send segment
            if (state.mediaRecorder && state.mediaRecorder.state !== "inactive") {
              state.mediaRecorder.stop();
            }
          } else if (!state.speechDetected && approxBytes >= MIN_SEGMENT_BYTES * 3) {
            // Long silence with no speech — just discard chunks and restart recorder
            // to keep blob size small and avoid ever sending noise-only audio
            state.silenceStart = null;
            state.audioChunks = [];
            if (state.mediaRecorder && state.mediaRecorder.state !== "inactive") {
              state.mediaRecorder.stop();
            }
          }
        }
      }
    }

    // ── Draw waveform ──────────────────────────────────────────────────────
    const W = canvas.width, H = canvas.height;
    ctx.clearRect(0, 0, W, H);
    ctx.fillStyle = "#0E1118";
    ctx.fillRect(0, 0, W, H);

    ctx.strokeStyle = "#FFD700";
    ctx.lineWidth = 1.5;
    ctx.beginPath();

    const sliceW = W / bufLen;
    let x = 0;
    for (let i = 0; i < bufLen; i++) {
      const v = dataArr[i] / 128.0;
      const y = (v * H) / 2;
      i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
      x += sliceW;
    }
    ctx.lineTo(W, H / 2);
    ctx.stroke();
  }
  draw();
}

function stopWaveform() {
  cancelAnimationFrame(state.animFrameId);
  state.animFrameId = null;
  if (state.audioCtx) {
    state.audioCtx.close().catch(() => {});
    state.audioCtx = null;
    state.analyser = null;
  }
  const canvas = $("waveform");
  const ctx = canvas.getContext("2d");
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.fillStyle = "#0E1118";
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  ctx.strokeStyle = "#FFD700";
  ctx.lineWidth = 1.5;
  ctx.beginPath();
  ctx.moveTo(0, canvas.height / 2);
  ctx.lineTo(canvas.width, canvas.height / 2);
  ctx.stroke();
}

// ---------------------------------------------------------------------------
// MediaRecorder management
// ---------------------------------------------------------------------------
function _startMediaRecorder() {
  const mimeType = MediaRecorder.isTypeSupported("audio/webm;codecs=opus")
    ? "audio/webm;codecs=opus"
    : "audio/webm";

  state.mediaRecorder = new MediaRecorder(state.stream, { mimeType });
  state.audioChunks = [];
  state.speechDetected = false;  // reset VAD flag for new segment
  state.silenceStart = null;

  state.mediaRecorder.ondataavailable = (e) => {
    if (e.data && e.data.size > 0) state.audioChunks.push(e.data);
  };

  state.mediaRecorder.onstop = () => {
    const chunks = state.audioChunks.splice(0); // take and clear
    const isFinal = !state.isRecording;
    const hadSpeech = state.speechDetected;

    if (state.isRecording) {
      // Silence-triggered segment — restart recorder immediately so the mic
      // stays active while we send this segment in the background.
      _startMediaRecorder();
    } else {
      // User clicked Stop — release the microphone.
      if (state.stream) {
        state.stream.getTracks().forEach((t) => t.stop());
        state.stream = null;
      }
    }

    // VAD gate: only send to Whisper if speech was detected in this segment
    if (!isFinal && !hadSpeech) {
      state.isSegmentTranscribing = false;
      return; // discard silent segment silently
    }

    submitAudioSegment(chunks, isFinal);
  };

  state.mediaRecorder.start(250);
}

// ---------------------------------------------------------------------------
// Recording
// ---------------------------------------------------------------------------
async function startRecording() {
  try {
    state.stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    startWaveform(state.stream);
    state.silenceStart = null;
    state.isSegmentTranscribing = false;
    state.speechDetected = false;
    state.pendingSegments = 0;
    _startMediaRecorder();
    state.isRecording = true;
    startTimer();
    setUI("recording");
    setStatus("Recording… pause briefly to see live transcription.", "active");
  } catch (err) {
    setStatus(
      `Microphone access denied: ${err.message}. Check browser permissions.`,
      "error"
    );
  }
}

function stopRecording() {
  if (!state.isRecording) return;
  state.isRecording = false;
  stopTimer();
  stopWaveform();
  setUI("processing");
  setStatus("Processing remaining audio…", "active");

  if (state.mediaRecorder && state.mediaRecorder.state !== "inactive") {
    state.mediaRecorder.stop(); // onstop handles stream teardown + final transcription
  } else {
    // Edge case: recorder already stopped mid-silence-segment restart.
    // Stream cleanup happened (or will happen) in that onstop. Just update UI.
    if (state.stream) {
      state.stream.getTracks().forEach((t) => t.stop());
      state.stream = null;
    }
    state.isSegmentTranscribing = false;
    // Defer to _maybeAutoFormat — a mid-recording segment may still be in-flight
    _maybeAutoFormat();
  }
}

// ---------------------------------------------------------------------------
// Auto-format gate: fire only when recording stopped AND no segments in-flight
// ---------------------------------------------------------------------------
function _maybeAutoFormat() {
  if (state.isRecording || state.pendingSegments > 0) return;
  const hasText = $("transcription").value.trim();
  if (hasText) {
    setUI("transcribed");
    formatReport();
  } else {
    setUI("idle");
    setStatus("No speech detected.", "error");
  }
}

// ---------------------------------------------------------------------------
// Transcribe segment (called from onstop for both mid-recording and final)
// ---------------------------------------------------------------------------
async function submitAudioSegment(chunks, isFinal) {
  state.pendingSegments++;
  const blob = new Blob(chunks, { type: "audio/webm" });

  if (blob.size < MIN_SEGMENT_BYTES) {
    // Too small — silence or noise only, nothing to transcribe.
    state.isSegmentTranscribing = false;
    state.pendingSegments--;
    _maybeAutoFormat();
    return;
  }

  const formData = new FormData();
  formData.append("audio", blob, "segment.webm");
  const templateName = $("template-select").value;
  if (templateName) formData.append("template_name", templateName);

  try {
    const resp = await fetch("/transcribe", { method: "POST", body: formData });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({ detail: resp.statusText }));
      setStatus(`Transcription error: ${err.detail}`, "error");
      return;
    }
    const data = await resp.json();
    if (data.session_id) state.sessionId = data.session_id;

    const newText = data.transcription ? data.transcription.trim() : "";
    if (newText) {
      const existing = $("transcription").value.trim();
      $("transcription").value = existing ? existing + " " + newText : newText;
    }

    if (state.isRecording) {
      // Mid-recording segment done — recorder already restarted
      setStatus("Recording… pause briefly to see live transcription.", "active");
    }
  } catch (err) {
    setStatus(`Transcription error: ${err.message}`, "error");
  } finally {
    state.isSegmentTranscribing = false;
    state.pendingSegments--;
    _maybeAutoFormat();
  }
}

// ---------------------------------------------------------------------------
// FHIR patient lookup — populates patient context fields from RIS
// ---------------------------------------------------------------------------
async function lookupPatient() {
  const accession = $("accession").value.trim();
  if (!accession) {
    setStatus("Enter an accession number first.", "error");
    return;
  }
  const btn = $("btn-lookup");
  btn.disabled = true;
  const prevText = btn.textContent;
  btn.textContent = "…";
  try {
    const resp = await fetch(`/patient/${encodeURIComponent(accession)}`);
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({ detail: resp.statusText }));
      setStatus(`FHIR lookup: ${err.detail}`, "error");
      return;
    }
    const data = await resp.json();
    if (data.patient_name)        $("patient-name").value        = data.patient_name;
    if (data.patient_dob)         $("patient-dob").value         = data.patient_dob;
    if (data.patient_id)          $("patient-id").value          = data.patient_id;
    if (data.modality)            $("modality").value            = data.modality;
    if (data.body_part)           $("body-part").value           = data.body_part;
    if (data.referring_physician) $("referring-physician").value = data.referring_physician;
    setStatus("Patient details populated from FHIR RIS.", "success");
  } catch (err) {
    setStatus(`FHIR lookup failed: ${err.message}`, "error");
  } finally {
    btn.disabled = false;
    btn.textContent = prevText;
  }
}

// ---------------------------------------------------------------------------
// Format — streaming SSE
// ---------------------------------------------------------------------------
async function formatReport() {
  const transcription = $("transcription").value.trim();
  if (!transcription) {
    setStatus("No transcription to format.", "error");
    return;
  }

  const body = {
    transcription,
    template_name: $("template-select").value || null,
    session_id: state.sessionId || null,
    patient_name:         $("patient-name").value.trim()        || null,
    patient_dob:          $("patient-dob").value.trim()         || null,
    patient_id:           $("patient-id").value.trim()          || null,
    accession:            $("accession").value.trim()            || null,
    modality:             $("modality").value.trim()             || null,
    body_part:            $("body-part").value.trim()            || null,
    referring_physician:  $("referring-physician").value.trim()  || null,
    radiologist:          $("radiologist").value.trim()          || null,
  };

  setUI("formatting");
  setStatus("Generating report…", "active");

  // Clear report area and start streaming into it
  $("report-raw").value = "";
  $("report-rendered").innerHTML = "";
  _setReportEditMode(false);

  try {
    const resp = await fetch("/format/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({ detail: resp.statusText }));
      throw new Error(err.detail || resp.statusText);
    }

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let accumulated = "";

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      // Process complete SSE lines
      const lines = buffer.split("\n");
      buffer = lines.pop(); // keep incomplete last line

      for (const line of lines) {
        if (!line.startsWith("data: ")) continue;
        const payload = line.slice(6).trim();
        if (!payload) continue;
        let msg;
        try { msg = JSON.parse(payload); } catch { continue; }

        if (msg.token) {
          accumulated += msg.token;
          // Re-render the markdown live as tokens arrive
          $("report-raw").value = accumulated;
          $("report-rendered").innerHTML = marked.parse(accumulated);
        } else if (msg.done) {
          const fhirNote = msg.fhir_saved ? " · FHIR R4 JSON saved." : "";
          setUI("done");
          setStatus("Report ready." + fhirNote, "success");
          $("report-rendered").scrollIntoView({ behavior: "smooth", block: "start" });
        } else if (msg.error) {
          throw new Error(msg.error);
        }
      }
    }
  } catch (err) {
    setUI("transcribed");
    setStatus(`Format error: ${err.message}`, "error");
  }
}

// ---------------------------------------------------------------------------
// Report: render markdown + edit/preview toggle
// ---------------------------------------------------------------------------
let _reportEditMode = false;

function setReport(markdown) {
  $("report-raw").value = markdown;
  $("report-rendered").innerHTML = marked.parse(markdown);
  if (_reportEditMode) _setReportEditMode(false); // always start in preview
}

function _setReportEditMode(editing) {
  _reportEditMode = editing;
  $("report-rendered").style.display = editing ? "none" : "";
  $("report-raw").style.display      = editing ? "" : "none";
  $("btn-edit-toggle").textContent   = editing ? "✓ Done" : "✎ Edit";
}

function toggleReportEdit() {
  if (!_reportEditMode) {
    _setReportEditMode(true);
  } else {
    // Switching back to preview — re-render any edits
    $("report-rendered").innerHTML = marked.parse($("report-raw").value);
    _setReportEditMode(false);
  }
}

// ---------------------------------------------------------------------------
// Copy report
// ---------------------------------------------------------------------------
async function copyReport() {
  const text = $("report-raw").value;
  if (!text.trim()) return;
  try {
    await navigator.clipboard.writeText(text);
    setStatus("Report copied to clipboard.", "success");
  } catch {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    document.execCommand("copy");
    ta.remove();
    setStatus("Report copied to clipboard.", "success");
  }
}

// ---------------------------------------------------------------------------
// Canvas resize observer
// ---------------------------------------------------------------------------
function initCanvasResize() {
  const canvas = $("waveform");
  const ro = new ResizeObserver(() => {
    canvas.width = canvas.clientWidth;
    canvas.height = canvas.clientHeight;
    if (!state.isRecording) stopWaveform();
  });
  ro.observe(canvas);
  canvas.width = canvas.clientWidth;
  canvas.height = canvas.clientHeight;
  stopWaveform();
}

// ---------------------------------------------------------------------------
// Boot
// ---------------------------------------------------------------------------
document.addEventListener("DOMContentLoaded", () => {
  initCanvasResize();
  setUI("idle");
  setStatus("Press Record to start dictating.");

  $("btn-record").addEventListener("click", startRecording);
  $("btn-stop").addEventListener("click", stopRecording);
  $("btn-format").addEventListener("click", formatReport);
  $("btn-copy").addEventListener("click", copyReport);
  $("btn-edit-toggle").addEventListener("click", toggleReportEdit);
  $("btn-lookup").addEventListener("click", lookupPatient);
});
