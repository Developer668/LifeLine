"""Bound request buffering before parsing and private response defaults."""
from starlette.responses import JSONResponse

class RequestBoundary:
    def __init__(self, app, max_bytes=400_000):
        self.app, self.max_bytes = app, max_bytes

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        headers = dict(scope.get('headers', []))
        try:
            size = int(headers.get(b'content-length', b'0'))
        except ValueError:
            return await JSONResponse({'detail': 'Invalid content length'}, status_code=400)(scope, receive, send)
        if size < 0 or size > self.max_bytes:
            return await JSONResponse({'detail': 'Request too large'}, status_code=413)(scope, receive, send)
        body = bytearray()
        while True:
            message = await receive()
            if message['type'] == 'http.disconnect':
                return
            body.extend(message.get('body', b''))
            if len(body) > self.max_bytes:
                return await JSONResponse({'detail': 'Request too large'}, status_code=413)(scope, receive, send)
            if not message.get('more_body', False):
                break
        delivered = False
        async def replay():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {'type': 'http.request', 'body': bytes(body), 'more_body': False}
            return await receive()
        async def secure_send(message):
            if message['type'] == 'http.response.start':
                present = {k.lower() for k, _ in message.get('headers', [])}
                extra = [(b'cache-control', b'no-store'), (b'referrer-policy', b'no-referrer'),
                         (b'x-content-type-options', b'nosniff'), (b'x-frame-options', b'DENY')]
                message['headers'] = list(message.get('headers', [])) + [(k,v) for k,v in extra if k not in present]
            await send(message)
        await self.app(scope, replay, secure_send)
