"""Time-ordered audit of comp profile adjustments, using bundled evidence only."""
import contextlib
import argparse
import io
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from survivor.catalog import ROOT,build_catalog
from survivor.database import preview_database
from survivor.keepers import sync_bundled_keepers
from survivor.bundled_stats import sync_bundled_stats,sync_bundled_projections
from survivor.historical_projections import sync_historical_projections
from survivor.market_context import load_context
from survivor.valuation import (CONFIG,load_inputs,training_observations,PriceModel,metrics,
                               profiles,feature_vector,CATEGORIES,COUNT_FIELDS,COMPARISON_FIELDS)
from survivor.auction_context import market_observations,auction_context

# Freeze the candidates before examining errors or current player prices.
# Current projections never choose the adjustment strength.
VARIANTS={'v8':{'reference':'original','curve_share':1},
          **{f'{ref}_curve_{int(s*100)}':{'reference':ref,'curve_share':s}
             for ref in ('original','common') for s in (0,.25,.5,.75,1)
             if not (ref=='original' and s==1)}}


def rebase(other, target):
    ref=target['comparison_reference']; p=other['stats']
    contributions=[p[k] for k in COUNT_FIELDS]+[p['fgm_pg']-ref['fg_baseline']*p['fga_pg'],
                                              p['ftm_pg']-ref['ft_baseline']*p['fta_pg']]
    z={cat:(v-ref['means'][cat])/ref['standard_deviations'][cat] for cat,v in zip(CATEGORIES,contributions)}
    return {**other,'z':z,'score':sum(z.values()),'comparison_reference':ref}


def estimates(model,profile,scale,variants=VARIANTS):
    parts=model.local_components(profile); base=parts['base']; weighted=parts['neighbors']
    baskets=dict.fromkeys(variants,0.)
    for w in weighted:
        observation=model.observations[w['index']]
        others={'original':observation['profile'],'common':rebase(observation['profile'],profile)}
        for name,variant in variants.items():
            other=others[variant['reference']]
            curve_index=len(CATEGORIES)
            curve=model.beta[curve_index+1]/model.scales[curve_index]*(max(0,profile['score'])**2-max(0,other['score'])**2)/8
            delta=(base-model.raw_prediction(other)-(1-variant['curve_share'])*curve)*variant.get('profile_share',1)
            baskets[name]+=w['weight']*(observation['target']+delta)
    return {name:min(200,max(1,1+(base+parts['correction_share']*(basket-base))*scale))
            for name,basket in baskets.items()}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--initial',action='store_true',help='Reproduce the rejected uniform profile-shrinkage experiment.')
    args=parser.parse_args()
    variants=VARIANTS if not args.initial else {'v8':{'reference':'original','curve_share':1},
        **{f'common_{int(s*100)}':{'reference':'common','curve_share':1,'profile_share':s} for s in (0,.25,.5,.75,1)}}
    settings=json.loads(CONFIG.read_text()); rules=json.loads((ROOT/'config/league.json').read_text())
    settings.pop('comp_profile_adjustment',None)
    db=preview_database()
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            build_catalog(ROOT/'Survivor keeper log 2025.xlsx',db)
            sync_bundled_keepers(db);sync_bundled_stats(db);sync_bundled_projections(db)
        projection,actuals,statistics,auctions,keepers,draft=load_inputs(db,'2026-27')
        archives=sync_historical_projections(db)
        forecasts={a['season']:{'metadata':a,'players':{p['player_id']:p for p in a['records']}} for a in archives}
        context=load_context()
        observations,_=training_observations(actuals,statistics,auctions,keepers,rules,settings,context,forecasts=forecasts)
    finally:
        db.close()
    results={name:[] for name in variants}; selected=None
    for season in sorted({o['season'] for o in observations}):
        if season<settings['development_first_season']:continue
        if selected is None and season>=settings['holdout_first_season']:
            selected=min(variants,key=lambda name:(metrics(results[name])['mae'],name))
            print('Development-only selection:',selected,flush=True)
            for name in variants:print(name,metrics(results[name]),flush=True)
        train=market_observations([o for o in observations if o['season']<season])
        models={age:PriceModel(train,settings,int(season[:4]),age=age) for age in (False,True)}
        for item in [o for o in observations if o['season']==season]:
            m=models[item['profile'].get('age') is not None]
            predictions=estimates(m,item['profile'],item['auction_context']['market_scale'],variants)
            for name,price in predictions.items():
                results[name].append({'season':season,'player_id':item['player_id'],'actual':item['recorded_cost'],'predicted':price})
    report={'basis':'Second exploratory audit after rejecting uniform profile shrinkage (retained in profile-adjustment-initial-check.json) and decomposing the squared-score term. Fixed curve-only candidates hold v8 regression and comp weights constant. Select on 2018-19 through 2022-23 auction MAE only. Later seasons are reused retrospective checks, not independent validation.',
            'variants':variants,'selected':selected,'development':{},'holdout':{},'development_expensive':{},'holdout_expensive':{}}
    if args.initial:
        report['basis']='Initial fixed candidates uniformly shrinking all common-reference profile differences. Development selects the unchanged v8 baseline; rejected alternatives and reused later checks are preserved.'
    for name,records in results.items():
        dev=[r for r in records if r['season']<settings['holdout_first_season']]
        held=[r for r in records if r['season']>=settings['holdout_first_season']]
        report['development'][name]=metrics(dev);report['holdout'][name]=metrics(held)
        report['development_expensive'][name]=metrics([r for r in dev if r['actual']>=30])
        report['holdout_expensive'][name]=metrics([r for r in held if r['actual']>=30])
    players=statistics[projection['dataset_id']]
    scored,ref=profiles(players,pool_size=settings['reference_pool_size'],weights=settings['category_weights'])
    current=auction_context('2026-27',players,scored,
        [{'player_id':k['player_id'],'franchise_sheet':k['franchise'],'recorded_cost':k['keeper_cost']} for k in draft['rows']],
        [t['franchise'] for t in draft['teams']],rules,settings)
    model=PriceModel(market_observations(observations),settings,2026,age=True)
    examples={}
    for key in ('lukadoncic','jalenbrunson','anthonyedwards','nikolajokic'):
        p=next(p for p in players if p['player_id']==key)
        profile={**scored[key],'age':context.before(key,'2026-27')['target_season_age'],'projected_games':p['games'],
                 'stats':{k:p[k] for k in COMPARISON_FIELDS},'comparison_reference':ref,'outlook_basis':'projection'}
        comps=[]
        for w in model.weighted_neighbors(profile)[:3]:
            o=model.observations[w['index']]; old=o['profile']; common=rebase(old,profile)
            vectors=[feature_vector(v,age=True) for v in (profile,old,common)]
            old_parts=[b*(x-y)/s*current['market_scale'] for b,x,y,s in zip(model.beta[1:],vectors[0],vectors[1],model.scales)]
            common_parts=[b*(x-y)/s*current['market_scale'] for b,x,y,s in zip(model.beta[1:],vectors[0],vectors[2],model.scales)]
            comps.append({'player':o['player'],'season':o['season'],'weight':w['weight'],
                          'paid':o['recorded_cost'],'budget_adjusted':1+o['target']*current['market_scale'],
                          'old_adjustment':sum(old_parts),'common_adjustment':sum(common_parts),
                          'old_components':dict(zip(list(CATEGORIES)+['score_squared','age','age_score'],old_parts)),
                          'common_components':dict(zip(list(CATEGORIES)+['score_squared','age','age_score'],common_parts))})
        examples[key]={'expected_prices':estimates(model,profile,current['market_scale'],variants),'comps':comps}
    report['examples']=examples
    filename='profile-adjustment-initial-check.json' if args.initial else 'profile-adjustment-check.json'
    (ROOT/'output/valuations'/filename).write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2),flush=True)


if __name__=='__main__':main()
