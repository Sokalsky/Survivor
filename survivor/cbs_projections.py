"""Read published CBS season projections without estimating missing statistics."""
from __future__ import annotations

from collections import defaultdict
import re
from urllib.parse import parse_qs, urlsplit

from survivor.catalog import STAT_HEADERS, load_aliases, resolve_name
from survivor.stats_workbook import TableParser, download, numeric

SOURCE_NAME = 'CBS Sports published projections'
SOURCE_URL = 'https://www.cbssports.com/fantasy/basketball/projections/'
SOURCE_NOTE = ('Published CBS season totals divided by published projected games. '
               'FGM, FGA, FTM and FTA are supplied by CBS; no statistics are estimated or imputed. '
               'Snapshot date is the retrieval date; CBS does not show a publication timestamp. '
               'CBS displays the season by its starting year. Position filters overlap; one intact source row is retained per CBS player ID.')
GROUPS = ('PG', 'SG', 'SF', 'PF', 'C', 'G', 'F')
TOTAL_FIELDS = {'minutes_pg':'min', 'pts_pg':'pts', 'reb_pg':'reb', 'ast_pg':'ast',
                'stl_pg':'stl', 'blk_pg':'blk', 'fg3m_pg':'3pm', 'fgm_pg':'fgm',
                'fga_pg':'fga', 'ftm_pg':'ftm', 'fta_pg':'fta'}
EXPECTED_HEADERS = ['player','gp','gs','fpts','min','mpg','fgm','fga','fg%','ftm','fta','ft%',
                    '3pm','3pa','3fg%','pts','ppg','reb','rpg','ast','apg','stl','spg','to','topg','blk','bpg']
AUDIT_HEADERS = ['source_player_id','source_url','source_sha256','retrieved_at','source_group',
                 'source_rows','source_rows_differ','fg_pct','ft_pct'] + [field+'_total' for field in TOTAL_FIELDS.values()]


class CBSTable(TableParser):
    def __init__(self):
        super().__init__('cbs-projections')
        self.spans = []
        self.name_link = False
        self.tables = 0

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == 'table' and 'TableBase-table' in attributes.get('class', '').split():
            attrs = attrs + [('id', self.table_id)]
            self.tables += 1
        super().handle_starttag(tag, attrs)
        if tag == 'span':
            self.spans.append(attributes.get('class', ''))
        if tag == 'a' and 'CellPlayerName--long' in self.spans:
            self.name_link = True

    def handle_data(self, data):
        super().handle_data(data)
        if self.cell is not None and 'CellPlayerName--long' in self.spans:
            field = ('player' if self.name_link else 'positions' if 'CellPlayerName-position' in self.spans
                     else 'nba_team' if 'CellPlayerName-team' in self.spans else None)
            if field:
                self.cell[field] = self.cell.get(field, '') + data

    def handle_endtag(self, tag):
        if tag == 'a':
            self.name_link = False
        if tag == 'span' and self.spans:
            self.spans.pop()
        super().handle_endtag(tag)


def parse_cbs(html, season, group):
    year = season[:4]
    if not re.search(r'<title>\s*'+year+r' Projections Fantasy Basketball Stats - '+group+r' Points - CBS Sports\s*</title>', html):
        raise ValueError('CBS page does not identify the requested season and position projections.')
    table = CBSTable()
    table.feed(html)
    if table.tables != 1 or not table.records:
        raise ValueError('Expected exactly one CBS projection table.')
    headers = []
    for cell in table.records[0]:
        keys = [parse_qs(urlsplit(link.get('href', '')).query).get('sortcol', []) for link in cell['links']]
        headers.append(next((key[0] for key in keys if key), None))
    if headers != EXPECTED_HEADERS:
        raise ValueError('CBS projection columns changed; refusing a guessed mapping.')
    records, seen = [], set()
    for cells in table.records[1:]:
        if len(cells) != len(headers):
            raise ValueError('Incomplete CBS projection row.')
        first = cells[0]
        ids = {m.group(1) for link in first['links'] if (m := re.search(r'/nba/players/(\d+)/', link.get('href', '')))}
        if len(ids) != 1:
            raise ValueError('CBS player ID is missing or ambiguous.')
        source_id = ids.pop()
        if source_id in seen:
            raise ValueError('Duplicate CBS player on a single position page.')
        seen.add(source_id)
        row = {field:' '.join(first.get(field, '').split()) for field in STAT_HEADERS[:3]}
        if not all(row.values()):
            raise ValueError(f'CBS name, team or position is missing for {source_id}.')
        values = dict(zip(headers, (cell['text'] for cell in cells)))
        games = numeric(values['gp'], 'projected games')
        if not 0 < games <= 82:
            raise ValueError(f'Invalid published game count for {row["player"]}.')
        row.update(games=games, source_player_id=source_id, source_group=group)
        for field, source in TOTAL_FIELDS.items():
            total = numeric(values[source], source)
            row[source+'_total'] = total
            row[field] = total / games
        for field, source in (('fg_pct','fg%'), ('ft_pct','ft%')):
            row[field] = numeric(values[source], source) / 100
            if row[field] > 1:
                raise ValueError('Invalid published shooting percentage.')
        if row['fgm_pg'] > row['fga_pg'] or row['ftm_pg'] > row['fta_pg'] or row['fg3m_pg'] > row['fgm_pg']:
            raise ValueError(f'Invalid published shooting totals for {row["player"]}.')
        if row['minutes_pg'] > 60:
            raise ValueError('Unexpected minutes units in CBS projections.')
        records.append(row)
    if not records:
        raise ValueError('CBS has no projected players.')
    return records


def combine_pages(pages):
    candidates = defaultdict(list)
    for records in pages:
        for row in records:
            candidates[row['source_player_id']].append(row)
    combined = []
    identities = set()
    aliases = load_aliases()
    for source_id, options in candidates.items():
        # Prefer the matching primary-position page, then the newest snapshot.
        # Never blend fields from different rows or average projections ourselves.
        options.sort(key=lambda row: (row['source_group'] == row['positions'], row['retrieved_at'], row['source_group']), reverse=True)
        row = dict(options[0])
        row['source_rows'] = len(options)
        row['source_rows_differ'] = any(any(other[k] != row[k] for k in STAT_HEADERS) for other in options[1:])
        identity = resolve_name(row['player'], aliases)[0]
        if identity in identities:
            raise ValueError(f'Two CBS player IDs map to the same local player: {row["player"]}.')
        identities.add(identity)
        combined.append(row)
    return sorted(combined, key=lambda row: row['player'])


def collect_projections(output_dir, season, *, refresh=False):
    pages, sources = [], []
    for group in GROUPS:
        url = f'https://www.cbssports.com/fantasy/basketball/stats/{group}/{season[:4]}/season/projections/'
        html, metadata = download(url, output_dir/'cache', f'cbs-{group}-{season[:4]}', refresh=refresh)
        records = parse_cbs(html, season, group)
        for row in records:
            row.update(source_url=url, source_sha256=metadata['sha256'], retrieved_at=metadata['retrieved_at'])
        sources.append(dict(group=group, players=len(records), **metadata))
        pages.append(records)
    records = combine_pages(pages)
    if len(records) < 225:
        raise ValueError(f'CBS coverage is only {len(records)} players; refusing an incomplete league projection pool.')
    return records, sources
