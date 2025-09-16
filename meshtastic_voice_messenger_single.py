# meshtastic_voice_messenger_single.py
# Combined single-file version of Meshtastic Voice Messenger

import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
import meshtastic
import meshtastic.serial_interface
from pubsub import pub
import threading
import serial.tools.list_ports
import pyaudio
import wave
import os
import time
import base64
import json
import zlib
import audioop
import struct
import uuid
import math
import numpy as np
from datetime import datetime

# --- Codec2 Compression ---
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
QUALITY_TO_RATE = {
    "Ultra Low": 4000,
    "Very Low": 8000,
    "Low": 11025,
}

def _to_mono_int16(pcm_bytes, channels, sampwidth):
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

def _resample_to_8k(x_int16, in_rate):
    out_rate = 8000
    if in_rate == out_rate:
        return x_int16
    if resample_poly is not None:
        from math import gcd
        g = gcd(in_rate, out_rate)
        up, down = out_rate // g, in_rate // g
        y = resample_poly(x_int16.astype(np.float32), up, down)
        return np.clip(np.round(y), -32768, 32767).astype(np.int16)
    n_in = len(x_int16)
    dur = n_in / float(in_rate)
    n_out = int(round(dur * out_rate))
    if n_out <= 1 or n_in <= 1:
        return x_int16
    xi = np.linspace(0.0, 1.0, n_in, endpoint=True)
    xo = np.linspace(0.0, 1.0, n_out, endpoint=True)
    y = np.interp(xo, xi, x_int16.astype(np.float32))
    return np.clip(np.round(y), -32768, 32767).astype(np.int16)

def encode_to_codec2(wav_path, ui_quality="Low", log=lambda *a, **k: None):
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

def decode_codec2_to_wav(blob, out_wav, log=lambda *a, **k: None):
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

def decode_auto(voice_bytes, out_wav, log=lambda *a, **k: None):
    try:
        decode_codec2_to_wav(voice_bytes, out_wav, log=log)
        return
    except ValueError:
        pass
    except Exception as e:
        log(f"Codec2 decode attempt failed: {e}")
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

def downsample_audio(frames, channels, sample_width, original_rate, target_rate):
    try:
        out, _ = audioop.ratecv(frames, sample_width, channels, original_rate, target_rate, None)
        return out, target_rate
    except Exception:
        return frames, original_rate

def compress_dynamic_range(frames, sample_width, threshold=0.6, ratio=0.7):
    try:
        if sample_width == 1:
            arr = np.frombuffer(frames, dtype=np.uint8).astype(np.int16) - 128
            max_val = 128.0
        else:
            arr = np.frombuffer(frames, dtype=np.int16)
            max_val = 32768.0
        norm = arr.astype(np.float32) / max_val
        comp = np.empty_like(norm)
        over = np.abs(norm) > threshold
        comp[~over] = norm[~over]
        pos = (norm > threshold)
        comp[pos] = threshold + (norm[pos] - threshold) * ratio
        neg = (norm < -threshold)
        comp[neg] = -threshold + (norm[neg] + threshold) * ratio
        if sample_width == 1:
            result = (comp * 128 + 128).clip(0, 255).astype(np.uint8).tobytes()
        else:
            result = (comp * 32768).clip(-32768, 32767).astype(np.int16).tobytes()
        return result
    except Exception:
        return frames

# --- Main GUI and App ---
class MeshtasticVoiceMessenger:
    def __init__(self, master):
        self.master = master
        master.title("Meshtastic Voice Messenger")
        master.geometry("700x700")
        master.minsize(600, 600)
        
        # Configure the grid to expand properly
        master.columnconfigure(0, weight=1)
        master.rowconfigure(0, weight=1)

        # Set theme colors
        self.bg_color = "#f0f4f8"
        self.accent_color = "#3498db"
        self.master.configure(bg=self.bg_color)
        
        # Create a style
        self.style = ttk.Style()
        self.style.configure("TFrame", background=self.bg_color)
        self.style.configure("TLabel", background=self.bg_color, font=("Arial", 10))
        self.style.configure("TButton", font=("Arial", 10, "bold"))
        self.style.configure("Header.TLabel", font=("Arial", 14, "bold"))
        
        # Audio settings - improved quality
        self.chunk = 1024
        self.format = pyaudio.paInt16
        self.channels = 1
        self.rate = 8000  # Default sample rate - better quality
        self.record_seconds = 3  # Default recording length
        self.p = pyaudio.PyAudio()
        
        # Meshtastic connection
        self.interface = None
        self.is_connected = False
        
        # Voice message storage
        self.voice_messages = []
        self.recording = False
        self.playing = False
        self.current_recording_path = None
        
        # Message chunking
        self.chunk_sizes = {
            "Small": 150,    # More reliable, more chunks
            "Medium": 180,   # Balance between reliability and speed
            "Large": 200     # Faster transfer, less reliable
        }
        self.max_chunk_size = self.chunk_sizes["Medium"]  # Default
        self.message_chunks = {}  # To store incoming chunks
        self.sending_chunks = False
        self.chunk_retry_count = 2  # Number of times to retry sending a chunk
        self.chunk_retry_delay = 1  # Seconds between retries
        
        # Create directory for voice messages
        os.makedirs("voice_messages", exist_ok=True)
        
        self.create_widgets()

    def create_widgets(self):
        main_frame = ttk.Frame(self.master, padding="20", style="TFrame")
        main_frame.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        main_frame.columnconfigure(0, weight=1)
        
        # Header
        header_label = ttk.Label(main_frame, text="Meshtastic Voice Messenger", style="Header.TLabel")
        header_label.grid(row=0, column=0, pady=(0, 20))
        
        # Connection Settings Frame
        settings_frame = ttk.LabelFrame(main_frame, text="Connection Settings", padding="10")
        settings_frame.grid(row=1, column=0, sticky=(tk.W, tk.E), pady=(0, 10))
        settings_frame.columnconfigure(1, weight=1)
        
        # COM Port with refresh button
        port_frame = ttk.Frame(settings_frame)
        port_frame.grid(row=0, column=1, sticky=(tk.W, tk.E), padx=5, pady=5)
        port_frame.columnconfigure(0, weight=1)
        
        ttk.Label(settings_frame, text="COM Port:").grid(row=0, column=0, sticky=tk.W, padx=5, pady=5)
        self.com_ports = self.get_available_ports()
        self.com_port = ttk.Combobox(port_frame, values=self.com_ports, width=40)
        if self.com_ports:
            self.com_port.set(self.com_ports[0])
        self.com_port.grid(row=0, column=0, sticky=(tk.W, tk.E))
        
        refresh_button = ttk.Button(port_frame, text="⟳", width=3, command=self.refresh_ports)
        refresh_button.grid(row=0, column=1, padx=(5, 0))
        
        # Connect Button
        self.connect_button = ttk.Button(settings_frame, text="Connect", command=self.toggle_connection)
        self.connect_button.grid(row=0, column=2, padx=5, pady=5)
        
        # Recording Settings Frame
        recording_frame = ttk.LabelFrame(main_frame, text="Recording Settings", padding="10")
        recording_frame.grid(row=2, column=0, sticky=(tk.W, tk.E), pady=(0, 10))
        recording_frame.columnconfigure(1, weight=1)
        
        # Recording Length
        ttk.Label(recording_frame, text="Recording Length (seconds):").grid(row=0, column=0, sticky=tk.W, padx=5, pady=5)
        self.recording_length_var = tk.StringVar(value="3")
        self.recording_length_entry = ttk.Entry(recording_frame, textvariable=self.recording_length_var, width=10)
        self.recording_length_entry.grid(row=0, column=1, sticky=tk.W, padx=5, pady=5)
        
        # Compression Quality
        ttk.Label(recording_frame, text="Compression Quality:").grid(row=1, column=0, sticky=tk.W, padx=5, pady=5)
        self.compression_quality_var = tk.StringVar(value="Low")  # Changed default to Low
        self.compression_quality = ttk.Combobox(recording_frame, textvariable=self.compression_quality_var, 
                                               values=["Ultra Low", "Very Low", "Low"], width=10)
        self.compression_quality.grid(row=1, column=1, sticky=tk.W, padx=5, pady=5)
        
        # Chunk Size
        ttk.Label(recording_frame, text="Chunk Size:").grid(row=2, column=0, sticky=tk.W, padx=5, pady=5)
        self.chunk_size_var = tk.StringVar(value="Medium")
        self.chunk_size = ttk.Combobox(recording_frame, textvariable=self.chunk_size_var, 
                                      values=["Small", "Medium", "Large"], width=10)
        self.chunk_size.grid(row=2, column=1, sticky=tk.W, padx=5, pady=5)
        self.chunk_size.bind("<<ComboboxSelected>>", self.update_chunk_size)
        
        # Voice Controls Frame
        voice_frame = ttk.LabelFrame(main_frame, text="Voice Controls", padding="10")
        voice_frame.grid(row=3, column=0, sticky=(tk.W, tk.E), pady=(0, 10))
        
        # Record Button
        self.record_button = ttk.Button(voice_frame, text="Record Voice Message", command=self.toggle_recording, width=20)
        self.record_button.grid(row=0, column=0, padx=5, pady=5)
        
        # Send Button
        self.send_button = ttk.Button(voice_frame, text="Send Voice Message", command=self.send_voice_message, width=20, state=tk.DISABLED)
        self.send_button.grid(row=0, column=1, padx=5, pady=5)
        
        # Test Button
        self.test_button = ttk.Button(voice_frame, text="Send Test Message", command=self.send_test_message, width=20)
        self.test_button.grid(row=0, column=2, padx=5, pady=5)
        
        # Messages Frame
        messages_frame = ttk.LabelFrame(main_frame, text="Voice Messages", padding="10")
        messages_frame.grid(row=4, column=0, sticky=(tk.W, tk.E, tk.N, tk.S), pady=(0, 10))
        messages_frame.rowconfigure(0, weight=1)
        messages_frame.columnconfigure(0, weight=1)
        
        # Messages List
        self.messages_list = tk.Listbox(messages_frame, height=10)
        self.messages_list.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S), padx=5, pady=5)
        self.messages_list.bind('<<ListboxSelect>>', self.on_message_select)
        
        # Scrollbar for messages list
        scrollbar = ttk.Scrollbar(messages_frame, orient=tk.VERTICAL, command=self.messages_list.yview)
        scrollbar.grid(row=0, column=1, sticky=(tk.N, tk.S))
        self.messages_list.configure(yscrollcommand=scrollbar.set)
        
        # Playback Controls
        playback_frame = ttk.Frame(messages_frame)
        playback_frame.grid(row=1, column=0, columnspan=2, sticky=(tk.W, tk.E), pady=(5, 0))
        
        self.play_button = ttk.Button(playback_frame, text="Play", command=self.play_voice_message, width=10, state=tk.DISABLED)
        self.play_button.grid(row=0, column=0, padx=5, pady=5)
        
        self.stop_button = ttk.Button(playback_frame, text="Stop", command=self.stop_playback, width=10, state=tk.DISABLED)
        self.stop_button.grid(row=0, column=1, padx=5, pady=5)
        
        # Log Display Frame
        log_frame = ttk.LabelFrame(main_frame, text="Log Output", padding="10")
        log_frame.grid(row=5, column=0, sticky=(tk.W, tk.E, tk.N, tk.S), pady=(0, 10))
        log_frame.rowconfigure(0, weight=1)
        log_frame.columnconfigure(0, weight=1)
        
        # Log Display
        self.log_display = scrolledtext.ScrolledText(log_frame, width=80, height=10, wrap=tk.WORD)
        self.log_display.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S), padx=5, pady=5)
        
        # Status Bar
        self.status_var = tk.StringVar(value="Ready")
        status_bar = ttk.Label(main_frame, textvariable=self.status_var, relief=tk.SUNKEN, anchor=tk.W)
        status_bar.grid(row=6, column=0, sticky=(tk.W, tk.E))
        
        # Configure main frame to expand
        main_frame.rowconfigure(4, weight=1)
        main_frame.rowconfigure(5, weight=1)
        
        # Add tooltips
        self.add_tooltip(self.chunk_size, "Small: More reliable but slower\nMedium: Balanced\nLarge: Faster but less reliable")
        self.add_tooltip(self.compression_quality, "Ultra Low: Smallest size, lowest quality\nVery Low: Better quality, larger size\nLow: Best quality, largest size")

    def add_tooltip(self, widget, text):
        """Add a tooltip to a widget"""
        def enter(event):
            x, y, _, _ = widget.bbox("insert")
            x += widget.winfo_rootx() + 25
            y += widget.winfo_rooty() + 25
            
            # Create a toplevel window
            self.tooltip = tk.Toplevel(widget)
            self.tooltip.wm_overrideredirect(True)
            self.tooltip.wm_geometry(f"+{x}+{y}")
            
            label = tk.Label(self.tooltip, text=text, justify=tk.LEFT,
                           background="#ffffe0", relief=tk.SOLID, borderwidth=1,
                           font=("Arial", "8", "normal"))
            label.pack(ipadx=1)
            
        def leave(event):
            if hasattr(self, 'tooltip'):
                self.tooltip.destroy()
                
        widget.bind("<Enter>", enter)
        widget.bind("<Leave>", leave)

    def update_chunk_size(self, event=None):
        """Update the chunk size based on the dropdown selection"""
        selected = self.chunk_size_var.get()
        if selected in self.chunk_sizes:
            self.max_chunk_size = self.chunk_sizes[selected]
            self.log(f"Chunk size set to {selected} ({self.max_chunk_size} bytes)")

    def refresh_ports(self):
        """Refresh the list of available COM ports"""
        self.com_ports = self.get_available_ports()
        self.com_port['values'] = self.com_ports
        if self.com_ports:
            self.com_port.set(self.com_ports[0])
        self.log("COM ports refreshed")

    def get_available_ports(self):
        """Get a list of available COM ports"""
        return [port.device for port in serial.tools.list_ports.comports()]

    def log(self, message):
        """Add a timestamped message to the log display"""
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        self.log_display.insert(tk.END, f"[{timestamp}] {message}\n")
        self.log_display.see(tk.END)

    def toggle_connection(self):
        """Toggle connection to Meshtastic device"""
        if not self.is_connected:
            self.connect_to_device()
        else:
            self.disconnect_from_device()

    def connect_to_device(self):
        """Connect to Meshtastic device"""
        if not self.com_port.get():
            messagebox.showerror("Error", "COM Port is required")
            return
            
        try:
            self.log(f"Connecting to Meshtastic device on {self.com_port.get()}...")
            self.interface = meshtastic.serial_interface.SerialInterface(devPath=self.com_port.get())
            self.log("Connected to Meshtastic device successfully")
            
            # Subscribe to receive messages
            pub.subscribe(self.on_receive, "meshtastic.receive")
            self.log("Subscribed to Meshtastic messages")
            
            # Get device info
            myinfo = self.interface.myInfo
            if myinfo:
                self.log(f"Connected to node: {myinfo.my_node_num}")
                
            self.is_connected = True
            self.connect_button.config(text="Disconnect")
            self.status_var.set("Connected")
            
        except Exception as e:
            self.log(f"Error connecting to Meshtastic device: {str(e)}")
            messagebox.showerror("Connection Error", f"Failed to connect to Meshtastic device: {str(e)}")

    def disconnect_from_device(self):
        """Disconnect from Meshtastic device"""
        if self.interface:
            try:
                # pub.unsubscribe(self.on_receive, "meshtastic.receive")
                self.interface.close()
                self.log("Meshtastic interface closed")
            except Exception as e:
                self.log(f"Error closing interface: {str(e)}")
            finally:
                self.interface = None
                
        self.is_connected = False
        self.connect_button.config(text="Connect")
        self.status_var.set("Disconnected")

    def on_receive(self, packet, interface):
        """Handle received messages from the mesh network"""
        try:
            from_id = packet.get('fromId', 'unknown')
            self.log(f"Received packet: {packet.get('id')} from {from_id}")
            
            if packet.get('decoded', {}).get('portnum') == 'TEXT_MESSAGE_APP':
                self.log(f"Received text message from {from_id}")
                self.process_text_message(packet)
            elif packet.get('decoded', {}).get('portnum') == 'PRIVATE_APP':
                self.log(f"Received private app data from {from_id}")
                self.process_voice_message(packet)
        except Exception as e:
            self.log(f"Error processing received packet: {str(e)}")

    def process_text_message(self, packet):
        """Process a received text message"""
        try:
            message = packet.get('decoded', {}).get('text', '')
            from_node = packet.get('fromId', 'Unknown')
            self.log(f"Text message from {from_node}: {message}")
            
            # Add to messages list
            self.add_message_to_list(f"Text from {from_node}: {message}", None)
            
        except Exception as e:
            self.log(f"Error processing text message: {str(e)}")

    def process_voice_message(self, packet):
        """Process a received voice message"""
        try:
            data = packet.get('decoded', {}).get('payload')
            from_node = packet.get('fromId', 'Unknown')
            
            if not data:
                self.log("Received empty voice message payload")
                return
                
            try:
                # Try to decode as JSON
                json_data = json.loads(data.decode('utf-8'))
                
                # Check if this is a chunked message
                if 'chunk_id' in json_data and 'chunk_num' in json_data and 'total_chunks' in json_data:
                    self.log(f"Received chunk {json_data['chunk_num']}/{json_data['total_chunks']} of message {json_data['chunk_id']} from {from_node}")
                    self.process_message_chunk(json_data, from_node)
                elif 'voice_data' in json_data and 'timestamp' in json_data:
                    # This is a complete voice message
                    voice_data = base64.b64decode(json_data['voice_data'])
                    timestamp = json_data['timestamp']
                    
                    # Save the voice message
                    filename = f"voice_messages/received_{from_node}_{timestamp}.wav"
                    self.decode_and_save_voice(voice_data, filename)
                    
                    self.log(f"Received voice message from {from_node}")
                    self.add_message_to_list(f"Voice from {from_node} at {timestamp}", filename)
                elif 'test' in json_data:
                    # This is a test message
                    self.log(f"Received test message from {from_node}: {json_data['test']}")
                    messagebox.showinfo("Test Message", f"Received test message from {from_node}: {json_data['test']}")
            except json.JSONDecodeError:
                self.log("Received data is not in JSON format")
                
        except Exception as e:
            self.log(f"Error processing voice message: {str(e)}")

    def decode_and_save_voice(self, voice_data, filename):
        """Decode voice data and save as WAV file"""
        try:
            # Try to decode using the auto-decode function
            decode_auto(voice_data, filename, log=self.log)
        except Exception as e:
            self.log(f"Error decoding voice data: {str(e)}")

    def process_message_chunk(self, chunk_data, from_node):
        """Process a chunk of a multi-part voice message"""
        chunk_id = chunk_data['chunk_id']
        chunk_num = chunk_data['chunk_num']
        total_chunks = chunk_data['total_chunks']
        chunk_content = chunk_data['data']
        
        # Initialize storage for this message if needed
        if chunk_id not in self.message_chunks:
            self.message_chunks[chunk_id] = {
                'chunks': {},
                'total_chunks': total_chunks,
                'from_node': from_node,
                'timestamp': datetime.now().strftime('%Y%m%d_%H%M%S'),
                'last_chunk_time': time.time()
            }
        else:
            # Update last chunk time
            self.message_chunks[chunk_id]['last_chunk_time'] = time.time()
        
        # Store this chunk
        self.message_chunks[chunk_id]['chunks'][chunk_num] = chunk_content
        
        # Check if we have all chunks
        received_chunks = len(self.message_chunks[chunk_id]['chunks'])
        if received_chunks == total_chunks:
            self.log(f"Received all {total_chunks} chunks for message {chunk_id}")
            self.reassemble_message(chunk_id)
        else:
            # Log how many chunks we have so far
            self.log(f"Have {received_chunks}/{total_chunks} chunks for message {chunk_id}")

    def reassemble_message(self, chunk_id):
        """Reassemble a complete message from chunks"""
        message_data = self.message_chunks[chunk_id]
        from_node = message_data['from_node']
        timestamp = message_data['timestamp']
        
        # Combine chunks in order
        combined_data = ""
        missing_chunks = []
        
        for i in range(1, message_data['total_chunks'] + 1):
            if i in message_data['chunks']:
                combined_data += message_data['chunks'][i]
            else:
                missing_chunks.append(i)
        
        if missing_chunks:
            self.log(f"Missing chunks {missing_chunks} for message {chunk_id}, cannot reassemble")
            return
        
        try:
            # Decode the base64 data
            voice_data = base64.b64decode(combined_data)
            
            # Save as WAV file using decode_auto
            filename = f"voice_messages/received_{from_node}_{timestamp}.wav"
            self.decode_and_save_voice(voice_data, filename)
            
            self.log(f"Reassembled and saved voice message from {from_node}")
            self.add_message_to_list(f"Voice from {from_node} at {timestamp}", filename)
            
            # Clean up
            del self.message_chunks[chunk_id]
            
        except Exception as e:
            self.log(f"Error reassembling message: {str(e)}")

    def toggle_recording(self):
        """Toggle recording state"""
        if not self.recording:
            self.start_recording()
        else:
            self.stop_recording()

    def start_recording(self):
        """Start recording a voice message"""
        try:
            # Update recording length from input
            try:
                self.record_seconds = int(self.recording_length_var.get())
                if self.record_seconds < 1 or self.record_seconds > 30:
                    messagebox.showwarning("Warning", "Recording length should be between 1 and 30 seconds")
                    self.record_seconds = max(1, min(30, self.record_seconds))
                    self.recording_length_var.set(str(self.record_seconds))
            except ValueError:
                messagebox.showerror("Error", "Recording length must be a number")
                self.recording_length_var.set("3")
                self.record_seconds = 3
            
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            self.current_recording_path = f"voice_messages/recording_{timestamp}.wav"
            
            self.recording = True
            self.record_button.config(text="Stop Recording")
            self.status_var.set("Recording...")
            
            # Start recording in a separate thread
            threading.Thread(target=self.record_audio, daemon=True).start()
            
        except Exception as e:
            self.log(f"Error starting recording: {str(e)}")
            self.recording = False
            self.record_button.config(text="Record Voice Message")
            self.status_var.set("Ready")

    def record_audio(self):
        """Record audio to a WAV file"""
        try:
            # Set sample rate based on quality setting
            quality = self.compression_quality_var.get()
            if quality == "Ultra Low":
                self.rate = 4000  # Increased from 2000 for better quality
            elif quality == "Very Low":
                self.rate = 8000  # Increased from 4000 for better quality
            else:
                self.rate = 11025  # Increased from 8000 for better quality
                
            stream = self.p.open(format=self.format,
                                channels=self.channels,
                                rate=self.rate,
                                input=True,
                                frames_per_buffer=self.chunk)
            
            self.log(f"Recording for {self.record_seconds} seconds at {self.rate}Hz...")
            
            frames = []
            
            for i in range(0, int(self.rate / self.chunk * self.record_seconds)):
                if not self.recording:
                    break
                data = stream.read(self.chunk)
                frames.append(data)
            
            stream.stop_stream()
            stream.close()
            
            if self.recording:  # Only save if we didn't manually stop
                self.save_recording(frames)
                
            self.master.after(0, self.recording_finished)
            
        except Exception as e:
            self.log(f"Error during recording: {str(e)}")
            self.master.after(0, self.recording_finished)

    def save_recording(self, frames):
        """Save the recorded audio frames to a WAV file"""
        try:
            wf = wave.open(self.current_recording_path, 'wb')
            wf.setnchannels(self.channels)
            wf.setsampwidth(self.p.get_sample_size(self.format))
            wf.setframerate(self.rate)
            wf.writeframes(b''.join(frames))
            wf.close()
            
            self.log(f"Recording saved to {self.current_recording_path}")
            self.add_message_to_list(f"Recording at {datetime.now().strftime('%H:%M:%S')}", self.current_recording_path)
            
        except Exception as e:
            self.log(f"Error saving recording: {str(e)}")

    def recording_finished(self):
        """Update UI after recording is finished"""
        self.recording = False
        self.record_button.config(text="Record Voice Message")
        self.status_var.set("Ready")
        self.send_button.config(state=tk.NORMAL)

    def stop_recording(self):
        """Stop the current recording"""
        self.recording = False
        self.log("Recording stopped manually")

    def add_message_to_list(self, description, filepath):
        """Add a voice message to the list"""
        self.voice_messages.append({"description": description, "filepath": filepath})
        self.messages_list.insert(tk.END, description)

    def on_message_select(self, event):
        """Handle message selection from the list"""
        selection = self.messages_list.curselection()
        if selection:
            index = selection[0]
            if index < 0 or index >= len(self.voice_messages):
                return
                
            filepath = self.voice_messages[index]["filepath"]
            if filepath:
                self.play_button.config(state=tk.NORMAL)
                self.stop_button.config(state=tk.NORMAL)
            else:
                self.play_button.config(state=tk.DISABLED)
                self.stop_button.config(state=tk.DISABLED)

    def play_voice_message(self):
        """Play the selected voice message"""
        selection = self.messages_list.curselection()
        if not selection:
            return
            
        index = selection[0]
        if index < 0 or index >= len(self.voice_messages):
            return
            
        filepath = self.voice_messages[index]["filepath"]
        if not filepath:
            return
            
        if not os.path.exists(filepath):
            messagebox.showerror("Error", f"File not found: {filepath}")
            return
            
        self.playing = True
        self.status_var.set("Playing...")
        
        # Start playback in a separate thread
        threading.Thread(target=self.play_audio, args=(filepath,), daemon=True).start()

    def play_audio(self, filepath):
        """Play audio from a WAV file"""
        try:
            wf = wave.open(filepath, 'rb')
            
            stream = self.p.open(format=self.p.get_format_from_width(wf.getsampwidth()),
                                channels=wf.getnchannels(),
                                rate=wf.getframerate(),
                                output=True)
            
            data = wf.readframes(self.chunk)
            
            while data and self.playing:
                stream.write(data)
                data = wf.readframes(self.chunk)
            
            stream.stop_stream()
            stream.close()
            
            self.master.after(0, self.playback_finished)
            
        except Exception as e:
            self.log(f"Error during playback: {str(e)}")
            self.master.after(0, self.playback_finished)

    def playback_finished(self):
        """Update UI after playback is finished"""
        self.playing = False
        self.status_var.set("Ready")

    def stop_playback(self):
        """Stop the current playback"""
        self.playing = False
        self.log("Playback stopped")

    def ultra_compress_wav(self, wav_path, quality="Low"):
        """Ultra compress audio file for transmission using included functions"""
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
        self.log(f"Compressed audio: {len(frames)} -> {len(compressed)} bytes")
        return compressed

    def send_test_message(self):
        """Send a simple test message to verify connectivity"""
        if not self.is_connected:
            messagebox.showerror("Error", "Not connected to Meshtastic device")
            return
            
        try:
            # Create a simple test message
            test_payload = {
                "test": f"Test message from {self.interface.getLongName() or 'unknown'} at {datetime.now().strftime('%H:%M:%S')}"
            }
            
            # Convert to JSON string then to bytes
            json_payload = json.dumps(test_payload).encode('utf-8')
            
            # Send the message
            self.log(f"Sending test message: {test_payload['test']}")
            self.log(f"Payload size: {len(json_payload)} bytes")
            
            self.interface.sendData(
                json_payload,
                destinationId=meshtastic.BROADCAST_ADDR,
                portNum=256,  # PRIVATE_APP port
                wantAck=True
            )
            self.log("Test message sent successfully")
            
        except Exception as e:
            self.log(f"Error sending test message: {str(e)}")
            messagebox.showerror("Error", f"Failed to send test message: {str(e)}")

    def send_voice_message(self):
        """Send the last recorded voice message over Meshtastic"""
        if not self.is_connected:
            messagebox.showerror("Error", "Not connected to Meshtastic device")
            return
            
        if not self.current_recording_path or not os.path.exists(self.current_recording_path):
            messagebox.showerror("Error", "No recording available to send")
            return
            
        if self.sending_chunks:
            messagebox.showinfo("Info", "Already sending a voice message")
            return
            
        try:
            # Update chunk size from dropdown
            self.update_chunk_size()
            
            # Get quality setting
            quality = self.compression_quality_var.get()
            
            # Try Codec2 first if available
            compressed_data = None
            if Codec2 is not None:
                try:
                    self.log(f"Attempting Codec2 compression with quality: {quality}")
                    compressed_data = encode_to_codec2(self.current_recording_path, quality, log=self.log)
                except Exception as e:
                    self.log(f"Codec2 compression failed: {e}, falling back to legacy compression")
            
            # Fall back to legacy compression if Codec2 failed or not available
            if compressed_data is None:
                self.log(f"Using legacy compression with quality: {quality}")
                compressed_data = self.ultra_compress_wav(self.current_recording_path, quality)
                
            if not compressed_data:
                messagebox.showerror("Error", "Failed to compress audio")
                return
                
            # Encode as base64
            encoded_data = base64.b64encode(compressed_data).decode('utf-8')
            
            # Check if we need to chunk the message
            if len(encoded_data) <= self.max_chunk_size:
                # Small enough to send in one message
                timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                payload = {
                    "voice_data": encoded_data,
                    "timestamp": timestamp
                }
                
                # Convert to JSON string then to bytes
                json_payload = json.dumps(payload).encode('utf-8')
                
                # Log the size
                self.log(f"Voice message size: {len(json_payload)} bytes")
                
                # Send the message
                self.log("Sending voice message...")
                self.interface.sendData(
                    json_payload,
                    destinationId=meshtastic.BROADCAST_ADDR,
                    portNum=256,  # PRIVATE_APP port
                    wantAck=True
                )
                self.log("Voice message sent successfully")
                
            else:
                # Need to chunk the message
                self.log(f"Message too large ({len(encoded_data)} bytes), splitting into chunks")
                self.send_chunked_message(encoded_data)
                
            self.send_button.config(state=tk.DISABLED)
            
        except Exception as e:
            self.log(f"Error sending voice message: {str(e)}")
            messagebox.showerror("Error", f"Failed to send voice message: {str(e)}")
            self.sending_chunks = False

    def send_chunked_message(self, encoded_data):
        """Split a large message into chunks and send them sequentially"""
        # Generate a unique ID for this chunked message
        chunk_id = str(uuid.uuid4())[:8]
        
        # Calculate how many chunks we need
        total_chunks = math.ceil(len(encoded_data) / self.max_chunk_size)
        
        self.log(f"Splitting message into {total_chunks} chunks (size: {self.max_chunk_size} bytes)")
        
        # Set flag to prevent multiple sends at once
        self.sending_chunks = True
        
        # Start sending chunks in a separate thread
        threading.Thread(target=self.send_chunks_thread, 
                        args=(chunk_id, encoded_data, total_chunks),
                        daemon=True).start()
        
    def send_chunks_thread(self, chunk_id, encoded_data, total_chunks):
        """Send chunks of a message sequentially with retries"""
        try:
            # Send each chunk with retries
            for i in range(total_chunks):
                # Calculate chunk boundaries
                start = i * self.max_chunk_size
                end = min(start + self.max_chunk_size, len(encoded_data))
                chunk_data = encoded_data[start:end]
                
                # Create chunk payload
                payload = {
                    "chunk_id": chunk_id,
                    "chunk_num": i + 1,
                    "total_chunks": total_chunks,
                    "data": chunk_data
                }
                
                # Convert to JSON string then to bytes
                json_payload = json.dumps(payload).encode('utf-8')
                
                # Try to send the chunk with retries
                success = False
                for retry in range(self.chunk_retry_count):
                    try:
                        # Send the chunk
                        self.log(f"Sending chunk {i+1}/{total_chunks}...")
                        self.interface.sendData(
                            json_payload,
                            destinationId=meshtastic.BROADCAST_ADDR,
                            portNum=256,  # PRIVATE_APP port
                            wantAck=True
                        )
                        
                        success = True
                        break
                    except Exception as e:
                        self.log(f"Error sending chunk {i+1}, retry {retry+1}: {str(e)}")
                        time.sleep(self.chunk_retry_delay)
                
                if not success:
                    self.log(f"Failed to send chunk {i+1} after {self.chunk_retry_count} retries")
                
                # Wait between chunks to avoid flooding the network
                time.sleep(1)
                
            self.log(f"All {total_chunks} chunks sent")
            
        except Exception as e:
            self.log(f"Error sending chunks: {str(e)}")
        finally:
            self.sending_chunks = False
            self.master.after(0, lambda: self.send_button.config(state=tk.NORMAL))

def main():
    root = tk.Tk()
    app = MeshtasticVoiceMessenger(root)
    root.mainloop()

if __name__ == "__main__":
    main()
