import json
from datetime import datetime, timedelta, timezone
import httpx
import pytest
from pydantic import ValidationError
from relay.engine import plan, policy_response, build_context
from relay.engine.data import OfficialData, Location
from relay.contracts import ActionPlan

LOCATION=Location(latitude=32.8,longitude=-117.2,description='Test coordinates',approximate=False)

def adapter(body,status=200):
    return OfficialData(httpx.Client(transport=httpx.MockTransport(lambda req:httpx.Response(status,json=body))))

@pytest.mark.parametrize('text',["can't breathe",'person is unconscious','gas leak','severe bleeding','heart attack','I want to kill myself'])
def test_critical_referral(text):
    assert '911' in policy_response(text)
    assert plan(text).next_action == 'respond'

def test_planner_only_proposes():
    p=plan('notify my trusted contact')
    assert p.next_action=='contact_trusted' and p.requires_user_confirmation
    with pytest.raises(ValidationError):
        ActionPlan(spoken_response='Calling now',next_action='dispatch_911')
    with pytest.raises(ValidationError):
        ActionPlan(spoken_response='Hello',arbitrary_phone='+15555555555')

def test_location_required_without_network():
    assert json.loads(build_context('earthquake near me'))['state']=='location_required'
    loc=OfficialData().locate('32.8,-117.2')
    assert loc.latitude==32.8 and not loc.approximate

def test_zip_is_explicitly_approximate():
    loc=adapter({'places':[{'latitude':'32.8','longitude':'-117.2','place name':'La Jolla'}]}).locate('weather 92037')
    assert loc.approximate and 'ZIP centroid' in loc.description

def test_nws_times_expiration_and_source():
    now=datetime.now(timezone.utc)
    body={'updated':now.isoformat(),'features':[{'id':'https://api.weather.gov/alerts/test','properties':{'headline':'Flood warning','severity':'Severe','sent':(now-timedelta(hours=1)).isoformat(),'expires':(now-timedelta(minutes=1)).isoformat(),'areaDesc':'Local county'}}]}
    result=adapter(body).weather(LOCATION)
    assert result.state=='stale'
    assert result.alerts[0].source_time.tzinfo
    assert result.alerts[0].severity=='Severe'
    assert result.alerts[0].stale

def test_empty_not_safe_and_unavailable_distinct():
    empty=adapter({'features':[]}).weather(LOCATION)
    unavailable=adapter({},503).weather(LOCATION)
    assert empty.state=='available' and empty.alerts==[]
    assert 'does not establish safety' in empty.notice
    assert unavailable.state=='unavailable'

def test_usgs_query_radius_and_timestamps():
    captured=[]
    now=datetime.now(timezone.utc)
    def handler(req):
        captured.append(req)
        return httpx.Response(200,json={'metadata':{'generated':now.timestamp()*1000},'features':[{'properties':{'title':'M 3.2 test','mag':3.2,'time':now.timestamp()*1000,'url':'https://earthquake.usgs.gov/earthquakes/eventpage/test','place':'Near city'}}]})
    result=OfficialData(httpx.Client(transport=httpx.MockTransport(handler))).earthquakes(LOCATION)
    assert result.state=='available' and result.source_updated_at.tzinfo
    assert captured[0].url.params['maxradiuskm']=='100'
    assert result.alerts[0].source_time==now

def test_bad_source_timestamp_fails_closed():
    result=adapter({'features':[{'properties':{'sent':'yesterday'}}]}).weather(LOCATION)
    assert result.state=='unavailable'

def test_usgs_empty_204_has_no_invented_source_timestamp():
    def handler(req):
        assert req.url.params['nodata']=='204'
        return httpx.Response(204)
    result=OfficialData(httpx.Client(transport=httpx.MockTransport(handler))).earthquakes(LOCATION)
    assert result.state=='available' and result.alerts==[]
    assert result.source_updated_at is None
    assert 'no source timestamp' in result.notice


def test_future_nws_metadata_not_treated_as_fresh():
    result=adapter({'updated':(datetime.now(timezone.utc)+timedelta(days=1)).isoformat(),'features':[]}).weather(LOCATION)
    assert result.state=='stale'


def test_plural_hazard_requests_keep_the_official_lookup_intent():
    from relay.engine import plan
    for text in ['recent earthquakes near 92037','any quakes nearby?']:
        assert plan(text).next_action=='get_earthquakes'
    for text in ['any warnings near 92037','floods nearby?','tornadoes in my area']:
        assert plan(text).next_action=='get_weather_alerts'


def test_coordinates_allow_sentence_punctuation_without_partial_numbers():
    from relay.engine.data import OfficialData
    p=OfficialData().locate('Check 32.8328,-117.2713. Do these reports establish safety?')
    assert p.latitude==32.8328 and p.longitude==-117.2713
    assert OfficialData().locate('32.8328,-117.2713.5') is None
