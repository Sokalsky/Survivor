"""Collect reviewed, free preseason tables; never estimate missing forecasts.

Run with --collect to fetch publisher files and save numerical archives. PDF
extraction needs pypdf locally; the deployed app only reads bundled JSON.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
import re
from urllib.request import Request, urlopen

from survivor.catalog import ROOT, load_aliases, resolve_name
from survivor.historical_projections import bundled_archives, digest, load_archive, number
from survivor.stats_workbook import TableParser

RAZZBALL = {
    '2017-18': ('top-155-roto-projections', '2017-09-19'),
    '2018-19': ('fantasy-basketball-top-155-roto-projections', '2018-09-08'),
    '2019-20': ('fantasy-basketball-top-155-roto-projections-for-2019', '2019-08-30'),
    '2020-21': ('2020-2021-top-155-roto-projections-for-fantasy-basketball', '2020-12-02'),
    '2021-22': ('2021-22-top-155-roto-projections', '2021-09-17'),
    '2022-23': ('2022-23-top-155-roto-projections', '2022-09-12'),
    '2023-24': ('top-155-roto-projections-2023-24', '2023-09-19'),
}
NBC_PDF = 'https://nbcsports.brightspotcdn.com/03/10/b09f526442aab7ad4db7ba3584ed/rotoworld-2024-25-fantasy-basketball-kit.pdf'
NBC_PAGE = 'https://www.nbcsports.com/draftkit'
PDF_COLUMNS = 'Year Team G MIN PTS FGM FGA FG% FTM FTA FT% 3PM REB AST STL BLK TO Y!'.split()
TEAMS = ('Atlanta Hawks|Boston Celtics|Brooklyn Nets|Charlotte Hornets|Chicago Bulls|Cleveland Cavaliers|'
         'Dallas Mavericks|Denver Nuggets|Detroit Pistons|Golden State Warriors|Houston Rockets|Indiana Pacers|'
         'Los Angeles Clippers|Los Angeles Lakers|Memphis Grizzlies|Miami Heat|Milwaukee Bucks|Minnesota Timberwolves|'
         'New Orleans Pelicans|New York Knicks|Oklahoma City Thunder|Orlando Magic|Philadelphia 76ers|Phoenix Suns|'
         'Portland Trail Blazers|Sacramento Kings|San Antonio Spurs|Toronto Raptors|Utah Jazz|Washington Wizards')
NAME_ALIASES = {'Robert Dillingham':'Rob Dillingham', 'Gary Trent':'Gary Trent Jr',
                'Kenyon Martin Jr.':'KJ Martin', 'Kenyon Martin Jr':'KJ Martin',
                'Carlton Carrington':'Bub Carrington','Chris Dunn':'Kris Dunn',
                'Danilo Galinari':'Danilo Gallinari','Danuel House':'Danuel House Jr',
                'Dereck Lively':'Dereck Lively II','Derrick Jones':'Derrick Jones Jr',
                'Frank Kaminski':'Frank Kaminsky','GG Jackson':'GG Jackson II','Gary Payton':'Gary Payton II',
                'Herb Jones':'Herbert Jones','Jabari Smith':'Jabari Smith Jr','Jaime Jaquez':'Jaime Jaquez Jr',
                'Jaren Jackson':'Jaren Jackson Jr','Kelly Oubre':'Kelly Oubre Jr',
                'Kentavious Caldwell':'Kentavious Caldwell-Pope','Marvin Bagley':'Marvin Bagley III',
                'Michael Porter':'Michael Porter Jr','Robert Willliams':'Robert Williams III',
                'Rondae Hollis':'Rondae Hollis-Jefferson','Shai Gilgeous':'Shai Gilgeous-Alexander',
                'Spencer Dinwiddle':'Spencer Dinwiddie','Tim Hardaway':'Tim Hardaway Jr',
                'Trey Murphy':'Trey Murphy III','Tyrese Halibutron':'Tyrese Haliburton',
                'Vince Williams':'Vince Williams Jr','Wendell Carter':'Wendell Carter Jr',
                'Xavier Tillman':'Xavier Tillman Sr'}


def identity(raw):
    name = ' '.join(raw.split())
    key, canonical, _ = resolve_name(NAME_ALIASES.get(name, name), load_aliases())
    return {'player_id':key, 'player':canonical, 'raw_name':name,
            'games':None, 'minutes_pg':None, 'positions':None, 'nba_team':None, 'age':None}


def validate_record(row):
    for field in ('pts_pg','reb_pg','ast_pg','stl_pg','blk_pg','fg3m_pg','fgm_pg','fga_pg','ftm_pg','fta_pg'):
        number(row[field])
    if row['fgm_pg'] > row['fga_pg'] or row['ftm_pg'] > row['fta_pg'] or row['fg3m_pg'] > row['fgm_pg']:
        raise ValueError('Shooting makes exceed attempts: '+row['player'])
    if row['games'] is not None and not 0 < row['games'] <= 82:
        raise ValueError('Invalid published games: '+row['player'])
    # Four independently rounded per-game terms (2*FGM + 3PM + FTM vs
    # PTS) can differ by 0.3 in the PDF. Larger errors are not rounding.
    if abs(row.get('scoring_rounding_difference',0)) > .301:
        raise ValueError('Published scoring components do not reconcile: '+row['player'])


def unique(records):
    keys = [r['player_id'] for r in records]
    if len(keys) != len(set(keys)):
        raise ValueError('Duplicate published player identities.')
    for row in records:
        validate_record(row)
    return records


def parse_razzball(html, season, rejected=None):
    """Fixed dated article tables, NOT the mutable /projections-preseason page."""
    expected_date = RAZZBALL[season][1]
    dates = re.findall(r'"datePublished":"([^"]+)', html)
    if not dates or any(d[:10] != expected_date for d in dates):
        raise ValueError('Publisher date does not match the reviewed preseason article.')
    tables = re.findall(r'<table\b.*?</table>', html, re.S)
    if len(tables) != 1:
        raise ValueError('Expected one projection table in the dated article.')
    parser = TableParser('archive')
    parser.feed(re.sub(r'<table[^>]*>', '<table id="archive">', tables[0], count=1))
    headers = [c['text'] for c in parser.records[0]]
    # The reviewed articles use different names for the same fixed columns.
    allowed = [('#','Rank',''),('Name','NAME'),('Value','VALUE','Val'),('p/g','pts','PTS'),
               ('3/g','3pt','3PT'),('r/g','reb','REB'),('a/g','ast','AST'),('s/g','stl','STL'),
               ('b/g','blk','BLK'),('fg%','FG%'),('fga/g','fga','FGA'),('ft%','FT%'),
               ('fta/g','fta','FTA'),('to/g','to','tos','TOV')]
    if len(headers) != 14 or any(h not in options for h,options in zip(headers,allowed)):
        raise ValueError('Unrecognized preseason table columns.')
    records = []
    rejected = [] if rejected is None else rejected
    for index, cells in enumerate(parser.records[1:],2):
        values = [c['text'] for c in cells]
        if len(values) != 14:
            raise ValueError('Incomplete forecast row.')
        row = identity(values[1])
        row.update(source_row=index, original=dict(zip(headers,values)))
        divisor = 1 if season in ('2017-18','2018-19') else 100
        if any(not 0 <= number(values[pos].rstrip('%'))/divisor <= 1 for pos in (9,11)):
            rejected.append({**row,'reason':'Published percentage is outside 0–100%; no guessed correction.'})
            continue
        row.update(zip(('pts_pg','fg3m_pg','reb_pg','ast_pg','stl_pg','blk_pg'),map(number,values[3:9])))
        for prefix, pos in (('fg',9),('ft',11)):
            percentage = number(values[pos].rstrip('%')) / divisor
            if not 0 <= percentage <= 1:
                raise ValueError('Invalid percentage scale.')
            attempts = number(values[pos+1])
            row[prefix+'a_pg'] = attempts
            row[prefix+'m_pg'] = percentage * attempts
        # Independently rounded points/makes need not be exactly identical.
        row['scoring_rounding_difference'] = row['pts_pg']-(2*row['fgm_pg']+row['fg3m_pg']+row['ftm_pg'])
        try:
            validate_record(row)
        except ValueError as error:
            rejected.append({**row,'reason':str(error)+'; no guessed correction.'})
        else:
            records.append(row)
    if len(parser.records)-1 != (156 if season=='2023-24' else 155):
        raise ValueError('Dated projection table coverage changed; review before import.')
    return unique(records)


def parse_rotoworld(pages, rejected=None):
    records = []
    rejected = [] if rejected is None else rejected
    current = None
    positions = {'POINT GUARDS':'PG','SHOOTING GUARDS':'SG','SMALL FORWARD':'SF','POWER FORWARD':'PF','CENTERS':'C'}
    for page_number, page in enumerate(pages,1):
        lines = page.splitlines()
        position = next((value for label,value in positions.items() if label in lines),None)
        for i, line in enumerate(lines):
            if re.match(r'[\d\s]*Age:', line):
                name = next((m for header in reversed(lines[max(0,i-3):i])
                             if (m:=re.fullmatch(r'\s*\d*\s*(.+?)\s+('+TEAMS+r')\s*',header))),None)
                if not name:
                    raise ValueError('Unrecognized PDF name on page '+str(page_number)+': '+lines[i-1])
                if current is not None:
                    raise ValueError('PDF profile header has no matched projection: '+current['player'])
                current = identity(name[1])
                current.update(age=number(re.search(r'Age:\s*(\d+)',line)[1]),
                               positions=position, source_page=page_number)
            if not line.startswith('PROJ '):
                continue
            # PDF kerning can produce "7 .1"; remove only that decimal gap.
            values = re.sub(r'(?<=\d)\s+\.(?=\d)', '.', line).split()
            if current is None or len(values) != len(PDF_COLUMNS):
                raise ValueError('Unmatched or incomplete PDF projection row on page '+str(page_number)+': '+line)
            original = dict(zip(PDF_COLUMNS,values))
            current.update(original=original, source_page=page_number, nba_team=original['Team'], games=number(original['G']))
            mapping = {'minutes_pg':'MIN','pts_pg':'PTS','fgm_pg':'FGM','fga_pg':'FGA','ftm_pg':'FTM',
                       'fta_pg':'FTA','fg3m_pg':'3PM','reb_pg':'REB','ast_pg':'AST','stl_pg':'STL','blk_pg':'BLK'}
            current.update({field:number(original[column]) for field,column in mapping.items()})
            current['scoring_rounding_difference'] = current['pts_pg']-(2*current['fgm_pg']+current['fg3m_pg']+current['ftm_pg'])
            try:
                validate_record(current)
            except ValueError as error:
                rejected.append({**current,'reason':str(error)+'; no guessed correction.'})
            else:
                records.append(current)
            current = None
    if len(records)+len(rejected) != 286 or current is not None:
        raise ValueError('PDF projection count changed; review before import.')
    return unique(records)


def pdf_columns(reader):
    """Read each page's left column before its right, carrying split profiles.

    Content-stream order differs from visual order on several publisher pages.
    Sorting by column, baseline and horizontal position keeps names with tables.
    """
    pages = []
    for page in reader.pages:
        grouped = {}
        def visitor(text, cm, tm, font, size):
            if not text.strip():
                return
            x, y = tm[4]*cm[0]+tm[5]*cm[2]+cm[4], tm[4]*cm[1]+tm[5]*cm[3]+cm[5]
            grouped.setdefault((int(x >= 306), -round(y)), []).append((x,text.strip()))
        page.extract_text(visitor_text=visitor)
        pages.append('\n'.join(' '.join(t for _,t in sorted(grouped[key])) for key in sorted(grouped)))
    return pages


def save_archive(season, raw, records, provider, source_url, published_at, notes, **extra):
    path = ROOT/f'output/stats/preseason-{season}.json'
    source_hash = hashlib.sha256(raw).hexdigest()
    previous = load_archive(path) if path.exists() else None
    received = previous['received_at'] if previous and previous['source_sha256']==source_hash else datetime.now(timezone.utc).isoformat()
    payload = {'season':season, 'source_name':provider+' preseason projections', 'provider':provider,
               'source_url':source_url, 'source_sha256':source_hash, 'received_at':received,
               'published_at':published_at, 'coverage':'partial_player_pool',
               'season_basis':'Dated publisher preseason article. Exact league auction dates were not recorded; this is not a verified auction-day snapshot.',
               'notes':notes, **extra, 'records':records}
    payload['dataset_id'] = digest(payload)
    path.write_text(json.dumps(payload,indent=2,ensure_ascii=False,allow_nan=False)+'\n',encoding='utf-8')
    return payload


def fetch(url, filename):
    path = ROOT/'output/stats/cache'/filename
    if not path.exists():
        with urlopen(Request(url,headers={'User-Agent':'Mozilla/5.0'}),timeout=45) as response:
            raw = response.read()
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes(raw)
    return path.read_bytes()


def collect():
    for season,(slug,published) in RAZZBALL.items():
        url = 'https://basketball.razzball.com/'+slug+'/'
        raw = fetch(url,'razzball-'+season+'.html')
        rejected = []
        records = parse_razzball(raw.decode('utf-8'),season,rejected)
        result = save_archive(season,raw,records,'Razzball / Kostas',url,published,
            'Per-game rates and shooting attempts as published. Makes calculated only as published percentage times published attempts. No GP, minutes, positions or teams supplied; missing players are unknown. Publisher rankings and turnover projections are retained but not used for eight-category values.', rejected_records=rejected)
        print(season,len(result['records']),result['dataset_id'][:12])
    from pypdf import PdfReader
    raw = fetch(NBC_PDF,'rotoworld-2024-25.pdf')
    pages = pdf_columns(PdfReader(io.BytesIO(raw)))
    rejected = []
    records = parse_rotoworld(pages,rejected)
    result = save_archive('2024-25',raw,records,'NBC Sports / Rotoworld',NBC_PDF,'2024-10-09',
        'Only PROJ rows from player profiles; historical rows and publisher rankings are not forecasts. Per-game makes/attempts are independently rounded by the publisher. Page number and original numerical row retained. Positions are profile sections, not league eligibility.',landing_url=NBC_PAGE,rejected_records=rejected)
    print('2024-25',len(result['records']),result['dataset_id'][:12])


def export_workbook():
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    from openpyxl.utils import get_column_letter
    book = Workbook()
    sources = book.active
    sources.title = 'Sources'
    sources.append(['Season','Source','Published date','Received date','Usable players','Rejected rows','Source URL','Dataset ID','Notes'])
    rejected = book.create_sheet('Rejected rows')
    rejected.append(['Season','Player','Reason','Original values','Source URL'])
    fields = ['player','games','minutes_pg','positions','nba_team','pts_pg','reb_pg','ast_pg',
              'stl_pg','blk_pg','fg3m_pg','fgm_pg','fga_pg','ftm_pg','fta_pg','source_row','source_page','raw_name']
    for archive in bundled_archives():
        sources.append([archive['season'],archive['source_name'],archive['published_at'],archive['received_at'],
                        len(archive['records']),len(archive.get('rejected_records',[])),archive.get('source_url'),
                        archive['dataset_id'],archive['season_basis']+' '+archive['notes']])
        sheet = book.create_sheet(archive['season'])
        sheet.append(fields+['original_values'])
        for row in archive['records']:
            sheet.append([row.get(key) for key in fields]+[json.dumps(row['original'],ensure_ascii=False)])
        for row in archive.get('rejected_records',[]):
            rejected.append([archive['season'],row['player'],row['reason'],json.dumps(row['original'],ensure_ascii=False),archive.get('source_url')])
    for sheet in book:
        sheet.freeze_panes = 'B2'
        sheet.auto_filter.ref = sheet.dimensions
        for cell in sheet[1]:
            cell.font = Font(bold=True,color='FFFFFF')
            cell.fill = PatternFill('solid',fgColor='19352A')
        for i in range(1,sheet.max_column+1):
            sheet.column_dimensions[get_column_letter(i)].width = 24 if i==1 else 18
        for row in sheet.iter_rows(min_row=2):
            for cell in row:
                if isinstance(cell.value,(int,float)):
                    cell.number_format = '0.00'
    path = ROOT/'output/stats/historical-projections.xlsx'
    book.save(path)
    print('Exported',path)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--collect',action='store_true')
    parser.add_argument('--export',action='store_true')
    args = parser.parse_args()
    if not (args.collect or args.export):
        parser.error('Choose --collect and/or --export.')
    if args.collect:
        collect()
    if args.export:
        export_workbook()
