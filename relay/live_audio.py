"""Local streaming ASR and sentence TTS for Bandwidth's 8kHz PCMU transport.

No recordings or WAV files are created. Optional dependencies load lazily.
"""
import json
import os
import threading
import re
from pathlib import Path

_asr_model = None
_asr_path = None
_asr_lock = threading.Lock()
_tts_voice = None
_tts_path = None
_tts_lock = threading.Lock()


def normalize_transcript(text):
    """Preserve five individually spoken ZIP digits for deterministic lookups."""
    digits = {'zero': '0', 'oh': '0', 'one': '1', 'two': '2', 'three': '3',
              'four': '4', 'five': '5', 'six': '6', 'seven': '7', 'eight': '8', 'nine': '9'}
    pattern = r'\b(?:' + '|'.join(digits) + r')(?:[ -]+(?:' + '|'.join(digits) + r')){4}\b'
    def replace(match):
        before = text[:match.start()]
        after = text[match.end():]
        if re.search(r'\b(?:' + '|'.join(digits) + r')[ -]+$', before, re.I) or re.match(r'[ -]+(?:' + '|'.join(digits) + r')\b', after, re.I):
            return match.group()
        return ''.join(digits[word.lower()] for word in re.split(r'[ -]+', match.group()))
    return re.sub(pattern, replace, text, flags=re.I)


def ready():
    return (bool(os.getenv('RELAY_VOSK_MODEL'))
            and Path(os.getenv('RELAY_VOSK_MODEL', '')).is_dir()
            and Path(os.getenv('RELAY_PIPER_MODEL', '')).is_file())


class Recognizer:
    def __init__(self):
        global _asr_model, _asr_path
        from vosk import Model, KaldiRecognizer, SetLogLevel
        path = os.getenv('RELAY_VOSK_MODEL', '')
        if not path or not Path(path).is_dir():
            raise RuntimeError('Offline transcription model is not configured')
        with _asr_lock:
            if _asr_path != path:
                SetLogLevel(-1)
                _asr_model = Model(path)
                _asr_path = path
        self.recognizer = KaldiRecognizer(_asr_model, 16000)
        self.rate_state = None
        self.voiced_samples = 0
        self.quiet_samples = 0
        self.in_utterance = False

    def feed(self, mulaw):
        import audioop
        pcm = audioop.ulaw2lin(mulaw, 2)
        speech_start = False
        # Three 20ms frames above the noise gate trigger barge-in. Inbound track
        # only: the carrier's outgoing TTS must never be fed into this recognizer.
        if audioop.rms(pcm, 2) >= int(os.getenv('RELAY_VOICE_VAD_RMS', '450')):
            self.voiced_samples += len(mulaw)
            self.quiet_samples = 0
            if self.voiced_samples >= 480 and not self.in_utterance:
                speech_start = True
                self.in_utterance = True
        else:
            self.quiet_samples += len(mulaw)
            if self.quiet_samples >= 2400:
                self.voiced_samples = 0
                self.in_utterance = False
        wide, self.rate_state = audioop.ratecv(pcm, 2, 1, 8000, 16000, self.rate_state)
        final = self.recognizer.AcceptWaveform(wide)
        result = json.loads(self.recognizer.Result() if final else self.recognizer.PartialResult())
        text = result.get('text' if final else 'partial', '').strip()
        return {'speech_start': speech_start, 'final': bool(final), 'text': normalize_transcript(text[:2000])}


def sentence_audio(text):
    """Produce one sentence as raw PCMU in memory, never a file."""
    global _tts_voice, _tts_path
    from piper import PiperVoice
    import audioop
    path = os.getenv('RELAY_PIPER_MODEL', '')
    if not path or not Path(path).is_file():
        raise RuntimeError('Local voice is not configured')
    with _tts_lock:
        if path != _tts_path:
            _tts_voice = PiperVoice.load(path)
            _tts_path = path
        output = bytearray()
        state = None
        for chunk in _tts_voice.synthesize(text):
            pcm, state = audioop.ratecv(chunk.audio_int16_bytes, 2, 1, chunk.sample_rate, 8000, state)
            output.extend(audioop.lin2ulaw(pcm, 2))
        return bytes(output)
