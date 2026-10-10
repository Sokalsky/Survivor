/* Read the rendered auction UI by its labels, rather than user-picked CSS paths.
   No page internals, network interception, credentials, or Yahoo write actions. */
(function(root,factory){if(typeof module!=='object'&&root.SurvivorYahooReader?.version==='0.3.8')return;const api=factory();if(typeof module==='object'&&module.exports)module.exports=api;else root.SurvivorYahooReader=api;})(globalThis,function(){
  'use strict';
  const clean=s=>String(s||'').replace(/\s+/g,' ').trim();
  const key=s=>clean(s).normalize('NFD').replace(/[\u0300-\u036f]/g,'').toLowerCase().replace(/[^a-z0-9]/g,'');
  const words=s=>clean(s).normalize('NFD').replace(/[\u0300-\u036f]/g,'').toLowerCase().replace(/[^a-z0-9]+/g,' ').trim();
  const money=s=>{const m=clean(s).match(/^\$\s*([\d,]+)$/);return m?Number(m[1].replaceAll(',','')):null;};
  const text=e=>clean(e?.innerText??e?.textContent);
  const resultRegions=new WeakMap();
  const roomStates=new WeakMap();
  const usable=e=>!e.closest('script,style,noscript,template,[hidden],[aria-hidden="true"]')&&!!e.getClientRects().length;
  function identity(url,title=''){
    const u=new URL(url);if(/(?:token|auth|session|key|code)=/i.test(u.hash))u.hash='';
    for(const k of [...u.searchParams.keys()])if(/^(?:_|ts|timestamp|cache|token|auth|session)/i.test(k))u.searchParams.delete(k);
    u.searchParams.sort();
    return {key:u.origin+u.pathname+u.search+u.hash,purpose:/mock/i.test(u.pathname+u.search+u.hash+' '+title)?'mock':'unknown'};
  }
  function teamRow(value){
    const s=clean(value),slots=[...s.matchAll(/\b(\d{1,2})\s*\/\s*(\d{1,2})\b/g)],dollars=[...s.matchAll(/\$\s*[\d,]+(?:\.\d+)?/g)];
    if(slots.length!==1||!dollars.length)return null;
    // Yahoo prepends each participant's last bid to the budget row during an auction.
    // The wallet is the rightmost money value before the roster counter.
    const wallet=dollars.filter(m=>m.index<slots[0].index).at(-1);if(!wallet)return null;
    const owned=Number(slots[0][1]),size=Number(slots[0][2]),cash=money(wallet[0]);
    const name=clean(s.replace(slots[0][0],'').replace(/\$\s*[\d,]+(?:\.\d+)?/g,''));
    if(cash===null||cash>10000||size<1||size>30||owned>size||!name||name.length>100||/\b(?:max offer|budget|nominate|offer)\b/i.test(name))return null;
    const badges=dollars.filter(m=>m.index<wallet.index),bid=badges.length===1?money(badges[0][0]):null;
    return {name,cash,owned,size,...(bid>0&&bid<=10000?{bid}:{})};
  }
  function catalogue(players){
    const aliases=new Map();
    function put(alias,name){const k=words(alias);if(!k)return;const values=aliases.get(k)||new Set();values.add(name);aliases.set(k,values);}
    for(const p of players||[]){const name=clean(p.player);if(!name)continue;put(name,name);const parts=name.split(' ');if(parts.length>1)put(parts[0][0]+'. '+parts.slice(1).join(' '),name);}
    return value=>{
      const s=' '+words(value)+' ',found=new Set(),hits=[];
      for(const [alias,names] of aliases){
        const needle=' '+alias+' ';let start=s.indexOf(needle);
        while(start!==-1){hits.push({start,end:start+needle.length,names});start=s.indexOf(needle,start+1);}
      }
      // A suffix is identity evidence: "J. Smith Jr." must beat its contained
      // "J. Smith" match. Separate player names and genuinely shared aliases
      // remain ambiguous; never choose a player just because their name is longer.
      for(const hit of hits){
        if(hits.some(other=>other.start<=hit.start&&other.end>=hit.end&&(other.start<hit.start||other.end>hit.end)))continue;
        if(hit.names.size!==1)return null;
        for(const name of hit.names)found.add(name);
      }
      return found.size===1?[...found][0]:null;
    };
  }
  function owner(value,teams){
    const s=words(value),found=teams.filter(t=>s===words(t.name)||s===words('leading '+t.name)||s===words('high bidder '+t.name)||s===words('sold to '+t.name)||s===words('won by '+t.name)||s===words('drafted by '+t.name));
    return found.length===1?found[0].name:null;
  }
  function scan(doc,players=[],teamAliases=[]){
    const elements=[...doc.querySelectorAll('body *')].filter(usable),visible=new Set(elements),cache=new Map();
    // Separate adjacent text nodes: "$200" beside "00:51" is not "$20000".
    const txt=e=>{if(!cache.has(e))cache.set(e,clean([...e.childNodes].map(n=>n.nodeType===3?n.textContent:n.nodeType===1&&visible.has(n)?txt(n):'').join(' ')));return cache.get(e);};
    // Only exact roster counters qualify, not player statistics or unrelated page text.
    const counters=elements.filter(e=>/^\d{1,2}\s*\/\s*\d{1,2}$/.test(txt(e))),rows=[],rowNodes=[];
    for(const counter of counters){let el=counter;for(let n=0;el&&n<6;n++,el=el.parentElement){const value=txt(el);if(value.length>260)break;const row=teamRow(value);if(row){if(!rowNodes.includes(el)){rows.push(row);rowNodes.push(el);}break;}}}
    const unique=new Set(rows.map(t=>key(t.name)));
    const roomKey=identity(doc.URL).key+'|'+rows.map(t=>key(t.name)+':'+t.size).sort().join('|');
    let memory=roomStates.get(doc);
    if(!memory||memory.key!==roomKey){
      memory={key:roomKey,wallets:new Map(rows.map(t=>[t.name,{cash:t.cash,owned:t.owned}])),bids:new Map(),results:new Map(),settled:new Set()};
      roomStates.set(doc,memory);resultRegions.delete(doc);
    }
    let auction=elements.filter(e=>txt(e).length<1600&&/\bMax Offer\s*\$/i.test(txt(e))&&/\bBudget\s*\$/i.test(txt(e))&&[...e.querySelectorAll('button,[role="button"]')].some(b=>/^(?:Offer|Nominate)\s+\$/i.test(txt(b)))).sort((a,b)=>txt(a).length-txt(b).length)[0];
    if(!auction||rows.length<2||rows.length>30||unique.size!==rows.length||new Set(rows.map(t=>t.size)).size!==1)return {detected:false,message:'Waiting for Yahoo’s salary-cap draft room.'};
    const rowCount=Math.max(0,...rowNodes.map(n=>{let p=n;for(let i=0;p&&i<5;i++,p=p.parentElement){const count=Number(p.getAttribute('aria-rowcount')||p.getAttribute('aria-setsize'));if(count>0)return count;}return 0;}));
    if(rowCount>rows.length)return {detected:true,complete:false,message:'Waiting for the complete Yahoo team list.'};
    function offerControl(el,scope=auction){
      if(el.closest('button,[role="button"],input,select'))return true;
      for(let p=el.parentElement;p&&p!==scope;p=p.parentElement){
        if(/\b(?:Max Offer|Budget)\b/i.test(txt(p)))return true;
        if(/\bProj(?:ected)?\b/i.test(txt(p))&&[...txt(p).matchAll(/\$\s*[\d,]+/g)].length===1)return true;
        if([...p.querySelectorAll('button,[role="button"]')].some(b=>/^(?:Offer\s*\$|[+−-]$)/i.test(txt(b))))return true;
      }return false;
    }
    // The controls can be a sibling of the nomination, so include their nearest
    // shared card. Stop before the participants list or the available-player board.
    for(let i=0;i<6;i++){
      if(elements.some(e=>auction.contains(e)&&money(txt(e))!==null&&!offerControl(e)))break;
      const parent=auction.parentElement;
      if(!parent||parent===doc.body||txt(parent).length>2000||rowNodes.some(n=>parent.contains(n)))break;
      auction=parent;
    }
    const resolve=catalogue(players),inside=elements.filter(e=>auction.contains(e));
    const smallest=inside.filter(e=>![...e.children].some(c=>txt(c)===txt(e)));
    const names=new Set(smallest.flatMap(e=>[resolve(txt(e)),resolve(e.getAttribute('title'))]).filter(Boolean));
    const player=names.size===1?[...names][0]:null;
    const priceNodes=smallest.filter(e=>money(txt(e))!==null&&!offerControl(e));
    const prices=[...new Set(priceNodes.map(e=>money(txt(e))))];
    const owners=[...new Set(smallest.map(e=>owner(txt(e),rows)).filter(Boolean))];
    const amount=prices.length===1?prices[0]:null,badges=amount>0?rows.filter(t=>t.bid===amount):[];
    let leader=owners.length===1?owners[0]:null,leaderBasis=leader?'name':null;
    // Yahoo calls the local manager "You" in the roster list but uses their
    // display name on the auction card. A unique matching bid badge identifies
    // that roster row without guessing that every unknown name belongs to us.
    if(!owners.length&&badges.length===1){leader=badges[0].name;leaderBasis='team_bid_badge';}
    if(leader&&badges.length===1&&leader!==badges[0].name){leader=null;leaderBasis='conflicting_bid_badge';}
    const at=txt(auction),sold=/\b(?:sold\b|won by|drafted by)/i.test(at)&&!(/\b(?:not sold|unsold)\b/i.test(at));
    const auctionActive=[...auction.querySelectorAll('button,[role="button"]')].some(b=>usable(b)&&/^Offer\s+\$/i.test(txt(b)));
    const budgetMatch=at.match(/\bBudget\s*\$\s*([\d,]+)/i),own=rows.find(t=>/^you$/i.test(t.name));
    if(own&&budgetMatch&&own.cash!==Number(budgetMatch[1].replaceAll(',','')))return {detected:true,complete:false,message:'Yahoo team list is not showing remaining budgets.'};
    if(auctionActive&&player&&leader&&amount>0&&!memory.results.has(player)){
      const before=memory.bids.get(player);
      if(!before||amount>=before.amount)memory.bids.set(player,{team:leader,amount});
    }
    const timer=smallest.map(txt).find(s=>/^\d{1,2}:\d{2}$/.test(s))||'';
    const results=[],resultNodes=new Set();
    // A last-pick or completed-picks region supplies explicit sale evidence.
    const isLast=e=>/^Last\s*:\s*/i.test(txt(e))||/^Last Pick(?: will appear here)?$/i.test(txt(e));
    const labels=elements.filter(e=>isLast(e)&&txt(e).length<500||/^(?:Picks|Draft Results|Drafted Players|Pick History)$/i.test(txt(e)));
    const remembered=(resultRegions.get(doc)||[]).filter(e=>e.isConnected&&!e.contains(auction));
    for(const label of labels.filter(isLast)){
      let candidate=label;
      for(let i=0;i<4&&candidate.parentElement;i++){const parent=candidate.parentElement;if(txt(parent).length>500||parent.contains(auction)||rowNodes.some(n=>parent.contains(n)))break;candidate=parent;}
      if(!remembered.includes(candidate))remembered.push(candidate);
    }
    resultRegions.set(doc,remembered.slice(-12));labels.push(...remembered);
    for(const label of labels){const lastPick=isLast(label)||remembered.includes(label);let region=label;for(let i=0;i<4&&region?.parentElement;i++,region=region.parentElement){
      const value=txt(region);if(value.length>15000||region.contains(auction)||rowNodes.some(n=>region.contains(n)))break;
      const descendants=[region,...region.querySelectorAll('*')];let found=false;
      for(const el of descendants){const s=txt(el);if(s.length>450||!usable(el)||el.closest('button,[role="button"]'))continue;
        const p=resolve(s),dollars=[...s.matchAll(/\$\s*[\d,]+/g)];if(!p)continue;
        const teams=rows.filter(t=>(' '+words(s)+' ').includes(' '+words(t.name)+' '));
        let result=null;
        if(dollars.length===1&&teams.length===1){
          const paid=money(dollars[0][0]);if(paid>0)result={player:p,team:teams[0].name,amount:paid};
        }else if(lastPick&&!dollars.length&&remembered.includes(el)){
          const bid=memory.bids.get(p),current=bid&&rows.find(t=>t.name===bid.team),before=current&&memory.wallets.get(current.name);
          // Last pick confirms the player was awarded. Require a captured bid,
          // exactly one new roster spot, and that exact cash decrease as well.
          // A clock ending, a nomination changing, or a bid alone is insufficient.
          if(bid&&before&&current.owned===before.owned+1&&before.cash-current.cash===bid.amount&&(!teams.length||teams.length===1&&teams[0].name===bid.team)){
            result={player:p,team:bid.team,amount:bid.amount};
          }
        }
        if(!result)continue;
        const signature=JSON.stringify(result);if(!resultNodes.has(signature)){resultNodes.add(signature);results.push(result);}found=true;
      }if(found)break;
    }}
    // Real leagues publish completed purchases as separate cards in the Updates
    // feed. Unlike the Last pick strip, each card includes the winning price.
    // Require separate team, player and dollar fields; ordinary chat containing
    // a player and a dollar amount is not a confirmed purchase.
    for(const label of elements.filter(e=>/^Updates$/i.test(txt(e)))){
      let region=label;
      for(let i=0;i<7&&region;i++,region=region.parentElement){
        if(region===doc.body||region.contains(auction)||rowNodes.some(n=>region.contains(n)))break;
        let found=false;
        for(const el of region.querySelectorAll('*')){
          const s=txt(el);if(s.length>450||!usable(el)||el.closest('button,[role="button"]'))continue;
          const dollars=[...s.matchAll(/\$\s*[\d,]+/g)],p=resolve(s);
          if(!p||dollars.length!==1)continue;
          const paid=money(dollars[0][0]);if(!(paid>0&&paid<=10000))continue;
          const fields=[...el.querySelectorAll('*')].filter(usable);
          const parts=p.split(' '),short=parts[0][0]+'. '+parts.slice(1).join(' ');
          if(!fields.some(n=>words(txt(n))===words(p)||words(txt(n))===words(short))||!fields.some(n=>money(txt(n))===paid))continue;
          // Keep Yahoo's exact identity. Shared owner mappings must never turn
          // one visible Yahoo team into another team or an ambiguous purchase.
          const teams=rows.filter(t=>fields.some(n=>words(txt(n))===words(t.name)));
          let winner=teams.length===1?teams[0].name:null;
          const ownFranchise=own&&teamAliases.find(a=>key(a.name)===key(own.name))?.team;
          if(!teams.length&&ownFranchise&&fields.some(n=>teamAliases.some(a=>a.team===ownFranchise&&key(a.name)===key(txt(n)))))winner=own.name;
          // The Updates card uses our actual team name while the wallet says
          // "You". A captured winning bid plus its exact wallet/slot change
          // confirms this purchase without treating all unknown names as us.
          if(!teams.length&&own){
            const bid=memory.bids.get(p),before=memory.wallets.get(own.name);
            if(bid?.team===own.name&&bid.amount===paid&&before&&own.owned===before.owned+1&&before.cash-own.cash===paid)winner=own.name;
          }
          if(!winner)continue;
          const result={player:p,team:winner,amount:paid,...(!memory.bids.has(p)?{recovered:true}:{})},signature=JSON.stringify(result);
          if(!resultNodes.has(signature)){resultNodes.add(signature);results.push(result);}found=true;
        }
        if(found)break;
      }
    }
    for(const result of results)memory.results.set(result.player,result);
    for(const current of rows){
      const before=memory.wallets.get(current.name);
      const matched=[...memory.results.values()].filter(r=>r.team===current.name&&!memory.settled.has(r.player)&&current.owned===before.owned+1&&before.cash-current.cash===r.amount);
      if(matched.length===1){memory.settled.add(matched[0].player);memory.wallets.set(current.name,{cash:current.cash,owned:current.owned});}
      // A correction or a capture gap cannot support a one-purchase calculation.
      else if(current.owned>before.owned+1||current.owned<before.owned||current.cash>before.cash)memory.wallets.set(current.name,{cash:current.cash,owned:current.owned});
    }
    for(const result of memory.results.values())if(!results.some(r=>r.player===result.player))results.push(result);
    const teams=rows.map(t=>{const won=results.filter(r=>r.team===t.name);return {...t,budget:won.length===t.owned?t.cash+won.reduce((sum,r)=>sum+r.amount,0):null};});
    return {detected:true,complete:teams.every(t=>t.budget!==null),teams,rosterSize:rows[0].size,ownTeam:own?.name||null,player,amount,team:leader,sold,timer,auctionActive,results,diagnostics:{auctionText:at.slice(0,1800),playerCandidates:[...names],priceCandidates:prices,leaderCandidates:owners,leaderBasis,bidBadgeCandidates:badges.map(t=>t.name),teamRows:rows.length,cataloguePlayers:players.length},
      waiting:/Draft Starting Soon|YOU NOMINATE|NOMINATES NEXT/i.test(doc.body.innerText||''),message:'Yahoo salary-cap room detected.'};
  }
  return {version:'0.3.8',clean,key,money,identity,teamRow,catalogue,scan};
});
