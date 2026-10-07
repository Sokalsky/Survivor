"""Read the free ESPN full-season projection feed used by its public web page."""
from datetime import datetime, timezone
import hashlib
import json
from urllib.request import Request, urlopen

from survivor.stats_workbook import numeric

SOURCE_NAME = 'ESPN published projections'
SOURCE_URL = 'https://fantasy.espn.com/basketball/players/projections'
SOURCE_NOTE = ('ESPN full-season projected totals divided by projected games; makes and attempts come from ESPN. '
               'ESPN labels seasons by ending year (2027 = 2026-27). Only source 1, season split 0, scoring period 0 is used. '
               'Absent counting-stat keys inside a nonempty ESPN projection are sparse-encoded zeroes; '
               'players without a positive-games projection are excluded. No forecasts are blended or estimated. '
               'Snapshot date is retrieval time, not a provider publication date.')
TOTAL_FIELDS = {'minutes_pg':'40','pts_pg':'0','reb_pg':'6','ast_pg':'3','stl_pg':'2',
                'blk_pg':'1','fg3m_pg':'17','fgm_pg':'13','fga_pg':'14','ftm_pg':'15','fta_pg':'16'}
AUDIT_HEADERS = ['source_player_id','source_url','source_sha256','retrieved_at','source_stat_id',
                 'sparse_zero_stat_ids','fg_pct','ft_pct']+[k.removesuffix('_pg')+'_total' for k in TOTAL_FIELDS]


def fetch(url, cache, key, filters=None, *, refresh=False):
    cache.mkdir(parents=True,exist_ok=True)
    path, meta_path = cache/(key+'.json'),cache/(key+'-meta.json')
    if path.exists() and meta_path.exists() and not refresh:
        body = path.read_bytes()
        meta = json.loads(meta_path.read_text(encoding='utf-8'))
        if meta['url']!=url or meta.get('request_filter')!=filters or meta['sha256']!=hashlib.sha256(body).hexdigest():
            raise ValueError('ESPN source cache integrity mismatch.')
    else:
        headers = {'User-Agent':'SurvivorStats/1.0 (personal fantasy basketball research)'}
        if filters:
            headers['X-Fantasy-Filter'] = json.dumps(filters)
        with urlopen(Request(url,headers=headers),timeout=30) as response:
            body = response.read()
        json.loads(body)  # Never cache an HTML error as projection data.
        meta = {'url':url,'request_filter':filters,'sha256':hashlib.sha256(body).hexdigest(),
                'retrieved_at':datetime.now(timezone.utc).isoformat()}
        path.write_bytes(body)
        meta_path.write_text(json.dumps(meta,indent=2)+'\n',encoding='utf-8')
    return json.loads(body),meta


def parse_espn(payload, season, teams):
    end = int(season[:4])+1
    result, seen = [],set()
    for item in payload['players']:
        p = item['player']
        key = str(p['id'])
        if key in seen:
            raise ValueError('Duplicate ESPN player ID in a page.')
        seen.add(key)
        matches = [s for s in p.get('stats',[]) if s.get('seasonId')==end and s.get('statSourceId')==1
                   and s.get('statSplitTypeId')==0 and s.get('scoringPeriodId')==0 and s.get('externalId')==str(end)]
        if not matches:
            continue
        if len(matches)!=1:
            raise ValueError('Multiple ESPN full-season projections for one player.')
        source = matches[0]
        s = source['stats']
        if not s:  # ESPN also returns empty projection objects for unprojected players.
            continue
        if '42' not in s or '40' not in s:
            raise ValueError('Nonempty ESPN projection lacks games/minutes.')
        gp = numeric(str(s['42']),'games')
        if gp==0:
            continue
        if gp>82:
            raise ValueError('ESPN projected games exceed a full NBA season.')
        row = {'player':p['fullName'],'source_player_id':key,'nba_team':teams.get(p['proTeamId'],''),
               'positions':','.join(name for slot,name in [(0,'PG'),(1,'SG'),(2,'SF'),(3,'PF'),(4,'C')] if slot in p['eligibleSlots']),
               'games':gp,'source_stat_id':source['id'],
               'sparse_zero_stat_ids':','.join(sorted(set(TOTAL_FIELDS.values())-set(s)))}
        for field, stat in TOTAL_FIELDS.items():
            total = numeric(str(s.get(stat,0)),field)
            row[field.removesuffix('_pg')+'_total'] = total
            row[field] = total/gp
        if not row['nba_team'] or not row['positions'] or not 0<row['minutes_pg']<=60:
            raise ValueError('ESPN team, position or minutes missing/invalid.')
        if row['fgm_pg']>row['fga_pg'] or row['ftm_pg']>row['fta_pg'] or row['fg3m_pg']>row['fgm_pg']:
            raise ValueError('ESPN shooting makes exceed attempts.')
        if abs(row['pts_pg']-(2*row['fgm_pg']+row['fg3m_pg']+row['ftm_pg']))>.06:
            raise ValueError('ESPN points do not reconcile with shooting totals.')
        row['fg_pct'] = s.get('19',0)
        row['ft_pct'] = s.get('20',0)
        for label,makes,attempts in [('fg',row['fgm_pg'],row['fga_pg']),('ft',row['ftm_pg'],row['fta_pg'])]:
            # Counts are rounded to whole-season integers and percentages to .001.
            if attempts and abs((makes-attempts*row[label+'_pct'])*gp)>1+.0005*attempts*gp:
                raise ValueError('ESPN shooting percentage does not match makes/attempts.')
        result.append(row)
    return result


def collect_projections(output_dir, season, *, refresh=False):
    end = int(season[:4])+1
    root = f'https://lm-api-reads.fantasy.espn.com/apis/v3/games/fba/seasons/{end}'
    cache = output_dir/'cache'
    game,meta = fetch(root+'?view=proTeamSchedules_wl',cache,f'espn-teams-{season}',refresh=refresh)
    teams = {t['id']:t['abbrev'] for t in game['settings']['proTeams']}
    pages = [{**meta,'group':'teams','players':0}]
    records,seen = [],set()
    for offset in range(0,10000,1000):
        filters = {'players':{'filterStatsForExternalIds':{'value':[end]},
                              'filterStatsForSourceIds':{'value':[1]},'useFullProjectionTable':{'value':True},
                              'sortDraftRanks':{'sortPriority':1,'sortAsc':True,'value':'ROTO'},
                              'limit':1000,'offset':offset}}
        url = root+'/segments/0/leaguedefaults/1?view=kona_player_info'
        payload,metadata = fetch(url,cache,f'espn-projections-{season}-{offset}',filters,refresh=refresh)
        parsed = parse_espn(payload,season,teams)
        for row in parsed:
            if row['source_player_id'] in seen:
                raise ValueError('ESPN pagination returned duplicate players.')
            seen.add(row['source_player_id'])
            row.update(source_url=url,source_sha256=metadata['sha256'],retrieved_at=metadata['retrieved_at'])
        records.extend(parsed)
        pages.append({**metadata,'group':str(offset),'players':len(parsed),'response_players':len(payload['players'])})
        if len(payload['players'])<1000:
            break
    else:
        raise ValueError('ESPN pagination did not finish; refusing truncated coverage.')
    if len(records)<225:
        raise ValueError('ESPN projections do not cover the league roster depth.')
    from survivor.catalog import load_aliases, resolve_name
    aliases = load_aliases()
    ids = [resolve_name(r['player'],aliases)[0] for r in records]
    if len(set(ids))!=len(ids):
        raise ValueError('ESPN names map to duplicate local player IDs.')
    return sorted(records,key=lambda r:r['player']),pages
