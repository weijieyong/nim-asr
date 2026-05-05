# /// script
# dependencies = [
#   "pyaudio",
#   "sounddevice",
# ]
# ///

import pyaudio
import sounddevice as sd


def check_rates() -> None:
    print("Scanning audio devices...\n")
    try:
        devices = sd.query_devices()
    except Exception as e:
        print(f"Error querying devices: {e}")
        return

    found_input = False
    for i, dev in enumerate(devices):
        if dev["max_input_channels"] > 0:
            found_input = True
            print(f"Device ID {i}: {dev['name']}")
            print(f"  Max Input Channels: {dev['max_input_channels']}")
            print(f"  Default Sample Rate: {dev['default_samplerate']} Hz")

            print("  Supported Possible Rates (Probing): ", end="", flush=True)
            supported = []
            for rate in [16000, 32000, 44100, 48000, 88200, 96000]:
                try:
                    sd.check_input_settings(device=i, samplerate=rate)
                    supported.append(rate)
                except Exception:
                    pass

            print(supported if supported else "None of the common rates matched (or device busy)")
            print("-" * 40)

    if not found_input:
        print("No input devices found.")


def get_usb_mic_index(target_name: str = "usb") -> int | None:
    p = pyaudio.PyAudio()
    for i in range(p.get_device_count()):
        info = p.get_device_info_by_index(i)
        if target_name.lower() in info["name"].lower() and info["maxInputChannels"] > 0:
            p.terminate()
            return i
    p.terminate()
    return None


if __name__ == "__main__":
    check_rates()
    print()
    index = get_usb_mic_index()
    print(f"USB mic device index (pyaudio): {index}")
