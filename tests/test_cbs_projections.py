import contextlib
import csv
import io
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from survivor.bundled_stats import SNAPSHOT, sync_bundled_projections, validated_sources
from survivor.catalog import STAT_HEADERS, load_aliases, resolve_name
from survivor.cbs_projections import EXPECTED_HEADERS, TOTAL_FIELDS, combine_pages, parse_cbs
from survivor.dashboard import create_app
from survivor.database import preview_database
from survivor.keepers import sync_bundled_keepers


def synthetic_page(*, player_id='123', name='Synthetic Test Player', group='C', games='50'):
    # Synthetic fixtures are only used by tests, never written to the real snapshot.
    values = {'gp':games,'gs':'—','fpts':'40','min':'1500','mpg':'30','fgm':'350','fga':'700',
              'fg%':'50','ftm':'200','fta':'250','ft%':'80','3pm':'100','3pa':'300',
              '3fg%':'33.3','pts':'1001','ppg':'20.0','reb':'500','rpg':'10','ast':'200',
              'apg':'4','stl':'50','spg':'1','to':'100','topg':'2','blk':'75','bpg':'1.5'}
    header = ''.join(f'<th><a href="?sortcol={key.replace("%","%25")}">{key}</a></th>' for key in EXPECTED_HEADERS)
    player = (f'<span class="CellPlayerName--short"><a href="/nba/players/{player_id}/test/">S. Player</a></span>'
              f'<span class="CellPlayerName--long"><span><a href="/nba/players/{player_id}/test/">{name}</a>'
              '<span class="CellPlayerName-position"> C </span><span class="CellPlayerName-team"> TEST </span></span></span>')
    body = f'<td>{player}</td>'+''.join(f'<td>{values[key]}</td>' for key in EXPECTED_HEADERS[1:])
    return f'<title>2026 Projections Fantasy Basketball Stats - {group} Points - CBS Sports</title><table class="TableBase-table"><tr>{header}</tr><tr>{body}</tr></table>'


class CBSAdapterTests(unittest.TestCase):
    def test_provider_totals_are_preserved_and_converted_without_estimates(self):
        rows = parse_cbs(synthetic_page(), '2026-27', 'C')
        self.assertEqual(len(rows),1)
        row = rows[0]
        self.assertEqual((row['player'],row['positions'],row['nba_team']),('Synthetic Test Player','C','TEST'))
        self.assertEqual((row['fgm_pg'],row['fga_pg'],row['ftm_pg'],row['fta_pg']),(7,14,4,5))
        self.assertEqual(row['pts_pg'],1001/50)  # Do not replace the provider's PTS with our own calculation.
        for field, source in TOTAL_FIELDS.items():
            self.assertAlmostEqual(row[field]*row['games'], row[source+'_total'])

    def test_wrong_season_columns_blank_volume_and_invalid_games_fail(self):
        cases = [(synthetic_page(),'2025-26'),
                 (synthetic_page().replace('sortcol=fga','sortcol=unknown'),'2026-27'),
                 (synthetic_page().replace('<td>700</td>','<td></td>'),'2026-27'),
                 (synthetic_page(games='0'),'2026-27'),
                 (synthetic_page(games='83'),'2026-27')]
        for html, season in cases:
            with self.subTest(season=season, html=html[:70]), self.assertRaises(ValueError):
                parse_cbs(html,season,'C')

    def test_overlapping_filters_use_one_intact_primary_position_row(self):
        primary = parse_cbs(synthetic_page(),'2026-27','C')[0]
        primary.update(retrieved_at='2026-10-01',source_url='https://example.test/C')
        other = {**primary,'source_group':'F','retrieved_at':'2026-10-02','source_url':'https://example.test/F','pts_pg':99}
        combined = combine_pages([[other],[primary]])
        self.assertEqual(len(combined),1)
        self.assertEqual(combined[0]['pts_pg'],primary['pts_pg'])
        self.assertEqual(combined[0]['source_url'],primary['source_url'])
        self.assertEqual(combined[0]['source_rows'],2)
        self.assertTrue(combined[0]['source_rows_differ'])
        with self.assertRaisesRegex(ValueError,'same local player'):
            combine_pages([[primary, {**primary,'source_player_id':'999'}]])


class PublishedProjectionTests(unittest.TestCase):
    def test_bundled_provider_values_keeper_filters_restart_and_no_fake_valuations(self):
        sources = validated_sources(kind='projection')
        self.assertEqual(len(sources),1)
        metadata, path = sources[0]
        with path.open(encoding='utf-8-sig',newline='') as stream:
            published = list(csv.DictReader(stream))
        self.assertEqual(len(published),348)
        self.assertEqual(len({row['source_player_id'] for row in published}),348)
        self.assertEqual(len(metadata['source_pages']),3)
        db = preview_database()
        self.addCleanup(db.close)
        sync_bundled_keepers(db)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(sync_bundled_projections(db),{'datasets':1,'rows':348})
            self.assertEqual(sync_bundled_projections(db),{'datasets':1,'rows':348})
        self.assertEqual(db.execute('SELECT COUNT(*) FROM stat_datasets').fetchone()[0],1)
        self.assertEqual(db.execute('SELECT COUNT(*) FROM projected_values').fetchone()[0],0)
        client = create_app(db).test_client()
        payload = client.get('/api/projections').json
        self.assertEqual(len(payload['rows']),348)
        self.assertEqual(payload['dataset']['source_name'],'ESPN published projections')
        self.assertIn('No forecasts are blended or estimated',payload['dataset']['notes'])
        self.assertEqual(len(client.get('/api/projections?availability=available').json['rows']),318)
        self.assertEqual(len(client.get('/api/projections?availability=kept').json['rows']),30)
        indexed = {row['player_id']:row for row in payload['rows']}
        aliases = load_aliases()
        for row in published:
            stored = indexed[resolve_name(row['player'],aliases)[0]]
            for field in STAT_HEADERS[3:]:
                self.assertAlmostEqual(stored[field],float(row[field]))
            for field in TOTAL_FIELDS:
                self.assertAlmostEqual(stored[field]*stored['games'],float(row[field.removesuffix('_pg')+'_total']))
        acuff = indexed['dariusacuffjr']
        self.assertAlmostEqual(acuff['pts_pg'],1234/73)
        self.assertAlmostEqual(acuff['ast_pg'],431/73)
        self.assertAlmostEqual(acuff['stl_pg'],80/73)
        jokic = indexed['nikolajokic']
        self.assertAlmostEqual(jokic['fga_pg'],1325/72)
        self.assertAlmostEqual(jokic['fta_pg'],475/72)
        self.assertEqual(jokic['confirmed_keeper_cost'],88)
        self.assertEqual(client.get('/api/projections?season=2025-26').json,{'dataset':None,'rows':[]})
        self.assertFalse(db.execute('PRAGMA foreign_key_check').fetchall())

    def test_missing_shooting_and_corrupt_projection_never_enter_the_database(self):
        from survivor.import_stats import validate_csv
        with self.assertRaisesRegex(ValueError,'fgm_pg must be numeric'):
            validate_csv(SNAPSHOT/'projections-2026-27-INCOMPLETE.csv')
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            manifest = json.loads((SNAPSHOT/'manifest.json').read_text(encoding='utf-8'))
            source = next(s for s in manifest['sources'] if s['kind']=='projection')
            shutil.copyfile(SNAPSHOT/source['file'],folder/source['file'])
            (folder/'manifest.json').write_text(json.dumps(manifest),encoding='utf-8')
            with (folder/source['file']).open('a',encoding='utf-8') as stream:
                stream.write('changed')
            db = preview_database()
            self.addCleanup(db.close)
            with self.assertRaisesRegex(ValueError,'hash mismatch'):
                sync_bundled_projections(db,folder)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM stat_datasets').fetchone()[0],0)


if __name__ == '__main__':
    unittest.main()
