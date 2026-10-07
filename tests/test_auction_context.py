import unittest
from survivor.auction_context import auction_context, market_observations


class AuctionContextTests(unittest.TestCase):
    def context(self, keeper='0', cost=10):
        players=[{'player_id':str(i),'player':'Player '+str(i),'games':82,'minutes_pg':30} for i in range(9)]
        scores={p['player_id']:{'score':30 if i==0 else 9-i} for i,p in enumerate(players)}
        return auction_context('2026-27',players,scores,
            [{'player_id':keeper,'franchise_sheet':'A','recorded_cost':cost}],['A','B'],
            {'teams':2,'auction_budget':200},{'roster_size':3,'minimum_bid':1,'historical_minimum_games':10})

    def test_same_money_fewer_elite_players_increases_dollars_per_talent(self):
        elite_kept,fringe_kept=self.context('0'),self.context('7')
        self.assertEqual(elite_kept['remaining_budget'],fringe_kept['remaining_budget'])
        self.assertEqual(elite_kept['discretionary_per_slot'],fringe_kept['discretionary_per_slot'])
        self.assertGreater(elite_kept['market_scale'],fringe_kept['market_scale'])
        self.assertNotIn('0',{p['player_id'] for p in elite_kept['available_top_players']})

    def test_less_money_decreases_market_price_scale_and_team_reserves(self):
        cheap,expensive=self.context(cost=10),self.context(cost=100)
        self.assertEqual(cheap['available_strength'],expensive['available_strength'])
        self.assertGreater(cheap['market_scale'],expensive['market_scale'])
        self.assertEqual(cheap['average_team_budget'],195)
        self.assertEqual(cheap['teams'][0]['maximum_opening_bid'],189)
        with self.assertRaises(ValueError):
            self.context(cost=200)

    def test_bid_conversion_reconciles_and_does_not_mutate_observations(self):
        row={'target':3,'scale':10,'auction_context':{'market_scale':12}}
        normalized=market_observations([row])[0]
        self.assertEqual(normalized['target'],2.5)
        self.assertEqual(normalized['target']*normalized['scale'],30)
        self.assertEqual(row['target'],3)
