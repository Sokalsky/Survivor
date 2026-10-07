"""Value math and real-snapshot integration; no external database or network."""
import contextlib
import copy
import csv
import io
import json
import unittest
from unittest.mock import patch

from survivor.catalog import ROOT, build_catalog, rows
from survivor.bundled_stats import sync_bundled_stats, sync_bundled_projections
from survivor.database import preview_database
from survivor.dashboard import create_app
from survivor.keepers import sync_bundled_keepers
from survivor.valuation import (CONFIG, allocate, build_valuations, fingerprint,
                                profiles, save_run, survivor_scores)


def settings():
    return json.loads(CONFIG.read_text(encoding='utf-8'))


class ValueMathTests(unittest.TestCase):
    def test_percentages_use_attempt_volume_and_negative_impact(self):
        common = dict(games=82,minutes_pg=30,pts_pg=15,reb_pg=5,ast_pg=5,
                      stl_pg=1,blk_pg=1,fg3m_pg=2,ftm_pg=4,fta_pg=5)
        players = [{**common,'player_id':'a','fgm_pg':6,'fga_pg':10},
                   {**common,'player_id':'b','fgm_pg':12,'fga_pg':20},
                   {**common,'player_id':'c','fgm_pg':2,'fga_pg':10}]
        scored,ref = profiles(players,pool_size=3)
        self.assertEqual(ref['fg_baseline'],.5)
        self.assertEqual(scored['a']['contributions']['FG%'],1)
        self.assertEqual(scored['b']['contributions']['FG%'],2)
        self.assertEqual(scored['c']['contributions']['FG%'],-3)
        self.assertLess(scored['c']['z']['FG%'],0)

    def test_allocation_preserves_cents_floor_and_replacement(self):
        dollars,meta = allocate({'a':10,'b':5,'c':3,'d':2},3,200)
        self.assertEqual(round(sum(dollars.values()),2),200)
        self.assertEqual(dollars['d'],0)
        self.assertEqual(meta['replacement_score'],2)
        self.assertGreater(dollars['a'],dollars['b'])
        self.assertGreaterEqual(dollars['c'],1)
        tied,_ = allocate(dict.fromkeys('abcd',0),3,10)
        self.assertEqual(round(sum(tied.values()),2),10)
        self.assertEqual(sum(v>=1 for v in tied.values()),3)
        with self.assertRaises(ValueError):
            allocate({'a':1},2,200)

    def scenario(self, moves=100, cap=1000):
        players = [{'player_id':str(i),'games':82} for i in range(230)]
        scored = {str(i):{'score':230-i} for i in range(230)}
        rules = {'teams':15,'games_or_start_limits':cap,'roster_move_limit':moves}
        config = settings()
        result,report = survivor_scores(players,scored,rules,config,config['survivor']['scenarios'][1])
        return players,scored,rules,config,result,report

    def test_elite_persistence_replacement_cap_and_stage_lengths(self):
        _,_,_,_,result,report = self.scenario()
        self.assertAlmostEqual(result['0']['useful_season_fraction'],1)
        self.assertLess(result['150']['useful_season_fraction'],.5)
        self.assertEqual(result['229']['score'],0)
        self.assertGreater(result['0']['score'],result['29']['score'])
        self.assertEqual(report['stages'][0]['replacement_depth'],225)
        self.assertEqual(report['stages'][-1]['replacement_depth'],30)
        self.assertAlmostEqual(sum(s['fraction'] for s in report['stages']),1)
        self.assertAlmostEqual(report['neutral_games_per_team'],1000)
        self.assertLessEqual(report['expected_moves_used'],100)
        self.assertLessEqual(result['0']['useful_games'],82)

    def test_move_constraint_and_game_cap_are_effective(self):
        *_,result,report = self.scenario(moves=0,cap=800)
        self.assertTrue(all(s['replacement_depth']==225 for s in report['stages']))
        self.assertEqual(report['expected_moves_used'],0)
        self.assertAlmostEqual(result['150']['useful_season_fraction'],1)
        self.assertAlmostEqual(report['neutral_games_per_team'],800)

    def test_faster_cuts_reduce_marginal_players_and_bad_schedule_rejected(self):
        players,scored,rules,config,central,_ = self.scenario()
        fast,_ = survivor_scores(players,scored,rules,config,config['survivor']['scenarios'][0])
        self.assertLess(fast['150']['score'],central['150']['score'])
        with self.assertRaisesRegex(ValueError,'two-team final'):
            survivor_scores(players,scored,rules,config,{'first_cut_week':6,'cut_interval_weeks':2,'name':'invalid'})


class PublishedValueIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = preview_database()
        with contextlib.redirect_stdout(io.StringIO()):
            build_catalog(ROOT/'Survivor keeper log 2025.xlsx',cls.db)
            sync_bundled_keepers(cls.db)
            sync_bundled_stats(cls.db)
            sync_bundled_projections(cls.db)
        cls.before = fingerprint(rows(cls.db,'SELECT * FROM player_stats ORDER BY dataset_id,player_id'))
        cls.result = build_valuations(cls.db,'2026-27')
        save_run(cls.db,cls.result)
        cls.client = create_app(cls.db).test_client()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def test_complete_real_player_values_and_budget_reconcile(self):
        result = self.result
        self.assertEqual(len(result['values']),500)
        self.assertEqual(sum(v['keeper_cost'] is not None for v in result['values']),30)
        for name in ('central','faster','slower'):
            dollars = [v['category_values']['scenario_values'][name] for v in result['values']]
            self.assertEqual(round(sum(dollars),2),3000)
            self.assertEqual(sum(v>=1 for v in dollars),225)
        self.assertTrue(all(v['fair_value']==v['category_values']['neutral_value'] for v in result['values']))
        self.assertTrue(all(v['recommended_bid_ceiling'] is None for v in result['values']))
        self.assertEqual(self.before,fingerprint(rows(self.db,'SELECT * FROM player_stats ORDER BY dataset_id,player_id')))

    def test_chronological_market_validation_and_explicit_limits(self):
        result = self.result
        for r in result['backtest']:
            self.assertLess(r['stats_season'],r['season'])
            self.assertLess(r['training_last_season'],r['season'])
        v = result['validation']
        self.assertEqual(v['training_rows'],1991)
        self.assertEqual(v['unmatched_or_low_sample_rows'],182)
        self.assertEqual(v['holdout'][v['selected_model']]['n'],541)
        self.assertLess(v['holdout'][v['selected_model']]['mae'],v['holdout']['mean']['mae'])
        self.assertIn('not independently validated',v['interpretation'])
        self.assertEqual(v['retention_audit']['bands']['top_30'],{'opening':40,'on_same_final_roster':27})

    def test_run_atomic_idempotent_and_immutable(self):
        save_run(self.db,self.result)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM valuation_runs').fetchone()[0],1)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM projected_values').fetchone()[0],500)
        broken = copy.deepcopy(self.result)
        broken['run_id']='failed-test-run'
        broken['values'][1]['player_id']='missing-player-for-rollback-test'
        with self.assertRaises(Exception):
            save_run(self.db,broken)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM valuation_runs').fetchone()[0],1)
        self.assertFalse(self.db.execute('PRAGMA foreign_key_check').fetchall())

    def test_latest_data_excludes_future_actuals_and_sales(self):
        from survivor.valuation import load_inputs
        _,actuals,_,sales,_,_ = load_inputs(self.db,'2026-27')
        self.assertTrue(all(season<'2026-27' for season in actuals))
        self.assertTrue(all(sale['season']<'2026-27' for sale in sales))

    def test_api_three_outputs_availability_and_download(self):
        payload = self.client.get('/api/valuations').json
        self.assertEqual(payload['run']['run_id'],self.result['run_id'])
        self.assertIn('league_rules',payload['run']['settings'])
        self.assertEqual(len(payload['rows']),500)
        self.assertEqual(len(self.client.get('/api/valuations?availability=available').json['rows']),470)
        self.assertEqual(len(self.client.get('/api/valuations?availability=kept').json['rows']),30)
        response = self.client.get('/api/valuations.csv?availability=kept')
        exported = list(csv.DictReader(io.StringIO(response.data.decode('utf-8-sig'))))
        self.assertEqual(len(exported),30)
        jokic = next(r for r in exported if r['Player']=='Nikola Jokic')
        self.assertEqual(float(jokic['Confirmed keeper cost']),88)
        self.assertGreater(float(jokic['Survivor value score']),0)
        self.assertEqual(self.client.post('/api/valuations').status_code,405)
        self.assertEqual(self.client.get('/api/valuations?run=missing').json,{'run':None,'rows':[]})


if __name__=='__main__':
    unittest.main()
