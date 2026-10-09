/* Shared, deterministic draft reducer and advisory model. No network or DOM access. */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.SurvivorDraft = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';
  const CATS = ['PTS','REB','AST','STL','BLK','3PM','FG%','FT%'];
  const STATS = ['games','minutes_pg','pts_pg','reb_pg','ast_pg','stl_pg','blk_pg','fg3m_pg','fgm_pg','fga_pg','ftm_pg','fta_pg'];
  const COUNT = ['pts_pg','reb_pg','ast_pg','stl_pg','blk_pg','fg3m_pg'];
  const TOTAL_FIELDS = [...COUNT,'fgm_pg','fga_pg','ftm_pg','fta_pg'];
  const outlookCache = new WeakMap();
  const clamp = (n,lo,hi) => Math.min(hi,Math.max(lo,n));
  const round = n => Math.round(n*100)/100;
  const key = name => String(name || '').normalize('NFD').replace(/[\u0300-\u036f]/g,'').toLowerCase().replace(/[^a-z0-9]/g,'');
  const finite = (n,fallback=0) => Number.isFinite(Number(n)) ? Number(n) : fallback;
  const parse = value => typeof value === 'string' ? JSON.parse(value) : value || {};
  function player(row) {
    const details = parse(row.category_values_json);
    const p = {player_id:row.player_id, player:row.player, positions:row.positions || '', nba_team:row.nba_team || '',
      fair_value:Math.max(0,finite(row.fair_value)), expected_auction_price:Math.max(1,finite(row.expected_auction_price,1)),
      survivor_score:finite(row.survivor_score ?? details.score), z:{}, risk_notes:row.risk_notes || ''};
    for (const k of STATS) p[k] = Math.max(0,finite(row[k]));
    for (const k of CATS) p.z[k] = finite((row.z || details.rate_z || {})[k]);
    return p;
  }
  function create(rows,keepers,options={}) {
    if (!rows.length || !keepers?.teams?.length || !keepers?.rows) throw Error('A saved valuation run and confirmed keepers are required.');
    const players = rows.map(player);
    if (new Set(players.map(p=>p.player_id)).size !== players.length) throw Error('Duplicate player identities.');
    const teams = keepers.teams.map(t=>({name:t.franchise,budget:Number(t.budget_per_team ?? keepers.budget_per_team)}));
    const roster = keepers.rows.map(k=>({playerId:k.player_id,team:k.franchise,amount:Number(k.keeper_cost),keeper:true}));
    if (roster.some(k=>!players.some(p=>p.player_id===k.playerId))) throw Error('Every keeper needs a projection before draft advice can be calculated.');
    return {version:1,id:options.id || (globalThis.crypto?.randomUUID?.() || 'draft-'+Date.now()), mode:options.mode || 'live',
      createdAt:new Date().toISOString(),season:keepers.season,baselineRunId:options.runId || rows[0].run_id || '',
      players,teams,keepers:roster,settings:{rosterSize:15,minimumBid:1,reservePerSlot:1,rosterSlots:[],gamesCap:1000},events:[]};
  }
  function positions(p) { return (p.positions || '').toUpperCase().match(/PG|SG|SF|PF|C|(?<![A-Z])G|(?<![A-Z])F/g) || []; }
  function eligible(p,slot) {
    if (['UTIL','BN'].includes(slot)) return true;
    const pos=positions(p);
    return pos.some(v=>v===slot || slot==='G' && ['PG','SG'].includes(v) || slot==='F' && ['SF','PF'].includes(v));
  }
  function canFit(players,slots) {
    if (!slots.length) return true;
    const matched=Array(slots.length).fill(-1);
    function assign(index,seen) {
      for (let j=0;j<slots.length;j++) if (!seen.has(j) && eligible(players[index],slots[j])) {
        seen.add(j);
        if (matched[j]<0 || assign(matched[j],seen)) { matched[j]=index; return true; }
      }
      return false;
    }
    return players.every((_,i)=>assign(i,new Set()));
  }
  function validateSettings(settings) {
    const {rosterSize,minimumBid,reservePerSlot,rosterSlots} = settings;
    if (!Number.isInteger(rosterSize) || rosterSize<1 || rosterSize>30 || !Number.isInteger(minimumBid) || minimumBid<1 || minimumBid>20 ||
        !Number.isInteger(reservePerSlot) || reservePerSlot<minimumBid || reservePerSlot>200 || !Array.isArray(rosterSlots)) throw Error('Invalid roster or reserve settings.');
    if (rosterSlots.length && (rosterSlots.length!==rosterSize || rosterSlots.some(s=>!['PG','SG','SF','PF','C','G','F','UTIL','BN'].includes(s)))) throw Error('Enter exactly one valid position per roster slot, including bench slots as BN.');
    if (settings.gamesCap!==undefined && (!Number.isInteger(settings.gamesCap) || settings.gamesCap<1 || settings.gamesCap>3000)) throw Error('Invalid season games limit.');
  }
  function initial(session) {
    validateSettings(session.settings);
    if (!Array.isArray(session.players) || !session.players.length || session.players.length>2000 || new Set(session.players.map(p=>p.player_id)).size!==session.players.length || session.players.some(p=>typeof p.player_id!=='string' || typeof p.player!=='string' || !p.player_id || !p.player || !p.z || [...STATS,'fair_value','expected_auction_price'].some(k=>!Number.isFinite(p[k]) || p[k]<0) || CATS.some(k=>!Number.isFinite(p.z[k])))) throw Error('The saved player baseline is incomplete or invalid.');
    if (!Array.isArray(session.teams) || session.teams.some(t=>typeof t.name!=='string' || !t.name || !Number.isInteger(t.budget) || t.budget<1 || t.budget>10000)) throw Error('Invalid team budgets.');
    const byId=Object.fromEntries(session.players.map(p=>[p.player_id,p]));
    const teams=Object.fromEntries(session.teams.map(t=>[t.name,{name:t.name,budget:t.budget,roster:[],remaining:t.budget}]));
    if (Object.keys(teams).length!==session.teams.length || !session.teams.length) throw Error('Team identities must be unique.');
    const taken={};
    for (const k of session.keepers) {
      if (!teams[k.team] || !byId[k.playerId] || taken[k.playerId] || !Number.isInteger(k.amount) || k.amount<0) throw Error('Invalid keeper roster.');
      taken[k.playerId]=k;teams[k.team].roster.push(k);teams[k.team].remaining-=k.amount;
    }
    const s={session,byId,teams,taken,nomination:null,sales:[],bids:[],events:[],samples:[],revision:0};
    for (const team of Object.values(teams)) if (team.remaining<(session.settings.rosterSize-team.roster.length)*session.settings.minimumBid || team.roster.length>session.settings.rosterSize || !canFit(team.roster.map(r=>byId[r.playerId]),session.settings.rosterSlots)) throw Error('Roster settings conflict with the confirmed keepers.');
    s.initialRatio=budgetRatio(s,'expected_auction_price');
    return s;
  }
  function available(s) { return s.session.players.filter(p=>!s.taken[p.player_id]); }
  function legalMax(s,team) {
    const t=s.teams[team];if (!t) return 0;
    const open=s.session.settings.rosterSize-t.roster.length;
    return open>0 ? Math.max(0,t.remaining-(open-1)*s.session.settings.minimumBid) : 0;
  }
  function budgetRatio(s,field) {
    const {rosterSize,minimumBid}=s.session.settings;
    const slots=Object.values(s.teams).reduce((v,t)=>v+Math.max(0,rosterSize-t.roster.length),0);
    const cash=Object.values(s.teams).reduce((v,t)=>v+t.remaining,0);
    const pool=available(s).sort((a,b)=>b[field]-a[field]).slice(0,slots);
    const excess=pool.reduce((v,p)=>v+Math.max(0,p[field]-minimumBid),0);
    return excess>0 ? Math.max(0,cash-slots*minimumBid)/excess : 0;
  }
  function similarity(a,b) {
    const tier=Math.exp(-Math.pow(Math.log((a.expected_auction_price+5)/(b.expected_auction_price+5))/.7,2)/2);
    const dist=CATS.reduce((v,k)=>v+Math.pow(a.z[k]-b.z[k],2),0)/8;
    const overlap=positions(a).some(p=>positions(b).includes(p));
    return tier*(.35+.65*Math.exp(-dist/4))*(overlap?1:.75);
  }
  function market(s,p) {
    const pressure=s.initialRatio>0 ? clamp(budgetRatioCached(s)/s.initialRatio,0,4) : 1;
    let evidence=0,shift=0;
    for (const sample of s.samples) {
      const w=similarity(p,s.byId[sample.playerId]); evidence+=w;shift+=w*sample.residual;
    }
    const tier=Math.exp(shift/(6+evidence)); // Six comparable sales of prior weight; one auction is one observation.
    const max=Math.max(0,...Object.keys(s.teams).map(t=>legalMax(s,t)));
    const minimum=s.session.settings.minimumBid;
    const estimate=max<minimum?0:Math.min(max,Math.max(minimum,minimum+Math.max(0,p.expected_auction_price-minimum)*pressure*tier));
    return {expected:round(estimate),baseline:p.expected_auction_price,pressure:round(pressure),tierFactor:round(tier),evidence:round(evidence),
      confidence:evidence>=8?'Established sample':evidence>=3?'Building sample':'Early estimate'};
  }
  function budgetRatioCached(s) { if (s.marketRatio===undefined) s.marketRatio=budgetRatio(s,'expected_auction_price'); return s.marketRatio; }
  function eventShape(event,s) {
    if (!event || typeof event!=='object' || typeof event.id!=='string' || !event.id || event.id.length>200 || !['nominate','bid','sale','withdraw','undo'].includes(event.type)) throw Error('Invalid draft event.');
    if (event.type==='undo') { if (typeof event.targetId!=='string') throw Error('An undo must identify its event.');return; }
    if (!s.byId[event.playerId]) throw Error('Match this Yahoo player to a player in the projection list.');
    if (['bid','sale'].includes(event.type)) {
      if (!s.teams[event.team]) throw Error('Match this Yahoo team to a Survivor franchise.');
      if (!Number.isInteger(event.amount) || event.amount<s.session.settings.minimumBid || event.amount>10000) throw Error('A bid must be a whole dollar amount at or above the minimum.');
    }
  }
  function fold(s,event) {
    const {playerId,team,amount,type}=event;
    if (type==='nominate') {
      if (s.taken[playerId]) throw Error('This player is already rostered. Undo the sale first if Yahoo corrected it.');
      if (s.nomination && s.nomination.playerId!==playerId) throw Error('The previous nomination has no confirmed result. Record its sale or withdrawal before continuing.');
      if (s.nomination?.playerId!==playerId) s.nomination={playerId,amount:0,leader:null,observedAt:event.at};
    } else if (type==='bid') {
      s.bids.push(event);
      if (!s.taken[playerId] && !event.history) {
        if (!s.nomination || s.nomination.playerId!==playerId) throw Error('A bid arrived without a matching nomination.');
        if (amount>legalMax(s,team)) throw Error('The observed bid exceeds the tracked team budget. Reconcile missing sales before continuing.');
        if (amount>=s.nomination.amount) s.nomination={playerId,amount,leader:team,observedAt:event.at};
      }
    } else if (type==='sale') {
      if (s.taken[playerId]) throw Error('Conflicting sale for an already rostered player.');
      if (amount>legalMax(s,team)) throw Error('Sale exceeds the tracked budget or available roster slots.');
      if (!canFit([...s.teams[team].roster.map(r=>s.byId[r.playerId]),s.byId[playerId]],s.session.settings.rosterSlots)) throw Error('Sale conflicts with configured position slots. Check Yahoo roster settings.');
      const before=market(s,s.byId[playerId]);
      const sale={...event,expectedBefore:event.recovered?null:before.expected,baseline:s.byId[playerId].expected_auction_price};
      if (!event.recovered) s.samples.push({playerId,residual:clamp(Math.log((amount+4)/(before.expected+4)),-.7,.7)});
      s.sales.push(sale);s.taken[playerId]=sale;s.teams[team].roster.push(sale);s.teams[team].remaining-=amount;s.marketRatio=undefined;
      if (s.nomination?.playerId===playerId) s.nomination=null;
    } else if (type==='withdraw' && s.nomination?.playerId===playerId) s.nomination=null;
    s.events.push(event);s.revision++;
  }
  function replay(session) {
    if (session?.version!==1 || !Array.isArray(session.events) || session.events.length>25000 || !['live','practice'].includes(session.mode)) throw Error('Unsupported draft file.');
    const s=initial(session),ids=new Set(),undone=new Set();
    for (const event of session.events) {
      eventShape(event,s);
      if (ids.has(event.id)) throw Error('Duplicate event ID in draft file.');
      if (event.type==='undo') {
        const target=session.events.find(e=>e.id===event.targetId);
        if (!ids.has(event.targetId) || target?.type==='undo') throw Error('Undo must reference an earlier draft event.');
        undone.add(event.targetId);
      }
      ids.add(event.id);
    }
    for (const event of session.events) if (event.type!=='undo' && !undone.has(event.id)) fold(s,event);
    s.undone=[...undone];return s;
  }
  function append(session,event) {
    const duplicate=session.events.find(e=>e.id===event.id);
    if (duplicate) {
      for (const k of ['type','playerId','team','amount','targetId','history','recovered']) if (duplicate[k]!==event[k]) throw Error('An event ID was reused with different details.');
      return replay(session);
    }
    const trial={...session,events:[...session.events,event]};
    const result=replay(trial);session.events.push(event);result.session=session;return result;
  }
  function teamProfile(s,name) {
    const team=s.teams[name],roster=team.roster.map(r=>s.byId[r.playerId]);
    const z=Object.fromEntries(CATS.map(k=>[k,roster.reduce((n,p)=>n+p.z[k],0)/Math.max(1,roster.length)]));
    const fgA=roster.reduce((n,p)=>n+p.fga_pg*p.games,0),ftA=roster.reduce((n,p)=>n+p.fta_pg*p.games,0);
    return {z,fg:fgA?roster.reduce((n,p)=>n+p.fgm_pg*p.games,0)/fgA:null,ft:ftA?roster.reduce((n,p)=>n+p.ftm_pg*p.games,0)/ftA:null,
      needs:CATS.slice().sort((a,b)=>z[a]-z[b]).slice(0,3),open:s.session.settings.rosterSize-roster.length,roster};
  }
  function rawTotals(players) {
    const raw=Object.fromEntries(['games',...TOTAL_FIELDS].map(k=>[k,0]));
    for (const p of players) {raw.games+=p.games;for(const k of TOTAL_FIELDS)raw[k]+=p[k]*p.games;}
    return raw;
  }
  function projection(raw,cap=1000) {
    const use=raw.games>cap?cap/raw.games:1;
    const values=Object.fromEntries(CATS.map((k,i)=>[k,i<6?raw[COUNT[i]]*use:k==='FG%'?(raw.fga_pg?raw.fgm_pg/raw.fga_pg:null):(raw.fta_pg?raw.ftm_pg/raw.fta_pg:null)]));
    const averages=Object.fromEntries(CATS.map((k,i)=>[k,i<6?(raw.games?raw[COUNT[i]]/raw.games:null):values[k]]));
    return {values,averages,games:raw.games*use,rawGames:raw.games,capped:use<1};
  }
  // Fractional inclusion weights describe a pool average, never predicted individual picks.
  // At most one copy of each player, exactly the fillable slots, and expected spend <= cash.
  function portfolio(pool,slots,budget) {
    const cheapest=[...pool].sort((a,b)=>a.price-b.price || a.p.player_id.localeCompare(b.p.player_id));
    let fill=0,cost=0;
    while(fill<slots && fill<cheapest.length && cost+cheapest[fill].price<=budget+1e-8)cost+=cheapest[fill++].price;
    if(!fill)return {entries:[],unfilled:slots,cost:0};
    const fallback=()=>({entries:cheapest.slice(0,fill).map(q=>({...q,weight:1})),unfilled:slots-fill,cost});
    if(fill<slots || fill===pool.length || Math.abs(cost-budget)<1e-7)return fallback();
    const scale=Math.max(1,budget/slots);
    function weighted(penalty) {
      const logs=pool.map(q=>1.5*Math.log(1+q.p.fair_value)-penalty*q.price/scale),max=Math.max(...logs);
      const weights=logs.map(v=>Math.exp(Math.max(-60,v-max))),allocation=weights.map(()=>0);
      let remaining=slots,active=weights.map((_,i)=>i);
      while(active.length && remaining>1e-9) {
        const sum=active.reduce((n,i)=>n+weights[i],0),over=active.filter(i=>weights[i]*remaining/sum>=1);
        if(!over.length){for(const i of active)allocation[i]=weights[i]*remaining/sum;break;}
        for(const i of over)allocation[i]=1;
        remaining-=over.length;const full=new Set(over);active=active.filter(i=>!full.has(i));
      }
      return {entries:pool.map((q,i)=>({...q,weight:allocation[i]})),unfilled:0,cost:pool.reduce((n,q,i)=>n+q.price*allocation[i],0)};
    }
    let low=0,high=1,result=weighted(0);
    if(result.cost<=budget)return result;
    while(high<4096 && weighted(high).cost>budget)high*=2;
    result=weighted(high);if(result.cost>budget+1e-6)return fallback();
    for(let i=0;i<16;i++){const mid=(low+high)/2,trial=weighted(mid);if(trial.cost>budget)low=mid;else{high=mid;result=trial;}}
    return result;
  }
  function completedProfile(s,ctx,roster,cash,exclude=null) {
    const open=Math.max(0,s.session.settings.rosterSize-roster.length),raw=rawTotals(roster);
    const pool=open?ctx.pool.filter(q=>q.p.player_id!==exclude && canFit([...roster,q.p],s.session.settings.rosterSlots)):[];
    const fill=portfolio(pool,open,cash);
    for(const q of fill.entries){raw.games+=q.p.games*q.weight;for(const k of TOTAL_FIELDS)raw[k]+=q.p[k]*q.p.games*q.weight;}
    return {...projection(raw,s.session.settings.gamesCap??1000),unfilled:fill.unfilled,fillCost:fill.cost,open};
  }
  function outlookContext(s) {
    if(s.outlook)return s.outlook;
    // Bid ladders do not change rosters or forecasts. Reuse the expensive estimates until a sale/settings change.
    const signature=JSON.stringify([s.session.settings,Object.values(s.teams).map(t=>[t.name,t.remaining,t.roster]),s.sales.map(t=>[t.playerId,t.amount,t.recovered])]);
    const old=outlookCache.get(s.session);
    if(old?.signature===signature && old.players===s.session.players)return s.outlook=old.context;
    const ctx={pool:available(s).map(p=>({p,price:market(s,p).expected})),teams:{},scales:{},fits:new Map()};
    for(const t of Object.values(s.teams)) {
      const roster=t.roster.map(r=>s.byId[r.playerId]);
      ctx.teams[t.name]={owned:projection(rawTotals(roster),s.session.settings.gamesCap??1000),projected:completedProfile(s,ctx,roster,t.remaining),roster};
    }
    for(const k of CATS) {
      const values=Object.values(ctx.teams).map(t=>t.projected.values[k]).filter(v=>v!=null),mean=values.reduce((a,b)=>a+b,0)/Math.max(1,values.length);
      ctx.scales[k]=Math.max(Math.sqrt(values.reduce((n,v)=>n+(v-mean)**2,0)/Math.max(1,values.length)),k==='FG%'?.003:k==='FT%'?.004:Math.max(1,mean*.06));
    }
    s.outlook=ctx;outlookCache.set(s.session,{signature,players:s.session.players,context:ctx});return ctx;
  }
  function categoryStanding(ctx,name,k,value) {
    const others=Object.entries(ctx.teams).filter(([n])=>n!==name).map(([,t])=>t.projected.values[k]).filter(v=>v!=null);
    if(value==null || !others.length)return {rank:null,points:null,gap:null,status:'No comparison',priority:false};
    const scale=ctx.scales[k],epsilon=k.includes('%')?1e-7:1e-5,above=others.filter(v=>v>value+epsilon),rank=1+above.length;
    const tiedCount=others.filter(v=>Math.abs(v-value)<=epsilon).length,tied=tiedCount>0,count=others.length+1,midRank=rank+tiedCount/2;
    const priority=midRank>Math.ceil(count*2/3),lead=value-Math.max(...others);
    const status=priority?'Needs attention':rank===1&&!tied&&lead>scale*.5?'Comfortable lead':midRank<=Math.ceil(count/3)?'Strong':'Competitive';
    const points=1+others.reduce((n,v)=>n+1/(1+Math.exp(clamp((v-value)/(scale*.4),-30,30))),0);
    return {rank,tied,points,gap:above.length?Math.min(...above)-value:0,status,priority};
  }
  function outlook(s,name,p=null) {
    const ctx=outlookContext(s),team=ctx.teams[name],f=p?fit(s,p,name):null;
    const categories=CATS.map(k=>({category:k,owned:team.owned.values[k],projected:team.projected.values[k],...categoryStanding(ctx,name,k,team.projected.values[k]),
      after:f?.impact[k]?.after??null,afterRank:f?.impact[k]?.afterRank??null,change:f?.impact[k]?.change??null}));
    return {categories,owned:team.owned,projected:team.projected,teamCount:s.session.teams.length,gamesCap:s.session.settings.gamesCap??1000,
      provisional:Object.values(ctx.teams).some(t=>t.projected.open>0),unfilled:Object.values(ctx.teams).some(t=>t.projected.unfilled>0),price:f?.scenarioPrice??null,
      canAdd:Boolean(f?.positionFit&&legalMax(s,name)>0),afterUnfilled:f?.afterUnfilled??0};
  }
  function fit(s,p,name) {
    const ctx=outlookContext(s),cacheKey=name+':'+p.player_id;
    if(ctx.fits.has(cacheKey))return ctx.fits.get(cacheKey);
    const t=s.teams[name],profile=ctx.teams[name],{minimumBid,reservePerSlot,rosterSlots}=s.session.settings;
    const open=s.session.settings.rosterSize-profile.roster.length,positionFit=open>0&&canFit([...profile.roster,p],rosterSlots);
    const scenarioPrice=Math.min(legalMax(s,name),Math.max(minimumBid,Math.round(market(s,p).expected)));
    const after=positionFit?completedProfile(s,ctx,[...profile.roster,p],t.remaining-scenarioPrice,p.player_id):profile.projected;
    const ownedAfter=positionFit?projection(rawTotals([...profile.roster,p]),s.session.settings.gamesCap??1000):profile.owned;
    let gain=0,needGain=0;const impact={};
    for(const k of CATS) {
      const before=categoryStanding(ctx,name,k,profile.projected.values[k]),next=categoryStanding(ctx,name,k,after.values[k]);
      const rawChange=after.values[k]==null||profile.projected.values[k]==null?0:after.values[k]-profile.projected.values[k];
      const change=Math.abs(rawChange)<ctx.scales[k]*1e-10?0:rawChange;
      const points=next.points==null||before.points==null?0:next.points-before.points;
      gain+=points;if(before.priority)needGain+=clamp(change/ctx.scales[k],-2,2);
      impact[k]={before:profile.projected.values[k],after:after.values[k],change,points,priority:before.priority,rank:before.rank,afterRank:next.rank,
        avgBefore:profile.owned.averages[k],avgAfter:ownedAfter.averages[k]};
    }
    const factor=clamp(1+gain*.045+needGain*.07,.75,1.3);
    if (s.neutralRatio===undefined) s.neutralRatio=budgetRatio(s,'fair_value');
    const value=p.fair_value>=minimumBid ? minimumBid+(p.fair_value-minimumBid)*s.neutralRatio*factor : 0;
    const reserve=Math.max(0,open-1)*reservePerSlot;
    const cap=positionFit?Math.max(0,Math.floor(Math.min(value,legalMax(s,name),t.remaining-reserve))):0;
    const helps=CATS.filter(k=>impact[k].change>ctx.scales[k]*.04).sort((a,b)=>(Number(impact[b].priority)-Number(impact[a].priority))||impact[b].points-impact[a].points).slice(0,3);
    const costs=CATS.filter(k=>impact[k].change<-ctx.scales[k]*.04).sort((a,b)=>impact[a].points-impact[b].points).slice(0,2);
    const needs=CATS.slice().sort((a,b)=>(categoryStanding(ctx,name,b,profile.projected.values[b]).rank||0)-(categoryStanding(ctx,name,a,profile.projected.values[a]).rank||0)).slice(0,3);
    const priorities=helps.filter(k=>impact[k].priority);
    const fitLabel=!positionFit?'No roster slot':gain>1 || priorities.length&&gain>-.3?'Strong fit':gain>.3?'Good fit':helps.length?'Trade-off':'Limited fit';
    const explanation=!positionFit?'No eligible open roster slot.':(priorities.length?'Addresses your need in '+priorities.join(', '):helps.length?'Improves '+helps.join(', '):'Little improvement over your budget-matched remaining options')+(costs.length?'; gives ground in '+costs.join(', '):'')+'.';
    const result={cap,fitFactor:round(factor),fitLabel,helps,costs,needs,reserve,positionFit,impact,scenarioPrice,explanation,standingsGain:gain,afterUnfilled:after.unfilled,
      fgBefore:profile.owned.averages['FG%'],ftBefore:profile.owned.averages['FT%'],fgAfter:ownedAfter.averages['FG%'],ftAfter:ownedAfter.averages['FT%']};
    ctx.fits.set(cacheKey,result);return result;
  }
  function board(s,name) {
    if (!s.teams[name]) name=Object.keys(s.teams)[0];
    const rows=available(s).map(p=>({...p,market:market(s,p),fit:fit(s,p,name)}));
    rows.sort((a,b)=>b.fair_value-a.fair_value || a.player.localeCompare(b.player));
    const current=rows.find(p=>p.player_id===s.nomination?.playerId) || null;
    const next=s.nomination ? Math.max(s.session.settings.minimumBid,s.nomination.amount+1) : null;
    const teams=Object.values(s.teams).map(t=>{
      const prof=teamProfile(s,t.name),legal=legalMax(s,t.name),f=current?fit(s,current,t.name):null;
      const bids=current?s.bids.filter(b=>b.playerId===current.player_id && b.team===t.name):[];
      let interest='Waiting',reason='No active nomination.';
      if (current) {
        if (s.nomination.leader===t.name) {interest='Leading';reason='Currently holds the highest observed bid.';}
        else if (legal<next || !f.positionFit) {interest='Cannot bid';reason=legal<next?'Tracked budget cannot cover the next bid.':'No eligible roster slot under configured settings.';}
        else if (bids.length) {interest='Has bid';reason='Observed bidding on this player; their maximum is unknown.';}
        else if (f.cap>=next && f.cap>=current.market.expected*.85) {interest='Likely';reason='Price is within estimated team value and available budget.';}
        else if (f.cap>=next*.8) {interest='Possible';reason='Affordable, but price is near estimated team value.';}
        else {interest='Unlikely';reason='Next bid is above estimated team value. They may still bid.';}
      }
      const needs=f?f.needs:outlook(s,t.name).categories.slice().sort((a,b)=>(b.rank||0)-(a.rank||0)).slice(0,3).map(c=>c.category);
      return {...t,open:prof.open,needs,legalMax:legal,interest,reason,highestObserved:bids.length?Math.max(...bids.map(b=>b.amount)):null,fg:prof.fg,ft:prof.ft};
    });
    const decision=!current?'WAIT':s.nomination.leader===name?'HOLD':next<=current.fit.cap?'BID':'PASS';
    const alternatives=current?rows.filter(p=>p.player_id!==current.player_id && p.fit.cap>0 && (positions(current).some(pos=>positions(p).includes(pos)) || current.fit.helps.some(c=>p.z[c]>1)))
      .sort((a,b)=>(b.fit.cap-b.market.expected)-(a.fit.cap-a.market.expected)).slice(0,3):[];
    return {rows,current,next,teams,decision,alternatives,outlook:outlook(s,name,current),selectedTeam:name,market:{sales:s.sales.length,remaining:teams.reduce((v,t)=>v+t.remaining,0),slots:teams.reduce((v,t)=>v+t.open,0),pressure:s.initialRatio?round(budgetRatioCached(s)/s.initialRatio):1}};
  }
  function resolvePlayer(session,name) {
    const matches=session.players.filter(p=>key(p.player)===key(name) || p.player_id===name);
    return matches.length===1?matches[0].player_id:null;
  }
  return {CATS,STATS,key,create,replay,append,board,market,fit,outlook,projection,rawTotals,legalMax,canFit,resolvePlayer,validateSettings};
});
