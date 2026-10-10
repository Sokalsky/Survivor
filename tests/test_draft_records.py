import copy
import json
import os
import unittest
from unittest.mock import patch

from survivor.database import preview_database
from survivor.dashboard import create_app
from survivor.draft_records import manager_history, projections, replay

KEY='test-only-recording-key-0123456789'

def baseline(draft_id='real-test',season='2026-27'):
    players=[]
    for i in range(12):
        players.append(dict(player_id='p'+str(i),player='Player '+str(i),positions='PG,C',nba_team='NBA',games=50,minutes_pg=30,pts_pg=20,reb_pg=5,ast_pg=4,stl_pg=1,blk_pg=1,fg3m_pg=2,fgm_pg=5,fga_pg=10,ftm_pg=4,fta_pg=5,fair_value=40,expected_auction_price=50,z={k:0 for k in ['PTS','REB','AST','STL','BLK','3PM','FG%','FT%']}))
    return dict(version=1,id=draft_id,mode='live',purpose='real',season=season,createdAt='2026-10-09T12:00:00Z',baselineRunId='frozen-test-run',players=players,teams=[dict(name=t,budget=200) for t in ['Max','A','B']],keepers=[dict(playerId='p0',team='Max',amount=10,keeper=True),dict(playerId='p1',team='A',amount=10,keeper=True),dict(playerId='p2',team='B',amount=10,keeper=True)],settings=dict(rosterSize=10,minimumBid=1,reservePerSlot=1,rosterSlots=[],gamesCap=1000),events=[])

def event(id,kind,player='p3',team=None,amount=None,**extra):
    return dict(id=id,type=kind,playerId=player,**({'team':team,'amount':amount} if team else {}),at='2026-10-09T12:00:00Z',source='yahoo-observed',**extra)

class DraftRecordsTests(unittest.TestCase):
    def setUp(self):
        self.db=preview_database();self.app=create_app(self.db);self.client=self.app.test_client();self.env=patch.dict(os.environ,{'DRAFT_RECORDING_KEY':KEY});self.env.start()
    def tearDown(self):
        self.env.stop();self.db.close()
    def post(self,path,body):
        return self.client.post(path,json=body,headers={'Authorization':'Bearer '+KEY})
    def create(self,s=None):
        r=self.post('/api/drafts',{'session':s or baseline()});self.assertEqual(r.status_code,200,r.json);return r
    def append(self,events,offset=0,id='real-test'):
        return self.post('/api/drafts/'+id+'/events',dict(offset=offset,events=events))
    def test_writes_require_configured_key_and_reads_are_public(self):
        self.assertEqual(self.client.post('/api/drafts',json={'session':baseline()}).status_code,403)
        with patch.dict(os.environ,{'DRAFT_RECORDING_KEY':''}):
            self.assertFalse(self.client.get('/api/drafts').json['recordingConfigured'])
            self.assertEqual(self.post('/api/drafts',{'session':baseline()}).status_code,503)
        self.create();self.assertEqual(len(self.client.get('/api/drafts').json['drafts']),1)
        self.assertEqual(self.client.get('/api/drafts/real-test').status_code,200)
    def test_team_correction_preserves_original_events_and_snapshots(self):
        self.create()
        batch=[event('n','nominate'),event('b','bid',team='A',amount=30,sourceTeam='Ozzy'),event('s','sale',team='A',amount=30,sourceTeam='ozzy')]
        self.assertEqual(self.append(batch).status_code,200)
        original=self.client.get('/api/drafts/real-test').json['session']['events']
        correction=dict(id='map',type='team-map',source='manual-confirmed',assignments=[dict(sourceTeam='Ozzy',team='B')])
        response=self.append([correction],3);self.assertEqual(response.status_code,200,response.json)
        saved=self.client.get('/api/drafts/real-test').json
        self.assertEqual(saved['session']['events'][:3],original)
        self.assertEqual(next(t for t in saved['teams'] if t['name']=='B')['remaining'],160)
        self.assertEqual(replay(saved['session'],saved['session']['events'])['bids'][0]['team'],'B')
        old=self.client.get('/api/drafts/real-test?at=3').json
        self.assertEqual(next(t for t in old['teams'] if t['name']=='A')['remaining'],160)
        self.assertEqual(self.append([dict(id='undo-map',type='undo',targetId='map')],4).status_code,200)
        undone=self.client.get('/api/drafts/real-test').json
        self.assertEqual(next(t for t in undone['teams'] if t['name']=='A')['remaining'],160)
    def test_yahoo_team_names_preserve_baseline_and_ignore_wrong_owner_maps(self):
        self.create()
        old=[event('s','sale',team='A',amount=30,sourceTeam='Ozzy'),event('mine','sale',player='p4',team='Max',amount=20,sourceTeam='You')]
        self.assertEqual(self.append(old).status_code,200)
        identity=dict(id='names',type='yahoo-teams',teamNames={'Max':'Cookin in Jokicin','A':'Shut Down','B':'Ozzy'},ownTeam='Cookin in Jokicin')
        response=self.append([identity],2);self.assertEqual(response.status_code,200,response.json)
        saved=self.client.get('/api/drafts/real-test').json
        self.assertEqual(saved['session']['teams'],baseline()['teams'])
        self.assertEqual(saved['session']['events'][:2],old)
        self.assertEqual(next(t for t in saved['teams'] if t['name']=='Ozzy')['remaining'],160)
        self.assertEqual(next(t for t in saved['teams'] if t['name']=='Cookin in Jokicin')['remaining'],170)
        # Even a retained incorrect owner alias cannot redirect an observed Yahoo name.
        correction=dict(id='bad-old-map',type='team-map',assignments=[dict(sourceTeam='Ozzy',team='A')])
        self.assertEqual(self.append([correction,event('next','sale',player='p5',team='A',amount=10,sourceTeam='Ozzy')],3).status_code,200)
        saved=self.client.get('/api/drafts/real-test').json
        self.assertEqual(next(t for t in saved['teams'] if t['name']=='Ozzy')['remaining'],150)
        self.assertEqual(self.create().status_code,200)  # same frozen baseline still resumes
    def test_team_correction_rejects_overspending_without_partial_write(self):
        self.create()
        self.assertEqual(self.append([event('a','sale',team='A',amount=150,sourceTeam='Ozzy'),event('b','sale',player='p4',team='B',amount=150,sourceTeam='Shut Down')]).status_code,200)
        correction=dict(id='map',type='team-map',assignments=[dict(sourceTeam='Ozzy',team='B')])
        self.assertEqual(self.append([correction],2).status_code,400)
        self.assertEqual(self.client.get('/api/drafts/real-test').json['record']['revision'],2)
    def test_mock_simulator_and_simulated_events_cannot_enter_real_archive(self):
        for changes in [dict(mode='practice'),dict(purpose='mock'),dict(purpose=None)]:
            s=baseline();s.update(changes);self.assertEqual(self.post('/api/drafts',{'session':s}).status_code,400)
        self.create();self.assertEqual(self.append([event('n','nominate',sourcePlayer='Player 3')|{'source':'practice'}]).status_code,400)
        self.assertEqual(self.client.get('/api/drafts/real-test').json['record']['revision'],0)
    def test_events_are_idempotent_and_conflicting_writers_never_replace_history(self):
        self.create();batch=[event('n','nominate'),event('b','bid',team='A',amount=30),event('s','sale',team='B',amount=31)]
        self.assertEqual(self.append(batch).json['revision'],3);self.assertEqual(self.append(batch).json['revision'],3)
        conflict=copy.deepcopy(batch);conflict[1]['amount']=32
        self.assertEqual(self.append(conflict).status_code,409)
        a=self.client.get('/api/drafts/real-test').json
        self.assertEqual(a['session']['events'],batch);self.assertEqual(a['record']['revision'],3)
        self.assertEqual(len(a['snapshots']),2)
    def test_one_real_record_per_season_and_baseline_is_frozen(self):
        self.create();self.assertEqual(self.post('/api/drafts',{'session':baseline('different')}).status_code,409)
        changed=baseline();changed['players'][0]['pts_pg']=100
        self.assertEqual(self.post('/api/drafts',{'session':changed}).status_code,409)
        self.assertEqual(self.client.get('/api/drafts/real-test').json['session']['players'][0]['pts_pg'],20)
    def test_bad_batch_rolls_back_every_event_and_snapshot(self):
        self.create();r=self.append([event('n','nominate'),event('b','bid',team='A',amount=300),event('s','sale',team='A',amount=300)])
        self.assertEqual(r.status_code,400);a=self.client.get('/api/drafts/real-test').json
        self.assertEqual(a['record']['revision'],0);self.assertEqual(a['session']['events'],[]);self.assertEqual(len(a['snapshots']),1)
    def test_rosters_budgets_weighted_projections_and_original_snapshot_are_saved(self):
        s=baseline();s['players'][3].update(games=20,pts_pg=30,fgm_pg=9,fga_pg=10);s['settings']['gamesCap']=60;self.create(s)
        self.assertEqual(self.append([event('sale','sale',team='Max',amount=40)]).status_code,200)
        a=self.client.get('/api/drafts/real-test').json;team=next(t for t in a['teams'] if t['name']=='Max')
        self.assertEqual([r['playerId'] for r in team['roster']],['p0','p3']);self.assertEqual(team['remaining'],150)
        self.assertAlmostEqual(team['values']['PTS'],1600*60/70);self.assertAlmostEqual(team['averages']['PTS'],1600/70)
        self.assertAlmostEqual(team['values']['FG%'],430/700);self.assertEqual(team['projectionRunId'],'frozen-test-run')
        self.assertEqual(sum(team['categoryPoints'].values()),team['points'])
        before=self.client.get('/api/drafts/real-test?at=0').json
        self.assertEqual(next(t for t in before['teams'] if t['name']=='Max')['remaining'],190)
    def test_undo_keeps_evidence_and_recalculates_roster_and_manager_outcomes(self):
        self.create();batch=[event('n','nominate'),event('b','bid',team='A',amount=30),event('s','sale',team='B',amount=31)]
        self.assertEqual(self.append(batch).status_code,200)
        correction=[dict(id='undo',type='undo',targetId='s'),event('corrected','sale',team='A',amount=30)]
        self.assertEqual(self.append(correction,3).status_code,200)
        a=self.client.get('/api/drafts/real-test').json;self.assertEqual(len(a['session']['events']),5)
        self.assertEqual(len(a['snapshots']),4)
        history=self.client.get('/api/draft-managers').json['teams']
        self.assertEqual(history['A']['bidAuctionsWon'],1);self.assertEqual(history['A']['bidAuctionsLost'],0);self.assertEqual(history['B']['purchases'],0)
    def test_history_tracks_losing_bids_repeated_raises_and_premium_pursuit_across_seasons(self):
        for year in ['2026-27','2027-28']:
            id='d'+year;self.create(baseline(id,year));events=[]
            for i in range(3,9):
                p='p'+str(i);events.extend([event(p+'n','nominate',p),event(p+'a','bid',p,'A',1),event(p+'b','bid',p,'B',2),event(p+'a2','bid',p,'A',3),event(p+'s','sale',p,'B',4)])
            self.assertEqual(self.append(events,id=id).status_code,200)
        data=self.client.get('/api/draft-managers').json;p=data['teams']['A']
        self.assertEqual(p['seasons'],['2026-27','2027-28']);self.assertEqual(p['observedBids'],24)
        self.assertEqual((p['auctionsEntered'],p['bidAuctionsLost'],p['repeatRaiseAuctions'],p['premiumEntered'],p['premiumOpportunities']),(12,12,12,12,12))
        self.assertGreater(p['premiumInterestFactor'],1);self.assertLessEqual(p['premiumInterestFactor'],1.2)
        self.assertIn('Frequent repeat bidder',p['labels']);self.assertIn('Often bids without winning',p['labels'])
        excluded=self.client.get('/api/draft-managers?exclude=d2027-28').json['teams']['A'];self.assertEqual(excluded['observedBids'],12)
    def test_recovered_history_does_not_invent_live_raise_frequency_or_eligible_opportunities(self):
        self.create();events=[event('s','sale',team='B',amount=60,recovered=True),event('b1','bid',team='A',amount=30,history=True),event('b2','bid',team='A',amount=50,history=True)]
        self.assertEqual(self.append(events).status_code,200);p=self.client.get('/api/draft-managers').json['teams']['A']
        self.assertEqual(p['bidAuctionsLost'],1);self.assertEqual(p['repeatRaiseAuctions'],0);self.assertEqual(p['premiumOpportunities'],0);self.assertEqual(p['premiumReached'],1)
    def test_completion_requires_current_revision_and_explicit_reopening(self):
        self.create();self.append([event('n','nominate')])
        self.assertEqual(self.post('/api/drafts/real-test/status',dict(status='complete',revision=0)).status_code,409)
        self.assertEqual(self.post('/api/drafts/real-test/status',dict(status='complete',revision=1)).status_code,200)
        self.assertEqual(self.append([event('n','nominate')]).status_code,200)
        self.assertEqual(self.append([event('s','sale',team='A',amount=1)],1).status_code,409)
        self.assertEqual(self.post('/api/drafts/real-test/status',dict(status='recording',revision=1)).status_code,200)
        self.assertEqual(self.append([event('s','sale',team='A',amount=1)],1).status_code,200)
    def test_negative_nan_and_malformed_inputs_never_persist(self):
        for change in [lambda s:s['players'][0].update(games=-1),lambda s:s['teams'][0].update(budget=True),lambda s:s['settings'].update(rosterSize=0)]:
            s=baseline();change(s);self.assertEqual(self.post('/api/drafts',{'session':s}).status_code,400)
        self.assertEqual(self.client.get('/api/drafts').json['drafts'],[])
    def test_tiny_projection_differences_use_the_same_tie_tolerance_as_the_live_board(self):
        s=baseline();s['players'][0]['pts_pg']+=1e-8
        teams=projections(s,replay(s,[]))
        self.assertTrue(all(t['categoryPoints']['PTS']==2 and t['points']==16 for t in teams))
        s['players'][0]['fga_pg']=s['players'][0]['fgm_pg']=0
        missing=projections(s,replay(s,[]))
        self.assertTrue(all(t['points'] is None and t['rank'] is None and t['categoryPoints']['PTS'] is None for t in missing))

    def test_shared_category_ties_use_fractional_points_and_shared_overall_rank(self):
        s=baseline();teams=projections(s,replay(s,[]));self.assertTrue(all(t['points']==16 and t['rank']==1 for t in teams))

if __name__=='__main__':unittest.main()
