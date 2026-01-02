# /// script
# dependencies = [
#   "sounddevice",
# ]
# ///

import sounddevice as sd


def check_rates():
    print("Scanning audio devices...\n")
    try:
        devices = sd.query_devices()
    except Exception as e:
        print(f"Error querying devices: {e}")
        return

    # Filter for input devices
    found_input = False
    for i, dev in enumerate(devices):
        if dev["max_input_channels"] > 0:
            found_input = True
            print(f"Device ID {i}: {dev['name']}")
            print(f"  Max Input Channels: {dev['max_input_channels']}")
            print(f"  Default Sample Rate: {dev['default_samplerate']} Hz")

            print("  Supported Possible Rates (Probing): ", end="", flush=True)
            supported = []
            # Common audio sample rates to test
            test_rates = [16000, 32000, 44100, 48000, 88200, 96000]
            for rate in test_rates:
                try:
                    # check_input_settings raises an error if the config is invalid
                    sd.check_input_settings(device=i, samplerate=rate)
                    supported.append(rate)
                except Exception:
                    pass

            if supported:
                print(f"{supported}")
            else:
                print("None of the common rates matched (or device busy)")
            print("-" * 40)

    if not found_input:
        print("No input devices found.")


if __name__ == "__main__":
    check_rates()
