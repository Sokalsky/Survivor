import copy
import json
from pathlib import Path
import tempfile
import unittest

from survivor.historical_projections import load_archive, bundled_archives, archive_metadata, sync_historical_projections, stored_archives
from survivor.published_archives import parse_razzball, validate_record, pdf_columns
from survivor.preseason import load_preseason_evidence
from survivor.database import preview_database
from survivor.valuation import PriceModel, CATEGORIES
from tests.test_valuation import settings


class HistoricalForecastTests(unittest.TestCase):
    def test_archive_integrity_aliases_and_totals(self):
        archive=load_archive()
        self.assertEqual(len(archive['records']),200)
        self.assertIsNone(archive['published_at'])
        self.assertIsNone(archive['provider'])
        by_id={r['player_id']:r for r in archive['records']}
        self.assertEqual(by_id['laurimarkkanen']['raw_name'],'Lauri Markannen')
        self.assertEqual(by_id['bennedictmathurin']['raw_name'],'Bennidict Mathurin')
        self.assertIn('derecklivelyii',by_id)
        for r in archive['records']:
            self.assertAlmostEqual(r['pts_pg']*r['games'],r['original']['pts'])
            self.assertIsNone(r['minutes_pg'])
            self.assertIsNone(r['positions'])
        altered=copy.deepcopy(archive)
        altered['records'][0]['games']=1
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'bad.json';path.write_text(json.dumps(altered),encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'checksum'):
                load_archive(path)
        db=preview_database()
        try:
            sync_historical_projections(db);sync_historical_projections(db)
            self.assertEqual(len(stored_archives(db)),9)
            self.assertEqual(next(a for a in stored_archives(db) if a['season']=='2025-26'),archive)
        finally:
            db.close()

    def test_evidence_is_season_specific_and_rejects_hindsight(self):
        payload,index=load_preseason_evidence()
        self.assertIn(('2025-26','jaysontatum'),index)
        self.assertNotIn(('2026-27','jaysontatum'),index)
        payload['records'][0]['reported_at']='2020-04-01'
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'bad.json';path.write_text(json.dumps(payload),encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'precede'):
                load_preseason_evidence(path)

    def test_projected_games_affect_similarity_only_when_supplied(self):
        profile={'score':8,'z':dict.fromkeys(CATEGORIES,1),'age':26,'projected_games':75}
        observations=[{'season':'2025-26','profile':{**profile,'projected_games':games},'target':2} for games in (75,50)]
        model=PriceModel(observations,settings(),2026)
        distances=dict((i,d) for d,i in model.nearest(profile))
        self.assertEqual(distances[0],0)
        self.assertGreater(distances[1],0)
        del profile['projected_games']
        self.assertEqual([d for d,_ in model.nearest(profile)],[0,0])

    def test_published_archives_preserve_dates_units_missing_fields_and_rejections(self):
        archives={a['season']:a for a in bundled_archives()}
        self.assertEqual(sum(len(a['records']) for a in archives.values()),1567)
        for season,archive in archives.items():
            if season=='2025-26':
                continue
            self.assertTrue(archive['source_url'].startswith('https://'))
            self.assertEqual(archive_metadata(archive)['date_basis'],'published')
            self.assertEqual(archive['published_at'][:4],season[:4])
            for row in archive['records']:
                validate_record(row)
                if season<'2024-25':
                    self.assertIsNone(row['games'])
                    self.assertIsNone(row['minutes_pg'])
                    self.assertIsNone(row['positions'])
                else:
                    self.assertGreater(row['games'],0)
                    self.assertEqual(row['fgm_pg'],float(row['original']['FGM']))
                    self.assertEqual(row['pts_pg'],float(row['original']['PTS']))
        rejected=archives['2022-23']['rejected_records']
        self.assertEqual({r['player_id'] for r in rejected},{'kawhileonard','tobiasharris','kevinhuerter'})
        self.assertEqual({r['original']['ft%'] for r in rejected},{'885','865','805'})
        self.assertEqual(archives['2024-25']['rejected_records'][0]['player_id'],'vincewilliamsjr')
        pdf={r['player_id']:r for r in archives['2024-25']['records']}
        # These profiles straddle PDF columns/pages; a content-order parser can
        # silently attach the wrong player's name to these numeric rows.
        for key,points in [('cadecunningham',24.6),('jordanpoole',18.5),('joshgiddey',12.9),
                           ('herbertjones',11.1),('danielgafford',11.3),('wendellcarterjr',11.3)]:
            self.assertEqual(pdf[key]['pts_pg'],points)
        self.assertIn('bubcarrington',pdf)
        self.assertIn('treymurphyiii',pdf)

    def test_dated_table_parser_rejects_bad_cells_without_correcting_them(self):
        from html import escape
        archive=next(a for a in bundled_archives() if a['season']=='2022-23')
        rows=sorted(archive['records']+archive['rejected_records'],key=lambda r:r['source_row'])
        header=list(rows[0]['original'])
        cells=[header]+[list(r['original'].values()) for r in rows]
        html='"datePublished":"2022-09-12T07:30:01+00:00"<table>'+''.join(
            '<tr>'+''.join('<td>'+escape(str(c))+'</td>' for c in row)+'</tr>' for row in cells)+'</table>'
        rejected=[]
        parsed=parse_razzball(html,'2022-23',rejected)
        self.assertEqual(len(parsed),152)
        self.assertEqual(len(rejected),3)
        jokic=next(r for r in parsed if r['player_id']=='nikolajokic')
        self.assertAlmostEqual(jokic['fgm_pg'],.57*17.5)
        self.assertAlmostEqual(jokic['ftm_pg'],.82*6)
        self.assertEqual(jokic['pts_pg'],26.2)
        with self.assertRaisesRegex(ValueError,'date'):
            parse_razzball(html.replace('2022-09-12','2023-09-12'),'2022-23')
        with self.assertRaisesRegex(ValueError,'columns'):
            parse_razzball(html.replace('<td>fga/g</td>','<td>unknown</td>'),'2022-23')

    def test_pdf_extraction_uses_visual_column_order(self):
        class Page:
            def extract_text(self,visitor_text):
                for text,x,y in [('right bottom',312,70),('left bottom',36,50),
                                 ('right top',312,700),('left top',36,700)]:
                    visitor_text(text,[1,0,0,1,0,0],[7,0,0,7,x,y],None,1)
        class Reader:
            pages=[Page()]
        self.assertEqual(pdf_columns(Reader()),['left top\nleft bottom\nright top\nright bottom'])
