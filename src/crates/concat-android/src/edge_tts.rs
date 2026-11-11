// SPDX-License-Identifier: AGPL-3.0-or-later
//! Standalone Edge read-aloud for Android.
//!
//! This implementation runs inside the APK (no Termux, Python, localhost
//! bridge or external executable). Only narration text is sent to Microsoft;
//! source media and projects remain on the phone. Microsoft's unofficial
//! read-aloud protocol can change or become unavailable without notice.
//! TLS certificates use a bundled WebPKI trust store from the pinned
//! msedge-tts adaptation; there is no Android Kotlin verifier dependency.

use concat_media::samples::{AudioDecoder, AudioOptions, SampleFormat};
use msedge_tts::tts::{SpeechConfig, client};
use std::fs::{self, File};
use std::io::{Seek, SeekFrom, Write};
use std::path::{Path, PathBuf};
use std::sync::mpsc;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

const MAX_CHARS: usize = 3_900;
const MAX_MP3_BYTES: usize = 16 * 1024 * 1024;
const MAX_PCM_BYTES: usize = 64 * 1024 * 1024;
const SAMPLE_RATE: u32 = 24_000;
const TIMEOUT: Duration = Duration::from_secs(42);

fn selected_voice(id: i32) -> Result<&'static str, String> {
    match id {
        3000 => Ok("Microsoft Server Speech Text to Speech Voice (zh-TW, YunJheNeural)"),
        3001 => Ok("Microsoft Server Speech Text to Speech Voice (zh-TW, HsiaoChenNeural)"),
        3002 => Ok("Microsoft Server Speech Text to Speech Voice (zh-TW, HsiaoYuNeural)"),
        _ => Err(format!("不支援的語音編號：{id}")),
    }
}

fn xml_escape(value: &str) -> String {
    let mut escaped = String::with_capacity(value.len());
    for c in value.chars() {
        match c {
            '&' => escaped.push_str("&amp;"),
            '<' => escaped.push_str("&lt;"),
            '>' => escaped.push_str("&gt;"),
            '"' => escaped.push_str("&quot;"),
            '\'' => escaped.push_str("&apos;"),
            _ => escaped.push(c),
        }
    }
    escaped
}

fn request_mp3(text: &str, voice: &'static str, speed: f32) -> Result<Vec<u8>, String> {
    let config = SpeechConfig {
        voice_name: voice.to_owned(),
        audio_format: "audio-24khz-48kbitrate-mono-mp3".to_owned(),
        pitch: 0,
        rate: ((speed - 1.0) * 100.0).round() as i32,
        volume: 0,
    };
    let utterance = xml_escape(text);
    // The network library has blocking reads. Limit the caller's wait so
    // unavailable cloud voices do not stall an entire subtitle batch.
    // Timed-out workers never write project files.
    let (sender, receiver) = mpsc::sync_channel(1);
    std::thread::Builder::new()
        .name("kencat-edge-tts".to_owned())
        .spawn(move || {
            let result = (|| {
                let mut connection =
                    client::connect().map_err(|e| format!("Edge TTS 連線失敗：{e}"))?;
                let result = connection
                    .synthesize(&utterance, &config)
                    .map_err(|e| format!("Edge TTS 語音生成失敗：{e}"))?;
                if result.audio_bytes.is_empty() {
                    return Err("Microsoft 沒有回傳語音（NoAudioReceived）".to_owned());
                }
                if result.audio_bytes.len() > MAX_MP3_BYTES {
                    return Err("語音檔超過 16 MiB 限制".to_owned());
                }
                Ok(result.audio_bytes)
            })();
            let _ = sender.send(result);
        })
        .map_err(|e| format!("無法啟動配音工作：{e}"))?;
    receiver
        .recv_timeout(TIMEOUT)
        .map_err(|_| "Edge TTS 等待逾時，請檢查網路後重試".to_owned())?
}

struct TemporaryFile(PathBuf);
impl Drop for TemporaryFile {
    fn drop(&mut self) {
        let _ = fs::remove_file(&self.0);
    }
}

fn convert_to_wav(mp3: &[u8], target: &Path) -> Result<(), String> {
    // Concat already bundles native FFmpeg for media decoding. Reuse it
    // instead of requiring the Termux ffmpeg command.
    let unique = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(|e| e.to_string())?
        .as_nanos();
    let source =
        TemporaryFile(target.with_extension(format!("tts-{}-{unique}.mp3", std::process::id())));
    fs::write(&source.0, mp3).map_err(|e| format!("無法儲存暫存語音：{e}"))?;
    let options = AudioOptions {
        rate: SAMPLE_RATE,
        channels: 1,
        format: SampleFormat::I16,
        ..AudioOptions::default()
    };
    let mut decoder =
        AudioDecoder::open(&source.0, &options).map_err(|e| format!("無法開啟語音解碼器：{e}"))?;

    let temp =
        TemporaryFile(target.with_extension(format!("tts-{}-{unique}.wav", std::process::id())));
    let mut file = File::create(&temp.0).map_err(|e| format!("無法建立 WAV：{e}"))?;
    file.write_all(&[0u8; 44]).map_err(|e| e.to_string())?;
    let mut count = 0usize;
    while let Some(frame) = decoder
        .next_frame()
        .map_err(|e| format!("語音解碼失敗：{e}"))?
    {
        let samples = decoder.bytes_of(&frame);
        count = count.checked_add(samples.len()).ok_or("音訊資料過長")?;
        if count > MAX_PCM_BYTES {
            return Err("音訊超過 64 MiB 限制".to_owned());
        }
        file.write_all(&samples)
            .map_err(|e| format!("WAV 寫入失敗：{e}"))?;
    }
    if count < 2 || count % 2 != 0 {
        return Err("語音資料為空白或格式錯誤".to_owned());
    }
    // RIFF/WAVE, PCM16, 24 kHz, mono.
    let mut header = Vec::with_capacity(44);
    header.extend_from_slice(b"RIFF");
    header.extend_from_slice(&(36u32 + count as u32).to_le_bytes());
    header.extend_from_slice(b"WAVEfmt ");
    header.extend_from_slice(&16u32.to_le_bytes());
    header.extend_from_slice(&1u16.to_le_bytes());
    header.extend_from_slice(&1u16.to_le_bytes());
    header.extend_from_slice(&SAMPLE_RATE.to_le_bytes());
    header.extend_from_slice(&(SAMPLE_RATE * 2).to_le_bytes());
    header.extend_from_slice(&2u16.to_le_bytes());
    header.extend_from_slice(&16u16.to_le_bytes());
    header.extend_from_slice(b"data");
    header.extend_from_slice(&(count as u32).to_le_bytes());
    debug_assert_eq!(header.len(), 44);
    file.seek(SeekFrom::Start(0))
        .map_err(|e| format!("WAV 定位失敗：{e}"))?;
    file.write_all(&header).map_err(|e| e.to_string())?;
    file.sync_all().map_err(|e| e.to_string())?;
    drop(file);
    fs::rename(&temp.0, target).map_err(|e| format!("無法完成配音輸出：{e}"))?;
    Ok(())
}

pub fn synthesize(text: &str, output: &Path, speed: f32, id: i32) -> Result<(), String> {
    let voice = selected_voice(id)?;
    let text = text.trim();
    if text.is_empty() || text.chars().count() > MAX_CHARS {
        return Err("配音文字長度必須介於 1–3900 字".to_owned());
    }
    if !speed.is_finite() || !(0.5..=2.0).contains(&speed) {
        return Err("語速必須介於 0.5 至 2.0".to_owned());
    }

    let tries = if id == 3000 { 2 } else { 1 };
    let mut last_failure = String::new();
    for attempt in 0..tries {
        if attempt > 0 {
            // Avoid retrying instantly into a potentially overloaded voice backend.
            std::thread::sleep(Duration::from_secs(2));
        }
        match request_mp3(text, voice, speed) {
            Ok(mp3) => return convert_to_wav(&mp3, output),
            Err(why) => {
                log::warn!(
                    "Edge TTS voice={voice} attempt={}/{}: {why}",
                    attempt + 1,
                    tries
                );
                last_failure = why;
            }
        }
    }
    Err(format!(
        "{voice}：{last_failure}；已嘗試 {tries} 次，未拆句、未自動更換音色"
    ))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn xml_escaping_preserves_traditional_chinese_text() {
        assert_eq!(xml_escape("雲哲 & <字幕>"), "雲哲 &amp; &lt;字幕&gt;");
    }

    #[test]
    fn voice_ids_are_explicit() {
        assert!(
            selected_voice(3000)
                .unwrap()
                .contains("(zh-TW, YunJheNeural)")
        );
        assert!(selected_voice(3001).is_ok());
        assert!(selected_voice(3002).is_ok());
        assert!(selected_voice(3010).is_err());
    }
}
