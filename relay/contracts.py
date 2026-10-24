"""Validated interchange types; proposals never execute provider actions."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

class ReferenceMessage(BaseModel):
    model_config = ConfigDict(extra='forbid')
    required: bool = False
    source: Literal['nws', 'usgs', 'none'] = 'none'
    requires_consent: bool = True

class ActionPlan(BaseModel):
    model_config = ConfigDict(extra='forbid')
    spoken_response: str = Field(min_length=1, max_length=1800)
    reference_message: ReferenceMessage = Field(default_factory=ReferenceMessage)
    next_action: Literal['respond', 'get_weather_alerts', 'get_earthquakes', 'request_camera', 'contact_trusted'] = 'respond'
    requires_user_confirmation: bool = False

class IncidentEvent(BaseModel):
    model_config = ConfigDict(extra='forbid')
    event_id: str = Field(min_length=1, max_length=100)
    incident_id: str = Field(min_length=1, max_length=100)
    session_id: str = Field(min_length=1, max_length=100)
    channel: Literal['voice','sms','camera','demo']
    type: Literal['user_message','observation','response','consent']
    payload: dict
