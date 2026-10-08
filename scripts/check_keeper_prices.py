"""Audit keeper escalation and derive prices for explicitly recorded blocked claims.

Uses catalog exports and the supplied keeper summary. Does not infer claimants
from roster ownership, modify recorded salaries, or change the valuation model.
"""
import csv
import hashlib
import json
from collections import Counter,defaultdict
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from survivor.catalog import ROOT,load_aliases,resolve_name,season_name


def read_csv(name):
    with (ROOT/'output/csv'/name).open(encoding='utf-8-sig',newline='') as stream:
        return list(csv.DictReader(stream))


def advance(cost,year):
    # The audit below supports completed contract years 1 through 5 only.
    if year not in range(1,6):
        raise ValueError(f'Unsupported recorded contract year: {year}')
    return cost+year*(year+1)//2


def main():
    opening,final=read_csv('opening_rosters.csv'),read_csv('final_rosters.csv')
    roster=defaultdict(list)
    for row in final:
        roster[(row['season'],row['franchise_sheet'],row['player_id'])].append(row)
    def previous(season,franchise,player_id):
        matches=roster[(season_name(int(season[:4])-1),franchise,player_id)]
        if len(matches)!=1:
            raise ValueError(f'Missing or ambiguous prior final roster: {season}/{franchise}/{player_id}')
        return matches[0]
    transitions,unmatched=[],[]
    increases=defaultdict(Counter)
    disagreements=[]
    for row in opening:
        same=roster[(row['season'],row['franchise_sheet'],row['player_id'])]
        if len(same)==1 and row['contract_year_recorded']!=same[0]['contract_year_recorded']:
            disagreements.append({'season':row['season'],'franchise':row['franchise_sheet'],'player':row['player'],
                'opening_year':int(row['contract_year_recorded']),'final_year':int(same[0]['contract_year_recorded']),
                'opening_year_cell':row['contract_year_cell'],'final_year_cell':same[0]['contract_year_cell'],
                'status':'review_required; recorded values preserved'})
        if row['acquisition_class']!='keeper':continue
        try:
            old=previous(row['season'],row['franchise_sheet'],row['player_id'])
        except ValueError as error:
            unmatched.append({'season':row['season'],'franchise':row['franchise_sheet'],'player':row['player'],'reason':str(error)})
            continue
        year=int(old['contract_year_recorded']);before=float(old['recorded_cost']);paid=float(row['recorded_cost'])
        predicted=advance(before,year)
        increases[year][paid-before]+=1
        transitions.append({'season':row['season'],'franchise':row['franchise_sheet'],'player':row['player'],
            'previous_cost':before,'previous_contract_year':year,'keeper_cost':paid,'keeper_contract_year':int(row['contract_year_recorded']),
            'expected_cost':predicted,'price_matches':paid==predicted,'year_matches':int(row['contract_year_recorded'])==year+1,
            'previous_cost_cell':old['cost_cell'],'previous_year_cell':old['contract_year_cell'],
            'keeper_cost_cell':row['cost_cell'],'keeper_year_cell':row['contract_year_cell']})
    assert set(increases)==set(range(1,6))
    assert all(counts.most_common(1)[0][0]==year*(year+1)//2 for year,counts in increases.items())
    aliases=load_aliases()
    payload=json.loads((ROOT/'config/keepers/2026-27.json').read_text())
    season=payload['season']; winners={}; checks=[]
    for team in payload['teams']:
        for keeper in team['keepers']:
            key=resolve_name(keeper['player'],aliases)[0]
            old=previous(season,team['franchise'],key)
            calculated=advance(float(old['recorded_cost']),int(old['contract_year_recorded']))
            checks.append({'franchise':team['franchise'],'player':keeper['player'],'confirmed_cost':keeper['cost'],
                           'calculated_cost':calculated,'matches':keeper['cost']==calculated})
            winners[key]={'franchise':team['franchise'],'cost':keeper['cost'],'player':keeper['player']}
    if len(checks)!=30 or not all(c['matches'] for c in checks):
        raise ValueError('Current confirmed salaries do not fully support the inferred rule; review before deriving claims.')
    source_path=ROOT/'config/keeper_claims'/f'{season}.json'
    source=json.loads(source_path.read_text())
    claims=[]; seen=set()
    for claim in source['claims']:
        key=resolve_name(claim['player'],aliases)[0]
        if (claim['franchise'],key) in seen or claim['kept_by']!=winners[key]['franchise'] or claim['franchise']==claim['kept_by']:
            raise ValueError('Duplicate or inconsistent keeper claimant.')
        seen.add((claim['franchise'],key))
        old=previous(season,claim['franchise'],key)
        year=int(old['contract_year_recorded']);cost=float(old['recorded_cost'])
        calculated=advance(cost,year)
        if claim['eligible_cost'] is not None and claim['eligible_cost']!=calculated:
            raise ValueError('Explicit claimant price conflicts with calculation.')
        claims.append({**claim,'player_id':key,'eligible_cost':calculated,'winner_cost':winners[key]['cost'],
            'price_basis':'Derived from claimant-specific prior final roster and inferred escalation; not a recorded bid.',
            'prior_season':old['season'],'prior_cost':cost,'prior_contract_year':year,'increase':calculated-cost,
            'source_workbook_sha256':old['source_id'],'prior_cost_cell':old['cost_cell'],'prior_year_cell':old['contract_year_cell']})
    demand=[]
    for key in sorted({c['player_id'] for c in claims}):
        winner=winners[key]
        offers=[{'franchise':winner['franchise'],'cost':winner['cost'],'basis':'confirmed_keeper'}]
        offers += [{'franchise':c['franchise'],'cost':c['eligible_cost'],'basis':'blocked_ranked_choice; calculated_price'} for c in claims if c['player_id']==key]
        offers.sort(key=lambda r:(-r['cost'],r['franchise']))
        demand.append({'player_id':key,'player':winner['player'],'known_managers':len(offers),
                       'second_highest_supported_cost':offers[1]['cost'],'choices':offers})
    report={'status':'source audit for v10 claimant-specific evidence; no historical salary correction',
        'rule':{'status':'inferred_from_records','formula':'prior cost + n*(n+1)/2, where n is the claimant\'s prior final-roster contract year',
                'supported_completed_contract_years':[1,2,3,4,5],
                'observed_increases':{str(y):dict(c) for y,c in sorted(increases.items())},
                'matched_historical_transitions':len(transitions),'historical_price_matches':sum(t['price_matches'] for t in transitions),
                'current_confirmed_matches':sum(c['matches'] for c in checks)},
        'coverage':{'season':season,'older_unsuccessful_claims':'unknown; roster ownership is not a claim',
                    'source_sha256':hashlib.sha256(source_path.read_bytes()).hexdigest(),
                    'interpretation':'Conditional ranked choices. Shared price support is not an exact winning auction bid or a guaranteed price floor.'},
        'historical_transitions':transitions,'unmatched_historical_keepers':unmatched,
        'historical_exceptions':[t for t in transitions if not(t['price_matches'] and t['year_matches'])],
        'opening_final_year_disagreements':disagreements,'current_confirmed_checks':checks,
        'claims':claims,'contested_players':demand}
    folder=ROOT/'output/keeper_evidence';folder.mkdir(parents=True,exist_ok=True)
    (folder/'2026-27-audit.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    with (folder/'2026-27-blocked-claims.csv').open('w',encoding='utf-8-sig',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(claims[0]));writer.writeheader();writer.writerows(claims)
    print(json.dumps({'rule':report['rule'],'claims':len(claims),'players':len(demand),
                      'luka':next(d for d in demand if d['player_id']=='lukadoncic')},indent=2))


if __name__=='__main__':main()
