import pytest
from relay.persona import VOICE_GREETING, SMS_INTRO, PURPOSE, introduction
from relay.presentation import spoken
from relay.bandwidth_sms import sms_parts
from relay import worker


def test_identity_is_concise_honest_and_fits_phone_channels():
    for text in (VOICE_GREETING, SMS_INTRO, PURPOSE):
        assert len(text.split()) <= 65
        assert spoken(text) == text
        assert 'AI' in text and "can't dispatch" in text
        assert 'http' not in text
        assert len(sms_parts(text)) <= 4
    assert 'quickly' in VOICE_GREETING
    assert 'under stress' in PURPOSE


@pytest.mark.parametrize('question', ['Hello', 'Who are you?', 'What can you do?', 'Why is Relay important?', 'Who am I speaking with?'])
def test_purpose_answers_do_not_depend_on_model(question, monkeypatch):
    def unavailable(*args, **kwargs):raise AssertionError('Identity should not need inference or source lookup')
    monkeypatch.setattr(worker,'infer',unavailable)
    monkeypatch.setattr(worker,'fetch_official_context',unavailable)
    text, reference, context=worker.run_answer({'kind':'message','payload':{'text':question,'channel':'sms'}})
    assert 'AI' in text
    assert reference is None and context is None


def test_real_questions_and_critical_risk_do_not_get_an_introduction(monkeypatch):
    assert introduction('Hello, is there a weather warning for 92037?') is None
    assert introduction('Check earthquakes near 92037') is None
    from relay.phone_commands import handle
    assert handle('test-owner','+15550001111','Hello, someone is not breathing') is None
    text, _, _=worker.run_answer({'kind':'message','payload':{'text':'Hello, someone is not breathing','channel':'sms'}})
    assert '911' in text
    assert "I'm Relay" not in text


def test_help_explains_purpose_without_a_website():
    from relay.phone_commands import handle
    text=handle('test-owner','+15550001111','HELP')
    assert 'AI for official alerts' in text
    assert 'reliable information quickly' in text
    assert 'STOP' in text and 'http' not in text
    assert len(sms_parts(text)) <= 4


def test_sms_greeting_uses_one_trial_message():
    assert sms_parts(SMS_INTRO) == [SMS_INTRO]
