'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs'),path=require('node:path'),{webcrypto}=require('node:crypto');
const Reader=require('../extensions/yahoo-draft-watcher/yahoo-reader.js'),E=require('../survivor/web/static/draft-engine.js'),Importer=require('../survivor/web/static/yahoo-room.js');
const appOrigin='https://survivor-production-bdd5.up.railway.app';
const sample={key:'https://basketball.fantasysports.yahoo.com/draft?mockId=42',purpose:'mock',complete:true,rosterSize:13,ownTeam:'You',teams:[{name:'You',budget:200,cash:200,owned:0},{name:'Emre',budget:200,cash:200,owned:0}]};
const source=fs.readFileSync(path.join(__dirname,'../extensions/yahoo-draft-watcher/background.js'),'utf8');
function worker(){
 let listener,removed;const data={},messages=[];
 const tabs={1:{id:1,url:appOrigin+'/?yahooMock=1#draft',incognito:true},2:{id:2,url:sample.key,incognito:true},3:{id:3,url:appOrigin+'/#draft',incognito:true},4:{id:4,url:appOrigin+'/?yahooMock=1',incognito:false}};
 const chrome={storage:{local:{get:async()=>structuredClone(data),set:async v=>Object.assign(data,structuredClone(v))}},runtime:{onMessage:{addListener:f=>listener=f}},tabs:{get:async id=>{if(!tabs[id])throw Error('No tab');return tabs[id];},sendMessage:async(id,m)=>messages.push({id,...m}),update:async()=>{},onUpdated:{addListener:()=>{}},onRemoved:{addListener:f=>removed=f}},scripting:{executeScript:async opts=>[{result:opts.func?'draft-v1':null}]},alarms:{create:()=>{},onAlarm:{addListener:()=>{}}}};
 vm.runInNewContext(source,{chrome,crypto:webcrypto,TextEncoder,URL,structuredClone,Date,Promise,Set,Array,JSON,Error});
 const call=(m,id)=>new Promise(resolve=>listener(m,id?{tab:tabs[id],url:tabs[id].url,origin:new URL(tabs[id].url).origin}:{},resolve));
 return {call,data,messages,tabs,remove:async id=>{delete tabs[id];await removed(id);}};
}
async function connected(){const w=worker();await w.call({type:'app-online'},1);await w.call({type:'room-detected',room:sample},2);const c=w.data.draftWatcher;await w.call({type:'ready',nonce:c.nonce,sessionId:'placeholder',purpose:'mock',players:[{player:'Nikola Jokic'}]},1);return w;}
test('room labels yield exact names, dollars and roster count without selectors',()=>{
 assert.deepEqual(Reader.teamRow('Yiğit Uğur $200 0/13'),{name:'Yiğit Uğur',cash:200,owned:0,size:13});
 assert.equal(Reader.teamRow('Max Offer $188 Budget $200 0/13'),null);assert.equal(Reader.teamRow('You $15.38 0/13'),null);
});
test('initials and accents resolve only when the player identity is unique',()=>{
 const resolve=Reader.catalogue([{player:'Nikola Jokic'},{player:'Jalen Williams'},{player:'Jaylin Williams'}]);
 assert.equal(resolve('N. JOKIĆ C DEN'),'Nikola Jokic');assert.equal(resolve('J. WILLIAMS'),null);assert.equal(resolve('Jalen Williams'),'Jalen Williams');
});
test('automatic connection imports room before releasing any bids',async()=>{
 const w=await connected(),c=w.data.draftWatcher;
 assert.equal(c.automatic,true);assert.equal(c.appTab,1);assert.equal(c.watching,true);assert.ok(w.messages.some(m=>m.type==='room'));
 await w.call({type:'observation',events:[{type:'bid',player:'Nikola Jokic',team:'Emre',amount:8}]},2);
 assert.equal(w.messages.filter(m=>m.type==='events').length,0);
 await w.call({type:'room-ready',nonce:c.nonce,roomKey:sample.key,sessionId:'imported'},1);
 assert.equal(w.data.draftWatcher.sessionId,'imported');assert.equal(w.messages.filter(m=>m.type==='events').length,1);
});
test('a mock cannot automatically select a real draft or another incognito context',async()=>{
 const w=worker();await w.call({type:'app-online'},3);await w.call({type:'app-online'},4);await w.call({type:'room-detected',room:sample},2);
 assert.equal(w.data.draftWatcher.appTab,null);assert.equal(w.data.draftWatcher.watching,false);
});
test('an unknown Yahoo room can only auto-connect to a mock workspace',async()=>{
 const w=worker();await w.call({type:'app-online'},3);await w.call({type:'room-detected',room:{...sample,purpose:'unknown'}},2);assert.equal(w.data.draftWatcher.appTab,null);
});
test('different simultaneous rooms cannot steal the current capture',async()=>{
 const w=await connected();w.tabs[5]={...w.tabs[2],id:5,url:sample.key+'9'};
 const result=await w.call({type:'room-detected',room:{...sample,key:w.tabs[5].url}},5);
 assert.match(result.error,/Another Yahoo room/);assert.equal(w.data.draftWatcher.yahooTab,2);
});
test('new mock preserves undelivered old-room updates without assigning them to the new room',async()=>{
 const w=await connected();await w.call({type:'observation',events:[{type:'bid',player:'Nikola Jokic',team:'Emre',amount:8}]},2);const old=w.data.draftWatcher.source;
 await w.remove(2);w.tabs[5]={id:5,url:sample.key+'9',incognito:true};await w.call({type:'room-detected',room:{...sample,key:w.tabs[5].url}},5);
 assert.equal(w.data['draftWatcher.mockBackup.'+old].queue.length,1);assert.equal(w.data.draftWatcher.queue.length,0);assert.notEqual(w.data.draftWatcher.source,old);
});
test('user pause survives later automatic discovery',async()=>{const w=await connected();await w.call({type:'pause'});await w.call({type:'room-detected',room:sample},2);assert.equal(w.data.draftWatcher.watching,false);});
test('mock room importer replaces placeholders, preserves same-room events and rejects a real target',()=>{
 const p={player_id:'p',player:'Nikola Jokic',games:60};const current=E.create([p],{season:'2026-27',teams:[{franchise:'Max'}],budget_per_team:200,rows:[]},{purpose:'mock'});
 const imported=Importer.prepare(E,current,sample,true);assert.equal(imported.session.teams.length,2);assert.equal(imported.session.settings.rosterSize,13);assert.equal(imported.ownTeam,'You');assert.equal(imported.session.keepers.length,0);
 E.append(imported.session,{id:'nom',type:'nominate',playerId:'p'});
 assert.equal(Importer.prepare(E,imported.session,sample,true).session.events.length,1);
 assert.throws(()=>Importer.prepare(E,{...current,purpose:'real'},sample,false),/only available/);
 assert.equal(current.teams[0].name,'Max');assert.equal(current.settings.rosterSize,15);
});
