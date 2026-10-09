"""Calendar boundaries, scoring time and integration into survivor values."""
import copy
import json
from pathlib import Path
import unittest

from survivor.schedule import calendar_bounds, load_calendar, scoring_stages
from survivor.valuation import survivor_scores

ROOT = Path(__file__).resolve().parents[1]


class ScheduleTests(unittest.TestCase):
    def setUp(self):
        self.settings = json.loads((ROOT/'config/valuation.json').read_text(encoding='utf-8'))
        self.calendar = load_calendar('2026-27')
        self.settings['survivor']['calendar'] = self.calendar
        self.central = next(s for s in self.settings['survivor']['scenarios'] if s['name'] == 'central')

    def stages(self):
        return scoring_stages(15, self.settings['survivor'], self.central)

    def test_all_confirmed_cuts_start_monday_after_sunday_scoring(self):
        stages, report = self.stages()
        self.assertEqual(len(stages), 14)
        self.assertEqual([s['teams'] for s in stages], list(range(15, 1, -1)))
        self.assertEqual(stages[0]['start_date'], '2026-10-20')
        self.assertEqual(stages[0]['last_scoring_date'], '2026-11-29')
        self.assertEqual(stages[0]['next_cut_date'], '2026-11-30')
        self.assertEqual(stages[0]['active_days'], 41)
        for previous, following, cut in zip(stages, stages[1:], self.calendar['cuts']):
            self.assertEqual(previous['next_cut_date'], cut['date'])
            self.assertEqual(following['start_date'], cut['date'])
        self.assertEqual(stages[-1]['start_date'], '2027-03-29')
        self.assertEqual(stages[-1]['last_scoring_date'], '2027-04-11')
        self.assertEqual(stages[-1]['active_days'], 14)
        self.assertIsNone(stages[-1]['next_cut_date'])
        self.assertEqual(report['schedule_basis'], 'confirmed_dates')

    def test_all_star_break_has_no_projected_games_and_days_reconcile(self):
        stages, report = self.stages()
        self.assertEqual(report['season_days'], 174)
        self.assertEqual(report['active_days'], 167)
        self.assertEqual(report['all_star_break_days'], 7)
        affected = next(s for s in stages if s['start_date'] == '2027-02-15')
        self.assertEqual((affected['calendar_days'], affected['active_days']), (14, 7))
        self.assertAlmostEqual(affected['fraction'], 7/167)
        self.assertEqual(sum(s['calendar_days'] for s in stages), 174)
        self.assertEqual(sum(s['active_days'] for s in stages), 167)
        self.assertAlmostEqual(sum(s['fraction'] for s in stages), 1)

    def test_tie_policy_preserves_original_sunday_cohort_without_invented_delays(self):
        _, report = self.stages()
        policy = report['tie_policy']
        self.assertEqual(policy['extension_days'], 1)
        self.assertIn('originally', policy['eligible_teams'])
        self.assertIn('safe', policy['later_lower_teams'])
        self.assertIn('not predicted', report['tie_delay_basis'])

    def test_invalid_calendar_rejected(self):
        for mutation in ('missing_cut', 'duplicate_date', 'wrong_place', 'non_monday', 'outside_season', 'break', 'unconfirmed', 'after_games'):
            with self.subTest(mutation=mutation):
                cal = copy.deepcopy(self.calendar)
                if mutation == 'missing_cut': cal['cuts'].pop()
                elif mutation == 'duplicate_date': cal['cuts'][1]['date'] = cal['cuts'][0]['date']
                elif mutation == 'wrong_place': cal['cuts'][0]['place'] = 14
                elif mutation == 'non_monday': cal['cuts'][0]['date'] = '2026-12-01'
                elif mutation == 'outside_season': cal['cuts'][-1]['date'] = '2027-04-12'
                elif mutation == 'break': cal['all_star_break']['end_date'] = '2027-02-17'
                elif mutation == 'unconfirmed': cal['status'] = 'pending_clarification'
                else: cal['cut_effective_timing'] = 'after_games'
                with self.assertRaises(ValueError): calendar_bounds(cal, 15)

    def test_sensitivities_share_the_same_season_and_break_but_are_labeled_hypothetical(self):
        for scenario in self.settings['survivor']['scenarios']:
            if scenario['name'] == 'central': continue
            stages, report = scoring_stages(15, self.settings['survivor'], scenario)
            self.assertEqual(report['schedule_basis'], 'hypothetical_sensitivity')
            self.assertEqual(report['active_days'], 167)
            self.assertAlmostEqual(sum(s['fraction'] for s in stages), 1)
            self.assertAlmostEqual(sum(s['active_days'] for s in stages), 167)

    def test_scoring_uses_exact_stage_lengths_and_preserves_game_move_limits(self):
        players = [{'player_id':str(i), 'games':82} for i in range(230)]
        scored = {str(i):{'score':230-i} for i in range(230)}
        rules = {'teams':15, 'games_or_start_limits':1000, 'roster_move_limit':100}
        values, report = survivor_scores(players, scored, rules, self.settings, self.central)
        # Player 220 is useful only before the first cut, with +5 above replacement.
        expected_games = 82*(41/167)*(1000/(15*82))
        self.assertAlmostEqual(values['220']['useful_games'], expected_games)
        self.assertAlmostEqual(values['220']['score'], 5*expected_games/82)
        self.assertAlmostEqual(report['neutral_games_per_team'], 1000)
        self.assertEqual(report['stages'][-1]['replacement_depth'], 30)
        self.assertLessEqual(report['expected_moves_used'], 100)
        self.assertAlmostEqual(values['0']['useful_season_fraction'], 1)
        rules.update(games_or_start_limits=800, roster_move_limit=0)
        values, report = survivor_scores(players, scored, rules, self.settings, self.central)
        self.assertTrue(all(s['replacement_depth'] == 225 for s in report['stages']))
        self.assertEqual(report['expected_moves_used'], 0)
        self.assertAlmostEqual(report['neutral_games_per_team'], 800)

    def test_bad_hypothetical_schedule_cannot_remove_two_team_final(self):
        with self.assertRaisesRegex(ValueError, 'two-team final'):
            scoring_stages(15, self.settings['survivor'], {'name':'invalid', 'first_cut_week':6, 'cut_interval_weeks':2})


if __name__ == '__main__':
    unittest.main()
