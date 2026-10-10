"""Durable real-draft event journal, roster projections and observed manager history."""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from datetime import datetime, timezone
import hmac
import json
import math
import os
import re
import unicodedata

from flask import jsonify, request
from werkzeug.exceptions import BadRequest, Conflict, Forbidden, NotFound, ServiceUnavailable

CATS = ['PTS','REB','AST','STL','BLK','3PM','FG%','FT%']
COUNTS = ['pts_pg','reb_pg','ast_pg','stl_pg','blk_pg','fg3m_pg']
STATS = ['games','minutes_pg',*COUNTS,'fgm_pg','fga_pg','ftm_pg','fta_pg']
EVENT_FIELDS = ['id','type','playerId','team','amount','at','source','history','recovered','targetId','sourcePlayer','sourceTeam','assignments']


def team_key(value):
    return re.sub(r'[^a-z0-9]','',unicodedata.normalize('NFD',str(value or '')).lower())


def now():
    return datetime.now(timezone.utc).isoformat()


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',',':'), allow_nan=False)


def integer(value, low, high):
    return type(value) is int and low <= value <= high


def text(value, limit=160):
    return isinstance(value,str) and bool(value.strip()) and len(value) <= limit


def number(value):
    return type(value) in (int,float) and math.isfinite(value) and value >= 0


def validate_baseline(session):
    if not isinstance(session,dict) or session.get('version') != 1 or session.get('mode') != 'live' or session.get('purpose') != 'real':
        raise BadRequest('Only explicitly marked real drafts can be recorded. Practice and Yahoo mock drafts cannot be saved here.')
    if not text(session.get('id'),200) or not re.fullmatch(r'20\d{2}-\d{2}',session.get('season','')):
        raise BadRequest('Invalid draft identity or season.')
    if session.get('events'):
        raise BadRequest('Create the archive baseline first, then append its events.')
    settings=session.get('settings',{})
    if not isinstance(settings,dict) or not integer(settings.get('rosterSize'),1,30) or not integer(settings.get('minimumBid'),1,20) or not integer(settings.get('reservePerSlot'),settings.get('minimumBid',1),200) or not integer(settings.get('gamesCap',1000),1,3000):
        raise BadRequest('Invalid roster settings.')
    slots=settings.get('rosterSlots')
    if not isinstance(slots,list) or slots and (len(slots)!=settings['rosterSize'] or any(s not in ['PG','SG','SF','PF','C','G','F','UTIL','BN'] for s in slots)):
        raise BadRequest('Invalid position slots.')
    players=session.get('players',[]);teams=session.get('teams',[]);keepers=session.get('keepers',[])
    if not isinstance(players,list) or not 1<=len(players)<=2000 or any(not isinstance(p,dict) or not text(p.get('player_id')) or not text(p.get('player')) or any(not number(p.get(k)) for k in [*STATS,'fair_value','expected_auction_price']) or not isinstance(p.get('z'),dict) or any(type(p['z'].get(k)) not in (int,float) or not math.isfinite(p['z'][k]) for k in CATS) for p in players):
        raise BadRequest('Invalid frozen player projections.')
    if len({p['player_id'] for p in players})!=len(players):
        raise BadRequest('Duplicate player identities.')
    if not isinstance(teams,list) or not 2<=len(teams)<=30 or any(not isinstance(t,dict) or not text(t.get('name')) or not integer(t.get('budget'),1,10000) for t in teams) or len({t['name'] for t in teams})!=len(teams):
        raise BadRequest('Invalid team identities or budgets.')
    if not isinstance(keepers,list) or len(keepers)>900:
        raise BadRequest('Invalid keeper list.')
    baseline={k:deepcopy(session[k]) for k in ['version','id','mode','purpose','season','players','teams','keepers','settings']}
    baseline['baselineRunId']=str(session.get('baselineRunId',''))[:200]
    baseline['createdAt']=str(session.get('createdAt',now()))[:60]
    baseline['events']=[]
    replay(baseline,[])
    return baseline


def event_value(raw):
    if not isinstance(raw,dict) or not text(raw.get('id'),200) or raw.get('type') not in ['nominate','bid','sale','withdraw','undo','team-map']:
        raise BadRequest('Invalid draft event.')
    if raw.get('source') in ('practice','mock','simulated'):
        raise BadRequest('Simulated events cannot enter real draft history.')
    e={k:deepcopy(raw[k]) for k in EVENT_FIELDS if k in raw and raw[k] is not None}
    for k in ['source','at','sourcePlayer','sourceTeam']:
        if k in e and (not isinstance(e[k],str) or len(e[k])>200):
            raise BadRequest('Invalid event metadata.')
    for k in ['history','recovered']:
        if k in e and type(e[k]) is not bool:
            raise BadRequest('Invalid event coverage flag.')
    if e['type']=='team-map':
        assignments=e.get('assignments')
        if not isinstance(assignments,list) or not 1<=len(assignments)<=200 or any(not isinstance(a,dict) or not text(a.get('sourceTeam')) or not team_key(a['sourceTeam']) or not text(a.get('team')) for a in assignments):
            raise BadRequest('Invalid team mapping correction.')
        e['assignments']=[{'sourceTeam':a['sourceTeam'],'team':a['team']} for a in assignments]
    elif e['type']=='undo':
        if not text(e.get('targetId'),200):
            raise BadRequest('Undo must identify an earlier event.')
    elif not text(e.get('playerId')):
        raise BadRequest('An event must identify its player.')
    if e['type'] in ('bid','sale') and (not text(e.get('team')) or not integer(e.get('amount'),1,10000)):
        raise BadRequest('Invalid team or bid amount.')
    return e


def fits(players, slots):
    if not slots:
        return True
    matched={}
    def assign(i,seen):
        positions=re.findall(r'PG|SG|SF|PF|C|(?<![A-Z])G|(?<![A-Z])F',str(players[i].get('positions','')).upper())
        for j,slot in enumerate(slots):
            eligible=slot in ('UTIL','BN') or slot in positions or slot=='G' and any(v in positions for v in ('PG','SG')) or slot=='F' and any(v in positions for v in ('PF','SF'))
            if j not in seen and eligible:
                seen.add(j)
                if j not in matched or assign(matched[j],seen):
                    matched[j]=i
                    return True
        return False
    return all(assign(i,set()) for i in range(len(players)))


def replay(baseline, events):
    players={p['player_id']:p for p in baseline['players']};settings=baseline['settings']
    teams={t['name']:{'name':t['name'],'budget':t['budget'],'remaining':t['budget'],'roster':[]} for t in baseline['teams']}
    taken={};seen={};undone=set()
    for raw in events:
        e=event_value(raw)
        if e['id'] in seen:
            raise BadRequest('Duplicate event ID.')
        if e['type']=='undo':
            if e['targetId'] not in seen or seen[e['targetId']]['type']=='undo':
                raise BadRequest('Undo must reference an earlier non-undo event.')
            undone.add(e['targetId'])
        elif e['type']=='team-map':
            if any(a['team'] not in teams for a in e['assignments']):
                raise BadRequest('Unknown team in mapping correction.')
        elif e['playerId'] not in players or e['type'] in ('bid','sale') and (e['team'] not in teams or e['amount']<settings['minimumBid']):
            raise BadRequest('Unknown player, team or invalid minimum bid.')
        seen[e['id']]=e
    def max_bid(name):
        t=teams[name];slots=settings['rosterSize']-len(t['roster'])
        return max(0,t['remaining']-(slots-1)*settings['minimumBid']) if slots>0 else 0
    for k in baseline['keepers']:
        if not isinstance(k,dict) or k.get('playerId') not in players or k.get('team') not in teams or k['playerId'] in taken or not integer(k.get('amount'),0,10000):
            raise BadRequest('Invalid keeper roster.')
        taken[k['playerId']]=k;teams[k['team']]['roster'].append(k);teams[k['team']]['remaining']-=k['amount']
    for t in teams.values():
        if len(t['roster'])>settings['rosterSize'] or t['remaining']<(settings['rosterSize']-len(t['roster']))*settings['minimumBid'] or not fits([players[k['playerId']] for k in t['roster']],settings['rosterSlots']):
            raise BadRequest('Keeper roster conflicts with budget or slots.')
    nomination=None;bids=[];sales=[];auctions=[];current={}
    assignments={}
    for e in seen.values():
        if e['type']=='team-map' and e['id'] not in undone:
            assignments.update({team_key(a['sourceTeam']):a['team'] for a in e['assignments']})
    for e in seen.values():
        if e['type'] in ('undo','team-map') or e['id'] in undone:
            continue
        if e['type'] in ('bid','sale') and team_key(e.get('sourceTeam')) in assignments:
            e={**e,'team':assignments[team_key(e['sourceTeam'])]}
        pid=e['playerId'];kind=e['type']
        if kind=='nominate':
            if pid in taken or nomination and nomination['playerId']!=pid:
                raise BadRequest('Nomination conflicts with an owned player or unresolved auction.')
            if not nomination:
                nomination={'playerId':pid,'amount':0,'leader':None}
                a={'playerId':pid,'bids':[],'winner':None,'nominated':True,'eligible':{n:max_bid(n) for n in teams}}
                auctions.append(a);current[pid]=a
        elif kind=='bid':
            bids.append(e)
            if not e.get('history') and pid not in taken:
                if not nomination or nomination['playerId']!=pid or e['amount']>max_bid(e['team']):
                    raise BadRequest('Bid conflicts with the nomination or tracked budget.')
                if e['amount']>=nomination['amount']:
                    nomination={'playerId':pid,'amount':e['amount'],'leader':e['team']}
            if pid not in current:
                a={'playerId':pid,'bids':[],'winner':None,'nominated':False,'eligible':{}}
                auctions.append(a);current[pid]=a
            current[pid]['bids'].append(e)
        elif kind=='sale':
            t=teams[e['team']]
            if pid in taken or e['amount']>max_bid(e['team']) or not fits([players[k['playerId']] for k in t['roster']]+[players[pid]],settings['rosterSlots']):
                raise BadRequest('Sale conflicts with owned players, roster slots or the tracked budget.')
            taken[pid]=e;t['roster'].append(e);t['remaining']-=e['amount'];sales.append(e)
            if pid not in current:
                a={'playerId':pid,'bids':[],'winner':None,'nominated':False,'eligible':{}}
                auctions.append(a);current[pid]=a
            current[pid]['winner']=e['team'];current[pid]['price']=e['amount']
            if nomination and nomination['playerId']==pid:
                nomination=None
        elif kind=='withdraw':
            if nomination and nomination['playerId']==pid:
                nomination=None
            current.pop(pid,None)
    return {'teams':teams,'players':players,'sales':sales,'bids':bids,'nomination':nomination,'undone':sorted(undone),'auctions':auctions}


def projections(baseline, state):
    results=[];cap=baseline['settings'].get('gamesCap',1000)
    for t in state['teams'].values():
        roster=[dict(r,player=state['players'][r['playerId']]['player'],positions=state['players'][r['playerId']].get('positions','')) for r in t['roster']]
        ps=[state['players'][r['playerId']] for r in roster];games=sum(p['games'] for p in ps)
        sums={k:sum(p[k]*p['games'] for p in ps) for k in STATS if k not in ('games','minutes_pg')}
        scale=min(1,cap/games) if games else 1
        values={k:sums[f]*scale for k,f in zip(CATS,COUNTS)}
        values.update({'FG%':sums['fgm_pg']/sums['fga_pg'] if sums['fga_pg'] else None,'FT%':sums['ftm_pg']/sums['fta_pg'] if sums['fta_pg'] else None})
        averages={k:sums[f]/games if games else None for k,f in zip(CATS,COUNTS)}
        averages.update({k:values[k] for k in ('FG%','FT%')})
        results.append(dict(t,roster=roster,rawGames=games,games=min(games,cap),values=values,averages=averages,categoryRanks={},categoryPoints={},points=None,rank=None,projectionRunId=baseline['baselineRunId']))
    valid=all(all(r['values'][k] is not None for k in CATS) for r in results)
    for r in results:
        for k in CATS:
            value=r['values'][k];epsilon=1e-7 if '%' in k else 1e-5
            r['categoryRanks'][k]=None if value is None else 1+sum(t['values'][k] is not None and t['values'][k]>value+epsilon for t in results)
            r['categoryPoints'][k]=None
    if valid:
        for k in CATS:
            ordered=sorted(results,key=lambda r:r['values'][k],reverse=True);epsilon=1e-7 if '%' in k else 1e-5
            first=0
            while first<len(ordered):
                last=first
                while last+1<len(ordered) and abs(ordered[last+1]['values'][k]-ordered[first]['values'][k])<=epsilon:
                    last+=1
                points=len(ordered)-(first+last)/2
                for i in range(first,last+1):ordered[i]['categoryPoints'][k]=points
                first=last+1
        for r in results:r['points']=sum(r['categoryPoints'].values())
        for r in results:r['rank']=1+sum(t['points']>r['points'] for t in results)
    return results


def get_record(db, draft_id, lock=False):
    suffix=' FOR UPDATE' if lock and getattr(db,'dialect','')=='postgres' else ''
    row=db.execute('SELECT * FROM recorded_drafts WHERE draft_id=?'+suffix,(draft_id,)).fetchone()
    if not row:
        raise NotFound('Saved draft not found.')
    return dict(row)


def events_for(db, draft_id, revision=None):
    condition=' AND sequence<=?' if revision is not None else ''
    params=(draft_id,revision) if revision is not None else (draft_id,)
    return [json.loads(r[0]) for r in db.execute('SELECT payload_json FROM recorded_draft_events WHERE draft_id=?'+condition+' ORDER BY sequence',params)]


def save_snapshot(db, draft_id, sequence, baseline, events):
    for team in projections(baseline,replay(baseline,events)):
        db.execute('INSERT INTO recorded_team_snapshots(draft_id,sequence,team,payload_json,created_at) VALUES (?,?,?,?,?) ON CONFLICT DO NOTHING',(draft_id,sequence,team['name'],encoded(team),now()))


def create_record(db, session):
    baseline=validate_baseline(session);stamp=now()
    db.execute('INSERT INTO recorded_drafts(draft_id,season,purpose,baseline_json,revision,status,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?) ON CONFLICT DO NOTHING',(baseline['id'],baseline['season'],'real',encoded(baseline),0,'recording',stamp,stamp))
    row=db.execute('SELECT draft_id FROM recorded_drafts WHERE season=?',(baseline['season'],)).fetchone()
    if not row or row[0]!=baseline['id']:
        raise Conflict('A real draft is already saved for this season. Open it from Saved drafts to continue; a second capture cannot replace it.')
    record=get_record(db,baseline['id'],True)
    if record['baseline_json']!=encoded(baseline):
        raise Conflict('This saved draft has a different frozen baseline. Restore it before recording.')
    save_snapshot(db,baseline['id'],0,baseline,[])
    return {'id':record['draft_id'],'revision':record['revision'],'status':record['status']}


def append_events(db, draft_id, body):
    if not isinstance(body,dict) or not integer(body.get('offset'),0,25000) or not isinstance(body.get('events'),list) or not 1<=len(body['events'])<=100:
        raise BadRequest('Append 1–100 events with the last saved event offset.')
    record=get_record(db,draft_id,True);baseline=json.loads(record['baseline_json']);old=events_for(db,draft_id)
    incoming=[event_value(e) for e in body['events']];offset=body['offset']
    if offset>len(old) or offset+len(incoming)>25000:
        raise Conflict('Draft cursor does not match the saved journal.')
    for i,e in enumerate(incoming):
        if offset+i<len(old) and encoded(old[offset+i])!=encoded(e):
            raise Conflict('Another capture has different events at this point. Restore the saved draft before continuing.')
    fresh=incoming[max(0,len(old)-offset):]
    if not fresh:
        return {'revision':len(old),'status':record['status']}
    if record['status']!='recording':
        raise Conflict('This draft is complete. Reopen it before recording a correction.')
    combined=old+fresh;replay(baseline,combined)
    for i,e in enumerate(fresh,len(old)+1):
        db.execute('INSERT INTO recorded_draft_events(draft_id,sequence,event_id,event_type,payload_json,received_at) VALUES (?,?,?,?,?,?)',(draft_id,i,e['id'],e['type'],encoded(e),now()))
        if e['type'] in ('sale','undo','team-map'):
            save_snapshot(db,draft_id,i,baseline,combined[:i])
    db.execute('UPDATE recorded_drafts SET revision=?,updated_at=? WHERE draft_id=?',(len(combined),now(),draft_id))
    return {'revision':len(combined),'status':'recording'}


def manager_history(db, exclude=None):
    profiles={};drafts=db.execute('SELECT draft_id,season,baseline_json,revision FROM recorded_drafts WHERE purpose=? ORDER BY season',('real',)).fetchall()
    for record in drafts:
        if record['draft_id']==exclude:
            continue
        baseline=json.loads(record['baseline_json']);state=replay(baseline,events_for(db,record['draft_id'],record['revision']))
        for name,t in state['teams'].items():
            p=profiles.setdefault(name,{'name':name,'seasons':set(),'observedBids':0,'auctionsEntered':0,'bidAuctionsWon':0,'bidAuctionsLost':0,'repeatRaiseAuctions':0,'premiumOpportunities':0,'premiumEntered':0,'premiumReached':0,'purchases':0,'premiumPurchases':0,'spent':0,'maxBid':0})
            p['seasons'].add(record['season'])
            for a in state['auctions']:
                own=[b for b in a['bids'] if b['team']==name];p['observedBids']+=len(own)
                if own:p['maxBid']=max(p['maxBid'],max(b['amount'] for b in own))
                if not a['winner']:
                    continue  # Outcomes and participation denominators use completed auctions only.
                premium=state['players'][a['playerId']]['expected_auction_price']>=t['budget']*.2
                eligible=premium and a['nominated'] and a['eligible'].get(name,0)>=t['budget']*.2
                p['premiumOpportunities']+=int(eligible);p['premiumEntered']+=int(eligible and bool(own))
                p['premiumReached']+=int(bool(own) and max(b['amount'] for b in own)>=t['budget']*.2)
                if own:
                    p['auctionsEntered']+=1;p['bidAuctionsWon']+=int(a['winner']==name);p['bidAuctionsLost']+=int(a['winner']!=name)
                    known=[b for b in own if not b.get('history') and not b.get('recovered')]
                    p['repeatRaiseAuctions']+=int(len({b['amount'] for b in known})>=2)
                if a['winner']==name:
                    p['purchases']+=1;p['spent']+=a['price'];p['premiumPurchases']+=int(a['price']>=t['budget']*.2)
    all_opportunities=sum(p['premiumOpportunities'] for p in profiles.values());all_entries=sum(p['premiumEntered'] for p in profiles.values())
    league_rate=all_entries/all_opportunities if all_opportunities else None
    for p in profiles.values():
        p['seasons']=sorted(p['seasons']);n=p['premiumOpportunities'];entered=p['auctionsEntered']
        p['premiumParticipation']=p['premiumEntered']/n if n else None
        p['repeatRaiseRate']=p['repeatRaiseAuctions']/entered if entered else None
        p['bidWithoutWinRate']=p['bidAuctionsLost']/entered if entered else None
        # Bounded, shrinkage-based interest signal, not a calibrated bidding probability.
        p['premiumInterestFactor']=round(max(.8,min(1.2,1+((p['premiumEntered']+12*(league_rate or 0))/(n+12)-(league_rate or 0))*.6)),3) if n>=5 else 1
        p['sampleLabel']='Building history' if entered<10 else 'Observed history'
        p['labels']=[]
        if n>=5 and p['premiumParticipation']>(league_rate or 0)+.15:p['labels'].append('Often joins premium auctions')
        if entered>=5 and p['repeatRaiseRate']>=.5:p['labels'].append('Frequent repeat bidder')
        if entered>=5 and p['bidWithoutWinRate']>=.6:p['labels'].append('Often bids without winning')
    return {'teams':profiles,'leaguePremiumParticipation':league_rate,'premiumBudgetShare':.2,'coverage':'Observed bids only. Missing bids and capture gaps can undercount activity. Repeated raises or losing bids do not establish intent.'}


def register_draft_routes(app, database):
    def authorize():
        expected=os.environ.get('DRAFT_RECORDING_KEY','')
        if len(expected)<24:
            raise ServiceUnavailable('Recording is not configured. Set DRAFT_RECORDING_KEY to a private random value of at least 24 characters in the dashboard service.')
        token=request.headers.get('Authorization','').removeprefix('Bearer ')
        if not hmac.compare_digest(token.encode(),expected.encode()):
            raise Forbidden('Enter the league recording key to save or change draft records.')
        if not request.is_json:
            raise BadRequest('A JSON request body is required.')

    @app.get('/api/drafts')
    def draft_list():
        with database() as db:
            rows=[dict(r) for r in db.execute('SELECT draft_id,season,purpose,revision,status,created_at,updated_at FROM recorded_drafts ORDER BY season DESC')]
        return jsonify(drafts=rows,recordingConfigured=len(os.environ.get('DRAFT_RECORDING_KEY',''))>=24)

    @app.post('/api/drafts')
    def draft_create():
        authorize();body=request.get_json()
        if not isinstance(body,dict):raise BadRequest('Invalid draft request.')
        with database(write=True) as db:
            result=create_record(db,body.get('session'))
        return jsonify(result)

    @app.get('/api/drafts/<draft_id>')
    def draft_get(draft_id):
        with database() as db:
            record=get_record(db,draft_id);session=json.loads(record.pop('baseline_json'));session['events']=events_for(db,draft_id,record['revision'])
            versions=[dict(r) for r in db.execute('SELECT sequence,MAX(created_at) AS created_at FROM recorded_team_snapshots WHERE draft_id=? AND sequence<=? GROUP BY sequence ORDER BY sequence DESC',(draft_id,record['revision']))]
            try:at=int(request.args.get('at',record['revision']))
            except (TypeError,ValueError):raise BadRequest('Invalid snapshot sequence.')
            if not 0<=at<=record['revision']:raise BadRequest('Snapshot sequence is outside the saved journal.')
            snap=db.execute('SELECT MAX(sequence) FROM recorded_team_snapshots WHERE draft_id=? AND sequence<=?',(draft_id,at)).fetchone()[0]
            teams=[json.loads(r[0]) for r in db.execute('SELECT payload_json FROM recorded_team_snapshots WHERE draft_id=? AND sequence=? ORDER BY team',(draft_id,snap))]
        return jsonify(record=record,session=session,teams=teams,snapshotSequence=snap,snapshots=versions)

    @app.post('/api/drafts/<draft_id>/events')
    def draft_append(draft_id):
        authorize()
        with database(write=True) as db:
            result=append_events(db,draft_id,request.get_json())
        return jsonify(result)

    @app.post('/api/drafts/<draft_id>/status')
    def draft_status(draft_id):
        authorize();body=request.get_json()
        if not isinstance(body,dict) or body.get('status') not in ('complete','recording') or not integer(body.get('revision'),0,25000):raise BadRequest('Invalid draft status or revision.')
        with database(write=True) as db:
            record=get_record(db,draft_id,True)
            if record['revision']!=body['revision']:raise Conflict('Save all pending events before changing draft status.')
            db.execute('UPDATE recorded_drafts SET status=?,updated_at=? WHERE draft_id=?',(body['status'],now(),draft_id))
        return jsonify(status=body['status'],revision=record['revision'])

    @app.get('/api/draft-managers')
    def draft_managers():
        with database() as db:
            result=manager_history(db,request.args.get('exclude'))
        return jsonify(result)
