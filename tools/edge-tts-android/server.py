#!/usr/bin/env python3
"""Loopback-only Edge TTS -> PCM WAV bridge for Concat Android.

Only narration text is sent to Microsoft's online Edge TTS service.
Videos, original audio and projects never leave this phone.
"""
import asyncio
import json
import logging
import subprocess
import tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import edge_tts
from edge_tts.exceptions import NoAudioReceived

HOST = "127.0.0.1"
PORT = 18729
VOICES = {
    3000: "zh-TW-YunJheNeural",
    3001: "zh-TW-HsiaoChenNeural",
    3002: "zh-TW-HsiaoYuNeural",
}
MAX_REQUEST = 30_000
MAX_CHARS = 3_900


# Only these phonetic aliases are used when the ORIGINAL character is
# rejected by Microsoft. They are homophones for Taiwan Mandarin.
# The user's actual title/caption text is never altered.
HOMOPHONES = {"啟": "起", "啓": "起"}
_SAMPLE_RATE = 24000
_SINGLE_CHAR_CACHE = {}


def _make_wav(samples):
    import io
    import wave
    with io.BytesIO() as buf:
        with wave.open(buf, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(_SAMPLE_RATE)
            wav.writeframes(samples)
        return buf.getvalue()


def _decode_and_trim(mp3_file):
    """Decode MP3 to 24 kHz PCM, remove dead air around each phoneme."""
    import array
    raw = subprocess.run(
        ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
         "-i", str(mp3_file), "-f", "s16le", "-ac", "1", "-ar", str(_SAMPLE_RATE), "-"],
        check=True, timeout=30, capture_output=True,
    ).stdout
    if not raw:
        raise RuntimeError("Edge TTS returned an empty PCM segment")
    # 10 ms windows, keep 50 ms around the first/last speech sample.
    audio = array.array("h")
    audio.frombytes(raw)
    if not audio:
        raise RuntimeError("Empty speech segment")
    block = 240
    peaks = [max(abs(v) for v in audio[i:i + block])
             for i in range(0, len(audio), block)]
    threshold = max(100, int(max(peaks) * 0.018))
    loud = [i for i, peak in enumerate(peaks) if peak >= threshold]
    if not loud:
        raise RuntimeError("Selected voice returned silent audio")
    first = max(0, (loud[0] - 5) * block)
    last = min(len(audio), (loud[-1] + 6) * block)
    return audio[first:last].tobytes()


async def _single_char(char, voice, rate, folder, index):
    key = (char, voice, rate)
    if key in _SINGLE_CHAR_CACHE:
        return _SINGLE_CHAR_CACHE[key]
    for attempt, candidate in enumerate([char] + ([HOMOPHONES[char]] if char in HOMOPHONES else [])):
        mp3 = Path(folder) / f"char-{index}-{attempt}.mp3"
        try:
            await asyncio.wait_for(
                edge_tts.Communicate(candidate, voice, rate=rate).save(str(mp3)), timeout=30
            )
            if not mp3.exists() or mp3.stat().st_size == 0:
                raise NoAudioReceived("Empty voice segment")
            pcm = _decode_and_trim(mp3)
            if candidate != char:
                logging.warning("Phonetic alias in same voice: %s -> %s (voice %s)", char, candidate, voice)
            if len(_SINGLE_CHAR_CACHE) > 256:
                _SINGLE_CHAR_CACHE.clear()
            _SINGLE_CHAR_CACHE[key] = pcm
            return pcm
        except (NoAudioReceived, asyncio.TimeoutError):
            if attempt == 0 and char in HOMOPHONES:
                continue
            raise RuntimeError(f"雲端語音無法正確朗讀字元「{char}」；未切換音色")
    raise RuntimeError(f"無法朗讀字元「{char}」")


async def _fallback_to_syllables(text, requested_voice, rate, folder):
    """Last resort: speak individual characters in THE SAME voice, join to WAV.

    Avoids any silent switch to a different Microsoft voice. This is intended
    for rare short lines with Edge service failures; it cannot imitate the
    full-sentence prosody, so avoid using it if direct synthesis succeeds.
    """
    import unicodedata
    chars = list(text)
    if len(chars) > 48:
        raise RuntimeError("原句音色產生失敗，文字過長，無法使用同音色分段模式")
    parts = []
    # 30-50 ms gaps smooth out separately synthesized syllables.
    tiny_gap = bytes(int(_SAMPLE_RATE * 0.035) * 2)
    pause = bytes(int(_SAMPLE_RATE * 0.12) * 2)
    for i, char in enumerate(chars):
        if char.isspace():
            parts.append(tiny_gap)
        elif unicodedata.category(char).startswith("P"):
            parts.append(pause)
        else:
            parts.append(await _single_char(char, requested_voice, rate, folder, i))
            parts.append(tiny_gap)
    if not parts:
        raise RuntimeError("分段語音無有效字元")
    out = Path(folder) / "reconstructed.wav"
    out.write_bytes(_make_wav(b"".join(parts)))
    logging.warning(
        "Edge TTS full phrase failed; reconstructed %s characters using ONLY %s",
        len(chars), requested_voice,
    )
    return out


async def get_audio(text, requested_voice, voice_id, rate, folder):
    """Never switch voices; if the full phrase fails, salvage via syllables."""
    attempts = 3
    for n in range(attempts):
        output = Path(folder) / f"attempt{n}.mp3"
        try:
            await asyncio.wait_for(
                edge_tts.Communicate(text, requested_voice, rate=rate).save(str(output)),
                timeout=45.0,
            )
            if not output.exists() or output.stat().st_size == 0:
                raise NoAudioReceived("Audio file is empty")
            if n > 0:
                logging.info("Edge TTS same voice recovered on attempt %s", n + 1)
            return output, requested_voice
        except (NoAudioReceived, asyncio.TimeoutError) as err:
            logging.warning("Edge TTS voice %s full phrase attempt %s/%s: %s",
                            requested_voice, n + 1, attempts, err)
            if n == attempts - 1:
                break
            await asyncio.sleep(0.6 + n * 0.8)
    # Reconstruct only short utterances, using the same selected voice.
    audio = await _fallback_to_syllables(text, requested_voice, rate, folder)
    return audio, requested_voice


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def respond(self, status, body, content_type="text/plain; charset=utf-8", extra_headers=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            self.respond(
                200,
                json.dumps(
                    {"status": "ok", "provider": "Microsoft Edge TTS",
                     "voices": VOICES, "strict_voice": True,
                     "no_audio_retry": True, "same_voice_syllable_recovery": True},
                    ensure_ascii=False,
                ),
                "application/json; charset=utf-8",
            )
        else:
            self.respond(404, "Not found")

    def do_POST(self):
        if self.path != "/tts":
            return self.respond(404, "Not found")
        try:
            length = int(self.headers.get("Content-Length", "-1"))
            if length < 2 or length > MAX_REQUEST:
                return self.respond(413, "Invalid request size")
            payload = json.loads(self.rfile.read(length))
            voice_id = int(payload.get("voice", 3000))
            requested_voice = VOICES.get(voice_id)
            if requested_voice is None:
                return self.respond(400, "Unsupported voice")
            txt = str(payload["text"]).strip()
            if not txt or len(txt) > MAX_CHARS:
                return self.respond(400, "Text must be 1-3900 characters")
            speed = float(payload.get("speed", 1.0))
            if not (0.5 <= speed <= 2.0):
                return self.respond(400, "Speed must be 0.5-2.0")
            rate = f"{round((speed - 1.0) * 100):+d}%"

            with tempfile.TemporaryDirectory(prefix="concat-edge-") as folder:
                mp3, actual_voice = asyncio.run(
                    get_audio(txt, requested_voice, voice_id, rate, folder)
                )
                wav = Path(folder) / "speech.wav"
                if mp3.suffix == ".wav":
                    wav_bytes = mp3.read_bytes()
                else:
                    subprocess.run(
                        ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
                         "-y", "-i", str(mp3), "-ar", "24000", "-ac", "1",
                         "-c:a", "pcm_s16le", str(wav)],
                        check=True, timeout=60, capture_output=True,
                    )
                    wav_bytes = wav.read_bytes()
                self.respond(
                    200, wav_bytes, "audio/wav",
                    extra_headers={
                        "X-TTS-Voice-Requested": requested_voice,
                        "X-TTS-Voice-Actual": actual_voice,
                        "X-TTS-Fallback": str(actual_voice != requested_voice).lower(),
                    },
                )
            logging.info(
                "Synthesized %s characters: requested=%s actual=%s speed=%s",
                len(txt), requested_voice, actual_voice, speed,
            )
        except Exception as exc:
            logging.exception("Edge TTS request failed")
            self.respond(502, "Edge TTS failed: " + str(exc)[:500])

    def log_message(self, fmt, *args):
        logging.info("%s %s", self.address_string(), fmt % args)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
