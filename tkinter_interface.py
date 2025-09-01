# tkinter_interface.py
import os
import json
import base64
import threading
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
import serial.tools.list_ports
from datetime import datetime

from recording import Recorder
from Voice_compression import ultra_compress_wav, QUALITY_TO_RATE
from reconstruction import ChunkReassembler, save_single_packet_base64
from transmission import MeshtasticTransport

class MeshtasticVoiceMessenger(tk.Frame):
    def __init__(self, master):
        super().__init__(master)
        self.master.title("Meshtastic Voice Messenger")
        self.master.geometry("700x700")
        self.master.minsize(600, 600)

        # UI theme
        self.bg_color = "#f0f4f8"
        self.accent_color = "#3498db"
        self.master.configure(bg=self.bg_color)
        style = ttk.Style()
        style.configure("TFrame", background=self.bg_color)
        style.configure("TLabel", background=self.bg_color, font=("Arial", 10))
        style.configure("TButton", font=("Arial", 10, "bold"))
        style.configure("Header.TLabel", font=("Arial", 14, "bold"))

        # Runtime state
        self.recorder = Recorder()
        self.transport = MeshtasticTransport(log=self.log)
        self.reassembler = ChunkReassembler(log=self.log)
        self.recording = False
        self.playing = False
        self.current_recording_path = None
        self.voice_messages = []

        # Chunk sizes (base64 bytes per packet)
        self.chunk_sizes = {"Small": 150, "Medium": 180, "Large": 200}
        self.max_chunk_size = self.chunk_sizes["Medium"]

        # Build UI + folders
        os.makedirs("voice_messages", exist_ok=True)
        self._build_widgets()

    # ---------- UI building ----------
    def _build_widgets(self):
        main = ttk.Frame(self.master, padding="20", style="TFrame")
        main.grid(row=0, column=0, sticky="nsew")
        self.master.columnconfigure(0, weight=1)
        self.master.rowconfigure(0, weight=1)
        main.columnconfigure(0, weight=1)

        header = ttk.Label(main, text="Meshtastic Voice Messenger", style="Header.TLabel")
        header.grid(row=0, column=0, pady=(0, 20))

        # Connection
        settings = ttk.LabelFrame(main, text="Connection Settings", padding="10")
        settings.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        settings.columnconfigure(1, weight=1)

        ttk.Label(settings, text="COM Port:").grid(row=0, column=0, sticky="w", padx=5, pady=5)
        self.com_port = ttk.Combobox(settings, values=self._get_ports(), width=40)
        if self.com_port['values']:
            self.com_port.set(self.com_port['values'][0])
        self.com_port.grid(row=0, column=1, sticky="ew", padx=5, pady=5)
        ttk.Button(settings, text="⟳", width=3, command=self._refresh_ports).grid(row=0, column=2, padx=(5, 0))
        self.connect_button = ttk.Button(settings, text="Connect", command=self._toggle_connection)
        self.connect_button.grid(row=0, column=3, padx=5, pady=5)

        # Recording settings
        recf = ttk.LabelFrame(main, text="Recording Settings", padding="10")
        recf.grid(row=2, column=0, sticky="ew", pady=(0, 10))
        recf.columnconfigure(1, weight=1)

        ttk.Label(recf, text="Recording Length (seconds):").grid(row=0, column=0, sticky="w", padx=5, pady=5)
        self.recording_length_var = tk.StringVar(value="3")
        ttk.Entry(recf, textvariable=self.recording_length_var, width=10).grid(row=0, column=1, sticky="w", padx=5, pady=5)

        ttk.Label(recf, text="Compression Quality:").grid(row=1, column=0, sticky="w", padx=5, pady=5)
        self.compression_quality_var = tk.StringVar(value="Low")
        self.compression_quality = ttk.Combobox(recf, textvariable=self.compression_quality_var, values=list(QUALITY_TO_RATE.keys()), width=10)
        self.compression_quality.grid(row=1, column=1, sticky="w", padx=5, pady=5)

        ttk.Label(recf, text="Chunk Size:").grid(row=2, column=0, sticky="w", padx=5, pady=5)
        self.chunk_size_var = tk.StringVar(value="Medium")
        self.chunk_size = ttk.Combobox(recf, textvariable=self.chunk_size_var, values=list(self.chunk_sizes.keys()), width=10)
        self.chunk_size.grid(row=2, column=1, sticky="w", padx=5, pady=5)
        self.chunk_size.bind("<<ComboboxSelected>>", self._update_chunk_size)
        self._add_tooltip(self.chunk_size, "Small: More reliable\nMedium: Balanced\nLarge: Faster")

        # Voice controls
        voicef = ttk.LabelFrame(main, text="Voice Controls", padding="10")
        voicef.grid(row=3, column=0, sticky="ew", pady=(0, 10))
        ttk.Button(voicef, text="Record Voice Message", width=20, command=self._toggle_recording).grid(row=0, column=0, padx=5, pady=5)
        self.send_button = ttk.Button(voicef, text="Send Voice Message", width=20, command=self._send_voice, state=tk.DISABLED)
        self.send_button.grid(row=0, column=1, padx=5, pady=5)
        ttk.Button(voicef, text="Send Test Message", width=20, command=self._send_test).grid(row=0, column=2, padx=5, pady=5)

        # Messages list + playback
        msgf = ttk.LabelFrame(main, text="Voice Messages", padding="10")
        msgf.grid(row=4, column=0, sticky="nsew", pady=(0, 10))
        msgf.rowconfigure(0, weight=1)
        msgf.columnconfigure(0, weight=1)

        self.messages_list = tk.Listbox(msgf, height=10)
        self.messages_list.grid(row=0, column=0, sticky="nsew", padx=5, pady=5)
        self.messages_list.bind('<<ListboxSelect>>', self._on_message_select)
        scroll = ttk.Scrollbar(msgf, orient=tk.VERTICAL, command=self.messages_list.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.messages_list.configure(yscrollcommand=scroll.set)

        pbf = ttk.Frame(msgf)
        pbf.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(5, 0))
        self.play_button = ttk.Button(pbf, text="Play", width=10, command=self._play, state=tk.DISABLED)
        self.play_button.grid(row=0, column=0, padx=5, pady=5)
        self.stop_button = ttk.Button(pbf, text="Stop", width=10, command=self._stop_play, state=tk.DISABLED)
        self.stop_button.grid(row=0, column=1, padx=5, pady=5)

        # Logs
        logf = ttk.LabelFrame(main, text="Log Output", padding="10")
        logf.grid(row=5, column=0, sticky="nsew", pady=(0, 10))
        logf.rowconfigure(0, weight=1)
        logf.columnconfigure(0, weight=1)
        self.log_display = scrolledtext.ScrolledText(logf, width=80, height=10, wrap=tk.WORD)
        self.log_display.grid(row=0, column=0, sticky="nsew", padx=5, pady=5)

        # Status bar
        self.status_var = tk.StringVar(value="Ready")
        ttk.Label(main, textvariable=self.status_var, relief=tk.SUNKEN, anchor="w").grid(row=6, column=0, sticky="ew")

        # Expand
        main.rowconfigure(4, weight=1)
        main.rowconfigure(5, weight=1)

    # ---------- logging ----------
    def log(self, msg):
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        self.log_display.insert(tk.END, f"[{timestamp}] {msg}\n")
        self.log_display.see(tk.END)

    # ---------- ports / connection ----------
    def _get_ports(self):
        return [p.device for p in serial.tools.list_ports.comports()]

    def _refresh_ports(self):
        ports = self._get_ports()
        self.com_port['values'] = ports
        if ports:
            self.com_port.set(ports[0])
        self.log("COM ports refreshed")

    def _toggle_connection(self):
        if not self.transport.is_connected:
            self._connect()
        else:
            self._disconnect()

    def _connect(self):
        dev = self.com_port.get()
        if not dev:
            messagebox.showerror("Error", "COM Port is required")
            return
        try:
            self.transport.connect(dev, self._on_receive)
            self.connect_button.config(text="Disconnect")
            self.status_var.set("Connected")
            self.log("Subscribed to Meshtastic messages")
        except Exception as e:
            self.log(f"Connection error: {e}")
            messagebox.showerror("Connection Error", str(e))

    def _disconnect(self):
        self.transport.disconnect()
        self.connect_button.config(text="Connect")
        self.status_var.set("Disconnected")

    # ---------- recording ----------
    def _toggle_recording(self):
        if not self.recording:
            self._start_recording()
        else:
            self.recording = False  # flag checked in recorder loop

    def _start_recording(self):
        try:
            seconds = int(self.recording_length_var.get())
            if not (1 <= seconds <= 30):
                seconds = max(1, min(30, seconds))
                self.recording_length_var.set(str(seconds))
                messagebox.showwarning("Warning", "Recording length must be between 1 and 30 seconds")

            quality = self.compression_quality_var.get()
            rate = QUALITY_TO_RATE.get(quality, 11025)

            self.current_recording_path = f"voice_messages/recording_{datetime.now().strftime('%Y%m%d_%H%M%S')}.wav"
            self.recording = True
            self.status_var.set("Recording...")
            self._rec_thread = threading.Thread(
                target=self.recorder.record_to_wav,
                args=(self.current_recording_path, rate, seconds),
                kwargs={"log": self.log, "should_stop": lambda: not self.recording},
                daemon=True
            )
            self._rec_thread.start()
            # watcher to flip UI when done
            threading.Thread(target=self._wait_rec_finish, daemon=True).start()
        except ValueError:
            messagebox.showerror("Error", "Recording length must be a number")

    def _wait_rec_finish(self):
        if hasattr(self, "_rec_thread"):
            self._rec_thread.join()
        self.master.after(0, self._recording_finished)

    def _recording_finished(self):
        self.recording = False
        self.status_var.set("Ready")
        self.send_button.config(state=tk.NORMAL)
        self.log("Recording finished")

    # ---------- list / playback ----------
    def _add_message(self, description, filepath):
        self.voice_messages.append({"description": description, "filepath": filepath})
        self.messages_list.insert(tk.END, description)

    def _on_message_select(self, _evt):
        sel = self.messages_list.curselection()
        if not sel:
            return
        idx = sel[0]
        filepath = self.voice_messages[idx]["filepath"]
        if filepath:
            self.play_button.config(state=tk.NORMAL)
            self.stop_button.config(state=tk.NORMAL)
        else:
            self.play_button.config(state=tk.DISABLED)
            self.stop_button.config(state=tk.DISABLED)

    def _play(self):
        sel = self.messages_list.curselection()
        if not sel:
            return
        idx = sel[0]
        filepath = self.voice_messages[idx]["filepath"]
        if not filepath or not os.path.exists(filepath):
            messagebox.showerror("Error", f"File not found: {filepath}")
            return

        self.playing = True
        self.status_var.set("Playing...")
        self._play_thread = threading.Thread(
            target=self.recorder.play_wav,
            args=(filepath,),
            kwargs={"log": self.log, "should_stop": lambda: not self.playing},
            daemon=True
        )
        self._play_thread.start()
        threading.Thread(target=self._wait_play_finish, daemon=True).start()

    def _wait_play_finish(self):
        if hasattr(self, "_play_thread"):
            self._play_thread.join()
        self.master.after(0, self._playback_finished)

    def _stop_play(self):
        self.playing = False
        self.log("Playback stopped")

    def _playback_finished(self):
        self.playing = False
        self.status_var.set("Ready")

    # ---------- TX ----------
    def _update_chunk_size(self, _evt=None):
        self.max_chunk_size = self.chunk_sizes.get(self.chunk_size_var.get(), 180)
        self.log(f"Chunk size set to {self.chunk_size_var.get()} ({self.max_chunk_size} bytes)")

    def _send_test(self):
        if not self.transport.is_connected:
            messagebox.showerror("Error", "Not connected to Meshtastic device")
            return
        try:
            self.transport.send_test_message()
        except Exception as e:
            self.log(f"Test send error: {e}")

    def _send_voice(self):
        if not self.transport.is_connected:
            messagebox.showerror("Error", "Not connected to Meshtastic device")
            return
        if not self.current_recording_path or not os.path.exists(self.current_recording_path):
            messagebox.showerror("Error", "No recording available to send")
            return

        self._update_chunk_size()
        try:
            comp = ultra_compress_wav(self.current_recording_path, self.compression_quality_var.get(), log=self.log)
            encoded = base64.b64encode(comp).decode('utf-8')
            self.transport.send_base64_voice(encoded, chunk_size=self.max_chunk_size)
            self.send_button.config(state=tk.DISABLED)
        except Exception as e:
            self.log(f"Voice send error: {e}")
            messagebox.showerror("Error", str(e))

    # ---------- RX ----------
    def _on_receive(self, packet, _interface):
        try:
            from_id = packet.get('fromId', 'unknown')
            port = packet.get('decoded', {}).get('portnum')
            self.log(f"Received packet {packet.get('id')} from {from_id}")

            if port == 'TEXT_MESSAGE_APP':
                message = packet.get('decoded', {}).get('text', '')
                self._add_message(f"Text from {from_id}: {message}", None)
                return

            if port == 'PRIVATE_APP':
                data = packet.get('decoded', {}).get('payload')
                if not data:
                    self.log("Empty PRIVATE_APP payload")
                    return
                try:
                    js = json.loads(data.decode('utf-8'))
                except json.JSONDecodeError:
                    self.log("PRIVATE_APP payload is not JSON")
                    return

                if {'chunk_id', 'chunk_num', 'total_chunks'}.issubset(js):
                    self.log(f"Chunk {js['chunk_num']}/{js['total_chunks']} for {js['chunk_id']} from {from_id}")
                    out = self.reassembler.process_chunk(js, from_id)
                    if out:
                        self._add_message(f"Voice from {from_id} at {datetime.now().strftime('%Y%m%d_%H%M%S')}", out)
                elif 'voice_data' in js and 'timestamp' in js:
                    # FIX: properly decompress + rebuild WAV for single-packet path
                    out = save_single_packet_base64(js['voice_data'], from_id, js['timestamp'], log=self.log)
                    self._add_message(f"Voice from {from_id} at {js['timestamp']}", out)
                elif 'test' in js:
                    self.log(f"Test message from {from_id}: {js['test']}")
                    messagebox.showinfo("Test Message", f"Received test message from {from_id}: {js['test']}")
        except Exception as e:
            self.log(f"on_receive error: {e}")

    # ---------- misc ----------
    def _add_tooltip(self, widget, text):
        def enter(_):
            x, y, _, _ = widget.bbox("insert")
            x += widget.winfo_rootx() + 25
            y += widget.winfo_rooty() + 25
            self.tooltip = tk.Toplevel(widget)
            self.tooltip.wm_overrideredirect(True)
            self.tooltip.wm_geometry(f"+{x}+{y}")
            label = tk.Label(self.tooltip, text=text, justify=tk.LEFT,
                             background="#ffffe0", relief=tk.SOLID, borderwidth=1,
                             font=("Arial", 8))
            label.pack(ipadx=1)
        def leave(_):
            if hasattr(self, 'tooltip'):
                self.tooltip.destroy()
        widget.bind("<Enter>", enter)
        widget.bind("<Leave>", leave)
