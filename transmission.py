# transmission.py
import time
import json
import uuid
import math
import meshtastic
import meshtastic.serial_interface
from pubsub import pub

PRIVATE_PORT = 256

class MeshtasticTransport:
    """Thin wrapper around meshtastic send/receive + chunked sending."""

    def __init__(self, log=lambda *a, **k: None):
        self.interface = None
        self.is_connected = False
        self.log = log
        self._rx_callback = None
        # TX pacing & reliability knobs
        self.chunk_retry_count = 2
        self.chunk_retry_delay = 1.0
        self.inter_chunk_delay = 1.0
        self.default_chunk_size = 180

    # ---------- connection ----------
    def connect(self, dev_path, on_receive):
        self.log(f"Connecting to Meshtastic on {dev_path}...")
        self.interface = meshtastic.serial_interface.SerialInterface(devPath=dev_path)
        pub.subscribe(self._on_receive, "meshtastic.receive")
        self._rx_callback = on_receive
        self.is_connected = True

        info = self.interface.myInfo
        if info:
            self.log(f"Connected to node: {info.my_node_num}")

    def disconnect(self):
        if not self.interface:
            return
        try:
            self.interface.close()
            self.log("Meshtastic interface closed")
        finally:
            self.interface = None
            self.is_connected = False
            try:
                pub.unsubscribe(self._on_receive, "meshtastic.receive")
            except Exception:
                pass
            self._rx_callback = None

    def _on_receive(self, packet, interface):
        if self._rx_callback:
            self._rx_callback(packet, interface)

    # ---------- send helpers ----------
    def send_test_message(self):
        payload = {
            "test": f"Test message from {self.interface.getLongName() or 'unknown'} at {time.strftime('%H:%M:%S')}"
        }
        self._send_json(payload)
        self.log("Test message sent")

    def send_base64_voice(self, encoded_data, chunk_size=None):
        """Decide single vs chunked and send over PRIVATE_APP."""
        chunk_size = chunk_size or self.default_chunk_size
        if len(encoded_data) <= chunk_size:
            timestamp = time.strftime('%Y%m%d_%H%M%S')
            self._send_json({"voice_data": encoded_data, "timestamp": timestamp})
            self.log("Sent single-packet voice message")
        else:
            self._send_chunked(encoded_data, chunk_size)

    def _send_chunked(self, encoded_data, chunk_size):
        chunk_id = str(uuid.uuid4())[:8]
        total = math.ceil(len(encoded_data) / chunk_size)
        self.log(f"Sending {total} chunks (size {chunk_size} bytes)")

        for i in range(total):
            start = i * chunk_size
            end = min(start + chunk_size, len(encoded_data))
            chunk = encoded_data[start:end]
            payload = {"chunk_id": chunk_id, "chunk_num": i + 1, "total_chunks": total, "data": chunk}

            ok = False
            for r in range(self.chunk_retry_count):
                try:
                    self._send_json(payload)
                    ok = True
                    break
                except Exception as e:
                    self.log(f"Chunk {i+1} send error (retry {r+1}): {e}")
                    time.sleep(self.chunk_retry_delay)
            if not ok:
                self.log(f"Failed to send chunk {i+1} after retries")
            time.sleep(self.inter_chunk_delay)

        self.log("Finished sending chunks")

    def _send_json(self, obj):
        data = json.dumps(obj).encode('utf-8')
        self.interface.sendData(
            data,
            destinationId=meshtastic.BROADCAST_ADDR,
            portNum=PRIVATE_PORT,
            wantAck=True
        )
