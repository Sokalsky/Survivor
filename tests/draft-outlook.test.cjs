'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),E=require('../survivor/web/static/draft-engine.js');
function fixture(){
 const row=(id,blk=1)=>({player_id:id,player:id,positions:'PG,C',nba_team:'NBA',fair_value:10,expected_auction_price:10,games:50,minutes_pg:30,pts_pg:20,reb_pg:5,ast_pg:4,stl_pg:1,blk_pg:blk,fg3m_pg:2,fgm_pg:5,fga_pg:10,ftm_pg:4,fta_pg:5});
 const rows=[row('k0',.1),row('k1',1.2),row('k2',2),...Array.from({length:12},(_,i)=>row('p'+i,1))];
 const teams=['Max','A','B'],s=E.create(rows,{season:'2026-27',budget_per_team:200,teams:teams.map(franchise=>({franchise})),rows:teams.map((franchise,i)=>({franchise,player_id:'k'+i,keeper_cost:10}))},{id:'outlook'});
 s.settings.rosterSize=2;return s;
}
let seq=0;const sale=(playerId,team,amount=10)=>({id:'outlook-'+(++seq),type:'sale',playerId,team,amount});
const close=(a,b)=>assert.ok(Math.abs(a-b)<1e-8,a+' != '+b);
test('owned totals multiply each rate by that player’s projected games',()=>{
 const s=fixture();s.players[0].games=20;s.players[0].pts_pg=30;E.append(s,sale('p0','Max'));
 const o=E.outlook(E.replay(s),'Max');assert.equal(o.owned.values.PTS,1600);close(o.owned.averages.PTS,1600/70);assert.equal(o.owned.games,70);
});
test('every counting stat and both shooting percentages expose owned before/after averages',()=>{
 const s=fixture();s.players[0].games=20;s.players[0].pts_pg=30;s.players[0].fgm_pg=1;s.players[0].fga_pg=2;
 s.players[3].fgm_pg=9;s.players[3].fga_pg=10;s.players[3].pts_pg=10;
 const f=E.fit(E.replay(s),s.players[3],'Max');assert.deepEqual(Object.keys(f.impact),E.CATS);
 assert.equal(f.impact.PTS.avgBefore,30);close(f.impact.PTS.avgAfter,1100/70);close(f.impact['FG%'].avgAfter,470/540);
});
test('zero attempts give no percentage instead of a fabricated zero or NaN',()=>{
 const s=fixture();s.players[0].fga_pg=s.players[0].fgm_pg=s.players[0].fta_pg=s.players[0].ftm_pg=0;
 const o=E.outlook(E.replay(s),'Max');assert.equal(o.owned.values['FG%'],null);assert.equal(o.owned.values['FT%'],null);
 const f=E.fit(E.replay(s),s.players[3],'Max');assert.equal(f.fgBefore,null);assert.equal(f.fgAfter,.5);
});
test('season games limit scales all counting totals and preserves weighted shooting',()=>{
 const s=fixture();s.settings.gamesCap=60;E.append(s,sale('p0','Max'));
 const o=E.outlook(E.replay(s),'Max');assert.equal(o.owned.games,60);assert.equal(o.owned.rawGames,100);
 assert.equal(o.owned.values.PTS,1200);assert.equal(o.owned.values.AST,240);assert.equal(o.owned.values['FT%'],.8);
});
test('legacy sessions default to the league’s 1000-game limit and invalid caps are rejected',()=>{
 const s=fixture();delete s.settings.gamesCap;assert.equal(E.outlook(E.replay(s),'Max').gamesCap,1000);
 for(const gamesCap of [0,-1,1.5,3001,NaN])assert.throws(()=>E.replay({...s,settings:{...s.settings,gamesCap}}),/games limit/);
});
test('a blocks specialist helps a weak blocks team more than a comfortable leader',()=>{
 const s=fixture();s.players[3].blk_pg=1.6;s.players[3].fair_value=5;for(const k of ['pts_pg','reb_pg','ast_pg','stl_pg','fg3m_pg','fgm_pg','fga_pg','ftm_pg','fta_pg'])s.players[3][k]=0;const st=E.replay(s),weak=E.fit(st,s.players[3],'Max'),strong=E.fit(st,s.players[3],'B');
 assert.equal(weak.impact.BLK.priority,true);assert.equal(strong.impact.BLK.priority,false);
 assert.ok(weak.impact.BLK.change>0);assert.ok(weak.fitFactor>strong.fitFactor);
 assert.ok(weak.cap>strong.cap);assert.ok(weak.explanation.includes('BLK'));assert.equal(weak.fitLabel,'Strong fit');
});
test('fit can identify a counting-stat need with identical shooting across all players',()=>{
 const s=fixture();s.players[3].blk_pg=3;const f=E.fit(E.replay(s),s.players[3],'Max');
 assert.ok(f.helps.includes('BLK'));assert.equal(f.impact['FG%'].change,0);assert.equal(f.impact['FT%'].change,0);assert.match(f.explanation,/need in BLK/);
});
test('rankings use the players actually owned even when roster sizes differ',()=>{
 const s=fixture();E.append(s,sale('p0','Max'));const st=E.replay(s),o=E.outlook(st,'Max'),other=E.outlook(st,'A');
 assert.equal(o.owned.values.PTS,2000);assert.equal(other.owned.values.PTS,1000);
 assert.equal(o.categories.find(c=>c.category==='PTS').rank,1);assert.equal(other.categories.find(c=>c.category==='PTS').rank,2);
 assert.equal(o.rosterCount,2);assert.equal(other.rosterCount,1);
 assert.equal(o.categories.reduce((n,c)=>n+c.rotoPoints,0),o.overall.current.points);
});
test('tied ranks and next distinct team gaps do not count a tied team as ahead',()=>{
 const s=fixture();const o=E.outlook(E.replay(s),'Max');const pts=o.categories.find(c=>c.category==='PTS'),blk=o.categories.find(c=>c.category==='BLK');
 assert.equal(pts.rank,1);assert.equal(pts.tied,true);assert.equal(pts.status,'Competitive');assert.equal(pts.gap,0);assert.equal(blk.rank,3);close(blk.gap,55);
});
test('with-player preview adds only that player and matches the next recorded sale',()=>{
 const s=fixture(),st=E.replay(s),p=s.players[3],f=E.fit(st,p,'Max');E.append(s,sale(p.player_id,'Max',10));
 const o=E.outlook(E.replay(s),'Max');for(const k of E.CATS)close(f.impact[k].after,o.owned.values[k]);
 assert.equal(o.owned.games,100);assert.equal(f.impact.PTS.change,1000);
});
test('remaining cash and undrafted player projections never fill empty roster slots',()=>{
 const s=fixture();s.settings.rosterSize=4;const before=E.outlook(E.replay(s),'Max');
 const changed=JSON.parse(JSON.stringify(s));changed.keepers[0].amount=188;
 for(const p of changed.players.slice(3)){p.games=82;p.pts_pg=10000;p.blk_pg=200;p.expected_auction_price=1000;}
 const after=E.outlook(E.replay(changed),'Max');assert.deepEqual(after,before);
 assert.equal(after.owned.games,50);assert.equal(after.owned.values.PTS,1000);
});
test('unfillable open position slots do not add or remove owned production',()=>{
 const s=fixture();s.settings.rosterSlots=['PG','C'];s.players[0].positions='PG';for(const p of s.players.slice(3))p.positions='PG';
 const o=E.outlook(E.replay(s),'Max');assert.equal(o.owned.values.PTS,1000);assert.equal(o.rosterCount,1);
 assert.equal(E.fit(E.replay(s),s.players[3],'Max').cap,0);
});
test('sale and undo refresh outlook while observed bids alone do not change it',()=>{
 const s=fixture(),before=E.outlook(E.replay(s),'Max');E.append(s,{id:'n',type:'nominate',playerId:'p0'});E.append(s,{id:'b',type:'bid',playerId:'p0',team:'A',amount:30});
 assert.deepEqual(E.outlook(E.replay(s),'Max'),before);
 const event=sale('p0','Max',30);E.append(s,event);assert.notDeepEqual(E.outlook(E.replay(s),'Max'),before);
 E.append(s,{id:'undo',type:'undo',targetId:event.id});assert.deepEqual(E.outlook(E.replay(s),'Max'),before);
});
test('advice and outlook calculations never modify baseline projections or the event journal',()=>{
 const s=fixture(),before=JSON.stringify(s);E.board(E.replay(s),'Max');assert.equal(JSON.stringify(s),before);
});
test('last slot preview and fit cannot bypass reserve or roster constraints',()=>{
 const s=fixture();s.settings.reservePerSlot=195;s.settings.rosterSize=3;const b=E.board(E.replay(s),'Max');assert.ok(b.rows.every(p=>p.fit.cap===0));
 s.settings.rosterSize=1;const full=E.board(E.replay(s),'Max');assert.equal(full.outlook.canAdd,false);assert.ok(full.rows.every(p=>p.fit.fitLabel==='No roster slot'&&p.fit.cap===0));
});

test('overall standings sum exact roto category points, with the best receiving team count',()=>{
 const profiles=[1,2,3].map((v,i)=>({name:['Low','Middle','High'][i],values:Object.fromEntries(E.CATS.map(k=>[k,v]))}));
 const result=E.rotoStandings(profiles);assert.equal(result.maxPoints,24);
 assert.deepEqual(result.rows.map(t=>[t.name,t.points,t.rank]),[['High',24,1],['Middle',16,2],['Low',8,3]]);
 assert.equal(result.rows[2].gap,8);assert.equal(result.rows.reduce((n,t)=>n+t.points,0),48);
});
test('category ties share occupied-place points and overall ties share a rank',()=>{
 const values=v=>Object.fromEntries(E.CATS.map(k=>[k,v]));
 const result=E.rotoStandings([{name:'A',values:values(2)},{name:'B',values:values(1)},{name:'C',values:values(1)}]);
 assert.deepEqual(result.rows.map(t=>[t.points,t.rank,t.tied]),[[24,1,false],[12,2,true],[12,2,true]]);
 assert.equal(result.rows[1].categoryPoints.BLK,1.5);
 const tied=E.rotoStandings([{name:'A',values:values(1)},{name:'B',values:values(1)},{name:'C',values:values(1)}]);
 assert.ok(tied.rows.every(t=>t.points===16&&t.rank===1&&t.tied));
});
test('overall ranks use total points, not a single strongest category',()=>{
 const a=Object.fromEntries(E.CATS.map(k=>[k,1])),b=Object.fromEntries(E.CATS.map(k=>[k,2])),c=Object.fromEntries(E.CATS.map(k=>[k,3]));
 a.PTS=100;const result=E.rotoStandings([{name:'A',values:a},{name:'B',values:b},{name:'C',values:c}]);
 assert.deepEqual(result.rows.map(t=>[t.name,t.points]),[['C',23],['B',15],['A',10]]);
});
test('with-player overall rank recalculates rival points when a category changes hands',()=>{
 const s=fixture();s.players[3].blk_pg=5;for(const k of ['pts_pg','reb_pg','ast_pg','stl_pg','fg3m_pg','fgm_pg','fga_pg','ftm_pg','fta_pg'])s.players[3][k]=0;
 const o=E.outlook(E.replay(s),'Max',s.players[3]);
 assert.equal(o.overall.current.points,15);assert.equal(o.overall.current.rank,3);
 assert.equal(o.overall.after.points,17);assert.equal(o.overall.after.rank,1);assert.equal(o.overall.after.tied,false);
});
test('missing categories do not invent an overall standing',()=>{
 const s=fixture();s.settings.rosterSize=1;s.players[0].fgm_pg=s.players[0].fga_pg=0;
 const o=E.outlook(E.replay(s),'Max');assert.equal(o.overall.available,false);assert.equal(o.overall.current,null);assert.deepEqual(o.overall.teams,[]);
});


test('a totals leader can have a weak average when it owns more player-games',()=>{
 const s=fixture();s.players[0].pts_pg=10;s.players[3].pts_pg=10;s.players[1].pts_pg=15;s.players[2].pts_pg=20;s.players[2].games=30;
 E.append(s,sale('p0','Max'));const o=E.outlook(E.replay(s),'Max'),c=o.categories.find(c=>c.category==='PTS');
 assert.equal(c.total,1000);assert.equal(c.rank,1);assert.equal(c.rotoPoints,3);
 assert.equal(c.average.value,10);assert.equal(c.average.median,15);assert.equal(c.average.status,'Needs attention');close(c.average.relativeDifference,-1/3);
 assert.deepEqual(o.volume,{games:100,medianGames:50,players:2,medianPlayers:1});
 assert.equal(o.overall.current.points,o.categories.reduce((n,c)=>n+c.rotoPoints,0));
});

test('a low-volume roster can have a strong average despite last place in totals',()=>{
 const s=fixture();s.players[0].pts_pg=30;s.players[0].games=10;s.players[1].pts_pg=15;
 const c=E.outlook(E.replay(s),'Max').categories.find(c=>c.category==='PTS');
 assert.equal(c.total,300);assert.equal(c.rank,3);assert.equal(c.priority,true);assert.equal(c.rotoPoints,1);
 assert.equal(c.average.value,30);assert.equal(c.average.median,20);assert.equal(c.average.status,'Strong');assert.equal(c.average.relativeDifference,.5);
});

test('average comparisons weight projected games and attempts, even above the games cap',()=>{
 const s=fixture();s.players[0].games=20;s.players[0].pts_pg=30;s.players[0].fgm_pg=1;s.players[0].fga_pg=2;
 s.players[3].pts_pg=10;s.players[3].fgm_pg=9;s.players[3].fga_pg=10;s.settings.gamesCap=60;E.append(s,sale('p0','Max'));
 const o=E.outlook(E.replay(s),'Max'),pts=o.categories.find(c=>c.category==='PTS'),fg=o.categories.find(c=>c.category==='FG%');
 close(pts.average.value,1100/70);close(pts.total,1100*60/70);close(fg.average.value,470/540);close(fg.average.difference,470/540-.5);
 assert.equal(o.volume.games,60);assert.equal(o.volume.medianGames,50);
});

test('tied averages are competitive and missing shooting rates are excluded from the median',()=>{
 const s=fixture();s.players[0].fgm_pg=s.players[0].fga_pg=0;
 const o=E.outlook(E.replay(s),'Max'),pts=o.categories.find(c=>c.category==='PTS'),fg=o.categories.find(c=>c.category==='FG%');
 assert.equal(pts.average.status,'Competitive');assert.equal(pts.average.difference,0);
 assert.equal(fg.average.value,null);assert.equal(fg.average.median,.5);assert.equal(fg.average.difference,null);assert.equal(fg.average.status,'No average yet');
});
