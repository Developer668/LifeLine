"""Revocable, expiring single-frame camera capabilities. No continuous capture."""
import base64
import hashlib
import io
import json
import os
import secrets
import time
from pathlib import Path
from urllib.parse import urlencode
from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, Field
from PIL import Image, UnidentifiedImageError
from . import store


def initialize():
    with store.connect() as c:
        c.execute('''CREATE TABLE IF NOT EXISTS camera_sessions (
          token_hash TEXT PRIMARY KEY, incident TEXT NOT NULL, expires REAL NOT NULL,
          revoked INTEGER NOT NULL DEFAULT 0, last_frame REAL NOT NULL DEFAULT 0)''')


def issue_camera_link(incident):
    if not store.incident_info(incident):
        raise HTTPException(404, 'Incident not found')
    initialize()
    token = secrets.token_urlsafe(32)
    expires = int(time.time()) + 600
    with store.connect() as c:
        c.execute('INSERT INTO camera_sessions(token_hash,incident,expires) VALUES(?,?,?)',
                  (hashlib.sha256(token.encode()).hexdigest(), incident, expires))
    base = os.getenv('PUBLIC_BASE_URL', 'http://127.0.0.1:8000').rstrip('/')
    return f'{base}/camera/{incident}?' + urlencode({'expires': expires, 'token': token})


def authorize(incident, expires, token):
    initialize()
    digest = hashlib.sha256(token.encode()).hexdigest()
    with store.connect() as c:
        row = c.execute('SELECT * FROM camera_sessions WHERE token_hash=? AND incident=?',
                        (digest, incident)).fetchone()
    if not row or row['revoked'] or row['expires'] <= time.time() or row['expires'] != expires:
        raise HTTPException(403, 'Camera link expired or stopped. Request a new link.')
    return digest


class Frame(BaseModel):
    image_base64: str = Field(min_length=12, max_length=300000)


def install_media(app):
    # Replace the baseline prototype handlers; preserve its public paths.
    app.router.routes[:] = [r for r in app.router.routes if not getattr(r, 'path', '').startswith('/camera/')
                           and getattr(r, 'path', '') != '/demo/incidents/{incident}/camera-link']

    @app.post('/demo/incidents/{incident}/camera-link')
    def link(request: Request, incident: str):
        from .app import require_token
        require_token(request, 'DEMO_TOKEN')
        url = issue_camera_link(incident)
        return {'url': url, 'expires_in': 600, 'requires_user_camera_permission': True}

    @app.get('/camera/{incident}', response_class=HTMLResponse)
    def page(incident: str, expires: int, token: str):
        authorize(incident, expires, token)
        body = Path(__file__).with_name('camera.html').read_text()
        return HTMLResponse(body, headers={'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer',
            'Permissions-Policy': 'camera=(self), microphone=()',
            'Content-Security-Policy': "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; media-src blob:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'"})

    @app.post('/camera/{incident}/frame')
    def frame(incident: str, expires: int, token: str, body: Frame):
        digest = authorize(incident, expires, token)
        try:
            raw = base64.b64decode(body.image_base64, validate=True)
            if len(raw) > 200000:
                raise ValueError('oversized')
            with Image.open(io.BytesIO(raw)) as im:
                if im.format != 'JPEG' or im.width * im.height > 4_000_000:
                    raise ValueError('invalid format or dimensions')
                im.verify()
        except (ValueError, OSError, UnidentifiedImageError, Image.DecompressionBombError):
            raise HTTPException(422, 'A valid JPEG under 200KB and 4 megapixels is required')
        now = time.time()
        with store.connect() as c:
            changed = c.execute('''UPDATE camera_sessions SET last_frame=? WHERE token_hash=?
                AND revoked=0 AND expires>? AND last_frame<?''', (now, digest, now, now-2)).rowcount
        if not changed:
            raise HTTPException(429, 'Wait two seconds between frames')
        job = store.enqueue(incident, 'message', {'text': 'Describe visible details cautiously. Never diagnose or declare a scene safe.',
            'channel': 'camera', 'sms_consent': False, 'image_base64': body.image_base64})
        return {'state': 'queued', 'job_id': job, 'notice': 'One frame queued; this is not a live video call'}

    @app.get('/camera/{incident}/jobs/{job_id}')
    def observation(incident: str, job_id: str, expires: int, token: str):
        authorize(incident, expires, token)
        with store.connect() as c:
            row = c.execute('SELECT state,result FROM jobs WHERE id=? AND incident=? AND json_extract(payload,\'$.channel\')=\'camera\'',
                            (job_id, incident)).fetchone()
        if not row:
            raise HTTPException(404, 'Observation not found')
        return dict(row)

    @app.post('/camera/{incident}/stop')
    def stop(incident: str, expires: int, token: str):
        digest = authorize(incident, expires, token)
        with store.connect() as c:
            c.execute('UPDATE camera_sessions SET revoked=1 WHERE token_hash=?', (digest,))
        store.add_event(incident, 'camera_stopped', {'revoked': True})
        return {'state': 'stopped'}
