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
  isPaused: false,
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
  streamingAnchorPos: 0,   // cursor start we last set programmatically
  streamingAnchorEnd: 0,   // cursor end we last set programmatically (for selection highlight)
  streamingSelectedText: "",  // text user selected for replacement (kept visible until speech arrives)
  // Voice editing: {elId, start, end, selectedText} — used for segment (non-streaming) mode
  voiceEditTarget: null,
  // Selection made DURING an active recording session. Set by the selectionchange
  // listener so the VAD/onstop handler can promote it to a voice-edit on the next cut.
  pendingVoiceEditSelection: null,
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
  // mode: idle | recording | paused | processing | transcribed | formatting | done
  const rec = $("btn-record");
  rec.disabled = !["idle", "transcribed", "done", "recording", "paused"].includes(mode);
  if (mode === "recording") {
    rec.innerHTML = '<span>&#10073;&#10073;</span> Pause';
  } else if (mode === "paused") {
    rec.innerHTML = '<span>&#9654;</span> Resume';
  } else {
    rec.innerHTML = '<span>&#9654;</span> Record';
  }
  $("btn-stop").disabled        = !["recording", "paused"].includes(mode);
  $("btn-format").disabled      = !["transcribed", "done"].includes(mode);
  $("btn-copy").disabled        = mode !== "done";
  $("btn-edit-toggle").disabled = mode !== "done";
  $("btn-refine").disabled      = mode !== "done" || fbState.isRecording;
  const refineHint = $("report-refine-hint");
  if (refineHint) refineHint.style.display = mode === "done" ? "" : "none";

  const dot = $("rec-dot");
  if (dot) dot.style.display = (mode === "recording" || mode === "paused") ? "inline-block" : "none";
  if (dot) dot.style.opacity = mode === "paused" ? "0.4" : "1";

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
    if (state.isRecording && !state.isPaused && !state.isSegmentTranscribing) {
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
    const hadSpeech = state.speechDetected;

    // Promote a mid-recording selection ONLY when this segment had real speech
    // — i.e. the user actually said the replacement. A silent VAD cut (user
    // paused to think after selecting) must NOT consume the selection or end
    // recording; we keep both alive so the NEXT cut (with their replacement
    // utterance) does the splice.
    const promoteNow = !state.voiceEditTarget && state.pendingVoiceEditSelection && hadSpeech;
    if (promoteNow) {
      // Mark this voice-edit as mid-recording so submitAudioSegment knows to
      // keep the UI in "recording" mode after splicing.
      state.voiceEditTarget = { ...state.pendingVoiceEditSelection, keepRecording: true };
      state.pendingVoiceEditSelection = null;
      console.log("[voice-edit] promoted mid-recording selection to voice-edit:",
        JSON.stringify(state.voiceEditTarget));
    }

    const isVoiceEdit = !!state.voiceEditTarget;
    // Voice edit always treats the segment as final (one utterance, then done).
    const isFinal = !state.isRecording || isVoiceEdit;
    // Mid-recording promotion: the user wants to keep dictating after the
    // splice (PowerScribe style). Restart the mediaRecorder so the mic stays
    // hot while we send the voice-edit segment to the server.
    const keepRecording = isVoiceEdit && state.voiceEditTarget.keepRecording && state.isRecording;

    if ((state.isRecording && !isVoiceEdit) || keepRecording) {
      // Silence-triggered segment OR mid-recording voice-edit — restart
      // recorder immediately so the mic stays active.
      _startMediaRecorder();
    } else {
      // Final segment or explicit voice-edit (started with selection + Record):
      // release the microphone.
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
// Record button: starts recording when idle, toggles pause/resume while active.
async function onRecordClick() {
  if (state.isRecording) {
    if (state.isPaused) resumeRecording();
    else pauseRecording();
    return;
  }
  return startRecording();
}

function pauseRecording() {
  if (!state.isRecording || state.isPaused) return;
  state.isPaused = true;
  // Groq segment mode: pause the MediaRecorder so no more audio is collected
  // and the VAD silence-cut doesn't fire. Streaming mode handles pause via
  // the worklet sending zero-filled PCM in place of real audio.
  if (state.mediaRecorder && state.mediaRecorder.state === "recording") {
    try { state.mediaRecorder.pause(); } catch (_) {}
  }
  state.silenceStart = null;
  setUI("paused");
  setStatus("Paused — press Resume to continue.", "");
}

function resumeRecording() {
  if (!state.isRecording || !state.isPaused) return;
  state.isPaused = false;
  if (state.mediaRecorder && state.mediaRecorder.state === "paused") {
    try { state.mediaRecorder.resume(); } catch (_) {}
  }
  state.silenceStart = null;
  setUI("recording");
  setStatus(state.streamingWs
    ? "Streaming STT active — words appear in real time."
    : "Recording… pause briefly to see live transcription.", "active");
}

async function startRecording() {
  if (state.isRecording) return; // Stop button ends recording
  state.isPaused = false;

  // Belt-and-suspenders: re-read selections at click time.
  // _grabVoiceEdit() on pointerdown is the primary path; this catches keyboard
  // activation and cross-browser edge cases.
  if (!state.voiceEditTarget) {
    // Check report-rendered div first (non-textarea, survives button click)
    const sel = window.getSelection();
    if (sel && !sel.isCollapsed) {
      const reportDiv = $("report-rendered");
      if (reportDiv && reportDiv.contains(sel.anchorNode)) {
        const selectedText = sel.toString().trim();
        if (selectedText) {
          state.voiceEditTarget = { elId: "report-rendered", selectedText };
          console.log("[voice-edit] captured rendered (belt):", JSON.stringify(selectedText));
        }
      }
    }
  }
  if (!state.voiceEditTarget) {
    for (const id of ["transcription", "report-raw"]) {
      const _el = $(id);
      if (_el && _el.selectionStart !== _el.selectionEnd) {
        state.voiceEditTarget = {
          elId: id,
          start: _el.selectionStart,
          end: _el.selectionEnd,
          selectedText: _el.value.slice(_el.selectionStart, _el.selectionEnd),
        };
        console.log("[voice-edit] captured (belt):", id, _el.selectionStart, _el.selectionEnd);
        break;
      }
    }
  }
  console.log("[voice-edit] startRecording — voiceEditTarget:",
    state.voiceEditTarget ? JSON.stringify({
      elId: state.voiceEditTarget.elId,
      start: state.voiceEditTarget.start,
      end: state.voiceEditTarget.end,
      text: state.voiceEditTarget.selectedText,
    }) : "null",
    "streamingSupported:", state.streamingSupported);

  if (state.voiceEditTarget) {
    // Rendered-report edits must use segment mode — no textarea cursor position
    // exists, so streaming insertion doesn't apply.
    if (state.voiceEditTarget.elId === "report-rendered") {
      await startVoiceEditRecording();
    } else if (state.streamingSupported) {
      await startStreamingRecording();
    } else {
      await startVoiceEditRecording();
    }
  } else if (state.streamingSupported) {
    await startStreamingRecording();
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
    const label = state.voiceEditTarget.elId === "transcription" ? "transcript" : "report";
    setStatus(`Voice editing ${label} (replacing "${state.voiceEditTarget.selectedText}") — dictate replacement, then pause.`, "active");
  } catch (err) {
    state.voiceEditTarget = null;
    setStatus(`Microphone access denied: ${err.message}`, "error");
  }
}

async function startSegmentRecording() {
  state.pendingVoiceEditSelection = null; // fresh session — don't inherit old selection
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
    setStatus("Recording… pause briefly to see live transcription. Select any word to voice-edit it.", "active");
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
  // If a previous streaming session's WebSocket is still open (e.g. server was
  // slow to send session_complete), detach its handlers and close it now so
  // stale messages can't call _cleanupStreaming() and destroy this new session.
  if (state.streamingWs) {
    const oldWs = state.streamingWs;
    state.streamingWs = null;
    oldWs.onopen    = null;  // prevent stale onopen from firing if connection was still pending
    oldWs.onmessage = null;
    oldWs.onerror   = null;
    oldWs.onclose   = null;
    try { oldWs.close(); } catch (_) {}
  }

  // Capture cursor position / selection BEFORE the mic opens.
  // This makes streaming PowerScribe-style: text is inserted at the cursor,
  // replacing any selection, rather than always appending to the end.
  const tx = $("transcription");
  if (state.voiceEditTarget && state.voiceEditTarget.elId === "transcription") {
    state.streamingBefore = tx.value.slice(0, state.voiceEditTarget.start);
    state.streamingAfter  = tx.value.slice(state.voiceEditTarget.end);
    state.streamingSelectedText = state.voiceEditTarget.selectedText || "";
  } else {
    // No selection — default: append after existing transcript.
    state.streamingBefore = tx.value;
    state.streamingAfter  = "";
    state.streamingSelectedText = "";   // always clear stale selection from any prior session
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
      // Guard: if _cleanupStreaming() was called by onerror during the await above
      // (e.g. Deepgram rejected the connection), streamingAudioCtx is now null.
      // The error status is already set; exit cleanly without crashing.
      if (!state.streamingAudioCtx || !state.stream) {
        stopTimer();
        stopWaveform();
        setUI("idle");
        return;
      }
      const source = state.streamingAudioCtx.createMediaStreamSource(state.stream);
      state.streamingWorkletNode = new AudioWorkletNode(state.streamingAudioCtx, "pcm-processor");
      state.streamingWorkletNode.port.onmessage = (e) => {
        if (!state.streamingWs || state.streamingWs.readyState !== WebSocket.OPEN) return;
        if (state.isPaused) {
          // Send zero-filled PCM of the same length so the provider's session
          // stays alive during a pause but sees only silence.
          state.streamingWs.send(new ArrayBuffer(e.data.byteLength));
          return;
        }
        state.streamingWs.send(e.data);
      };
      source.connect(state.streamingWorkletNode);
      // Connect to destination to keep AudioContext alive in some browsers
      state.streamingWorkletNode.connect(state.streamingAudioCtx.destination);
    } catch (err) {
      setStatus(`AudioWorklet setup failed: ${err.message}`, "error");
      _cleanupStreaming();
      stopTimer();
      stopWaveform();
      setUI("idle");
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
      // Use client-tracked confirmed text — the server's full transcription is
      // chronologically ordered and doesn't account for cursor repositioning.
      // Previous confirmed text is already baked into streamingBefore/After.
      state.streamingSelectedText = "";
      const speech = state.confirmedText;
      const before = state.streamingBefore;
      const after  = state.streamingAfter;
      const sep1 = (before && !/\s$/.test(before) && speech) ? " " : "";
      // Don't insert a space before punctuation (e.g. after voice-editing a word
      // mid-sentence, `after` starts with "." or "," — no space needed).
      const sep2 = (after && !/^\s/.test(after) && !/^[.,;:!?)]/.test(after) && speech) ? " " : "";
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

  // If user selected text for replacement and no speech has arrived yet,
  // re-display the selected text with its highlight preserved.
  if (!speech && state.streamingSelectedText) {
    const sel = state.streamingSelectedText;
    const newValue = before + sel + after;
    const selStart = before.length;
    const selEnd   = selStart + sel.length;
    _suppressStreamingSelChange = true;
    tx.value = newValue;
    tx.selectionStart = selStart;
    tx.selectionEnd   = selEnd;
    state.streamingAnchorPos = selStart;
    state.streamingAnchorEnd = selEnd;
    setTimeout(() => { _suppressStreamingSelChange = false; }, 0);
    return;
  }
  // Clear selected text once speech arrives to replace it
  if (speech && state.streamingSelectedText) {
    state.streamingSelectedText = "";
  }

  const sep1 = (before && !/\s$/.test(before) && speech) ? " " : "";
  const sep2 = (after && !/^\s/.test(after) && !/^[.,;:!?)]/.test(after) && speech) ? " " : "";
  const newValue  = before + sep1 + speech + sep2 + after;
  // Anchor: right after confirmed text (interim trails the cursor; after-text follows).
  const anchorPos = before.length + sep1.length + confirmed.length;

  _suppressStreamingSelChange = true;
  tx.value = newValue;
  tx.selectionStart = tx.selectionEnd = anchorPos;
  state.streamingAnchorPos = anchorPos;
  state.streamingAnchorEnd = anchorPos;
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
  state.isPaused      = false;
  state.confirmedText   = "";
  state.interimText     = "";
  state.streamingBefore = "";
  state.streamingAfter  = "";
  state.streamingAnchorPos = 0;
  state.streamingAnchorEnd = 0;
  state.streamingSelectedText = "";
}

function stopRecording() {
  if (!state.isRecording) return;

  // If we stopped while paused, resume the MediaRecorder first so its onstop
  // fires with any buffered audio. Clear the flag so worklet frames stop being
  // zeroed out and the VAD can evaluate normally.
  if (state.isPaused) {
    state.isPaused = false;
    if (state.mediaRecorder && state.mediaRecorder.state === "paused") {
      try { state.mediaRecorder.resume(); } catch (_) {}
    }
  }

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
  console.log("[voice-edit] submitAudioSegment — isFinal:", isFinal,
    "editTarget:", editTarget ? JSON.stringify({ elId: editTarget.elId, start: editTarget.start, end: editTarget.end, text: editTarget.selectedText }) : "null");

  state.pendingSegments++;
  const blob = new Blob(chunks, { type: "audio/webm" });

  if (blob.size < MIN_SEGMENT_BYTES) {
    state.isSegmentTranscribing = false;
    state.pendingSegments--;
    if (!editTarget) {
      _maybeAutoFormat();
    } else {
      const wasMidRecording = !!editTarget.keepRecording;
      state.voiceEditTarget = null;
      if (wasMidRecording && state.isRecording) {
        setStatus("No speech detected — edit unchanged. Keep dictating.", "active");
      } else {
        setUI(_inferUIMode());
        setStatus("No speech detected — edit unchanged.", "error");
      }
    }
    return;
  }

  const formData = new FormData();
  formData.append("audio", blob, "segment.webm");
  if (editTarget) {
    // Voice edit: pass the text before the selection as Whisper context rather
    // than the vocabulary list — prevents Whisper hallucinating prompt completions.
    const el = $(editTarget.elId);
    const before = el.value.slice(
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
    console.log("[voice-edit] /transcribe returned:", JSON.stringify(newText), "editTarget still:", editTarget ? editTarget.elId : "null");

    if (editTarget) {
      if (editTarget.elId === "report-rendered") {
        // Voice edit on the rendered report: find-and-replace in raw markdown.
        const raw = $("report-raw").value;
        const updated = _spliceRenderedEdit(raw, editTarget.selectedText, newText || "");
        state.voiceEditTarget = null;
        if (updated !== null) {
          $("report-raw").value = updated;
          $("report-rendered").innerHTML = marked.parse(updated);
          setUI(_inferUIMode());
          setStatus(newText ? "Voice edit applied." : "No speech detected — edit unchanged.",
                    newText ? "success" : "error");
        } else {
          setUI(_inferUIMode());
          setStatus("Could not locate selected text in report — edit manually.", "error");
        }
      } else {
        // Voice edit on a textarea: splice at selection position.
        const el = $(editTarget.elId);
        const { start, end } = editTarget;
        el.value = el.value.slice(0, start) + (newText || "") + el.value.slice(end);
        if (newText) {
          el.selectionStart = el.selectionEnd = start + newText.length;
          el.focus();
        }
        if (editTarget.elId === "report-raw") {
          $("report-rendered").innerHTML = marked.parse(el.value);
        }
        const wasMidRecording = !!editTarget.keepRecording;
        state.voiceEditTarget = null;
        if (wasMidRecording && state.isRecording) {
          setStatus(newText ? "Voice edit applied — keep dictating."
                            : "No speech detected — edit unchanged. Keep dictating.",
                    "active");
        } else {
          setUI(_inferUIMode());
          setStatus(newText ? "Voice edit applied." : "No speech detected — edit unchanged.",
                    newText ? "success" : "error");
        }
      }
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

// Find selectedText in raw markdown and replace with replacement.
// Returns the updated string, or null if the text was not found.
function _spliceRenderedEdit(raw, selectedText, replacement) {
  // Verbatim match
  const idx = raw.indexOf(selectedText);
  if (idx !== -1) return raw.slice(0, idx) + replacement + raw.slice(idx + selectedText.length);
  // Case-insensitive fallback (capitalisation post-processing may have changed case)
  const lower = raw.toLowerCase();
  const lowerSel = selectedText.toLowerCase();
  const idxLow = lower.indexOf(lowerSel);
  if (idxLow !== -1) return raw.slice(0, idxLow) + replacement + raw.slice(idxLow + selectedText.length);
  return null;
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
// HL7 worklist — pending orders from the RIS inbox
// ---------------------------------------------------------------------------
let _worklistOrders = [];
let _worklistFilter = "";
const _KNOWN_MODALITIES = new Set(["CT", "MR", "US", "XR"]);

function _waitingTime(receivedAt) {
  if (!receivedAt) return "";
  const secs = Math.max(0, Math.floor(Date.now() / 1000 - receivedAt));
  if (secs < 60)     return `${secs}s ago`;
  const mins = Math.floor(secs / 60);
  if (mins < 60)     return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24)      return `${hrs}h ago`;
  return `${Math.floor(hrs / 24)}d ago`;
}

function _worklistLabel(order) {
  const bits = [];
  if (order.modality || order.procedure) {
    bits.push(order.modality ? `${order.modality}${order.body_part ? " " + order.body_part : ""}` : order.procedure);
  }
  if (order.patient_name) bits.push(order.patient_name);
  if (order.accession)    bits.push(`#${order.accession}`);
  const wait = _waitingTime(order.received_at);
  if (wait) bits.push(wait);
  return bits.join(" · ") || order.order_id || "(unnamed order)";
}

function _worklistMatchesFilter(order) {
  if (!_worklistFilter) return true;
  const mod = (order.modality || "").toUpperCase();
  if (_worklistFilter === "OTHER") return !_KNOWN_MODALITIES.has(mod);
  return mod === _worklistFilter;
}

function _renderWorklistOptions() {
  const select = $("worklist-select");
  const count = $("worklist-count");
  if (!select) return;
  const prev = select.value;
  const filtered = _worklistOrders.filter(_worklistMatchesFilter);
  select.innerHTML = '<option value="">Select a pending order…</option>';
  for (const order of filtered) {
    const opt = document.createElement("option");
    opt.value = order.order_id;
    opt.textContent = _worklistLabel(order);
    select.appendChild(opt);
  }
  // Preserve selection across filter changes when the order is still visible.
  if (prev && filtered.some((o) => o.order_id === prev)) {
    select.value = prev;
  } else {
    select.value = "";
    $("btn-worklist-archive").disabled = true;
  }
  const total = _worklistOrders.length;
  if (count) {
    if (_worklistFilter && filtered.length !== total) {
      count.textContent = `(${filtered.length} / ${total})`;
    } else {
      count.textContent = total ? `(${total})` : "(empty)";
    }
  }
}

async function refreshWorklist() {
  const panel = $("worklist-panel");
  const select = $("worklist-select");
  if (!panel || !select) return;
  try {
    const resp = await fetch("/api/hl7/worklist");
    if (!resp.ok) {
      panel.style.display = "none";
      return;
    }
    const data = await resp.json();
    _worklistOrders = data.orders || [];
    // Hide the panel entirely when no inbox is configured and no orders exist.
    if (!data.enabled && _worklistOrders.length === 0) {
      panel.style.display = "none";
      return;
    }
    panel.style.display = "";
    _renderWorklistOptions();
  } catch (err) {
    console.warn("Worklist refresh failed:", err);
  }
}

function _setWorklistFilter(modality) {
  _worklistFilter = modality || "";
  document.querySelectorAll(".worklist-chip").forEach((chip) => {
    chip.classList.toggle("active", (chip.dataset.modality || "") === _worklistFilter);
  });
  _renderWorklistOptions();
}

function applyWorklistOrder() {
  const select = $("worklist-select");
  const archiveBtn = $("btn-worklist-archive");
  if (!select) return;
  const id = select.value;
  archiveBtn.disabled = !id;
  if (!id) return;
  const order = _worklistOrders.find((o) => o.order_id === id);
  if (!order) return;

  const setIfEmpty = (elId, val) => {
    if (!val) return;
    const el = $(elId);
    if (el && !el.value.trim()) el.value = val;
  };
  setIfEmpty("patient-name",         order.patient_name);
  setIfEmpty("patient-dob",          order.patient_dob);
  setIfEmpty("patient-id",           order.patient_id);
  setIfEmpty("accession",            order.accession);
  setIfEmpty("modality",             order.modality);
  setIfEmpty("body-part",            order.body_part);
  setIfEmpty("referring-physician",  order.referring_physician);
  setStatus(`Loaded order ${order.accession || order.order_id}`, "active");
}

async function archiveWorklistOrder() {
  const select = $("worklist-select");
  if (!select || !select.value) return;
  const id = select.value;
  try {
    const resp = await fetch(`/api/hl7/worklist/${encodeURIComponent(id)}/archive`, { method: "POST" });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({ detail: resp.statusText }));
      throw new Error(err.detail || resp.statusText);
    }
    setStatus(`Order ${id} archived.`, "active");
    await refreshWorklist();
  } catch (err) {
    setStatus(`Archive failed: ${err.message}`, "error");
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
          // Replace streamed text with the post-processed version (e.g. capitalisation fixes)
          if (msg.report) {
            $("report-raw").value = msg.report;
            $("report-rendered").innerHTML = marked.parse(msg.report);
          }
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
// Copy report — honors the user's paste_format preference
// ---------------------------------------------------------------------------
function _pasteFormat() {
  return document.body.dataset.pasteFormat || "rich";
}

function _renderedPlainText() {
  // Use the rendered DOM's innerText so headings stay on their own line and
  // bold/italic markers are dropped — what most RIS text fields want.
  const div = $("report-rendered");
  return (div && div.innerText ? div.innerText : $("report-raw").value).trim();
}

async function copyReport() {
  const markdown = $("report-raw").value;
  if (!markdown.trim()) return;
  const fmt = _pasteFormat();
  const plain = _renderedPlainText();
  const html = $("report-rendered").innerHTML ||
               (window.marked ? marked.parse(markdown) : markdown);
  // Wrap in a minimal document so rich-text targets (PowerScribe, Word, Outlook)
  // reliably pick up the text/html flavor.
  const htmlDoc = `<!DOCTYPE html><html><body>${html}</body></html>`;

  // Build the clipboard payload per the user's paste-format preference.
  //   rich     — html + plain-rendered fallback
  //   plain    — plain-rendered only (strips markdown markers)
  //   markdown — raw markdown source only
  const payload =
    fmt === "markdown" ? { plain: markdown } :
    fmt === "plain"    ? { plain } :
                         { plain, html: htmlDoc };
  const labelSuffix =
    fmt === "markdown" ? " (markdown)" :
    fmt === "plain"    ? " (plain)" :
                         "";

  try {
    if (payload.html && window.ClipboardItem && navigator.clipboard && navigator.clipboard.write) {
      const item = new ClipboardItem({
        "text/html":  new Blob([payload.html],  { type: "text/html" }),
        "text/plain": new Blob([payload.plain], { type: "text/plain" }),
      });
      await navigator.clipboard.write([item]);
    } else if (navigator.clipboard && navigator.clipboard.writeText) {
      await navigator.clipboard.writeText(payload.plain);
    } else {
      throw new Error("Clipboard API unavailable");
    }
    setStatus(`Report copied to clipboard${labelSuffix}. Press Alt+N for next case.`, "success");
    return;
  } catch {
    // Fallback: use a contenteditable div + execCommand("copy").
    const div = document.createElement("div");
    div.contentEditable = "true";
    if (payload.html) {
      div.innerHTML = html;
    } else {
      div.textContent = payload.plain;
    }
    div.style.position = "fixed";
    div.style.left = "-9999px";
    div.style.top = "0";
    div.style.opacity = "0";
    document.body.appendChild(div);
    const range = document.createRange();
    range.selectNodeContents(div);
    const sel = window.getSelection();
    sel.removeAllRanges();
    sel.addRange(range);
    try {
      document.execCommand("copy");
      setStatus(`Report copied to clipboard${labelSuffix}. Press Alt+N for next case.`, "success");
    } catch {
      setStatus("Copy failed — select and copy manually.", "error");
    }
    sel.removeAllRanges();
    div.remove();
  }
}

// ---------------------------------------------------------------------------
// Next Case — atomically reset the UI so a radiologist can burn through a list
// ---------------------------------------------------------------------------
function nextCase({ keepRadiologist = true } = {}) {
  // Transcription + report
  $("transcription").value = "";
  $("report-raw").value = "";
  $("report-rendered").innerHTML = "";

  // Patient context — preserve radiologist name so they don't retype it each case.
  const fields = [
    "patient-name", "patient-dob", "patient-id",
    "accession", "modality", "body-part", "referring-physician",
  ];
  if (!keepRadiologist) fields.push("radiologist");
  for (const id of fields) {
    const el = $(id);
    if (el) el.value = "";
  }

  // Worklist — clear selection and disable archive button
  const wl = $("worklist-select");
  if (wl) wl.value = "";
  const arch = $("btn-worklist-archive");
  if (arch) arch.disabled = true;

  // Reset edit state so the user sees the rendered view next time
  if (typeof _setReportEditMode === "function") _setReportEditMode(false);

  // New dictation session
  state.sessionId = null;
  state.voiceEditTarget = null;
  if (fbState) fbState.selectedText = "";

  setUI("idle");
  setStatus("Ready for next case.", "active");

  // Focus the transcription textarea so the user can start immediately
  const t = $("transcription");
  if (t) t.focus();
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
// Voice-feedback report refinement
// ---------------------------------------------------------------------------
const fbState = {
  isRecording: false,
  mediaRecorder: null,
  stream: null,
  chunks: [],
  selectedText: "",   // passage selected in report-rendered when Refine was clicked
};

function _captureReportSelection() {
  const sel = window.getSelection();
  if (!sel || sel.isCollapsed) return "";
  const reportDiv = $("report-rendered");
  if (!reportDiv || !reportDiv.contains(sel.anchorNode)) return "";
  return sel.toString().trim();
}

function _resetFeedbackUI() {
  $("feedback-bar").style.display = "none";
  $("btn-refine-stop").disabled = false;
  fbState.selectedText = "";
  fbState.isRecording = false;
  // Re-evaluate Refine button based on current report state
  const hasReport = !!$("report-raw").value.trim();
  $("btn-refine").disabled = !hasReport;
}

async function startFeedback() {
  if (state.isRecording || fbState.isRecording) return;

  fbState.selectedText = _captureReportSelection();

  try {
    fbState.stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch (err) {
    setStatus(`Microphone error: ${err.message}`, "error");
    return;
  }

  fbState.chunks = [];
  fbState.mediaRecorder = new MediaRecorder(fbState.stream);
  fbState.mediaRecorder.ondataavailable = (e) => {
    if (e.data.size > 0) fbState.chunks.push(e.data);
  };
  fbState.mediaRecorder.onstop = _onFeedbackStop;
  fbState.mediaRecorder.start();
  fbState.isRecording = true;

  // Show feedback bar, hide Refine button
  $("btn-refine").disabled = true;
  $("feedback-bar").style.display = "flex";
  $("feedback-status").textContent = fbState.selectedText
    ? `Listening… targeting "${fbState.selectedText.slice(0, 50).trimEnd()}${fbState.selectedText.length > 50 ? "…" : ""}"`
    : "Listening for feedback…";
}

function stopFeedback() {
  if (!fbState.isRecording || !fbState.mediaRecorder) return;
  $("feedback-status").textContent = "Processing…";
  $("btn-refine-stop").disabled = true;
  fbState.mediaRecorder.stop();
}

async function _onFeedbackStop() {
  if (fbState.stream) {
    fbState.stream.getTracks().forEach((t) => t.stop());
    fbState.stream = null;
  }
  fbState.isRecording = false;

  const blob = new Blob(fbState.chunks, { type: "audio/webm" });
  if (blob.size < MIN_SEGMENT_BYTES) {
    _resetFeedbackUI();
    setStatus("No speech detected — feedback unchanged.", "error");
    return;
  }

  // Transcribe the feedback dictation
  const fd = new FormData();
  fd.append("audio", blob, "feedback.webm");

  let feedbackText;
  try {
    const resp = await fetch("/transcribe", { method: "POST", body: fd });
    if (!resp.ok) throw new Error("Transcription failed");
    const data = await resp.json();
    feedbackText = data.transcription?.trim();
  } catch (err) {
    _resetFeedbackUI();
    setStatus(`Feedback transcription error: ${err.message}`, "error");
    return;
  }

  if (!feedbackText) {
    _resetFeedbackUI();
    setStatus("No speech detected — feedback unchanged.", "error");
    return;
  }

  // Apply the feedback to the report
  $("feedback-status").textContent = "Applying feedback…";
  try {
    const resp = await fetch("/format/feedback", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        report: $("report-raw").value,
        feedback: feedbackText,
        selected_text: fbState.selectedText || "",
      }),
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({ detail: resp.statusText }));
      throw new Error(err.detail || resp.statusText);
    }
    const data = await resp.json();
    setReport(data.report);
    _resetFeedbackUI();
    setStatus("Report updated.", "success");
  } catch (err) {
    _resetFeedbackUI();
    setStatus(`Feedback error: ${err.message}`, "error");
  }
}

// ---------------------------------------------------------------------------
// Boot
// ---------------------------------------------------------------------------
// ---------------------------------------------------------------------------
// Template editor modal
// ---------------------------------------------------------------------------
let _tmplData = [];         // [{name, is_custom}]
let _tmplCurrent = null;    // {name, is_custom}
let _tmplDirty = false;

async function tmplOpen() {
  $("tmpl-modal").style.display = "flex";
  await _tmplFetchList();
}

function tmplClose() {
  if (_tmplDirty && !confirm("You have unsaved changes. Close anyway?")) return;
  $("tmpl-modal").style.display = "none";
  _tmplDirty = false;
}

async function _tmplFetchList() {
  const r = await fetch("/templates");
  const data = await r.json();
  _tmplData = data.templates;   // [{name, is_custom}]
  _tmplRenderList();
}

function _tmplRenderList() {
  const q = $("tmpl-search").value.toLowerCase();
  const ul = $("tmpl-list");
  ul.innerHTML = "";
  _tmplData
    .filter(t => !q || t.name.toLowerCase().includes(q))
    .forEach(t => {
      const li = document.createElement("li");
      li.dataset.name = t.name;
      if (_tmplCurrent && t.name === _tmplCurrent.name) li.classList.add("tmpl-active");
      const label = t.name.replace(/_/g, " ").replace(/\.(txt|md)$/, "");
      li.innerHTML = `<span style="flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${label}</span>`
        + (t.is_custom ? `<span class="tmpl-badge">Custom</span>` : "");
      li.addEventListener("click", () => _tmplSelect(t.name));
      ul.appendChild(li);
    });
}

async function _tmplSelect(name) {
  if (_tmplDirty && !confirm("You have unsaved changes. Switch template anyway?")) return;
  _tmplDirty = false;

  document.querySelectorAll("#tmpl-list li").forEach(li =>
    li.classList.toggle("tmpl-active", li.dataset.name === name)
  );

  $("tmpl-status").textContent = "Loading…";
  const r = await fetch(`/api/templates/${encodeURIComponent(name)}`);
  if (!r.ok) { $("tmpl-status").textContent = "Failed to load."; return; }
  const data = await r.json();

  _tmplCurrent = { name, is_custom: data.is_custom };
  const titleEl = $("tmpl-editor-title");
  titleEl.textContent = name.replace(/_/g, " ").replace(/\.(txt|md)$/, "");
  titleEl.classList.remove("tmpl-dirty");
  $("tmpl-content").value = data.content;
  $("tmpl-save").disabled = false;
  $("tmpl-duplicate").disabled = false;
  $("tmpl-restore").style.display = data.is_custom ? "inline-flex" : "none";
  $("tmpl-status").textContent = data.is_custom ? "Custom version" : "Bundled default";
}

function _tmplMarkDirty() {
  if (!_tmplCurrent) return;
  _tmplDirty = true;
  $("tmpl-editor-title").classList.add("tmpl-dirty");
}

async function tmplSave() {
  if (!_tmplCurrent) return;
  $("tmpl-status").textContent = "Saving…";
  $("tmpl-save").disabled = true;
  const r = await fetch(`/api/templates/${encodeURIComponent(_tmplCurrent.name)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content: $("tmpl-content").value }),
  });
  $("tmpl-save").disabled = false;
  if (r.ok) {
    _tmplDirty = false;
    _tmplCurrent.is_custom = true;
    $("tmpl-editor-title").classList.remove("tmpl-dirty");
    $("tmpl-restore").style.display = "inline-flex";
    $("tmpl-status").textContent = "Saved ✓  (Custom version)";
    await _tmplFetchList();
  } else {
    $("tmpl-status").textContent = "Save failed.";
  }
}

async function tmplRestore() {
  if (!_tmplCurrent) return;
  const label = _tmplCurrent.name.replace(/_/g, " ").replace(/\.(txt|md)$/, "");
  if (!confirm(`Restore "${label}" to the bundled default? Your customisation will be deleted.`)) return;
  const r = await fetch(`/api/templates/${encodeURIComponent(_tmplCurrent.name)}`, { method: "DELETE" });
  if (r.ok) {
    _tmplDirty = false;
    await _tmplSelect(_tmplCurrent.name);
    await _tmplFetchList();
  }
}

async function tmplDuplicate() {
  if (!_tmplCurrent) return;
  const base = _tmplCurrent.name.replace(/\.(txt|md)$/, "");
  const raw = prompt("New template filename (no extension):", `${base}_copy`);
  if (!raw || !raw.trim()) return;
  const filename = raw.trim().replace(/\s+/g, "_").replace(/[^A-Za-z0-9_\-. ]/g, "") + ".txt";
  const r = await fetch(`/api/templates/${encodeURIComponent(filename)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content: $("tmpl-content").value }),
  });
  if (r.ok) {
    await _tmplFetchList();
    await _tmplSelect(filename);
    _tmplSyncDropdown();
  }
}

async function tmplNew() {
  const raw = prompt("New template filename (no extension):", "My_Template");
  if (!raw || !raw.trim()) return;
  const filename = raw.trim().replace(/\s+/g, "_").replace(/[^A-Za-z0-9_\-. ]/g, "") + ".txt";
  const r = await fetch(`/api/templates/${encodeURIComponent(filename)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content: "" }),
  });
  if (r.ok) {
    await _tmplFetchList();
    await _tmplSelect(filename);
    _tmplSyncDropdown();
  }
}

// ---------------------------------------------------------------------------
// Template upload
// ---------------------------------------------------------------------------
const _TMPL_WRAP_HEADER = `### THIS IS CONTINUATION OF SYSTEM PROMPT ###
### THIS IS THE SAMPLE TEMPLATE TO BE USED FOR THE TRANSCRIPT ###

---

### Clinical Details
**Instructions:**
- **If provided:** 'The patient presented with [describe symptoms or reason for the examination], with a medical history notable for [mention relevant medical conditions].'
  - **Exclude** name, age, gender, and IDs anywhere in the report, even if provided in the transcript.
- **If not provided:** 'No clinical details provided.'

### Comparison:
**Instructions:**
- **If provided:** Mention any prior imaging studies and briefly note significant changes or findings compared to previous examinations.
- **If not provided:** 'No prior imaging scans/reports available for comparison.'

### Findings:
**Instructions:**
- Describe pathological structures specifically. Normal structures within the same anatomical group may be combined into a single statement — do not force a separate bullet for every structure when all are normal.
- For any structure the radiologist did not comment on, use standard normal radiological terminology.
- Follow the structure below.

`;

const _TMPL_WRAP_FOOTER = `

### Impression:
**Instructions:**
- Concise bullet-point summary of key positive findings.
- Omit normal findings from the impression unless clinically significant.

---
`;

let _tmplUploadFile = null;   // File object

function _tmplSanitizeFilename(raw) {
  if (!raw) return "";
  let name = raw.trim().replace(/\s+/g, "_").replace(/[^A-Za-z0-9_\-. ]/g, "");
  if (!/\.(txt|md)$/i.test(name)) name += ".txt";
  return name;
}

function _tmplUploadValidate() {
  const nameInput = $("tmpl-upload-name");
  const confirmBtn = $("tmpl-upload-confirm");
  const warn = $("tmpl-upload-warn");
  const raw = nameInput.value.trim();
  if (!_tmplUploadFile || !raw) {
    confirmBtn.disabled = true;
    warn.style.display = "none";
    return;
  }
  if (!/^[A-Za-z0-9_\-. ]+\.(txt|md)$/i.test(raw)) {
    confirmBtn.disabled = true;
    warn.textContent = "⚠ Filename must contain only letters, numbers, spaces, dots, dashes, underscores, and end in .txt or .md";
    warn.style.display = "block";
    return;
  }
  const clash = _tmplData.find(t => t.name.toLowerCase() === raw.toLowerCase());
  if (clash) {
    warn.textContent = clash.is_custom
      ? `⚠ "${raw}" already exists as a custom template — uploading will overwrite it.`
      : `⚠ "${raw}" matches a bundled template — uploading will create a custom version that overrides it.`;
    warn.style.display = "block";
  } else {
    warn.style.display = "none";
  }
  confirmBtn.disabled = false;
}

function _tmplUploadOpen(file) {
  _tmplUploadFile = file;
  $("tmpl-upload-source").textContent = `Source: ${file.name} (${Math.round(file.size / 1024 * 10) / 10} KB)`;
  $("tmpl-upload-name").value = _tmplSanitizeFilename(file.name);
  $("tmpl-upload-mode-asis").checked = true;
  $("tmpl-upload-warn").style.display = "none";
  $("tmpl-upload-modal").style.display = "flex";
  _tmplUploadValidate();
  setTimeout(() => $("tmpl-upload-name").focus(), 0);
}

function _tmplUploadClose() {
  $("tmpl-upload-modal").style.display = "none";
  $("tmpl-upload-input").value = "";
  _tmplUploadFile = null;
}

async function _tmplUploadConfirm() {
  if (!_tmplUploadFile) return;
  const filename = _tmplSanitizeFilename($("tmpl-upload-name").value);
  if (!filename) return;

  const mode = document.querySelector('input[name="tmpl_upload_mode"]:checked').value;
  let content;
  try {
    content = await _tmplUploadFile.text();
  } catch (_) {
    $("tmpl-upload-warn").textContent = "⚠ Failed to read file.";
    $("tmpl-upload-warn").style.display = "block";
    return;
  }
  if (mode === "wrap") {
    content = _TMPL_WRAP_HEADER + content.trimEnd() + _TMPL_WRAP_FOOTER;
  }

  const clash = _tmplData.find(t => t.name.toLowerCase() === filename.toLowerCase());
  if (clash && clash.is_custom && !confirm(`"${filename}" already exists. Overwrite?`)) return;

  const confirmBtn = $("tmpl-upload-confirm");
  confirmBtn.disabled = true;
  const r = await fetch(`/api/templates/${encodeURIComponent(filename)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content }),
  });
  if (r.ok) {
    _tmplUploadClose();
    await _tmplFetchList();
    await _tmplSelect(filename);
    _tmplSyncDropdown();
    $("tmpl-status").textContent = "Uploaded ✓";
  } else {
    $("tmpl-upload-warn").textContent = "⚠ Upload failed.";
    $("tmpl-upload-warn").style.display = "block";
    confirmBtn.disabled = false;
  }
}

function _tmplSyncDropdown() {
  fetch("/templates")
    .then(r => r.json())
    .then(data => {
      const sel = $("template-select");
      const current = sel.value;
      sel.innerHTML = '<option value="">Auto-select (AI chooses)</option>';
      data.templates.forEach(t => {
        const opt = document.createElement("option");
        opt.value = t.name;
        opt.textContent = t.name.replace(/_/g, " ").replace(/\.(txt|md)$/, "");
        if (t.name === current) opt.selected = true;
        sel.appendChild(opt);
      });
    });
}

document.addEventListener("DOMContentLoaded", async () => {
  initCanvasResize();
  setUI("idle");
  setStatus("Press Record to start dictating.");

  // Track the last known selection in the transcription/report textareas.
  // _pendingSelection is set on every non-zero selection event and cleared only
  // when consumed by _grabVoiceEdit or when focus leaves for a non-Record target.
  let _pendingSelection = null;
  // Timestamp of the last pointerdown on the Record button. Used to suppress
  // _pendingSelection clearing when blur is triggered by clicking Record — on
  // Safari/iOS, buttons don't receive focus so relatedTarget is null, causing
  // the blur handler to incorrectly wipe the selection before we can use it.
  let _recordPointerdownAt = 0;

  ["transcription", "report-raw"].forEach(id => {
    const el = $(id);
    if (!el) return;
    const save = () => {
      const s = el.selectionStart, e = el.selectionEnd;
      if (s !== e) {
        _pendingSelection = { elId: id, start: s, end: e, selectedText: el.value.slice(s, e) };
      }
    };
    el.addEventListener("mouseup", save);
    el.addEventListener("select", save);
    el.addEventListener("keyup", save);
    el.addEventListener("touchend", save);  // iOS: fires after selection gesture
    // Clear _pendingSelection when focus leaves, UNLESS the user just pressed
    // the Record button (within 500 ms). On Safari/iOS, buttons don't get focus
    // so relatedTarget is null even when Record was clicked — the timestamp check
    // prevents us from wiping the selection we need for voice-edit.
    el.addEventListener("blur", (evt) => {
      const toRecord = evt.relatedTarget && evt.relatedTarget.id === "btn-record";
      const justPressedRecord = Date.now() - _recordPointerdownAt < 500;
      if (!toRecord && !justPressedRecord) {
        _pendingSelection = null;
      }
    });
  });

  // Global selectionchange tracker — most reliable across browsers/platforms.
  // Fires on every selection mutation; we snapshot non-zero selections on the
  // two editable textareas. Never clears _pendingSelection on collapse, since
  // a collapse during a Record tap (e.g. Safari/iOS button tap) must not wipe
  // the last real selection before _grabVoiceEdit consumes it.
  document.addEventListener("selectionchange", () => {
    const active = document.activeElement;
    if (!active || (active.id !== "transcription" && active.id !== "report-raw")) return;
    const s = active.selectionStart, e = active.selectionEnd;
    if (s != null && e != null && s !== e) {
      const sel = { elId: active.id, start: s, end: e, selectedText: active.value.slice(s, e) };
      _pendingSelection = sel;
      // If already recording (user editing transcript mid-session), track as
      // a pending voice-edit so the next VAD cut does a splice not an append.
      if (state.isRecording && !state.streamingWs) {
        state.pendingVoiceEditSelection = sel;
      }
    } else if (s === e) {
      // Collapsed — clear the mid-recording selection so clicking elsewhere
      // during a session doesn't accidentally voice-edit the wrong span.
      state.pendingVoiceEditSelection = null;
    }
  });

  // Capture the textarea selection at pointerdown time.
  // Three-tier fallback to handle cross-browser selection clearing on blur:
  //   1. Read selectionStart/End directly from #transcription (most browsers
  //      preserve these even after blur).
  //   2. _pendingSelection saved by mouseup/select/keyup on the textarea.
  //      The blur handler won't clear this if the Record button was just pressed
  //      (timestamp guard handles Safari/iOS where buttons don't receive focus).
  //   3. _pendingSelection for report-raw editing.
  const _grabVoiceEdit = () => {
    _recordPointerdownAt = Date.now(); // timestamp for blur handler guard

    // Tier 0: selection in the rendered report div — survives button click.
    const domSel = window.getSelection();
    if (domSel && !domSel.isCollapsed) {
      const reportDiv = $("report-rendered");
      if (reportDiv && reportDiv.contains(domSel.anchorNode)) {
        const selectedText = domSel.toString().trim();
        if (selectedText) {
          state.voiceEditTarget = { elId: "report-rendered", selectedText };
          console.log("[voice-edit] captured rendered (tier0):", JSON.stringify(selectedText));
          return;
        }
      }
    }

    // Tier 1: direct read from either editable textarea.
    for (const id of ["transcription", "report-raw"]) {
      const el = $(id);
      if (el && el.selectionStart !== el.selectionEnd) {
        const s = el.selectionStart, e = el.selectionEnd;
        state.voiceEditTarget = { elId: id, start: s, end: e, selectedText: el.value.slice(s, e) };
        _pendingSelection = null;
        console.log("[voice-edit] captured (tier1):", id, s, e, JSON.stringify(el.value.slice(s, e)));
        return;
      }
    }
    // Tier 2 & 3: _pendingSelection (saved by mouseup/select/keyup/touchend/selectionchange)
    if (_pendingSelection) {
      state.voiceEditTarget = _pendingSelection;
      console.log("[voice-edit] captured (tier2):",
        _pendingSelection.elId, _pendingSelection.start, _pendingSelection.end,
        JSON.stringify(_pendingSelection.selectedText));
      _pendingSelection = null;
    } else {
      console.log("[voice-edit] no selection captured on pointerdown");
    }
    // If no selection is detected on this pointerdown, DO NOT clear an existing
    // voiceEditTarget: a rapid double-tap or repeat pointerdown during a voice-
    // edit session must not cancel the pending edit. The target is cleared only
    // by submitAudioSegment (after splice) or startStreamingRecording (consume).
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
        displayEnd === state.streamingAnchorEnd) return; // nothing changed

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
    // Store selected text so _updateStreamingDisplay can keep it highlighted
    state.streamingSelectedText = (newPos !== newEnd)
      ? stableText.slice(newPos, newEnd)
      : "";
    state.confirmedText   = "";
    state.interimText     = "";

    _suppressStreamingSelChange = true;
    // Only strip trailing interim — don't erase the selected text.
    if (interimSuffix) {
      tx.value = tx.value.slice(0, stableLen);
    }
    tx.selectionStart = newPos;
    tx.selectionEnd   = newEnd;
    state.streamingAnchorPos = newPos;
    state.streamingAnchorEnd = newEnd;
    setTimeout(() => { _suppressStreamingSelChange = false; }, 0);
  });
  // Use pointerdown (handles both mouse and touch, fires earliest in event chain).
  $("btn-record").addEventListener("pointerdown", _grabVoiceEdit, { passive: true });
  $("btn-record").addEventListener("click", onRecordClick);
  $("btn-stop").addEventListener("click", stopRecording);
  $("btn-format").addEventListener("click", formatReport);
  $("btn-copy").addEventListener("click", copyReport);
  $("btn-edit-toggle").addEventListener("click", toggleReportEdit);
  $("btn-lookup").addEventListener("click", lookupPatient);
  if ($("btn-next-case")) $("btn-next-case").addEventListener("click", () => nextCase());

  // HL7 worklist
  if ($("worklist-select")) {
    $("worklist-select").addEventListener("change", applyWorklistOrder);
    $("btn-worklist-refresh").addEventListener("click", refreshWorklist);
    $("btn-worklist-archive").addEventListener("click", archiveWorklistOrder);
    document.querySelectorAll(".worklist-chip").forEach((chip) => {
      chip.addEventListener("click", () => _setWorklistFilter(chip.dataset.modality || ""));
    });
    refreshWorklist();
    // Refresh waiting-time labels every 30s without re-fetching the inbox.
    setInterval(() => {
      if ($("worklist-panel") && $("worklist-panel").style.display !== "none") {
        _renderWorklistOptions();
      }
    }, 30000);
  }

  // Voice feedback
  $("btn-refine").addEventListener("click", startFeedback);
  $("btn-refine-stop").addEventListener("click", stopFeedback);

  // Template editor
  $("btn-template-edit").addEventListener("click", tmplOpen);
  $("tmpl-close").addEventListener("click", tmplClose);
  $("tmpl-save").addEventListener("click", tmplSave);
  $("tmpl-restore").addEventListener("click", tmplRestore);
  $("tmpl-duplicate").addEventListener("click", tmplDuplicate);
  $("tmpl-new").addEventListener("click", tmplNew);
  $("tmpl-search").addEventListener("input", () => _tmplRenderList());
  $("tmpl-content").addEventListener("input", _tmplMarkDirty);
  $("tmpl-modal").addEventListener("click", (e) => { if (e.target === $("tmpl-modal")) tmplClose(); });

  // Upload wiring
  $("tmpl-upload").addEventListener("click", () => $("tmpl-upload-input").click());
  $("tmpl-upload-input").addEventListener("change", (e) => {
    const file = e.target.files && e.target.files[0];
    if (file) _tmplUploadOpen(file);
  });
  $("tmpl-upload-close").addEventListener("click", _tmplUploadClose);
  $("tmpl-upload-cancel").addEventListener("click", _tmplUploadClose);
  $("tmpl-upload-confirm").addEventListener("click", _tmplUploadConfirm);
  $("tmpl-upload-name").addEventListener("input", _tmplUploadValidate);
  $("tmpl-upload-modal").addEventListener("click", (e) => {
    if (e.target === $("tmpl-upload-modal")) _tmplUploadClose();
  });

  document.addEventListener("keydown", (e) => {
    if ($("tmpl-upload-modal").style.display !== "none") {
      if (e.key === "Escape") _tmplUploadClose();
      return;
    }
    if ($("tmpl-modal").style.display !== "none") {
      if (e.key === "Escape") tmplClose();
      if ((e.ctrlKey || e.metaKey) && e.key === "s") { e.preventDefault(); tmplSave(); }
      return;
    }
    // Alt+N — Next Case. Fires even while focused in the report/transcription
    // fields, since the whole point is to clear them quickly.
    if (e.altKey && !e.ctrlKey && !e.metaKey && (e.key === "n" || e.key === "N")) {
      e.preventDefault();
      nextCase();
    }
  });

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
