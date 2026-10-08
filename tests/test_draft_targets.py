import json
import unittest
from survivor.draft_targets import CATEGORIES,target_metrics,target_matches


def player(neutral=30,expected=25,z=None):
    return {'fair_value':neutral,'expected_auction_price':expected,
            'category_values_json':json.dumps({'rate_z':dict.fromkeys(CATEGORIES,.5) if z is None else z})}


class DraftTargetTests(unittest.TestCase):
    def test_prices_use_dollar_and_relative_gaps_without_changing_saved_inputs(self):
        cases=[(30,30,'even'),(30,27,'solid'),(30,22,'elite'),(30,33,'slight_over'),(30,38,'overvalued'),
               (100,92,'even'),(100,90,'solid'),(100,76,'solid'),(100,75,'elite'),
               (2,1,'even'),(0,1,'even'),(10,7.01,'even'),(10,7,'solid')]
        for neutral,expected,tier in cases:
            with self.subTest(neutral=neutral,expected=expected):
                row=player(neutral,expected);before=dict(row)
                metrics=target_metrics(row)
                self.assertEqual(metrics['value_tier'],tier)
                self.assertAlmostEqual(metrics['value_gap'],round(neutral-expected,2))
                self.assertEqual(row,before)

    def test_missing_values_and_profiles_remain_unknown(self):
        for missing in (None,float('nan'),float('inf')):
            self.assertEqual(target_metrics(player(expected=missing))['value_tier'],'unrated')
        incomplete=target_metrics(player(z={'FG%':1,'FT%':1}))
        self.assertIsNone(incomplete['positive_categories'])
        self.assertFalse(target_matches(incomplete,profile='percentages'))
        self.assertIsNone(target_metrics(player(neutral=0,expected=1))['value_gap_pct'])

    def test_shooting_uses_volume_weighted_impacts_and_both_must_help(self):
        z=dict.fromkeys(CATEGORIES,.5);z['FT%']=-.01;z['FG%']=3
        row=target_metrics(player(z=z))
        self.assertEqual(row['positive_categories'],7)
        self.assertEqual(row['shooting_floor'],-.01)
        self.assertFalse(row['both_percentages_positive'])
        self.assertFalse(target_matches(row,profile='balanced'))
        z['FT%']=.2;z['BLK']=-.01
        row=target_metrics(player(z=z))
        self.assertTrue(target_matches(row,profile='balanced'))
        self.assertFalse(target_matches(row,profile='all_eight'))
        z['BLK']=0
        self.assertFalse(target_metrics(player(z=z))['all_categories_positive'])
        z['BLK']=.01
        self.assertTrue(target_metrics(player(z=z))['all_categories_positive'])

    def test_bargain_color_and_category_balance_are_independent(self):
        cheap=target_metrics(player(30,15,dict.fromkeys(CATEGORIES,-.1)))
        expensive=target_metrics(player(30,45))
        self.assertTrue(target_matches(cheap,value='undervalued'))
        self.assertFalse(target_matches(cheap,value='undervalued',profile='all_eight'))
        self.assertTrue(target_matches(expensive,value='overvalued',profile='all_eight'))


if __name__=='__main__':unittest.main()
