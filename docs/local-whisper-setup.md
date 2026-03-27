# Local Whisper Setup Guide

Run VoxRad with a fully local ASR backend — no API key, no cloud, no PHI leaving your machine.

---

## Why local Whisper?

VoxRad sends audio to an OpenAI-compatible transcription endpoint. By default this is OpenAI's hosted API. Swapping the base URL to a local server means:

- Zero data leaves your network — suitable for HIPAA / GDPR environments
- No per-minute API costs
- Works offline (air-gapped hospital deployments)
- Latency depends on your hardware, not internet connectivity

---

## Option A: faster-whisper (recommended for most users)

`faster-whisper` is a reimplementation of Whisper using CTranslate2. It is 4× faster than the original at the same accuracy, with lower memory usage.

### Install

```bash
pip install faster-whisper
```

### Run as an OpenAI-compatible HTTP server

Use [whisper-server](https://github.com/fedirz/faster-whisper-server) which exposes an `/v1/audio/transcriptions` endpoint compatible with VoxRad:

```bash
pip install faster-whisper-server
uvicorn faster_whisper_server.main:app --host 0.0.0.0 --port 8000
```

The server loads the model on first request. To pre-load a specific model:

```bash
WHISPER_MODEL=large-v3 uvicorn faster_whisper_server.main:app --host 0.0.0.0 --port 8000
```

### Configure VoxRad

In **Settings → Transcription**:

| Field | Value |
|-------|-------|
| Base URL | `http://localhost:8000/v1` |
| API Key | `local` (any non-empty string) |
| Model | `Systran/faster-whisper-large-v3` |

---

## Option B: whisper.cpp HTTP server

`whisper.cpp` is a pure C++ port of Whisper. It compiles to a single binary with no Python dependency — ideal for hospital servers with locked-down software environments.

### Build

```bash
git clone https://github.com/ggerganov/whisper.cpp
cd whisper.cpp
make -j

# Download a model (GGML format)
bash ./models/download-ggml-model.sh large-v3
```

### Run the server

```bash
./build/bin/server \
  --model models/ggml-large-v3.bin \
  --host 0.0.0.0 \
  --port 8080
```

This exposes `/inference` at port 8080. Because `whisper.cpp` server uses a different endpoint format from OpenAI, you need a thin proxy. Use [whisper-cpp-openai-compat](https://github.com/morioka/tiny-openai-whisper-api) or run the `faster-whisper-server` instead (Option A is simpler).

### Configure VoxRad

| Field | Value |
|-------|-------|
| Base URL | `http://localhost:8080/v1` |
| API Key | `local` |
| Model | `whisper-large-v3` |

---

## Model selection

| Model | Size | VRAM | Speed (RTF) | Notes |
|-------|------|------|-------------|-------|
| `tiny.en` | 39 MB | ~390 MB | ~32× real-time | English only, fast, low accuracy |
| `base.en` | 74 MB | ~500 MB | ~16× real-time | Good for clear audio |
| `small.en` | 244 MB | ~900 MB | ~6× real-time | Good balance |
| `medium.en` | 769 MB | ~2.5 GB | ~2× real-time | **Recommended for radiology** |
| `large-v3` | 1.5 GB | ~4 GB | ~1× real-time | Best accuracy, needs dedicated GPU |

**RTF** = Real-Time Factor. RTF of 2× means a 30-second dictation takes ~15 seconds to transcribe.

For radiology, `medium.en` is the recommended starting point — it handles medical terminology well and runs comfortably on a modern CPU (M1 Mac, i7/i9) or entry-level GPU.

### Hardware benchmarks (approximate)

| Hardware | Model | RTF |
|----------|-------|-----|
| Apple M1 (CPU) | medium.en | ~3× real-time |
| Apple M2/M3 (Neural Engine) | large-v3 | ~2× real-time |
| NVIDIA RTX 3080 | large-v3 | ~8× real-time |
| NVIDIA A100 | large-v3 | ~30× real-time |
| Intel i7-12700 (CPU only) | medium.en | ~1.5× real-time |

---

## HIPAA air-gapped deployment note

For deployments where **no patient data can leave the premises**:

1. Use Option A (`faster-whisper-server`) or Option B (`whisper.cpp`) on a server inside your network.
2. Set VoxRad's transcription base URL to the internal IP/hostname of that server.
3. Set the text model base URL to a local Ollama or LM Studio instance running a medical LLM.
4. Disable all network access from the VoxRad workstations to the internet at the firewall level.
5. VoxRad's audio is encrypted at rest (Fernet) on the local machine — the decrypted audio is only held in memory during the transcription API call and immediately deleted.

With both the transcription and text model APIs pointing to local servers, **zero patient information leaves your network**.

---

## Testing your setup

Once configured, use the **Fetch Models** button in VoxRad Settings to verify connectivity. If the server is running and the base URL is correct, the model dropdown will populate with the available Whisper models.

Common issues:

| Symptom | Likely cause |
|---------|-------------|
| "Failed to fetch models" | Server not running, or wrong base URL |
| Empty model list | Server running but models not loaded yet (wait ~30s on first start) |
| Slow transcription | GPU not detected — check CUDA/Metal drivers |
| Poor accuracy on radiology terms | Use `medium` or `large-v3`; avoid `tiny`/`base` for medical use |
