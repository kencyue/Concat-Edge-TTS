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

Microsoft Edge TTS may intermittently return `NoAudioReceived`. **Strict voice mode:** the companion retries only the selected voice, for up to three attempts. The YunJhe selection always uses `zh-TW-YunJheNeural`; HsiaoChen and HsiaoYu are equally fixed to their respective voice IDs. If the upstream service remains unavailable, the request fails clearly rather than silently substituting a different voice or accent. `X-TTS-Voice-Requested` and `X-TTS-Voice-Actual` remain identical; `X-TTS-Fallback` is always `false`. This is a companion-only update and does not require reinstalling the APK.


## 繁體中文字幕整合版（2026-11-11）

正式 APK 名稱：`kencat.apk`，App 名稱：**kencat**

- 套件 ID：`app.concat.zh`。與既有 `app.concat.edge`、官方 `app.concat.editor` 並存，不覆蓋原有專案資料。三個 App 的私人設定與專案目錄彼此獨立。
- 內附 Noto Sans TC Regular + SemiBold，首次建立的新字幕／文字預設使用 Noto Sans TC。
- 文字面板新增 **同軌字幕全部套用此字型**：選取字幕、選字型、點按一次；只變更同一時間軸軌道中的文字片段字型，保留內容、時間、大小、顏色、陰影等，可一次復原。
- 首次開啟時將 Whisper 預設設為 Base (Multilingual)（模型代碼 `base`），字幕辨識語言設為中文（`zh`）。模型 **不包含在 APK 中**；App 啟動後首次下載約 148 MB，後續離線辨識。為免超出數據額度，首次建議 Wi-Fi 連線。
- TTS 仍為 Microsoft Edge TTS（雲哲、曉臻、曉雨），需要原先已配置的 Termux 本機橋接服務 `127.0.0.1:18729` 和 Internet。
- Whisper 可辨識中文但 **不保證輸出繁體字**；Noto Sans TC 是字型，不是簡繁轉換器。本版本沒有聲稱已加入簡轉繁功能。
- Noto Sans TC 授權 SIL Open Font License，Build CI 由 Google Fonts 官方原始檔產生靜態字重，授權文字隨字型資源內嵌於專案建置樹。


## kencat zh2 — 同一音色的短句補救

* 同一句話可能在 Microsoft Edge TTS 間歇性無音訊（NoAudioReceived）。這不是 Android 或 WAV 格式故障。
* 正常情況優先整句合成，保留自然語氣。若整句連續三次失敗，短句（最多 48 字元）會切成同一個音色的字音並以 24 kHz WAV 重組；分段補救的語調可能較不連貫。
* 僅對無法朗讀的「啟／啓」使用同音「起」作為語音輸入，畫面字幕及原文字不變。
* 絕不切換成其他 Microsoft 音色。App 端也會核對服務回傳的實際音色，若不一致則拒絕匯入音訊。
* 此版本不依賴新增語音模型，但仍需 Termux helper 與微軟線上 Edge TTS 連線。
