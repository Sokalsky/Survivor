"""Audit a stricter forecast-match tier against past auctions; bundled data only."""
import contextlib
import argparse
import copy
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
from survivor.valuation import CONFIG,load_inputs,training_observations,PriceModel,metrics
from survivor.auction_context import market_observations

# Freeze these choices before examining price errors. Price/player identity is
# never a qualification input. Only the development split selects a setting.
variants={'v7':None,'near_90_90':(.9,.9),'near_95_90':(.95,.9),'near_95_95':(.95,.95)}
settings=json.loads(CONFIG.read_text())
settings.pop('comp_profile_adjustment',None)
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--broad',action='store_true',help='Reproduce the rejected initial rule without known-GP or separation requirements.')
args=parser.parse_args()
if args.broad:
    settings['comp_rules']['near_identical']['requires_known_games']=False
    settings['comp_rules']['near_identical']['separation_ratio']=None
rules=json.loads((ROOT/'config/league.json').read_text())
old_settings=copy.deepcopy(settings)
old_settings['comp_rules'].pop('near_identical')
unfloored=copy.deepcopy(settings)
unfloored['comp_rules']['near_identical']['minimum_weight']=0
db=preview_database()
try:
    with contextlib.redirect_stdout(io.StringIO()):
        build_catalog(ROOT/'Survivor keeper log 2025.xlsx',db)
        sync_bundled_keepers(db);sync_bundled_stats(db);sync_bundled_projections(db)
    _,actuals,statistics,auctions,keeper_history,_=load_inputs(db,'2026-27')
    archives=sync_historical_projections(db)
    forecasts={a['season']:{'metadata':a,'players':{p['player_id']:p for p in a['records']}} for a in archives}
    observations,_=training_observations(actuals,statistics,auctions,keeper_history,rules,settings,load_context(),forecasts=forecasts)
finally:
    db.close()
results={name:[] for name in variants}
selected=None
selection_checked=False
for season in sorted({o['season'] for o in observations}):
    if season<settings['development_first_season']:continue
    if not selection_checked and season>=settings['holdout_first_season']:
        selection_checked=True
        if any(r['near_identical_count'] for r in results['v7']):
            selected=min(variants,key=lambda name:metrics(results[name])['mae'])
        print('Development-only selection:',selected,flush=True)
        for name in variants:print(name,metrics(results[name]),flush=True)
    train=market_observations([o for o in observations if o['season']<season])
    models={age:PriceModel(train,old_settings,int(season[:4]),age=age) for age in (False,True)}
    for item in [o for o in observations if o['season']==season]:
        m=models[item['profile'].get('age') is not None];p=item['profile']
        # Changing only match settings preserves the exact fitted ridge model.
        m.settings=old_settings
        previous=m.local_components(p)
        m.settings=unfloored
        weighted=m.weighted_neighbors(p)
        precise=[w for w in weighted if w['match_quality']=='near_identical']
        rest=[w for w in weighted if w['match_quality']!='near_identical']
        share=sum(w['weight'] for w in precise)
        precise_residual=sum(w['weight']*m.residuals[w['index']] for w in precise)/share if precise else 0
        remainder=sum(w['weight'] for w in rest)
        rest_residual=sum(w['weight']*m.residuals[w['index']] for w in rest)/remainder if rest else 0
        for name,variant in variants.items():
            prediction=previous['base']+previous['correction']
            if variant and precise:
                minimum,alpha=variant
                fraction=max(share,minimum) if rest else 1
                prediction=previous['base']+alpha*(fraction*precise_residual+(1-fraction)*rest_residual)
            price=min(200,max(1,1+prediction*item['auction_context']['market_scale']))
            results[name].append({'season':season,'player_id':item['player_id'],'actual':item['recorded_cost'],
                                  'predicted':price,'near_identical_count':len(precise),
                                  'same_player_match':any(m.observations[w['index']]['player_id']==item['player_id'] for w in precise)})
report={'basis':'Revised qualification after the broad-rule audit: require known projected games and clear separation. These checks are exploratory, not independent validation. Historical development archives lack GP, so a zero matched development sample cannot select influence weights.',
        'qualification':{k:v for k,v in settings['comp_rules']['near_identical'].items() if k not in ('minimum_weight','correction_share')},
        'variants':variants,'selected':selected,'development':{},'holdout':{},'development_matched':{},'holdout_matched':{},'holdout_expensive':{}}
if args.broad:
    report['basis']='Initial broad rule, rejected after worse errors on both splits. Known projected GP and relative separation were not required.'
else:
    report['deployment_choice']='near_95_90'
    report['deployment_basis']='Working weights implementing the requested dominant influence of exceptionally close matches; not chosen by held-out errors.'
for name,records in results.items():
    dev=[r for r in records if r['season']<settings['holdout_first_season']]
    held=[r for r in records if r['season']>=settings['holdout_first_season']]
    report['development'][name]=metrics(dev);report['holdout'][name]=metrics(held)
    report['development_matched'][name]=metrics([r for r in dev if r['near_identical_count']])
    report['holdout_matched'][name]=metrics([r for r in held if r['near_identical_count']])
    report['holdout_expensive'][name]=metrics([r for r in held if r['actual']>=30])
report['affected_auctions']=[{k:r[k] for k in ('season','player_id','near_identical_count','same_player_match')} for r in results['v7'] if r['near_identical_count']]
name='near-identical-broad-check.json' if args.broad else 'near-identical-check.json'
(ROOT/'output/valuations'/name).write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps(report,indent=2),flush=True)
