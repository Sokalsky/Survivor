"""Value math and real-snapshot integration; no external database or network."""
import contextlib
import copy
import csv
import io
import json
import unittest

from survivor.catalog import ROOT, build_catalog, rows
from survivor.bundled_stats import sync_bundled_stats, sync_bundled_projections
from survivor.database import preview_database
from survivor.dashboard import create_app
from survivor.keepers import sync_bundled_keepers
from survivor.valuation import (CONFIG, allocate, build_valuations, fingerprint,
                                profiles, save_run, survivor_scores, PriceModel, CATEGORIES)
from survivor.market_context import MarketContext, COLUMNS, digest, load_context


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


class ComparableTests(unittest.TestCase):
    def profile(self, value, age=25):
        return {'z':dict.fromkeys(CATEGORIES,value),'score':8*value,'age':age}

    def model(self, age=False):
        observations = [{'season':'2025-26','profile':self.profile(v,a),'target':price,'player_id':str(i)}
                        for i,(v,a,price) in enumerate([(0,25,1),(.2,25,2),(.8,25,4),(.05,38,2)])]
        return PriceModel(observations,{**settings(),'comp_rules':None},2026,age=age)

    def test_closer_matches_receive_more_weight_and_power_strengthens_it(self):
        model = self.model()
        profile = self.profile(0)
        stronger = {r['index']:r['weight'] for r in model.weighted_neighbors(profile,power=3)}
        previous = {r['index']:r['weight'] for r in model.weighted_neighbors(profile,power=2)}
        self.assertAlmostEqual(sum(stronger.values()),1)
        self.assertGreater(stronger[1],stronger[2])
        self.assertGreater(stronger[1]/stronger[2],previous[1]/previous[2])
        self.assertAlmostEqual(stronger[1]/stronger[2],((.8+.25)/(.2+.25))**3)

    def test_recency_and_age_are_explicit_and_zero_distance_is_finite(self):
        profile = self.profile(0)
        model = self.model(age=True)
        weights = model.weighted_neighbors(profile)
        indexed = {w['index']:w for w in weights}
        self.assertGreater(indexed[1]['weight'],indexed[3]['weight'])  # More similar age wins here.
        self.assertTrue(all(0<w['weight']<=1 for w in weights))
        duplicate = [dict(model.observations[0],season=s) for s in ['2024-25','2025-26']]
        recent = PriceModel(duplicate,{**settings(),'comp_rules':None},2026).weighted_neighbors(profile)
        shares = {w['index']:w['weight'] for w in recent}
        self.assertAlmostEqual(shares[0]/shares[1],.9)
        with self.assertRaisesRegex(ValueError,'precede'):
            PriceModel(duplicate,settings(),2025)

    def test_local_price_reconciles_to_weighted_actual_minus_model_residuals(self):
        model = self.model(age=True)
        model.settings.pop('comp_profile_adjustment',None)  # Reproduce stored v8 runs.
        profile = self.profile(.3)
        components = model.local_components(profile)
        expected = sum(w['weight']*(model.observations[w['index']]['target']-
                                    model.raw_prediction(model.observations[w['index']]['profile']))
                       for w in components['neighbors'])*settings()['comp_correction_share']
        self.assertAlmostEqual(expected,components['correction'])
        self.assertAlmostEqual(model.predictions(profile)['local'],max(0,components['base']+expected))
        self.assertGreaterEqual(components['effective_comps'],1)
        self.assertLessEqual(components['effective_comps'],len(model.observations))

    def test_comp_transport_preserves_observed_premium_without_removing_base_curve(self):
        model = self.model(age=True)
        target,other = self.profile(1.2,27),self.profile(1.3,25)
        change = model.profile_adjustment(target,other)
        self.assertEqual(change['curve'],0)
        self.assertNotEqual(change['full_curve'],0)
        self.assertNotEqual(change['stat'],0)
        self.assertNotEqual(change['age'],0)
        self.assertAlmostEqual(change['total'],-model.profile_adjustment(other,target)['total'])
        self.assertEqual(model.profile_adjustment(target,target)['total'],0)
        base = model.raw_prediction(target)
        model.beta[len(CATEGORIES)+1] += 100
        # A steeper global elite curve changes the base, but cannot magnify
        # small differences when transporting an actual qualifying auction bid.
        self.assertNotAlmostEqual(base,model.raw_prediction(target))
        self.assertAlmostEqual(change['total'],model.profile_adjustment(target,other)['total'])
        model.settings.pop('comp_profile_adjustment')
        self.assertAlmostEqual(model.profile_adjustment(target,other)['total'],
                               model.raw_prediction(target)-model.raw_prediction(other))

    def test_one_to_three_qualified_matches_dominate_without_promoting_distant_ones(self):
        for count in (1,2,3):
            observations = [{'season':'2017-18','profile':self.profile(i*.1),'target':i+1}
                            for i in range(count)]
            observations += [{'season':'2025-26','profile':self.profile(.45),'target':10} for _ in range(17)]
            model = PriceModel(observations,settings(),2026,age=True)
            weighted = model.weighted_neighbors(self.profile(0))
            strong = [w for w in weighted if w['match_quality']=='strong']
            self.assertEqual(len(strong),count)
            self.assertGreaterEqual(sum(w['weight'] for w in strong),.8-1e-12)
            self.assertAlmostEqual(sum(w['weight'] for w in weighted),1)
            self.assertTrue(all(w['weight']<strong[-1]['weight'] for w in weighted if w['match_quality']=='supporting'))
            # Prices are never an input to similarity or qualifying rules.
            altered = [dict(o,target=200-o['target']) for o in observations]
            self.assertEqual(weighted,PriceModel(altered,settings(),2026,age=True).weighted_neighbors(self.profile(0)))

    def test_production_shape_age_and_known_games_each_can_disqualify_a_match(self):
        target = {**self.profile(0),'projected_games':75}
        shape = {**target,'z':{**target['z'],'PTS':2.1,'REB':-2.1}}
        samples = [self.profile(.6),shape,{**target,'age':36},{**target,'projected_games':40}]
        model = PriceModel([{'season':'2025-26','profile':p,'target':1} for p in samples],settings(),2026,age=True)
        self.assertEqual(model.nearest(target),[])
        components = model.local_components(target)
        self.assertEqual(components['correction'],0)
        self.assertEqual(components['effective_comps'],0)
        for key,value in model.predictions(target).items():
            if key!='mean':
                self.assertAlmostEqual(value,max(0,model.raw_prediction(target)))

    def test_cross_era_similarity_uses_actual_stat_gaps_and_shared_shooting_baseline(self):
        from survivor.valuation import COMPARISON_FIELDS
        stats = dict.fromkeys(COMPARISON_FIELDS,1.)
        reference = {'fg_baseline':.5,'ft_baseline':.8,'standard_deviations':dict.fromkeys(CATEGORIES,1.)}
        target = {**self.profile(0),'stats':stats,'comparison_reference':reference}
        # Equal era-relative z scores cannot hide a much smaller scoring projection.
        other = {**target,'stats':{**stats,'pts_pg':5}}
        model = PriceModel([{'season':'2025-26','profile':other,'target':2}],settings(),2026)
        self.assertEqual(model.nearest(target),[])
        impact = model.match(target,{**target,'stats':{**stats,'fgm_pg':3,'fga_pg':5}})
        self.assertEqual(impact['distance'],0)  # Same makes above baseline despite more attempts.
        harmful = model.match(target,{**target,'stats':{**stats,'fga_pg':6}})
        self.assertEqual(harmful['match_quality'],'excluded')

    def test_supporting_only_reduces_correction_and_prior_actuals_cannot_be_strong(self):
        profile = {**self.profile(0),'outlook_basis':'prior_actuals_proxy'}
        model = PriceModel([{'season':'2025-26','profile':profile,'target':2}],settings(),2026)
        components = model.local_components(self.profile(0))
        self.assertEqual(components['strong_count'],0)
        self.assertEqual(components['neighbors'][0]['match_quality'],'supporting')
        self.assertEqual(components['correction_share'],settings()['comp_rules']['supporting_correction_share'])

    def precise_profile(self, value=0, games=72):
        return {**self.profile(value),'projected_games':games,'outlook_basis':'projection'}

    def test_one_to_three_near_identical_forecasts_dominate_many_merely_close_matches(self):
        for count in (1,2,3):
            observations = [{'season':'2025-26','player_id':f'close{i}',
                             'profile':self.precise_profile(i*.01),'target':4} for i in range(count)]
            observations += [{'season':'2025-26','player_id':f'other{i}',
                              'profile':self.precise_profile(.15),'target':1} for i in range(20)]
            model = PriceModel(observations,settings(),2026,age=True)
            components = model.local_components(self.precise_profile())
            precise = [w for w in components['neighbors'] if w['match_quality']=='near_identical']
            self.assertEqual(len(precise),count)
            self.assertGreaterEqual(sum(w['weight'] for w in precise),.95-1e-12)
            self.assertEqual(components['correction_share'],.9)
            self.assertGreaterEqual(sum(w['weight'] for w in precise)*components['correction_share'],.855-1e-12)
            self.assertAlmostEqual(sum(w['weight'] for w in components['neighbors']),1)
            changed = [{**o,'target':100-o['target'],'player_id':'renamed'+o['player_id']} for o in observations]
            self.assertEqual(model.weighted_neighbors(self.precise_profile()),
                             PriceModel(changed,settings(),2026,age=True).weighted_neighbors(self.precise_profile()))

    def test_near_identical_requires_known_games_age_recent_projection_and_separation(self):
        target = self.precise_profile(.1)
        rows = [{'season':'2025-26','profile':self.precise_profile(),'target':1},
                {'season':'2025-26','profile':self.precise_profile(.23),'target':2}]
        model = PriceModel(rows,settings(),2026,age=True)
        # One record passes absolute limits, but its .224 distance is too close
        # to the next record's .291 distance to deserve the dominant tier.
        self.assertTrue(all(w['match_quality']=='strong' for w in model.weighted_neighbors(target)))
        tests = [({'season':'2025-26','profile':self.precise_profile(games=None),'target':1},True),
                 ({'season':'2022-23','profile':self.precise_profile(),'target':1},True),
                 ({'season':'2025-26','profile':{**self.precise_profile(),'outlook_basis':'prior_actuals_proxy'},'target':1},True),
                 ({'season':'2025-26','profile':self.precise_profile(),'target':1},False)]
        for observation,age in tests:
            m = PriceModel([observation],settings(),2026,age=age)
            self.assertEqual(m.local_components(self.precise_profile())['near_identical_count'],0)

    def test_match_cache_invalidates_when_games_or_settings_change(self):
        target = self.precise_profile()
        config = settings()
        model = PriceModel([{'season':'2025-26','profile':self.precise_profile(),'target':1}],config,2026,age=True)
        self.assertEqual(model.local_components(target)['near_identical_count'],1)
        target['projected_games'] = None
        self.assertEqual(model.local_components(target)['near_identical_count'],0)
        target['projected_games'] = 72
        config['comp_rules'].pop('near_identical')
        self.assertEqual(model.local_components(target)['near_identical_count'],0)

    def test_age_context_cannot_use_future_rows_or_guess_missing_players(self):
        sources = {s:{'url':'https://example.test/'+s,'sha256':'test','retrieved_at':'2026-10-01'}
                   for s in ['2023-24','2025-26']}
        data = {'version':1,'columns':COLUMNS,'sources':sources,
                'records':[['2023-24','p','provider-p',23],['2025-26','p','provider-p',25]]}
        payload = {**data,'sha256':digest(data)}
        context = MarketContext(payload)
        self.assertEqual(context.before('p','2025-26')['target_season_age'],25)
        self.assertEqual(context.before('p','2025-26')['season'],'2023-24')
        self.assertIsNone(context.before('p','2023-24'))
        self.assertIsNone(context.before('unknown','2026-27'))
        payload['records'][0][3]=99
        with self.assertRaisesRegex(ValueError,'integrity'):
            MarketContext(payload)

    def test_bundled_age_metadata_covers_verified_source_rows(self):
        context = load_context()
        self.assertEqual(context.metadata['rows'],6460)
        self.assertEqual(len(context.metadata['sources']),12)
        self.assertEqual(context.before('anthonyedwards','2026-27')['target_season_age'],25)
        self.assertEqual(context.before('jamesharden','2026-27')['target_season_age'],37)


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
        self.assertEqual(len(result['values']),348)
        self.assertEqual(sum(v['keeper_cost'] is not None for v in result['values']),30)
        for name in ('central','faster','slower'):
            dollars = [v['category_values']['scenario_values'][name] for v in result['values']]
            self.assertEqual(round(sum(dollars),2),3000)
            self.assertEqual(sum(v>=1 for v in dollars),225)
        self.assertTrue(all(v['fair_value']==v['category_values']['neutral_value'] for v in result['values']))
        by_id = {v['player_id']:v for v in result['values']}
        self.assertGreater(by_id['anthonyedwards']['fair_value'],by_id['jamesharden']['fair_value'])
        self.assertLess(by_id['dariusacuffjr']['fair_value'],by_id['anthonyedwards']['fair_value'])
        self.assertTrue(all(v['recommended_bid_ceiling'] is None for v in result['values']))
        self.assertEqual(self.before,fingerprint(rows(self.db,'SELECT * FROM player_stats ORDER BY dataset_id,player_id')))

    def test_chronological_market_validation_and_explicit_limits(self):
        result = self.result
        for r in result['backtest']:
            if r['forecast_dataset_id']:
                self.assertEqual(r['stats_season'],r['season'])
                self.assertIsNotNone(r['forecast_dataset_id'])
            else:
                self.assertLess(r['stats_season'],r['season'])
            self.assertLess(r['training_last_season'],r['season'])
            if r['age_source_season']:
                self.assertLess(r['age_source_season'],r['season'])
        v = result['validation']
        self.assertEqual(v['training_rows']+v['excluded_rows'],v['historical_sales'])
        self.assertEqual(v['excluded_rows'],v['unmatched_or_low_sample_rows']+v['preseason_excluded_rows']+v['missing_historical_projection_rows'])
        self.assertEqual(v['forecast_training_rows'],1203)
        self.assertEqual(v['forecast_training_by_season']['2025-26'],163)
        self.assertEqual(len(v['historical_projection_sources']),9)
        self.assertEqual(v['preseason_excluded_rows'],16)
        self.assertEqual(v['holdout'][v['selected_model']]['n'],468)
        self.assertLess(v['holdout'][v['selected_model']]['mae'],v['holdout']['mean']['mae'])
        self.assertIn('not independently validated',v['interpretation'])
        self.assertEqual(v['retention_audit']['bands']['top_30'],{'opening':40,'on_same_final_roster':27})
        self.assertIn('reused',v['model_selection_basis'])
        self.assertEqual(v['selected_model'],min(v['development'],key=lambda name:v['development'][name]['mae']))
        self.assertLess(v['holdout'][v['selected_model']]['mae'],v['holdout']['ridge']['mae'])

    def test_all_comps_weights_price_adjustments_and_age_fallback_reconcile(self):
        missing_age = 0
        for row in self.result['values']:
            market = row['category_values']['market']
            self.assertLessEqual(len(row['comps']),20)
            self.assertAlmostEqual(sum(c['weight'] for c in row['comps']),1 if row['comps'] else 0)
            strong = [c for c in row['comps'] if c['match_quality'] in ('strong','near_identical')]
            self.assertEqual(market['strong_comp_count'],len(strong))
            precise = [c for c in row['comps'] if c['match_quality']=='near_identical']
            self.assertEqual(market['near_identical_comp_count'],len(precise))
            if precise:
                self.assertGreaterEqual(sum(c['weight'] for c in precise),.95-1e-12)
                self.assertGreaterEqual(sum(c['final_price_weight'] for c in precise),.855-1e-12)
            if strong:
                self.assertGreaterEqual(sum(c['weight'] for c in strong),.8-1e-12)
            self.assertAlmostEqual(sum(c['price_adjustment'] for c in row['comps']),market['comp_adjustment'])
            self.assertAlmostEqual(sum(c['final_price_weight'] for c in row['comps'])+market['base_weight'],1)
            if row['comps']:
                self.assertAlmostEqual(market['comp_estimate'],sum(c['weight']*c['implied_price'] for c in row['comps']))
                blend = market['base_weight']*market['base_price']+market['correction_share']*market['comp_estimate']
                self.assertAlmostEqual(row['expected_auction_price'],round(min(200,max(1,blend)),2))
            expected = min(200,max(1,market['base_price']+market['comp_adjustment']))
            self.assertAlmostEqual(row['expected_auction_price'],round(expected,2))
            for comp in row['comps']:
                if comp['forecast_dataset_id']:
                    self.assertEqual(comp['stats_season'],comp['season'])
                    if comp['season']>='2024-25':
                        self.assertGreater(comp['projected_games'],0)
                    else:
                        self.assertIsNone(comp['projected_games'])
                else:
                    self.assertLess(comp['stats_season'],comp['season'])
                self.assertLess(comp['season'],self.result['season'])
                self.assertAlmostEqual(comp['price_adjustment'],market['correction_share']*comp['weight']*(comp['implied_price']-market['base_price']))
                self.assertAlmostEqual(comp['implied_price'],comp['budget_adjusted_price']+comp['profile_adjustment'])
                self.assertAlmostEqual(comp['profile_adjustment'],comp['stat_adjustment']+comp['age_adjustment']+comp['curve_adjustment'])
                self.assertEqual(comp['profile_curve_share'],0)
                self.assertEqual(comp['curve_adjustment'],0)
                self.assertAlmostEqual(comp['profile_adjustment']+comp['full_curve_adjustment'],market['base_price']-comp['model_price'])
                self.assertAlmostEqual(comp['implied_price'],comp['actual_price']+comp['cash_adjustment']+comp['supply_adjustment']+comp['profile_adjustment'])
                self.assertAlmostEqual(comp['final_price_weight'],comp['weight']*market['correction_share'])
                limits = settings()['comp_rules'][comp['match_quality']]
                for key in ('category_rms','production_gap','largest_category_gap','age_gap','games_gap'):
                    gap = comp['match_details'][key]
                    if gap is not None:
                        self.assertLessEqual(gap,limits[key])
                if comp['match_quality'] in ('strong','near_identical'):
                    self.assertIsNotNone(comp['forecast_dataset_id'])
                if comp['match_quality']=='near_identical':
                    self.assertIsNotNone(comp['projected_games'])
                    self.assertIsNotNone(comp['target_season_age'])
                    self.assertLessEqual(int(self.result['season'][:4])-int(comp['season'][:4]),3)
            if market['age_source'] is None:
                missing_age += 1
                self.assertFalse(market['age_in_model'])
                self.assertEqual(market['method'],'market_local' if market['supply_adjusted'] else 'local')
                self.assertIn('no invented age',row['risk_notes'])
        self.assertEqual(missing_age,16)
        markets=self.result['validation']['auction_contexts']
        self.assertEqual(len(markets),12)
        self.assertEqual(markets[-1]['remaining_budget'],2210)
        self.assertAlmostEqual(markets[-1]['average_team_budget'],2210/15)
        self.assertEqual(markets[-1]['open_slots'],195)
        for market in markets:
            self.assertEqual(len(market['teams']),15)
            if market['stats_season']:
                self.assertLess(market['stats_season'],market['season'])

    def test_ant_close_match_has_comparable_forecast_not_recent_lesser_production(self):
        ant = next(v for v in self.result['values'] if v['player_id']=='anthonyedwards')
        strong = [c for c in ant['comps'] if c['match_quality']=='strong']
        self.assertEqual([(c['player_id'],c['season']) for c in strong],[('bradleybeal','2020-21')])
        self.assertGreaterEqual(strong[0]['weight'],.8-1e-12)
        self.assertAlmostEqual(strong[0]['final_price_weight'],.6)
        self.assertEqual(ant['category_values']['market']['base_weight'],.25)
        self.assertEqual(strong[0]['remaining_budget'],2148)
        self.assertEqual(strong[0]['available_top_30'],13)
        self.assertEqual(ant['category_values']['market']['remaining_budget'],2210)
        self.assertEqual(ant['category_values']['market']['available_top_30'],17)
        self.assertGreater(strong[0]['cash_adjustment'],0)
        self.assertLess(strong[0]['supply_adjustment'],0)
        self.assertGreater(strong[0]['profile_adjustment'],0)
        self.assertGreater(strong[0]['stats']['pts_pg'],27)
        self.assertIsNone(strong[0]['projected_games'])
        miller = next(c for c in ant['comps'] if c['player_id']=='brandonmiller')
        self.assertEqual(miller['match_quality'],'supporting')
        self.assertLess(miller['weight'],.05)

    def test_brunson_prior_auction_dominates_and_keeper_cost_is_not_a_comp(self):
        brunson = next(v for v in self.result['values'] if v['player_id']=='jalenbrunson')
        precise = [c for c in brunson['comps'] if c['match_quality']=='near_identical']
        self.assertEqual([(c['player_id'],c['season']) for c in precise],[('jalenbrunson','2025-26')])
        self.assertEqual(precise[0]['actual_price'],45)
        self.assertEqual(brunson['keeper_cost'],46)
        self.assertAlmostEqual(precise[0]['weight'],.95)
        self.assertAlmostEqual(precise[0]['final_price_weight'],.855)
        self.assertGreater(brunson['expected_auction_price'],43)
        self.assertEqual(brunson['fair_value'],25.52)

    def test_luka_close_comp_keeps_elite_premium_and_transparent_adjustments(self):
        luka = next(v for v in self.result['values'] if v['player_id']=='lukadoncic')
        self.assertEqual(len(luka['comps']),1)
        comp = luka['comps'][0]
        self.assertEqual((comp['player_id'],comp['season'],comp['actual_price']),('lukadoncic','2024-25',65))
        self.assertAlmostEqual(comp['profile_adjustment'],-5.15,places=2)
        self.assertAlmostEqual(comp['full_curve_adjustment'],-8.31,places=2)
        self.assertAlmostEqual(comp['implied_price'],57.74,places=2)
        self.assertEqual(luka['expected_auction_price'],58.58)
        self.assertEqual(luka['fair_value'],59.95)

    def test_injury_exceptions_removed_from_fit_and_every_comp_but_history_preserved(self):
        from survivor.preseason import load_preseason_evidence
        _,evidence = load_preseason_evidence()
        for player in self.result['values']:
            self.assertTrue(all((c['season'],c['player_id']) not in evidence for c in player['comps']))
        for record in self.result['backtest']:
            self.assertNotIn((record['season'],record['player_id']),evidence)
        tatum = next(r for r in self.client.get('/api/players/jaysontatum').json['history'] if r['season']=='2025-26')
        self.assertEqual(tatum['recorded_cost'],23)
        self.assertEqual(tatum['acquisition_class'],'auction')
        excluded = {(r['season'],r['player_id']):r for r in self.result['excluded']}
        self.assertEqual(excluded['2025-26','jaysontatum']['reason_code'],'preseason_availability')
        self.assertEqual(excluded['2019-20','klaythompson']['reason_code'],'preseason_availability')
        self.assertNotIn(('2025-26','klaythompson'),excluded)

    def test_supplied_archive_visible_with_no_invented_minutes_positions_or_zero_forecasts(self):
        data=self.client.get('/api/bootstrap').json
        archive=next(d for d in data['datasets'] if d.get('coverage')=='partial_player_pool' and d['season']=='2025-26')
        supplied=self.client.get('/api/projections?dataset='+archive['dataset_id']).json
        self.assertEqual(len(supplied['rows']),200)
        self.assertEqual(supplied['dataset']['date_basis'],'received')
        ant=next(p for p in supplied['rows'] if p['player_id']=='anthonyedwards')
        self.assertEqual(ant['games'],79)
        self.assertAlmostEqual(ant['pts_pg'],2209/79)
        self.assertAlmostEqual(ant['fgm_pg'],741/79)
        self.assertIsNone(ant['minutes_pg'])
        self.assertIsNone(ant['positions'])
        self.assertIsNone(ant.get('fair_value'))
        self.assertNotIn('jaysontatum',{p['player_id'] for p in supplied['rows']})
        self.assertTrue(all(p['draft_status']=='unknown' for p in supplied['rows']))

    def test_training_really_uses_the_supplied_outlook(self):
        from survivor.valuation import load_inputs, training_observations
        from survivor.historical_projections import load_archive
        _,actuals,stats,sales,keepers,draft=load_inputs(self.db,'2026-27')
        archive=load_archive()
        records={r['player_id']:r for r in archive['records']}
        forecasts={archive['season']:{'metadata':archive,'players':records}}
        rules=self.result['settings']['league_rules']
        observations,excluded=training_observations(actuals,stats,sales,keepers,rules,settings(),load_context(),forecasts=forecasts)
        row=next(o for o in observations if o['season']=='2025-26' and o['player_id']=='laurimarkkanen')
        prior,_=profiles(stats[actuals['2024-25']['dataset_id']],pool_size=225,minimum_games=10)
        self.assertNotEqual(row['profile']['z'],prior[row['player_id']]['z'])
        self.assertEqual(row['profile']['projected_games'],records[row['player_id']]['games'])
        self.assertEqual(row['forecast_dataset_id'],archive['dataset_id'])
        self.assertEqual(sum(o['outlook_basis']=='supplied_projection' for o in observations),163)
        self.assertTrue(all(o['season']!='2025-26' or o['outlook_basis']=='supplied_projection' for o in observations))

    def test_published_forecasts_join_their_auction_season_and_leave_unknown_games_empty(self):
        from survivor.historical_projections import bundled_archives
        from survivor.valuation import load_inputs, training_observations
        archives=bundled_archives()
        forecasts={a['season']:{'metadata':a,'players':{r['player_id']:r for r in a['records']}} for a in archives}
        _,actuals,stats,sales,keepers,_=load_inputs(self.db,'2026-27')
        observations,excluded=training_observations(actuals,stats,sales,keepers,self.result['settings']['league_rules'],settings(),load_context(),forecasts=forecasts)
        rejected={(a['season'],r['player_id']) for a in archives for r in a.get('rejected_records',[])}
        self.assertTrue(all((o['season'],o['player_id']) not in rejected for o in observations))
        for row in observations:
            if row['season']<'2017-18':
                self.assertEqual(row['outlook_basis'],'prior_actuals_proxy')
                continue
            archive=forecasts[row['season']]
            source=archive['players'][row['player_id']]
            self.assertEqual(row['forecast_dataset_id'],archive['metadata']['dataset_id'])
            self.assertEqual(row['profile']['projected_games'],source['games'])
            self.assertEqual(row['stats_season'],row['season'])
            if row['season']<'2024-25':
                self.assertIsNone(row['stats_games'])
        for season,count in [('2018-19',154),('2024-25',285)]:
            result=self.client.get('/api/projections?season='+season).json
            self.assertEqual(len(result['rows']),count)
            self.assertEqual(result['dataset']['date_basis'],'published')
            self.assertTrue(result['dataset']['source_url'].startswith('https://'))
            self.assertTrue(all(r.get('fair_value') is None for r in result['rows']))
        per_game=self.client.get('/api/projections?season=2018-19&sort=games&direction=desc').json
        self.assertTrue(all(r['games'] is None for r in per_game['rows']))

    def test_board_sort_filter_and_csv_agree(self):
        for route in ('valuations','projections'):
            query='?position=SG&availability=available&sort=fg_pct&direction=asc&q=a'
            result=self.client.get('/api/'+route+query).json['rows']
            self.assertTrue(result)
            self.assertTrue(all('SG' in r['positions'].split(',') and r['draft_status']=='available' for r in result))
            present=[r['fg_pct'] for r in result if r['fg_pct'] is not None]
            self.assertEqual(present,sorted(present))
            exported=list(csv.DictReader(io.StringIO(self.client.get('/api/'+route+'.csv'+query).data.decode('utf-8-sig'))))
            self.assertEqual([r.get('Player',r.get('player')) for r in exported],[r['player'] for r in result])
            desc=self.client.get('/api/'+route+'?position=SG&availability=available&sort=fg_pct&direction=desc&q=a').json['rows']
            self.assertEqual([r['fg_pct'] for r in desc if r['fg_pct'] is not None],list(reversed(present)))
        history=self.client.get('/api/history?season=2025-26&position=PG&limit=5&sort=price&direction=asc').json
        self.assertGreater(history['total'],5)
        exported=list(csv.DictReader(io.StringIO(self.client.get('/api/history.csv?season=2025-26&position=PG&sort=price&direction=asc').data.decode('utf-8-sig'))))
        self.assertEqual(len(exported),history['total'])
        self.assertEqual([r['Player'] for r in exported[:5]],[r['player'] for r in history['rows']])
        for route in ('projections','valuations','history'):
            self.assertEqual(self.client.get('/api/'+route+'?position=bad').status_code,400)

    def test_run_atomic_idempotent_and_immutable(self):
        save_run(self.db,self.result)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM valuation_runs').fetchone()[0],1)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM projected_values').fetchone()[0],348)
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
        self.assertEqual(len(payload['rows']),348)
        self.assertNotIn('comps_json',payload['rows'][0])
        detail = self.client.get('/api/players/anthonyedwards').json
        self.assertEqual(len(json.loads(detail['valuations'][0]['comps_json'])),20)
        self.assertEqual(len(self.client.get('/api/valuations?availability=available').json['rows']),318)
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
