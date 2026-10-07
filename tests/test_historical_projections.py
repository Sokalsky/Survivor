import copy
import json
from pathlib import Path
import tempfile
import unittest

from survivor.historical_projections import load_archive, sync_historical_projections, stored_archives
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
            self.assertEqual(len(stored_archives(db)),1)
            self.assertEqual(stored_archives(db)[0],archive)
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
