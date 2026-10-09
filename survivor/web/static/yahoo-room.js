/* Pure import policy: automatic room setup is confined to the Yahoo mock store. */
(function(root,factory){const api=factory();if(typeof module==='object'&&module.exports)module.exports=api;else root.SurvivorYahooRoom=api;})(globalThis,function(){
 'use strict';
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
 return {prepare};
});
