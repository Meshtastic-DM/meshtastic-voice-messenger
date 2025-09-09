# encoding/decoder.py
import struct
import zlib
import wave

from .codec2_codec import decode_codec2_to_wav
# legacy header is handled inline (rate,channels,width)

def decode_auto(voice_bytes: bytes, out_wav: str, log=lambda *a, **k: None) -> None:
    """
    Try Codec2 first (header 'C2' embedded), else fallback to legacy:
    [1B header_len][b"%d,%d,%d"] + raw PCM, zlib-compressed
    """
    # Try Codec2
    try:
        # If this is Codec2, decode function will succeed or raise ValueError
        decode_codec2_to_wav(voice_bytes, out_wav, log=log)
        return
    except ValueError:
        pass
    except Exception as e:
        log(f"Codec2 decode attempt failed: {e}")

    # Legacy path: zlib then tiny header then PCM
    try:
        data = zlib.decompress(voice_bytes)
        hlen = struct.unpack('!B', data[0:1])[0]
        header = data[1:1+hlen]
        pcm = data[1+hlen:]

        rate_s, ch_s, sw_s = header.split(b',')
        rate = int(rate_s)
        ch   = int(ch_s)
        sw   = int(sw_s)

        with wave.open(out_wav, 'wb') as wf:
            wf.setnchannels(ch)
            wf.setsampwidth(sw)
            wf.setframerate(rate)
            wf.writeframes(pcm)

        log(f"Decoded legacy zlib+header → {out_wav}")
    except Exception as e:
        log(f"Legacy decode failed: {e}")
        raise
