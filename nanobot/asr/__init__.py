"""ASR (Automatic Speech Recognition) plugin subsystem.

§76: Gateway scans ``~/.nanobot/plugins/asr/`` at startup to load ASR
engine registrations.  When an audio message arrives, the gateway calls
the registered script via subprocess to get a transcription.
"""

from nanobot.asr.registry import ASRRegistry

__all__ = ["ASRRegistry"]
