'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs'),{webcrypto}=require('node:crypto');
const source=fs.readFileSync(require('node:path').join(__dirname,'../extensions/yahoo-draft-watcher/background.js'),'utf8');
function worker(persisted={}){
 let listener;const messages=[],injections=[];const data=persisted;
 const tabs={1:{id:1,url:'https://survivor.example.test/'},2:{id:2,url:'https://basketball.fantasysports.yahoo.com/draft'},3:{id:3,url:'https://unrelated.example.test/'}};
 const chrome={storage:{local:{get:async()=>structuredClone(data),set:async v=>Object.assign(data,structuredClone(v))}},runtime:{onMessage:{addListener:f=>listener=f}},tabs:{get:async id=>tabs[id],sendMessage:async(id,m)=>messages.push({id,...m}),update:async()=>{},onUpdated:{addListener:()=>{}},onRemoved:{addListener:()=>{}}},scripting:{executeScript:async opts=>{injections.push(opts);return [{result:opts.func?'draft-v1':null}];}},alarms:{create:()=>{},onAlarm:{addListener:()=>{}}}};
 vm.runInNewContext(source,{chrome,crypto:webcrypto,TextEncoder,URL,structuredClone,Date,Promise,Set,Array,JSON,Error});
 const call=(m,sender={})=>new Promise(resolve=>listener(m,sender,resolve));
 return {call,data,messages,injections};
}
async function paired(){const w=worker();assert.equal((await w.call({type:'pair',tabId:1})).ok,true);const c=w.data.draftWatcher;assert.equal((await w.call({type:'ready',nonce:c.nonce,sessionId:'live-session'},{tab:{id:1},origin:c.appOrigin})).ok,true);assert.equal((await w.call({type:'watch',tabId:2})).ok,true);return w;}
const yahooSender={tab:{id:2},url:'https://basketball.fantasysports.yahoo.com/draft'};
test('watcher rejects a non-Yahoo source tab',async()=>{const w=await paired();assert.match((await w.call({type:'watch',tabId:3})).error,/Yahoo/);});
test('only paired Yahoo tab may submit observations',async()=>{const w=await paired();const r=await w.call({type:'observation',events:[]},{tab:{id:3},url:'https://unrelated.example.test/'});assert.match(r.error,/not paired/);assert.equal(w.data.draftWatcher.queue.length,0);});
test('duplicate observation batches retain one bid and one sale',async()=>{const w=await paired(),events=[{type:'bid',player:'Stephen Curry',team:'Max',amount:30},{type:'sale',player:'Stephen Curry',team:'Max',amount:30}];await w.call({type:'observation',events},yahooSender);await w.call({type:'observation',events},yahooSender);assert.equal(w.data.draftWatcher.queue.length,2);assert.equal(w.data.draftWatcher.seen.length,2);assert.ok(w.messages.some(m=>m.type==='events'));});
test('acknowledgement cannot come from another tab or origin',async()=>{const w=await paired();await w.call({type:'observation',events:[{type:'bid',player:'Curry',team:'Max',amount:30}]},yahooSender);const c=w.data.draftWatcher,ids=c.queue.map(e=>e.id);await w.call({type:'ack',nonce:c.nonce,sessionId:c.sessionId,ids},{tab:{id:1},origin:'https://evil.example.test'});assert.equal(w.data.draftWatcher.queue.length,1);await w.call({type:'ack',nonce:c.nonce,sessionId:c.sessionId,ids},{tab:{id:1},origin:c.appOrigin});assert.equal(w.data.draftWatcher.queue.length,0);});
test('durable unsent queue survives a service-worker restart',async()=>{const w=await paired();await w.call({type:'observation',events:[{type:'bid',player:'Curry',team:'Max',amount:30}]},yahooSender);const restarted=worker(w.data);assert.equal((await restarted.call({type:'get'})).queue,1);const c=w.data.draftWatcher;await restarted.call({type:'ready',nonce:c.nonce,sessionId:c.sessionId},{tab:{id:1},origin:c.appOrigin});assert.ok(restarted.messages.some(m=>m.type==='events'&&m.events.length===1));});
test('queued events are never silently reassigned to a different session',async()=>{const w=await paired();await w.call({type:'observation',events:[{type:'bid',player:'Curry',team:'Max',amount:30}]},yahooSender);const c=w.data.draftWatcher;const r=await w.call({type:'ready',nonce:c.nonce,sessionId:'new-session'},{tab:{id:1},origin:c.appOrigin});assert.match(r.error,/different session/);assert.equal(w.data.draftWatcher.sessionId,'live-session');assert.equal(w.data.draftWatcher.queue.length,1);assert.equal(w.data.draftWatcher.status.ready,false);});
test('transport strips unrelated capture fields and rejects malformed amounts',async()=>{const w=await paired();await w.call({type:'observation',events:[{type:'bid',player:'Curry',team:'Max',amount:30,cookie:'NEVER STORE',html:'<secret>'},{type:'sale',player:'Curry',team:'Max',amount:'30'},{type:'sale',player:'Curry',team:'Max',amount:-1}]},yahooSender);assert.equal(w.data.draftWatcher.queue.length,1);assert.equal(w.data.draftWatcher.queue[0].cookie,undefined);assert.equal(w.data.draftWatcher.queue[0].html,undefined);});
test('new empty session gets a fresh capture identity',async()=>{const w=await paired(),c=w.data.draftWatcher,oldSource=c.source;await w.call({type:'ready',nonce:c.nonce,sessionId:'fresh'},{tab:{id:1},origin:c.appOrigin});assert.notEqual(w.data.draftWatcher.source,oldSource);assert.equal(w.data.draftWatcher.sessionId,'fresh');});

test('repeated app handshakes cannot turn a stale watcher heartbeat fresh',async()=>{const w=await paired(),c=w.data.draftWatcher;w.data.draftWatcher.status={ready:true,at:Date.now()-20000};await w.call({type:'ready',nonce:c.nonce,sessionId:c.sessionId},{tab:{id:1},origin:c.appOrigin});const latest=w.messages.filter(m=>m.type==='heartbeat').at(-1);assert.equal(latest.status.ready,false);});

test('pairing another Survivor destination pauses capture until Yahoo is explicitly selected',async()=>{const w=await paired();assert.equal(w.data.draftWatcher.watching,true);await w.call({type:'pair',tabId:1});assert.equal(w.data.draftWatcher.watching,false);assert.equal(w.data.draftWatcher.status.ready,false);});

test('confirmed owner aliases reach real Yahoo capture but not mock rooms',async()=>{
 const w=await paired(),c=w.data.draftWatcher,teamAliases=[{name:'You',team:'Max'},{name:'Cookin n Jokic',team:'Max'}];
 await w.call({type:'ready',nonce:c.nonce,sessionId:c.sessionId,purpose:'real',teamAliases},{tab:{id:1},origin:c.appOrigin});
 assert.deepEqual(structuredClone(w.messages.filter(m=>m.type==='configure').at(-1).teamAliases),teamAliases);
 await w.call({type:'ready',nonce:c.nonce,sessionId:c.sessionId,purpose:'mock'},{tab:{id:1},origin:c.appOrigin});
 assert.equal(w.messages.filter(m=>m.type==='configure').at(-1).teamAliases.length,0);
});
