/* Pure import policy: automatic room setup is confined to the Yahoo mock store. */
(function(root,factory){const api=factory();if(typeof module==='object'&&module.exports)module.exports=api;else root.SurvivorYahooRoom=api;})(globalThis,function(){
 'use strict';
 // Existing 2026 keeper rosters are attached once using the pre-draft Yahoo list.
 // All subsequent bids and purchases use the captured Yahoo team name directly.
 const liveNames2026={Alvin:'Fantasy Sports Czar',Joe:'Shut Down',Ian:'The KAT in the hat',Mark:'Ozzy',Benji:'Morant and Gang',Ron:"Let's Go Get It!",Brent:'Swag Management',Jason:'JYK',Max:'Cookin in Jokicin',Isaac:'Brunson burners',Arthur:'3-four-1',Darren:'Pretty Savage',Kerry:'Burner Account',Peter:'Cyclops',Graham:'Graham'};
 function identify(E,current){
  if(current.mode!=='live'||current.purpose!=='real'||current.season!=='2026-27'||current.teams.length!==15||current.teams.some(t=>!Object.hasOwn(liveNames2026,t.name))||current.events.some(e=>e.type==='yahoo-teams'))return false;
  E.append(current,{id:current.id+':yahoo-names-v1',type:'yahoo-teams',teamNames:liveNames2026,ownTeam:liveNames2026.Max,source:'yahoo-team-identities',at:new Date().toISOString()});
  return true;
 }
 function prepare(E,current,metadata,isMock){
  if(!isMock||current.purpose!=='mock')throw Error('Automatic room import is only available in the Yahoo mock workspace.');
  const r=metadata;
  if(!r?.complete||typeof r.key!=='string'||r.key.length>1000||!Array.isArray(r.teams)||r.teams.length<2||r.teams.length>30||!Number.isInteger(r.rosterSize)||r.rosterSize<1||r.rosterSize>30)throw Error('Waiting for the complete Yahoo room settings.');
  if(r.teams.some(t=>typeof t.name!=='string'||!t.name.trim()||t.name.length>100||!Number.isInteger(t.budget)||t.budget<1||t.budget>10000)||new Set(r.teams.map(t=>E.key(t.name))).size!==r.teams.length)throw Error('Yahoo team names or budgets could not be read reliably.');
  if(current.yahooRoomKey===r.key)return {session:current,changed:false};
  const session=E.create(current.players,{season:current.season,teams:r.teams.map(t=>({franchise:t.name,budget_per_team:t.budget})),rows:[]},{purpose:'mock',mode:'live',runId:current.baselineRunId});
  session.settings.rosterSize=r.rosterSize;session.yahooRoomKey=r.key;session.yahooRoomObservedAt=new Date().toISOString();
  E.replay(session);
  return {session,changed:true,ownTeam:r.teams.some(t=>t.name===r.ownTeam)?r.ownTeam:r.teams[0].name};
 }
 return {prepare,identify};
});
