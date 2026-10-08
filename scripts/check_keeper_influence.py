"""Select a capped keeper-evidence contribution using earlier auction errors."""
import contextlib
import io
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from survivor.catalog import ROOT,build_catalog,rows
from survivor.database import preview_database
from survivor.keepers import sync_bundled_keepers
from survivor.bundled_stats import sync_bundled_stats,sync_bundled_projections
from survivor.historical_projections import sync_historical_projections
from survivor.market_context import load_context
from survivor.valuation import CONFIG,load_inputs,training_observations,PriceModel,metrics,profiles,COMPARISON_FIELDS
from survivor.auction_context import market_observations,auction_context
from survivor.keeper_evidence import attach_contracts,add_claims,keeper_adjustment,combine_support

# Fixed before examining results; all variants use exactly the v9 auction fit.
RULES={'maximum_seasons_ago':3,'repeat_share':.25,'supporting_share':.35,
       'claim_share':.5,'auction_overlap_share':.5,'maximum_share':1,'scope':'all_players'}
VARIANTS={'v9':{'scope':'all_players','maximum_share':0},
          **{f'{scope}_{int(cap*100)}':{'scope':scope,'maximum_share':cap}
             for scope in ('all_players','same_player') for cap in (.1,.2,.3,.4)}}


def main():
    settings=json.loads(CONFIG.read_text());settings.pop('keeper_evidence',None)
    rules=json.loads((ROOT/'config/league.json').read_text());context=load_context()
    db=preview_database()
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            build_catalog(ROOT/'Survivor keeper log 2025.xlsx',db)
            sync_bundled_keepers(db);sync_bundled_stats(db);sync_bundled_projections(db)
        projection,actuals,statistics,auctions,keepers,draft=load_inputs(db,'2026-27')
        archives=sync_historical_projections(db)
        forecasts={a['season']:{'metadata':a,'players':{p['player_id']:p for p in a['records']}} for a in archives}
        observations,_=training_observations(actuals,statistics,auctions,keepers,rules,settings,context,forecasts=forecasts)
        keeper_records,excluded=training_observations(actuals,statistics,auctions,keepers,rules,settings,context,forecasts=forecasts,profile_rows=keepers)
        claims=rows(db,'SELECT * FROM keeper_claims ORDER BY season,franchise,choice_rank')
    finally:db.close()
    keeper_records=attach_contracts(keeper_records,auctions)
    results={name:[] for name in VARIANTS};selected=None
    for season in sorted({o['season'] for o in observations}):
        if season<settings['development_first_season']:continue
        if selected is None and season>=settings['holdout_first_season']:
            selected=min(VARIANTS,key=lambda name:(metrics(results[name])['mae'],name))
            print('Development-only choice:',selected,flush=True)
        models={age:PriceModel(market_observations([o for o in observations if o['season']<season]),settings,int(season[:4]),age=age) for age in (False,True)}
        for item in [o for o in observations if o['season']==season]:
            model=models[item['profile'].get('age') is not None]
            parts=model.local_components(item['profile']);market=item['auction_context']
            baseline=min(200,max(1,1+(parts['base']+parts['correction'])*market['market_scale']))
            evidence=keeper_adjustment(model,item['player_id'],item['profile'],season,market,baseline,keeper_records,RULES,parts['neighbors'])
            for name,variant in VARIANTS.items():
                matching=[r for r in evidence['records'] if variant['scope']=='all_players' or r['player_id']==item['player_id']]
                adjusted=combine_support(baseline,matching,{**RULES,**variant})
                results[name].append({'season':season,'player_id':item['player_id'],'actual':item['recorded_cost'],
                                      'predicted':baseline+adjusted['adjustment'],'adjustment':adjusted['adjustment']})
    report={'basis':'Fixed scope/cap candidates; v9 auction model unchanged. Select on 610 development auctions only. Same-season keepers are known before the auction; only preseason forecasts and preceding actual pools are used. Later seasons are reused checks, not independent validation.',
            'claim_calibration':'No older unsuccessful-claim logs exist. Claim reliability is an explicit working assumption, not calibrated from these historical errors.',
            'rules':RULES,'variants':VARIANTS,'selected':selected,'keeper_profiles':len(keeper_records),'excluded_profiles':len(excluded),
            'development':{},'holdout':{},'development_expensive':{},'holdout_expensive':{},'affected':{}}
    for name,records in results.items():
        dev=[r for r in records if r['season']<settings['holdout_first_season']];held=[r for r in records if r['season']>=settings['holdout_first_season']]
        report['development'][name]=metrics(dev);report['holdout'][name]=metrics(held)
        report['development_expensive'][name]=metrics([r for r in dev if r['actual']>=30])
        report['holdout_expensive'][name]=metrics([r for r in held if r['actual']>=30])
        report['affected'][name]={'development':sum(r['adjustment']>0 for r in dev),'holdout':sum(r['adjustment']>0 for r in held)}
    players=statistics[projection['dataset_id']];scored,ref=profiles(players,pool_size=settings['reference_pool_size'],weights=settings['category_weights'])
    target_profiles={p['player_id']:{**scored[p['player_id']],'age':(context.before(p['player_id'],'2026-27') or {}).get('target_season_age'),
                     'projected_games':p['games'],'stats':{k:p[k] for k in COMPARISON_FIELDS},'comparison_reference':ref,'outlook_basis':'projection'} for p in players}
    market=auction_context('2026-27',players,scored,[{'player_id':k['player_id'],'franchise_sheet':k['franchise'],'recorded_cost':k['keeper_cost']} for k in draft['rows']],
                           [t['franchise'] for t in draft['teams']],rules,settings)
    current=[{'season':'2026-27','player_id':k['player_id'],'player':k['player'],'franchise_sheet':k['franchise'],'recorded_cost':k['keeper_cost'],
              'profile':target_profiles[k['player_id']],'auction_context':market,'forecast_dataset_id':projection['dataset_id']} for k in draft['rows']]
    records=attach_contracts(add_claims(keeper_records+current,claims),auctions)
    model=PriceModel(market_observations(observations),settings,2026,age=True)
    report['examples']={}
    for key in ('lukadoncic','nikolajokic','anthonyedwards','jalenbrunson'):
        profile=target_profiles[key];parts=model.local_components(profile)
        baseline=min(200,max(1,1+(parts['base']+parts['correction'])*market['market_scale']))
        evidence=keeper_adjustment(model,key,profile,'2026-27',market,baseline,records,RULES,parts['neighbors'])
        output={}
        for name,variant in VARIANTS.items():
            matching=[r for r in evidence['records'] if variant['scope']=='all_players' or r['player_id']==key]
            d=combine_support(baseline,matching,{**RULES,**variant})
            output[name]={'expected':baseline+d['adjustment'],'share':d['share'],'decisions':d['supporting_decisions'],'claims':d['supporting_claims']}
        report['examples'][key]={'auction_estimate':baseline,'variants':output,'eligible_records':evidence['records']}
    (ROOT/'output/valuations/keeper-influence-check.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k!='examples'},indent=2),flush=True)


if __name__=='__main__':main()
