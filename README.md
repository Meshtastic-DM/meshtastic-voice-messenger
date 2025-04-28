### Meshtastic Voice Messenger

## Proof of Concept

This project demonstrates the concept of sending voice messages over Meshtastic mesh networks. It allows users to record, compress, and transmit audio data between Meshtastic devices.

**⚠️ IMPORTANT: This is a proof of concept only and not intended for production use. The transmission is unreliable and serves primarily to demonstrate the possibility of voice communication over Meshtastic.**





## Description

Meshtastic Voice Messenger is an experimental Python application that explores the possibility of transmitting compressed audio data over Meshtastic mesh networks. The application provides a simple GUI for recording voice messages, compressing them using various algorithms, and sending them in chunks to other Meshtastic devices on the network.

The primary goal of this project is to demonstrate that voice communication is technically possible over Meshtastic's low-bandwidth protocol, even though it's not what the protocol was originally designed for.

## Features

- Record voice messages of configurable length
- Compress audio using different quality settings
- Split large messages into chunks for transmission
- Reassemble received chunks into complete audio messages
- Play received voice messages
- Send test messages to verify connectivity
- Detailed logging for debugging


## Best Settings

Through extensive testing, we've found that the following settings work best for most situations:

- **Compression Quality**: "Very Low"
- **Chunk Size**: "Small" (150 bytes)


These settings provide the best balance between audio quality and transmission reliability. However, even with these optimal settings, transmission remains experimental and may fail under various conditions.

## Requirements

- Python 3.7+
- Meshtastic-compatible device (e.g., T-Beam, Heltec, LilyGo)
- Required Python packages:

- meshtastic
- pyaudio
- numpy
- tkinter





## Installation

1. Clone this repository:

```plaintext
git clone https://github.com/TelemetryHarbor/meshtastic-voice-messenger.git
cd meshtastic-voice-messenger
```


2. Install required packages:

```plaintext
pip install meshtastic pyaudio numpy
```


3. Connect your Meshtastic device via USB
4. Run the application:

```plaintext
python app.py
```




## Usage

1. Select your device's COM port and click "Connect"
2. Set your desired recording length, compression quality, and chunk size
3. Click "Record Voice Message" to record audio
4. Click "Send Voice Message" to transmit the recording
5. Received voice messages will appear in the list and can be played back


## Limitations

- **Experimental Only**: This is not a reliable communication tool
- **High Failure Rate**: Expect message transmission to fail frequently
- **Limited Audio Quality**: Audio is heavily compressed to fit within bandwidth constraints
- **Network Congestion**: Sending voice messages can flood the mesh network
- **Battery Impact**: Frequent use will significantly impact device battery life


## Future Possibilities

While this proof of concept demonstrates that voice transmission is technically possible, a production-ready implementation would require:

- More efficient compression algorithms
- Better error correction and recovery
- Improved acknowledgment and retry mechanisms
- Optimized bandwidth usage
- Integration with the Meshtastic protocol at a deeper level


## Contributing

This project is primarily a demonstration, but suggestions and improvements are welcome. Feel free to fork the repository and submit pull requests.

## License

This project is licensed under the MIT License - see the LICENSE file for details.

## Acknowledgments

- The Meshtastic project for creating an open-source mesh networking platform
- All contributors to the Python libraries used in this project


---

*Remember: This is an experimental proof of concept to demonstrate the possibility of voice communication over Meshtastic. It is not intended for reliable communication or production use.*
