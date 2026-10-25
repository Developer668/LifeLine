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
    if os.getenv('RELAY_ASR_ENGINE') == 'openai':
        return bool(os.getenv('OPENAI_API_KEY')) and Path(os.getenv('RELAY_PIPER_MODEL', '')).is_file()
    model = os.getenv('RELAY_WHISPER_MODEL' if os.getenv('RELAY_ASR_ENGINE') == 'whisper' else 'RELAY_VOSK_MODEL', '')
    return (bool(model) and Path(model).is_dir()
            and Path(os.getenv('RELAY_PIPER_MODEL', '')).is_file())


class VoskRecognizer:
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


_whisper_model = None
_whisper_path = None
_whisper_lock = threading.Lock()


class WhisperRecognizer:
    """Phone-rate voice activity with bounded in-memory utterance transcription."""
    def __init__(self, api_primary=False):
        global _whisper_model, _whisper_path
        import webrtcvad
        from collections import deque
        self.api_primary = api_primary
        if not api_primary:
            from faster_whisper import WhisperModel
            path = os.getenv('RELAY_WHISPER_MODEL', '')
            if not path or not Path(path).is_dir():
                raise RuntimeError('Local Whisper model is not configured')
            with _whisper_lock:
                if _whisper_path != path:
                    _whisper_model = WhisperModel(path, device='cpu', compute_type='int8',
                        cpu_threads=4, num_workers=1, local_files_only=True)
                    _whisper_path = path
            self.model = _whisper_model
        self.vad = webrtcvad.Vad(2)
        self.pending = bytearray()
        self.preroll = deque(maxlen=8)
        self.buffer = bytearray()
        self.active = False
        self.voiced = 0
        self.quiet = 0
        self.notified = False
        self.last_decode = {}
        self.fallback_attempts = 0
        self.local_failures = 0

    def decode(self, pcm):
        if getattr(self, 'api_primary', False):
            from . import openai_fallback
            duration = len(pcm) / 16000
            self.last_decode = {'seconds': round(duration, 2), 'recognizer': 'openai'}
            try:
                text = openai_fallback.transcribe(pcm)
                self.last_decode['word_count'] = len(text.split())
                return normalize_transcript(text) if reliable_transcript(text, duration) else ''
            except openai_fallback.FallbackUnavailable as error:
                self.last_decode['error_type'] = type(error).__name__
                return ''
        import audioop
        import numpy as np
        resampled, _ = audioop.ratecv(pcm, 2, 1, 8000, 16000, None)
        samples = np.frombuffer(resampled, dtype='<i2').astype(np.float32) / 32768.0
        with _whisper_lock:
            segments, _ = self.model.transcribe(samples, language='en', beam_size=1,
                condition_on_previous_text=False, vad_filter=False, temperature=0,
                no_speech_threshold=.6, log_prob_threshold=-1.0)
            segments = list(segments)
            accepted = [s for s in segments if s.no_speech_prob < .6 and s.avg_logprob > -.8
                        and s.compression_ratio < 2.2]
            text = ' '.join(s.text.strip() for s in accepted)
        duration = len(pcm) / 16000
        self.last_decode = {'seconds': round(duration, 2), 'accepted_segments': len(accepted),
                            'word_count': len(text.split())}
        # Hallucinated loops are common on noisy/silent telephone audio. Never
        # feed them to the reasoning model or speak an endless clarification loop.
        reliable = reliable_transcript(text, duration)
        confident = reliable and accepted and min(s.avg_logprob for s in accepted) >= -.45
        if not confident:
            self.local_failures += 1
        speech_evidence = duration >= .5 and any(s.no_speech_prob < .8 for s in segments)
        if (not confident or self.local_failures >= 2) and speech_evidence and self.fallback_attempts < 3:
            from . import openai_fallback
            if openai_fallback.enabled():
                self.fallback_attempts += 1
                try:
                    alternate = openai_fallback.transcribe(pcm)
                    if reliable_transcript(alternate, duration):
                        self.last_decode['recognizer'] = 'openai_fallback'
                        return normalize_transcript(alternate)
                except openai_fallback.FallbackUnavailable:
                    pass
        return normalize_transcript(text[:2000].strip()) if reliable else ''

    def feed(self, mulaw):
        import audioop
        self.pending.extend(mulaw)
        event = {'speech_start': False, 'speech_confirmed': False, 'final': False, 'text': ''}
        while len(self.pending) >= 160:
            frame = bytes(self.pending[:160]); del self.pending[:160]
            pcm = audioop.ulaw2lin(frame, 2)
            voiced = self.vad.is_speech(pcm, 8000) and audioop.rms(pcm, 2) >= 180
            if not self.active:
                self.preroll.append(pcm)
                self.voiced = self.voiced + 1 if voiced else 0
                if self.voiced >= 4:
                    self.active = True
                    self.buffer = bytearray(b''.join(self.preroll))
                    self.quiet = 0
                    self.notified = False
                    event['speech_start'] = True
                continue
            self.buffer.extend(pcm)
            self.quiet = 0 if voiced else self.quiet + 1
            if voiced:
                self.voiced += 1
            if self.voiced >= 12 and not self.notified:
                event['speech_confirmed'] = True
                self.notified = True
            if self.quiet >= 30 or len(self.buffer) >= 8000 * 2 * 10:
                audio = bytes(self.buffer)
                eligible = self.voiced >= 12
                self.active = False
                self.buffer.clear(); self.preroll.clear()
                self.voiced = self.quiet = 0
                event.update(final=True, speech_confirmed=False, text=self.decode(audio) if eligible else '')
        return event


def Recognizer():
    engine = os.getenv('RELAY_ASR_ENGINE')
    return WhisperRecognizer(api_primary=engine == 'openai') if engine in ('whisper', 'openai') else VoskRecognizer()


def warmup():
    Recognizer()
    sentence_audio('Ready.')


def reliable_transcript(text, duration):
    words = re.findall(r"[a-z0-9']+", text.lower())
    if not words:
        return False
    from .engine import policy_response
    if policy_response(text) or ('help' in words and set(words) <= {'help', 'please', 'me'}):
        return True
    if len(words) > max(12, duration * 5):
        return False
    if len(words) >= 8:
        from collections import Counter
        for width in (2, 3, 4):
            phrases = Counter(tuple(words[i:i+width]) for i in range(len(words)-width+1))
            if any(count >= 4 and count * width >= len(words) * .5 for count in phrases.values()):
                return False
    return True


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
