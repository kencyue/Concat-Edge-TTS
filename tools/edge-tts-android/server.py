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


# Preserve the source text exactly: no aliasing, homophones or voice switches.
_SAMPLE_RATE = 24000


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


async def _synthesize_piece(text, voice, rate, folder, index, depth=0):
    """Synthesize exact source substring with the selected voice only.

    Split large phrases further only when Edge TTS returned no audio.
    Never rewrite a word or change the speaker.
    """
    file = Path(folder) / f"phrase-{index}-{depth}.mp3"
    for attempt in range(2):
        try:
            if file.exists():
                file.unlink()
            await asyncio.wait_for(
                edge_tts.Communicate(text, voice, rate=rate).save(str(file)), timeout=35
            )
            if not file.exists() or file.stat().st_size == 0:
                raise NoAudioReceived("No audio in phrase")
            return _decode_and_trim(file)
        except (NoAudioReceived, asyncio.TimeoutError) as exc:
            if attempt < 1:
                await asyncio.sleep(0.4)
    if len(text) < 3 or depth >= 4:
        raise RuntimeError(
            f"指定音色 {voice} 無法朗讀「{text}」；已保留原文及音色，請稍後重試。"
        )
    mid = len(text) // 2
    first = await _synthesize_piece(text[:mid], voice, rate, folder, index + "L", depth + 1)
    last = await _synthesize_piece(text[mid:], voice, rate, folder, index + "R", depth + 1)
    return first + bytes(int(_SAMPLE_RATE * 0.035) * 2) + last


async def _fallback_to_phrases(text, selected_voice, rate, folder):
    """Reconstruct only by ORIGINAL text segments using the same voice.

    Segment at Chinese punctuation first to avoid single-character prosody.
    If a segment remains unavailable, recursively shorten its boundaries.
    """
    import re

    if len(text) > 96:
        raise RuntimeError("指定音色整句合成失敗，內容過長，請分成較短的句子。")
    tokens = re.split(r"([，。！？、；：,.!?;:\n])", text)
    pieces = []
    pause = bytes(int(_SAMPLE_RATE * 0.11) * 2)
    for idx, segment in enumerate(tokens):
        if not segment:
            continue
        if re.fullmatch(r"[，。！？、；：,.!?;:\n]", segment):
            pieces.append(pause)
        elif segment.isspace():
            pieces.append(pause)
        else:
            pieces.append(await _synthesize_piece(
                segment, selected_voice, rate, folder, str(idx)
            ))
            pieces.append(bytes(int(_SAMPLE_RATE * 0.035) * 2))
    if not pieces:
        raise RuntimeError("沒有可朗讀的文字")
    out = Path(folder) / "reconstructed.wav"
    out.write_bytes(_make_wav(b"".join(pieces)))
    logging.warning(
        "Strict-voice phrase recovery for %d chars using only %s (no text rewrite)",
        len(text), selected_voice
    )
    return out


async def get_audio(text, requested_voice, voice_id, rate, folder):
    """Never switch voices or rewrite text; split on phrase boundaries if needed."""
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
    # Split only the original text; honor the selected voice.
    audio = await _fallback_to_phrases(text, requested_voice, rate, folder)
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
                     "no_audio_retry": True, "same_voice_phrase_recovery": True},
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
