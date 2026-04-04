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
  // Streaming STT state
  streamingSupported: false,
  streamingWs: null,
  streamingWorkletNode: null,
  streamingAudioCtx: null,
  confirmedText: "",
  interimText: "",
  // Cursor-aware insertion: text before/after the insert point when recording started.
  // Updated live as the user moves the cursor during streaming.
  streamingBefore: "",
  streamingAfter: "",
  streamingAnchorPos: 0,   // cursor position we last set programmatically
  // Voice editing: {el, start, end, selectedText} — used for segment (non-streaming) mode
  voiceEditTarget: null,
};

// Suppress our own programmatic cursor moves from triggering the selectionchange handler.
let _suppressStreamingSelChange = false;

const SILENCE_THRESHOLD   = 0.01;   // RMS below this = silence
const SPEECH_THRESHOLD    = 0.015;  // RMS above this = speech
const SILENCE_DURATION_MS = 800;    // 800 ms pause triggers segment
const MIN_SEGMENT_BYTES   = 12000;  // ignore blobs smaller than this

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

      if (rms >= SPEECH_THRESHOLD) {
        state.speechDetected = true;
      }
      if (rms >= SILENCE_THRESHOLD) {
        state.silenceStart = null;
      } else {
        if (!state.silenceStart) {
          state.silenceStart = now;
        } else if (now - state.silenceStart >= SILENCE_DURATION_MS) {
          const approxBytes = state.audioChunks.reduce((s, c) => s + c.size, 0);
          if (state.speechDetected && approxBytes >= MIN_SEGMENT_BYTES) {
            state.silenceStart = null;
            state.isSegmentTranscribing = true;
            if (state.mediaRecorder && state.mediaRecorder.state !== "inactive") {
              state.mediaRecorder.stop();
            }
          } else if (!state.speechDetected && approxBytes >= MIN_SEGMENT_BYTES * 3) {
            state.silenceStart = null;
            state.audioChunks = [];
            if (state.mediaRecorder && state.mediaRecorder.state !== "inactive") {
              state.mediaRecorder.stop();
            }
          }
        }
      }
    }

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
    const isVoiceEdit = !!state.voiceEditTarget;
    // Voice edit always treats the segment as final (one utterance, then done).
    const isFinal = !state.isRecording || isVoiceEdit;
    const hadSpeech = state.speechDetected;

    if (state.isRecording && !isVoiceEdit) {
      // Silence-triggered segment — restart recorder immediately so the mic
      // stays active while we send this segment in the background.
      _startMediaRecorder();
    } else {
      // Final segment or voice edit: release the microphone.
      if (isVoiceEdit && state.isRecording) {
        state.isRecording = false;
        stopTimer();
        stopWaveform();
      }
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
// Recording — branches to voice-edit, streaming, or segment mode
// ---------------------------------------------------------------------------
async function startRecording() {
  if (state.isRecording) return; // Stop button ends recording
  if (state.streamingSupported) {
    await startStreamingRecording();
  } else if (state.voiceEditTarget) {
    await startVoiceEditRecording();
  } else {
    await startSegmentRecording();
  }
}

// Voice edit: record one utterance, replace the saved textarea selection.
async function startVoiceEditRecording() {
  // If the previous streaming session's WebSocket is still open waiting for
  // session_complete, close it now. Otherwise, when session_complete arrives
  // mid-voice-edit, _cleanupStreaming() would kill the voice edit microphone.
  if (state.streamingWs) {
    state.streamingWs.close();
    state.streamingWs = null;
  }
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
    const label = state.voiceEditTarget.el.id === "transcription" ? "transcript" : "report";
    setStatus(`Voice editing ${label} — dictate replacement, then pause.`, "active");
  } catch (err) {
    state.voiceEditTarget = null;
    setStatus(`Microphone access denied: ${err.message}`, "error");
  }
}

async function startSegmentRecording() {
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

// ---------------------------------------------------------------------------
// Streaming STT recording
// ---------------------------------------------------------------------------
async function startStreamingRecording() {
  // Capture cursor position / selection BEFORE the mic opens.
  // This makes streaming PowerScribe-style: text is inserted at the cursor,
  // replacing any selection, rather than always appending to the end.
  const tx = $("transcription");
  if (state.voiceEditTarget && state.voiceEditTarget.el === tx) {
    state.streamingBefore = tx.value.slice(0, state.voiceEditTarget.start);
    state.streamingAfter  = tx.value.slice(state.voiceEditTarget.end);
  } else {
    // No selection — default: append after existing transcript.
    state.streamingBefore = tx.value;
    state.streamingAfter  = "";
  }
  state.voiceEditTarget = null;

  const wsToken = document.body.dataset.wsToken || "";
  const proto   = location.protocol === "https:" ? "wss:" : "ws:";
  const wsUrl   = `${proto}//${location.host}/ws/transcribe?token=${encodeURIComponent(wsToken)}`;

  try {
    state.stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch (err) {
    setStatus(`Microphone access denied: ${err.message}. Check browser permissions.`, "error");
    return;
  }

  state.streamingWs = new WebSocket(wsUrl);
  state.streamingWs.binaryType = "arraybuffer";

  state.streamingWs.onopen = async () => {
    // Send config
    state.streamingWs.send(JSON.stringify({
      template_name: $("template-select").value || null,
    }));

    // Set up AudioContext + AudioWorklet
    try {
      state.streamingAudioCtx = new (window.AudioContext || window.webkitAudioContext)();
      await state.streamingAudioCtx.audioWorklet.addModule("/static/pcm-worklet.js");
      const source = state.streamingAudioCtx.createMediaStreamSource(state.stream);
      state.streamingWorkletNode = new AudioWorkletNode(state.streamingAudioCtx, "pcm-processor");
      state.streamingWorkletNode.port.onmessage = (e) => {
        if (state.streamingWs && state.streamingWs.readyState === WebSocket.OPEN) {
          state.streamingWs.send(e.data);
        }
      };
      source.connect(state.streamingWorkletNode);
      // Connect to destination to keep AudioContext alive in some browsers
      state.streamingWorkletNode.connect(state.streamingAudioCtx.destination);
    } catch (err) {
      setStatus(`AudioWorklet setup failed: ${err.message}`, "error");
      _cleanupStreaming();
      return;
    }

    // Also start visual waveform using the same stream
    startWaveform(state.stream);

    state.confirmedText = "";
    state.interimText   = "";
    state.isRecording   = true;
    startTimer();
    setUI("recording");
    const sdot = $("streaming-dot");
    if (sdot) sdot.style.display = "inline-block";
    setStatus("Streaming STT active — words appear in real time.", "active");
  };

  state.streamingWs.onmessage = (e) => handleStreamingMessage(JSON.parse(e.data));

  state.streamingWs.onerror = (e) => {
    setStatus("Streaming connection error.", "error");
    _cleanupStreaming();
  };

  state.streamingWs.onclose = (e) => {
    if (e.code === 4001) {
      setStatus("Streaming auth failed. Please reload.", "error");
    }
  };
}

function handleStreamingMessage(msg) {
  switch (msg.type) {
    case "interim":
      state.interimText = (msg.text || "").replace(/\s*—\s*/g, " ").trim();
      _updateStreamingDisplay();
      break;
    case "final": {
      const chunk = (msg.text || "").replace(/\s*—\s*/g, " ").trim();
      state.confirmedText = state.confirmedText
        ? state.confirmedText + (chunk ? " " + chunk : "")
        : chunk;
      state.interimText = "";
      _updateStreamingDisplay();
      break;
    }
    case "session_complete": {
      if (msg.session_id) state.sessionId = msg.session_id;
      const speech = (msg.transcription || "").replace(/\s*—\s*/g, " ").replace(/\s+/g, " ").trim();
      const before = state.streamingBefore;
      const after  = state.streamingAfter;
      const sep1 = (before && !/\s$/.test(before) && speech) ? " " : "";
      const sep2 = (after  && !/^\s/.test(after)  && speech) ? " " : "";
      $("transcription").value = before + sep1 + speech + sep2 + after;
      _cleanupStreaming();
      if (speech || before || after) {
        setUI("transcribed");
        if (speech) formatReport();
      } else {
        setUI("idle");
        setStatus("No speech detected.", "error");
      }
      break;
    }
    case "error":
      setStatus(`Streaming error: ${msg.message}`, "error");
      _cleanupStreaming();
      setUI("idle");
      break;
    default:
      break;
  }
}

function _updateStreamingDisplay() {
  const tx      = $("transcription");
  const before  = state.streamingBefore;
  const after   = state.streamingAfter;
  const confirmed = state.confirmedText;
  // Only show interim when inserting at the end of the text (no after-text).
  // This keeps the display equal to "stableText" during mid-text insertion,
  // so cursor positions map 1-to-1 without any offset arithmetic.
  const speech  = after
    ? confirmed
    : confirmed + (state.interimText ? (confirmed ? " " : "") + state.interimText : "");
  const sep1 = (before && !/\s$/.test(before) && speech) ? " " : "";
  const sep2 = (after  && !/^\s/.test(after)  && speech) ? " " : "";
  const newValue  = before + sep1 + speech + sep2 + after;
  // Anchor: right after confirmed text (interim trails the cursor; after-text follows).
  const anchorPos = before.length + sep1.length + confirmed.length;

  _suppressStreamingSelChange = true;
  tx.value = newValue;
  tx.selectionStart = tx.selectionEnd = anchorPos;
  state.streamingAnchorPos = anchorPos;
  // Release the suppress flag after the browser has dispatched any
  // selectionchange events queued by the value/cursor assignment above.
  setTimeout(() => { _suppressStreamingSelChange = false; }, 0);
}

function _cleanupStreaming() {
  const sdot = $("streaming-dot");
  if (sdot) sdot.style.display = "none";

  if (state.streamingWorkletNode) {
    try { state.streamingWorkletNode.disconnect(); } catch (_) {}
    state.streamingWorkletNode = null;
  }
  if (state.streamingAudioCtx) {
    state.streamingAudioCtx.close().catch(() => {});
    state.streamingAudioCtx = null;
  }
  if (state.stream) {
    state.stream.getTracks().forEach((t) => t.stop());
    state.stream = null;
  }
  if (state.streamingWs) {
    if (state.streamingWs.readyState === WebSocket.OPEN ||
        state.streamingWs.readyState === WebSocket.CONNECTING) {
      state.streamingWs.close();
    }
    state.streamingWs = null;
  }
  state.isRecording   = false;
  state.confirmedText   = "";
  state.interimText     = "";
  state.streamingBefore = "";
  state.streamingAfter  = "";
  state.streamingAnchorPos = 0;
}

function stopRecording() {
  if (!state.isRecording) return;

  if (state.streamingSupported && state.streamingWs) {
    stopStreamingRecording();
    return;
  }

  // Segment (Groq Whisper) path
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

function stopStreamingRecording() {
  state.isRecording = false;
  stopTimer();
  stopWaveform();
  setUI("processing");
  setStatus("Processing final audio…", "active");

  // Disconnect worklet so no more audio frames are sent
  if (state.streamingWorkletNode) {
    try { state.streamingWorkletNode.disconnect(); } catch (_) {}
  }

  // Tell server to flush and finalize
  if (state.streamingWs && state.streamingWs.readyState === WebSocket.OPEN) {
    state.streamingWs.send(JSON.stringify({ type: "stop" }));
  } else {
    // WS already closed; treat as no speech
    _cleanupStreaming();
    setUI("idle");
    setStatus("No speech detected.", "error");
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
  // Capture voice edit target at call time — async gaps could clear state later.
  const editTarget = isFinal ? state.voiceEditTarget : null;

  state.pendingSegments++;
  const blob = new Blob(chunks, { type: "audio/webm" });

  if (blob.size < MIN_SEGMENT_BYTES) {
    state.isSegmentTranscribing = false;
    state.pendingSegments--;
    if (!editTarget) _maybeAutoFormat();
    else { state.voiceEditTarget = null; setUI(_inferUIMode()); setStatus("No speech detected — edit unchanged.", "error"); }
    return;
  }

  const formData = new FormData();
  formData.append("audio", blob, "segment.webm");
  if (editTarget) {
    // Voice edit: pass the text before the selection as Whisper context rather
    // than the vocabulary list — prevents Whisper hallucinating prompt completions.
    const before = editTarget.el.value.slice(
      Math.max(0, editTarget.start - 300), editTarget.start
    ).trim();
    formData.append("whisper_prompt", before);
  } else {
    const templateName = $("template-select").value;
    if (templateName) formData.append("template_name", templateName);
  }

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

    if (editTarget) {
      // Voice edit: splice transcription into the selection.
      const { el, start, end } = editTarget;
      el.value = el.value.slice(0, start) + (newText || "") + el.value.slice(end);
      if (newText) {
        el.selectionStart = el.selectionEnd = start + newText.length;
        el.focus();
      }
      // Re-render markdown if editing the report.
      if (el.id === "report-raw") {
        $("report-rendered").innerHTML = marked.parse(el.value);
      }
      state.voiceEditTarget = null;
      setUI(_inferUIMode());
      setStatus(newText ? "Voice edit applied." : "No speech detected — edit unchanged.",
                newText ? "success" : "error");
    } else {
      if (newText) {
        const existing = $("transcription").value.trim();
        $("transcription").value = existing ? existing + " " + newText : newText;
      }
      if (state.isRecording) {
        setStatus("Recording… pause briefly to see live transcription.", "active");
      }
    }
  } catch (err) {
    setStatus(`Transcription error: ${err.message}`, "error");
  } finally {
    state.isSegmentTranscribing = false;
    state.pendingSegments--;
    if (!editTarget) _maybeAutoFormat();
  }
}

// Infer the correct UI mode from current content after a voice edit.
function _inferUIMode() {
  if ($("report-raw").value.trim()) return "done";
  if ($("transcription").value.trim()) return "transcribed";
  return "idle";
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

      const lines = buffer.split("\n");
      buffer = lines.pop();

      for (const line of lines) {
        if (!line.startsWith("data: ")) continue;
        const payload = line.slice(6).trim();
        if (!payload) continue;
        let msg;
        try { msg = JSON.parse(payload); } catch { continue; }

        if (msg.token) {
          accumulated += msg.token;
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
  if (_reportEditMode) _setReportEditMode(false);
}

function _setReportEditMode(editing) {
  _reportEditMode = editing;
  $("report-rendered").style.display = editing ? "none" : "";
  $("report-raw").style.display      = editing ? "" : "none";
  $("btn-edit-toggle").textContent   = editing ? "✓ Done" : "✎ Edit";
  const hint = $("report-edit-hint");
  if (hint) hint.style.display = editing ? "" : "none";
}

function toggleReportEdit() {
  if (!_reportEditMode) {
    _setReportEditMode(true);
  } else {
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
document.addEventListener("DOMContentLoaded", async () => {
  initCanvasResize();
  setUI("idle");
  setStatus("Press Record to start dictating.");

  // Track the last known selection in the transcription/report textareas.
  // _pendingSelection is updated continuously on selection events, and cleared:
  //   - when the textarea loses focus to anywhere except the Record button
  //   - after it's consumed by the Record button handler
  // This is more reliable than reading at click/mousedown time because pointerdown
  // fires before blur in all browsers, and we save selection state as the user makes it.
  let _pendingSelection = null;
  ["transcription", "report-raw"].forEach(id => {
    const el = $(id);
    if (!el) return;
    const save = () => {
      const s = el.selectionStart, e = el.selectionEnd;
      _pendingSelection = (s !== e) ? { el, start: s, end: e } : null;
    };
    el.addEventListener("mouseup", save);
    el.addEventListener("select", save);
    el.addEventListener("keyup", save);
    // Clear if focus leaves for anything other than the Record button.
    // In Safari on macOS, <button> elements don't receive focus on click,
    // so relatedTarget will be null — but pointerdown fires BEFORE blur,
    // so _pendingSelection has already been consumed by then.
    el.addEventListener("blur", (evt) => {
      if (!evt.relatedTarget || evt.relatedTarget.id !== "btn-record") {
        _pendingSelection = null;
      }
    });
  });

  // pointerdown fires before blur in every browser, so document.activeElement
  // is still the textarea and the selection is still intact when this runs.
  const _grabVoiceEdit = () => {
    const active = document.activeElement;
    const s = active && active.selectionStart !== undefined ? active.selectionStart : null;
    const e = active && active.selectionEnd !== undefined ? active.selectionEnd : null;
    if (active && (active.id === "transcription" || active.id === "report-raw")) {
      if (s !== e) {
        const selectedText = active.value.slice(s, e);
        state.voiceEditTarget = { el: active, start: s, end: e, selectedText };
      } else {
        state.voiceEditTarget = _pendingSelection || null;
      }
    } else {
      state.voiceEditTarget = _pendingSelection || null;
    }
    _pendingSelection = null;
  };

  // Live cursor tracking during streaming: when the user clicks or moves the
  // cursor inside #transcription while streaming is active, immediately resplit
  // the text so the next spoken words appear at the new cursor position.
  // This gives PowerScribe-style behaviour — no button presses needed.
  document.addEventListener("selectionchange", () => {
    if (_suppressStreamingSelChange) return;
    if (!state.streamingWs || !state.isRecording) return;
    const tx = $("transcription");
    if (document.activeElement !== tx) return;

    const displayPos = tx.selectionStart;
    const displayEnd = tx.selectionEnd;
    if (displayPos === state.streamingAnchorPos &&
        displayEnd === state.streamingAnchorPos) return; // nothing changed

    // "Stable text" = what the textarea contains minus any trailing interim word.
    // For mid-text insertion we suppress interim, so stableText === tx.value.
    // For end-of-text, interim is at the tail — strip it.
    const interimSuffix = (!state.streamingAfter && state.interimText)
      ? (state.confirmedText ? " " : "") + state.interimText
      : "";
    const stableLen  = tx.value.length - interimSuffix.length;
    const newPos     = Math.min(displayPos, stableLen);
    const newEnd     = Math.min(displayEnd, stableLen);

    // Resplit: everything up to cursor becomes "before", everything after becomes "after".
    // Confirmed text is now baked into whichever side it falls on.
    const stableText = tx.value.slice(0, stableLen);
    state.streamingBefore = stableText.slice(0, newPos);
    state.streamingAfter  = stableText.slice(newEnd);
    state.confirmedText   = "";
    state.interimText     = "";

    _suppressStreamingSelChange = true;
    // Only strip trailing interim — don't erase the selected text.
    // The highlight stays visible until speech arrives and _updateStreamingDisplay
    // replaces it with streamingBefore + new speech + streamingAfter.
    if (interimSuffix) {
      tx.value = tx.value.slice(0, stableLen);
    }
    tx.selectionStart = newPos;
    tx.selectionEnd   = newEnd;
    state.streamingAnchorPos = newPos;
    setTimeout(() => { _suppressStreamingSelChange = false; }, 0);
  });
  // Use pointerdown (handles both mouse and touch, fires earliest in event chain).
  $("btn-record").addEventListener("pointerdown", _grabVoiceEdit, { passive: true });
  $("btn-record").addEventListener("click", startRecording);
  $("btn-stop").addEventListener("click", stopRecording);
  $("btn-format").addEventListener("click", formatReport);
  $("btn-copy").addEventListener("click", copyReport);
  $("btn-edit-toggle").addEventListener("click", toggleReportEdit);
  $("btn-lookup").addEventListener("click", lookupPatient);

  // Check streaming STT capability
  try {
    const resp = await fetch("/api/capabilities");
    if (resp.ok) {
      const caps = await resp.json();
      state.streamingSupported = !!caps.streaming_stt;
      if (state.streamingSupported) {
        setStatus(
          `Press Record to start dictating. (Streaming STT: ${caps.provider || "enabled"})`,
        );
      }
    }
  } catch (_) {
    // Non-critical — fall back to segment mode silently
  }
});
