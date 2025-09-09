# encoding/codec2_codec.py
import struct
import wave
import numpy as np

try:
    from codec2 import Codec2
except Exception:
    Codec2 = None

try:
    from scipy.signal import resample_poly
except Exception:
    resample_poly = None


QUALITY_TO_BPS = {
    "Ultra Low": 700,
    "Very Low": 1200,
    "Low": 1600,
}

def _to_mono_int16(pcm_bytes: bytes, channels: int, sampwidth: int) -> np.ndarray:
    if sampwidth == 2:
        x = np.frombuffer(pcm_bytes, dtype=np.int16)
    elif sampwidth == 1:
        x_u8 = np.frombuffer(pcm_bytes, dtype=np.uint8).astype(np.int16)
        x = (x_u8 - 128) << 8
    else:
        raise ValueError(f"Unsupported sample width: {sampwidth}")

    if channels == 1:
        return x

    n = len(x) // channels
    x = x[:n*channels].reshape(n, channels)
    mono = np.mean(x.astype(np.int32), axis=1).astype(np.int16)
    return mono

def _resample_to_8k(x_int16: np.ndarray, in_rate: int) -> np.ndarray:
    out_rate = 8000
    if in_rate == out_rate:
        return x_int16
    if resample_poly is not None:
        from math import gcd
        g = gcd(in_rate, out_rate)
        up, down = out_rate // g, in_rate // g
        y = resample_poly(x_int16.astype(np.float32), up, down)
        return np.clip(np.round(y), -32768, 32767).astype(np.int16)
    # linear fallback
    n_in = len(x_int16)
    dur = n_in / float(in_rate)
    n_out = int(round(dur * out_rate))
    if n_out <= 1 or n_in <= 1:
        return x_int16
    xi = np.linspace(0.0, 1.0, n_in, endpoint=True)
    xo = np.linspace(0.0, 1.0, n_out, endpoint=True)
    y = np.interp(xo, xi, x_int16.astype(np.float32))
    return np.clip(np.round(y), -32768, 32767).astype(np.int16)

def encode_to_codec2(wav_path: str, ui_quality: str = "Low", log=lambda *a, **k: None) -> bytes | None:
    if Codec2 is None:
        log("Codec2 not available: `pip install codec2`")
        return None
    try:
        with wave.open(wav_path, 'rb') as wf:
            ch = wf.getnchannels()
            sw = wf.getsampwidth()
            sr = wf.getframerate()
            pcm = wf.readframes(wf.getnframes())

        mono = _to_mono_int16(pcm, ch, sw)
        mono = _resample_to_8k(mono, sr)

        bps = QUALITY_TO_BPS.get(ui_quality, 1600)
        c2 = Codec2(bps)
        spf = c2.samples_per_frame
        bpf = (c2.bits_per_frame + 7) // 8

        rem = len(mono) % spf
        if rem:
            mono = np.pad(mono, (0, spf - rem), mode='constant')

        out = bytearray()
        for i in range(0, len(mono), spf):
            out.extend(c2.encode(mono[i:i+spf].tobytes()))

        header = f"8000,1,2,C2,{bps},{spf},{bpf}".encode("utf-8")
        if len(header) > 255:
            log("Unexpected long header.")
            return None

        payload = struct.pack("!B", len(header)) + header + bytes(out)
        log(f"Codec2: {len(mono)*2} PCM bytes -> {len(out)} C2 bytes @ {bps} bps")
        return payload
    except Exception as e:
        log(f"encode_to_codec2 error: {e}")
        return None

def decode_codec2_to_wav(blob: bytes, out_wav: str, log=lambda *a, **k: None) -> None:
    try:
        hlen = struct.unpack('!B', blob[0:1])[0]
        h = blob[1:1+hlen].decode('utf-8')
        audio = blob[1+hlen:]

        parts = h.split(',')
        if len(parts) < 7 or parts[3] != 'C2':
            raise ValueError("Not a Codec2 payload")

        samplerate = int(parts[0])
        channels   = int(parts[1])
        sampwidth  = int(parts[2])
        bps        = int(parts[4])
        spf        = int(parts[5])
        bpf        = int(parts[6])

        if Codec2 is None:
            raise RuntimeError("Codec2 not available on receiver")

        c2 = Codec2(bps)
        pcm = bytearray()
        for i in range(0, len(audio), bpf):
            chunk = audio[i:i+bpf]
            if len(chunk) < bpf:
                break
            pcm.extend(c2.decode(chunk))

        with wave.open(out_wav, 'wb') as wf:
            wf.setnchannels(channels)
            wf.setsampwidth(sampwidth)
            wf.setframerate(samplerate)
            wf.writeframes(pcm)

        log(f"Decoded Codec2 → {out_wav}")
    except ValueError:
        raise
    except Exception as e:
        log(f"decode_codec2_to_wav error: {e}")
        raise
