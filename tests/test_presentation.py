import pytest
from relay.presentation import clean_text,reference,spoken


def test_machine_envelopes_and_reasoning_never_reach_readers():
    assert clean_text('<think>internal notes</think> **A clear answer.**')=='A clear answer.'
    assert clean_text('```json\n{"spoken_response":"A clear answer."}\n```')=='A clear answer.'
    for value in ['{"next_action":"respond"}','<think>unfinished reasoning','[{"text":"raw"}]']:
        with pytest.raises(ValueError):clean_text(value)
    assert spoken('Certainly! Read the [official source](https://weather.gov).')=='Read the official source.'


def test_reference_keeps_verified_links_and_a_readable_update_time():
    source='https://earthquake.usgs.gov/earthquakes/eventpage/test'
    context={'source':'usgs','state':'available','source_updated_at':'2026-10-24T22:58:00Z',
      'fetched_at':'2026-10-24T22:59:00Z','alerts':[{'title':'M 2.0 - Example area','source_url':source,'source_time':'2026-10-24T12:00:00Z'}]}
    value=reference('Reports do not establish safety.',context)
    assert value.startswith('Earthquake update\n\n')
    assert 'Official reports\nM 2.0 - Example area' in value
    assert 'Oct 24, 2026 at 15:58 PDT' in value
    assert source in value and len(value)<=1000
    assert 'spoken_response' not in value


def test_no_data_is_not_presented_as_no_alerts_and_clipping_preserves_links():
    for state in ['unavailable','location_required']:
        value=reference('No alerts.',{'source':'nws','state':state})
        assert 'No alerts.' not in value
    source='https://weather.gov/alerts/'+'a'*100
    context={'source':'nws','state':'stale','fetched_at':'2026-10-24T22:00:00Z','alerts':[
        {'title':'Long warning '*40,'severity':'Severe','source_url':source}]}
    value=reference('Important details. '*25,context)
    assert source in value and len(value)<=1000 and 'out of date' in value


def test_long_answers_are_not_cut_before_a_late_safety_qualification():
    value='This is a sentence with many details '+('additional detail '*70)+'. I cannot confirm safety.'
    output=spoken(value)
    assert 'many details' not in output and len(output.split())<65
