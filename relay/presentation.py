"""Channel-specific wording and layout. Model output is content, never a template."""
from datetime import datetime, timezone
import json
import os
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import re


def clean_text(value):
    text=str(value or '').strip()
    text=re.sub(r'<think>.*?</think>', '', text, flags=re.S|re.I).strip()
    if '<think' in text.lower():
        raise ValueError('Unfinished model reasoning cannot be presented')
    text=re.sub(r'^```(?:json|text)?\s*|\s*```$', '', text, flags=re.I).strip()
    if text.startswith(('{','[')):
        try:
            data=json.loads(text)
            text=data['spoken_response'] if isinstance(data,dict) else ''
        except (ValueError,KeyError,TypeError):
            raise ValueError('Unrendered machine output cannot be presented')
    if not isinstance(text,str):raise ValueError('Response must be readable text')
    text=re.sub(r'\[([^\]]+)\]\(https?://[^\s)]+\)',r'\1',text)
    text=re.sub(r'(?m)^\s*(?:#{1,6}\s+|[-*•]\s+|\d+[.)]\s+)', '', text)
    text=text.replace('**','').replace('__','').replace('`','')
    text=re.sub(r'^(?:certainly|of course|absolutely|sure)[!,:.\s]+', '', text, flags=re.I)
    text=re.sub(r'\s+',' ',text).strip()
    if not text or any(x in text for x in ['"spoken_response":','"next_action":']):
        raise ValueError('Response has no presentable wording')
    return text


def spoken(value, word_budget=65):
    text=clean_text(value)
    text=re.sub(r'https?://\S+', '',text).strip()
    if len(text.split())<=word_budget:return text
    # A partial answer can lose a safety qualifier; never blindly cut the tail.
    return 'I could not make that answer clear enough. Please ask one specific question so I can help you check it.'



def readable_time(value):
    if not value:return 'Not provided by the source'
    try:
        dt=datetime.fromisoformat(str(value).replace('Z','+00:00'))
        if dt.tzinfo is None:raise ValueError('Missing timezone')
        try:zone=ZoneInfo(os.getenv('RELAY_DISPLAY_TIMEZONE','America/Los_Angeles'))
        except ZoneInfoNotFoundError:zone=timezone.utc
        return dt.astimezone(zone).strftime('%b %d, %Y at %H:%M %Z')
    except (ValueError,TypeError):return 'Not provided by the source'


def reference(summary, context=None, limit=1000):
    summary=spoken(summary,90)
    if not context:return 'RELAY update\n\n'+summary
    source=str(context.get('source','')).upper()
    title='Weather update' if source=='NWS' else 'Earthquake update'
    state=context.get('state')
    if state=='location_required':
        return title+'\n\nPlease share your ZIP code or latitude and longitude so I can check the right area.'
    if state=='unavailable':
        return title+'\n\nCurrent '+source+' information could not be verified. Try again shortly. This does not establish that conditions are safe.'
    alerts=context.get('alerts',[])
    sections=[title,summary]
    if state=='stale':sections.insert(1,'The source is out of date. Confirm current conditions with local authorities.')
    if alerts:
        details=[]
        for item in alerts[:2]:
            line=clean_text(item.get('title','Official report'))
            if source=='NWS' and item.get('severity') not in (None,'Unknown'):
                line+=' — '+str(item['severity'])
            details.append(line)
        sections.append('Official reports\n'+'\n'.join(details))
    else:
        sections.append('No matching reports were returned. This does not confirm safety.')
    loc=context.get('location') or {}
    if loc.get('approximate'):
        sections.append('Location is approximate; alerts may differ at your exact position.')
    updated=context.get('source_updated_at')
    # Earthquake occurrence time is not a feed-update timestamp.
    if not updated and source=='NWS' and alerts:updated=alerts[0].get('source_time')
    footer=source+' · Updated '+readable_time(updated)+'\nChecked '+readable_time(context.get('fetched_at'))
    urls=list(dict.fromkeys(str(a.get('source_url','')) for a in alerts if a.get('source_url')))
    if not urls:urls=['https://www.weather.gov/' if source=='NWS' else 'https://earthquake.usgs.gov/']
    source_lines=[footer,urls[0]]
    if len(urls)>1 and len(urls[0])+len(urls[1])<240:source_lines.append(urls[1])
    tail='\n\n'+'\n'.join(source_lines)
    room=max(0,limit-len(tail))
    body='\n\n'.join(sections)
    if len(body)>room:
        body=body[:max(0,room-1)].rsplit(' ',1)[0].rstrip(' ,;:')+'…'
    return body+tail
