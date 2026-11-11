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


# A bounded RAM-only cache avoids repeated online generation when a user
# reopens/reuses the exact same line, speaker and speed. Nothing is written to
# storage and no private text is logged.
from collections import OrderedDict
from threading import Lock

_CACHE = OrderedDict()
_CACHE_BYTES = 0
_CACHE_LIMIT_BYTES = 24 * 1024 * 1024
_CACHE_LOCK = Lock()
_METRICS = {"yun_jhe_attempts": 0, "yun_jhe_retry_success": 0,
            "yun_jhe_no_audio": 0, "yun_jhe_retry_exhausted": 0}
_METRICS_LOCK = Lock()


def _count(name):
    with _METRICS_LOCK:
        _METRICS[name] += 1


def metrics_snapshot():
    with _METRICS_LOCK:
        return dict(_METRICS)


def cache_get(key):
    with _CACHE_LOCK:
        item = _CACHE.get(key)
        if item is not None:
            _CACHE.move_to_end(key)
        return item


def cache_put(key, wav):
    global _CACHE_BYTES
    if len(wav) > _CACHE_LIMIT_BYTES:
        return
    with _CACHE_LOCK:
        if key in _CACHE:
            _CACHE_BYTES -= len(_CACHE.pop(key))
        _CACHE[key] = wav
        _CACHE_BYTES += len(wav)
        while _CACHE_BYTES > _CACHE_LIMIT_BYTES:
            _, old = _CACHE.popitem(last=False)
            _CACHE_BYTES -= len(old)


async def get_audio(text, requested_voice, voice_id, rate, folder):
    """Speak the complete text. YunJhe gets one bounded same-voice retry.

    Microsoft sometimes closes the WebSocket without audio. Never segment
    text or switch voices: a conditional second whole-sentence request is the
    only recovery, and only for YunJhe (the flaky voice).
    """
    yun_jhe = voice_id == 3000
    attempts = 2 if yun_jhe else 1
    for n in range(attempts):
        out = Path(folder) / f"whole-{n}.mp3"
        try:
            if yun_jhe:
                _count("yun_jhe_attempts")
            await asyncio.wait_for(
                edge_tts.Communicate(text, requested_voice, rate=rate).save(str(out)),
                timeout=6.0,
            )
            if not out.exists() or out.stat().st_size == 0:
                raise NoAudioReceived("empty audio file")
            if n:
                _count("yun_jhe_retry_success")
                logging.info("YunJhe recovered on second complete-utterance request")
            return out, requested_voice
        except (NoAudioReceived, asyncio.TimeoutError) as err:
            if yun_jhe:
                _count("yun_jhe_no_audio")
            logging.warning("Edge TTS no audio voice=%s attempt=%d/%d: %s",
                            requested_voice, n + 1, attempts, type(err).__name__)
            if n + 1 < attempts:
                await asyncio.sleep(0.25)
                continue
            if yun_jhe:
                _count("yun_jhe_retry_exhausted")
            raise RuntimeError(
                f"Microsoft 雲端音色 {requested_voice} 沒有回傳完整語音。"
                f"已嘗試 {attempts} 次相同音色，未拆句、未換人聲。請稍後再試。"
            ) from err
    raise RuntimeError("Unexpected speech state")


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
                     "no_audio_retry": "one conditional retry for YunJhe only",
                     "whole_sentence_only": True, "cache": "memory",
                     "metrics": metrics_snapshot()},
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

            key = (txt, requested_voice, rate)
            wav_bytes = cache_get(key)
            hit = wav_bytes is not None
            if not hit:
                with tempfile.TemporaryDirectory(prefix="concat-edge-") as folder:
                    mp3, actual_voice = asyncio.run(
                        get_audio(txt, requested_voice, voice_id, rate, folder)
                    )
                    wav = Path(folder) / "speech.wav"
                    subprocess.run(
                        ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
                         "-y", "-i", str(mp3), "-ar", "24000", "-ac", "1",
                         "-c:a", "pcm_s16le", str(wav)],
                        check=True, timeout=20, capture_output=True,
                    )
                    wav_bytes = wav.read_bytes()
                    if not wav_bytes.startswith(b"RIFF") or wav_bytes[8:12] != b"WAVE":
                        raise RuntimeError("Audio conversion did not return a valid WAV")
                    cache_put(key, wav_bytes)
            self.respond(
                200, wav_bytes, "audio/wav",
                extra_headers={
                    "X-TTS-Voice-Requested": requested_voice,
                    "X-TTS-Voice-Actual": requested_voice,
                    "X-TTS-Fallback": "false",
                    "X-TTS-Cache": "HIT" if hit else "MISS",
                },
            )
            logging.info(
                "Synthesized %s characters: requested=%s actual=%s speed=%s",
                len(txt), requested_voice, requested_voice, speed,
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
