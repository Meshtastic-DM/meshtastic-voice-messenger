# transmission.py
import math
import json
import time
import uuid
import base64
import threading
from datetime import datetime

import meshtastic
import meshtastic.serial_interface
from pubsub import pub

CHUNK_SIZES = {"Small": 150, "Medium": 180, "Large": 200}

class MeshtasticTransport:
    """
    Handles connection, sending (single/chunked), receiving, and chunk reassembly.
    Emits events to GUI via callbacks passed in constructor.
    """
    def __init__(self, log, on_text, on_voice_file, on_status):
        self.log = log
        self.on_text = on_text
        self.on_voice_file = on_voice_file
        self.on_status = on_status

        self.interface = None
        self.is_connected = False

        self.max_chunk_size = CHUNK_SIZES["Medium"]
        self.chunk_retry_count = 2
        self.chunk_retry_delay = 1
        self.sending_chunks = False
        self.message_chunks = {}

    def set_chunk_size(self, label: str):
        if label in CHUNK_SIZES:
            self.max_chunk_size = CHUNK_SIZES[label]
            self.log(f"Chunk size set to {label} ({self.max_chunk_size} bytes)")

    # ---------- Connection ----------
    def connect(self, dev_path: str):
        self.log(f"Connecting to Meshtastic on {dev_path}...")
        self.interface = meshtastic.serial_interface.SerialInterface(devPath=dev_path)
        pub.subscribe(self._on_receive, "meshtastic.receive")
        self.is_connected = True
        myinfo = self.interface.myInfo
        if myinfo:
            self.log(f"Connected to node: {myinfo.my_node_num}")
        self.on_status("Connected")

    def disconnect(self):
        if self.interface:
            try:
                # pub.unsubscribe(self._on_receive, "meshtastic.receive")
                self.interface.close()
                self.log("Meshtastic interface closed")
            except Exception as e:
                self.log(f"Error closing interface: {e}")
            finally:
                self.interface = None
        self.is_connected = False
        self.on_status("Disconnected")

    # ---------- Sending ----------
    def send_test(self, text: str):
        payload = {"test": text}
        js = json.dumps(payload).encode('utf-8')
        self.interface.sendData(js, destinationId=meshtastic.BROADCAST_ADDR, portNum=256, wantAck=True)
        self.log("Test message sent.")

    def send_voice_payload(self, voice_bytes: bytes):
        """
        Send already-encoded bytes (Codec2 or legacy). Handles single vs chunked.
        """
        encoded = base64.b64encode(voice_bytes).decode('utf-8')
        if len(encoded) <= self.max_chunk_size:
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            js = json.dumps({"voice_data": encoded, "timestamp": timestamp}).encode('utf-8')
            self.log(f"Voice message size: {len(js)} bytes (single packet)")
            self.interface.sendData(js, destinationId=meshtastic.BROADCAST_ADDR, portNum=256, wantAck=True)
            self.log("Voice message sent successfully")
        else:
            self.log(f"Message too large ({len(encoded)} B), splitting into chunks")
            self._send_chunked(encoded)

    def _send_chunked(self, encoded: str):
        chunk_id = str(uuid.uuid4())[:8]
        total = math.ceil(len(encoded) / self.max_chunk_size)
        self.log(f"Splitting into {total} chunks ({self.max_chunk_size} bytes)")
        self.sending_chunks = True
        threading.Thread(target=self._send_chunks_thread, args=(chunk_id, encoded, total), daemon=True).start()

    def _send_chunks_thread(self, chunk_id, encoded, total):
        try:
            for i in range(total):
                start = i * self.max_chunk_size
                end = min(start + self.max_chunk_size, len(encoded))
                part = encoded[start:end]
                js = json.dumps({
                    "chunk_id": chunk_id,
                    "chunk_num": i + 1,
                    "total_chunks": total,
                    "data": part
                }).encode('utf-8')

                ok = False
                for r in range(self.chunk_retry_count):
                    try:
                        self.interface.sendData(js, destinationId=meshtastic.BROADCAST_ADDR, portNum=256, wantAck=True)
                        ok = True
                        break
                    except Exception as e:
                        self.log(f"Chunk {i+1} retry {r+1}: {e}")
                        time.sleep(self.chunk_retry_delay)
                if not ok:
                    self.log(f"Failed to send chunk {i+1}")

                time.sleep(1)
            self.log(f"All {total} chunks sent")
        except Exception as e:
            self.log(f"Error sending chunks: {e}")
        finally:
            self.sending_chunks = False

    # ---------- Receiving ----------
    def _on_receive(self, packet, interface):
        try:
            from_id = packet.get('fromId', 'unknown')
            if packet.get('decoded', {}).get('portnum') == 'TEXT_MESSAGE_APP':
                msg = packet.get('decoded', {}).get('text', '')
                self.log(f"Text from {from_id}: {msg}")
                self.on_text(from_id, msg)
            elif packet.get('decoded', {}).get('portnum') == 'PRIVATE_APP':
                self._process_private(packet)
        except Exception as e:
            self.log(f"Error processing received packet: {e}")

    def _process_private(self, packet):
        data = packet.get('decoded', {}).get('payload')
        from_node = packet.get('fromId', 'Unknown')
        if not data:
            self.log("Empty PRIVATE_APP payload")
            return

        try:
            js = json.loads(data.decode('utf-8'))
            if 'chunk_id' in js:
                self._on_chunk(js, from_node)
            elif 'voice_data' in js and 'timestamp' in js:
                encoded = js['voice_data']
                self.on_voice_file(from_node, encoded, js['timestamp'])
            elif 'test' in js:
                self.log(f"Received test from {from_node}: {js['test']}")
        except json.JSONDecodeError:
            self.log("PRIVATE_APP payload not JSON")

    def _on_chunk(self, chunk, from_node):
        cid = chunk['chunk_id']
        num = chunk['chunk_num']
        total = chunk['total_chunks']
        data = chunk['data']

        if cid not in self.message_chunks:
            self.message_chunks[cid] = {
                "chunks": {},
                "total": total,
                "from": from_node,
                "timestamp": datetime.now().strftime('%Y%m%d_%H%M%S'),
                "last": time.time(),
            }
        else:
            self.message_chunks[cid]["last"] = time.time()

        self.message_chunks[cid]["chunks"][num] = data
        have = len(self.message_chunks[cid]["chunks"])
        self.log(f"Have {have}/{total} chunks for {cid}")

        if have == total:
            self._reassemble(cid)

    def _reassemble(self, cid):
        entry = self.message_chunks[cid]
        from_node = entry["from"]
        ts = entry["timestamp"]

        seq = []
        missing = []
        for i in range(1, entry["total"] + 1):
            if i in entry["chunks"]:
                seq.append(entry["chunks"][i])
            else:
                missing.append(i)

        if missing:
            self.log(f"Missing {missing} for {cid}")
            return

        combined = "".join(seq)
        self.on_voice_file(from_node, combined, ts)
        del self.message_chunks[cid]
