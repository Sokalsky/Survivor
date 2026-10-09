'use strict';
let chain=Promise.resolve();
const serial=fn=>{const result=chain.then(fn);chain=result.catch(()=>{});return result;};
const initial=()=>({appTab:null,appOrigin:null,nonce:null,sessionId:null,yahooTab:null,selectors:{},queue:[],seen:[],watching:false,source:crypto.randomUUID(),status:{ready:false,message:'Pair your Survivor page and select Yahoo fields.'}});
const load=async()=> (await chrome.storage.local.get('draftWatcher')).draftWatcher || initial();
const save=async c=>chrome.storage.local.set({draftWatcher:c});
const yahoo=url=>{try{const h=new URL(url).hostname;return h==='sports.yahoo.com'||h==='fantasysports.yahoo.com'||h.endsWith('.fantasysports.yahoo.com');}catch{return false;}};
async function appMessage(c,message){if(!c.appTab)return;try{await chrome.tabs.sendMessage(c.appTab,{...message,nonce:c.nonce,sessionId:c.sessionId});}catch{c.status.message='Survivor tab is unavailable. Updates are queued locally.';}}
async function flush(c){if(c.sessionId&&c.queue.length)await appMessage(c,{type:'events',events:c.queue.slice(0,100)});}
async function injectApp(c){await chrome.scripting.executeScript({target:{tabId:c.appTab},files:['bridge.js']});await chrome.tabs.sendMessage(c.appTab,{type:'init',nonce:c.nonce});}
async function injectYahoo(c){await chrome.scripting.executeScript({target:{tabId:c.yahooTab},files:['capture.js']});await chrome.tabs.sendMessage(c.yahooTab,{type:'configure',selectors:c.selectors,watching:c.watching,captureId:c.source});}
async function digest(value){const bytes=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(value));return Array.from(new Uint8Array(bytes),b=>b.toString(16).padStart(2,'0')).join('');}
chrome.runtime.onMessage.addListener((message,sender,sendResponse)=>{
  serial(async()=>{
    const c=await load();
    if(sender.tab){
      if(sender.tab.id===c.appTab&&sender.origin===c.appOrigin&&message.nonce===c.nonce){
        if(message.type==='ready'){
          if(c.sessionId&&c.sessionId!==message.sessionId&&c.queue.length){c.status={ready:false,message:'Queued updates belong to a different session. Restore that session or explicitly clear the queue.'};await save(c);throw Error(c.status.message);}
          if(c.sessionId&&c.sessionId!==message.sessionId){c.source=crypto.randomUUID();c.seen=[];if(c.yahooTab)await injectYahoo(c);}
          c.sessionId=message.sessionId;await flush(c);await appMessage(c,{type:'heartbeat',status:c.status.at&&Date.now()-c.status.at<5000?c.status:{ready:false,message:'Waiting for a fresh Yahoo observation.'}});
        }
        if(message.type==='ack'&&message.sessionId===c.sessionId&&Array.isArray(message.ids)){const ids=new Set(message.ids);c.queue=c.queue.filter(e=>!ids.has(e.id));await flush(c);}
      }else if(sender.tab.id===c.yahooTab&&yahoo(sender.url)){
        if(message.type==='picked') {c.selectors[message.field]=String(message.selector).slice(0,1500);await injectYahoo(c);}
        if(message.type==='observation'&&c.watching){
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
        if(message.type==='heartbeat') {c.status={ready:!!message.ready&&c.watching,message:String(message.message||'').slice(0,300),timer:String(message.timer||'').slice(0,40),partial:true,at:Date.now()};await appMessage(c,{type:'heartbeat',status:c.status});await flush(c);}
      }else throw Error('This tab is not paired with the watcher.');
    }else {
      if(message.type==='get')return {...c,nonce:undefined,seen:undefined,queue:c.queue.length};
      if(message.type==='pair'){
        const tab=await chrome.tabs.get(message.tabId),url=new URL(tab.url);
        if(!['https:','http:'].includes(url.protocol)||url.protocol==='http:'&&!['localhost','127.0.0.1'].includes(url.hostname))throw Error('Use HTTPS, or a local Survivor preview.');
        const result=await chrome.scripting.executeScript({target:{tabId:tab.id},func:()=>document.querySelector('meta[name="survivor-app"]')?.content});
        if(result[0]?.result!=='draft-v1')throw Error('Open the Survivor Draft page before pairing.');
        c.watching=false;c.status={ready:false,message:'Survivor paired. Choose Watch Yahoo tab explicitly before capturing into this session.'};
        if(c.yahooTab)await injectYahoo(c);
        c.appTab=tab.id;c.appOrigin=url.origin;c.nonce=crypto.randomUUID();await save(c);await injectApp(c);return {ok:true};
      }
      if(message.type==='watch'){
        const tab=await chrome.tabs.get(message.tabId);if(!yahoo(tab.url))throw Error('Open the Yahoo fantasy draft room first.');
        if(!c.appTab)throw Error('Pair your Survivor tab first.');
        c.yahooTab=tab.id;c.watching=true;await save(c);await injectYahoo(c);return {ok:true};
      }
      if(message.type==='pick') {if(!c.yahooTab)throw Error('Choose Watch Yahoo tab first.');await chrome.tabs.update(c.yahooTab,{active:true});await chrome.tabs.sendMessage(c.yahooTab,{type:'pick',field:message.field});}
      if(message.type==='pause'){c.watching=!c.watching;if(c.yahooTab)await injectYahoo(c);}
      if(message.type==='selectors'){if(!message.selectors||typeof message.selectors!=='object'||Array.isArray(message.selectors))throw Error('Selectors must be an object.');c.selectors=message.selectors;if(c.yahooTab)await injectYahoo(c);}
      if(message.type==='clear'){c.watching=false;c.queue=[];c.seen=[];c.source=crypto.randomUUID();c.sessionId=null;c.status={ready:false,message:'Queue cleared. Pair Survivor and review any missed activity.'};if(c.appTab)await injectApp(c);if(c.yahooTab)await injectYahoo(c);}
    }
    await save(c);return {ok:true};
  }).then(sendResponse,error=>sendResponse({error:error.message}));
  return true;
});
chrome.tabs.onUpdated.addListener((id,change)=>{if(change.status!=='complete')return;serial(async()=>{const c=await load();try{if(id===c.appTab)await injectApp(c);if(id===c.yahooTab)await injectYahoo(c);}catch{/* Popup explains missing access; never request new permissions automatically. */}});});
chrome.tabs.onRemoved.addListener(id=>serial(async()=>{const c=await load();if(id===c.yahooTab){c.watching=false;c.status={ready:false,message:'Yahoo tab closed. Reopen it and resume watching.'};await appMessage(c,{type:'heartbeat',status:c.status});}if(id===c.appTab)c.appTab=null;await save(c);}));
chrome.alarms.create('draft-retry',{periodInMinutes:.5});
chrome.alarms.onAlarm.addListener(()=>serial(async()=>{const c=await load();await flush(c);}));
