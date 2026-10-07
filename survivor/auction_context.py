"""Draft-day budgets and available talent, without using that year's NBA outcomes."""
from collections import defaultdict
from statistics import mean, median, pstdev


def auction_context(season, players, scored, keepers, franchises, rules, settings, *, stats_season=None):
    teams, roster = rules['teams'], settings['roster_size']
    minimum = settings['minimum_bid']
    franchises = sorted(set(franchises))
    if len(franchises) != teams:
        raise ValueError('Auction context requires every franchise.')
    kept = {k['player_id'] for k in keepers}
    if len(kept) != len(keepers):
        raise ValueError('Duplicate keeper in auction context.')
    by_team = defaultdict(list)
    for keeper in keepers:
        if keeper['franchise_sheet'] not in franchises:
            raise ValueError('Keeper franchise missing from auction context.')
        by_team[keeper['franchise_sheet']].append(keeper)
    budgets = []
    for franchise in franchises:
        selections = by_team[franchise]
        spend = sum(k['recorded_cost'] for k in selections)
        slots = roster-len(selections)
        budget = rules['auction_budget']-spend
        if slots <= 0 or budget < slots*minimum:
            raise ValueError('Team budget cannot fill its remaining roster.')
        budgets.append({'franchise':franchise,'keeper_spend':spend,'remaining_budget':budget,
                        'open_slots':slots,'maximum_opening_bid':budget-(slots-1)*minimum})
    # Historical talent is a PRIOR-season proxy, not the set who eventually sold.
    eligible = [p for p in players if p['minutes_pg']>0 and
                (stats_season is None or p['games']>=settings['historical_minimum_games'])]
    ordered = sorted(eligible,key=lambda p:(-scored[p['player_id']]['score'],p['player_id']))
    available = [p for p in ordered if p['player_id'] not in kept]
    slots = sum(t['open_slots'] for t in budgets)
    full_slots = teams*roster
    if len(ordered)<=full_slots or len(available)<=slots:
        raise ValueError('Insufficient player coverage for draft-pool context.')
    def strength(pool,depth):
        replacement = scored[pool[depth]['player_id']]['score']
        return sum(max(0,scored[p['player_id']]['score']-replacement) for p in pool[:depth])
    full_strength, available_strength = strength(ordered,full_slots), strength(available,slots)
    if not full_strength or not available_strength:
        raise ValueError('Available talent has no measurable advantage over replacement.')
    money = sum(t['remaining_budget'] for t in budgets)
    discretionary = money-slots*minimum
    slot_scale = discretionary/slots
    # Normalize each year's category scale against its own no-keeper pool.
    # More unkept talent per open slot reduces the dollars available per unit.
    supply_ratio = (full_strength/full_slots)/(available_strength/slots)
    top_counts = {str(n):sum(p['player_id'] not in kept for p in ordered[:n]) for n in (10,20,30,50,100)}
    money_values = [t['remaining_budget'] for t in budgets]
    return {'season':season,'stats_season':stats_season,'teams':budgets,'keeper_count':len(keepers),
            'remaining_budget':money,'open_slots':slots,'average_team_budget':mean(money_values),
            'median_team_budget':median(money_values),'minimum_team_budget':min(money_values),
            'maximum_team_budget':max(money_values),'team_budget_stddev':pstdev(money_values),
            'discretionary_per_slot':slot_scale,'available_top_counts':top_counts,
            'available_top_players':[{'player_id':p['player_id'],'player':p['player'],
                                      'rank':rank,'score':scored[p['player_id']]['score']}
                                     for rank,p in enumerate(ordered[:30],1) if p['player_id'] not in kept],
            'unmatched_keeper_ids':sorted(kept-{p['player_id'] for p in eligible}),
            'available_pool_count':len(available),'available_strength':available_strength,
            'full_strength':full_strength,'supply_ratio':supply_ratio,'market_scale':slot_scale*supply_ratio,
            'basis':('Prior-season NBA rates, excluding recorded keepers. Missing rookies, low-sample players, '
                     'retirements, injuries and offseason changes are not reconstructed; this is a draft-pool proxy.'
                     if stats_season else 'Published projected NBA rates, excluding confirmed keepers.'),
            'budget_basis':'$200 starting budget and 15 opening slots per team; historical budget rule assumed constant.'}


def historical_contexts(actuals, statistics, auctions, keeper_history, rules, settings, profile_function):
    from survivor.catalog import season_name
    result = {}
    for season in sorted({a['season'] for a in auctions}):
        prior = season_name(int(season[:4])-1)
        if prior not in actuals:
            continue
        players = statistics[actuals[prior]['dataset_id']]
        scored,_ = profile_function(players,pool_size=settings['reference_pool_size'],
                                    minimum_games=settings['historical_minimum_games'])
        keepers = [k for k in keeper_history if k['season']==season]
        franchises = {a['franchise_sheet'] for a in auctions if a['season']==season}
        franchises.update(k['franchise_sheet'] for k in keepers)
        result[season] = auction_context(season,players,scored,keepers,franchises,rules,settings,stats_season=prior)
    return result


def market_observations(observations):
    """Convert bids into dollars per available talent unit instead of per slot."""
    return [{**o,'scale':o['auction_context']['market_scale'],
             'target':o['target']*o['scale']/o['auction_context']['market_scale']} for o in observations]
