'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),{Recorder}=require('../survivor/web/static/draft-recorder.js');
const memory=()=>{const map=new Map();return {getItem:k=>map.get(k)||null,setItem:(k,v)=>map.set(k,String(v)),removeItem:k=>map.delete(k)};};
function fixture(options={}){
 const session={id:'real',mode:'live',purpose:'real',events:[],settings:{rosterSize:15}},storage=memory(),keyStorage=memory(),calls=[];let saved=[],failure=null,status='recording';
 const fetcher=async(url,request)=>{const body=JSON.parse(request.body);calls.push({url,body});if(failure){const f=failure;failure=null;if(f==='offline')throw Error('offline');return {ok:false,status:f,json:async()=>({error:'test rejection'})};}
  if(url==='/api/drafts')return {ok:true,json:async()=>({revision:saved.length,status})};
  if(url.endsWith('/status')){status=body.status;return {ok:true,json:async()=>({revision:saved.length,status})};}
  for(let i=0;i<body.events.length;i++){const n=body.offset+i;if(n<saved.length&&JSON.stringify(saved[n])!==JSON.stringify(body.events[i]))return {ok:false,status:409,json:async()=>({error:'conflict'})};if(n>=saved.length)saved.push(body.events[i]);}
  return {ok:true,json:async()=>({revision:saved.length,status})};
 };
 const recorder=new Recorder({getSession:()=>session,storage,keyStorage,fetcher,...options});
 return {session,storage,keyStorage,calls,recorder,get saved(){return saved},fail:value=>failure=value};
}
const bid=i=>({id:'e'+i,type:'bid',playerId:'p',team:'A',amount:i+1});
test('real recording batches events, preserves losing bids and never uploads projection inputs again',async()=>{
 const f=fixture();f.session.events=Array.from({length:205},(_,i)=>bid(i));await f.recorder.start('test-key');
 assert.equal(f.saved.length,205);assert.deepEqual(f.calls.filter(c=>c.url.endsWith('/events')).map(c=>c.body.events.length),[100,100,5]);assert.deepEqual(f.calls[0].body.session.events,[]);assert.equal(f.recorder.revision,205);assert.match(f.recorder.message,/Saved 205/);
});
test('mock and practice recording are rejected before any network write',async()=>{
 for(const changes of [{purpose:'mock'},{mode:'practice'},{events:[{source:'practice'}]}]){const f=fixture();Object.assign(f.session,changes);await assert.rejects(()=>f.recorder.start('key'),/cannot be recorded/);assert.equal(f.calls.length,0);}
 const f=fixture({isTest:true});await assert.rejects(()=>f.recorder.start('key'),/cannot be recorded/);assert.equal(f.calls.length,0);
});
test('a network failure retains local events and resumes without duplicates',async()=>{
 const f=fixture();f.session.events=[bid(0),bid(1)];f.fail('offline');await assert.rejects(()=>f.recorder.start('key'),/offline/);assert.equal(f.session.events.length,2);assert.equal(f.recorder.blocked,false);clearTimeout(f.recorder.timer);f.recorder.timer=null;
 await f.recorder.flush();assert.equal(f.saved.length,2);await f.recorder.flush();assert.equal(f.saved.length,2);
});
test('reload verifies the saved prefix before adding new events',async()=>{
 const f=fixture();f.session.events=Array.from({length:150},(_,i)=>bid(i));await f.recorder.start('key');f.session.events.push(bid(150));f.recorder.verifiedId=null;f.recorder.revision=0;await f.recorder.flush();assert.equal(f.saved.length,151);assert.equal(f.recorder.revision,151);
});
test('conflicting capture stops retries instead of overwriting saved history',async()=>{
 const f=fixture();f.session.events=[bid(0)];await f.recorder.start('key');f.session.events[0].amount=99;f.recorder.verifiedId=null;
 await assert.rejects(()=>f.recorder.flush(),/conflict/);assert.equal(f.recorder.blocked,true);assert.equal(f.saved[0].amount,1);assert.equal(f.recorder.timer,null);
});
test('recording key is kept out of local draft storage and uploaded payloads',async()=>{
 const f=fixture();await f.recorder.start('private-test-key');assert.equal(f.keyStorage.getItem('survivor.recording.key'),'private-test-key');assert.equal(f.storage.getItem('survivor.recording.key'),null);assert.ok(!JSON.stringify(f.calls).includes('private-test-key'));
});
test('a completed draft can be explicitly reopened for pending corrections',async()=>{
 const f=fixture();f.session.events=[bid(0)];await f.recorder.start('key');await f.recorder.setStatus('complete');assert.equal(f.recorder.serverStatus,'complete');f.session.events.push(bid(1));await f.recorder.setStatus('recording');assert.equal(f.saved.length,2);assert.equal(f.recorder.serverStatus,'recording');
});
test('unresolved captures block completion',async()=>{
 const f=fixture({getPending:()=>1});await f.recorder.start('key');await assert.rejects(()=>f.recorder.setStatus('complete'),/Resolve captured/);assert.equal(f.calls.filter(c=>c.url.endsWith('/status')).length,0);
});
