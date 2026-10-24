"""Deterministic safety and schema-constrained proposals, without side effects."""
import json
import re
from .data import OfficialData, DataResult
from ..contracts import ActionPlan, ReferenceMessage

CRITICAL = re.compile(r"\b(?:not breathing|unconscious|unresponsive|can['’]?t breathe|cannot breathe|severe bleeding|heart attack|stroke|someone is dying|immediate danger|trapped|overdose|gas leak|smell gas|suicid(?:e|al)|kill myself|kill someone|shooting|chest pain)\b", re.I)
EMERGENCY_TEXT = ('If someone is in immediate danger, call 911 in the United States or your local emergency number now. RELAY cannot contact emergency dispatch for you, and this service is not monitored by emergency responders.')

def policy_response(text):
    return EMERGENCY_TEXT if CRITICAL.search(text) else None

def plan(text):
    fallback = policy_response(text)
    if fallback:
        return ActionPlan(spoken_response=fallback)
    lower = text.lower()
    if re.search(r'\b(earthquake|quake|seismic|shaking)\b',lower):
        return ActionPlan(spoken_response='I can check recent USGS earthquake reports near your location.', next_action='get_earthquakes', reference_message=ReferenceMessage(required=True,source='usgs'))
    if re.search(r'\b(weather|flood|tornado|hurricane|storm|wildfire|warning|alerts?)\b',lower):
        return ActionPlan(spoken_response='I can check official NWS alerts for your location.', next_action='get_weather_alerts', reference_message=ReferenceMessage(required=True,source='nws'))
    if re.search(r'\b(camera|visual|video)\b',lower):
        return ActionPlan(spoken_response='You can choose to share a single camera frame when it is safe.', next_action='request_camera',requires_user_confirmation=True)
    if re.search(r'\b(contact|notify|tell|message)\b.*\b(friend|family|mom|dad|trusted|contact)\b', lower):
        return ActionPlan(spoken_response='Only a pre-enrolled consenting trusted contact can be notified, after you approve the request.',next_action='contact_trusted',requires_user_confirmation=True)
    return ActionPlan(spoken_response='Please describe what information you need. Share a ZIP code or coordinates for an official alert lookup.')

def build_context(text):
    action = plan(text)
    if action.next_action not in ('get_weather_alerts','get_earthquakes'):
        return ''
    source = 'nws' if action.next_action == 'get_weather_alerts' else 'usgs'
    adapter = OfficialData()
    try:
        location = adapter.locate(text)
    except Exception:
        return DataResult(source=source,state='unavailable',notice='The requested location could not be verified. Ask for coordinates or confirm a ZIP code; do not invent local alerts.').model_dump_json()
    if location is None:
        return DataResult(source=source,state='location_required',notice='Ask the user for a ZIP code or latitude,longitude before looking up alerts.').model_dump_json()
    result = adapter.weather(location) if source == 'nws' else adapter.earthquakes(location)
    return result.model_dump_json()
