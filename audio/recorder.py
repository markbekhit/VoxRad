import sounddevice as sd
import threading
import time
import numpy as np
import os
import tempfile
import logging
from cryptography.fernet import Fernet
import wave
from config.config import config
from audio.transcriber import transcribe_audio, mm_gemini
from ui.utils import update_status, draw_straight_line, stop_waveform_simulation, start_waveform_simulation
import lameenc

logger = logging.getLogger(__name__)

# --- Shared state ---
# `_recording_event` replaces the bare `recording` bool so cross-thread
# reads/writes go through a proper threading primitive.
_recording_event = threading.Event()

paused = False
audio_data = []
start_time = None
recording_thread = None
pause_event = threading.Event()

# `latest_audio_chunk` is written by the audio callback thread and read by
# the waveform-visualiser callback on the main thread.  A lock prevents torn
# reads/writes of the numpy array reference.
_chunk_lock = threading.Lock()
latest_audio_chunk = None


def record_audio():
    """Starts audio recording with encryption."""
    global recording_thread, audio_data, paused

    # Check for API keys
    if config.multimodal_pref and config.MM_API_KEY is None:
        update_status("Please Save/Unlock your Multimodal Model API key in Settings.")
        return
    if not config.multimodal_pref and (config.TRANSCRIPTION_API_KEY is None or config.TEXT_API_KEY is None):
        update_status("Please Save/Unlock your Transcription and Text Model API keys in Settings.")
        return

    _recording_event.set()
    paused = False
    audio_data = []
    config.current_encryption_key = None
    config.current_encrypted_mp3_path = None

    from ui.main_window import record_button, stop_button, pause_button, canvas
    record_button['state'] = 'disabled'
    stop_button['state'] = 'normal'
    pause_button['state'] = 'normal'

    if config.audio_device is None:
        update_status("Please select an audio device in settings.")
        return

    device_index = next(
        (i for i, device in enumerate(sd.query_devices()) if device['name'] == config.audio_device),
        None,
    )
    if device_index is None:
        update_status("Selected audio device not found. Please check settings.")
        return

    recording_thread = threading.Thread(target=background_recording, args=(device_index,), daemon=True)
    recording_thread.start()
    update_status("Recording 🔴")
    start_waveform_simulation(canvas, config.root)


def pause_audio():
    global paused, pause_event
    from ui.main_window import record_button, stop_button, pause_button, canvas
    if not paused:
        paused = True
        pause_event.clear()
        pause_button.config(text="Resume")
        record_button['state'] = 'disabled'
        stop_button['state'] = 'disabled'
        update_status("Paused ⏸️")
        stop_waveform_simulation(canvas)
        draw_straight_line(canvas)
    else:
        paused = False
        pause_event.set()
        pause_button.config(text="Pause")
        record_button['state'] = 'disabled'
        stop_button['state'] = 'normal'
        update_status("Recording 🔴")
        start_waveform_simulation(canvas, config.root)


def background_recording(device_index=None):
    global audio_data, start_time, paused, latest_audio_chunk
    fs = 44100
    start_time = time.time()

    try:
        device_config = (
            {'samplerate': fs, 'channels': 1, 'dtype': 'float32', 'device': device_index}
            if device_index is not None
            else {'samplerate': fs, 'channels': 1, 'dtype': 'float32'}
        )
        with sd.InputStream(**device_config) as stream:
            logger.info("Recording started.")
            loop_counter = 0
            while _recording_event.is_set():
                if paused:
                    pause_event.wait()
                data, overflowed = stream.read(fs)
                if overflowed:
                    logger.warning("Audio buffer overflowed.")
                audio_data.append(data)
                with _chunk_lock:
                    latest_audio_chunk = data
                logger.debug("Chunk %d: shape=%s, overflowed=%s", loop_counter, data.shape, overflowed)
                loop_counter += 1
            logger.info("Recording stopped. Total chunks recorded: %d", loop_counter)
    except PermissionError:
        logger.error("Microphone permission denied.")
        update_status(
            "Microphone access denied. Grant permission in System Settings → Privacy → Microphone."
        )
    except Exception as e:
        logger.error("An error occurred during recording: %s", e)
        update_status(f"Recording error: {e}")
    finally:
        if not _recording_event.is_set():
            update_status("Started Processing...⚙️")


def stop_recording():
    global recording_thread
    logger.debug("stop_recording called.")
    _recording_event.clear()
    pause_event.set()
    from ui.main_window import pause_button, canvas
    pause_button['state'] = 'disabled'
    stop_waveform_simulation(canvas)
    config.root.after(100, check_recording_finished)


def check_recording_finished():
    global recording_thread
    logger.debug("check_recording_finished called.")
    if recording_thread.is_alive():
        config.root.after(100, check_recording_finished)
    else:
        complete_stop_recording()


def complete_stop_recording():
    global audio_data, start_time
    logger.debug("complete_stop_recording called.")
    sd.stop()
    from ui.main_window import record_button, stop_button, canvas
    record_button['state'] = 'normal'
    stop_button['state'] = 'disabled'
    draw_straight_line(canvas)

    fs = 44100
    if audio_data:
        wav_data = np.concatenate(audio_data, axis=0)
        encrypted_mp3_path, encryption_key = convert_wav_to_encrypted_mp3(wav_data, fs)

        config.current_encrypted_mp3_path = encrypted_mp3_path
        config.current_encryption_key = encryption_key

        logger.debug("Encrypted MP3 path: %s", config.current_encrypted_mp3_path)
        logger.debug("Encryption key present: %s", config.current_encryption_key is not None)

        if config.current_encrypted_mp3_path:
            if config.multimodal_pref:
                config.root.after(
                    100,
                    lambda: mm_gemini(config.current_encrypted_mp3_path, config.current_encryption_key),
                )
            else:
                config.root.after(
                    100,
                    lambda: transcribe_audio(config.current_encrypted_mp3_path, config.current_encryption_key),
                )
        else:
            update_status("Error converting audio.")
    else:
        logger.warning("No audio data to process.")
        update_status("No audio recorded.")


def convert_wav_to_encrypted_mp3(wav_data, fs):
    """Converts in-memory WAV data to an encrypted MP3 file.

    Ensures the MP3 file size is less than 25 MB by adjusting the bitrate.
    """
    key = Fernet.generate_key()
    cipher_suite = Fernet(key)

    temp_wav_path = None
    temp_mp3_path = None
    temp_enc_mp3_path = None

    try:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp_wav_file:
            with wave.open(tmp_wav_file.name, 'wb') as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(fs)
                wf.writeframes((wav_data * 32767).astype(np.int16).tobytes())
            temp_wav_path = tmp_wav_file.name

        with wave.open(temp_wav_path, 'rb') as wav_file:
            num_channels = wav_file.getnchannels()
            frame_rate = wav_file.getframerate()
            pcm_data = wav_file.readframes(wav_file.getnframes())

        target_size_bytes = 25 * 1024 * 1024
        bitrate = 128

        while True:
            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp_mp3_file:
                temp_mp3_path = tmp_mp3_file.name

            encoder = lameenc.Encoder()
            encoder.set_bit_rate(bitrate)
            encoder.set_in_sample_rate(frame_rate)
            encoder.set_channels(num_channels)
            encoder.set_quality(2)

            mp3_data = encoder.encode(pcm_data)
            mp3_data += encoder.flush()

            with open(temp_mp3_path, 'wb') as f:
                f.write(mp3_data)

            file_size = os.path.getsize(temp_mp3_path)
            logger.info("Conversion successful. File size: %.2f MB", file_size / (1024 * 1024))

            if file_size <= target_size_bytes:
                break
            else:
                bitrate -= 10
                if bitrate < 64:
                    logger.warning("Unable to reduce file size below 25 MB with acceptable quality.")
                    break
                os.remove(temp_mp3_path)

        with open(temp_mp3_path, 'rb') as f:
            mp3_data = f.read()
        encrypted_mp3_data = cipher_suite.encrypt(mp3_data)

        with tempfile.NamedTemporaryFile(suffix=".mp3.enc", delete=False) as tmp_enc_mp3_file:
            tmp_enc_mp3_file.write(encrypted_mp3_data)
            temp_enc_mp3_path = tmp_enc_mp3_file.name

        logger.info("Encrypted MP3 saved to %s", temp_enc_mp3_path)
        return temp_enc_mp3_path, key

    except Exception as e:
        logger.error("Error during conversion or encryption: %s", e)
        return None, None

    finally:
        if temp_wav_path and os.path.exists(temp_wav_path):
            os.remove(temp_wav_path)
        if temp_mp3_path and os.path.exists(temp_mp3_path):
            os.remove(temp_mp3_path)
