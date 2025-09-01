# recording.py
import pyaudio
import wave

class Recorder:
    """Microphone capture to WAV and WAV playback (blocking helpers)."""

    def __init__(self, chunk=1024, fmt=pyaudio.paInt16, channels=1):
        self.chunk = chunk
        self.format = fmt
        self.channels = channels
        self.p = pyaudio.PyAudio()

    def record_to_wav(self, filepath, rate, seconds, log=lambda *a, **k: None, should_stop=lambda: False):
        stream = self.p.open(format=self.format, channels=self.channels, rate=rate, input=True, frames_per_buffer=self.chunk)
        frames = []
        n_chunks = int(rate / self.chunk * seconds)

        for _ in range(n_chunks):
            if should_stop():
                break
            data = stream.read(self.chunk)
            frames.append(data)

        stream.stop_stream()
        stream.close()

        with wave.open(filepath, 'wb') as wf:
            wf.setnchannels(self.channels)
            wf.setsampwidth(self.p.get_sample_size(self.format))
            wf.setframerate(rate)
            wf.writeframes(b''.join(frames))

        log(f"Recording saved to {filepath}")

    def play_wav(self, filepath, log=lambda *a, **k: None, should_stop=lambda: False):
        wf = wave.open(filepath, 'rb')
        stream = self.p.open(
            format=self.p.get_format_from_width(wf.getsampwidth()),
            channels=wf.getnchannels(),
            rate=wf.getframerate(),
            output=True
        )
        data = wf.readframes(self.chunk)
        while data and not should_stop():
            stream.write(data)
            data = wf.readframes(self.chunk)

        stream.stop_stream()
        stream.close()
