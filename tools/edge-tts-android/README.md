# Concat 0.2.9 Android — Microsoft Edge TTS (繁體中文)

This personal fork adds an **online** Microsoft Edge TTS voice provider to Concat's Android speech dialog, through the system-voice adapter already provided in Concat. It does not embed a language model.

* Voices: 雲哲 (3000), 曉臻 (3001), 曉雨 (3002)
* Result: 24 kHz mono PCM WAV placed into Concat's project audio folder
* Compatible with the existing timeline import workflow
* Requires an internet connection only. Current builds embed the native TTS client without a local server or Termux
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

Microsoft Edge TTS may intermittently return `NoAudioReceived`. **Strict voice mode:** the companion now makes one complete-utterance attempt, without automatic retries or segmentation. The YunJhe selection always uses `zh-TW-YunJheNeural`; HsiaoChen and HsiaoYu are equally fixed to their respective voice IDs. If the upstream service remains unavailable, the request fails clearly rather than silently substituting a different voice or accent. `X-TTS-Voice-Requested` and `X-TTS-Voice-Actual` remain identical; `X-TTS-Fallback` is always `false`. This is a companion-only update and does not require reinstalling the APK.


## 繁體中文字幕整合版（2026-11-11）

正式 APK 名稱：`kencat.apk`，App 名稱：**kencat**

- 套件 ID：`app.concat.zh`。與既有 `app.concat.edge`、官方 `app.concat.editor` 並存，不覆蓋原有專案資料。三個 App 的私人設定與專案目錄彼此獨立。
- 內附 Noto Sans TC Regular + SemiBold，首次建立的新字幕／文字預設使用 Noto Sans TC。
- 文字面板新增 **同軌字幕全部套用此字型**：選取字幕、選字型、點按一次；只變更同一時間軸軌道中的文字片段字型，保留內容、時間、大小、顏色、陰影等，可一次復原。
- 首次開啟時將 Whisper 預設設為 Base (Multilingual)（模型代碼 `base`），字幕辨識語言設為中文（`zh`）。模型 **不包含在 APK 中**；App 啟動後首次下載約 148 MB，後續離線辨識。為免超出數據額度，首次建議 Wi-Fi 連線。
- TTS 仍為 Microsoft Edge TTS（雲哲、曉臻、曉雨），需要原先已配置的 Termux 本機橋接服務 `127.0.0.1:18729` 和 Internet。
- Whisper 可辨識中文但 **不保證輸出繁體字**；Noto Sans TC 是字型，不是簡繁轉換器。本版本沒有聲稱已加入簡轉繁功能。
- Noto Sans TC 授權 SIL Open Font License，Build CI 由 Google Fonts 官方原始檔產生靜態字重，授權文字隨字型資源內嵌於專案建置樹。


## kencat zh2 — 自然語音優先／快速完整句朗讀

* POCO 的 TTS helper 使用 Microsoft Edge TTS **只生成完整原句**，絕不拆字、分段拼接、改寫文字或切換音色。
* 曉臻、曉雨正常仍只呼叫一次；只有雲哲遇到 `NoAudioReceived`／逾時時，會以相同文字及相同音色最多再重試一次。每次連線最長 6 秒、兩次間隔 0.25 秒；不再拆句、不換人聲。
* 最近成功的整句配音使用手機記憶體快取（最多約 24 MiB）；同樣文字、音色、語速再產生時可立即返回。重啟 Termux 會清除快取，不存字幕內容到檔案。
* 台灣中文選擇雲哲，就永遠使用 `zh-TW-YunJheNeural`。Android 端仍驗證實際音色回應標頭。
* 保留手機素材庫安全移除按鈕、字幕長度提醒及所有既有字幕字型功能。
* **音檔本身只在成功時保留**；在微軟雲端不穩定時，部分請求可能需要稍後手動重試，無法保證每次都成功。
