# voice_compression.py
import wave
import zlib
import audioop
import struct
import numpy as np

QUALITY_TO_RATE = {
    "Ultra Low": 4000,
    "Very Low": 8000,
    "Low": 11025,
}

def ultra_compress_wav(wav_path, quality="Low", log=lambda *a, **k: None):
    """Read WAV -> (optional) downsample -> (optional) bit-depth reduce -> dyn-range compress -> header -> zlib."""
    with wave.open(wav_path, 'rb') as wf:
        channels = wf.getnchannels()
        sample_width = wf.getsampwidth()
        sample_rate = wf.getframerate()
        frames = wf.readframes(wf.getnframes())

    target_rate = QUALITY_TO_RATE.get(quality, 11025)

    # Downsample if needed
    if sample_rate > target_rate:
        frames, sample_rate = downsample_audio(frames, channels, sample_width, sample_rate, target_rate)

    # Reduce bit depth for Ultra Low
    if quality == "Ultra Low" and sample_width > 1:
        frames = audioop.lin2lin(frames, sample_width, 1)  # 16-bit -> 8-bit
        sample_width = 1

    # Gentle dynamic range compression to help zlib
    if quality in ("Ultra Low", "Very Low"):
        thr, ratio = (0.6, 0.7) if quality == "Ultra Low" else (0.7, 0.8)
        frames = compress_dynamic_range(frames, sample_width, thr, ratio)

    # Tiny self-describing header: 1 byte length + "rate,channels,width"
    header = f"{sample_rate},{channels},{sample_width}".encode()
    data_with_header = struct.pack('!B', len(header)) + header + frames

    compressed = zlib.compress(data_with_header, 9)
    log(f"Compressed audio: {len(frames)} -> {len(compressed)} bytes")
    return compressed

def downsample_audio(frames, channels, sample_width, original_rate, target_rate):
    """Downsample PCM using stdlib audioop.ratecv (includes a simple low-pass)."""
    try:
        out, _ = audioop.ratecv(frames, sample_width, channels, original_rate, target_rate, None)
        return out, target_rate
    except Exception:
        # Fallback: return original if conversion fails
        return frames, original_rate

def compress_dynamic_range(frames, sample_width, threshold=0.6, ratio=0.7):
    """Simple soft-knee style compression in the amplitude domain."""
    try:
        if sample_width == 1:           # unsigned 8-bit
            arr = np.frombuffer(frames, dtype=np.uint8).astype(np.int16) - 128
            max_val = 128.0
        else:                            # signed 16-bit
            arr = np.frombuffer(frames, dtype=np.int16)
            max_val = 32768.0

        norm = arr.astype(np.float32) / max_val
        comp = np.empty_like(norm)

        # Soft compression
        over = np.abs(norm) > threshold
        comp[~over] = norm[~over]
        # Positive
        pos = (norm > threshold)
        comp[pos] = threshold + (norm[pos] - threshold) * ratio
        # Negative
        neg = (norm < -threshold)
        comp[neg] = -threshold + (norm[neg] + threshold) * ratio

        if sample_width == 1:
            result = (comp * 128 + 128).clip(0, 255).astype(np.uint8).tobytes()
        else:
            result = (comp * 32768).clip(-32768, 32767).astype(np.int16).tobytes()

        return result
    except Exception:
        return frames
