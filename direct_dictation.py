import riva.client
import riva.client.audio_io
import subprocess
import os
import sys

# Configuration
SERVER = "localhost:50051"
# Match your manual socket path
os.environ["YDOTOOL_SOCKET"] = "/tmp/ydotool/socket"

def type_text(text):
    """Feeds text to ydotool client without unsupported flags."""
    if not text:
        return
    try:
        # Reverting to standard call; environment variable handles the socket
        subprocess.run([
            "ydotool", 
            "type", 
            "--", 
            text
        ], check=False)
    except Exception as e:
        print(f"ydotool error: {e}")

def main():
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
            profanity_filter=True,
            enable_automatic_punctuation=True,
            verbatim_transcripts=False,
            sample_rate_hertz=16000,
            audio_channel_count=1,
        ),
        interim_results=True,
    )

    # Use the default input device (index 11 as identified earlier)
    with riva.client.audio_io.MicrophoneStream(16000, 1600) as audio_chunks:
        responses = asr_service.streaming_response_generator(
            audio_chunks=audio_chunks,
            streaming_config=config,
        )

        print("🎙️ Local GPU Dictation: READY")
        print("Listening for speech...")

        last_transcript = ""
        for response in responses:
            if not response.results:
                continue
            
            result = response.results[0]
            transcript = result.alternatives[0].transcript

            if result.is_final:
                # Type the delta of the sentence plus a trailing space
                new_part = transcript[len(last_transcript):]
                type_text(new_part + " ")
                last_transcript = ""
            else:
                # Word-by-word interim typing
                if transcript.startswith(last_transcript):
                    new_part = transcript[len(last_transcript):]
                    type_text(new_part)
                    last_transcript = transcript

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopping dictation...")
        sys.exit(0)