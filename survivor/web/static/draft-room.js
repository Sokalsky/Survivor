/* Live draft workspace. Private sessions stay in this browser; saved forecasts remain read-only. */
(function () {
  'use strict';
  const E=window.SurvivorDraft, STORE='survivor.draft.v1';
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const dollar=v=>v==null?'—':'$'+Number(v).toLocaleString('en-US',{maximumFractionDigits:1});
  const num=(v,n=1)=>v==null?'—':Number(v).toFixed(n);
  const uid=()=>crypto.randomUUID();
  let root,boot,room,session,snapshot,view,nonce,paired=false,lastHeartbeat=0,watcherInfo={},timer,playing=false,saveStamp='',search='',position='',sort='fair_value',tab='available',onlyStars=false,limit=60,manual=false,lastRenderedStatus='';
  const active=()=>!!root?.isConnected && location.hash.replace('#','')!=='history' && document.body.classList.contains('draft-page');
  const status=()=>room?.mode==='practice'?'Practice':manual?'Manual entry':lastHeartbeat && Date.now()-lastHeartbeat<6000?'Watching Yahoo':paired?'Watcher paused':'Not connected';
  function readRoom() { const text=localStorage.getItem(STORE);saveStamp=text||'';return text?JSON.parse(text):null; }
  function persist() {
    if ((localStorage.getItem(STORE)||'')!==saveStamp) throw Error('This draft changed in another tab. Reload this page before editing.');
    const text=JSON.stringify(room);localStorage.setItem(STORE,text);saveStamp=text;
  }
  function selectSession() { session=room.sessions[room.mode];snapshot=E.replay(session);view=E.board(snapshot,room.team); }
  function event(type,fields={},source='manual') { return {id:uid(),type,...fields,source,at:new Date().toISOString()}; }
  function add(ev,target=session) { E.append(target,ev);if(target===room.sessions.live&&room.pending.length)processPending();persist();if(target===session){selectSession();renderLive();} }
  function error(message) { const box=document.getElementById('draft-error');if(box){box.textContent=message;box.hidden=false;} }
  function banner() {
    const pending=room.pending.length;
    return '<div class="draft-statusbar"><div><span class="draft-light '+(status()==='Watching Yahoo'?'on':'')+'"></span><strong id="draft-source">'+esc(status())+'</strong><span>'+esc(session.season)+' · '+session.teams.length+' teams · Salary cap</span></div><span id="draft-observation">'+(room.mode==='practice'?'SIMULATED BIDS & SALES':pending?pending+' updates need review':watcherInfo.partial?'Observed since connection · earlier bids may be missing':'Draft activity stays in this browser')+'</span></div>';
  }
  function shell() {
    root.innerHTML='<div class="draft-room">'+banner()+'<div class="draft-toolbar"><label>Your team <select id="draft-team">'+session.teams.map(t=>'<option '+(t.name===room.team?'selected':'')+'>'+esc(t.name)+'</option>').join('')+'</select></label><button class="draft-button" data-draft="setup">Connect Yahoo</button><button class="draft-button '+(room.mode==='practice'?'selected':'')+'" data-draft="mode">'+(room.mode==='practice'?'Return to live draft':'Try practice draft')+'</button><button class="draft-button quiet" data-draft="export">Export session</button><label class="draft-button quiet file-label">Import session<input id="draft-import" type="file" accept="application/json,.json" hidden></label><button class="draft-button quiet" data-draft="settings">Roster & reserve</button></div><div id="draft-error" class="draft-alert" role="alert" hidden></div><div id="draft-warning"></div><div class="draft-layout"><div class="draft-main"><div id="draft-nomination" aria-live="polite"></div><div id="draft-controls"></div><section class="draft-panel draft-board"><div class="draft-tabs" role="tablist">'+[['available','Available players'],['roster','My roster'],['sales','Draft results'],['bids','Observed bids']].map(([id,title])=>'<button role="tab" aria-selected="'+(tab===id)+'" class="'+(tab===id?'selected':'')+'" data-draft-tab="'+id+'">'+title+'</button>').join('')+'</div><div class="draft-filters"><input id="draft-search" type="search" placeholder="Find a player…" aria-label="Find a draft player" value="'+esc(search)+'"><select id="draft-position" aria-label="Position">'+['','PG','SG','SF','PF','C','G','F'].map(p=>'<option value="'+p+'" '+(position===p?'selected':'')+'>'+(p||'All positions')+'</option>').join('')+'</select><select id="draft-sort" aria-label="Draft board sort">'+[['fair_value','Neutral value'],['cap','Your bid cap'],['edge','Value vs. price'],['expected','Live price'],['player','Player name']].map(([k,t])=>'<option value="'+k+'" '+(sort===k?'selected':'')+'>'+t+'</option>').join('')+'</select><button class="draft-button quiet" data-draft="stars" aria-pressed="'+onlyStars+'">'+(onlyStars?'★ Watchlist':'☆ Watchlist')+'</button></div><div id="draft-table"></div></section><p class="draft-baseline">Frozen forecast: '+esc(session.baselineRunId.slice(0,12))+' · Session started '+esc(new Date(session.createdAt).toLocaleDateString())+'<button class="draft-button quiet" data-draft="new-live">New live session</button></p><details class="draft-method"><summary>How live advice works</summary><p>Saved projections and neutral values stay fixed. Expected prices respond to money and talent left, plus completed sales of similar players. A six-sale prior keeps early results from dominating. Individual bids show interest; only a completed sale trains the price adjustment.</p><p>Your cap uses neutral value, remaining league money, eight-category fit and your reserve. It is a working heuristic, not a tested optimal strategy. Opponent interest is an estimate unless a bid was observed. Shooting fit combines projected makes and attempts over games, not averages of percentages.</p><p>Position eligibility comes from the projection source; confirm Yahoo’s slots and any differences. Connection gaps can miss bids. A selected status or completed-results row must explicitly confirm a sale.</p></details></div><aside class="draft-opponents"><div id="draft-wallet"></div><section class="draft-panel"><div class="draft-panel-heading"><h2>The room</h2><span>Cash / max bid</span></div><p class="draft-hint">Interest in the nominated player</p><div id="draft-teams"></div></section></aside></div><dialog id="draft-dialog" class="draft-dialog"><div class="draft-dialog-head"><h2 id="draft-dialog-title"></h2><button class="draft-button quiet" data-draft="close-dialog" aria-label="Close draft setup">✕</button></div><div id="draft-dialog-body"></div></dialog></div>';
    renderLive();
  }
  function statLine(p) {
    return [['PTS',p.pts_pg],['REB',p.reb_pg],['AST',p.ast_pg],['STL',p.stl_pg],['BLK',p.blk_pg],['3PM',p.fg3m_pg],['FG%',p.fga_pg?p.fgm_pg/p.fga_pg*100:null],['FT%',p.fta_pg?p.ftm_pg/p.fta_pg*100:null]].map(([k,v])=>'<div><small>'+k+'</small><strong>'+num(v)+(k.includes('%')&&v!=null?'%':'')+'</strong></div>').join('');
  }
  function renderLive() {
    if(!active())return;
    lastRenderedStatus=status();
    const me=view.teams.find(t=>t.name===view.selectedTeam),p=view.current;
    const pending=room.mode==='live'&&room.pending.length;
    const stale=room.mode==='live'&&!manual&&status()!=='Watching Yahoo';
    const decision=p&&(pending||stale)?'CHECK SYNC':view.decision;
    document.getElementById('draft-warning').innerHTML=room.mode==='practice'?'<div class="draft-notice practice"><strong>Practice draft</strong> · Real projections, simulated activity. Your live session is separate.<button data-draft="reset-practice">Restart practice</button></div>':pending?'<div class="draft-notice warn"><strong>'+room.pending.length+' updates need review.</strong> Advice is paused until identities or conflicting results are reconciled.<button data-draft="review">Review updates</button></div>':!session.settings.rosterSlots.length?'<div class="draft-notice">Position slots are not confirmed. Category and budget advice is available.<button data-draft="settings">Set Yahoo roster slots</button></div>':'';
    document.getElementById('draft-wallet').innerHTML='<section class="draft-wallet"><small>'+esc(me.name.toUpperCase())+'’S DRAFT</small><div><strong>'+dollar(me.remaining)+'</strong><span>'+me.open+' slots left<br>Legal max '+dollar(me.legalMax)+'</span></div><div class="draft-wallet-foot"><span>Reserve per other slot</span><b>'+dollar(session.settings.reservePerSlot)+'</b></div></section>';
    document.getElementById('draft-nomination').innerHTML=p?'<section class="nomination"><div class="nomination-heading"><div><span class="draft-eyebrow">ON THE CLOCK</span><h2><button data-player="'+esc(p.player_id)+'">'+esc(p.player)+'</button></h2><p>'+esc(p.nba_team)+' · '+esc(p.positions.replaceAll(',',' / '))+' <span>Projected '+num(p.games,0)+' GP</span></p></div><div class="draft-clock">'+(watcherInfo.timer && room.mode==='live'?esc(watcherInfo.timer):'LIVE BOARD')+'<small>'+(room.mode==='practice'?'Practice nomination':'Yahoo remains the bid control')+'</small></div></div><div class="nomination-stats">'+statLine(p)+'</div><div class="draft-bidline"><div><small>CURRENT BID</small><strong>'+dollar(snapshot.nomination.amount)+'</strong><span>'+esc(snapshot.nomination.leader || 'Awaiting first bid')+'</span></div><div><small>LIVE EXPECTED PRICE</small><strong>'+dollar(p.market.expected)+'</strong><span>Saved estimate '+dollar(p.market.baseline)+'</span></div><div><small>NEUTRAL VALUE</small><strong>'+dollar(p.fair_value)+'</strong><span>Saved production value</span></div></div><div class="draft-advice '+(decision==='BID'?'bid':decision==='PASS'?'pass':'hold')+'"><div><span class="draft-decision">'+decision+'</span><span class="draft-advice-sub">'+(decision==='BID'?'Next bid '+dollar(view.next):decision==='HOLD'?'You hold the observed lead':decision==='PASS'?'Next bid exceeds your cap':pending?'Resolve pending updates first':'Reconnect or use manual entry')+'</span></div><div><small>YOUR BID CAP</small><strong>'+dollar(p.fit.cap)+'</strong></div></div><div class="draft-fit"><strong>'+esc(p.fit.fitLabel)+'</strong><span>'+(p.fit.helps.length?'Adds '+p.fit.helps.join(', '):'Limited positive category impact')+(p.fit.costs.length?' · Watch '+p.fit.costs.join(', '):'')+' · '+dollar(p.fit.reserve)+' reserved for remaining slots</span><p>Projected team FG% '+num(p.fit.fgBefore==null?null:p.fit.fgBefore*100)+' → '+num(p.fit.fgAfter==null?null:p.fit.fgAfter*100)+' · FT% '+num(p.fit.ftBefore==null?null:p.fit.ftBefore*100)+' → '+num(p.fit.ftAfter==null?null:p.fit.ftAfter*100)+'</p>'+(view.alternatives.length?'<div class="draft-alternatives"><small>OTHER OPTIONS</small>'+view.alternatives.map(a=>'<button data-player="'+esc(a.player_id)+'">'+esc(a.player)+' <span>'+dollar(a.market.expected)+'</span></button>').join('')+'</div>':'')+'</div></section>':'<section class="draft-empty"><span class="draft-eyebrow">YOUR DRAFT DESK</span><h2>Ready when the room is.</h2><p>Connect the Yahoo watcher to see the nomination, every observed bid, and your next move here.</p><div><button class="draft-button primary" data-draft="setup">Connect Yahoo</button><button class="draft-button" data-draft="mode">'+(room.mode==='practice'?'Return to live draft':'Explore a practice draft')+'</button></div><div class="draft-empty-stats"><span><b>'+view.rows.length+'</b> available players</span><span><b>'+dollar(view.market.remaining)+'</b> left in the room</span><span><b>'+view.market.sales+'</b> completed sales</span></div></section>';
    document.getElementById('draft-controls').innerHTML='<div class="draft-controls"><span>'+view.market.sales+' sales · '+view.market.slots+' open slots · Budget pressure '+num(view.market.pressure,2)+'×'+(p?' · '+p.market.confidence:'')+'</span><div>'+(room.mode==='practice'?'<button class="draft-button quiet" data-draft="play">'+(playing?'Pause practice':'Play practice')+'</button><button class="draft-button quiet" data-draft="next">Next event</button>':'<button class="draft-button quiet" data-draft="manual">'+(manual?'Exit manual entry':'Manual entry')+'</button>')+'<button class="draft-button quiet" data-draft="record">Record / correct</button></div></div>';
    document.getElementById('draft-teams').innerHTML=view.teams.map(t=>'<article class="draft-team '+(t.name===me.name?'mine ':'')+(t.interest==='Leading'?'leading':'')+'"><div class="draft-team-top"><strong>'+esc(t.name)+(t.name===me.name?' <small>YOU</small>':'')+'</strong><span>'+dollar(t.remaining)+' <small>/ '+dollar(t.legalMax)+'</small></span></div><div class="draft-team-middle"><span>'+t.open+' slots · Needs '+t.needs.join(' / ')+'</span><b class="interest '+t.interest.toLowerCase().replaceAll(' ','-')+'" title="'+esc(t.reason)+'">'+t.interest+'</b></div>'+(t.highestObserved!=null?'<small class="draft-observed">Observed bid '+dollar(t.highestObserved)+'</small>':'')+'</article>').join('');
    renderTable();refreshStatus();
  }
  function renderTable() {
    const target=document.getElementById('draft-table');if(!target)return;
    const previousScroll=target.querySelector('.draft-table-scroll'),scrollTop=previousScroll?.scrollTop||0,scrollLeft=previousScroll?.scrollLeft||0;
    document.querySelectorAll('[data-draft-tab]').forEach(b=>{b.classList.toggle('selected',b.dataset.draftTab===tab);b.setAttribute('aria-selected',String(b.dataset.draftTab===tab));});
    let rows=tab==='available'?view.rows:tab==='roster'?view.teams.find(t=>t.name===view.selectedTeam).roster.map(r=>({...snapshot.byId[r.playerId],purchase:r})):tab==='sales'?[...snapshot.sales].reverse().map(r=>({...snapshot.byId[r.playerId],purchase:r})):[...snapshot.bids].reverse().map(r=>({...snapshot.byId[r.playerId],purchase:r}));
    rows=rows.filter(p=>(!search||E.key(p.player).includes(E.key(search)))&&(!position||String(p.positions).includes(position))&&(!onlyStars||room.stars.includes(p.player_id)));
    if(tab==='available')rows.sort((a,b)=>sort==='player'?a.player.localeCompare(b.player):sort==='cap'?b.fit.cap-a.fit.cap:sort==='expected'?b.market.expected-a.market.expected:sort==='edge'?(b.fit.cap-b.market.expected)-(a.fit.cap-a.market.expected):b.fair_value-a.fair_value);
    const headers=tab==='available'?'<th>Live price</th><th>Your cap</th><th>Neutral</th><th>Team fit</th><th>PTS</th><th>REB</th><th>AST</th><th>STL</th><th>BLK</th><th>3PM</th><th>FG%</th><th>FT%</th>':'<th>Team</th><th>'+ (tab==='bids'?'Bid':'Paid')+'</th><th>Type</th><th>Expected before sale</th>';
    target.innerHTML=(tab==='bids'?'<p class="draft-hint">Observed bids only. A bid is evidence of interest, not a team’s hidden maximum. Connection gaps can leave missing bids.</p>':'')+'<div class="draft-table-scroll"><table class="draft-player-table"><thead><tr><th><span class="sr-only">Watchlist</span>☆</th><th>Player</th>'+headers+(tab==='available'&&(room.mode==='practice'||manual)?'<th>Action</th>':'')+'</tr></thead><tbody>'+rows.slice(0,limit).map(p=>'<tr class="'+(p.player_id===view.current?.player_id?'nominated':'')+'"><td><button class="star-button" data-draft-star="'+esc(p.player_id)+'" aria-label="'+(room.stars.includes(p.player_id)?'Remove from':'Add to')+' watchlist" aria-pressed="'+room.stars.includes(p.player_id)+'">'+(room.stars.includes(p.player_id)?'★':'☆')+'</button></td><td><button class="draft-player" data-player="'+esc(p.player_id)+'">'+esc(p.player)+'<small>'+esc(p.positions)+' · '+esc(p.nba_team)+'</small></button></td>'+(tab==='available'?'<td title="Saved estimate '+dollar(p.market.baseline)+'">'+dollar(p.market.expected)+'</td><td class="draft-cap '+(p.fit.cap>=p.market.expected?'value':'')+'">'+dollar(p.fit.cap)+'</td><td>'+dollar(p.fair_value)+'</td><td><span class="draft-fit-tag">'+esc(p.fit.fitLabel)+'</span></td>'+['pts_pg','reb_pg','ast_pg','stl_pg','blk_pg','fg3m_pg'].map(k=>'<td>'+num(p[k])+'</td>').join('')+'<td>'+num(p.fga_pg?p.fgm_pg/p.fga_pg*100:null)+'%</td><td>'+num(p.fta_pg?p.ftm_pg/p.fta_pg*100:null)+'%</td>'+(room.mode==='practice'||manual?'<td><button class="draft-button quiet" data-draft-nominate="'+esc(p.player_id)+'">Nominate</button></td>':''):'<td>'+esc(p.purchase.team)+'</td><td>'+dollar(p.purchase.amount)+'</td><td>'+esc(p.purchase.keeper?'Keeper':p.purchase.source || 'Observed')+'</td><td>'+dollar(p.purchase.expectedBefore)+'</td>')+'</tr>').join('')+'</tbody></table></div>'+(rows.length?'':'<div class="no-results">No players in this view.</div>')+'<div class="draft-table-foot"><span>'+Math.min(limit,rows.length)+' of '+rows.length+' '+(tab==='bids'?'observed bids':'players')+'</span>'+(rows.length>limit?'<button class="draft-button quiet" data-draft="more">Show more</button>':'')+'<span>Live estimates · projections per game</span></div>';
    const scroller=target.querySelector('.draft-table-scroll');if(scroller){scroller.scrollTop=scrollTop;scroller.scrollLeft=scrollLeft;}
  }
  function dialog(title,body) {document.getElementById('draft-dialog-title').textContent=title;document.getElementById('draft-dialog-body').innerHTML=body;const d=document.getElementById('draft-dialog');if(!d.open)d.showModal();}
  function setup() {dialog('Connect your Yahoo draft','<p>Keep this page and Yahoo’s draft room open in the same Chrome profile. The extension reads the fields you select and sends updates directly to this tab.</p><ol><li><a class="draft-link" href="/static/yahoo-draft-watcher.zip" download>Download the Chrome watcher</a>, unzip it, open <code>chrome://extensions</code>, enable Developer mode, and choose <strong>Load unpacked</strong>.</li><li>On this page, open the extension and click <strong>Pair Survivor tab</strong>.</li><li>On Yahoo, open the extension and click <strong>Watch Yahoo tab</strong>. Use its field pickers to identify the current player, bid, bidder, and explicit sold status or completed-results rows.</li><li>Match any unfamiliar Yahoo team or player names here, then check the first nomination, bids and completed sale against Yahoo.</li></ol><div class="draft-notice">The Yahoo adapter is configurable because this draft room’s current markup has not been inspected. “Watching” requires a calibrated player, bid, bidder and sale confirmation. No bids are placed by the extension.</div><p><b>Status:</b> '+esc(status())+'. '+esc(watcherInfo.message || '')+'</p><p>Start before the first nomination. Visible bid history can help recover missed rows, but every intermediate bid cannot be guaranteed after a gap. Sessions and exports stay on your computer; they are not shared with other visitors.</p>');}
  function settings() {dialog('Roster and budget settings','<p>Match your Yahoo draft settings. Position slots are optional until confirmed. Include bench as BN; exclude injured-list slots that cannot be drafted.</p><form id="draft-settings-form"><label>Total draft roster size<input name="rosterSize" type="number" min="1" max="30" required value="'+session.settings.rosterSize+'"></label><label>Minimum bid<input name="minimumBid" type="number" min="1" max="20" required value="'+session.settings.minimumBid+'"></label><label>Reserve for each other open slot<input name="reservePerSlot" type="number" min="1" max="200" required value="'+session.settings.reservePerSlot+'"></label><label>Yahoo position slots, separated by commas<input name="rosterSlots" placeholder="PG, SG, G, SF, PF, F, C, C, UTIL, UTIL, BN, BN, BN, BN, BN" value="'+esc(session.settings.rosterSlots.join(', '))+'"></label><p class="draft-hint">Example only. Empty means position legality is not enforced. The recommendation still reserves money and uses all eight categories.</p><button class="draft-button primary" type="submit">Save settings</button></form>');}
  function record() {
    dialog('Record or correct draft activity','<p>Use this for manual tracking or an explicit Yahoo correction. Your saved event history is preserved.</p><form id="draft-record-form"><label>Event<select name="type"><option value="sale">Completed sale</option><option value="bid">Observed bid</option><option value="nominate">Nomination</option><option value="withdraw">Withdraw nomination</option></select></label><label>Player<select name="playerId">'+session.players.filter(p=>!snapshot.taken[p.player_id]).sort((a,b)=>a.player.localeCompare(b.player)).map(p=>'<option value="'+esc(p.player_id)+'" '+(p.player_id===snapshot.nomination?.playerId?'selected':'')+'>'+esc(p.player)+'</option>').join('')+'</select></label><label>Team<select name="team">'+session.teams.map(t=>'<option '+(t.name===snapshot.nomination?.leader?'selected':'')+'>'+esc(t.name)+'</option>').join('')+'</select></label><label>Amount<input name="amount" type="number" min="1" step="1" value="'+(snapshot.nomination?.amount||1)+'" required></label><button class="draft-button primary" type="submit">Record event</button></form><hr><label>Undo a recorded event<select id="draft-undo-target">'+session.events.filter(e=>e.type!=='undo'&&!snapshot.undone.includes(e.id)).slice(-100).reverse().map(e=>'<option value="'+esc(e.id)+'">'+esc(e.type+' · '+snapshot.byId[e.playerId]?.player+' · '+(e.team||'')+' '+(e.amount||''))+'</option>').join('')+'</select></label><p class="draft-hint">Undo dependent bids or sales first if they prevent replay. A corrected sale can then be recorded above.</p><button class="draft-button" data-draft="undo">Undo selected event</button>');
  }
  function practice() {
    const live=room.sessions.live;
    const s={...JSON.parse(JSON.stringify(live)),id:uid(),mode:'practice',events:[]};
    for(let i=0;i<4;i++) {
      const st=E.replay(s),b=E.board(st,room.team),p=b.rows[i],t=b.teams.filter(t=>t.name!==room.team&&t.legalMax>10)[i];
      if(!p||!t)break;
      const amount=Math.max(1,Math.min(t.legalMax,Math.round(p.market.expected*[.8,1.18,.9,1.08][i])));
      E.append(s,event('sale',{playerId:p.player_id,team:t.name,amount},'practice'));
    }
    room.sessions.practice=s;room.mode='practice';persist();selectSession();step();
  }
  function step() {
    if(room.mode!=='practice')return;
    if(!snapshot.nomination) {
      const candidate=view.rows.find(p=>p.fit.cap>10)||view.rows[0];if(candidate)add(event('nominate',{playerId:candidate.player_id},'practice'));
    } else {
      const count=snapshot.bids.filter(b=>b.playerId===view.current.player_id).length;
      if(count>=5&&snapshot.nomination.leader) {add(event('sale',{playerId:view.current.player_id,team:snapshot.nomination.leader,amount:snapshot.nomination.amount},'practice'));return;}
      const eligible=view.teams.filter(t=>t.name!==snapshot.nomination.leader&&t.legalMax>=view.next);
      if(!eligible.length) {if(snapshot.nomination.leader)add(event('sale',{playerId:view.current.player_id,team:snapshot.nomination.leader,amount:snapshot.nomination.amount},'practice'));return;}
      const t=eligible[count%eligible.length],amount=Math.min(t.legalMax,Math.max(view.next,Math.floor(view.current.market.expected*(.48+count*.09))));
      add(event('bid',{playerId:view.current.player_id,team:t.name,amount},'practice'));
    }
  }
  function download() {const blob=new Blob([JSON.stringify(session,null,2)],{type:'application/json'}),url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download='survivor-'+session.season+'-'+session.mode+'-draft.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
  function review() {
    const e=room.pending[0];if(!e)return;
    const s=room.sessions.live;
    dialog('Reconcile Yahoo update','<p><b>'+esc(e.type)+'</b> · '+esc(e.player)+' · '+esc(e.team)+' '+dollar(e.amount)+'</p><p>'+esc(e.problem || 'Match the captured names to your league.')+'</p><form id="draft-map-form"><label>Player<select name="playerId"><option value="">Select matching player</option>'+s.players.slice().sort((a,b)=>a.player.localeCompare(b.player)).map(p=>'<option value="'+esc(p.player_id)+'" '+(E.resolvePlayer(s,e.player)===p.player_id?'selected':'')+'>'+esc(p.player)+'</option>').join('')+'</select></label><label>Yahoo team → Survivor franchise<select name="team"><option value="">Select franchise</option>'+s.teams.map(t=>'<option '+(t.name===e.team?'selected':'')+'>'+esc(t.name)+'</option>').join('')+'</select></label><button class="draft-button primary" type="submit">Save mapping & retry</button></form><hr><p class="draft-hint">If this is an incorrect capture, ignore it explicitly, then correct the watcher’s selected fields. Ignored updates remain in this browser’s review log.</p><button class="draft-button" data-draft="ignore">Ignore this captured update</button>');
  }
  function normalize(raw) {
    const s=room.sessions.live,playerId=room.playerMap[E.key(raw.player)]||E.resolvePlayer(s,raw.player),team=room.teamMap[raw.team] || s.teams.find(t=>E.key(t.name)===E.key(raw.team))?.name;
    return {id:raw.id,type:raw.type,playerId,team,amount:raw.amount,at:raw.at,history:!!raw.history,recovered:!!raw.recovered,source:'yahoo-observed'};
  }
  function processPending() {
    const s=room.sessions.live;
    while(room.pending.length) {
      const raw=room.pending[0],ev=normalize(raw);
      try {
        const st=E.replay(s);
        if(ev.type==='nominate'&&st.nomination&&ev.playerId!==st.nomination.playerId){
          const resultIndex=room.pending.findIndex((candidate,index)=>index>0&&candidate.type==='sale'&&normalize(candidate).playerId===st.nomination.playerId);
          if(resultIndex>0){room.pending.unshift(room.pending.splice(resultIndex,1)[0]);continue;}
        }
        if(ev.type==='nominate'&&st.taken[ev.playerId]){room.pending.shift();continue;}
        if(ev.type==='sale'&&st.taken[ev.playerId]&&st.taken[ev.playerId].team===ev.team&&st.taken[ev.playerId].amount===ev.amount) {room.pending.shift();continue;}
        if(ev.type==='bid'&&!ev.history&&!st.nomination&&!st.taken[ev.playerId]&&ev.playerId) E.append(s,{...ev,id:ev.id+':nomination',type:'nominate'});
        E.append(s,ev);room.pending.shift();
      } catch(err) {raw.problem=err.message;break;}
    }
  }
  function refreshStatus() {
    if(!active())return;
    const el=document.getElementById('draft-source');if(el)el.textContent=status();
    const dot=root.querySelector('.draft-light');dot?.classList.toggle('on',status()==='Watching Yahoo');
    const obs=document.getElementById('draft-observation');if(obs)obs.textContent=room.mode==='practice'?'SIMULATED BIDS & SALES':room.pending.length?room.pending.length+' updates need review':lastHeartbeat?'Last watcher update '+Math.max(0,Math.floor((Date.now()-lastHeartbeat)/1000))+'s ago · observed bids only':'Draft activity stays in this browser';
  }
  async function mount(container,bootstrap,isCurrent=()=>true) {
    root=container;boot=bootstrap;document.body.classList.add('draft-page');
    if(!room) {
      room=readRoom();
      if(!room) {
        const res=await fetch('/api/valuations?season='+encodeURIComponent(boot.target_season));if(!res.ok)throw Error('Draft valuations are unavailable.');
        const data=await res.json();
        const s=E.create(data.rows,boot.draft,{mode:'live',runId:data.run?.run_id});
        room={version:1,mode:'live',team:s.teams.some(t=>t.name==='Max')?'Max':s.teams[0].name,sessions:{live:s},pending:[],ignored:[],teamMap:{},playerMap:{},stars:[]};persist();
      }
    }
    if(!isCurrent())return;
    selectSession();shell();
    if(!timer)timer=setInterval(()=>{if(!active()){playing=false;return;}if(playing&&room.mode==='practice'){try{step();}catch(err){playing=false;error(err.message);}}else if(view.current&&room.mode==='live'&&status()!==lastRenderedStatus)renderLive();else refreshStatus();},2000);
  }
  function unmount() {playing=false;document.body.classList.remove('draft-page');}
  document.addEventListener('click',e=>{
    const action=e.target.closest('[data-draft]')?.dataset.draft,star=e.target.closest('[data-draft-star]')?.dataset.draftStar,nom=e.target.closest('[data-draft-nominate]')?.dataset.draftNominate,t=e.target.closest('[data-draft-tab]')?.dataset.draftTab;
    if(!action&&!star&&!nom&&!t)return;
    try {
      if(star){room.stars=room.stars.includes(star)?room.stars.filter(s=>s!==star):[...room.stars,star];persist();renderTable();}
      if(nom){add(event('nominate',{playerId:nom},room.mode==='practice'?'practice':'manual'));}
      if(t){tab=t;limit=60;renderTable();}
      if(action==='new-live')dialog('Start a fresh live draft','<p>This replaces the live session on this browser with the latest saved forecast and confirmed keepers. Export your current live session first if you want to retain it. Practice remains separate.</p><button class="draft-button primary" data-draft-new-confirm>Start fresh live session</button>');
      if(action==='setup')setup();if(action==='settings')settings();if(action==='record')record();if(action==='review')review();
      if(action==='close-dialog')document.getElementById('draft-dialog').close();
      if(action==='mode'){playing=false;if(room.mode==='practice'){room.mode='live';persist();selectSession();shell();}else{if(room.sessions.practice){room.mode='practice';persist();selectSession();}else practice();shell();}}
      if(action==='reset-practice'){playing=false;practice();shell();}
      if(action==='next')step();if(action==='play'){playing=!playing;renderLive();}
      if(action==='manual'){manual=!manual;renderLive();}
      if(action==='export')download();if(action==='more'){limit+=60;renderTable();}
      if(action==='stars'){onlyStars=!onlyStars;e.target.setAttribute('aria-pressed',String(onlyStars));e.target.textContent=onlyStars?'★ Watchlist':'☆ Watchlist';renderTable();}
      if(action==='undo'){const id=document.getElementById('draft-undo-target').value;if(id){add(event('undo',{targetId:id}));record();}}
      if(action==='ignore'){room.ignored.push({...room.pending.shift(),ignoredAt:new Date().toISOString()});processPending();persist();selectSession();renderLive();if(room.pending.length)review();else document.getElementById('draft-dialog').close();}
    } catch(err){error(err.message);const d=document.getElementById('draft-dialog-body');if(document.getElementById('draft-dialog')?.open)d.insertAdjacentHTML('afterbegin','<p class="draft-alert" role="alert">'+esc(err.message)+'</p>');}
  });
  document.addEventListener('click',async e=>{if(!e.target.closest('[data-draft-new-confirm]'))return;try{const response=await fetch('/api/valuations?season='+encodeURIComponent(boot.target_season));if(!response.ok)throw Error('Could not load the latest forecast.');const data=await response.json();room.sessions.live=E.create(data.rows,boot.draft,{mode:'live',runId:data.run?.run_id});room.mode='live';room.pending=[];room.ignored=[];manual=false;lastHeartbeat=0;persist();selectSession();shell();}catch(err){error(err.message);}});
  document.addEventListener('input',e=>{if(e.target.id==='draft-search'){search=e.target.value;limit=60;renderTable();}});
  document.addEventListener('change',async e=>{
    try {
      if(e.target.id==='draft-team'){room.team=e.target.value;persist();selectSession();renderLive();}
      if(e.target.id==='draft-position'){position=e.target.value;renderTable();}
      if(e.target.id==='draft-sort'){sort=e.target.value;renderTable();}
      if(e.target.id==='draft-import'){
        const file=e.target.files[0];if(!file)return;if(file.size>8*1024*1024)throw Error('Draft file is too large.');
        const imported=JSON.parse(await file.text());E.replay(imported);
        if(imported.season!==boot.target_season)throw Error('This draft belongs to a different season.');
        room.importCandidate=imported;
        dialog('Restore this draft session','<p>'+esc(imported.season)+' · '+esc(imported.mode)+' · '+imported.events.length+' events. Export your current session first if you want to keep both.</p><button class="draft-button primary" data-draft-restore>Restore session</button>');
      }
    }catch(err){error(err.message);}
  });
  document.addEventListener('click',e=>{if(e.target.closest('[data-draft-restore]')){try{const s=room.importCandidate;delete room.importCandidate;room.sessions[s.mode]=s;room.mode=s.mode;room.pending=[];persist();selectSession();shell();}catch(err){error(err.message);}}});
  document.addEventListener('submit',e=>{
    if(!['draft-settings-form','draft-record-form','draft-map-form'].includes(e.target.id))return;e.preventDefault();
    try {
      const f=new FormData(e.target);
      if(e.target.id==='draft-settings-form'){
        const settings={rosterSize:Number(f.get('rosterSize')),minimumBid:Number(f.get('minimumBid')),reservePerSlot:Number(f.get('reservePerSlot')),rosterSlots:String(f.get('rosterSlots')).toUpperCase().split(/[,\s]+/).filter(Boolean)};
        E.replay({...session,settings});session.settings=settings;persist();selectSession();renderLive();
      }
      if(e.target.id==='draft-record-form'){
        const type=f.get('type');if(room.mode==='live')manual=true;
        add(event(type,{playerId:f.get('playerId'),...(['bid','sale'].includes(type)?{team:f.get('team'),amount:Number(f.get('amount'))}:{})},room.mode==='practice'?'practice':'manual'));
      }
      if(e.target.id==='draft-map-form'){
        const raw=room.pending[0];if(!f.get('playerId'))throw Error('Choose a player.');
        room.playerMap[E.key(raw.player)]=f.get('playerId');if(f.get('team'))room.teamMap[raw.team]=f.get('team');processPending();persist();selectSession();renderLive();if(room.pending.length){review();return;}
      }
      document.getElementById('draft-dialog').close();
    }catch(err){document.getElementById('draft-dialog-body').insertAdjacentHTML('afterbegin','<p class="draft-alert" role="alert">'+esc(err.message)+'</p>');}
  });
  window.addEventListener('message',e=>{
    if(e.source!==window||e.origin!==location.origin||e.data?.channel!=='survivor-draft-extension'||!room)return;
    const m=e.data;
    if(m.type==='hello'){nonce=m.nonce;paired=true;window.postMessage({channel:'survivor-draft-page',type:'ready',nonce,sessionId:room.sessions.live.id},location.origin);return;}
    if(!nonce||m.nonce!==nonce||m.sessionId!==room.sessions.live.id)return;
    if(m.type==='heartbeat'){watcherInfo=m.status||{};if(watcherInfo.ready)lastHeartbeat=Date.now();else lastHeartbeat=0;refreshStatus();return;}
    if(m.type==='events'){
      try {
        if(!Array.isArray(m.events)||m.events.length>100)throw Error('Invalid watcher event batch.');
        for(const raw of m.events) {
          if(!raw||typeof raw.id!=='string'||typeof raw.player!=='string'||raw.player.length>160||!['nominate','bid','sale','withdraw'].includes(raw.type))throw Error('Invalid watcher event.');
          if(!room.pending.some(x=>x.id===raw.id)&&!room.ignored.some(x=>x.id===raw.id)&&!room.sessions.live.events.some(x=>x.id===raw.id))room.pending.push(raw);
        }
        processPending();persist();selectSession();renderLive();
        window.postMessage({channel:'survivor-draft-page',type:'ack',nonce,sessionId:m.sessionId,ids:m.events.map(x=>x.id)},location.origin);
      }catch(err){error('Watcher update could not be saved: '+err.message);}
    }
  });
  window.addEventListener('storage',e=>{if(e.key===STORE){try{room=readRoom();if(room){selectSession();if(active())shell();}}catch(err){error(err.message);}}});
  window.DraftRoom={mount,unmount};
})();
