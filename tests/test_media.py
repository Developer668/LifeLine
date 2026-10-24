import base64
import io
from urllib.parse import urlsplit
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from relay import store
from relay.app import app
from relay.media import issue_camera_link

@pytest.fixture
def media(monkeypatch, tmp_path):
    monkeypatch.setenv('RELAY_DB', str(tmp_path/'media.db'))
    store.initialize()
    store.upsert_incident('visual-one', channel='demo')
    store.upsert_incident('visual-two', channel='demo')
    return TestClient(app)

def link_path(incident='visual-one'):
    u=urlsplit(issue_camera_link(incident))
    return u.path, '?'+u.query

def jpeg():
    buf=io.BytesIO()
    Image.new('RGB',(32,24),'green').save(buf,format='JPEG')
    return base64.b64encode(buf.getvalue()).decode()

def test_capability_expiry_revocation_and_scope(media):
    path, query=link_path()
    r=media.get(path+query)
    assert r.status_code==200
    assert r.headers['referrer-policy']=='no-referrer'
    assert media.get(path+query.replace('token=', 'token=forged')).status_code==403
    assert media.get(path.replace('visual-one','visual-two')+query).status_code==403
    assert media.post(path+'/stop'+query).status_code==200
    assert media.get(path+query).status_code==403
    assert media.post(path+'/frame'+query,json={'image_base64':jpeg()}).status_code==403

def test_camera_validates_decode_limits_and_observation_isolation(media):
    path,query=link_path()
    assert media.post(path+'/frame'+query,json={'image_base64':base64.b64encode(b'\xff\xd8not-an-image').decode()}).status_code==422
    r=media.post(path+'/frame'+query,json={'image_base64':jpeg()})
    assert r.status_code==200
    job_id=r.json()['job_id']
    assert media.post(path+'/frame'+query,json={'image_base64':jpeg()}).status_code==429
    assert media.get(path+'/jobs/'+job_id+query).json()['state']=='queued'
    other,otherquery=link_path('visual-two')
    assert media.get(other+'/jobs/'+job_id+otherquery).status_code==404
    job=store.claim()
    store.finish(job['id'],job['lease'],'[SIMULATED] A green frame.')
    assert media.get(path+'/jobs/'+job_id+query).json()['state']=='complete'
    with store.connect() as c:
        row=c.execute('SELECT payload FROM jobs WHERE id=?',(job_id,)).fetchone()
    assert 'image_base64' not in row['payload']
