"""Apply the pinned Omni/StepAudio vocoder compatibility repair observed on MoLab.
Preserves the original source and refuses unexpected upstream code.
"""
from importlib.util import find_spec
from pathlib import Path

spec=find_spec('vllm_omni')
if not spec or not spec.origin:raise SystemExit('vLLM-Omni is not installed')
path=Path(spec.origin).parent/'model_executor/models/step_audio2/step_audio2_token2wav.py'
text=path.read_text()
marker='        # RELAY: register the reference vocoder package namespaces before YAML construction.\n'
needle='        with open(f"{self.model_path}/flow.yaml") as f:\n            configs = load_hyperpyyaml(f)'
if marker in text:
    print('Vocoder namespace compatibility repair already present.')
elif text.count(needle)==1:
    backup=path.with_suffix('.py.relay-original')
    if not backup.exists():backup.write_text(text)
    repair=marker+'        from stepaudio2.token2wav import _setup_cosyvoice2_alias\n        _setup_cosyvoice2_alias()\n'+needle
    path.write_text(text.replace(needle,repair))
    print('Registered reference StepAudio vocoder namespaces in pinned Omni adapter.')
else:
    raise SystemExit('Unexpected upstream source. Review this version before patching.')
