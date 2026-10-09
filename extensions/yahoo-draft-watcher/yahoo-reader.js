/* Read the rendered auction UI by its labels, rather than user-picked CSS paths.
   No page internals, network interception, credentials, or Yahoo write actions. */
(function(root,factory){const api=factory();if(typeof module==='object'&&module.exports)module.exports=api;else root.SurvivorYahooReader=api;})(globalThis,function(){
  'use strict';
  const clean=s=>String(s||'').replace(/\s+/g,' ').trim();
  const key=s=>clean(s).normalize('NFD').replace(/[\u0300-\u036f]/g,'').toLowerCase().replace(/[^a-z0-9]/g,'');
  const words=s=>clean(s).normalize('NFD').replace(/[\u0300-\u036f]/g,'').toLowerCase().replace(/[^a-z0-9]+/g,' ').trim();
  const money=s=>{const m=clean(s).match(/^\$\s*([\d,]+)$/);return m?Number(m[1].replaceAll(',','')):null;};
  const text=e=>clean(e?.innerText??e?.textContent);
  const resultRegions=new WeakMap();
  const usable=e=>!e.closest('script,style,noscript,template,[hidden],[aria-hidden="true"]')&&!!e.getClientRects().length;
  function identity(url,title=''){
    const u=new URL(url);if(/(?:token|auth|session|key|code)=/i.test(u.hash))u.hash='';
    for(const k of [...u.searchParams.keys()])if(/^(?:_|ts|timestamp|cache|token|auth|session)/i.test(k))u.searchParams.delete(k);
    u.searchParams.sort();
    return {key:u.origin+u.pathname+u.search+u.hash,purpose:/mock/i.test(u.pathname+u.search+u.hash+' '+title)?'mock':'unknown'};
  }
  function teamRow(value){
    const s=clean(value),slots=[...s.matchAll(/\b(\d{1,2})\s*\/\s*(\d{1,2})\b/g)],dollars=[...s.matchAll(/\$\s*[\d,]+(?:\.\d+)?/g)];
    if(slots.length!==1||dollars.length!==1)return null;
    const owned=Number(slots[0][1]),size=Number(slots[0][2]),cash=money(dollars[0][0]);
    const name=clean(s.replace(slots[0][0],'').replace(dollars[0][0],''));
    if(cash===null||cash>10000||size<1||size>30||owned>size||!name||name.length>100||/\b(?:max offer|budget|nominate|offer)\b/i.test(name))return null;
    return {name,cash,owned,size};
  }
  function catalogue(players){
    const aliases=new Map();
    function put(alias,name){const k=words(alias);if(!k)return;const values=aliases.get(k)||new Set();values.add(name);aliases.set(k,values);}
    for(const p of players||[]){const name=clean(p.player);if(!name)continue;put(name,name);const parts=name.split(' ');if(parts.length>1)put(parts[0][0]+'. '+parts.slice(1).join(' '),name);}
    return value=>{
      const s=' '+words(value)+' ',found=new Set();
      for(const [alias,names] of aliases)if(s.includes(' '+alias+' ')){if(names.size!==1)return null;for(const name of names)found.add(name);}
      return found.size===1?[...found][0]:null;
    };
  }
  function owner(value,teams){
    const s=words(value),found=teams.filter(t=>s===words(t.name)||s===words('leading '+t.name)||s===words('high bidder '+t.name)||s===words('sold to '+t.name)||s===words('won by '+t.name)||s===words('drafted by '+t.name));
    return found.length===1?found[0].name:null;
  }
  function scan(doc,players=[]){
    const elements=[...doc.querySelectorAll('body *')].filter(usable),visible=new Set(elements),cache=new Map();
    // Separate adjacent text nodes: "$200" beside "00:51" is not "$20000".
    const txt=e=>{if(!cache.has(e))cache.set(e,clean([...e.childNodes].map(n=>n.nodeType===3?n.textContent:n.nodeType===1&&visible.has(n)?txt(n):'').join(' ')));return cache.get(e);};
    // Only exact roster counters qualify, not player statistics or unrelated page text.
    const counters=elements.filter(e=>/^\d{1,2}\s*\/\s*\d{1,2}$/.test(txt(e))),rows=[],rowNodes=[];
    for(const counter of counters){let el=counter;for(let n=0;el&&n<6;n++,el=el.parentElement){const value=txt(el);if(value.length>260)break;const row=teamRow(value);if(row){if(!rowNodes.includes(el)){rows.push(row);rowNodes.push(el);}break;}}}
    const unique=new Set(rows.map(t=>key(t.name)));
    const auction=elements.filter(e=>txt(e).length<1600&&/\bMax Offer\s*\$/i.test(txt(e))&&/\bBudget\s*\$/i.test(txt(e))&&[...e.querySelectorAll('button,[role="button"]')].some(b=>/^Offer\s+\$/i.test(txt(b)))).sort((a,b)=>txt(a).length-txt(b).length)[0];
    if(!auction||rows.length<2||rows.length>30||unique.size!==rows.length||new Set(rows.map(t=>t.size)).size!==1)return {detected:false,message:'Waiting for Yahoo’s salary-cap draft room.'};
    const rowCount=Math.max(0,...rowNodes.map(n=>{let p=n;for(let i=0;p&&i<5;i++,p=p.parentElement){const count=Number(p.getAttribute('aria-rowcount')||p.getAttribute('aria-setsize'));if(count>0)return count;}return 0;}));
    if(rowCount>rows.length)return {detected:true,complete:false,message:'Waiting for the complete Yahoo team list.'};
    const resolve=catalogue(players),inside=elements.filter(e=>auction.contains(e));
    const smallest=inside.filter(e=>![...e.children].some(c=>txt(c)===txt(e)));
    const names=new Set(smallest.map(e=>resolve(e.getAttribute('title')||txt(e))).filter(Boolean));
    const player=names.size===1?[...names][0]:null;
    function offerControl(el){
      if(el.closest('button,[role="button"],input,select'))return true;
      for(let p=el.parentElement;p&&p!==auction;p=p.parentElement){
        if(/\b(?:Max Offer|Budget)\b/i.test(txt(p)))return true;
        if([...p.querySelectorAll('button,[role="button"]')].some(b=>/^(?:Offer\s*\$|[+−-]$)/i.test(txt(b))))return true;
      }return false;
    }
    const priceNodes=smallest.filter(e=>money(txt(e))!==null&&!offerControl(e));
    const prices=[...new Set(priceNodes.map(e=>money(txt(e))))];
    const owners=[...new Set(smallest.map(e=>owner(txt(e),rows)).filter(Boolean))];
    const at=txt(auction),sold=/\b(?:sold\b|won by|drafted by)/i.test(at)&&!(/\b(?:not sold|unsold)\b/i.test(at));
    const timer=smallest.map(txt).find(s=>/^\d{1,2}:\d{2}$/.test(s))||'';
    const results=[],resultNodes=new Set();
    // A last-pick or completed-picks region supplies explicit sale evidence.
    const labels=elements.filter(e=>/^(?:Last Pick(?: will appear here)?|Picks|Draft Results|Drafted Players|Pick History)$/i.test(txt(e)));
    const remembered=(resultRegions.get(doc)||[]).filter(e=>e.isConnected&&!e.contains(auction));
    for(const label of labels.filter(e=>/^Last Pick/i.test(txt(e)))){
      let candidate=label;
      for(let i=0;i<4&&candidate.parentElement;i++){const parent=candidate.parentElement;if(txt(parent).length>500||parent.contains(auction)||rowNodes.some(n=>parent.contains(n)))break;candidate=parent;}
      if(!remembered.includes(candidate))remembered.push(candidate);
    }
    resultRegions.set(doc,remembered.slice(-12));labels.push(...remembered);
    for(const label of labels){let region=label;for(let i=0;i<4&&region?.parentElement;i++,region=region.parentElement){
      const value=txt(region);if(value.length>15000||region.contains(auction)||rowNodes.some(n=>region.contains(n)))break;
      const descendants=[region,...region.querySelectorAll('*')];let found=false;
      for(const el of descendants){const s=txt(el);if(s.length>450||!usable(el)||el.closest('button,[role="button"]'))continue;
        const p=resolve(s),dollars=[...s.matchAll(/\$\s*[\d,]+/g)];if(!p||dollars.length!==1)continue;
        const teams=rows.filter(t=>(' '+words(s)+' ').includes(' '+words(t.name)+' '));if(teams.length!==1)continue;
        const amount=money(dollars[0][0]);if(!(amount>0))continue;
        const signature=JSON.stringify([p,teams[0].name,amount]);if(!resultNodes.has(signature)){resultNodes.add(signature);results.push({player:p,team:teams[0].name,amount});}found=true;
      }if(found)break;
    }}
    const budgetMatch=at.match(/\bBudget\s*\$\s*([\d,]+)/i),own=rows.find(t=>/^you$/i.test(t.name));
    if(own&&budgetMatch&&own.cash!==Number(budgetMatch[1].replaceAll(',','')))return {detected:true,complete:false,message:'Yahoo team list is not showing remaining budgets.'};
    const teams=rows.map(t=>{const won=results.filter(r=>r.team===t.name);return {...t,budget:won.length===t.owned?t.cash+won.reduce((sum,r)=>sum+r.amount,0):null};});
    return {detected:true,complete:teams.every(t=>t.budget!==null),teams,rosterSize:rows[0].size,ownTeam:own?.name||null,player,amount:prices.length===1?prices[0]:null,team:owners.length===1?owners[0]:null,sold,timer,results,
      waiting:/Draft Starting Soon|YOU NOMINATE|NOMINATES NEXT/i.test(doc.body.innerText||''),message:'Yahoo salary-cap room detected.'};
  }
  return {clean,key,money,identity,teamRow,catalogue,scan};
});
