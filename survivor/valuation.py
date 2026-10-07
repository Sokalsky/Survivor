"""Reproducible eight-category dollars and time-ordered league price comparables."""
from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
import math
from pathlib import Path
from statistics import mean, pstdev

from survivor.catalog import ROOT, build_catalog, now, rows, season_name
from survivor.database import preview_database, railway_database
from survivor.keepers import keeper_summary, sync_bundled_keepers
from survivor.bundled_stats import sync_bundled_stats, sync_bundled_projections
from survivor.market_context import load_context
from survivor.preseason import load_preseason_evidence
from survivor.historical_projections import sync_historical_projections
from survivor.auction_context import auction_context, historical_contexts, market_observations

CATEGORIES = ('PTS','REB','AST','STL','BLK','3PM','FG%','FT%')
COUNT_FIELDS = ('pts_pg','reb_pg','ast_pg','stl_pg','blk_pg','fg3m_pg')
COMPARISON_FIELDS = COUNT_FIELDS+('fgm_pg','fga_pg','ftm_pg','fta_pg')
CONFIG = ROOT/'config/valuation.json'


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',',':'), allow_nan=False)


def fingerprint(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def profiles(players, *, pool_size=225, totals=False, minimum_games=0, weights=None):
    """One frozen reference pool, chosen by minutes, not by prices or category scores."""
    eligible = [p for p in players if p['games'] >= minimum_games and p['minutes_pg'] > 0]
    pool = sorted(eligible, key=lambda p: (-p['minutes_pg']*(p['games'] if totals else 1),p['player_id']))[:pool_size]
    if len(pool) < 2:
        raise ValueError('Too few players to establish an eight-category reference pool.')
    factor = lambda p: p['games'] if totals else 1
    fg_attempts = sum(p['fga_pg']*factor(p) for p in pool)
    ft_attempts = sum(p['fta_pg']*factor(p) for p in pool)
    if not fg_attempts or not ft_attempts:
        raise ValueError('Shooting attempts are required for valuation.')
    fg = sum(p['fgm_pg']*factor(p) for p in pool)/fg_attempts
    ft = sum(p['ftm_pg']*factor(p) for p in pool)/ft_attempts
    def contributions(p):
        f = factor(p)
        return [p[k]*f for k in COUNT_FIELDS] + [(p['fgm_pg']-fg*p['fga_pg'])*f,(p['ftm_pg']-ft*p['fta_pg'])*f]
    reference = [contributions(p) for p in pool]
    centers = [mean(v[i] for v in reference) for i in range(8)]
    scales = [pstdev(v[i] for v in reference) or 1 for i in range(8)]
    weights = weights or dict.fromkeys(CATEGORIES,1)
    scored = {}
    for p in players:
        values = contributions(p)
        z = [(x-c)/s for x,c,s in zip(values,centers,scales)]
        scored[p['player_id']] = {'z':dict(zip(CATEGORIES,z)), 'score':sum(z[i]*weights[k] for i,k in enumerate(CATEGORIES)),
                                  'contributions':dict(zip(CATEGORIES,values))}
    return scored, {'pool_size':len(pool),'selection':'highest projected season minutes' if totals else 'highest minutes per game among eligible players',
                    'fg_baseline':fg,'ft_baseline':ft,'means':dict(zip(CATEGORIES,centers)),
                    'standard_deviations':dict(zip(CATEGORIES,scales))}


def allocate(scores, slots, budget, minimum_bid=1, exponent=1):
    """Select N players and distribute every cent above their minimum bid by VOR."""
    if slots <= 0 or len(scores) < slots or budget < slots*minimum_bid or exponent<=0:
        raise ValueError('Roster slots, available players and budget do not reconcile.')
    ordered = sorted(scores, key=lambda key: (-scores[key],key))
    chosen = ordered[:slots]
    replacement = scores[ordered[slots]] if len(ordered)>slots else min(scores.values())
    advantage = {key:max(0,scores[key]-replacement)**exponent for key in chosen}
    denominator = sum(advantage.values())
    if denominator == 0:
        advantage = dict.fromkeys(chosen,1)
        denominator = slots
    discretionary_cents = round((budget-slots*minimum_bid)*100)
    shares = {key:discretionary_cents*advantage[key]/denominator for key in chosen}
    cents = {key:math.floor(value) for key,value in shares.items()}
    remainder = discretionary_cents-sum(cents.values())
    for key in sorted(chosen,key=lambda key: (-(shares[key]-cents[key]),key))[:remainder]:
        cents[key] += 1
    dollars = dict.fromkeys(scores,0.0)
    dollars.update({key:round(minimum_bid+cents[key]/100,2) for key in chosen})
    return dollars, {'replacement_score':replacement,'slots':slots,'budget':budget,'exponent':exponent,
                     'minimum_bid':minimum_bid,'discretionary_budget':round(discretionary_cents/100,2),
                     'dollars_per_score':discretionary_cents/100/denominator}


def survivor_scores(players, scored, rules, settings, scenario):
    """Conditional finalist scenario, not a forecast of actual waiver outcomes.

    A shared game-pacing factor ensures the neutral top-N rosters cannot consume
    more than their pro-rata 1,000 games during any stage. Replacement improves
    only while the expected neutral turnover fits the move allowance.
    """
    weeks = settings['survivor']['season_weeks']
    roster = settings['roster_size']
    teams = rules['teams']
    cuts = [scenario['first_cut_week']+i*scenario['cut_interval_weeks'] for i in range(teams-2)]
    if not cuts or cuts[0]<=0 or any(a>=b for a,b in zip(cuts,cuts[1:])) or cuts[-1]>=weeks:
        raise ValueError('Elimination scenario must leave time for a two-team final.')
    ordered = sorted(players,key=lambda p:(-scored[p['player_id']]['score'],p['player_id']))
    if len(ordered)<=teams*roster or rules['games_or_start_limits']<=0 or rules['roster_move_limit']<0:
        raise ValueError('Invalid survivor pool, game cap or move allowance.')
    result = {p['player_id']:{'score':0.,'useful_games':0.,'useful_season_fraction':0.} for p in players}
    stages, moves, effective_teams = [], 0., teams
    for index,(start,end) in enumerate(zip([0]+cuts,cuts+[weeks])):
        remaining = teams-index
        if index:
            required = roster/(remaining+1)
            if moves+required <= rules['roster_move_limit']:
                moves += required
                effective_teams = remaining
        depth = effective_teams*roster
        replacement = scored[ordered[depth]['player_id']]['score']
        projected_games_per_team = sum(p['games'] for p in ordered[:depth])/effective_teams
        utilization = min(1,rules['games_or_start_limits']/projected_games_per_team) if projected_games_per_team else 1
        fraction = (end-start)/weeks
        stages.append({'teams':remaining,'start_week':start,'end_week':end,'fraction':fraction,
                       'replacement_depth':depth,'replacement_score':replacement,'game_utilization':utilization,
                       'neutral_games_per_team':projected_games_per_team*fraction*utilization,
                       'expected_moves_used':moves})
        for p in players:
            key = p['player_id']
            advantage = max(0,scored[key]['score']-replacement)
            if advantage>0:
                games = p['games']*fraction*utilization
                # Season-equivalent z advantage: 82 games of +1 advantage = 1.
                result[key]['score'] += advantage*games/82
                result[key]['useful_games'] += games
                result[key]['useful_season_fraction'] += fraction
    return result, {'name':scenario['name'],'stages':stages,'expected_moves_used':moves,
                    'neutral_games_per_team':sum(s['neutral_games_per_team'] for s in stages),
                    'conditional_on':'Surviving to the final with neutral access to the improving player pool.'}


def retention_audit(db, actuals, statistics, settings):
    """Opening membership on the same finalist's ending roster; never drop dates."""
    excluded = {r['season'] for r in rows(db,"SELECT season FROM data_issues WHERE code='finish_order_inconsistent'")}
    records = rows(db,"SELECT season,franchise_sheet,player_id,stage FROM roster_history WHERE finish IN (1,2) ORDER BY season,franchise_sheet,player_id,stage")
    final = {(r['season'],r['franchise_sheet'],r['player_id']) for r in records if r['stage']=='final'}
    ranks = {}
    for season in {r['season'] for r in records}-excluded:
        prior = season_name(int(season[:4])-1)
        if prior not in actuals:
            continue
        players = statistics[actuals[prior]['dataset_id']]
        scored,_ = profiles(players,pool_size=settings['reference_pool_size'],minimum_games=settings['historical_minimum_games'])
        eligible = [p['player_id'] for p in players if p['games']>=settings['historical_minimum_games']]
        ranks[season] = {key:i+1 for i,key in enumerate(sorted(eligible,key=lambda key:(-scored[key]['score'],key)))}
    bands = {name:{'opening':0,'on_same_final_roster':0} for name in ('top_30','31_to_100','101_plus','no_prior_sample')}
    for r in records:
        if r['stage']!='opening' or r['season'] not in ranks:
            continue
        rank = ranks[r['season']].get(r['player_id'])
        name = 'no_prior_sample' if rank is None else 'top_30' if rank<=30 else '31_to_100' if rank<=100 else '101_plus'
        bands[name]['opening'] += 1
        bands[name]['on_same_final_roster'] += (r['season'],r['franchise_sheet'],r['player_id']) in final
    return {'bands':bands,'seasons':sorted(ranks),'excluded_seasons':sorted(excluded),
            'interpretation':'Only finalists, classified by preceding-season per-game rank. Matching opening and final snapshots does not prove continuous ownership. This descriptive audit is not used to fit replacement dates or prices.'}


def feature_vector(profile, full=True, age=False):
    score = profile['score']
    values = [profile['z'][cat] for cat in CATEGORIES] if full else [score]
    values += [max(0,score)**2/8]
    if age:
        # Centering is only a feature transform, not an assigned/missing age.
        a = (profile['age']-27)/5
        values += [a,a*max(0,score)/8]
    return values


def solve(matrix, rhs):
    # Small positive-definite ridge system; partial pivoting, no extra dependency.
    augmented = [list(row)+[value] for row,value in zip(matrix,rhs)]
    n = len(rhs)
    for col in range(n):
        pivot = max(range(col,n),key=lambda i:abs(augmented[i][col]))
        if abs(augmented[pivot][col]) < 1e-12:
            raise ValueError('Singular price-model fit.')
        augmented[col],augmented[pivot] = augmented[pivot],augmented[col]
        divisor = augmented[col][col]
        augmented[col] = [v/divisor for v in augmented[col]]
        for i in range(n):
            if i == col:
                continue
            multiplier = augmented[i][col]
            augmented[i] = [a-multiplier*b for a,b in zip(augmented[i],augmented[col])]
    return [row[-1] for row in augmented]


class PriceModel:
    def __init__(self, observations, settings, target_year, *, full=True, age=False):
        if age:
            observations = [o for o in observations if o['profile'].get('age') is not None]
        if not observations:
            raise ValueError('No past auction observations available to fit prices.')
        if any(int(o['season'][:4])>=target_year for o in observations):
            raise ValueError('Price training must precede the target auction season.')
        self.observations = observations
        self.settings = settings
        self.full = full
        self.age = age
        vectors = [feature_vector(o['profile'],full,age) for o in observations]
        self.z_vectors = [[o['profile']['z'][cat] for cat in CATEGORIES] for o in observations]
        self.means = [mean(row[j] for row in vectors) for j in range(len(vectors[0]))]
        self.scales = [pstdev(row[j] for row in vectors) or 1 for j in range(len(vectors[0]))]
        self.target_year = target_year
        self.weights = [settings['recency_decay']**max(0,target_year-int(o['season'][:4])-1) for o in observations]
        x = [self.standardize(vector) for vector in vectors]
        n = len(x[0])
        matrix = [[sum(w*r[i]*r[j] for w,r in zip(self.weights,x)) + (settings['ridge_penalty'] if i==j and i>0 else 0)
                   for j in range(n)] for i in range(n)]
        rhs = [sum(w*r[i]*o['target'] for w,r,o in zip(self.weights,x,observations)) for i in range(n)]
        self.beta = solve(matrix,rhs)
        self.mean_price = sum(w*o['target'] for w,o in zip(self.weights,observations))/sum(self.weights)
        self.residuals = [o['target']-self.raw_prediction(o['profile']) for o in observations]

    def standardize(self, vector):
        return [1]+[(v-m)/s for v,m,s in zip(vector,self.means,self.scales)]

    def raw_prediction(self, profile):
        return sum(a*b for a,b in zip(self.beta,self.standardize(feature_vector(profile,self.full,self.age))))

    def match(self, profile, other):
        """Compare both stat lines on the target's scale, without era/age dilution."""
        rules = self.settings['comp_rules']
        ref = profile.get('comparison_reference')
        if ref and profile.get('stats') and other.get('stats'):
            a,b = profile['stats'],other['stats']
            differences = [a[k]-b[k] for k in COUNT_FIELDS]+[
                a['fgm_pg']-b['fgm_pg']-ref['fg_baseline']*(a['fga_pg']-b['fga_pg']),
                a['ftm_pg']-b['ftm_pg']-ref['ft_baseline']*(a['fta_pg']-b['fta_pg'])]
            gaps = [v/ref['standard_deviations'][cat] for v,cat in zip(differences,CATEGORIES)]
        else:
            gaps = [profile['z'][cat]-other['z'][cat] for cat in CATEGORIES]
        rms = math.sqrt(sum(g*g for g in gaps)/8)
        tier = abs(sum(gaps))
        maximum = max(abs(g) for g in gaps)
        age = abs(profile['age']-other['age']) if self.age else None
        games = (abs(profile['projected_games']-other['projected_games'])
                 if profile.get('projected_games') is not None and other.get('projected_games') is not None else None)
        distance = math.sqrt(rms*rms+(tier/rules['production_distance_scale'])**2+
                             ((age or 0)/rules['age_distance_scale'])**2+
                             ((games or 0)/rules['games_distance_scale'])**2)
        def qualifies(limits):
            return (rms<=limits['category_rms'] and tier<=limits['production_gap'] and
                    maximum<=limits['largest_category_gap'] and
                    (age is None or age<=limits['age_gap']) and
                    (games is None or games<=limits['games_gap']))
        # Prior actuals are useful secondary evidence, never a close preseason forecast.
        strong = qualifies(rules['strong']) and other.get('outlook_basis')!='prior_actuals_proxy'
        quality = 'strong' if strong else 'supporting' if qualifies(rules['supporting']) else 'excluded'
        return {'distance':distance,'match_quality':quality,'category_rms':rms,'production_gap':tier,
                'largest_category_gap':maximum,'age_gap':age,'games_gap':games}

    def nearest(self, profile, count=None):
        if self.settings.get('comp_rules'):
            matches = [(self.match(profile,o['profile']),i) for i,o in enumerate(self.observations)]
            eligible = [(m,i) for m,i in matches if m['match_quality']!='excluded']
            eligible.sort(key=lambda row:(row[0]['match_quality']!='strong',row[0]['distance'],row[1]))
            return [(m['distance'],i) for m,i in eligible[:count or self.settings['neighbors']]]
        z = [profile['z'][cat] for cat in CATEGORIES]
        distances = []
        for index, other in enumerate(self.z_vectors):
            squared = sum((a-b)**2 for a,b in zip(z,other))
            if self.age:
                squared += ((profile['age']-self.observations[index]['profile']['age'])/self.settings['age_distance_years'])**2
            dimensions = 9 if self.age else 8
            other_games = self.observations[index]['profile'].get('projected_games')
            if other_games is not None and profile.get('projected_games') is not None:
                squared += ((profile['projected_games']-other_games)/self.settings['projection_games_distance_scale'])**2
                dimensions += 1
            distance = math.sqrt(squared/dimensions)
            distances.append((distance,index))
        return sorted(distances)[:count or self.settings['neighbors']]

    def weighted_neighbors(self, profile, *, power=None, neighbors=None):
        power = self.settings['comp_distance_power'] if power is None else power
        offset = self.settings['comp_distance_offset']
        if power<0 or offset<=0:
            raise ValueError('Comparable weights require a positive distance offset and nonnegative power.')
        neighbors = self.nearest(profile) if neighbors is None else neighbors
        weights = [(self.weights[i]/(distance+offset)**power,distance,i) for distance,i in neighbors]
        total = sum(w for w,_,_ in weights)
        result = [{'weight':w/total,'distance':distance,'index':i} for w,distance,i in weights]
        rules = self.settings.get('comp_rules')
        if rules:
            for row in result:
                row.update(self.match(profile,self.observations[row['index']]['profile']))
            strong = [r for r in result if r['match_quality']=='strong']
            supporting = [r for r in result if r['match_quality']=='supporting']
            if strong and supporting:
                share = max(rules['strong_minimum_weight'],sum(r['weight'] for r in strong))
                for group,allocation in ((strong,share),(supporting,1-share)):
                    subtotal = sum(r['weight'] for r in group)
                    for row in group:
                        row['weight'] *= allocation/subtotal
        return result

    def local_components(self, profile, weighted=None):
        weighted = self.weighted_neighbors(profile) if weighted is None else weighted
        share = self.settings['comp_correction_share']
        if self.settings.get('comp_rules') and not any(w['match_quality']=='strong' for w in weighted):
            share = self.settings['comp_rules']['supporting_correction_share'] if weighted else 0
        correction = share*sum(w['weight']*self.residuals[w['index']] for w in weighted)
        return {'base':self.raw_prediction(profile),'correction':correction,'neighbors':weighted,
                'correction_share':share,'strong_count':sum(w.get('match_quality')=='strong' for w in weighted),
                'effective_comps':1/sum(w['weight']**2 for w in weighted) if weighted else 0}

    def predictions(self, profile):
        ridge = self.raw_prediction(profile)
        neighbors = self.nearest(profile)
        weights = self.weighted_neighbors(profile,neighbors=neighbors)
        def estimate(weighted):
            return sum(w['weight']*self.observations[w['index']]['target'] for w in weighted) if weighted else ridge
        knn = estimate(self.weighted_neighbors(profile,power=2,neighbors=neighbors))
        local = self.local_components(profile,weights)
        weighted_knn = estimate(weights)
        blend = self.settings['comp_blend_share']
        return {'ridge':max(0,ridge),'comps':max(0,knn),'blend':max(0,(ridge+knn)/2),'mean':self.mean_price,
                'uniform_comps':max(0,estimate(self.weighted_neighbors(profile,power=0,neighbors=neighbors))),
                'weighted_comps':max(0,weighted_knn),'weighted_blend':max(0,(1-blend)*ridge+blend*weighted_knn),
                'local':max(0,local['base']+local['correction'])}


def load_inputs(db, season):
    datasets = rows(db, "SELECT * FROM stat_datasets WHERE coverage='full_season' ORDER BY as_of_date DESC,imported_at DESC,dataset_id")
    projection = next((d for d in datasets if d['kind']=='projection' and d['season']==season),None)
    if projection is None:
        raise ValueError(f'No complete projections exist for {season}.')
    actuals = {}
    for d in datasets:
        if d['kind']=='actual' and d['season'] < season:
            actuals.setdefault(d['season'],d)
    ids = {projection['dataset_id']} | {d['dataset_id'] for d in actuals.values()}
    statistics = defaultdict(list)
    for row in rows(db, 'SELECT s.*,p.display_name AS player FROM player_stats s JOIN players p USING(player_id) ORDER BY s.dataset_id,s.player_id'):
        if row['dataset_id'] in ids:
            statistics[row['dataset_id']].append(row)
    auctions = rows(db, 'SELECT season,player_id,player,franchise_sheet,recorded_cost FROM auction_sales WHERE season<? ORDER BY season,player_id', (season,))
    historical_keepers = rows(db, 'SELECT season,player_id,franchise_sheet,recorded_cost FROM keeper_costs WHERE season<? ORDER BY season,player_id', (season,))
    draft = keeper_summary(db,season)
    if not draft:
        raise ValueError('A complete keeper list is required for auction-dollar allocation.')
    return projection,actuals,statistics,auctions,historical_keepers,draft


def historical_keeper_groups(keeper_history):
    groups = defaultdict(lambda: {'keepers':0,'cost':0,'ids':set()})
    for row in keeper_history:
        group = groups[row['season']]
        group['keepers'] += 1
        group['cost'] += row['recorded_cost']
        group['ids'].add(row['player_id'])
    return groups


def training_observations(actuals, statistics, auctions, keeper_history, rules, settings, context=None, availability=None, forecasts=None):
    availability = load_preseason_evidence()[1] if availability is None else availability
    forecasts = forecasts or {}
    historical = {}
    references = {}
    for season,dataset in actuals.items():
        players = statistics[dataset['dataset_id']]
        scored,reference = profiles(players,pool_size=settings['reference_pool_size'],minimum_games=settings['historical_minimum_games'])
        references[season] = reference
        historical[season] = {p['player_id']:(p,scored[p['player_id']]) for p in players}
    keepers = historical_keeper_groups(keeper_history)
    markets = historical_contexts(actuals,statistics,auctions,keeper_history,rules,settings,profiles)
    observations, missing = [], []
    for sale in auctions:
        prior = season_name(int(sale['season'][:4])-1)
        evidence = availability.get((sale['season'],sale['player_id']))
        if evidence:
            missing.append({**sale,'stats_season':prior,'reason_code':'preseason_availability',
                            'reason':evidence['reason'],'reported_at':evidence['reported_at'],
                            'availability_status':evidence['status'],'source_url':evidence['source_url']})
            continue
        pair = historical.get(prior,{}).get(sale['player_id'])
        archive = forecasts.get(sale['season'])
        projected = archive['players'].get(sale['player_id']) if archive else None
        if archive and projected is None:
            missing.append({**sale,'stats_season':sale['season'],'reason_code':'missing_historical_projection',
                            'reason':'No usable row in the season\'s partial projection archive; no prior-actuals fallback or invented zero forecast.'})
            continue
        if not projected and (pair is None or pair[0]['games'] < settings['historical_minimum_games']):
            missing.append({**sale,'stats_season':prior,'reason_code':'prior_sample','reason':'No matched prior season or fewer than 10 games'})
            continue
        if projected:
            player = projected
            ref = references[prior]
            contributions = dict(zip(CATEGORIES,[player[k] for k in COUNT_FIELDS]+[
                player['fgm_pg']-ref['fg_baseline']*player['fga_pg'],player['ftm_pg']-ref['ft_baseline']*player['fta_pg']]))
            z = {cat:(contributions[cat]-ref['means'][cat])/ref['standard_deviations'][cat] for cat in CATEGORIES}
            profile = {'z':z,'score':sum(z.values()),'projected_games':player['games']}
        else:
            player,profile = pair
        age_record = context.before(sale['player_id'],sale['season']) if context else None
        profile = {**profile,'age':age_record['target_season_age'] if age_record else None,
                   'stats':{k:player[k] for k in COMPARISON_FIELDS},'comparison_reference':references[prior],
                   'outlook_basis':('projection' if projected else 'prior_actuals_proxy')}
        keeper = keepers.get(sale['season'],{'keepers':0,'cost':0})
        slots = rules['teams']*settings['roster_size']-keeper['keepers']
        budget = rules['teams']*rules['auction_budget']-keeper['cost']
        dollars_per_slot = (budget-slots*settings['minimum_bid'])/slots
        if dollars_per_slot <= 0:
            raise ValueError('Historical keeper costs exceed the assumed auction budget.')
        observations.append({**sale,'stats_season':sale['season'] if projected else prior,'stats_games':player['games'],
                             'outlook_basis':('published_projection' if archive['metadata'].get('provider') else 'supplied_projection') if projected else 'prior_actuals_proxy',
                             'forecast_dataset_id':archive['metadata']['dataset_id'] if archive else None,
                             'availability_review':'no_documented_exception',
                             'profile':profile,'scale':dollars_per_slot,'age_source':age_record,
                             'auction_context':markets[sale['season']],
                             'target':max(0,(sale['recorded_cost']-settings['minimum_bid'])/dollars_per_slot)})
    return observations,missing


def quantile(values, probability):
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered)-1,max(0,math.ceil((len(ordered)+1)*probability)-1))]


def metrics(records):
    if not records:
        return {'n':0,'mae':None,'rmse':None,'bias':None}
    errors = [r['predicted']-r['actual'] for r in records]
    return {'n':len(records),'mae':mean(abs(e) for e in errors),'rmse':math.sqrt(mean(e*e for e in errors)),
            'bias':mean(errors)}


def price_tier(price):
    return 'under_10' if price < 10 else '10_to_30' if price < 30 else '30_plus'


def error_radius(records, expected, scale, coverage):
    band = [r for r in records if price_tier(r['predicted'])==price_tier(expected)]
    if len(band)<30:
        band = records
    return quantile([abs(r['predicted']-r['actual'])/r['scale'] for r in band],coverage)*scale


def evaluate(observations,settings):
    """Choose on development years; lock the choice before the final three seasons."""
    plain_candidates = ('mean','ridge','comps','blend','uniform_comps','weighted_comps','weighted_blend','local')
    candidates = ('score_curve',)+plain_candidates+tuple('age_'+name for name in plain_candidates if name!='mean')+('market_local','market_age_local')
    results = {name:[] for name in candidates}
    previous_results = []
    previous_settings = {**settings,'comp_rules':None}
    seasons = sorted({o['season'] for o in observations})
    selected = None
    for season in seasons:
        if season < settings['development_first_season']:
            continue
        train = [o for o in observations if o['season'] < season]
        test = [o for o in observations if o['season']==season]
        if len(train) < 100:
            continue
        if selected is None and season >= settings['holdout_first_season']:
            selected = min(candidates,key=lambda name:(metrics(results[name])['mae'],name))
        full = PriceModel(train,settings,int(season[:4]))
        age_model = PriceModel(train,settings,int(season[:4]),age=True)
        score = PriceModel(train,settings,int(season[:4]),full=False)
        market_train = market_observations(train)
        market_model = PriceModel(market_train,settings,int(season[:4]))
        market_age_model = PriceModel(market_train,settings,int(season[:4]),age=True)
        previous_model = PriceModel(market_train,previous_settings,int(season[:4]),age=True)
        previous_fallback = PriceModel(market_train,previous_settings,int(season[:4]))
        for item in test:
            predicted = full.predictions(item['profile'])
            age_predictions = age_model.predictions(item['profile']) if item['profile'].get('age') is not None else predicted
            predicted.update({'age_'+name:value for name,value in age_predictions.items() if name!='mean'})
            predicted['score_curve'] = max(0,score.raw_prediction(item['profile']))
            predicted['market_local'] = market_model.predictions(item['profile'])['local']
            predicted['market_age_local'] = (market_age_model if item['profile'].get('age') is not None else market_model).predictions(item['profile'])['local']
            old = previous_model if item['profile'].get('age') is not None else previous_fallback
            prior = old.local_components(item['profile'])
            previous_results.append({'season':season,'actual':item['recorded_cost'],
                                     'predicted':min(200,max(settings['minimum_bid'],settings['minimum_bid']+
                                         (prior['base']+prior['correction'])*item['auction_context']['market_scale']))})
            for candidate in candidates:
                scale = item['auction_context']['market_scale'] if candidate.startswith('market_') else item['scale']
                price = min(200,max(settings['minimum_bid'],settings['minimum_bid']+predicted[candidate]*scale))
                results[candidate].append({'season':season,'player_id':item['player_id'],'player':item['player'],
                                          'actual':item['recorded_cost'],'predicted':price,'scale':scale,
                                          'average_team_budget':item['auction_context']['average_team_budget'],
                                          'available_top_30':item['auction_context']['available_top_counts']['30'],
                                          'supply_ratio':item['auction_context']['supply_ratio'],
                                          'stats_season':item['stats_season'],'training_last_season':max(o['season'] for o in train),
                                          'outlook_basis':item['outlook_basis'],'forecast_dataset_id':item['forecast_dataset_id'],
                                          'target_season_age':item['profile'].get('age'),
                                          'age_source_season':item['age_source']['season'] if item.get('age_source') else None})
    if selected is None:
        raise ValueError('Not enough seasons for development and held-out price evaluation.')
    development = {name:metrics([r for r in records if r['season']<settings['holdout_first_season']]) for name,records in results.items()}
    holdout = {name:metrics([r for r in records if r['season']>=settings['holdout_first_season']]) for name,records in results.items()}
    selected_holdout = [r for r in results[selected] if r['season']>=settings['holdout_first_season']]
    covered = 0
    for record in selected_holdout:
        earlier = [r for r in results[selected] if r['season']<record['season']]
        radius = error_radius(earlier,record['predicted'],record['scale'],settings['interval_coverage'])
        covered += abs(record['predicted']-record['actual'])<=radius
    return selected, {'selected_model':selected,'development':development,'holdout':holdout,
                     'previous_comp_method':{'development':metrics([r for r in previous_results if r['season']<settings['holdout_first_season']]),
                                             'holdout':metrics([r for r in previous_results if r['season']>=settings['holdout_first_season']])},
                     'holdout_by_actual_price_tier':{tier:metrics([r for r in selected_holdout if price_tier(r['actual'])==tier]) for tier in ('under_10','10_to_30','30_plus')},
                     'holdout_interval_coverage':covered/len(selected_holdout),
                     'holdout_first_season':settings['holdout_first_season'],
                     'model_selection_basis':'Candidate choice uses development-year MAE only. Later seasons have been reviewed during earlier model development and are reused retrospective checks, not fresh independent holdouts.',
                     'holdout_tiers_by_model':{name:{tier:metrics([r for r in records if r['season']>=settings['holdout_first_season'] and price_tier(r['actual'])==tier]) for tier in ('under_10','10_to_30','30_plus')} for name,records in results.items()},
                     'by_season':{s:metrics([r for r in results[selected] if r['season']==s]) for s in seasons if any(r['season']==s for r in results[selected])},
                     'interpretation':'Seasons with archived forecasts use their preseason stat profiles; other seasons use prior-year actuals as explicit proxies. Published per-game-only archives have unknown projected GP. The 2025-26 user workbook has unverified provider/date. Missing or invalid forecasts and documented availability exceptions are excluded, changing the evaluation cohort. Exact league auction dates are unknown. Current estimates are not independently validated.'}, results[selected]


def build_valuations(db, season, settings=None):
    settings = settings or json.loads(CONFIG.read_text(encoding='utf-8'))
    rules = json.loads((ROOT/'config/league.json').read_text(encoding='utf-8'))
    projection,actuals,statistics,auctions,keeper_history,draft = load_inputs(db,season)
    players = statistics[projection['dataset_id']]
    keepers = {r['player_id']:r for r in draft['rows']}
    if set(keepers)-{p['player_id'] for p in players}:
        raise ValueError('Some keepers lack projections; refusing distorted budget allocation.')
    if settings['minimum_bid'] <= 0 or settings['roster_size'] <= rules['keepers_per_team']:
        raise ValueError('Invalid roster size or minimum bid.')
    context = load_context()
    evidence,evidence_index = load_preseason_evidence()
    archives = sync_historical_projections(db)
    forecasts = {archive['season']:{'metadata':{k:v for k,v in archive.items() if k not in ('records','rejected_records')},
                                  'players':{p['player_id']:p for p in archive['records']}}
                 for archive in archives if archive['season']<season}
    observations,missing = training_observations(actuals,statistics,auctions,keeper_history,rules,settings,context,evidence_index,forecasts)
    selected,validation,backtest = evaluate(observations,settings)
    rate_profiles,rate_reference = profiles(players,pool_size=settings['reference_pool_size'],weights=settings['category_weights'])
    scenarios = {}
    for scenario in settings['survivor']['scenarios']:
        contributions,details = survivor_scores(players,rate_profiles,rules,settings,scenario)
        scores = {key:value['score'] for key,value in contributions.items()}
        dollars,allocation = allocate(scores,rules['teams']*settings['roster_size'],rules['teams']*rules['auction_budget'],settings['minimum_bid'])
        scenarios[scenario['name']] = {'contributions':contributions,'dollars':dollars,'allocation':allocation,'details':details}
    central = scenarios['central']
    base,base_allocation = central['dollars'],central['allocation']
    slots = rules['teams']*settings['roster_size']-len(keepers)
    scale = (draft['remaining_budget']-slots*settings['minimum_bid'])/slots
    current_market = auction_context(season,players,rate_profiles,
        [{'player_id':k['player_id'],'franchise_sheet':k['franchise'],'recorded_cost':k['keeper_cost']} for k in draft['rows']],
        [t['franchise'] for t in draft['teams']],rules,settings)
    market_adjusted = selected.startswith('market_')
    method = selected.removeprefix('market_')
    model_rows = market_observations(observations) if market_adjusted else observations
    if market_adjusted:
        scale = current_market['market_scale']
    model = PriceModel(model_rows,settings,int(season[:4]),full=method!='score_curve',age=method.startswith('age_'))
    fallback = PriceModel(model_rows,settings,int(season[:4]),full=method!='score_curve') if model.age else model
    latest_prior = season_name(int(season[:4])-1)
    prior_games = {p['player_id']:p['games'] for p in statistics[actuals[latest_prior]['dataset_id']]} if latest_prior in actuals else {}
    values = []
    for p in players:
        key = p['player_id']
        age_record = context.before(key,season)
        profile = {**rate_profiles[key],'age':age_record['target_season_age'] if age_record else None,'projected_games':p['games'],
                   'stats':{k:p[k] for k in COMPARISON_FIELDS},'comparison_reference':rate_reference,'outlook_basis':'projection'}
        player_model = model if age_record or not model.age else fallback
        prediction_key = 'ridge' if method=='score_curve' else method.removeprefix('age_')
        expected = min(200,max(settings['minimum_bid'],settings['minimum_bid']+player_model.predictions(profile)[prediction_key]*scale))
        radius = error_radius(backtest,expected,scale,settings['interval_coverage'])
        power = 0 if prediction_key=='uniform_comps' else 2 if prediction_key in ('comps','blend') else settings['comp_distance_power']
        weighted = player_model.weighted_neighbors(profile,power=power)
        components = player_model.local_components(profile,weighted)
        nearest = components['neighbors']
        comps = []
        for weighted in nearest:
            distance,i = weighted['distance'],weighted['index']
            comp = player_model.observations[i]
            regression_price = settings['minimum_bid']+player_model.raw_prediction(comp['profile'])*scale
            adjusted_price = settings['minimum_bid']+comp['target']*scale
            target_base = settings['minimum_bid']+components['base']*scale
            comps.append({'player':comp['player'],'player_id':comp['player_id'],'season':comp['season'],
                          'stats_season':comp['stats_season'],'actual_price':comp['recorded_cost'],
                          'outlook_basis':comp['outlook_basis'],'availability_review':comp['availability_review'],
                          'projected_games':comp['profile'].get('projected_games'),'forecast_dataset_id':comp['forecast_dataset_id'],
                          'franchise':comp['franchise_sheet'],'distance':round(distance,4),
                          'weight':weighted['weight'],'target_season_age':comp['profile'].get('age'),
                          'average_team_budget':comp['auction_context']['average_team_budget'],
                          'available_top_30':comp['auction_context']['available_top_counts']['30'],
                          'supply_ratio':comp['auction_context']['supply_ratio'],
                          'budget_adjusted_price':adjusted_price,'model_price':regression_price,
                          'profile_adjustment':target_base-regression_price,
                          'implied_price':adjusted_price+target_base-regression_price,
                          'match_quality':weighted.get('match_quality'),
                          'match_details':{k:weighted.get(k) for k in ('category_rms','production_gap','largest_category_gap','age_gap','games_gap')},
                          'stats':comp['profile']['stats'],
                          'price_adjustment':components['correction_share']*weighted['weight']*(adjusted_price-regression_price) if prediction_key=='local' else None,
                          'category_z':comp['profile']['z']})
        keeper = keepers.get(key)
        fair = base[key]
        flags = []
        if prior_games.get(key,0)<settings['historical_minimum_games']:
            flags.append('Fewer than 10 NBA games in the previous season: rookie/returning or low-sample player; historical forecast coverage for these cases is limited to the archived partial player pools.')
        if not nearest:
            flags.append('No historical match meets the comparable rules; using the regression estimate without a comp correction.')
        elif not components['strong_count']:
            flags.append('No strong historical forecast match; supporting comps have a reduced price correction.')
        if not age_record:
            flags.append('No earlier recorded season age: market estimate uses the statistics-only counterpart, with no invented age or youth premium.')
        if components['effective_comps']<3:
            flags.append('Comparable pricing is concentrated in fewer than three effectively weighted records.')
        if p['games'] < 65:
            flags.append(f'The provider projects {p["games"]:g} games; availability is spread uniformly, so a delayed return may overstate early usefulness.')
        if keeper:
            flags.append('Kept: expected price is hypothetical if available; neutral value uses a fresh $3,000 draft with no keepers.')
        elif fair == 0:
            flags.append('Outside the 225-player neutral allocation. Market price is conditional on being drafted.')
        flags.append('Survivor score assumes reaching the final and neutral access to replacements; no actual waiver winners, positions, dated injuries or category resets are simulated.')
        values.append({'player_id':key,'player':p['player'],'fair_value':fair,'expected_auction_price':round(expected,2),
                       'recommended_bid_ceiling':None,'lower_estimate':round(max(settings['minimum_bid'],expected-radius),2),
                       'upper_estimate':round(min(200,expected+radius),2),'keeper_cost':keeper['keeper_cost'] if keeper else None,
                       'keeper_surplus':round(fair-keeper['keeper_cost'],2) if keeper else None,
                       'category_values':{'rate_z':profile['z'],'per_game_score':profile['score'],
                                          **central['contributions'][key],'neutral_value':fair,
                                          'value_basis':'neutral_15_team_no_keepers',
                                          'neutral_slot':fair>0,'games':p['games'],
                                          'market':{'method':selected if player_model is model else ('market_' if market_adjusted else '')+prediction_key,
                                                    'supply_adjusted':market_adjusted,
                                                    'average_team_budget':current_market['average_team_budget'],
                                                    'available_top_30':current_market['available_top_counts']['30'],
                                                    'supply_ratio':current_market['supply_ratio'],
                                                    'age_source':age_record,'age_in_model':player_model.age,
                                                    'base_price':settings['minimum_bid']+components['base']*scale,
                                                    'comp_adjustment':components['correction']*scale if prediction_key=='local' else None,
                                                    'effective_comps':components['effective_comps'],
                                                    'comp_count':len(nearest),'weight_power':power,
                                                    'strong_comp_count':components['strong_count'],
                                                    'strong_comp_weight':sum(w['weight'] for w in nearest if w.get('match_quality')=='strong'),
                                                    'comparison_stats':profile['stats'],
                                                    'comparison_player':p['player'],'comparison_games':p['games'],
                                                    'distance_offset':settings['comp_distance_offset'],
                                                    'correction_share':components['correction_share']},
                                          'scenario_values':{name:s['dollars'][key] for name,s in scenarios.items()}},
                       'comps':comps,'risk_notes':' '.join(flags)})
    excluded_availability = [r for r in missing if r['reason_code']=='preseason_availability']
    validation.update(training_rows=len(observations),unmatched_or_low_sample_rows=sum(r['reason_code']=='prior_sample' for r in missing),
                      excluded_rows=len(missing),preseason_excluded_rows=len(excluded_availability),
                      preseason_evidence={**evidence,'excluded_sales':excluded_availability},historical_sales=len(auctions),
                      historical_projection_sources=[v['metadata'] for v in forecasts.values()],
                      forecast_training_rows=sum(o['forecast_dataset_id'] is not None for o in observations),
                      forecast_training_by_season={s:sum(o['season']==s and o['forecast_dataset_id'] is not None for o in observations) for s in forecasts},
                      missing_historical_projection_rows=sum(r['reason_code']=='missing_historical_projection' for r in missing),
                      remaining_budget=draft['remaining_budget'],remaining_slots=slots,projected_players=len(players),
                      neutral_allocation=base_allocation,rate_reference=rate_reference,
                      market_context=context.metadata,
                      age_training_rows=sum(o['profile'].get('age') is not None for o in observations),
                      current_age_coverage=sum(context.before(p['player_id'],season) is not None for p in players),
                      auction_contexts=[next(o['auction_context'] for o in observations if o['season']==s) for s in sorted({o['season'] for o in observations})]+[current_market],
                      supply_adjustment_selected=market_adjusted,
                      survivor_scenarios={name:s['details'] for name,s in scenarios.items()},
                      retention_audit=retention_audit(db,actuals,statistics,settings),
                      neutral_value_basis='Equal category weights; linear allocation of positive survivor value above replacement. No historical price fitting, star multiplier or category punt. Scenario estimate, not an empirically validated optimum.',
                      market_price_basis='Conditional on selection at auction; keeper-adjusted discretionary money '+('per available talent unit.' if market_adjusted else 'per open slot; available-talent correction was evaluated but not selected.')+' Individual estimates are not a simultaneous auction allocation. Team budget distribution is displayed, not a simulation of manager bidding.',
                      interval={'nominal':settings['interval_coverage'],'basis':'Absolute normalized out-of-season price errors, grouped by predicted price tier; descriptive error band, not a guaranteed confidence interval.'},
                      market_coefficients={'features':(list(CATEGORIES)+['positive_score_squared/8'] if model.full else ['score','positive_score_squared/8'])+(['age_centered/5','age_centered/5 * positive_score/8'] if model.age else []),
                                           'standardized_coefficients':model.beta,'feature_means':model.means,'feature_scales':model.scales})
    identity = {'version':settings['model_version'],'settings':settings,'rules':rules,'projection':projection['dataset_id'],
                'historical_datasets':{k:d['dataset_id'] for k,d in actuals.items()},'auctions':auctions,
                'keeper_history':keeper_history,'keepers':draft['rows'],'values':values,'validation':validation}
    run_id = fingerprint(identity)
    return {'run_id':run_id,'projection_dataset_id':projection['dataset_id'],'season':season,
            'model_version':settings['model_version'],'settings':{**settings,'league_rules':rules},'validation':validation,
            'training_cutoff':max(o['season'] for o in observations),'values':values,'backtest':backtest,'excluded':missing}


def save_run(db, result):
    with db:
        if getattr(db,'dialect',None)=='postgres':
            db.execute("SELECT pg_advisory_xact_lock(hashtext('survivor-valuations'))")
        if db.execute('SELECT run_id FROM valuation_runs WHERE run_id=?',(result['run_id'],)).fetchone():
            return result['run_id']
        db.execute('INSERT INTO valuation_runs VALUES (?,?,?,?,?,?,?,?)',
                   (result['run_id'],result['projection_dataset_id'],now(),result['model_version'],canonical(result['settings']),
                    result['training_cutoff'],canonical(result['validation']),
                    'Published provider forecasts valued with 8-category z scores and actual league prices. No NBA projection statistics are changed.'))
        db.executemany('INSERT INTO projected_values VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                       [(result['run_id'],v['player_id'],v['fair_value'],v['expected_auction_price'],v['recommended_bid_ceiling'],
                         v['lower_estimate'],v['upper_estimate'],v['keeper_cost'],v['keeper_surplus'],canonical(v['category_values']),
                         canonical(v['comps']),v['risk_notes']) for v in result['values']])
    return result['run_id']


def sync_valuations(db):
    rules = json.loads((ROOT/'config/league.json').read_text(encoding='utf-8'))
    result = build_valuations(db,rules['target_season'])
    save_run(db,result)
    print(f'Valuations ready: {result["season"]} / {len(result["values"])} players / {result["model_version"]}',flush=True)
    return result


def export_results(result, folder):
    from survivor.stats_workbook import add_sheet, write_csv
    from openpyxl import Workbook
    folder.mkdir(parents=True,exist_ok=True)
    flat = [{'player_id':row['player_id'],'player':row['player'],
             'survivor_value_score':row['category_values']['score'],
             'expected_league_price':row['expected_auction_price'],'neutral_auction_value':row['fair_value'],
             'price_low':row['lower_estimate'],'price_high':row['upper_estimate'],
             'market_season_age':(row['category_values']['market']['age_source'] or {}).get('target_season_age'),
             'market_method':row['category_values']['market']['method'],
             'price_before_comp_adjustment':row['category_values']['market']['base_price'],
             'comp_price_adjustment':row['category_values']['market']['comp_adjustment'],
             'effective_comps':row['category_values']['market']['effective_comps'],
             'useful_games_scenario':row['category_values']['useful_games'],
             **{f'neutral_{name}':value for name,value in row['category_values']['scenario_values'].items()},
             'keeper_cost':row['keeper_cost'],'keeper_surplus':row['keeper_surplus'],'risk_notes':row['risk_notes']}
            for row in result['values']]
    flat.sort(key=lambda row:(-row['neutral_auction_value'],row['player']))
    write_csv(folder/'player-values.csv',flat,list(flat[0]))
    write_csv(folder/'backtest.csv',result['backtest'],list(result['backtest'][0]))
    workbook = Workbook();workbook.remove(workbook.active)
    add_sheet(workbook,'Player values',flat,list(flat[0]))
    category_rows = [{'player':v['player'],**v['category_values']['rate_z']} for v in result['values']]
    add_sheet(workbook,'Category contributions',category_rows,['player']+list(CATEGORIES))
    comp_rows = [{'target_player':v['player'],**{k:c for k,c in comp.items() if k not in ('category_z','match_details','stats')},
                  **comp.get('stats',{}),**comp.get('match_details',{})} for v in result['values'] for comp in v['comps']]
    add_sheet(workbook,'Historical comps',comp_rows,list(comp_rows[0]))
    contexts = [{k:v for k,v in c.items() if k not in ('teams','available_top_players','available_top_counts','unmatched_keeper_ids')} |
                {f'available_top_{n}':count for n,count in c['available_top_counts'].items()} |
                {'unmatched_keepers':len(c['unmatched_keeper_ids'])} for c in result['validation']['auction_contexts']]
    add_sheet(workbook,'Auction markets',contexts,list(contexts[0]))
    teams = [{'season':c['season'],**t} for c in result['validation']['auction_contexts'] for t in c['teams']]
    add_sheet(workbook,'Historical budgets',teams,list(teams[0]))
    add_sheet(workbook,'Backtest',result['backtest'],list(result['backtest'][0]))
    model_checks = [{'model':name,'split':split,**metrics} for split in ('development','holdout') for name,metrics in result['validation'][split].items()]
    add_sheet(workbook,'Model comparison',model_checks,list(model_checks[0]))
    excluded_fields = list(dict.fromkeys(k for row in result['excluded'] for k in row))
    add_sheet(workbook,'Excluded history',result['excluded'],excluded_fields)
    write_csv(folder/'excluded-history.csv',result['excluded'],excluded_fields)
    stage_rows = [{'scenario':name,**stage} for name,scenario in result['validation']['survivor_scenarios'].items() for stage in scenario['stages']]
    add_sheet(workbook,'Elimination scenarios',stage_rows,list(stage_rows[0]))
    add_sheet(workbook,'Method',[{'item':key,'value':canonical(value)} for key,value in result['settings'].items()],['item','value'])
    workbook.save(folder/'survivor-valuations.xlsx')
    report = {k:v for k,v in result.items() if k not in ('values','backtest','excluded')}
    (folder/'model-report.json').write_text(json.dumps(report,indent=2,allow_nan=False)+'\n',encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--preview',action='store_true')
    parser.add_argument('--export-dir',type=Path,default=ROOT/'output/valuations')
    args = parser.parse_args()
    db = preview_database() if args.preview else railway_database()
    try:
        if args.preview:
            build_catalog(ROOT/'Survivor keeper log 2025.xlsx',db)
            sync_bundled_keepers(db);sync_bundled_stats(db);sync_bundled_projections(db)
        result = sync_valuations(db)
        export_results(result,args.export_dir)
        print(canonical({'selected_model':result['validation']['selected_model'],'holdout':result['validation']['holdout'],
                         'training_rows':result['validation']['training_rows'],'excluded':result['validation']['unmatched_or_low_sample_rows']}))
    finally:
        db.close()


if __name__=='__main__':
    main()
