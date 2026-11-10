#!/usr/bin/env python3
"""Local-only Edge TTS -> PCM WAV bridge for Concat's Android native audio importer.

This service receives text from the patched Concat APK on 127.0.0.1:18729.
It sends text (NOT video) to Microsoft's online Edge TTS endpoint via edge-tts.
No API key; subject to Microsoft's endpoint availability and service terms.
"""
import asyncio
import json
import logging
import subprocess
import tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import edge_tts

HOST = "127.0.0.1"
PORT = 18729
VOICES = {
    3000: "zh-TW-YunJheNeural",
    3001: "zh-TW-HsiaoChenNeural",
    3002: "zh-TW-HsiaoYuNeural",
}
MAX_REQUEST = 30_000
MAX_CHARS = 3_900


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def respond(self, status, body, content_type="text/plain; charset=utf-8"):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            self.respond(200, json.dumps({"status": "ok", "provider": "Microsoft Edge TTS",
                                          "voices": VOICES}, ensure_ascii=False),
                         "application/json; charset=utf-8")
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
            voice = VOICES.get(voice_id)
            if voice is None:
                return self.respond(400, "Unsupported voice")
            txt = str(payload["text"]).strip()
            if not txt or len(txt) > MAX_CHARS:
                return self.respond(400, "Text must be 1-3900 characters")
            speed = float(payload.get("speed", 1.0))
            if not (0.5 <= speed <= 2.0):
                return self.respond(400, "Speed must be 0.5-2.0")
            rate = f"{round((speed - 1.0) * 100):+d}%"

            with tempfile.TemporaryDirectory(prefix="concat-edge-") as folder:
                mp3 = Path(folder) / "speech.mp3"
                wav = Path(folder) / "speech.wav"
                asyncio.run(edge_tts.Communicate(txt, voice, rate=rate).save(str(mp3)))
                subprocess.run(
                    ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
                     "-y", "-i", str(mp3), "-ar", "24000", "-ac", "1",
                     "-c:a", "pcm_s16le", str(wav)],
                    check=True, timeout=60, capture_output=True
                )
                self.respond(200, wav.read_bytes(), "audio/wav")
            logging.info("Synthesized %s chars, voice %s, speed %s", len(txt), voice, speed)
        except Exception as exc:
            logging.exception("Edge TTS request failed")
            self.respond(502, "Edge TTS failed: " + str(exc)[:500])

    def log_message(self, fmt, *args):
        logging.info("%s %s", self.address_string(), fmt % args)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
