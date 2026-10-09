/* Passive DOM capture. No Yahoo cookies, network interception, clicks or bids. */
(()=>{'use strict';if(globalThis.__survivorCapture)return;globalThis.__survivorCapture=true;
 let selectors={},watching=false,previous='',lastBid='',lastSale='',saleCandidate='',saleSince=0,nominationSince=0,picking=null,overlay=null,scheduled=false;
 const sentRows=new Set();let resultsPrimed=false,captureId=null;
 const send=m=>chrome.runtime.sendMessage(m).catch(()=>{});
 const text=el=>String(el?.innerText||el?.textContent||'').replace(/\s+/g,' ').trim();
 const read=(selector,parent=document)=>selector?text(parent.querySelector(selector)):'';
 function amount(value){const s=String(value).trim();const dollar=[...s.matchAll(/\$\s*([\d,]+)(?:\.(\d{2}))?(?![\d.])/g)];if(dollar.length===1&&(!dollar[0][2]||dollar[0][2]==='00'))return Number(dollar[0][1].replaceAll(',',''));return /^\d+$/.test(s)?Number(s):null;}
 const fields=['player','bid','bidder','sold','timer','resultRow','resultPlayer','resultTeam','resultAmount','logRow','logPlayer','logTeam','logAmount'];
 function complete(){return !!(selectors.player&&selectors.bid&&selectors.bidder&&(selectors.sold||selectors.resultRow&&selectors.resultPlayer&&selectors.resultTeam&&selectors.resultAmount));}
 function collect(){
  if(!watching||picking)return;
  const events=[];
  try{
   const name=read(selectors.player),price=amount(read(selectors.bid)),team=read(selectors.bidder),state=read(selectors.sold),now=Date.now();
   if(name&&name!==previous){previous=name;lastBid='';lastSale='';saleCandidate='';nominationSince=now;events.push({id:crypto.randomUUID(),type:'nominate',player:name});}
   const sold=!!state&&/\b(sold(?: to)?|drafted by|won by)\b/i.test(state)&&!/\b(not|unsold|unsuccessful)\b/i.test(state);
   const signature=JSON.stringify([name,team,price]);
   if(name&&team&&price>0&&now-nominationSince>=120){
    if(sold){if(saleCandidate!==signature){saleCandidate=signature;saleSince=now;}if(now-saleSince>=350&&lastSale!==signature){events.push({type:'sale',player:name,team,amount:price});lastSale=signature;}}
    else {saleCandidate='';if(lastBid!==signature){events.push({type:'bid',player:name,team,amount:price});lastBid=signature;}}
   }
   for(const kind of ['result','log']){
    if(!selectors[kind+'Row']||!selectors[kind+'Player']||!selectors[kind+'Team']||!selectors[kind+'Amount'])continue;
    const rows=[...document.querySelectorAll(selectors[kind+'Row'])].slice(-400);
    for(const row of rows){const player=read(selectors[kind+'Player'],row),owner=read(selectors[kind+'Team'],row),paid=amount(read(selectors[kind+'Amount'],row));if(!player||!owner||!(paid>0))continue;
      const type=kind==='result'?'sale':'bid',sig=JSON.stringify([type,player,owner,paid]);if(sentRows.has(sig))continue;
      /* Log rows require their own player name; old bids are never assigned to the current player by guesswork. */
      events.push({type,player,team:owner,amount:paid,history:kind==='log',recovered:kind==='result'&&!resultsPrimed});sentRows.add(sig);
    }
   }
   if(selectors.resultRow)resultsPrimed=true;
   if(events.length){const earlier=events.filter(e=>e.type==='sale'&&e.player!==name),current=events.filter(e=>!(e.type==='sale'&&e.player!==name));send({type:'observation',events:[...earlier,...current]});}
  }catch(err){send({type:'heartbeat',ready:false,message:'Selected fields could not be read: '+err.message});}
 }
 function schedule(){if(scheduled)return;scheduled=true;queueMicrotask(()=>{scheduled=false;collect();});}
 new MutationObserver(schedule).observe(document.documentElement,{subtree:true,childList:true,characterData:true,attributes:true,attributeFilter:['aria-label','data-value','data-state']});
 setInterval(collect,180);
 setInterval(()=>{try{const player=read(selectors.player);send({type:'heartbeat',ready:watching&&complete()&&!!player,message:!watching?'Paused':!complete()?'Select player, bid, bidder and explicit sale confirmation.':!player?'Selected player field is not visible. Check selectors.':'Reading selected Yahoo draft fields. Verify first result against Yahoo.',timer:read(selectors.timer)});}catch(err){send({type:'heartbeat',ready:false,message:'A selector is invalid. Re-select the field.'});}},1500);
 function path(el,stop){
  if(el===stop)return ':scope';
  const parts=[];while(el&&el!==stop&&el.nodeType===1){
   if(!stop&&el.id&&document.querySelectorAll('#'+CSS.escape(el.id)).length===1){parts.unshift('#'+CSS.escape(el.id));break;}
   let segment=el.tagName.toLowerCase();const siblings=el.parentElement?[...el.parentElement.children].filter(n=>n.tagName===el.tagName):[];
   if(siblings.length>1)segment+=':nth-of-type('+(siblings.indexOf(el)+1)+')';parts.unshift(segment);el=el.parentElement;
  }return parts.join(' > ');
 }
 function unique(el){for(const attr of ['data-testid','data-test']){const value=el.getAttribute(attr);if(value){const sel='['+attr+'="'+CSS.escape(value)+'"]';if(document.querySelectorAll(sel).length===1)return sel;}}return path(el);}
 function stop(){picking=null;overlay?.remove();overlay=null;document.removeEventListener('click',pick,true);document.removeEventListener('mousemove',highlight,true);document.removeEventListener('keydown',escape,true);}
 function highlight(e){if(!overlay||e.target===overlay)return;const r=e.target.getBoundingClientRect();Object.assign(overlay.style,{left:r.left+'px',top:r.top+'px',width:r.width+'px',height:r.height+'px'});}
 function escape(e){if(e.key==='Escape'){e.preventDefault();stop();}}
 function pick(e){e.preventDefault();e.stopPropagation();e.stopImmediatePropagation();const el=e.target,field=picking;let selector;
  if(field==='resultRow'||field==='logRow'){const row=el.closest('tr,[role="row"],li')||el;selector=unique(row.parentElement)+' > '+row.tagName.toLowerCase();}
  else if(/^(result|log)(Player|Team|Amount)$/.test(field)){const prefix=field.startsWith('result')?'result':'log',row=selectors[prefix+'Row']?el.closest(selectors[prefix+'Row']):null;if(!row){stop();send({type:'heartbeat',ready:false,message:'Select a result/log row first, then select its cells.'});return;}selector=path(el,row);}
  else selector=unique(el);
  stop();send({type:'picked',field,selector});
 }
 chrome.runtime.onMessage.addListener(m=>{
  if(m.type==='configure'){if(m.captureId!==captureId){captureId=m.captureId;previous='';lastBid='';lastSale='';sentRows.clear();resultsPrimed=false;}if(JSON.stringify(selectors)!==JSON.stringify(m.selectors)){resultsPrimed=false;sentRows.clear();}selectors=m.selectors||{};watching=!!m.watching;collect();}
  if(m.type==='pick'&&fields.includes(m.field)){stop();picking=m.field;overlay=document.createElement('div');Object.assign(overlay.style,{position:'fixed',zIndex:2147483647,pointerEvents:'none',border:'2px solid #9762e8',background:'#9762e825'});document.documentElement.appendChild(overlay);document.addEventListener('mousemove',highlight,true);document.addEventListener('click',pick,true);document.addEventListener('keydown',escape,true);}
 });
})();
