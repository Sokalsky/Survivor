"use strict";

const $ = (selector, root = document) => root.querySelector(selector);
const esc = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const icon = name => `<svg aria-hidden="true"><use href="#i-${name}"/></svg>`;
const number = (value, digits = 0) => value == null ? "—" : Number(value).toLocaleString("en-US", {maximumFractionDigits:digits, minimumFractionDigits:digits});
const money = value => value == null ? "—" : `$${number(value, Number(value) % 1 ? 1 : 0)}`;
const preciseMoney = value => value == null ? "—" : `${value<0?'-':''}$${number(Math.abs(value),2)}`;
const object = value => typeof value==='string' ? JSON.parse(value) : value || {};
const seasonLabel = value => String(value ?? "").replace("-", "–");
const dateLabel = value => value ? new Date(value).toLocaleDateString("en-US", {month:"short",day:"numeric",year:"numeric",timeZone:"UTC"}) : "—";
const initials = name => String(name).split(/\s+/).filter(Boolean).map(s => s[0]).slice(0,2).join("");
const badge = type => `<span class="tag ${type === 'keeper' ? 'keeper' : type === 'auction' ? 'auction' : 'final'}">${type === 'keeper' ? 'Keeper' : type === 'auction' ? 'Auction' : 'Final roster'}</span>`;
const state = {view:"history", season:"", team:"", kind:"all", q:"", sort:"price", direction:"desc", position:"", page:1, bootstrap:null, history:null, dataset:"", run:"", dataRows:[], dataSort:"", sequence:0, tableSequence:0, playerSequence:0};
const views = {
  history:{title:"Price history",eyebrow:"THE LEAGUE ARCHIVE",description:"Know what the room pays. Find where the value lives."},
  projections:{title:"Player projections",eyebrow:"THE SEASON AHEAD",description:"Eight categories. A clearer picture of what comes next."},
  valuations:{title:"Draft valuations",eyebrow:"FIND YOUR EDGE",description:"Survivor value. Your room’s price. A neutral draft’s price."},
  keepers:{title:"Keepers & budgets",eyebrow:"THE DRAFT STARTS HERE",description:"Who’s off the board. What every team has left to spend."},
  rosters:{title:"Team rosters",eyebrow:"THE LEAGUE, TEAM BY TEAM",description:"Explore the players each franchise started and finished with."},
  notes:{title:"Data notes",eyebrow:"BEHIND THE NUMBERS",description:"A transparent record of the details that need a second look."}
};

async function api(path) {
  const response = await fetch(path, {headers:{Accept:"application/json"}});
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || "Could not load league data.");
  return result;
}

function setError(error) {
  $("#error-message").textContent = error.message || "Could not load league data.";
  $("#app-error").hidden = false;
  $("#connection-state").classList.add("offline");
  $("#connection-state").innerHTML = "<i></i> Connection interrupted";
}

function connected() {
  $("#app-error").hidden = true;
  $("#connection-state").classList.remove("offline");
  $("#connection-state").innerHTML = `<i></i> ${state.bootstrap?.preview ? "Archive preview" : "League data connected"}`;
}

function playerButton(row) {
  const type=row.acquisition_class;
  const status=row.draft_status;
  const availability=status ? `<small class="draft-status ${status==='kept'?'keeper-text':''}">${status==='kept'?`Kept · ${esc(row.keeper_franchise)} · ${money(row.confirmed_keeper_cost)}`:status==='available'?'Available to draft':'Keeper list not supplied'}</small>` : '';
  return `<button class="player-button" data-player="${esc(row.player_id)}"><span class="player-initials">${esc(initials(row.player))}</span><span>${esc(row.player)}${row.positions?`<small class="player-position">${esc(row.positions.replaceAll(","," / "))}${row.nba_team?" · "+esc(row.nba_team):""}</small>`:""}${availability}${type?`<small class="mobile-entry-type ${type==='keeper'?'keeper-text':''}">${type==='keeper'?'Keeper':type==='auction'?'Auction':'Final roster'}</small>`:''}</span></button>`;
}

function setNavigation() {
  const view = views[state.view];
  document.title = `${view.title} · Survivor`;
  $("#page-title").innerHTML = `${view.title}<span>.</span>`;
  $("#breadcrumb-view").textContent = view.title;
  document.querySelectorAll("[data-view]").forEach(link => {
    link.classList.toggle("active", link.dataset.view === state.view);
    if (link.dataset.view === state.view) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  });
  $("#season-control").hidden = !["history","rosters"].includes(state.view);
}

function loading() { return `<div class="initial-loading"><span class="spinner"></span> Loading league data…</div>`; }

function distributionChart(data) {
  const bands = ["1-5","6-15","16-30","31-50","51+"];
  const counts = bands.map(band => data.distribution.find(row => row.band === band)?.players || 0);
  const peak = Math.max(1, ...counts);
  const maxY = Math.ceil(peak / 20) * 20;
  const y = n => 125 - (n / maxY) * 100;
  let svg = `<svg viewBox="0 0 510 155" role="img" aria-label="Auction purchases by price band for ${esc(data.season)}">`;
  [0, maxY/2, maxY].forEach(tick => {
    svg += `<path d="M27 ${y(tick)}H505" stroke="#eaf0e4" stroke-width="1" stroke-dasharray="3 4"/><text x="18" y="${y(tick)+3}" text-anchor="end" fill="#a0ae93" font-size="8">${tick}</text>`;
  });
  bands.forEach((band,i) => {
    const x = 48 + i*96;
    const height = counts[i] / maxY * 100;
    svg += `<rect x="${x}" y="${125-height}" width="48" height="${height}" rx="3" fill="${i===0 ? '#345f43' : i===1 ? '#6e9760' : '#c5d7b1'}"/><text x="${x+24}" y="${116-height}" text-anchor="middle" fill="#6d805e" font-size="9" font-weight="600">${counts[i]}</text><text x="${x+24}" y="145" text-anchor="middle" fill="#8a9b7a" font-size="8">$${band.replace('-', '–')}</text>`;
  });
  return svg + "</svg>";
}

function overviewHTML(data) {
  const title = data.season === "all" ? "All seasons" : seasonLabel(data.season);
  return `<section class="stats-strip" aria-label="Season statistics">
    <div class="stat"><div class="stat-label">Players at auction ${icon('users')}</div><div class="stat-value">${number(data.auction_players)}</div><div class="stat-note">${title} <b>· ${number(data.keepers || 0)} keepers</b></div></div>
    <div class="stat"><div class="stat-label">Auction spend ${icon('chart')}</div><div class="stat-value">${money(data.auction_spend)}</div><div class="stat-note">Recorded winning bids</div></div>
    <div class="stat"><div class="stat-label">Keeper spend ${icon('target')}</div><div class="stat-value">${money(data.keeper_spend)}</div><div class="stat-note">Carried into the season</div></div>
    <div class="stat"><div class="stat-label">Highest auction bid ${icon('history')}</div><div class="stat-value">${money(data.top_bid)}</div><div class="stat-note">${esc(data.leaders[0]?.player || 'No auction records')}</div></div>
  </section>
  <div class="insights-grid">
    <section class="panel"><div class="panel-heading"><div><h2 class="panel-title">Where the money goes</h2></div><span class="mini-label">${esc(title)}</span></div><div class="price-chart">${distributionChart(data)}</div><div class="chart-caption"><span>Every price tier has a role to play.</span><span>Auction purchases only</span></div></section>
    <section class="panel"><div class="panel-heading"><div><h2 class="panel-title">The biggest bids</h2></div>${icon('arrow')}</div><div class="leader-list">${data.leaders.map((row,i) => `<button class="leader" data-player="${esc(row.player_id)}"><span class="leader-number">0${i+1}</span><span class="player-initials">${esc(initials(row.player))}</span><span class="leader-name">${esc(row.player)}<span class="leader-team">${esc(row.franchise_sheet)}${data.season==='all' ? ' · '+esc(seasonLabel(row.season)) : ''}</span></span><span class="leader-price">${money(row.recorded_cost)}</span>${icon('chevron')}</button>`).join('') || '<div class="no-results">No auction records for this season.</div>'}</div></section>
  </div>`;
}


const tableColumns = [
  ['player','Player','text'],['positions','Position','text'],['nba_team','Team','text'],
  ['survivor_score','Survivor value','score'],['expected_auction_price','Expected league price','money'],
  ['fair_value','Neutral auction value','money'],['confirmed_keeper_surplus','Keeper surplus','money'],
  ['games','GP','integer'],['minutes_pg','MIN','number'],['pts_pg','PTS','number'],['reb_pg','REB','number'],
  ['ast_pg','AST','number'],['stl_pg','STL','number'],['blk_pg','BLK','number'],['fg3m_pg','3PM','number'],
  ['fg_pct','FG%','percent'],['ft_pct','FT%','percent'],['fgm_pg','FGM','number'],['fga_pg','FGA','number'],
  ['ftm_pg','FTM','number'],['fta_pg','FTA','number']
];
const normalizedName = value => String(value || '').normalize('NFD').replace(/[\u0300-\u036f]/g,'').toLowerCase().replace(/[^a-z0-9]/g,'');
function positionOptions(selected='') {
  return [['','All positions'],['PG','PG'],['SG','SG'],['SF','SF'],['PF','PF'],['C','C'],['G','All guards'],['F','All forwards'],['unknown','Position unavailable']].map(([key,label])=>`<option value="${key}" ${key===selected?'selected':''}>${label}</option>`).join('');
}
function hasPosition(value, selected) {
  if (!selected) return true;
  const tokens=String(value || '').toUpperCase().match(/[A-Z]+/g) || [];
  if (selected==='unknown') return !tokens.length;
  return tokens.some(t=>(selected==='G'?['G','PG','SG']:selected==='F'?['F','SF','PF']:[selected]).includes(t));
}
function sortHeader(key,label,sort,direction,scope='data',numeric=true) {
  const active=sort===key;
  return `<th ${numeric?'class="right"':''} aria-sort="${active?(direction==='asc'?'ascending':'descending'):'none'}"><button class="column-sort" data-sort-scope="${scope}" data-sort-key="${key}">${esc(label.toUpperCase())}<span aria-hidden="true">${active?(direction==='asc'?' ↑':' ↓'):' ↕'}</span></button></th>`;
}

function filtersHTML() {
  const roster=state.view==='rosters';
  const options=roster?[['all','Opening rosters'],['final','Final rosters']]:[['all','All prices'],['auction','Auction'],['keeper','Keepers']];
  return `<div class="filter-row"><label class="search-box">${icon('search')}<input id="player-search" type="search" placeholder="Search players…" aria-label="Search players" value="${esc(state.q)}"></label>
    <div class="segmented" aria-label="Entry type">${options.map(([value,label])=>`<button data-kind="${value}" class="${state.kind===value?'active':''}" aria-pressed="${state.kind===value}">${label}</button>`).join('')}</div>
    <select class="select-filter" id="history-position" aria-label="Position">${positionOptions(state.position)}</select>
    <select class="select-filter" id="team-filter" aria-label="Franchise"><option value="">All franchises</option>${state.bootstrap.teams.map(t=>`<option value="${esc(t)}" ${state.team===t?'selected':''}>${esc(t)}</option>`).join('')}</select>
    <select class="select-filter" id="sort-filter" aria-label="Sort prices">${[['price','Price'],['player','Player'],['position','Position'],['season','Season'],['team','Franchise'],['type','Entry type'],['contract','Contract year']].map(([key,label])=>`<option value="${key}" ${state.sort===key?'selected':''}>${label}</option>`).join('')}</select>
    <select class="select-filter direction-filter" id="history-direction" aria-label="Sort direction"><option value="desc" ${state.direction==='desc'?'selected':''}>Descending</option><option value="asc" ${state.direction==='asc'?'selected':''}>Ascending</option></select></div>`;
}

function historyPanelHTML() {
  return `<section class="panel history-panel"><div class="table-title-row"><h2 class="table-heading">${state.view==='rosters'?'Rosters':'Prices'} <span id="results-count" class="count-badge">—</span></h2><a id="export-link" class="text-button" href="/api/history.csv">${icon('download')} Export CSV</a></div>${filtersHTML()}<div id="history-table">${loading()}</div></section>`;
}

function queryString() {
  return new URLSearchParams({season:state.season,team:state.team,kind:state.kind,q:state.q,sort:state.sort,direction:state.direction,position:state.position,page:state.page,limit:30}).toString();
}

function historyTableHTML(data) {
  const start = data.total ? (data.page-1)*data.limit+1 : 0;
  return `<div class="table-scroll"><table class="price-table"><thead><tr>${[["player","Player"],["season","Season"],["team","Franchise"],["type","Entry type"],["price","Price paid"],["contract","Contract year"]].map(([key,label],i)=>sortHeader(key,label,state.sort,state.direction,"history",i>3)).join("")}<th aria-label="Details"></th></tr></thead><tbody>${data.rows.map(row => `<tr><td class="player-cell">${playerButton(row)}</td><td>${esc(seasonLabel(row.season))}</td><td>${esc(row.franchise_sheet)}</td><td>${badge(row.acquisition_class)}</td><td class="money-cell">${money(row.recorded_cost)}</td><td class="right">${number(row.contract_year_recorded)}</td><td><button class="text-button" data-player="${esc(row.player_id)}" aria-label="View ${esc(row.player)} history">${icon('arrow')}</button></td></tr>`).join('') || '<tr><td colspan="7"><div class="no-results">No players match these filters. Try another name or season.</div></td></tr>'}</tbody></table></div><div class="table-foot"><span>Showing ${number(start)}–${number(Math.min(data.page*data.limit,data.total))} of ${number(data.total)} records</span><div class="pager"><button data-page="${data.page-1}" ${data.page<=1?'disabled':''}>← Previous</button><span>${data.page} / ${Math.max(1,Math.ceil(data.total/data.limit))}</span><button data-page="${data.page+1}" ${data.page*data.limit>=data.total?'disabled':''}>Next →</button></div></div>`;
}

async function loadHistoryTable() {
  const token = ++state.tableSequence;
  const view = state.view;
  const container = $('#history-table');
  if (!container) return;
  container.classList.add('is-loading');
  try {
    const data = await api(`/api/history?${queryString()}`);
    if (token !== state.tableSequence || view !== state.view) return;
    state.history = data; state.page = data.page;
    $('#history-table').innerHTML = historyTableHTML(data);
    $('#results-count').textContent = number(data.total);
    $('#export-link').href = `/api/history.csv?${queryString()}`;
    connected();
  } catch (error) { if (token===state.tableSequence) setError(error); }
  finally { if (token===state.tableSequence) $('#history-table')?.classList.remove('is-loading'); }
}

function keeperCards() {
  const data = state.keepers;
  const q = ($('#keeper-search')?.value || '').toLowerCase();
  const sort = $('#keeper-sort')?.value || 'budget';
  const teams = data.teams.filter(team => [team.franchise,...data.rows.filter(row=>row.franchise===team.franchise).map(row=>row.player)].some(text=>text.toLowerCase().includes(q)));
  teams.sort((a,b)=>sort==='team'?a.franchise.localeCompare(b.franchise):sort==='spend'?b.keeper_spend-a.keeper_spend || a.franchise.localeCompare(b.franchise):b.remaining_budget-a.remaining_budget || a.franchise.localeCompare(b.franchise));
  $('#keeper-team-count').textContent = `${teams.length} teams`;
  $('#keeper-grid').innerHTML = teams.map(team=>`<article class="panel keeper-card" data-franchise="${esc(team.franchise)}">
    <div class="keeper-card-heading"><h2>${esc(team.franchise)}</h2><span class="tag keeper">${team.keeper_count} keepers</span></div>
    <div class="keeper-budget"><strong>${money(team.remaining_budget)}</strong><span>left to draft</span></div>
    <div class="budget-track" role="img" aria-label="${money(team.remaining_budget)} of ${money(team.budget_per_team)} remaining"><span style="width:${team.remaining_budget/team.budget_per_team*100}%"></span></div>
    <div class="budget-caption"><span>${money(team.keeper_spend)} committed</span><span>${money(team.budget_per_team)} budget</span></div>
    <div class="keeper-players">${data.rows.filter(row=>row.franchise===team.franchise).map(row=>`<div class="keeper-player">${playerButton(row)}<strong>${money(row.keeper_cost)}</strong></div>`).join('')}</div>
  </article>`).join('') || '<div class="no-results">No matching teams or keepers.</div>';
}

function keepersHTML(data) {
  if (!data) return '<div class="no-results">No confirmed keeper list has been added for this season.</div>';
  return `<section class="stats-strip" aria-label="Draft budgets">
    <div class="stat"><div class="stat-label">Confirmed keepers ${icon('check')}</div><div class="stat-value">${data.keeper_count}</div><div class="stat-note">${esc(seasonLabel(data.season))} · unavailable to draft</div></div>
    <div class="stat"><div class="stat-label">Keeper spend ${icon('target')}</div><div class="stat-value">${money(data.keeper_spend)}</div><div class="stat-note">Already committed across the league</div></div>
    <div class="stat"><div class="stat-label">Auction money ${icon('chart')}</div><div class="stat-value">${money(data.remaining_budget)}</div><div class="stat-note">Remaining across ${data.team_count} teams</div></div>
    <div class="stat"><div class="stat-label">Starting budget ${icon('users')}</div><div class="stat-value">${money(data.budget_per_team)}</div><div class="stat-note">Per team, including keeper costs</div></div>
  </section>
  <section class="panel keeper-toolbar"><div class="filter-row"><label class="search-box">${icon('search')}<input id="keeper-search" type="search" placeholder="Find a team or player…" aria-label="Search keepers"></label><span class="subtle-count" id="keeper-team-count"></span><select id="keeper-sort" class="select-filter" aria-label="Sort keeper teams"><option value="budget">Most money remaining</option><option value="spend">Highest keeper spend</option><option value="team">Team: A–Z</option></select></div></section>
  <div class="keeper-grid" id="keeper-grid"></div>`;
}

function emptyHTML(kind) {
  return `<section class="empty-state"><h2>No ${esc(kind)} for ${esc(seasonLabel(state.bootstrap.target_season))}.</h2></section>`;
}

function statsToolbar(kind) {
  const values=kind==='valuations';
  const known=state.dataRows.length?state.dataRows.every(r=>r.draft_status && r.draft_status!=='unknown'):state.bootstrap.draft?.season===state.dataSeason;
  const sort=values?'survivor_score':'pts_pg';
  return `<div class="table-title-row"><h2 class="table-heading">${values?'Values':'Projections'} <span class="count-badge" id="data-count"></span></h2><a id="values-export" class="text-button" href="#">${icon('download')} Export CSV</a></div>
    <div class="filter-row"><label class="search-box">${icon('search')}<input id="data-search" type="search" placeholder="Search players…" aria-label="Search ${kind}"></label>
    <select id="data-position" class="select-filter" aria-label="Position">${positionOptions()}</select>
    <select id="draft-filter" class="select-filter" aria-label="Draft availability"><option value="available" ${known?'selected':''}>Available to draft</option><option value="all" ${known?'':'selected'}>All players</option><option value="kept">Keepers only</option></select>
    <select id="data-columns" class="select-filter" aria-label="Table columns"><option value="values" ${values?'selected':''}>Values</option><option value="projections" ${values?'':'selected'}>Projections · per game</option><option value="all">Values + projections</option></select>
    <select id="data-sort" class="select-filter" aria-label="Sort ${kind}">${tableColumns.map(([key,label])=>`<option value="${key}" ${key===sort?'selected':''}>${label}</option>`).join('')}</select>
    <select id="data-direction" class="select-filter direction-filter" aria-label="Sort direction"><option value="desc">Descending</option><option value="asc">Ascending</option></select></div>`;
}

function datasetHeading(metadata,kind) {
  const values=kind==='valuations';
  const options=values?state.bootstrap.runs:state.bootstrap.datasets.filter(d=>d.kind==='projection');
  const key=values?'run_id':'dataset_id';
  const available=options.some(d=>d[key]===metadata[key])?options:[metadata,...options];
  return `<div class="dataset-header"><div><strong>${esc(seasonLabel(metadata.season))} · ${esc(metadata.source_name)}</strong><small>${metadata.date_basis==='received'?'Received: ':''}${dateLabel(values?metadata.created_at:metadata.as_of_date)}${metadata.coverage==='partial_player_pool'?' · Partial pool':''}${/^https:\/\//.test(metadata.source_url || '')?` · <a href="${esc(metadata.source_url)}" target="_blank" rel="noopener noreferrer">Source</a>`:''}</small></div><select class="select-filter" id="dataset-select" aria-label="${values?'Valuation run':'Projection set'}">${available.map(d=>`<option value="${esc(d[key])}" ${d[key]===metadata[key]?'selected':''}>${esc(seasonLabel(d.season))} · ${esc(values?d.model_version:d.source_name)} · ${dateLabel(values?d.created_at:d.as_of_date)}</option>`).join('')}</select></div>`;
}





function valuationDetail(valuation, keeper) {
  const c=object(valuation.category_values_json),comps=object(valuation.comps_json),z=c.rate_z || {};
  const max=Math.max(1,...Object.values(z).map(Math.abs));
  const kept=keeper && keeper.season===valuation.season;
  return `<div class="drawer-metrics valuation-metrics"><div><small>SURVIVOR VALUE</small><strong>${number(c.score,2)}</strong></div><div><small>EXPECTED LEAGUE PRICE${kept?' · IF AVAILABLE':''}</small><strong>${money(valuation.expected_auction_price)}</strong></div><div><small>NEUTRAL AUCTION VALUE</small><strong>${money(valuation.fair_value)}</strong></div></div>
    <div class="detail-facts"><span>Price band ${money(valuation.lower_estimate)}–${money(valuation.upper_estimate)}</span>${kept?`<span>Keeper surplus ${money(valuation.fair_value-keeper.keeper_cost)}</span>`:''}${c.useful_games!=null?`<span>Useful GP ${number(c.useful_games,1)} / ${number(c.games)}</span>`:''}</div>
    ${Array.isArray(comps)&&(comps.length || c.market)?comparableDetail(comps,c.market):''}
    ${Object.keys(z).length?`<h3 class="drawer-section-title">Category contributions</h3><div class="category-bars" role="img" aria-label="Standardized category contributions">${Object.entries(z).map(([cat,value])=>`<div class="category-row"><span>${esc(cat)}</span><div class="category-track"><i class="${value<0?'negative':''}" style="left:${value<0?50-Math.abs(value)/max*50:50}%;width:${Math.abs(value)/max*50}%"></i></div><strong>${value>0?'+':''}${number(value,2)}</strong></div>`).join('')}</div>`:''}
    ${c.scenario_values?`<h3 class="drawer-section-title">Neutral value by cut schedule</h3><div class="projection-grid scenario-grid">${Object.entries(c.scenario_values).map(([name,value])=>`<div class="projection-stat"><small>${esc(name.toUpperCase())}</small><strong>${money(value)}</strong></div>`).join('')}</div>`:''}`;
}

function comparableDetail(comps,market,sort='weight',direction='desc') {
  state.drawerComps={comps,market,sort,direction};
  const ordered=[...comps].sort((a,b)=>{
    if(a[sort]==null || b[sort]==null)return a[sort]==null?(b[sort]==null?0:1):-1;
    const comparison=['player','season'].includes(sort)?a[sort].localeCompare(b[sort]):a[sort]-b[sort];
    return comparison*(direction==='asc'?1:-1) || a.player.localeCompare(b.player);
  });
  const first=ordered.slice(0,5),rest=ordered.slice(5);
  return `<div id="comparable-content"><h3 class="drawer-section-title" id="player-comps" tabindex="-1">Historical comps</h3>
    ${market?.comp_adjustment!=null?`<div class="comp-price-breakdown"><span>Base estimate <strong>${preciseMoney(market.base_price)}</strong></span><span>Weighted comp adjustment <strong>${market.comp_adjustment>=0?'+':''}${preciseMoney(market.comp_adjustment)}</strong></span></div>`:''}
    ${market?.strong_comp_count!=null?`<p class="comp-summary">${market.strong_comp_count?`${market.strong_comp_count} close forecast ${market.strong_comp_count===1?'match':'matches'} · ${number(market.strong_comp_weight*100,1)}% weight`:comps.length?'Supporting matches only':'No qualifying comps · base estimate only'}</p>`:''}
    ${market?.comparison_stats && comps.length?compStatComparison(comps,market):''}
    ${first.length?comparableTable(first,!!market,sort,direction):''}
    ${rest.length?`<details class="remaining-comps"><summary class="drawer-section-title">${rest.length} more comps · ${number(rest.reduce((sum,c)=>sum+(c.weight || 0),0)*100,1)}% weight</summary>${comparableTable(rest,!!market,sort,direction)}</details>`:''}</div>`;
}

function compStatComparison(comps,market) {
  const best=[...comps].sort((a,b)=>b.weight-a.weight).slice(0,3);
  const columns=[['pts_pg','PTS'],['reb_pg','REB'],['ast_pg','AST'],['stl_pg','STL'],['blk_pg','BLK'],['fg3m_pg','3PM'],['fg_pct','FG%'],['ft_pct','FT%']];
  const cell=(stats,key)=>{
    if(key==='fg_pct' || key==='ft_pct') {
      const prefix=key.slice(0,2),attempts=stats[prefix+'a_pg'];
      return `${attempts?number(stats[prefix+'m_pg']/attempts*100,1)+'%':'—'}<small>${number(attempts,1)} ${prefix.toUpperCase()}A</small>`;
    }
    return number(stats[key],1);
  };
  const rows=[{player:market.comparison_player || 'This projection',stats:market.comparison_stats,projected_games:market.comparison_games,current:true},...best];
  return `<details class="comp-stat-comparison" open><summary>Per-game comparison · top ${best.length} by weight</summary><div class="panel table-scroll"><table class="comp-stats-table"><thead><tr><th>PLAYER</th><th>GP</th>${columns.map(([,label])=>`<th>${label}</th>`).join('')}<th title="Paid bid adjusted to today's auction budget, available talent and this player's stat/age profile, before weighting">ADJUSTED $</th></tr></thead><tbody>${rows.map(row=>`<tr class="${row.current?'comp-target':''}"><td>${esc(row.player)}<small>${row.current?'This projection':esc(seasonLabel(row.stats_season))+' · '+(row.forecast_dataset_id?'projection':'prior stats')}</small></td><td>${number(row.projected_games)}</td>${columns.map(([key])=>`<td>${cell(row.stats,key)}</td>`).join('')}<td>${row.current?'—':preciseMoney(row.implied_price)}</td></tr>`).join('')}</tbody></table></div></details>`;
}

function comparableTable(comps,weighted,sort,direction) {
  return `<div class="panel table-scroll"><table class="comps-table"><thead><tr>${[["player","Player / stats"],["season","Auction"],["actual_price","Paid"],["distance","Distance"],...(weighted?[["weight","Weight"],["price_adjustment","Price effect"]]:[])].map(([key,label],i)=>sortHeader(key,label,sort,direction,"comps",i>1)).join("")}</tr></thead><tbody>${comps.map(comp=>`<tr><td>${playerButton(comp)}${comp.match_quality?`<span class="comp-quality ${comp.match_quality}">${comp.match_quality==='strong'?'Close forecast':'Supporting'}</span>`:''}<small>${esc(seasonLabel(comp.stats_season))} ${comp.forecast_dataset_id?'projection · GP '+number(comp.projected_games):'prior stats proxy'}${comp.target_season_age!=null?' · age '+number(comp.target_season_age)+' at auction':''}</small></td><td>${esc(seasonLabel(comp.season))}<small>${esc(comp.franchise)}</small>${comp.average_team_budget!=null?`<small>Avg budget ${preciseMoney(comp.average_team_budget)} · ${comp.available_top_30}/30 available</small>`:''}</td><td class="money-cell">${money(comp.actual_price)}</td><td class="right" title="Combined profile distance; smaller is closer">${number(comp.distance,2)}</td>${weighted?`<td class="right comp-weight">${number(comp.weight*100,1)}%</td><td class="right" title="Implied price for this player: ${preciseMoney(comp.implied_price)}. Budget-adjusted bid: ${preciseMoney(comp.budget_adjusted_price)}; profile adjustment: ${preciseMoney(comp.profile_adjustment)}. Displayed effect includes comp weight and correction share.">${comp.price_adjustment>0?'+':''}${preciseMoney(comp.price_adjustment)}</td>`:''}</tr>`).join('')}</tbody></table></div>`;
}

function renderStatsTable() {
  const sort=$('#data-sort').value,direction=$('#data-direction').value;
  const availability=$('#draft-filter').value,position=$('#data-position').value;
  const q=$('#data-search').value,mode=$('#data-columns').value;
  const data=state.dataRows.filter(r=>normalizedName(r.player).includes(normalizedName(q)) && hasPosition(r.positions,position) && (availability==='all' || r.draft_status===availability));
  data.sort((a,b)=>{
    const av=a[sort],bv=b[sort];
    if(av==null || bv==null) return av==null && bv==null?a.player.localeCompare(b.player):av==null?1:-1;
    const comparison=['player','positions','nba_team'].includes(sort)?String(av).localeCompare(String(bv)):Number(av)-Number(bv);
    return comparison*(direction==='asc'?1:-1) || a.player.localeCompare(b.player);
  });
  const valueKeys=['survivor_score','expected_auction_price','fair_value'];
  const statKeys=['nba_team','positions','games','minutes_pg','pts_pg','reb_pg','ast_pg','stl_pg','blk_pg','fg3m_pg','fg_pct','ft_pct'];
  const keys=['player',...(mode==='values'?valueKeys:mode==='projections'?statKeys:[...valueKeys,...statKeys])];
  if(!keys.includes(sort))keys.push(sort);
  const columns=keys.map(k=>tableColumns.find(c=>c[0]===k));
  $('#data-count').textContent=number(data.length);
  const parameters=new URLSearchParams({[state.view==='valuations'?'run':'dataset']:state.view==='valuations'?state.activeRun:state.activeDataset,q,position,availability,sort,direction});
  $('#values-export').href=`/api/${state.view}.csv?${parameters}`;
  const cell=(r,[key,label,type])=>{
    if(key==='player')return `<td class="player-cell">${playerButton(r)}</td>`;
    const v=r[key];
    const formatted=type==='text'?esc(v || '—'):type==='money'?money(v):type==='percent'?(v==null?'—':number(v*100,1)+'%'):number(v,type==='score'?2:type==='integer'?0:1);
    return `<td class="${type==='money'||type==='score'?'money-cell':'right'}" data-label="${esc(label)}" data-value="${esc(v)}">${key==='expected_auction_price'&&v!=null?`<button class="price-comps-button" data-player="${esc(r.player_id)}" data-focus="comps" aria-label="View price comps for ${esc(r.player)}">${formatted}<span>View comps</span></button>`:formatted}</td>`;
  };
  $('#data-table').innerHTML=`<div class="table-scroll"><table class="${mode==='values'&&keys.length===4?'value-table':'stat-table'}"><thead><tr>${columns.map(([k,l,t])=>sortHeader(k,l,sort,direction,'data',t!=='text')).join('')}</tr></thead><tbody>${data.map(r=>`<tr>${columns.map(c=>cell(r,c)).join('')}</tr>`).join('') || `<tr><td colspan="${keys.length}"><div class="no-results">No matching players.</div></td></tr>`}</tbody></table></div>`;
}

function historicalStatsStatus() {
  // Bootstrap orders datasets newest first; count one snapshot per season.
  const latest = new Map();
  for (const dataset of state.bootstrap.datasets) {
    if (dataset.kind==='actual' && !latest.has(dataset.season)) latest.set(dataset.season,dataset);
  }
  const seasons = [...latest.keys()].sort();
  const rows = [...latest.values()].reduce((sum,dataset)=>sum+Number(dataset.players),0);
  const message = seasons.length ? `<strong>Historical NBA stats loaded: ${number(seasons.length)} seasons / ${number(rows)} player-season records.</strong><br>${esc(seasonLabel(seasons[0]))} through ${esc(seasonLabel(seasons.at(-1)))}. Counts use the latest stored dataset for each season.` : 'No historical NBA stats have been loaded yet.';
  return `<div class="status-banner" id="historical-stats-status">${icon('chart')}<span>${message}</span></div>`;
}

async function loadView() {
  if (!state.bootstrap) return initialize();
  const token = ++state.sequence;
  ++state.tableSequence;
  setNavigation();
  $('#view-content').innerHTML = loading();
  try {
    if (state.view==='history' || state.view==='rosters') {
      let heading = '';
      if (state.view==='history') {
        const overview = await api(`/api/overview?season=${encodeURIComponent(state.season)}`);
        if (token!==state.sequence) return;
        heading = overviewHTML(overview);
      }
      $('#view-content').innerHTML = heading + historyPanelHTML();
      await loadHistoryTable();
    } else if (state.view==='keepers') {
      const data = await api('/api/keepers');
      if (token!==state.sequence) return;
      state.keepers = data.draft;
      $('#view-content').innerHTML = keepersHTML(data.draft);
      if (data.draft) keeperCards();
    } else if (state.view==='projections' || state.view==='valuations') {
      const valuations = state.view==='valuations';
      const key = valuations ? 'run' : 'dataset';
      const id = valuations ? state.run : state.dataset;
      const data = await api(`/api/${state.view}?${key}=${encodeURIComponent(id)}`);
      if (token!==state.sequence) return;
      const metadata = valuations ? data.run : data.dataset;
      if (!metadata) {
        // Older dated sets remain accessible even if the target season has no data yet.
        const available = valuations ? state.bootstrap.runs : state.bootstrap.datasets.filter(d=>d.kind==='projection');
        const picker = available.length ? `<div class="dataset-header"><span>Browse an earlier ${valuations?'valuation run':'projection set'}</span><select class="select-filter" id="dataset-select" aria-label="Earlier data"><option value="">Choose a saved set</option>${available.map(d=>`<option value="${esc(d[valuations?'run_id':'dataset_id'])}">${esc(seasonLabel(d.season))} · ${esc(d.source_name)} · ${dateLabel(valuations?d.created_at:d.as_of_date)}</option>`).join('')}</select></div>` : '';
        $('#view-content').innerHTML = picker + emptyHTML(state.view);
      } else {
        state.dataRows = data.rows.map(row=>({...row,fg_pct:row.fga_pg?row.fgm_pg/row.fga_pg:null,ft_pct:row.fta_pg?row.ftm_pg/row.fta_pg:null,survivor_score:object(row.category_values_json).score,confirmed_keeper_surplus:row.confirmed_keeper_cost!=null && row.fair_value!=null ? row.fair_value-row.confirmed_keeper_cost : null}));
        state.dataSeason = metadata.season;
        if (valuations) state.activeRun=metadata.run_id;
        else state.activeDataset=metadata.dataset_id;
        $('#view-content').innerHTML = datasetHeading(metadata,state.view)+`<section class="panel history-panel">${statsToolbar(state.view)}<div id="data-table"></div></section>`;
        renderStatsTable();
      }
    } else {
      const data = await api('/api/notes');
      if (token!==state.sequence) return;
      const labels = {final_cost_differs_from_opening:'Cost differs between opening and final roster',opening_roster_size:'Opening roster has fewer recorded players',ambiguous_player_name:'Player name needs confirmation',finish_order_inconsistent:'Finishing positions need review',duplicate_on_same_roster:'Player listed twice on a roster',explicit_vacancy:'A vacant roster slot was recorded'};
      $('#view-content').innerHTML = `<div class="status-banner">${icon('note')}<span>${state.bootstrap.issue_count} items need review. Original workbook entries are preserved so every correction can be traced back to its source.</span></div><div class="issue-list">${data.rows.map(row=>`<article class="issue-row"><div class="issue-title"><span class="tag ${row.severity==='info'?'final':'keeper'}">${row.severity==='info'?'Recorded':'Review'}</span>${esc(labels[row.code] || row.code.replaceAll('_',' '))}<span class="issue-source">${esc(seasonLabel(row.season))}${row.sheet ? ' · '+esc(row.sheet) : ''}${row.source_cell ? ' · '+esc(row.source_cell) : ''}</span></div><p>${esc(row.detail)}</p></article>`).join('') || '<div class="no-results">There are no data notes.</div>'}</div>`;
    }
    if (state.view==='notes') $('#view-content').insertAdjacentHTML('afterbegin',historicalStatsStatus());
    if (token===state.sequence) connected();
  } catch (error) { if (token===state.sequence) {setError(error);$('#view-content').innerHTML='<div class="no-results">League data couldn’t be loaded. Use “Try again” above to reconnect.</div>';} }
}

function playerChart(history) {
  if (!history.length) return '<div class="no-results">No opening prices recorded.</div>';
  const max = Math.ceil(Math.max(10,...history.map(row=>row.recorded_cost || 0))/20)*20;
  const step = 470/Math.max(1,history.length);
  let svg = `<svg viewBox="0 0 530 200" role="img" aria-label="Player opening prices by season; green indicates auction and gold indicates keeper cost">`;
  [0,max/2,max].forEach(value=>{const y=153-value/max*115;svg+=`<path d="M35 ${y}H520" stroke="#e4eadc" stroke-dasharray="3 4"/><text x="27" y="${y+3}" text-anchor="end" font-size="8" fill="#8b9a7b">$${value}</text>`;});
  history.forEach((row,i)=>{const h=(row.recorded_cost || 0)/max*115;const width=Math.min(30,step*.62);const x=43+i*step+(step-width)/2;svg+=`<rect x="${x}" y="${153-h}" width="${width}" height="${h}" rx="3" fill="${row.acquisition_class==='keeper'?'#b78b48':'#466e46'}"/><text x="${x+width/2}" y="${143-h}" text-anchor="middle" font-size="8" fill="#667f51">${money(row.recorded_cost)}</text><text x="${x+width/2}" y="174" text-anchor="middle" font-size="7" fill="#8b9a7b">${esc(row.season.slice(2))}</text>`;});
  return svg+'</svg>';
}

async function openPlayer(id,section) {
  const token = ++state.playerSequence;
  const dialog = $('#player-dialog');
  $('#player-content').innerHTML = '<h2 id="player-name">Loading player…</h2>'+loading();
  if (!dialog.open) dialog.showModal();
  dialog.scrollTop=0;
  document.body.style.overflow='hidden';
  try {
    const data=await api(`/api/players/${encodeURIComponent(id)}`);
    if (token!==state.playerSequence || !dialog.open) return;
    const auctions=data.history.filter(row=>row.acquisition_class==='auction');
    const last=data.history.at(-1);
    const peak=auctions.length ? Math.max(...auctions.map(row=>row.recorded_cost)) : null;
    const valuation=state.view==='valuations' && state.activeRun ? data.valuations.find(v=>v.run_id===state.activeRun) : state.view==='projections'?data.valuations.find(v=>v.projection_dataset_id===state.activeDataset):data.valuations[0];
    const projectionID=state.view==='projections'?state.activeDataset:valuation?.projection_dataset_id;
    const projection=projectionID ? data.projections.find(p=>p.dataset_id===projectionID) : data.projections[0];
    const keeper=data.confirmed_keeper;
    $('#player-content').innerHTML=`<div class="player-title"><span class="player-initials">${esc(initials(data.display_name))}</span><div><h2 id="player-name">${esc(data.display_name)}</h2><p>${number(data.history.length)} recorded opening seasons · ${number(auctions.length)} auction purchases</p></div></div>
      ${keeper?`<div class="status-banner confirmed-keeper">${icon('check')}<span><strong>${esc(seasonLabel(keeper.season))} keeper · ${esc(keeper.franchise)} · ${money(keeper.keeper_cost)}</strong></span></div>`:''}
      <h3 class="drawer-section-title">${valuation?esc(seasonLabel(valuation.season))+' ':''}Valuation</h3>${valuation ? valuationDetail(valuation,keeper) : '<div class="drawer-muted">No valuation for this projection set.</div>'}
      <div class="drawer-metrics"><div><small>LAST RECORDED COST</small><strong>${money(last?.recorded_cost)}</strong></div><div><small>HIGHEST AUCTION BID</small><strong>${money(peak)}</strong></div><div><small>LATEST ENTRY</small><strong style="font-size:18px;margin-top:10px">${last ? esc(seasonLabel(last.season)) : '—'}</strong></div></div>
      <section class="panel drawer-chart"><div class="panel-heading"><h3 class="panel-title">A history in dollars</h3><div class="legend"><span><i></i> Auction</span><span><i class="keeper"></i> Keeper</span></div></div>${playerChart(data.history)}</section>
      <h3 class="drawer-section-title">Opening price history</h3><div class="panel table-scroll"><table class="drawer-table"><thead><tr><th>SEASON</th><th>FRANCHISE</th><th>TYPE</th><th class="right">COST</th></tr></thead><tbody>${[...data.history].reverse().map(row=>`<tr><td>${esc(seasonLabel(row.season))}</td><td>${esc(row.franchise_sheet)}</td><td>${badge(row.acquisition_class)}</td><td class="money-cell">${money(row.recorded_cost)}</td></tr>`).join('') || '<tr><td colspan="4">No opening history recorded.</td></tr>'}</tbody></table></div>
      <h3 class="drawer-section-title">${projection ? esc(seasonLabel(projection.season)) : esc(seasonLabel(state.bootstrap.target_season))} projections</h3>
      ${projection ? `<div class="projection-grid">${[['PTS',projection.pts_pg],['REB',projection.reb_pg],['AST',projection.ast_pg],['STL',projection.stl_pg],['BLK',projection.blk_pg],['3PM',projection.fg3m_pg],['FG%',projection.fga_pg ? projection.fgm_pg/projection.fga_pg*100 : null],['FT%',projection.fta_pg ? projection.ftm_pg/projection.fta_pg*100 : null]].map(([label,value])=>`<div class="projection-stat"><small>${label}</small><strong>${number(value,1)}${label.includes('%')&&value!=null?'%':''}</strong></div>`).join('')}</div><p class="note-title">${esc(projection.source_name)} · ${dateLabel(projection.as_of_date)} · Per game${projection.date_basis==='received'?' · Received date':''}</p>` : '<div class="drawer-muted">No projection recorded.</div>'}
      ${data.finals.length?`<details><summary class="drawer-section-title">Final roster appearances (${data.finals.length})</summary><div class="panel table-scroll"><table class="drawer-table"><thead><tr><th>SEASON</th><th>FRANCHISE</th><th class="right">FINISH</th><th class="right">RECORDED COST</th></tr></thead><tbody>${data.finals.map(row=>`<tr><td>${esc(seasonLabel(row.season))}</td><td>${esc(row.franchise_sheet)}</td><td class="right">${row.finish}</td><td class="money-cell">${money(row.recorded_cost)}</td></tr>`).join('')}</tbody></table></div></details>`:''}`;
    document.querySelectorAll('.drawer-table thead th').forEach((th,i)=>{
      const index=Array.from(th.parentElement.children).indexOf(th);
      th.setAttribute('aria-sort','none');
      th.innerHTML=`<button class="column-sort" data-detail-column="${index}" data-numeric="${th.classList.contains('right')}">${esc(th.textContent)} <span aria-hidden="true">↕</span></button>`;
    });
    if(section==='comps') { $('#player-comps')?.scrollIntoView({block:'start'}); $('#player-comps')?.focus({preventScroll:true}); }
    else dialog.scrollTop=0;
    const url=new URL(location.href);url.searchParams.set('player',id);window.history.replaceState(null,'',url);
  } catch(error) { if(token===state.playerSequence) $('#player-content').innerHTML=`<h2 id="player-name">Player unavailable</h2><p>${esc(error.message)}</p>`; }
}

function mobileNav(open) {
  $('#sidebar').classList.toggle('open',open);$('#nav-scrim').hidden=!open;$('#menu-button').setAttribute('aria-expanded',String(open));
}

document.addEventListener('click',event=>{
  if(event.target.closest('[data-view]'))mobileNav(false);
  const detailHeader=event.target.closest('[data-detail-column]');
  if(detailHeader){
    const th=detailHeader.parentElement,table=th.closest('table'),index=Number(detailHeader.dataset.detailColumn);
    const direction=th.getAttribute('aria-sort')==='ascending'?'descending':'ascending';
    table.querySelectorAll('th').forEach(h=>h.setAttribute('aria-sort',h===th?direction:'none'));
    const entries=Array.from(table.tBodies[0].rows);
    const value=row=>row.cells[index]?.textContent.trim() || '';
    entries.sort((a,b)=>{
      const av=value(a),bv=value(b);
      return (detailHeader.dataset.numeric==='true'?Number(av.replace(/[$,%\s]/g,''))-Number(bv.replace(/[$,%\s]/g,'')):av.localeCompare(bv))*(direction==='ascending'?1:-1);
    });
    entries.forEach(row=>table.tBodies[0].append(row));
    return;
  }
  const header=event.target.closest('[data-sort-key]');
  if(header){
    const key=header.dataset.sortKey;
    if(header.dataset.sortScope==='history'){
      state.direction=state.sort===key?(state.direction==='asc'?'desc':'asc'):['player','position','team','type'].includes(key)?'asc':'desc';
      state.sort=key;state.page=1;$('#sort-filter').value=key;$('#history-direction').value=state.direction;loadHistoryTable();
    } else if(header.dataset.sortScope==='comps'){
      const c=state.drawerComps,expanded=$('.remaining-comps')?.open;
      const direction=c.sort===key?(c.direction==='asc'?'desc':'asc'):['player','season','distance'].includes(key)?'asc':'desc';
      $('#comparable-content').outerHTML=comparableDetail(c.comps,c.market,key,direction);
      if(expanded && $('.remaining-comps'))$('.remaining-comps').open=true;
    } else {
      $('#data-direction').value=$('#data-sort').value===key?($('#data-direction').value==='asc'?'desc':'asc'):['player','positions','nba_team'].includes(key)?'asc':'desc';
      $('#data-sort').value=key;renderStatsTable();
    }
    return;
  }
  const player=event.target.closest('[data-player]');if(player){openPlayer(player.dataset.player,player.dataset.focus);return;}
  const kind=event.target.closest('[data-kind]');if(kind){state.kind=kind.dataset.kind;state.page=1;document.querySelectorAll('[data-kind]').forEach(button=>{button.classList.toggle('active',button===kind);button.setAttribute('aria-pressed',String(button===kind));});loadHistoryTable();return;}
  const page=event.target.closest('[data-page]');if(page&&!page.disabled){state.page=Number(page.dataset.page);loadHistoryTable();return;}
});
let searchTimer;
document.addEventListener('input',event=>{
  if(event.target.id==='player-search'){state.q=event.target.value;state.page=1;clearTimeout(searchTimer);searchTimer=setTimeout(loadHistoryTable,180);}
  if(event.target.id==='data-search')renderStatsTable();
  if(event.target.id==='keeper-search')keeperCards();
});
document.addEventListener('change',event=>{
  const value=event.target.value;
  if(event.target.id==='season-select'){state.season=value;state.page=1;loadView();}
  if(event.target.id==='team-filter'){state.team=value;state.page=1;loadHistoryTable();}
  if(event.target.id==='sort-filter'){state.sort=value;state.page=1;loadHistoryTable();}
  if(['data-sort','draft-filter','data-position','data-direction','data-columns'].includes(event.target.id))renderStatsTable();
  if(event.target.id==='history-position'){state.position=value;state.page=1;loadHistoryTable();}
  if(event.target.id==='history-direction'){state.direction=value;state.page=1;loadHistoryTable();}
  if(event.target.id==='keeper-sort')keeperCards();
  if(event.target.id==='dataset-select'){if(state.view==='valuations')state.run=value;else state.dataset=value;loadView();}
});
window.addEventListener('hashchange',()=>{
  const view=location.hash.slice(1);state.view=views[view]?view:'history';state.kind='all';state.page=1;state.q='';mobileNav(false);loadView();
});
$('#menu-button').addEventListener('click',()=>mobileNav(!$('#sidebar').classList.contains('open')));
$('#nav-scrim').addEventListener('click',()=>mobileNav(false));
$('#close-player').addEventListener('click',()=>$('#player-dialog').close());
$('#player-dialog').addEventListener('close',()=>{++state.playerSequence;document.body.style.overflow='';const url=new URL(location.href);url.searchParams.delete('player');window.history.replaceState(null,'',url);});
$('#player-dialog').addEventListener('click',event=>{if(event.target===$('#player-dialog')){const rect=event.target.getBoundingClientRect();if(event.clientX<rect.left||event.clientX>rect.right)event.target.close();}});
$('#retry-button').addEventListener('click',()=>initialize());

async function initialize() {
  try {
    state.bootstrap=await api('/api/bootstrap');
    if(!state.season)state.season=state.bootstrap.seasons[0]?.season || 'all';
    $('#season-select').innerHTML=state.bootstrap.seasons.map(row=>`<option value="${esc(row.season)}" ${row.season===state.season?'selected':''}>${esc(seasonLabel(row.season))}</option>`).join('')+`<option value="all" ${state.season==='all'?'selected':''}>All seasons</option>`;
    $('#note-count').textContent=state.bootstrap.issue_count;
    $('#archive-footer').textContent=`${state.bootstrap.seasons.length} seasons · ${number(state.bootstrap.player_count)} player identities`;
    state.view=views[location.hash.slice(1)]?location.hash.slice(1):'history';
    connected();await loadView();
    const player=new URL(location.href).searchParams.get('player');if(player)openPlayer(player);
  } catch(error){setError(error);$('#view-content').innerHTML='<div class="no-results">Your league archive will appear here when the connection is restored.</div>';}
}
initialize();
