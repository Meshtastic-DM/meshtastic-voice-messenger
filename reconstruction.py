# reconstruction.py
import base64
import zlib
import wave
import struct
import time
from datetime import datetime

def create_wav_from_compressed(compressed_data, filename, log=lambda *a, **k: None):
    """Parse header (len + 'rate,channels,width') + raw PCM and write a WAV."""
    header_size = struct.unpack('!B', compressed_data[0:1])[0]
    header = compressed_data[1:1 + header_size]
    audio = compressed_data[1 + header_size:]

    sr, ch, sw = [int(x) for x in header.split(b',')]

    with wave.open(filename, 'wb') as wf:
        wf.setnchannels(ch)
        wf.setsampwidth(sw)
        wf.setframerate(sr)
        wf.writeframes(audio)

    log(f"Created WAV file: {filename}")

def save_single_packet_base64(encoded_str, from_node, timestamp, log=lambda *a, **k: None):
    """Handle the single-message path: base64 -> zlib -> WAV."""
    comp = base64.b64decode(encoded_str)
    raw = zlib.decompress(comp)
    fname = f"voice_messages/received_{from_node}_{timestamp}.wav"
    create_wav_from_compressed(raw, fname, log)
    return fname

class ChunkReassembler:
    """Order-agnostic chunk store and reassembly."""

    def __init__(self, log=lambda *a, **k: None, timeout_seconds=180):
        self.log = log
        self.timeout = timeout_seconds
        self.messages = {}  # chunk_id -> dict

    def process_chunk(self, json_data, from_node):
        """
        Store chunk; when all pieces arrive, base64-decode -> zlib-decompress -> WAV.
        Returns output filename when completed, else None.
        """
        chunk_id = json_data['chunk_id']
        n = json_data['chunk_num']
        total = json_data['total_chunks']
        data = json_data['data']

        s = self.messages.get(chunk_id)
        if not s:
            s = self.messages[chunk_id] = {
                'chunks': {},
                'total': total,
                'from_node': from_node,
                'timestamp': datetime.now().strftime('%Y%m%d_%H%M%S'),
                'last_seen': time.time()
            }
        s['chunks'][n] = data
        s['last_seen'] = time.time()

        # Completed?
        if len(s['chunks']) == s['total']:
            try:
                ordered = ''.join(s['chunks'][i] for i in range(1, s['total'] + 1))
                comp = base64.b64decode(ordered)
                raw = zlib.decompress(comp)
                out = f"voice_messages/received_{s['from_node']}_{s['timestamp']}.wav"
                create_wav_from_compressed(raw, out, self.log)
                del self.messages[chunk_id]
                return out
            except Exception as e:
                self.log(f"Error reassembling {chunk_id}: {e}")
        return None

    def reap_timeouts(self):
        """(Optional) Cleanup stale partial messages."""
        now = time.time()
        to_delete = [cid for cid, s in self.messages.items() if now - s['last_seen'] > self.timeout]
        for cid in to_delete:
            self.log(f"Reaping stale chunk set {cid}")
            del self.messages[cid]
