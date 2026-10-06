import copy
import json
import unittest
from unittest.mock import patch

from survivor.catalog import ROOT, build_catalog
from survivor.database import preview_database
from survivor.keepers import keeper_summary, sync_bundled_keepers, sync_keepers


class KeeperTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = preview_database()
        build_catalog(ROOT / 'Survivor keeper log 2025.xlsx', cls.db)
        cls.payload = json.loads((ROOT / 'config/keepers/2026-27.json').read_text(encoding='utf-8'))
        sync_bundled_keepers(cls.db)

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def tearDown(self):
        sync_keepers(self.db, self.payload)

    def test_confirmed_costs_ownership_and_budgets_match_supplied_list(self):
        expected = {
            'Benji': (159, [('Jalen Johnson', 39), ('Jeremiah Fears', 2)]),
            'Jason': (132, [('Cooper Flagg', 38), ("De'Aaron Fox", 30)]),
            'Mark': (165, [('Trey Murphy III', 11), ('Deni Avdija', 24)]),
            'Ian': (170, [('Jayson Tatum', 24), ('Keyonte George', 6)]),
            'Joe': (179, [('Kon Knueppel', 5), ('Tyler Herro', 16)]),
            'Isaac': (136, [('Jalen Brunson', 46), ('Darius Garland', 18)]),
            'Max': (109, [('Dejounte Murray', 3), ('Nikola Jokic', 88)]),
            'Brent': (151, [('Walker Kessler', 25), ('Scottie Barnes', 24)]),
            'Ron': (157, [('Cade Cunningham', 39), ('Dylan Harper', 4)]),
            'Arthur': (135, [('Victor Wembanyama', 49), ('Tyrese Haliburton', 16)]),
            'Darren': (143, [('Tyrese Maxey', 42), ('Desmond Bane', 15)]),
            'Kerry': (112, [('Luka Doncic', 69), ('Chet Holmgren', 19)]),
            'Peter': (151, [('Austin Reaves', 19), ('Jamal Murray', 30)]),
            'Alvin': (182, [('Damian Lillard', 2), ('Donovan Clingan', 16)]),
            'Graham': (129, [('Shai Gilgeous-Alexander', 50), ('Jalen Duren', 21)]),
        }
        summary = keeper_summary(self.db, '2026-27')
        self.assertEqual((summary['keeper_count'], summary['keeper_spend'], summary['remaining_budget']), (30, 790, 2210))
        self.assertEqual(summary['league_budget'], 3000)
        for team in summary['teams']:
            budget, keepers = expected[team['franchise']]
            self.assertEqual(team['remaining_budget'], budget)
            selected = [(r['raw_name'], r['keeper_cost']) for r in summary['rows'] if r['franchise'] == team['franchise']]
            self.assertEqual(selected, keepers)
        # Every identity joins to existing history, without creating duplicate players.
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM players').fetchone()[0], 641)

    def test_redeploy_is_idempotent_and_history_is_not_extended(self):
        before = keeper_summary(self.db, '2026-27')
        self.assertFalse(sync_keepers(self.db, self.payload))
        self.assertEqual(keeper_summary(self.db, '2026-27'), before)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM auction_sales').fetchone()[0], 2173)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM keeper_costs').fetchone()[0], 300)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM seasons').fetchone()[0], 11)
        self.assertIsNone(keeper_summary(self.db, '2030-31'))

    def test_correction_changes_only_that_seasons_keeper_list(self):
        revised = copy.deepcopy(self.payload)
        revised['teams'][6]['keepers'][1]['cost'] = 89
        self.assertTrue(sync_keepers(self.db, revised))
        max_team = next(t for t in keeper_summary(self.db, '2026-27')['teams'] if t['franchise']=='Max')
        self.assertEqual(max_team['remaining_budget'], 108)
        historical = self.db.execute("SELECT recorded_cost FROM keeper_costs WHERE season='2025-26' AND player_id='nikolajokic'").fetchone()[0]
        self.assertEqual(historical, 85)

    def test_reject_invalid_or_incomplete_lists_before_changing_data(self):
        invalid = []
        for cost in (-1, 201, 1.5, True):
            data = copy.deepcopy(self.payload)
            data['teams'][0]['keepers'][0]['cost'] = cost
            invalid.append(data)
        data = copy.deepcopy(self.payload)
        data['teams'][0]['keepers'][0]['player'] = 'Nikola Jokić'
        invalid.append(data)
        data = copy.deepcopy(self.payload)
        data['teams'][0]['keepers'].pop()
        invalid.append(data)
        data = copy.deepcopy(self.payload)
        data['teams'].pop()
        invalid.append(data)
        data = copy.deepcopy(self.payload)
        data['teams'][0]['franchise'] = 'Max'
        invalid.append(data)
        for payload in invalid:
            with self.subTest(payload=payload['teams'][0]):
                with self.assertRaises(ValueError):
                    sync_keepers(self.db, payload)
                self.assertEqual(keeper_summary(self.db, '2026-27')['keeper_spend'], 790)

    def test_failed_sync_rolls_back_entire_list(self):
        revised = copy.deepcopy(self.payload)
        revised['teams'][6]['keepers'][1]['cost'] = 89
        before = keeper_summary(self.db, '2026-27')
        with patch('survivor.keepers.register_player', side_effect=RuntimeError('simulated import failure')):
            with self.assertRaises(RuntimeError):
                sync_keepers(self.db, revised)
        self.assertEqual(keeper_summary(self.db, '2026-27'), before)


if __name__ == '__main__':
    unittest.main()
