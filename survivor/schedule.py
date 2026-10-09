"""Confirmed league calendar and neutral scoring time between eliminations."""
from datetime import date, timedelta
import json
import math
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def load_calendar(season):
    if not re.fullmatch(r'20\d{2}-\d{2}', season):
        raise ValueError('Invalid calendar season.')
    calendar = json.loads((ROOT/'config/season_schedules'/f'{season}.json').read_text(encoding='utf-8'))
    if calendar['season'] != season:
        raise ValueError('Calendar season does not match the valuation season.')
    return calendar


def calendar_bounds(calendar, teams):
    if calendar.get('status') != 'confirmed' or calendar.get('cut_effective_timing') != 'before_games':
        raise ValueError('A confirmed calendar with cuts before games is required.')
    start = date.fromisoformat(calendar['season_start_date'])
    end = date.fromisoformat(calendar['season_end_date']) + timedelta(days=1)
    if not date.fromisoformat(calendar['draft_date']) < start < end:
        raise ValueError('Draft and season dates do not reconcile.')
    cuts = calendar['cuts']
    if [c['place'] for c in cuts] != list(range(teams, 2, -1)):
        raise ValueError('Calendar must have one ordered cut per place, leaving two teams.')
    dates = [date.fromisoformat(c['date']) for c in cuts]
    if (not dates or any(d.weekday() != 0 for d in dates)
            or any(a >= b for a, b in zip([start] + dates, dates + [end]))):
        raise ValueError('Monday cuts must be ordered within the season and leave a two-team final.')
    pause = calendar['all_star_break']
    pause_start = date.fromisoformat(pause['start_date'])
    pause_end = date.fromisoformat(pause['end_date']) + timedelta(days=1)
    if not start <= pause_start < pause_end <= end:
        raise ValueError('All-Star break must be an inclusive range within the season.')
    return start, end, dates, (pause_start, pause_end)


def scoring_stages(teams, settings, scenario):
    """Use half-open boundaries: the old roster scores Sunday, the cut starts Monday.

    Published GP is spread over days outside the supplied All-Star break. This
    remains a neutral approximation, not a team-by-team NBA game schedule. The
    dated baseline assumes no unresolved bottom tie; no tie duration is guessed.
    """
    calendar = settings.get('calendar') or load_calendar(settings['calendar_season'])
    start, end, dates, pause = calendar_bounds(calendar, teams)
    days = (end-start).days
    pause_start, pause_end = ((d-start).days for d in pause)
    active_total = days-(pause_end-pause_start)
    dated = scenario.get('schedule') == 'league_calendar'
    if dated:
        cuts = [(d-start).days for d in dates]
    else:
        cuts = [7*(scenario['first_cut_week']+i*scenario['cut_interval_weeks']) for i in range(teams-2)]
    if (not cuts or not all(math.isfinite(d) for d in cuts) or cuts[0] <= 0
            or any(a >= b for a, b in zip(cuts, cuts[1:])) or cuts[-1] >= days):
        raise ValueError('Elimination scenario must leave time for a two-team final.')
    stages = []
    for index, (low, high) in enumerate(zip([0]+cuts, cuts+[days])):
        overlap = max(0, min(high, pause_end)-max(low, pause_start))
        active = high-low-overlap
        stages.append({'teams':teams-index, 'start_week':low/7, 'end_week':high/7,
                       'start_date':(start+timedelta(days=low)).isoformat() if dated else None,
                       'last_scoring_date':(start+timedelta(days=high-1)).isoformat() if dated else None,
                       'next_cut_date':dates[index].isoformat() if dated and index < len(dates) else None,
                       'calendar_days':high-low, 'active_days':active,
                       'fraction':active/active_total})
    return stages, {'calendar_season':calendar['season'], 'season_days':days,
                    'active_days':active_total, 'all_star_break_days':pause_end-pause_start,
                    'schedule_basis':'confirmed_dates' if dated else 'hypothetical_sensitivity',
                    'tie_policy':calendar.get('tie_policy'),
                    'tie_delay_basis':'Scheduled cuts with no unresolved tie. Daily standings are unavailable; tie extensions are not predicted.'}
