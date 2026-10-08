"""Secondary, one-sided price evidence from actual keeper decisions and claims."""
from collections import defaultdict
import hashlib
import json
from survivor.catalog import ROOT,rows,resolve_name,load_aliases,season_name


def eligible_cost(cost, completed_year):
    # Supported by 290/299 historical prices and all 30 current selections.
    if completed_year not in range(1,6):
        raise ValueError('Keeper increase is only supported for recorded years 1 through 5.')
    return cost+completed_year*(completed_year+1)//2


def sync_keeper_claims(db):
    """Persist explicit claims, with claimant-specific, auditable derived costs."""
    aliases=load_aliases()
    for path in sorted((ROOT/'config/keeper_claims').glob('*.json')):
        payload=json.loads(path.read_text(encoding='utf-8'));season=payload['season']
        selected=rows(db,'SELECT * FROM keeper_selections WHERE season=?',(season,))
        if not selected:
            selected=rows(db,'SELECT player_id,franchise_sheet AS franchise,recorded_cost AS keeper_cost FROM keeper_costs WHERE season=?',(season,))
        confirmed={r['player_id']:r for r in selected}
        prior=season_name(int(season[:4])-1)
        records=[];seen=set();ranks=set()
        for raw in payload['claims']:
            if raw.get('status')!='blocked_by_keeper':
                raise ValueError('Only explicitly blocked keeper choices qualify as claims.')
            if type(raw['choice_rank']) is not int or raw['choice_rank']<1:
                raise ValueError('Keeper choice rank must be a positive integer.')
            if raw.get('eligible_cost') is not None and (type(raw['eligible_cost']) is not int or not 1<=raw['eligible_cost']<=200):
                raise ValueError('Explicit claimant price must be an integer from 1 to 200.')
            key=resolve_name(raw['player'],aliases)[0]
            identity=(raw['franchise'],key);rank=(raw['franchise'],raw['choice_rank'])
            if identity in seen or rank in ranks or key not in confirmed or confirmed[key]['franchise']!=raw['kept_by'] or raw['franchise']==raw['kept_by']:
                raise ValueError('Duplicate or inconsistent keeper claim.')
            seen.add(identity);ranks.add(rank)
            matches=rows(db,"SELECT * FROM roster_history WHERE season=? AND franchise_sheet=? AND player_id=? AND stage='final'",
                         (prior,raw['franchise'],key))
            cost=raw.get('eligible_cost');basis={'raw_claim':raw,'price_status':'explicit' if cost is not None else 'unknown'}
            if len(matches)==1 and matches[0]['contract_year_recorded'] in range(1,6):
                old=matches[0];derived=eligible_cost(old['recorded_cost'],old['contract_year_recorded'])
                if cost is not None and cost!=derived:
                    raise ValueError('Explicit claimant price conflicts with the recorded contract.')
                cost=derived
                basis.update(price_status='derived',prior_season=prior,prior_cost=old['recorded_cost'],
                             prior_contract_year=old['contract_year_recorded'],source_workbook_sha256=old['source_id'],
                             prior_cost_cell=old['cost_cell'],prior_year_cell=old['contract_year_cell'],
                             formula='prior cost + n*(n+1)/2; n is this claimant\'s prior final-roster contract year')
            records.append({'season':season,'franchise':raw['franchise'],'choice_rank':raw['choice_rank'],
                            'player_id':key,'eligible_cost':cost,'details':basis})
        digest=hashlib.sha256(json.dumps({'source':payload,'records':records},sort_keys=True).encode()).hexdigest()
        with db:
            if getattr(db,'dialect',None)=='postgres':
                db.execute("SELECT pg_advisory_xact_lock(hashtext(?))",('keeper-claims-'+season,))
            db.execute('INSERT INTO keeper_claim_sources VALUES (?,?,?) ON CONFLICT(season) DO UPDATE SET source_sha256=excluded.source_sha256,payload_json=excluded.payload_json',
                       (season,digest,json.dumps(payload,sort_keys=True)))
            db.execute('DELETE FROM keeper_claims WHERE season=?',(season,))
            db.executemany('INSERT INTO keeper_claims VALUES (?,?,?,?,?,?)',
                           [(r['season'],r['franchise'],r['choice_rank'],r['player_id'],r['eligible_cost'],json.dumps(r['details'],sort_keys=True)) for r in records])


def add_claims(keeper_records, claims):
    lookup={(r['season'],r['player_id']):r for r in keeper_records}
    result=list(keeper_records)
    for claim in claims:
        winner=lookup.get((claim['season'],claim['player_id']))
        if winner is None or claim['eligible_cost'] is None:
            continue
        result.append({**winner,'kind':'claim','recorded_cost':claim['eligible_cost'],
                       'franchise_sheet':claim['franchise'],'claim_details':json.loads(claim['details_json']),
                       'choice_rank':claim['choice_rank']})
    return result


def attach_contracts(records, auctions):
    """Salary lineage follows the last actual auction, including later waiver owners."""
    by_player=defaultdict(list)
    for sale in auctions:
        by_player[sale['player_id']].append(sale['season'])
    return [{**r,'auction_origin':max((s for s in by_player[r['player_id']] if s<=r['season']),default=None)} for r in records]


def group_strength(records, repeat_share):
    groups=defaultdict(list)
    for row in records:
        groups[(row['player_id'],row['franchise'],row['auction_origin'])].append(row['reliability'])
    return sum(max(w)+repeat_share*(sum(w)-max(w)) for w in groups.values())


def combine_support(baseline, records, config):
    """Integrate diminishing support above the auction estimate.

    Every record supports prices up to its adjusted cost. An extra inexpensive
    keeper cannot lower a stronger signal, and a cheaper claim only strengthens
    the portion of the estimate that it actually supports. This saturation is a
    working weighting rule, not an estimated probability of a future bid.
    """
    active=[r for r in records if r['adjusted_cost']>baseline and r['reliability']>0]
    strength=group_strength(active,config['repeat_share']) if active else 0
    share=config['maximum_share']*strength/(1+strength)
    intervals=[];low=baseline;uplift=0.
    for high in sorted({r['adjusted_cost'] for r in active}):
        supporting=[r for r in active if r['adjusted_cost']>=high]
        s=group_strength(supporting,config['repeat_share'])
        amount=(high-low)*config['maximum_share']*s/(1+s)
        intervals.append({'from':low,'to':high,'strength':s,'price_effect':amount})
        uplift+=amount;low=high
    return {'auction_estimate':baseline,'adjustment':uplift,'share':share,
            'signal':baseline+uplift/share if share else None,'strength':strength,
            'records':records,'intervals':intervals,
            'supporting_decisions':sum(r['kind']=='keeper' for r in active),
            'supporting_claims':sum(r['kind']=='claim' for r in active)}


def keeper_adjustment(model, player_id, profile, season, market, baseline, records, config, auction_neighbors=(), *, profile_scale=None):
    target_year=int(season[:4]);qualified=[];seen=set()
    origins={(model.observations[w['index']]['player_id'],model.observations[w['index']]['season']) for w in auction_neighbors}
    for record in records:
        years=target_year-int(record['season'][:4])
        if years<0 or years>config['maximum_seasons_ago'] or not record.get('forecast_dataset_id'):
            continue
        if config.get('scope')=='same_player' and record['player_id']!=player_id:
            continue
        if model.age and record['profile'].get('age') is None:
            continue
        match=model.match(profile,record['profile'],season=record['season'])
        if match['match_quality']=='excluded':
            continue
        identity=(record['season'],record['player_id'],record['franchise_sheet'],record.get('kind','keeper'))
        if identity in seen:continue
        seen.add(identity)
        scale=market['market_scale'];old=record['auction_context'];cost=record['recorded_cost']
        cash=1+(cost-1)*market['discretionary_per_slot']/old['discretionary_per_slot']
        budget=1+(cost-1)*scale/old['market_scale']
        change=model.profile_adjustment(profile,record['profile'])['total']*(scale if profile_scale is None else profile_scale)
        adjusted=min(200,max(1,budget+change))
        reliability=model.settings['recency_decay']**years/(1+match['distance'])**2
        if match['match_quality']=='supporting':reliability*=config['supporting_share']
        kind=record.get('kind','keeper')
        if kind=='claim':reliability*=config['claim_share']
        overlap=(record['player_id'],record.get('auction_origin')) in origins
        if overlap:reliability*=config['auction_overlap_share']
        qualified.append({'season':record['season'],'player_id':record['player_id'],'player':record['player'],
                          'franchise':record['franchise_sheet'],'kind':kind,'cost':cost,'adjusted_cost':adjusted,
                          'cash_adjustment':cash-cost,'supply_adjustment':budget-cash,'profile_adjustment':change,
                          'unclamped_adjusted_cost':budget+change,'distance':match['distance'],
                          'match_quality':'strong' if match['match_quality']=='near_identical' else match['match_quality'],
                          'reliability':reliability,'auction_origin':record.get('auction_origin'),'auction_overlap':overlap,
                          'projected_games':record['profile'].get('projected_games'),'stats':record['profile']['stats'],
                          'forecast_dataset_id':record['forecast_dataset_id'],'claim_details':record.get('claim_details'),
                          'source_cell':record.get('source_cell'),'contract_year_recorded':record.get('contract_year_recorded'),
                          'supports_uplift':adjusted>baseline})
    qualified.sort(key=lambda r:(not r['supports_uplift'],-r['reliability'],r['season'],r['player_id'],r['franchise'],r['kind']))
    return combine_support(baseline,qualified,config)
