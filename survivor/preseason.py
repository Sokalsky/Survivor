"""Evidence gate for prices that cannot be paired with healthy prior-year rates.

This does not manufacture forecasts, infer health from eventual games played,
or assert that an unreviewed player was healthy. Exceptions are season-specific.
"""
from datetime import date
import json
from urllib.parse import urlparse

from survivor.catalog import ROOT


def load_preseason_evidence(path=None):
    payload = json.loads((path or ROOT/'config/preseason_availability.json').read_text(encoding='utf-8'))
    indexed = {}
    for record in payload['records']:
        key = record['season'],record['player_id']
        year = int(record['season'][:4])
        reported = date.fromisoformat(record['reported_at'])
        # Conservative pre-opening bound for the reviewed seasons. This is
        # deliberately NOT presented as the missing league auction date.
        latest = date(year,12,21) if year==2020 else date(year,10,15)
        if not date(year,1,1) <= reported <= latest:
            raise ValueError('Availability evidence must precede the season, not use its outcomes.')
        if key in indexed or not record['reason'] or not record['status']:
            raise ValueError('Duplicate or incomplete preseason evidence.')
        url = urlparse(record['source_url'])
        if url.scheme!='https' or not (url.hostname=='nba.com' or (url.hostname or '').endswith('.nba.com')):
            raise ValueError('Expected a dated NBA source for the availability review.')
        indexed[key] = record
    return payload,indexed
