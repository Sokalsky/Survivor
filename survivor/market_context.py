"""Recorded season ages for price comparisons; never generates NBA projections."""
from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path

from survivor.catalog import ROOT, load_aliases, resolve_name, season_name

CONTEXT = ROOT/'output/stats/market-context.json'
COLUMNS = ['season','player_id','source_player_id','age']


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def write_context(datasets, sources, path=CONTEXT):
    aliases = load_aliases()
    records = []
    for season,players in datasets:
        for p in players:
            records.append([season,resolve_name(p['player'],aliases)[0],p['source_player_id'],p['age']])
    source_map = {s['season']:{k:s[k] for k in ('url','sha256','retrieved_at')} for s in sources if s['kind']=='actual'}
    data = {'version':1,'columns':COLUMNS,'sources':source_map,'records':sorted(records)}
    payload = {**data,'sha256':digest(data)}
    MarketContext(payload)  # Validate the entire file before replacing an export.
    header = json.dumps({k:v for k,v in payload.items() if k!='records'},indent=2,allow_nan=False)[:-2]
    lines = ',\n'.join('    '+json.dumps(record,separators=(',',':'),allow_nan=False) for record in data['records'])
    path.write_text(header+',\n  "records": [\n'+lines+'\n  ]\n}\n',encoding='utf-8')
    return payload


class MarketContext:
    def __init__(self, payload):
        data = {k:v for k,v in payload.items() if k!='sha256'}
        if data.get('version')!=1 or data.get('columns')!=COLUMNS or digest(data)!=payload.get('sha256'):
            raise ValueError('Market age context version or integrity check failed.')
        self.by_player = defaultdict(list)
        seen = set()
        for season,key,source_id,age in data['records']:
            if season!=season_name(int(season[:4])) or season not in data['sources']:
                raise ValueError('Market age context lacks a matching season source.')
            if not key or not source_id or (season,key) in seen or not isinstance(age,(int,float)) or not 15<=age<=50:
                raise ValueError('Invalid or duplicate recorded player age.')
            seen.add((season,key))
            self.by_player[key].append({'season':season,'source_player_id':source_id,'age':age})
        for records in self.by_player.values():
            records.sort(key=lambda r:r['season'],reverse=True)
        self.sources = data['sources']
        self.metadata = {'sha256':payload['sha256'],'rows':len(seen),'sources':self.sources,
                         'basis':'Basketball Reference season ages. Target-season age advances the latest earlier recorded season age by elapsed seasons; no birthdays or rookie ages are guessed.'}

    def before(self, player_id, season):
        record = next((r for r in self.by_player.get(player_id,[]) if r['season']<season),None)
        if record is None:
            return None
        return {**record,'target_season_age':record['age']+int(season[:4])-int(record['season'][:4]),
                'source_url':self.sources[record['season']]['url']}


def load_context(path=CONTEXT):
    return MarketContext(json.loads(path.read_text(encoding='utf-8')))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stats-dir',type=Path,default=CONTEXT.parent)
    args = parser.parse_args()
    # Reuse verified source HTML; this command has no network or DB connection.
    from survivor.stats_workbook import parse_history
    manifest = json.loads((args.stats_dir/'manifest.json').read_text(encoding='utf-8'))
    sources = [s for s in manifest['sources'] if s['kind']=='actual']
    datasets = []
    for source in sources:
        html = (args.stats_dir/'cache'/('actuals-'+source['season']+'.html')).read_bytes()
        if hashlib.sha256(html).hexdigest()!=source['sha256']:
            raise ValueError('Historical HTML does not match the recorded source hash.')
        records = parse_history(html.decode('utf-8'),source['season'])
        if len(records)!=source['players']:
            raise ValueError('Recorded age coverage does not match historical statistics.')
        datasets.append((source['season'],records))
    result = write_context(datasets,sources,args.stats_dir/'market-context.json')
    print(f'Recorded market ages: {len(result["records"])} rows / {len(sources)} seasons / {result["sha256"]}')


if __name__=='__main__':
    main()
