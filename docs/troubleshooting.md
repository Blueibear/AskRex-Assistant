# AskRex Assistant — Troubleshooting

## Missing API Keys

**Error:** `REX_SPEAK_API_KEY: not set`

**Solution:** Set the API key in your `.env` file:
```env
REX_SPEAK_API_KEY=your-secret-key-here
```

## FFmpeg Not Found

**Error:** `ffmpeg executable not found`

**Solution:**
- **macOS:** `brew install ffmpeg`
- **Ubuntu/Debian:** `sudo apt install ffmpeg`
- **Windows:** Download from https://ffmpeg.org/download.html and add to PATH

## PyTorch Installation Issues

**Error:** `torch is not installed`

**Solution:** Use the appropriate requirements file:
```bash
# CPU-only
pip uninstall -y torch torchvision torchaudio
pip install -r requirements-cpu.txt

# GPU with CUDA 12.4 (Windows 11)
pip uninstall -y torch torchvision torchaudio
pip install -r requirements-gpu-cu124.txt

# GPU with CUDA 11.8
pip uninstall -y torch torchvision torchaudio
pip install -r requirements-gpu.txt
```

## Unsupported Python Version

**Error:** `Unsupported Python ... for Rex ...`

**Solution:** Recreate the environment with Python 3.11.

```powershell
py -3.11 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip setuptools wheel
```

The current Rex install paths are validated on Python 3.11. Fresh installs on Python 3.13 and 3.14 are known to fail in the ML/TTS dependency path, so the installers now stop immediately instead of letting pip fail later.

## Microphone Permissions (macOS)

**Error:** `Audio device not accessible`

**Solution:**
1. Open **System Settings** → **Privacy & Security** → **Microphone**
2. Enable microphone access for **Terminal** or your Python interpreter

## WASAPI Issues (Windows)

**Error:** `sounddevice` or `portaudio` errors on Windows

**Solution:**
1. Install Visual C++ Redistributables: https://aka.ms/vs/17/release/vc_redist.x64.exe
2. Install `pyaudio` from wheels: https://www.lfd.uci.edu/~gohlke/pythonlibs/#pyaudio

## Wake Word Not Detected

**Issue:** Rex doesn't respond to wake word

**Solution:**
1. Open the **Voice** page while wake mode is active and check **Say:** for the actual live detector phrase. If fallback is active, this can differ from the configured wake word.
2. Expand **Wake-word diagnostics** to check the active backend, threshold, selected microphone, confidence evidence, and whether input is unusually quiet or consistently below threshold.
3. Check microphone is working: `python audio_config.py --list`
4. Lower threshold only after reviewing the live evidence: set `"threshold": 0.3` under `wakeword` in `config/rex_config.json`.
5. Test wake word detection: `python wakeword_listener.py`
6. Record custom wake word: `python scripts/record_wakeword.py`

The diagnostic surface intentionally exposes bounded detector metadata only. It does not display or persist raw microphone audio, transcripts, model/embedding filesystem paths, credentials, or user identity.

## Custom Wake Model — False Triggers on Silence

**Issue:** Rex wakes up constantly on background noise with a custom-trained wake
model, or logs show `custom_wake_model_unreliable` / `wakeword_backend_fallback_activated`.

**What happened:** During the first 10 seconds of each wake-listening cycle the
listener runs a noise self-test on the `custom_embedding` backend. If it sees 5
high-confidence detections (≥ 0.85) on effectively silent audio (RMS ≤ 0.006 and
peak ≤ 0.025), it concludes the model is unreliable in this environment:

- **With a fallback available** (`wakeword.fallback_to_builtin: true`, the default) —
  Rex automatically swaps to the built-in `openwakeword` keyword
  (`wakeword.fallback_keyword`, default "hey jarvis") and keeps listening. The log
  contains `wakeword_backend_fallback_activated`, and the detection that triggered
  the swap is suppressed so no false capture fires.

- **Without a fallback** (`fallback_to_builtin: false`) — the model is marked
  unreliable in the logs (`custom_wake_model_unreliable`) but keeps running;
  expect continued false triggers until you fix the model.

**Solutions:**
1. **Retrain in your environment.** Run `python scripts/record_wakeword.py` in the
   same room and mic setup Rex will use. More varied samples improve robustness.
2. **Keep the built-in fallback enabled** in `config/rex_config.json`:
   ```json
   "wakeword": {
     "backend": "custom_embedding",
     "fallback_to_builtin": true,
     "fallback_keyword": "hey jarvis"
   }
   ```
3. **Switch backend** to `openwakeword` if retraining is not practical.
4. **Check microphone gain.** Very high ambient gain (OS AGC) can make room
   noise look like speech. Reduce the OS microphone boost level.

## Rate Limit Errors (TTS API)

**Error:** `429 Too Many Requests`

**Solution:** Increase rate limits in `.env`:
```env
REX_SPEAK_RATE_LIMIT=60
REX_SPEAK_RATE_WINDOW=60
```

For production deployments with multiple workers, use Redis:
```env
REX_SPEAK_STORAGE_URI=redis://localhost:6379/0
```

## CUDA Out of Memory

**Error:** `RuntimeError: CUDA out of memory`

**Solution:**
1. Use smaller Whisper model: `REX_WHISPER_MODEL=tiny` or `base`
2. Reduce max tokens: `REX_LLM_MAX_TOKENS=50`
3. Switch to CPU: `REX_DEVICE=cpu` and `REX_WHISPER_DEVICE=cpu`


## Microphone Unavailable in Voice Mode

**Error:** `Microphone unavailable. Reconnect or select a microphone in Voice settings, then check your operating-system microphone permissions.`

**What it means:** AskRex could not initialize or continue using the microphone selected for voice mode. The Electron Voice page shows this message directly instead of only reporting a generic voice-pipeline failure.

**Solution:**
1. Open **Settings → Voice** and select an available microphone, or switch back to the system default.
2. Reconnect the microphone if it was unplugged or disconnected.
3. Confirm AskRex/Electron has microphone permission in your operating-system privacy settings.
4. Close another application temporarily if it may be holding the microphone exclusively, then try voice mode again.
5. Run `python -m rex doctor` if the device still cannot be opened.

## Speaker Output Unavailable in Voice Mode

**Error:** `Speaker output unavailable. Reconnect or select an output device in Voice settings, then check your operating-system sound output.`

**What it means:** AskRex generated a response but the configured playback device could not be initialized or used. The Voice page shows the output-device error directly.

**Solution:**
1. Open **Settings → Voice** and select an available output device, or switch back to the system default.
2. Reconnect or power on the selected speakers/headphones.
3. Confirm the operating system is routing sound to that device and that it is not disabled.
4. Close another application temporarily if it may be holding the output device exclusively, then try again.
5. Run `python -m rex doctor` if output still cannot be opened.
