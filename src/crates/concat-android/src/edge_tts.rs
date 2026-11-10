// SPDX-License-Identifier: AGPL-3.0-or-later
//! Microsoft Edge TTS adapter for the Android Concat speech interface.
//!
//! The phone's Termux companion serves only 127.0.0.1:18729. It uses
//! edge-tts to talk to Microsoft's online synthesizer, then returns a WAV.
//! Media and project files never leave the phone; ONLY narration text does.

use std::io::{Read, Write};
use std::net::{SocketAddr, TcpStream};
use std::path::Path;
use std::time::Duration;

pub fn synthesize(text: &str, output: &Path, speed: f32, voice: i32) -> Result<(), String> {
    let address = SocketAddr::from(([127, 0, 0, 1], 18729));
    let mut socket =
        TcpStream::connect_timeout(&address, Duration::from_secs(3)).map_err(|error| {
            format!("Microsoft TTS helper is not running in Termux (127.0.0.1:18729): {error}")
        })?;
    socket
        .set_read_timeout(Some(Duration::from_secs(180)))
        .map_err(|error| format!("could not set TTS timeout: {error}"))?;
    socket
        .set_write_timeout(Some(Duration::from_secs(10)))
        .map_err(|error| format!("could not set TTS write timeout: {error}"))?;
    let body = serde_json::json!({"text":text, "voice": voice, "speed": speed}).to_string();
    let headers = format!(
        "POST /tts HTTP/1.0\r\nHost: 127.0.0.1:18729\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
        body.len()
    );
    socket
        .write_all(headers.as_bytes())
        .and_then(|()| socket.write_all(body.as_bytes()))
        .map_err(|error| format!("could not send text to Microsoft TTS helper: {error}"))?;
    let mut data = Vec::new();
    socket
        .take(64 * 1024 * 1024)
        .read_to_end(&mut data)
        .map_err(|error| format!("could not receive TTS voice: {error}"))?;
    let end = data
        .windows(4)
        .position(|window| window == b"\r\n\r\n")
        .ok_or("invalid TTS helper response (no HTTP header)")?;
    let (header, payload) = data.split_at(end + 4);
    let status = String::from_utf8_lossy(header);
    if !(status.starts_with("HTTP/1.0 200 ") || status.starts_with("HTTP/1.1 200 ")) {
        let error = String::from_utf8_lossy(payload);
        return Err(format!(
            "Microsoft TTS request failed: {}",
            error.chars().take(300).collect::<String>()
        ));
    }
    if !payload.starts_with(b"RIFF") || payload.get(8..12) != Some(&b"WAVE"[..]) {
        return Err("Microsoft TTS helper did not return a WAV file".to_owned());
    }
    std::fs::write(output, payload)
        .map_err(|error| format!("could not save Microsoft TTS WAV: {error}"))?;
    Ok(())
}
