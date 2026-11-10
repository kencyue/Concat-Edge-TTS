# Concat 0.2.9 Android — Microsoft Edge TTS (繁體中文)

This personal fork adds an **online** Microsoft Edge TTS voice provider to Concat's Android speech dialog, through the system-voice adapter already provided in Concat. It does not embed a language model.

* Voices: 雲哲 (3000), 曉臻 (3001), 曉雨 (3002)
* Result: 24 kHz mono PCM WAV placed into Concat's project audio folder
* Compatible with the existing timeline import workflow
* Requires a local Termux helper running at **127.0.0.1:18729** and internet access
* Concat itself does not send videos or project files to Microsoft; **the text you request to synthesize is sent to Microsoft**.
* This Edge TTS route is unofficial, may change without notice, and is distinct from an Azure Speech subscription.
* Android application id is `app.concat.edge`: installs **alongside** the original `app.concat.editor`, preserving the original app's data. Project folders are separate unless you import them.

## Phone setup

Termux on the phone:

```bash
pkg install python ffmpeg
python -m venv ~/concat-edge-env
~/concat-edge-env/bin/pip install edge-tts
mkdir -p ~/concat-edge
cp server.py ~/concat-edge/server.py
nohup ~/concat-edge-env/bin/python ~/concat-edge/server.py > ~/concat-edge/bridge.log 2>&1 &
curl http://127.0.0.1:18729/health
```

Start the Termux helper again if Android kills the Termux process or the phone restarts.

In the modified Concat: Settings → Speech → **Microsoft Edge TTS (online)**, then Text-to-Speech → choose one of the three Mandarin voices and generate.

The APK is built on GitHub Actions Linux x86_64 because the official Android NDK toolchain is not prepared for native ARM64 Termux compilation.

## Local test

```bash
curl -fsS -H 'Content-Type: application/json' \
  -d '{"text":"這是一個測試。","voice":3001,"speed":1.0}' \
  http://127.0.0.1:18729/tts > test.wav
ffprobe -v error test.wav
```

This is a personal modification, not an official Concat release. Original project license: AGPL-3.0-or-later.

## Reliability update (2026-11-11)

Microsoft Edge TTS may intermittently return NoAudioReceived for particular voices. The companion now retries the requested voice once before falling back. For YunJhe (Taiwan Mandarin male), the fallback is Yunyang (mainland Mandarin male), so the accent may differ. Other voices fall back to another zh-TW female voice. The WAV response carries X-TTS-Voice-Actual and X-TTS-Fallback diagnostic headers; Concat currently ignores these headers, so consult the Termux bridge log to see which voice was actually used.
