import copy
import unittest
from survivor.espn_projections import parse_espn


def fixture():
    # Synthetic adapter fixture; never exported to the published data files.
    return {'players':[{'player':{'id':1,'fullName':'Test Player','proTeamId':7,'eligibleSlots':[4],
        'stats':[{'id':'102027','externalId':'2027','seasonId':2027,'statSourceId':1,
                  'statSplitTypeId':0,'scoringPeriodId':0,
                  'stats':{'42':50,'40':1500,'0':850,'6':400,'3':150,'2':50,'1':50,
                           '13':350,'14':700,'15':150,'16':200,'19':.5,'20':.75}}]}}]}


class ESPNProjectionTests(unittest.TestCase):
    def test_totals_shooting_volume_and_sparse_zero_representation(self):
        row=parse_espn(fixture(),'2026-27',{7:'DEN'})[0]
        self.assertEqual(row['fg3m_pg'],0)
        self.assertEqual(row['sparse_zero_stat_ids'],'17')
        self.assertEqual((row['pts_pg'],row['fgm_pg'],row['fga_pg'],row['ftm_pg'],row['fta_pg']),(17,7,14,3,4))

    def test_excludes_actual_wrong_season_and_short_term_splits(self):
        for key,value in [('seasonId',2026),('statSourceId',0),('statSplitTypeId',1),('scoringPeriodId',1),('externalId','2026')]:
            data=fixture()
            data['players'][0]['player']['stats'][0][key]=value
            self.assertEqual(parse_espn(data,'2026-27',{7:'DEN'}),[])

    def test_missing_projection_is_never_created_and_bad_rows_fail(self):
        data=fixture()
        data['players'][0]['player']['stats'][0]['stats']={}
        self.assertEqual(parse_espn(data,'2026-27',{7:'DEN'}),[])
        for key,value in [('42',83),('14',10),('0',1),('40',None)]:
            data=fixture()
            data['players'][0]['player']['stats'][0]['stats'][key]=value
            with self.assertRaises(ValueError):
                parse_espn(data,'2026-27',{7:'DEN'})
        data=fixture()
        data['players'].append(copy.deepcopy(data['players'][0]))
        with self.assertRaisesRegex(ValueError,'Duplicate'):
            parse_espn(data,'2026-27',{7:'DEN'})
