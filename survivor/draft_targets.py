"""Draft-board signals derived from saved values; no changes to price models."""
import json
import math

CATEGORIES=('PTS','REB','AST','STL','BLK','3PM','FG%','FT%')
VALUE_TIERS={'elite':'Elite value','solid':'Solid value','even':'Evenly valued',
             'slight_over':'Slightly overvalued','overvalued':'Overvalued','unrated':'Unrated'}
VALUE_FILTERS={'all','undervalued',*VALUE_TIERS}
PROFILE_FILTERS={'all','all_eight','balanced','percentages'}
TARGET_FIELDS=('value_tier','value_gap','value_gap_pct','positive_categories','category_floor',
               'fg_impact','ft_impact','shooting_floor','all_categories_positive','both_percentages_positive')


def finite(value):
    return isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value)


def target_metrics(row):
    neutral,expected=row.get('fair_value'),row.get('expected_auction_price')
    result=dict.fromkeys(TARGET_FIELDS)
    result['value_tier']='unrated'
    if finite(neutral) and finite(expected) and neutral>=0 and expected>=0:
        gap=round(neutral-expected,2)
        solid=max(3,.10*neutral);elite=max(8,.25*neutral)
        tier=('elite' if gap>=elite-1e-9 else 'solid' if gap>=solid-1e-9 else
              'overvalued' if gap<=-elite+1e-9 else 'slight_over' if gap<=-solid+1e-9 else 'even')
        result.update(value_gap=gap,value_gap_pct=gap/neutral if neutral else None,value_tier=tier)
    details=row.get('category_values_json') or '{}'
    details=json.loads(details) if isinstance(details,str) else details
    z=details.get('rate_z',{})
    if all(finite(z.get(k)) for k in CATEGORIES):
        count=sum(z[k]>1e-9 for k in CATEGORIES)
        fg,ft=z['FG%'],z['FT%']
        result.update(positive_categories=count,category_floor=min(z[k] for k in CATEGORIES),
                      fg_impact=fg,ft_impact=ft,shooting_floor=min(fg,ft),
                      all_categories_positive=count==8,both_percentages_positive=min(fg,ft)>1e-9)
    return result


def target_matches(row,value='all',profile='all'):
    price_matches=(value=='all' or row['value_tier']==value or
                   value=='undervalued' and row['value_tier'] in ('solid','elite'))
    profile_matches=(profile=='all' or profile=='all_eight' and row['all_categories_positive'] is True or
                     profile=='percentages' and row['both_percentages_positive'] is True or
                     profile=='balanced' and (row['positive_categories'] or 0)>=7 and row['both_percentages_positive'] is True)
    return price_matches and profile_matches
