"""Validated, season-specific keeper selections and remaining auction budgets."""
from __future__ import annotations

import hashlib
import json
import re

from survivor.catalog import ROOT, load_aliases, now, register_player, resolve_name, rows


def sync_keepers(db, payload):
    """Atomically sync one complete keeper list; identical redeploys are no-ops."""
    rules = json.loads((ROOT / 'config/league.json').read_text(encoding='utf-8'))
    aliases = load_aliases()
    season = payload['season']
    if not re.fullmatch(r'\d{4}-\d{2}', season) or int(season[-2:]) != (int(season[:4])+1) % 100:
        raise ValueError('Invalid keeper season.')
    budget = payload['budget_per_team']
    if type(budget) is not int or budget <= 0:
        raise ValueError('Keeper budget must be a positive integer.')
    teams = payload['teams']
    if len(teams) != rules['teams']:
        raise ValueError('A complete keeper list must include every team.')
    if not payload.get('source_name', '').strip():
        raise ValueError('A keeper source is required.')
    franchises, players, selections = set(), set(), []
    for team in teams:
        franchise = team['franchise'].strip()
        if not franchise or franchise in franchises:
            raise ValueError('Keeper franchises must be unique and nonempty.')
        franchises.add(franchise)
        if len(team['keepers']) != rules['keepers_per_team']:
            raise ValueError('Each franchise must have exactly two confirmed keepers.')
        spend = 0
        for slot, keeper in enumerate(team['keepers'], 1):
            player_id, _, _ = resolve_name(keeper['player'], aliases)
            if not player_id or player_id in players:
                raise ValueError('A player cannot be kept more than once in a season.')
            cost = keeper['cost']
            if type(cost) is not int or cost < 0:
                raise ValueError('Keeper costs must be nonnegative integers.')
            players.add(player_id)
            spend += cost
            selections.append((franchise, slot, keeper['player'], cost))
        if spend > budget:
            raise ValueError('Keeper costs exceed a team budget.')
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    with db:
        if getattr(db, 'dialect', '') == 'postgres':
            db.execute("SELECT pg_advisory_xact_lock(hashtext(?))", ('survivor-keepers-' + season,))
        previous = db.execute('SELECT source_sha256 FROM draft_seasons WHERE season=?', (season,)).fetchone()
        if previous and previous[0] == digest:
            return False
        db.execute('''INSERT INTO draft_seasons VALUES (?,?,?,?,?,?,?)
            ON CONFLICT(season) DO UPDATE SET budget_per_team=excluded.budget_per_team,
            team_count=excluded.team_count,keepers_per_team=excluded.keepers_per_team,
            source_name=excluded.source_name,source_sha256=excluded.source_sha256,
            imported_at=excluded.imported_at''',
            (season, budget, len(teams), rules['keepers_per_team'], payload['source_name'], digest, now()))
        db.execute('DELETE FROM keeper_selections WHERE season=?', (season,))
        for franchise, slot, name, cost in selections:
            player_id = register_player(db, name, aliases)
            db.execute('INSERT INTO keeper_selections VALUES (?,?,?,?,?,?)',
                       (season, franchise, slot, player_id, name, cost))
    return True


def sync_bundled_keepers(db):
    for path in sorted((ROOT / 'config/keepers').glob('*.json')):
        sync_keepers(db, json.loads(path.read_text(encoding='utf-8')))
    from survivor.keeper_evidence import sync_keeper_claims
    sync_keeper_claims(db)


def keeper_summary(db, season):
    metadata = rows(db, 'SELECT * FROM draft_seasons WHERE season=?', (season,))
    if not metadata:
        return None
    result = metadata[0]
    result['teams'] = rows(db, 'SELECT * FROM draft_team_budgets WHERE season=? ORDER BY remaining_budget DESC,franchise', (season,))
    result['rows'] = rows(db, '''SELECT k.*,p.display_name AS player
        FROM keeper_selections k JOIN players p USING(player_id)
        WHERE k.season=? ORDER BY k.franchise,k.keeper_slot''', (season,))
    result['keeper_count'] = len(result['rows'])
    result['keeper_spend'] = sum(team['keeper_spend'] for team in result['teams'])
    result['remaining_budget'] = sum(team['remaining_budget'] for team in result['teams'])
    result['league_budget'] = result['budget_per_team'] * result['team_count']
    return result


def annotate_availability(db, entries, season):
    """Unknown seasons stay unknown. Never apply today's keeper list to older sets."""
    summary = keeper_summary(db, season)
    keepers = {row['player_id']: row for row in summary['rows']} if summary else {}
    for entry in entries:
        keeper = keepers.get(entry['player_id'])
        entry['draft_status'] = 'kept' if keeper else 'available' if summary else 'unknown'
        entry['keeper_franchise'] = keeper['franchise'] if keeper else None
        entry['confirmed_keeper_cost'] = keeper['keeper_cost'] if keeper else None
    return summary
