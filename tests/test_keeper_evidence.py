import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from survivor.catalog import ROOT,build_catalog,rows
from survivor.database import preview_database
from survivor.keepers import sync_bundled_keepers
from survivor.keeper_evidence import combine_support,keeper_adjustment,eligible_cost,sync_keeper_claims


RULES={'maximum_share':.4,'repeat_share':.25,'maximum_seasons_ago':3,'scope':'same_player',
       'supporting_share':.35,'claim_share':.5,'auction_overlap_share':.5}


def record(cost=70,owner='A',season='2025-26',kind='keeper',reliability=1):
    return {'player_id':'player','franchise':owner,'season':season,'kind':kind,'adjusted_cost':cost,
            'auction_origin':'2023-24','reliability':reliability}


class KeeperWeightTests(unittest.TestCase):
    def test_bargains_cannot_pull_price_down_and_no_evidence_leaves_price_alone(self):
        for evidence in ([],[record(30)],[record(50)]):
            result=combine_support(50,evidence,RULES)
            self.assertEqual((result['adjustment'],result['share']),(0,0))
            self.assertIsNone(result['signal'])

    def test_repeated_support_grows_with_diminishing_returns_and_stays_secondary(self):
        prices=[]
        for n in range(1,10):
            result=combine_support(50,[record(owner=str(i)) for i in range(n)],RULES)
            prices.append(result['adjustment'])
            self.assertLess(result['share'],.4)
            self.assertLess(50+result['adjustment'],70)
        self.assertEqual(prices,sorted(prices))
        increments=[b-a for a,b in zip([0]+prices,prices)]
        self.assertEqual(increments,sorted(increments,reverse=True))
        repeated=combine_support(50,[record(),record(season='2024-25')],RULES)
        independent=combine_support(50,[record(),record(owner='B')],RULES)
        self.assertGreater(repeated['adjustment'],prices[0])
        self.assertLess(repeated['adjustment'],independent['adjustment'])

    def test_cheaper_claim_only_strengthens_its_supported_price_range(self):
        original=combine_support(50,[record()],RULES)
        result=combine_support(50,[record(),record(60,'B',kind='claim',reliability=.5)],RULES)
        self.assertGreater(result['adjustment'],original['adjustment'])
        self.assertEqual(result['intervals'][-1],{'from':60,'to':70,'strength':1,'price_effect':2.0})
        self.assertEqual((result['supporting_decisions'],result['supporting_claims']),(1,1))
        stronger=combine_support(50,[record(),record(70,'B',kind='claim',reliability=.5)],RULES)
        self.assertLess(result['adjustment'],stronger['adjustment'])

    def test_only_dated_same_player_forecasts_qualify_and_duplicates_do_not_add_weight(self):
        class Model:
            age=True
            observations=[{'player_id':'player','season':'2023-24'}]
            settings={'recency_decay':.9}
            def match(self,*args,**kwargs):return {'match_quality':'strong','distance':0}
            def profile_adjustment(self,*args):return {'total':0}
        profile={'age':26,'stats':{},'projected_games':72}
        market={'market_scale':10,'discretionary_per_slot':10}
        base={'season':'2025-26','player_id':'player','player':'Player','franchise_sheet':'A',
              'profile':profile,'forecast_dataset_id':'source','auction_context':market,'recorded_cost':70,'auction_origin':'2023-24'}
        excluded=[{**base,'season':'2027-28'},{**base,'season':'2022-23'},
                  {**base,'forecast_dataset_id':None},{**base,'player_id':'someoneelse'},
                  {**base,'profile':{**profile,'age':None}}]
        result=keeper_adjustment(Model(),'player',profile,'2026-27',market,50,[base,copy.deepcopy(base),*excluded],RULES,[{'index':0}])
        self.assertEqual(len(result['records']),1)
        self.assertTrue(result['records'][0]['auction_overlap'])
        self.assertAlmostEqual(result['records'][0]['reliability'],.45)
        self.assertEqual(result['records'][0]['adjusted_cost'],70)


class ClaimImportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db=preview_database()
        build_catalog(ROOT/'Survivor keeper log 2025.xlsx',cls.db)
        sync_bundled_keepers(cls.db)
        cls.payload=json.loads((ROOT/'config/keeper_claims/2026-27.json').read_text(encoding='utf-8'))

    @classmethod
    def tearDownClass(cls):cls.db.close()

    def tearDown(self):sync_keeper_claims(self.db)

    def import_payload(self,payload):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'config/keeper_claims'
            path.mkdir(parents=True)
            (path/'2026-27.json').write_text(json.dumps(payload),encoding='utf-8')
            with patch('survivor.keeper_evidence.ROOT',Path(folder)):
                sync_keeper_claims(self.db)

    def test_claimants_get_their_own_contract_costs_and_import_is_idempotent(self):
        before=rows(self.db,'SELECT * FROM keeper_claims ORDER BY franchise,choice_rank')
        self.assertEqual(len(before),13)
        lookup={(r['franchise'],r['player_id']):r for r in before}
        for player,price in [('lukadoncic',67),('nikolajokic',86),('cadecunningham',37),('scottiebarnes',22)]:
            claim=lookup['Alvin',player]
            self.assertEqual(claim['eligible_cost'],price)
            details=json.loads(claim['details_json'])
            self.assertEqual(details['price_status'],'derived')
            self.assertTrue(details['source_workbook_sha256'])
            self.assertTrue(details['prior_cost_cell'])
            self.assertEqual(eligible_cost(details['prior_cost'],details['prior_contract_year']),price)
        sync_keeper_claims(self.db)
        self.assertEqual(before,rows(self.db,'SELECT * FROM keeper_claims ORDER BY franchise,choice_rank'))

    def test_unknown_contract_stays_unpriced_and_does_not_become_winner_cost(self):
        def missing_contract(db,sql,parameters=()):
            if 'FROM roster_history' in sql:return []
            return rows(db,sql,parameters)
        with patch('survivor.keeper_evidence.rows',side_effect=missing_contract):
            self.import_payload(self.payload)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM keeper_claims WHERE eligible_cost IS NULL').fetchone()[0],13)

    def test_historical_claim_logs_can_join_historical_keeper_selections(self):
        payload=copy.deepcopy(self.payload)
        payload['season']='2025-26'
        payload['claims']=[next(r for r in payload['claims'] if r['player']=='Luka Doncic')]
        try:
            self.import_payload(payload)
            stored=rows(self.db,"SELECT * FROM keeper_claims WHERE season='2025-26'")
            self.assertEqual(len(stored),1)
            self.assertEqual(stored[0]['franchise'],'Alvin')
            self.assertEqual(stored[0]['player_id'],'lukadoncic')
        finally:
            with self.db:
                self.db.execute("DELETE FROM keeper_claims WHERE season='2025-26'")
                self.db.execute("DELETE FROM keeper_claim_sources WHERE season='2025-26'")

    def test_invalid_explicit_price_duplicates_and_rank_rejected_without_partial_write(self):
        before=rows(self.db,'SELECT * FROM keeper_claims ORDER BY franchise,choice_rank')
        invalid=[]
        for cost in (True,-1,201,2.5,199):
            p=copy.deepcopy(self.payload);p['claims'][0]['eligible_cost']=cost;invalid.append(p)
        p=copy.deepcopy(self.payload);p['claims'].append(p['claims'][0]);invalid.append(p)
        p=copy.deepcopy(self.payload);p['claims'][0]['choice_rank']=0;invalid.append(p)
        p=copy.deepcopy(self.payload);p['claims'][0]['status']='unreached';invalid.append(p)
        for payload in invalid:
            with self.assertRaises(ValueError):self.import_payload(payload)
            self.assertEqual(before,rows(self.db,'SELECT * FROM keeper_claims ORDER BY franchise,choice_rank'))

    def test_rule_is_bounded_by_observed_contract_years(self):
        self.assertEqual([eligible_cost(10,y)-10 for y in range(1,6)],[1,3,6,10,15])
        for year in (0,6):
            with self.assertRaises(ValueError):eligible_cost(10,year)


if __name__=='__main__':unittest.main()
