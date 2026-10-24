"""Bounded official-data adapters. Source text is data, never instructions.
NWS: https://www.weather.gov/documentation/services-web-api
USGS: https://earthquake.usgs.gov/fdsnws/event/1/
"""
from datetime import datetime, timedelta, timezone
import json
import os
import re
from urllib.parse import urlparse
import httpx
from pydantic import BaseModel, Field

UTC = timezone.utc

def now():
    return datetime.now(UTC)

def stamp(value):
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000, UTC)
    if isinstance(value, str):
        dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if dt.tzinfo is None:
            raise ValueError('Timestamp lacks timezone')
        return dt.astimezone(UTC)
    raise ValueError('Missing source timestamp')

def source_url(value, host):
    parsed = urlparse(str(value or ''))
    return str(value) if parsed.scheme == 'https' and (parsed.hostname == host or (parsed.hostname or '').endswith('.' + host)) else 'https://' + host

class Location(BaseModel):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    description: str
    approximate: bool = True

class Alert(BaseModel):
    title: str
    severity: str
    source_url: str
    source_time: datetime
    expires_at: datetime | None = None
    area: str = ''
    stale: bool = False
    details: str = ''

class DataResult(BaseModel):
    source: str
    state: str
    fetched_at: datetime = Field(default_factory=now)
    source_updated_at: datetime | None = None
    location: Location | None = None
    alerts: list[Alert] = Field(default_factory=list)
    notice: str

class OfficialData:
    def __init__(self, client=None):
        self.client = client

    def _get(self, url, **kwargs):
        headers = {'User-Agent': os.getenv('RELAY_DATA_USER_AGENT', 'RELAY/1.0 (emergency information prototype)'), 'Accept':'application/geo+json, application/json'}
        if self.client is not None:
            response = self.client.get(url, headers=headers, **kwargs)
        else:
            response = httpx.get(url, headers=headers, timeout=httpx.Timeout(3, connect=2), follow_redirects=False, **kwargs)
        response.raise_for_status()
        return response.json()

    def locate(self, text):
        coords = re.search(r'(?<![\d.])(-?\d{1,2}\.\d+)\s*,\s*(-?\d{1,3}\.\d+)(?![\d.])', text)
        if coords:
            return Location(latitude=float(coords[1]), longitude=float(coords[2]), description='User-supplied coordinates', approximate=False)
        match = re.search(r'\b(\d{5})\b', text)
        if not match:
            return None
        p = self._get('https://api.zippopotam.us/us/' + match[1])['places'][0]
        return Location(latitude=float(p['latitude']), longitude=float(p['longitude']), description=f"Approximate ZIP centroid {match[1]} ({p.get('place name', '')}); geocoding by Zippopotam.us, not an official emergency source")

    def weather(self, location):
        try:
            data = self._get('https://api.weather.gov/alerts/active', params={'point':f'{location.latitude},{location.longitude}'})
            fetched = now()
            alerts = []
            for feature in data.get('features', [])[:10]:
                p = feature['properties']
                sent = stamp(p.get('sent'))
                end = stamp(p.get('expires') or p.get('ends')) if p.get('expires') or p.get('ends') else None
                alerts.append(Alert(title=str(p.get('headline') or p.get('event') or 'Weather alert')[:300], severity=str(p.get('severity','Unknown')),
                    source_url=source_url(p.get('@id') or feature.get('id'), 'weather.gov'), source_time=sent, expires_at=end,
                    area=str(p.get('areaDesc',''))[:300], stale=bool(end and end <= fetched) or sent > fetched + timedelta(minutes=5), details=str(p.get('instruction') or '')[:700]))
            updated = stamp(data['updated']) if data.get('updated') else None
            stale = any(a.stale for a in alerts) or bool(updated and fetched-updated > timedelta(minutes=15))
            return DataResult(source='nws', state='stale' if stale else 'available', location=location, alerts=alerts, source_updated_at=updated,
                notice='NWS active alerts at the requested point. An empty result does not establish safety or absence of local evacuation orders. ZIP centroids may not match your position.')
        except (httpx.HTTPError, ValueError, KeyError, TypeError, IndexError, OverflowError):
            return DataResult(source='nws', state='unavailable', location=location, notice='NWS data could not be verified. Do not assume conditions are safe.')

    def earthquakes(self, location):
        try:
            fetched = now()
            data = self._get('https://earthquake.usgs.gov/fdsnws/event/1/query', params={'format':'geojson','latitude':location.latitude,'longitude':location.longitude,'maxradiuskm':100,'starttime':(fetched-timedelta(days=1)).isoformat(), 'orderby':'time','limit':10,'nodata':200})
            generated = stamp(data['metadata']['generated'])
            alerts = []
            for feature in data.get('features', []):
                p = feature['properties']
                occurred = stamp(p['time'])
                alerts.append(Alert(title=str(p.get('title') or 'Earthquake')[:300], severity=str(p.get('alert') or 'Not assigned; magnitude ' + str(p.get('mag'))),
                    source_url=source_url(p.get('url'),'earthquake.usgs.gov'), source_time=occurred,
                    area=str(p.get('place',''))[:300], stale=occurred < fetched-timedelta(days=1), details='Magnitude is not a measure of safety at your location.'))
            stale = fetched-generated > timedelta(minutes=15) or generated > fetched+timedelta(minutes=5)
            return DataResult(source='usgs',state='stale' if stale else 'available',source_updated_at=generated, location=location,alerts=alerts,
                notice='USGS reported events in the past 24 hours within 100 km of the requested point. Events may be revised; no result does not establish safety or predict future earthquakes.')
        except (httpx.HTTPError, ValueError, KeyError, TypeError, IndexError, OverflowError):
            return DataResult(source='usgs',state='unavailable',location=location,notice='USGS data could not be verified. Earthquake absence is not a safety assessment.')
