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


async def get_audio(text, requested_voice, voice_id, rate, folder):
    """Strict voice identity: never substitute another voice, even on failure.

    Microsoft Edge TTS occasionally returns no audio. Retry only the selected
    voice three times in total; propagate an error if it remains unavailable.
    """
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
                logging.info(
                    "Edge TTS recovered with same voice=%s on attempt=%s",
                    requested_voice, n + 1,
                )
            return output, requested_voice
        except (NoAudioReceived, asyncio.TimeoutError) as err:
            logging.warning(
                "Edge TTS selected voice=%s failed (%s/%s): %s",
                requested_voice, n + 1, attempts, err,
            )
            if n == attempts - 1:
                raise RuntimeError(
                    f"指定音色 {requested_voice} 暫時無法產生語音；"
                    "已重試三次，未切換成其他音色。請稍後重試。"
                ) from err
            await asyncio.sleep(0.6 + n * 0.8)


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
                     "no_audio_retry": True},
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
                subprocess.run(
                    ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
                     "-y", "-i", str(mp3), "-ar", "24000", "-ac", "1",
                     "-c:a", "pcm_s16le", str(wav)],
                    check=True, timeout=60, capture_output=True,
                )
                self.respond(
                    200, wav.read_bytes(), "audio/wav",
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
