import riva.client
import riva.client.audio_io
import subprocess
import sys
import threading
import queue
import time

# Configuration
SERVER = "localhost:50051"
SAMPLE_RATE = 48000
CHUNK_DURATION_MS = 100  # 100ms chunks for lower latency

# Endpointing parameters - tune for faster response
# Lower values = faster end-of-utterance detection (but may cut off speech)
# Note: stop_history_eou must be < stop_history
ENDPOINTING = {
    "start_history": 80,        # Frames of audio to analyze for speech start
    "start_threshold": 0.5,     # Confidence threshold to start speech
    "stop_history": 250,        # Frames to analyze for speech end
    "stop_threshold": 0.5,      # Confidence threshold to stop
    "stop_history_eou": 200,    # End-of-utterance history (must be < stop_history)
    "stop_threshold_eou": 0.8,  # End-of-utterance confidence
}

# Restart stream every N seconds to prevent buffer buildup
STREAM_RESTART_INTERVAL = 60  # seconds (0 to disable)

# Queue for non-blocking typing
typing_queue = queue.Queue()
typing_lock = threading.Lock()

# Word boosting: Add domain-specific words/phrases you frequently use
# This significantly improves recognition of technical terms, names, etc.
BOOSTED_WORDS = [
    # Programming terms
    "Python", "JavaScript", "TypeScript", "GitHub", "Copilot",
    "async", "await", "function", "variable", "const", "let",
    # Add your frequently used terms here
]
BOOST_SCORE = 10.0  # Higher = stronger bias toward these words (typical: 4-20)


def typing_worker():
    """Background thread that processes typing commands from the queue."""
    while True:
        cmd = typing_queue.get()
        if cmd is None:  # Shutdown signal
            break
        action, arg = cmd
        try:
            with typing_lock:
                if action == "type":
                    subprocess.run([
                        "xdotool", "type",
                        "--clearmodifiers", "--delay", "0",
                        "--", arg
                    ], check=False)
                elif action == "delete":
                    subprocess.run([
                        "xdotool", "key",
                        "--clearmodifiers", "--delay", "0",
                    ] + ["BackSpace"] * arg, check=False)
        except Exception as e:
            print(f"xdotool error: {e}")
        typing_queue.task_done()


def type_text(text):
    """Queue text for typing (non-blocking)."""
    if text:
        typing_queue.put(("type", text))


def delete_chars(count):
    """Queue character deletion (non-blocking)."""
    if count > 0:
        typing_queue.put(("delete", count))

def main():
    # Start background typing thread
    typing_thread = threading.Thread(target=typing_worker, daemon=True)
    typing_thread.start()

    try:
        auth = riva.client.Auth(uri=SERVER)
        asr_service = riva.client.ASRService(auth)
    except Exception as e:
        print(f"Failed to connect to Riva at {SERVER}: {e}")
        return

    config = riva.client.StreamingRecognitionConfig(
        config=riva.client.RecognitionConfig(
            encoding=riva.client.AudioEncoding.LINEAR_PCM,
            language_code="en-US",
            max_alternatives=1,
            profanity_filter=False,  # Disable for dictation - avoids censoring
            enable_automatic_punctuation=True,
            verbatim_transcripts=False,  # Apply inverse text normalization (numbers, dates, etc.)
            sample_rate_hertz=SAMPLE_RATE,
            audio_channel_count=1,
        ),
        interim_results=True,
    )

    # Add word boosting for domain-specific vocabulary
    if BOOSTED_WORDS:
        riva.client.add_word_boosting_to_config(config, BOOSTED_WORDS, BOOST_SCORE)

    # Add endpointing parameters for faster response
    riva.client.add_endpoint_parameters_to_config(
        config,
        start_history=ENDPOINTING["start_history"],
        start_threshold=ENDPOINTING["start_threshold"],
        stop_history=ENDPOINTING["stop_history"],
        stop_history_eou=ENDPOINTING["stop_history_eou"],
        stop_threshold=ENDPOINTING["stop_threshold"],
        stop_threshold_eou=ENDPOINTING["stop_threshold_eou"],
    )

    # Calculate chunk size for desired latency (samples = rate * duration_sec)
    chunk_size = int(SAMPLE_RATE * CHUNK_DURATION_MS / 1000)

    print("🎙️ Local GPU Dictation: READY")
    print("Listening for speech... (Ctrl+C to stop)")

    while True:  # Outer loop for stream restarts
        stream_start_time = time.time()
        
        with riva.client.audio_io.MicrophoneStream(SAMPLE_RATE, chunk_size) as audio_chunks:
            responses = asr_service.streaming_response_generator(
                audio_chunks=audio_chunks,
                streaming_config=config,
            )

            last_transcript = ""
            typed_length = 0

            for response in responses:
                # Check if we need to restart stream to prevent lag buildup
                if STREAM_RESTART_INTERVAL > 0:
                    elapsed = time.time() - stream_start_time
                    if elapsed > STREAM_RESTART_INTERVAL:
                        print(f"\n[Restarting stream after {int(elapsed)}s to prevent lag...]")
                        break  # Exit inner loop, restart stream

                if not response.results:
                    continue

                result = response.results[0]
                transcript = result.alternatives[0].transcript

                if result.is_final:
                    # Final result: correct any interim differences and add space
                    if typed_length > 0:
                        delete_chars(typed_length)
                    type_text(transcript + " ")
                    last_transcript = ""
                    typed_length = 0
                else:
                    # Interim result: handle corrections and additions
                    if transcript.startswith(last_transcript):
                        new_part = transcript[len(last_transcript):]
                        type_text(new_part)
                        typed_length += len(new_part)
                    else:
                        delete_chars(typed_length)
                        type_text(transcript)
                        typed_length = len(transcript)
                    last_transcript = transcript

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopping dictation...")
        typing_queue.put(None)  # Signal typing thread to exit
        sys.exit(0)