# gui.py
import os
import wave
import base64
import threading
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
import serial.tools.list_ports
import pyaudio
from datetime import datetime

from transmission import MeshtasticTransport
from encoding.codec2_codec import encode_to_codec2
from encoding.decoder import decode_auto
from encoding.voice_compression import ultra_compress_wav  # legacy (unchanged)

class AppGUI:
    def __init__(self, master):
        self.master = master
        master.title("Meshtastic Voice Messenger")
        master.geometry("700x700")
        master.minsize(600, 600)

        self.bg_color = "#f0f4f8"
        self.master.configure(bg=self.bg_color)
        self.style = ttk.Style()
        self.style.configure("TFrame", background=self.bg_color)
        self.style.configure("TLabel", background=self.bg_color, font=("Arial", 10))
        self.style.configure("TButton", font=("Arial", 10, "bold"))
        self.style.configure("Header.TLabel", font=("Arial", 14, "bold"))

        # audio
        self.chunk = 1024
        self.format = pyaudio.paInt16
        self.channels = 1
        self.rate = 8000
        self.record_seconds = 3
        self.p = pyaudio.PyAudio()
        self.current_recording_path = None
        os.makedirs("voice_messages", exist_ok=True)

        # transport
        self.transport = MeshtasticTransport(
            log=self.log,
            on_text=self._on_text_msg,
            on_voice_file=self._on_voice_data,
            on_status=self._on_status
        )

        self.voice_messages = []
        self.recording = False
        self.playing = False

        self._build_ui()

    # ---------- UI ----------
    def _build_ui(self):
        main_frame = ttk.Frame(self.master, padding="20", style="TFrame")
        main_frame.grid(row=0, column=0, sticky="nsew")
        main_frame.columnconfigure(0, weight=1)

        header = ttk.Label(main_frame, text="Meshtastic Voice Messenger", style="Header.TLabel")
        header.grid(row=0, column=0, pady=(0,20))

        # Connection
        settings = ttk.LabelFrame(main_frame, text="Connection Settings", padding="10")
        settings.grid(row=1, column=0, sticky="ew", pady=(0,10))
        settings.columnconfigure(1, weight=1)

        ttk.Label(settings, text="COM Port:").grid(row=0, column=0, sticky="w", padx=5, pady=5)
        self.com_box = ttk.Combobox(settings, values=self._ports(), width=40)
        if self.com_box['values']:
            self.com_box.set(self.com_box['values'][0])
        self.com_box.grid(row=0, column=1, sticky="ew", padx=5, pady=5)
        ttk.Button(settings, text="⟳", width=3, command=self._refresh_ports).grid(row=0, column=2, padx=(5,0))
        self.connect_btn = ttk.Button(settings, text="Connect", command=self._toggle_conn)
        self.connect_btn.grid(row=0, column=3, padx=5, pady=5)

        # Recording
        record_frame = ttk.LabelFrame(main_frame, text="Recording Settings", padding="10")
        record_frame.grid(row=2, column=0, sticky="ew", pady=(0,10))
        record_frame.columnconfigure(1, weight=1)

        ttk.Label(record_frame, text="Recording Length (seconds):").grid(row=0, column=0, sticky="w", padx=5, pady=5)
        self.rec_len_var = tk.StringVar(value="3")
        ttk.Entry(record_frame, textvariable=self.rec_len_var, width=10).grid(row=0, column=1, sticky="w", padx=5, pady=5)

        ttk.Label(record_frame, text="Compression Quality:").grid(row=1, column=0, sticky="w", padx=5, pady=5)
        self.quality_var = tk.StringVar(value="Low")
        self.quality_box = ttk.Combobox(record_frame, textvariable=self.quality_var, values=["Ultra Low","Very Low","Low"], width=10)
        self.quality_box.grid(row=1, column=1, sticky="w", padx=5, pady=5)

        ttk.Label(record_frame, text="Chunk Size:").grid(row=2, column=0, sticky="w", padx=5, pady=5)
        self.chunk_var = tk.StringVar(value="Medium")
        self.chunk_box = ttk.Combobox(record_frame, textvariable=self.chunk_var, values=["Small","Medium","Large"], width=10)
        self.chunk_box.grid(row=2, column=1, sticky="w", padx=5, pady=5)
        self.chunk_box.bind("<<ComboboxSelected>>", lambda e: self.transport.set_chunk_size(self.chunk_var.get()))

        # Controls
        voice_frame = ttk.LabelFrame(main_frame, text="Voice Controls", padding="10")
        voice_frame.grid(row=3, column=0, sticky="ew", pady=(0,10))
        ttk.Button(voice_frame, text="Record Voice Message", command=self._toggle_record, width=20).grid(row=0, column=0, padx=5, pady=5)
        self.send_btn = ttk.Button(voice_frame, text="Send Voice Message", command=self._send_voice, width=20, state=tk.DISABLED)
        self.send_btn.grid(row=0, column=1, padx=5, pady=5)
        ttk.Button(voice_frame, text="Send Test Message", command=self._send_test, width=20).grid(row=0, column=2, padx=5, pady=5)

        # Messages
        messages = ttk.LabelFrame(main_frame, text="Voice Messages", padding="10")
        messages.grid(row=4, column=0, sticky="nsew", pady=(0,10))
        messages.rowconfigure(0, weight=1)
        messages.columnconfigure(0, weight=1)

        self.listbox = tk.Listbox(messages, height=10)
        self.listbox.grid(row=0, column=0, sticky="nsew", padx=5, pady=5)
        self.listbox.bind('<<ListboxSelect>>', self._on_select)
        scroll = ttk.Scrollbar(messages, orient=tk.VERTICAL, command=self.listbox.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.listbox.configure(yscrollcommand=scroll.set)

        play_frame = ttk.Frame(messages)
        play_frame.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(5,0))
        self.play_btn = ttk.Button(play_frame, text="Play", command=self._play, width=10, state=tk.DISABLED)
        self.play_btn.grid(row=0, column=0, padx=5, pady=5)
        self.stop_btn = ttk.Button(play_frame, text="Stop", command=self._stop_play, width=10, state=tk.DISABLED)
        self.stop_btn.grid(row=0, column=1, padx=5, pady=5)

        # Logs
        log_frame = ttk.LabelFrame(main_frame, text="Log Output", padding="10")
        log_frame.grid(row=5, column=0, sticky="nsew", pady=(0,10))
        log_frame.rowconfigure(0, weight=1)
        log_frame.columnconfigure(0, weight=1)
        self.log_display = scrolledtext.ScrolledText(log_frame, width=80, height=10, wrap=tk.WORD)
        self.log_display.grid(row=0, column=0, sticky="nsew", padx=5, pady=5)

        # Status bar
        self.status_var = tk.StringVar(value="Ready")
        ttk.Label(main_frame, textvariable=self.status_var, relief=tk.SUNKEN, anchor=tk.W).grid(row=6, column=0, sticky="ew")

        main_frame.rowconfigure(4, weight=1)
        main_frame.rowconfigure(5, weight=1)

    # ---------- Helpers ----------
    def _ports(self):
        return [p.device for p in serial.tools.list_ports.comports()]

    def _refresh_ports(self):
        ports = self._ports()
        self.com_box['values'] = ports
        if ports:
            self.com_box.set(ports[0])
        self.log("COM ports refreshed")

    def _toggle_conn(self):
        if not self.transport.is_connected:
            dev = self.com_box.get()
            if not dev:
                messagebox.showerror("Error", "COM Port is required")
                return
            try:
                self.transport.connect(dev)
                self.connect_btn.config(text="Disconnect")
            except Exception as e:
                self.log(f"Connect error: {e}")
                messagebox.showerror("Connection Error", str(e))
        else:
            self.transport.disconnect()
            self.connect_btn.config(text="Connect")

    def _on_status(self, text):
        self.status_var.set(text)

    def log(self, msg):
        ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        self.log_display.insert(tk.END, f"[{ts}] {msg}\n")
        self.log_display.see(tk.END)

    # ---------- Recording & Playback ----------
    def _toggle_record(self):
        if not self.recording:
            try:
                self.record_seconds = int(self.rec_len_var.get())
                self.record_seconds = max(1, min(30, self.record_seconds))
            except ValueError:
                messagebox.showerror("Error", "Recording length must be a number")
                return
            ts = datetime.now().strftime('%Y%m%d_%H%M%S')
            self.current_recording_path = f"voice_messages/recording_{ts}.wav"
            self.recording = True
            self.status_var.set("Recording...")
            threading.Thread(target=self._record_thread, daemon=True).start()
        else:
            self.recording = False
            self.log("Recording stopped")

    def _record_thread(self):
        try:
            stream = self.p.open(format=self.format, channels=self.channels, rate=self.rate, input=True, frames_per_buffer=self.chunk)
            frames = []
            for _ in range(int(self.rate / self.chunk * self.record_seconds)):
                if not self.recording:
                    break
                frames.append(stream.read(self.chunk))
            stream.stop_stream(); stream.close()
            if self.recording:
                with wave.open(self.current_recording_path, 'wb') as wf:
                    wf.setnchannels(self.channels)
                    wf.setsampwidth(self.p.get_sample_size(self.format))
                    wf.setframerate(self.rate)
                    wf.writeframes(b''.join(frames))
                self.log(f"Saved {self.current_recording_path}")
                self._add_message(f"Recording at {datetime.now().strftime('%H:%M:%S')}", self.current_recording_path)
            self.recording = False
            self.status_var.set("Ready")
            self.send_btn.config(state=tk.NORMAL)
        except Exception as e:
            self.log(f"Record error: {e}")
            self.recording = False
            self.status_var.set("Ready")

    def _add_message(self, desc, path):
        self.voice_messages.append({"description": desc, "filepath": path})
        self.listbox.insert(tk.END, desc)

    def _on_select(self, _):
        sel = self.listbox.curselection()
        if not sel:
            return
        path = self.voice_messages[sel[0]]["filepath"]
        if path:
            self.play_btn.config(state=tk.NORMAL)
            self.stop_btn.config(state=tk.NORMAL)
        else:
            self.play_btn.config(state=tk.DISABLED)
            self.stop_btn.config(state=tk.DISABLED)

    def _play(self):
        sel = self.listbox.curselection()
        if not sel:
            return
        path = self.voice_messages[sel[0]]["filepath"]
        if not path or not os.path.exists(path):
            messagebox.showerror("Error", f"File not found: {path}")
            return
        self.playing = True
        self.status_var.set("Playing...")
        threading.Thread(target=self._play_thread, args=(path,), daemon=True).start()

    def _play_thread(self, path):
        try:
            wf = wave.open(path, 'rb')
            stream = self.p.open(format=self.p.get_format_from_width(wf.getsampwidth()),
                                 channels=wf.getnchannels(),
                                 rate=wf.getframerate(),
                                 output=True)
            data = wf.readframes(self.chunk)
            while data and self.playing:
                stream.write(data)
                data = wf.readframes(self.chunk)
            stream.stop_stream(); stream.close()
            self.playing = False
            self.status_var.set("Ready")
        except Exception as e:
            self.log(f"Playback error: {e}")
            self.playing = False
            self.status_var.set("Ready")

    def _stop_play(self):
        self.playing = False
        self.log("Playback stopped")

    # ---------- Send/Receive ----------
    def _send_test(self):
        if not self.transport.is_connected:
            messagebox.showerror("Error", "Not connected to Meshtastic device")
            return
        who = "unknown"
        self.transport.send_test(f"Test message from {who} at {datetime.now().strftime('%H:%M:%S')}")

    def _send_voice(self):
        if not self.transport.is_connected:
            messagebox.showerror("Error", "Not connected to Meshtastic device")
            return
        if not self.current_recording_path or not os.path.exists(self.current_recording_path):
            messagebox.showerror("Error", "No recording available to send")
            return

        quality = self.quality_var.get()

        # Prefer Codec2
        payload = encode_to_codec2(self.current_recording_path, quality, log=self.log)
        if payload is None:
            # Fallback to original (unchanged) compressor
            self.log("Falling back to legacy zlib compressor")
            payload = ultra_compress_wav(self.current_recording_path, quality=quality, log=self.log)

        if not payload:
            messagebox.showerror("Error", "Failed to encode audio")
            return

        self.transport.set_chunk_size(self.chunk_var.get())
        self.transport.send_voice_payload(payload)
        self.send_btn.config(state=tk.DISABLED)

    def _on_text_msg(self, from_node, text):
        self._add_message(f"Text from {from_node}: {text}", None)

    def _on_voice_data(self, from_node, encoded_b64_or_concat, timestamp):
        """Handle both single and chunked paths (we’re passed Base64 string)."""
        if isinstance(encoded_b64_or_concat, str):
            b = base64.b64decode(encoded_b64_or_concat)
        else:
            # if transmission passed raw bytes (unlikely), still handle
            b = encoded_b64_or_concat

        out = f"voice_messages/received_{from_node}_{timestamp}.wav"
        try:
            decode_auto(b, out, log=self.log)
            self._add_message(f"Voice from {from_node} at {timestamp}", out)
        except Exception as e:
            self.log(f"Decode failed: {e}")

