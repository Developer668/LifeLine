"""Configure existing Bandwidth trial callbacks. Does not call, text or buy numbers."""
import argparse
import os
from pathlib import Path
import secrets
import xml.etree.ElementTree as ET
import httpx
from dotenv import load_dotenv, set_key


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    load_dotenv()
    required = ['BANDWIDTH_CLIENT_ID', 'BANDWIDTH_CLIENT_SECRET', 'BANDWIDTH_ACCOUNT_ID', 'PUBLIC_BASE_URL']
    if not all(os.getenv(k) for k in required):
        raise SystemExit('Set Bandwidth credentials, account ID and public callback address first.')
    base = os.environ['PUBLIC_BASE_URL'].rstrip('/')
    if not base.startswith('https://'):
        raise SystemExit('Callbacks require HTTPS.')
    account = os.environ['BANDWIDTH_ACCOUNT_ID']
    secret = os.getenv('BANDWIDTH_CALLBACK_SECRET') or secrets.token_urlsafe(32)
    with httpx.Client(timeout=20) as client:
        r = client.post('https://api.bandwidth.com/api/v1/oauth2/token',
                        auth=(os.environ['BANDWIDTH_CLIENT_ID'], os.environ['BANDWIDTH_CLIENT_SECRET']),
                        data={'grant_type': 'client_credentials'})
        if not r.is_success:
            raise SystemExit(f'Credential validation failed (HTTP {r.status_code}).')
        headers = {'Authorization': 'Bearer ' + r.json()['access_token']}
        url = 'https://api.bandwidth.com/api/accounts/' + account + '/applications'
        r = client.get(url, headers=headers);r.raise_for_status()
        applications = ET.fromstring(r.text).findall('.//Application')
        for service, env_key in [('Voice-V2', 'BANDWIDTH_VOICE_APPLICATION_ID'),
                                 ('Messaging-V2', 'BANDWIDTH_MESSAGING_APPLICATION_ID')]:
            app = next((x for x in applications if x.findtext('ApplicationId') == os.getenv(env_key)), None)
            if app is None:
                app = next((x for x in applications if x.findtext('ServiceType') == service), None)
            if app is None:
                raise SystemExit('No existing trial voice application found. No number was ordered.')
            ident = app.findtext('ApplicationId')
            patch = ET.Element('Application')
            fields = [('CallInitiatedCallbackUrl', base + '/bandwidth/voice'),
                                 ('CallInitiatedMethod', 'POST'), ('CallStatusCallbackUrl', base + '/bandwidth/status'),
                                 ('CallStatusMethod', 'POST')] if service == 'Voice-V2' else [
                                     ('InboundCallbackUrl', base + '/bandwidth/sms'),
                                     ('OutboundCallbackUrl', base + '/bandwidth/sms')]
            for field, value in fields:
                ET.SubElement(patch, field).text = value
            for name in (['CallbackCreds'] if service == 'Voice-V2' else ['InboundCallbackCreds', 'OutboundCallbackCreds']):
                creds = ET.SubElement(patch, name)
                ET.SubElement(creds, 'UserId').text = 'relay-hook'
                ET.SubElement(creds, 'Password').text = secret
            print(service + ' callback plan ready; no call or text will be sent.')
            if not args.apply:
                continue
            directory = Path('.runtime');directory.mkdir(exist_ok=True)
            backup = directory / ('bandwidth-' + service.lower() + '-before-relay.xml')
            if not backup.exists():
                backup.write_text(ET.tostring(app, encoding='unicode'));backup.chmod(0o600)
            # Save auth before the remote callback can be switched over.
            set_key('.env', 'BANDWIDTH_CALLBACK_SECRET', secret)
            set_key('.env', env_key, ident)
            Path('.env').chmod(0o600)
            result = client.patch(url + '/' + ident, headers={**headers, 'Content-Type': 'application/xml'},
                                  content=ET.tostring(patch))
            if not result.is_success:
                raise SystemExit(f'Callback update rejected (HTTP {result.status_code}). No call or message sent.')
            check = client.get(url + '/' + ident, headers=headers);check.raise_for_status()
            current = ET.fromstring(check.text)
            if any(current.findtext('.//' + field) != value for field, value in fields):
                raise SystemExit('Callback update could not be verified.')
            print(service + ' callbacks configured and read back successfully. No call or message sent.')

if __name__ == '__main__':
    main()
