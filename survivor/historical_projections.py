"""Preserve user-supplied historical forecasts without inventing missing fields."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path

from survivor.catalog import ROOT, load_aliases, resolve_name, rows

ARCHIVE = ROOT/'output/stats/preseason-2025-26.json'
FIELDS = {'pts_pg':'pts','reb_pg':'reb','ast_pg':'ast','stl_pg':'st','blk_pg':'blk','fg3m_pg':'3ptm'}
# Source-local spelling corrections; raw names and every original cell survive.
ALIASES = {'Lauri Markannen':'Lauri Markkanen','Bennidict Mathurin':'Bennedict Mathurin',
           'Dereck Lively':'Dereck Lively II','Evan Mobely':'Evan Mobley','Desmon Bane':'Desmond Bane',
           'Naz Ried':'Naz Reid','Donte Divinchenzo':'Donte DiVincenzo','Reed Shephard':'Reed Sheppard',
           'Kon Kneuppel':'Kon Knueppel','Ayo Donmusu':'Ayo Dosunmu','Royce Oneal':"Royce O'Neale",
           'N. Alexander-Walker':'Nickeil Alexander-Walker','Normal Powell':'Norman Powell',
           'VJ Edgecome':'VJ Edgecombe','Dennis Schroeder':'Dennis Schroder'}


def digest(payload):
    return hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def number(value):
    result = float(str(value).replace(',',''))
    if not math.isfinite(result) or result<0:
        raise ValueError('Historical forecasts must be finite and nonnegative.')
    return result


def parse_workbook(path):
    from openpyxl import load_workbook
    workbook = load_workbook(path,data_only=True,read_only=True)
    sheet = workbook.active
    source = iter(sheet.values)
    headers = next(source)
    required = {'Name','GP','Age','FGM/FGA','FTM/FTA','FG%','FT%',*FIELDS.values()}
    if not required.issubset(headers):
        raise ValueError('Historical workbook headers do not match the reviewed format.')
    result,seen = [],set()
    for row_index,values in enumerate(source,2):
        if not any(v is not None for v in values):
            continue
        original = dict(zip(headers,values))
        raw_name = original['Name']
        key,name,_ = resolve_name(ALIASES.get(raw_name,raw_name),load_aliases())
        if key in seen:
            raise ValueError('Duplicate historical forecast player: '+name)
        seen.add(key)
        gp = number(original['GP'])
        if not 0<gp<=82:
            raise ValueError('Historical forecast games must be positive and at most 82.')
        parsed = {'player_id':key,'player':name,'raw_name':raw_name,'source_row':row_index,
                  'games':gp,'age':number(original['Age']) if original['Age'] is not None else None,
                  'minutes_pg':None,'positions':None,'nba_team':None,'original':original}
        parsed.update({field:number(original[label])/gp for field,label in FIELDS.items()})
        for prefix,label in [('fg','FGM/FGA'),('ft','FTM/FTA')]:
            made,attempted = map(number,original[label].split('/'))
            if made>attempted:
                raise ValueError('Historical shooting makes exceed attempts.')
            if attempted and abs(made/attempted-number(original[prefix.upper()+'%']))>.00051:
                raise ValueError('Historical shooting percentages do not reconcile.')
            parsed[prefix+'m_pg'],parsed[prefix+'a_pg'] = made/gp,attempted/gp
        if parsed['fg3m_pg']>parsed['fgm_pg'] or abs(parsed['pts_pg']-(2*parsed['fgm_pg']+parsed['fg3m_pg']+parsed['ftm_pg']))>.02:
            raise ValueError('Historical scoring totals do not reconcile.')
        result.append(parsed)
    workbook.close()
    if not result:
        raise ValueError('Empty historical projection workbook.')
    return result


def create_archive(path, destination=ARCHIVE):
    source_hash = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    previous = load_archive(destination) if destination.exists() else None
    received = previous['received_at'] if previous and previous['source_sha256']==source_hash else datetime.now(timezone.utc).isoformat()
    payload = {'season':'2025-26','source_name':'User-supplied 2025-26 projections',
               'source_file':Path(path).name,'source_sha256':source_hash,
               'received_at':received,'published_at':None,'provider':None,
               'coverage':'partial_player_pool','season_basis':'Season identified by user; original publication date and provider unverified.',
               'notes':'200 listed players only. Missing names are not zero projections. No projected minutes, positions or teams supplied. Rank columns are retained but not used as forecasts or features.',
               'records':parse_workbook(path)}
    payload['dataset_id'] = digest(payload)
    destination.parent.mkdir(parents=True,exist_ok=True)
    destination.write_text(json.dumps(payload,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    return payload


def load_archive(path=ARCHIVE):
    payload = json.loads(path.read_text(encoding='utf-8'))
    if payload['dataset_id']!=digest({k:v for k,v in payload.items() if k!='dataset_id'}):
        raise ValueError('Historical projection archive checksum mismatch.')
    return payload


def sync_historical_projections(db):
    payload = load_archive()
    with db:
        db.executemany('INSERT INTO players VALUES (?,?,NULL,?) ON CONFLICT(player_id) DO NOTHING',
                       [(r['player_id'],r['player'],'User-supplied historical projection; explicit name matching') for r in payload['records']])
        db.execute('INSERT INTO historical_projection_sets VALUES (?,?,?) ON CONFLICT(dataset_id) DO NOTHING',
                   (payload['dataset_id'],payload['season'],json.dumps(payload,sort_keys=True,allow_nan=False)))
    return payload


def stored_archives(db):
    return [json.loads(r['payload_json']) for r in rows(db,'SELECT payload_json FROM historical_projection_sets ORDER BY season')]


def archive_metadata(archive):
    return {'dataset_id':archive['dataset_id'],'kind':'projection','season':archive['season'],
            'source_name':archive['source_name'],'source_url':None,'as_of_date':archive['received_at'][:10],
            'coverage':archive['coverage'],'notes':archive['season_basis']+' '+archive['notes'],
            'players':len(archive['records']),'date_basis':'received','published_at':archive['published_at']}


if __name__=='__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('workbook',type=Path)
    args = parser.parse_args()
    result = create_archive(args.workbook)
    print(f"Archived {len(result['records'])} projections for {result['season']}; provider/date unverified.")
