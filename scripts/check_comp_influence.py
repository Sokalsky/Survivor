"""Reproduce the v6-to-v7 comp influence comparison using bundled data only."""
import contextlib
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

# Fixed comparison set before looking at errors; the holdout is not selected on.
variants={'v6':(.8,.5),'strong_75':(.8,.75),'strong_100':(.8,1.),
          'close_90_75':(.9,.75),'close_90_100':(.9,1.)}
settings=json.loads(CONFIG.read_text()); rules=json.loads((ROOT/'config/league.json').read_text())
# Fit the same ridge model for every influence candidate; only the blend changes.
settings['comp_rules'].pop('near_identical',None)
settings['comp_rules']['strong_minimum_weight']=.8
settings['comp_rules']['supporting_correction_share']=.25
db=preview_database()
try:
    with contextlib.redirect_stdout(io.StringIO()):
        build_catalog(ROOT/'Survivor keeper log 2025.xlsx',db)
        sync_bundled_keepers(db);sync_bundled_stats(db);sync_bundled_projections(db)
    _,actuals,statistics,auctions,keeper_history,_=load_inputs(db,'2026-27')
    archives=sync_historical_projections(db)
    forecasts={a['season']:{'metadata':{k:v for k,v in a.items() if k not in ('records','rejected_records')},
                            'players':{p['player_id']:p for p in a['records']}} for a in archives}
    observations,_=training_observations(actuals,statistics,auctions,keeper_history,rules,settings,load_context(),forecasts=forecasts)
finally:
    db.close()
results={name:[] for name in variants}
selected=None
for season in sorted({o['season'] for o in observations}):
    if season<settings['development_first_season']:continue
    if selected is None and season>=settings['holdout_first_season']:
        selected=min(variants,key=lambda name:metrics(results[name])['mae'])
        print('Development-only selection:',selected,flush=True)
        for name in variants:print(name,metrics(results[name]),flush=True)
    train=market_observations([o for o in observations if o['season']<season])
    models={False:PriceModel(train,settings,int(season[:4])),True:PriceModel(train,settings,int(season[:4]),age=True)}
    for item in [o for o in observations if o['season']==season]:
        m=models[item['profile'].get('age') is not None];p=item['profile']
        weighted=m.weighted_neighbors(p);base=m.raw_prediction(p)
        strong=[r for r in weighted if r['match_quality']=='strong'];weak=[r for r in weighted if r['match_quality']=='supporting']
        sw=sum(w['weight'] for w in strong);ww=sum(w['weight'] for w in weak)
        sr=sum(w['weight']*m.residuals[w['index']] for w in strong)/sw if sw else 0
        wr=sum(w['weight']*m.residuals[w['index']] for w in weak)/ww if ww else 0
        scale=item['auction_context']['market_scale']
        for name,(minimum,alpha) in variants.items():
            fraction=max(minimum,sw) if strong and weak else 1 if strong else 0
            residual=fraction*sr+(1-fraction)*wr
            correction=alpha if strong else settings['comp_rules']['supporting_correction_share'] if weak else 0
            results[name].append({'season':season,'player_id':item['player_id'],'actual':item['recorded_cost'],
                                  'predicted':min(200,max(1,1+(base+correction*residual)*scale)),
                                  'strong_count':len(strong)})
report={'basis':'Predefined influence variants; selection uses 2018-19 through 2022-23 MAE only. Each forecast uses earlier auctions. Later seasons are reused retrospective checks.', 'variants':variants,'selected':selected,'development':{},'holdout':{},'holdout_strong':{},'holdout_expensive':{}}
for name,records in results.items():
    dev=[r for r in records if r['season']<settings['holdout_first_season']];held=[r for r in records if r['season']>=settings['holdout_first_season']]
    report['development'][name]=metrics(dev);report['holdout'][name]=metrics(held)
    report['holdout_strong'][name]=metrics([r for r in held if r['strong_count']])
    report['holdout_expensive'][name]=metrics([r for r in held if r['actual']>=30])
(ROOT/'output/valuations/comp-influence-check.json').write_text(json.dumps(report,indent=2))
print(json.dumps(report,indent=2),flush=True)
