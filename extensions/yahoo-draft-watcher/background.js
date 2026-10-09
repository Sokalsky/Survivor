'use strict';
let chain=Promise.resolve();
const serial=fn=>{const result=chain.then(fn);chain=result.catch(()=>{});return result;};
const initial=()=>({appTab:null,appOrigin:null,nonce:null,sessionId:null,yahooTab:null,selectors:{},queue:[],seen:[],watching:false,apps:[],players:[],room:null,roomReady:false,automatic:false,userPaused:false,source:crypto.randomUUID(),status:{ready:false,message:'Open Survivor and a Yahoo salary-cap draft. Connection is automatic.'}});
const load=async()=> (await chrome.storage.local.get('draftWatcher')).draftWatcher || initial();
const save=async c=>chrome.storage.local.set({draftWatcher:c});
const yahoo=url=>{try{const h=new URL(url).hostname;return h==='sports.yahoo.com'||h==='fantasysports.yahoo.com'||h.endsWith('.fantasysports.yahoo.com');}catch{return false;}};
async function appMessage(c,message){if(!c.appTab)return;try{await chrome.tabs.sendMessage(c.appTab,{...message,nonce:c.nonce,sessionId:c.sessionId});}catch{c.status.message='Survivor tab is unavailable. Updates are queued locally.';}}
async function flush(c){if(c.sessionId&&c.queue.length&&(!c.automatic||c.roomReady))await appMessage(c,{type:'events',events:c.queue.slice(0,100)});}
async function injectApp(c){await chrome.scripting.executeScript({target:{tabId:c.appTab},files:['bridge.js']});await chrome.tabs.sendMessage(c.appTab,{type:'init',nonce:c.nonce});}
async function injectYahoo(c){await chrome.scripting.executeScript({target:{tabId:c.yahooTab},files:['yahoo-reader.js','capture.js']});await chrome.tabs.sendMessage(c.yahooTab,{type:'configure',selectors:c.selectors,watching:c.watching,captureId:c.source,players:c.players||[],teamAliases:c.appPurpose==='real'?c.teamAliases||[]:[]});}
const primaryApp='https://survivor-production-bdd5.up.railway.app';
function cleanRoom(raw){
 if(!raw||typeof raw.key!=='string'||raw.key.length>1000||!yahoo(raw.key)||!['mock','unknown'].includes(raw.purpose)||!Array.isArray(raw.teams)||raw.teams.length<2||raw.teams.length>30||!Number.isInteger(raw.rosterSize)||raw.rosterSize<1||raw.rosterSize>30)throw Error('Incomplete Yahoo room settings.');
 const teams=raw.teams.map(t=>({name:String(t.name||'').trim().slice(0,100),budget:t.budget,cash:t.cash,owned:t.owned}));
 if(new Set(teams.map(t=>t.name.toLowerCase())).size!==teams.length||teams.some(t=>!t.name||!Number.isInteger(t.cash)||t.cash<0||t.cash>10000||!Number.isInteger(t.owned)||t.owned<0||t.owned>raw.rosterSize))throw Error('Invalid Yahoo team list.');
 const complete=!!raw.complete&&teams.every(t=>Number.isInteger(t.budget)&&t.budget>=1&&t.budget<=10000);
 return {key:raw.key,purpose:raw.purpose,teams,rosterSize:raw.rosterSize,ownTeam:teams.some(t=>t.name===raw.ownTeam)?raw.ownTeam:null,complete};
}
async function connectAutomatic(c){
 if(!c.room||c.userPaused)return;
 const alive=[];for(const app of c.apps||[]){try{const tab=await chrome.tabs.get(app.id);if(tab&&new URL(tab.url).origin===app.origin)alive.push(app);}catch{/* Closed tab. */}}
 c.apps=alive;
 // Unknown rooms may enter an explicitly isolated mock workspace, never real history.
 const matches=alive.filter(a=>a.purpose==='mock'&&a.incognito===c.roomIncognito);
 const destination=matches.find(a=>a.id===c.appTab)||(matches.length===1?matches[0]:null);
 if(!destination){c.status={ready:false,message:matches.length>1?'More than one mock workspace is open. Choose the destination in Advanced setup.':'Yahoo detected. Open Survivor’s Yahoo mock workspace to connect.'};return;}
 if(c.queue.length&&c.appPurpose!=='mock')throw Error('Previous real draft updates are still queued. Reconnect that draft before switching.');
 if(c.appTab!==destination.id||!c.automatic){
  c.appTab=destination.id;c.appOrigin=destination.origin;c.appPurpose='mock';c.nonce=crypto.randomUUID();c.automatic=true;c.roomReady=false;c.sessionId=null;
  await save(c);await injectApp(c);
 }
 c.watching=true;if(!c.roomReady)c.status={ready:false,message:c.room.complete?'Importing Yahoo teams and roster settings.':'Yahoo connected. Reading teams and starting budgets.'};
 await save(c);await injectYahoo(c);
 if(c.sessionId&&c.room.complete&&!c.roomReady)await appMessage(c,{type:'room',room:c.room});
}
async function digest(value){const bytes=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(value));return Array.from(new Uint8Array(bytes),b=>b.toString(16).padStart(2,'0')).join('');}
chrome.runtime.onMessage.addListener((message,sender,sendResponse)=>{
  serial(async()=>{
    const c=await load();
    if(sender.tab){
      if(message.type==='app-online'){
        const url=new URL(sender.url||sender.tab.url);
        if(url.origin!==primaryApp&&url.origin!==c.appOrigin)throw Error('Unrecognized Survivor site.');
        const marker=await chrome.scripting.executeScript({target:{tabId:sender.tab.id},func:()=>document.querySelector('meta[name="survivor-app"]')?.content});
        if(marker[0]?.result!=='draft-v1')throw Error('Not a Survivor draft page.');
        const purpose=url.searchParams.get('yahooMock')==='1'?'mock':'real';
        c.apps=(c.apps||[]).filter(a=>a.id!==sender.tab.id);c.apps.push({id:sender.tab.id,origin:url.origin,purpose,incognito:!!sender.tab.incognito});
        await connectAutomatic(c);await save(c);return {ok:true};
      }
      if(message.type==='room-detected'&&yahoo(sender.url||sender.tab.url)){
        const room=cleanRoom(message.room),tab=sender.tab;
        if(tab.openerTabId){try{const opener=await chrome.tabs.get(tab.openerTabId);if(yahoo(opener.url)&&/mock/i.test((opener.title||'')+' '+opener.url))room.purpose='mock';}catch{/* Opener may have closed. */}}
        if(c.room&&c.room.key!==room.key){
          // A different open room cannot steal a running capture.
          if(c.yahooTab!==tab.id){try{if(await chrome.tabs.get(c.yahooTab))throw Error('Another Yahoo room is connected. Close it before connecting this room.');}catch(err){if(err.message.startsWith('Another'))throw err;}}
          if(c.queue.length){if(!c.automatic||c.appPurpose!=='mock')throw Error('Previous draft updates are still queued. Reconnect that draft first.');await chrome.storage.local.set({['draftWatcher.mockBackup.'+c.source]:c});}
          c.queue=[];c.seen=[];c.source=crypto.randomUUID();c.roomReady=false;c.sessionId=null;c.selectors={};
        }
        if(!c.room||c.room.key!==room.key||!c.room.complete)c.room=room;
        c.yahooTab=tab.id;c.roomIncognito=!!tab.incognito;
        // Preserve explicit real-draft pairing; a detected mock can never feed it.
        if(!c.automatic&&c.appTab&&c.appPurpose==='real'&&room.purpose!=='mock'){await save(c);return {ok:true};}
        await connectAutomatic(c);await save(c);return {ok:true};
      }
      if(sender.tab.id===c.appTab&&sender.origin===c.appOrigin&&message.nonce===c.nonce){
        if(message.type==='ready'){
          if(c.sessionId&&c.sessionId!==message.sessionId&&c.queue.length){c.status={ready:false,message:'Queued updates belong to a different session. Restore that session or explicitly clear the queue.'};await save(c);throw Error(c.status.message);}
          if(c.sessionId&&c.sessionId!==message.sessionId){c.source=crypto.randomUUID();c.seen=[];if(c.automatic)c.roomReady=false;if(c.yahooTab)await injectYahoo(c);}
          c.sessionId=message.sessionId;c.appPurpose=message.purpose||c.appPurpose;
          if(Array.isArray(message.players)&&message.players.length<=2000)c.players=message.players.map(p=>({player:String(p.player||'').slice(0,160)}));
          if(Array.isArray(message.teamAliases)&&message.teamAliases.length<=200)c.teamAliases=message.teamAliases.map(a=>({name:String(a.name||'').slice(0,100),team:String(a.team||'').slice(0,100)})).filter(a=>a.name&&a.team);
          if(c.automatic&&c.room?.complete&&!c.roomReady)await appMessage(c,{type:'room',room:c.room});
          if(c.yahooTab)await injectYahoo(c);
          await flush(c);await appMessage(c,{type:'heartbeat',status:c.status.at&&Date.now()-c.status.at<5000?c.status:{ready:false,message:'Waiting for a fresh Yahoo observation.'}});
        }
        if(message.type==='room-ready'&&c.automatic&&message.roomKey===c.room?.key){
          if(message.error){c.watching=false;c.status={ready:false,message:String(message.error).slice(0,300)};await injectYahoo(c);}
          else{c.sessionId=message.sessionId;c.roomReady=true;await flush(c);}
        }
        if(message.type==='ack'&&message.sessionId===c.sessionId&&Array.isArray(message.ids)){const ids=new Set(message.ids);c.queue=c.queue.filter(e=>!ids.has(e.id));await flush(c);}
      }else if(sender.tab.id===c.yahooTab&&yahoo(sender.url)){
        if(message.type==='picked') {c.selectors[message.field]=String(message.selector).slice(0,1500);await injectYahoo(c);}
        if(message.type==='observation'&&c.watching){
          if(c.room?.purpose==='mock'&&c.appPurpose!=='mock')throw Error('Mock updates cannot enter a real draft workspace.');
          const input=message.events||[];if(!Array.isArray(input)||input.length>500)throw Error('Invalid capture batch.');
          for(const raw of input){
            if(!['nominate','bid','sale','withdraw'].includes(raw.type)||typeof raw.player!=='string'||!raw.player.trim()||raw.player.length>160)continue;
            if(['bid','sale'].includes(raw.type)&&(!Number.isInteger(raw.amount)||raw.amount<1||raw.amount>10000||typeof raw.team!=='string'||!raw.team.trim()||raw.team.length>160))continue;
            const signature=raw.type==='nominate'?raw.id:JSON.stringify([raw.type,raw.player,raw.team,raw.amount]);
            const id=await digest(c.source+signature);
            if(c.seen.includes(id))continue;
            if(c.queue.length>=2000){c.watching=false;c.status={ready:false,message:'Queue is full. Reconnect Survivor before resuming; some later activity may need reconciliation.'};await injectYahoo(c);break;}
            c.queue.push({id,type:raw.type,player:raw.player,team:raw.team,amount:raw.amount,history:!!raw.history,recovered:!!raw.recovered,at:new Date().toISOString()});c.seen.push(id);
          }
          if(c.seen.length>25000){c.watching=false;c.status={ready:false,message:'Session event limit reached. Export and review this draft.'};}
          await flush(c);
        }
        if(message.type==='heartbeat') {c.status={ready:!!message.ready&&c.watching&&(!c.automatic||c.roomReady),connected:!!message.connected&&c.watching&&(!c.automatic||c.roomReady),message:String(message.message||'').slice(0,300),timer:String(message.timer||'').slice(0,40),partial:true,at:Date.now()};await appMessage(c,{type:'heartbeat',status:c.status});await flush(c);}
      }else throw Error('This tab is not paired with the watcher.');
    }else {
      if(message.type==='diagnostics'){
        let capture=null,error=null;
        if(c.yahooTab){try{capture=await chrome.tabs.sendMessage(c.yahooTab,{type:'diagnostics'});}catch(e){error=e.message;}}
        return {version:chrome.runtime.getManifest().version,recordedAt:new Date().toISOString(),automatic:c.automatic,userPaused:!!c.userPaused,watching:c.watching,roomReady:!!c.roomReady,queued:c.queue.length,status:c.status,capture,error};
      }
      if(message.type==='get')return {...c,nonce:undefined,seen:undefined,players:undefined,queue:c.queue.length};
      if(message.type==='pair'){
        const tab=await chrome.tabs.get(message.tabId),url=new URL(tab.url);
        if(!['https:','http:'].includes(url.protocol)||url.protocol==='http:'&&!['localhost','127.0.0.1'].includes(url.hostname))throw Error('Use HTTPS, or a local Survivor preview.');
        const result=await chrome.scripting.executeScript({target:{tabId:tab.id},func:()=>document.querySelector('meta[name="survivor-app"]')?.content});
        if(result[0]?.result!=='draft-v1')throw Error('Open the Survivor Draft page before pairing.');
        c.watching=false;c.status={ready:false,message:'Survivor paired. Choose Watch Yahoo tab explicitly before capturing into this session.'};
        if(c.yahooTab){try{await injectYahoo(c);}catch{c.yahooTab=null;}}
        c.automatic=false;c.userPaused=false;c.appTab=tab.id;c.appOrigin=url.origin;c.appPurpose=url.searchParams.get('yahooMock')==='1'?'mock':'real';c.nonce=crypto.randomUUID();await save(c);await injectApp(c);return {ok:true};
      }
      if(message.type==='watch'){
        const tab=await chrome.tabs.get(message.tabId);if(!yahoo(tab.url))throw Error('Open the Yahoo fantasy draft room first.');
        if(!c.appTab)throw Error('Pair your Survivor tab first.');
        c.yahooTab=tab.id;c.watching=true;c.userPaused=false;await save(c);await injectYahoo(c);return {ok:true};
      }
      if(message.type==='pick') {if(!c.yahooTab)throw Error('Choose Watch Yahoo tab first.');await chrome.tabs.update(c.yahooTab,{active:true});await chrome.tabs.sendMessage(c.yahooTab,{type:'pick',field:message.field});}
      if(message.type==='pause'){c.userPaused=!c.userPaused;c.watching=!c.userPaused&&!!c.yahooTab;if(c.yahooTab)await injectYahoo(c);if(!c.userPaused&&c.automatic)await connectAutomatic(c);}
      if(message.type==='selectors'){if(!message.selectors||typeof message.selectors!=='object'||Array.isArray(message.selectors))throw Error('Selectors must be an object.');c.selectors=message.selectors;if(c.yahooTab)await injectYahoo(c);}
      if(message.type==='clear'){c.watching=false;c.userPaused=true;c.queue=[];c.seen=[];c.source=crypto.randomUUID();c.sessionId=null;c.status={ready:false,message:'Queue cleared. Pair Survivor and review any missed activity.'};if(c.appTab)await injectApp(c);if(c.yahooTab)await injectYahoo(c);}
    }
    await save(c);return {ok:true};
  }).then(sendResponse,error=>sendResponse({error:error.message}));
  return true;
});
chrome.tabs.onUpdated.addListener((id,change)=>{if(change.status!=='complete')return;serial(async()=>{const c=await load();try{if(id===c.appTab)await injectApp(c);if(id===c.yahooTab)await injectYahoo(c);}catch{/* Popup explains missing access; never request new permissions automatically. */}});});
chrome.tabs.onRemoved.addListener(id=>serial(async()=>{const c=await load();c.apps=(c.apps||[]).filter(a=>a.id!==id);if(id===c.yahooTab){c.yahooTab=null;c.watching=false;c.status={ready:false,message:'Yahoo room closed. Waiting for your next draft.'};await appMessage(c,{type:'heartbeat',status:c.status});}if(id===c.appTab)c.appTab=null;await save(c);}));
chrome.alarms.create('draft-retry',{periodInMinutes:.5});
chrome.alarms.onAlarm.addListener(()=>serial(async()=>{const c=await load();await flush(c);}));
