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
const state = {view:"history", season:"", team:"", kind:"all", q:"", sort:"price_desc", page:1, bootstrap:null, history:null, dataset:"", run:"", dataRows:[], dataSort:"", sequence:0, tableSequence:0, playerSequence:0};
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
  return `<button class="player-button" data-player="${esc(row.player_id)}"><span class="player-initials">${esc(initials(row.player))}</span><span>${esc(row.player)}${availability}${type?`<small class="mobile-entry-type ${type==='keeper'?'keeper-text':''}">${type==='keeper'?'Keeper':type==='auction'?'Auction':'Final roster'}</small>`:''}</span></button>`;
}

function setNavigation() {
  const view = views[state.view];
  document.title = `${view.title} · Survivor`;
  $("#page-title").innerHTML = `${view.title}<span>.</span>`;
  $("#breadcrumb-view").textContent = view.title;
  $("#page-description").textContent = view.description;
  $("#page-eyebrow").textContent = view.eyebrow;
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
    <section class="panel"><div class="panel-heading"><div><h2 class="panel-title">Where the money goes</h2><p class="panel-subtitle">Players bought at each price point</p></div><span class="mini-label">${esc(title)}</span></div><div class="price-chart">${distributionChart(data)}</div><div class="chart-caption"><span>Every price tier has a role to play.</span><span>Auction purchases only</span></div></section>
    <section class="panel"><div class="panel-heading"><div><h2 class="panel-title">The biggest bids</h2><p class="panel-subtitle">The room paid a premium for these players</p></div>${icon('arrow')}</div><div class="leader-list">${data.leaders.map((row,i) => `<button class="leader" data-player="${esc(row.player_id)}"><span class="leader-number">0${i+1}</span><span class="player-initials">${esc(initials(row.player))}</span><span class="leader-name">${esc(row.player)}<span class="leader-team">${esc(row.franchise_sheet)}${data.season==='all' ? ' · '+esc(seasonLabel(row.season)) : ''}</span></span><span class="leader-price">${money(row.recorded_cost)}</span>${icon('chevron')}</button>`).join('') || '<div class="no-results">No auction records for this season.</div>'}</div></section>
  </div>`;
}

function filtersHTML() {
  const roster = state.view === 'rosters';
  const options = roster ? [['all','Opening rosters'],['final','Final rosters']] : [['all','All prices'],['auction','Auction'],['keeper','Keepers']];
  return `<div class="filter-row"><label class="search-box">${icon('search')}<input id="player-search" type="search" placeholder="Search players…" aria-label="Search players" value="${esc(state.q)}"></label>
    <div class="segmented" aria-label="Entry type">${options.map(([value,label]) => `<button data-kind="${value}" class="${state.kind===value?'active':''}" aria-pressed="${state.kind===value}">${label}</button>`).join('')}</div>
    <select class="select-filter" id="team-filter" aria-label="Franchise"><option value="">All franchises</option>${state.bootstrap.teams.map(team => `<option ${state.team===team?'selected':''} value="${esc(team)}">${esc(team)}</option>`).join('')}</select>
    <select class="select-filter sort-filter" id="sort-filter" aria-label="Sort prices">${[['price_desc','Price: high to low'],['price_asc','Price: low to high'],['player','Player: A–Z'],['season','Most recent'],['team','Franchise: A–Z']].map(([value,label]) => `<option value="${value}" ${state.sort===value?'selected':''}>${label}</option>`).join('')}</select></div>`;
}

function historyPanelHTML() {
  return `<section class="panel history-panel"><div class="table-title-row"><h2 class="table-heading">${state.view==='rosters'?'The roster book':'The price book'} <span id="results-count" class="count-badge">—</span></h2><a id="export-link" class="text-button" href="/api/history.csv">${icon('download')} Export CSV</a></div>${filtersHTML()}<div id="history-table">${loading()}</div></section>
    <p class="table-note">${icon('note')} ${state.view==='rosters' ? 'Franchise labels follow the current workbook sheets. Final rosters are snapshots at elimination or the league win.' : 'Auction prices are winning bids. Keeper costs are retained salaries. Click any player to see the full history.'}</p>`;
}

function queryString() {
  return new URLSearchParams({season:state.season,team:state.team,kind:state.kind,q:state.q,sort:state.sort,page:state.page,limit:30}).toString();
}

function historyTableHTML(data) {
  const start = data.total ? (data.page-1)*data.limit+1 : 0;
  return `<div class="table-scroll"><table class="price-table"><thead><tr><th>PLAYER</th><th>SEASON</th><th>FRANCHISE</th><th>ENTRY TYPE</th><th class="right">PRICE PAID</th><th class="right">CONTRACT YEAR</th><th aria-label="Details"></th></tr></thead><tbody>${data.rows.map(row => `<tr><td class="player-cell">${playerButton(row)}</td><td>${esc(seasonLabel(row.season))}</td><td>${esc(row.franchise_sheet)}</td><td>${badge(row.acquisition_class)}</td><td class="money-cell">${money(row.recorded_cost)}</td><td class="right">${number(row.contract_year_recorded)}</td><td><button class="text-button" data-player="${esc(row.player_id)}" aria-label="View ${esc(row.player)} history">${icon('arrow')}</button></td></tr>`).join('') || '<tr><td colspan="7"><div class="no-results">No players match these filters. Try another name or season.</div></td></tr>'}</tbody></table></div><div class="table-foot"><span>Showing ${number(start)}–${number(Math.min(data.page*data.limit,data.total))} of ${number(data.total)} records</span><div class="pager"><button data-page="${data.page-1}" ${data.page<=1?'disabled':''}>← Previous</button><span>${data.page} / ${Math.max(1,Math.ceil(data.total/data.limit))}</span><button data-page="${data.page+1}" ${data.page*data.limit>=data.total?'disabled':''}>Next →</button></div></div>`;
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
  <div class="status-banner">${icon('check')}<span>All ${data.team_count} teams have their ${data.keepers_per_team} keepers. These ${data.keeper_count} players are excluded when viewing available players on the ${esc(seasonLabel(data.season))} projection and valuation boards.</span></div>
  <section class="panel keeper-toolbar"><div class="filter-row"><label class="search-box">${icon('search')}<input id="keeper-search" type="search" placeholder="Find a team or player…" aria-label="Search keepers"></label><span class="subtle-count" id="keeper-team-count"></span><select id="keeper-sort" class="select-filter" aria-label="Sort keeper teams"><option value="budget">Most money remaining</option><option value="spend">Highest keeper spend</option><option value="team">Team: A–Z</option></select></div></section>
  <div class="keeper-grid" id="keeper-grid"></div>`;
}

function emptyHTML(kind) {
  const valuation = kind === 'valuations';
  const season = seasonLabel(state.bootstrap.target_season);
  return `${state.bootstrap.draft?`<a class="status-banner" href="#keepers">${icon('check')}<span>${state.bootstrap.draft.keeper_count} keepers confirmed · ${money(state.bootstrap.draft.remaining_budget)} left for the auction. View keepers & budgets ${icon('arrow')}</span></a>`:''}<section class="empty-state"><div class="empty-icon">${icon(valuation?'target':'chart')}</div><div class="eyebrow">${season} · ${valuation?'VALUATIONS':'PROJECTIONS'}</div><h2>${valuation?'The next edge is still taking shape.':'A new season needs a fresh forecast.'}</h2><p>${valuation?'No calculated valuations have been loaded yet. Once the model has results, compare survivor value, expected league price and neutral auction value here.':'No projection set has been loaded for this season yet. Once added, you’ll see each player’s expected games, minutes, and all eight categories here.'}</p><a class="solid-button" href="#history">Explore the price history ${icon('arrow')}</a></section>
    <div class="info-grid">${(valuation ? [['01','Survivor value','What a player’s projected contribution is worth across your eight categories.'],['02','Expected auction price','What similar player profiles have cost in your league’s auction room.'],['03','Neutral auction value','A fresh draft with 15 teams, $200 each, no keepers and no category punts.']] : [['01','All eight categories','Points, rebounds, assists, steals, blocks, threes, field goals and free throws.'],['02','Shooting volume matters','Makes and attempts give shooting percentages the context they need.'],['03','Always know the source','Compare dated projection sets and keep track of whose forecast you’re using.']]).map(([n,title,description]) => `<section class="info-tile"><span>${n} /</span><h3>${title}</h3><p>${description}</p></section>`).join('')}</div>`;
}

function statsToolbar(kind) {
  const valuations = kind === 'valuations';
  const sorts = valuations ? [['survivor_score','Survivor value'],['expected_auction_price','Expected league price'],['fair_value','Neutral auction value'],['confirmed_keeper_surplus','Neutral keeper surplus']] : [['pts_pg','Points'],['reb_pg','Rebounds'],['ast_pg','Assists'],['stl_pg','Steals'],['blk_pg','Blocks'],['fg3m_pg','Threes'],['games','Games played']];
  const known = state.dataRows.length ? state.dataRows.every(row=>row.draft_status && row.draft_status!=='unknown') : state.bootstrap.draft?.season===state.dataSeason;
  return `<div class="table-title-row"><h2 class="table-heading">${valuations?'The value board':'The projection board'} <span class="count-badge" id="data-count">${number(state.dataRows.length)}</span></h2>${valuations?`<a id="values-export" class="text-button" href="/api/valuations.csv?run=${encodeURIComponent(state.activeRun)}">${icon('download')} Export CSV</a>`:'<span class="subtle-count">Per game · except GP</span>'}</div>
    <div class="filter-row"><label class="search-box">${icon('search')}<input id="data-search" type="search" placeholder="Search players…" aria-label="Search ${kind}"></label>
    <select id="draft-filter" class="select-filter" aria-label="Draft availability"><option value="available" ${known?'selected':''}>Available to draft</option><option value="all" ${known?'':'selected'}>All players</option><option value="kept">Keepers only</option></select>
    <select id="data-sort" class="select-filter" aria-label="Sort ${kind}">${sorts.map(([key,label])=>`<option value="${key}">${label}: high to low</option>`).join('')}</select></div>
    ${known?'':`<p class="availability-note">No confirmed keeper list is available for ${esc(seasonLabel(state.dataSeason))}. Draft availability is unknown.</p>`}`;
}

function datasetHeading(metadata,kind) {
  const valuations = kind === 'valuations';
  const options = valuations ? state.bootstrap.runs : state.bootstrap.datasets.filter(d=>d.kind==='projection');
  const key = valuations ? 'run_id' : 'dataset_id';
  const available = options.some(d=>d[key]===metadata[key]) ? options : [metadata,...options];
  const sourceLink = /^https:\/\//.test(metadata.source_url || '') ? ` · <a href="${esc(metadata.source_url)}" target="_blank" rel="noopener noreferrer">View source</a>` : '';
  return `<div class="dataset-header"><div><strong>${esc(seasonLabel(metadata.season))} · ${esc(metadata.source_name)}</strong><small>${valuations ? 'Valuation run: '+dateLabel(metadata.created_at) : 'Snapshot: '+dateLabel(metadata.as_of_date)+' · '+esc(metadata.coverage.replaceAll('_',' '))}${sourceLink}</small></div><select class="select-filter" id="dataset-select" aria-label="${valuations?'Valuation run':'Projection set'}">${available.map(d=>`<option value="${esc(d[key])}" ${d[key]===metadata[key]?'selected':''}>${esc(seasonLabel(d.season))} · ${esc(valuations?d.model_version:d.source_name)} · ${dateLabel(valuations?d.created_at:d.as_of_date)}</option>`).join('')}</select></div>${!valuations && metadata.notes ? `<p class="table-note" id="projection-source-note">${icon('note')}${esc(metadata.notes)}</p>` : ''}`;
}

function auctionMarket(validation) {
  const markets=validation.auction_contexts;
  if (!markets?.length) return '';
  const current=markets.at(-1);
  return `<details class="panel model-details" id="auction-market"><summary>Auction room: ${preciseMoney(current.average_team_budget)} per team · ${current.available_top_counts['30']} of the top 30 available</summary>
    <p>${money(current.remaining_budget)} is available for ${current.open_slots} roster slots. Team budgets range from ${money(current.minimum_team_budget)} to ${money(current.maximum_team_budget)}. Top-player counts use eight-category per-game strength.</p>
    <p>${validation.supply_adjustment_selected?'The price model adjusts historical purchases for both available money and available talent.':'Prices adjust for money left after keepers. An additional available-talent adjustment was tested but did not beat the selected model on development years, so it is not applied to the displayed prices.'} Budget distribution is shown for context; individual bidding behavior is not simulated.</p>
    <div class="table-scroll"><table><thead><tr><th>SEASON / PLAYERS</th><th class="right">AVG BUDGET</th><th class="right">RANGE</th><th class="right">TOP 10 AVAILABLE</th><th class="right">TOP 30 AVAILABLE</th></tr></thead><tbody>${[...markets].reverse().map(m=>`<tr><td><details><summary>${esc(seasonLabel(m.season))}</summary><p>${esc(m.basis)}</p><p>Available from the top 30: ${m.available_top_players.map(p=>esc(p.player)).join(', ') || 'None'}.</p><p>${m.teams.map(t=>`${esc(t.franchise)} ${money(t.remaining_budget)}`).join(' · ')}</p>${m.unmatched_keeper_ids.length?`<p>${m.unmatched_keeper_ids.length} keepers lack a qualifying prior-season statistical profile.</p>`:''}</details></td><td class="right">${preciseMoney(m.average_team_budget)}</td><td class="right">${money(m.minimum_team_budget)}–${money(m.maximum_team_budget)}</td><td class="right">${m.available_top_counts['10']}</td><td class="right">${m.available_top_counts['30']}</td></tr>`).join('')}</tbody></table></div>
    <p>Historical budgets assume the same $200 cap. Historical talent uses the preceding season's stats and recorded keeper exclusions; it cannot reconstruct offseason changes or rookie expectations.</p>
  </details>`;
}

function valuationMethod(metadata) {
  const v=metadata.validation;
  if (!v?.neutral_allocation) return '';
  const settings=metadata.settings;
  const test=v.holdout[v.selected_model];
  const central=v.survivor_scenarios.central;
  const cards=[['01','Survivor value','Eight-category strength per game, multiplied by useful games above an improving replacement pool. Higher is better.'],
    ['02','Expected league price','Learned from your actual auction purchases. The band reflects historical pricing errors; keeper prices are hypothetical.'],
    ['03','Neutral auction value',`${money(v.neutral_allocation.budget)} across ${v.neutral_allocation.slots} roster slots. Fifteen equal-budget teams, no keepers and no category punts.`]];
  return `<div class="info-grid valuation-guide">${cards.map(([n,title,description])=>`<section class="info-tile"><span>${n} /</span><h3>${title}</h3><p>${description}</p></section>`).join('')}</div>
    ${auctionMarket(v)}<details class="panel model-details" id="valuation-method"><summary>Formula, assumptions & historical checks</summary>
      <p><strong>Survivor value</strong> = sum across stages of positive per-game advantage over replacement × useful games ÷ 82. Percentages use makes minus pool percentage × attempts; all eight categories receive equal weight after standardization.</p>
      <p><strong>Neutral dollars</strong> reserve ${money(v.neutral_allocation.minimum_bid)} per roster slot, then divide ${money(v.neutral_allocation.discretionary_budget)} in proportion to survivor value. These dollars are independent of historical bidding habits. The $1 floor is an assumption based on recorded purchases.</p>
      <p><strong>Elimination scenario:</strong> first cut at week 5.5, then every 1.5 weeks until two teams remain. The rosterable pool shrinks from 225 to 30 players. Published games are spread uniformly and paced to the 1,000-game cap. Neutral turnover uses about ${number(central.expected_moves_used,1)} of 100 moves. Regular waivers and the reverse-order sniper draft are recorded rules; actual winners and the 15-year order cycle are not simulated without order history.</p>
      <p>This assumes reaching the final and cumulative stats. Exact cut dates, category resets, positions, lineup constraints and dated injury returns are unresolved. Faster/slower schedules appear in each player’s detail; they are scenarios, not confidence intervals.</p>
      <p><strong>Historical price check:</strong> ${number(test.n)} purchases in the last three seasons; average absolute error ${money(test.mae)} versus ${money(v.holdout.mean.mae)} for a league-average baseline. For $30+ purchases the error was ${money(v.holdout_by_actual_price_tier['30_plus'].mae)}; stars were underpriced on average. The nominal 80% price band covered ${number(v.holdout_interval_coverage*100,1)}% of held-out purchases.</p>
      ${v.market_context?`<p><strong>Comparable pricing:</strong> ${settings.neighbors} similar historical purchases adjust the overall auction estimate. Weight = recency ÷ (distance + ${settings.comp_distance_offset})<sup>${settings.comp_distance_power}</sup>. Distance includes all eight categories and recorded season age where available. Half of the weighted difference between actual and modeled comp prices is applied to the target player. Age is learned from auction history; it does not impose a youth premium or predict future keeper savings.</p><p>Recorded age context covers ${v.current_age_coverage} of ${v.projected_players} projected players. Missing ages use the statistics-only counterpart. The earlier auction model’s average error was ${money(v.holdout.ridge.mae)} on these same seasons. Candidate selection uses older development years; these recent seasons have been reviewed before and are reused comparisons, not fresh independent validation.</p>`:''}
      <p>${number(v.training_rows)} matched purchases used for the current fit; ${number(v.unmatched_or_low_sample_rows)} lack a sufficient prior-year sample. Each historical test uses only earlier auctions and preceding-season actuals. These checks do not validate the provider’s current projections, rookies, or the neutral survivor formula.</p>
      <p>Current draft context: ${money(v.remaining_budget)} left for ${v.remaining_slots} open slots. Expected prices are individual conditional estimates and do not sum to an auction allocation. Neutral dollars always use the clean slate.</p>
      <p class="model-source">${esc(metadata.model_version)} · ${esc(settings.survivor?.season_length_basis || '')}</p>
    </details>`;
}

function valuationDetail(valuation, keeper) {
  const c=object(valuation.category_values_json);
  const comps=object(valuation.comps_json);
  const z=c.rate_z || {};
  const max=Math.max(1,...Object.values(z).map(Math.abs));
  return `<div class="drawer-metrics valuation-metrics"><div><small>SURVIVOR VALUE</small><strong>${number(c.score,2)}</strong></div><div><small>EXPECTED LEAGUE PRICE</small><strong>${money(valuation.expected_auction_price)}</strong></div><div><small>NEUTRAL AUCTION VALUE</small><strong>${money(valuation.fair_value)}</strong></div></div>
    <p class="drawer-muted">Historical price band: ${money(valuation.lower_estimate)}–${money(valuation.upper_estimate)}${keeper && keeper.season===valuation.season?' · Hypothetical price if available':''}. Neutral value: 15 teams, $200 each, no keepers.${keeper && keeper.season===valuation.season?` Neutral keeper surplus: ${money(valuation.fair_value-keeper.keeper_cost)}.`:''}</p>
    ${Array.isArray(comps)&&comps.length?comparableDetail(comps,c.market):''}
    ${c.useful_games!=null?`<p class="drawer-muted">${number(c.useful_games,1)} useful games above replacement in the central scenario, from ${number(c.games,1)} published games. Useful for ${number(c.useful_season_fraction*100,0)}% of the modeled season; this is a scenario, not a retention probability.</p>`:''}
    ${Object.keys(z).length?`<h3 class="drawer-section-title">Eight-category contribution per game</h3><div class="category-bars" role="img" aria-label="Standardized category contributions; positive is above the reference pool average">${Object.entries(z).map(([cat,value])=>`<div class="category-row"><span>${esc(cat)}</span><div class="category-track"><i class="${value<0?'negative':''}" style="left:${value<0?50-Math.abs(value)/max*50:50}%;width:${Math.abs(value)/max*50}%"></i></div><strong>${value>0?'+':''}${number(value,2)}</strong></div>`).join('')}</div><p class="note-title">Volume-weighted shooting impact · Same reference scale for every category</p>`:''}
    ${c.scenario_values?`<h3 class="drawer-section-title">Elimination schedule sensitivity</h3><div class="projection-grid scenario-grid">${Object.entries(c.scenario_values).map(([name,value])=>`<div class="projection-stat"><small>${esc(name.toUpperCase())} CUTS</small><strong>${money(value)}</strong></div>`).join('')}</div><p class="note-title">Neutral dollars under three illustrative schedules; not a statistical error band.</p>`:''}
    ${valuation.risk_notes?`<p class="drawer-muted">${esc(valuation.risk_notes)}</p>`:''}`;
}

function comparableDetail(comps,market) {
  if (!market) return `<h3 class="drawer-section-title">Closest historical profiles</h3><p class="drawer-muted">Saved comparisons from an earlier model; individual weights were not recorded.</p>${comparableTable(comps,false)}`;
  const ordered=[...comps].sort((a,b)=>(b.weight || 0)-(a.weight || 0));
  const weighted=market.comp_adjustment!=null;
  const age=market.age_source;
  const first=ordered.slice(0,5),rest=ordered.slice(5);
  return `<h3 class="drawer-section-title" id="player-comps" tabindex="-1">How the comps affect this price</h3>
    ${weighted?`<div class="comp-price-breakdown"><span>Overall auction estimate <strong>${preciseMoney(market.base_price)}</strong></span><span>Weighted comp adjustment <strong>${market.comp_adjustment>=0?'+':''}${preciseMoney(market.comp_adjustment)}</strong></span></div><p class="drawer-muted">The model applies ${number(market.correction_share*100)}% of the weighted difference between each comp’s actual and modeled price, after adjusting for that auction’s budget${market.supply_adjusted?' and available talent':''}. The final estimate respects the $1–$200 bounds.</p>`:'<p class="drawer-muted">These profiles explain similarity; the selected model does not use a local price adjustment.</p>'}
    <p class="drawer-muted">Closer matches receive more weight, and recent auctions also count more. ${market.age_in_model?`Similarity includes recorded season age; this player’s target-season age is ${number(age?.target_season_age)}, advanced from ${esc(seasonLabel(age?.season))} source data.`:'This comparison uses statistics only; no missing age is guessed.'} ${market.comp_count} comps carry the weight of approximately ${number(market.effective_comps,1)} equally weighted records. Repeated seasons of the same player are not independent evidence.</p>
    <p class="drawer-muted">Shown by weight. Paid = original winning bid. Adjustment = that comp’s contribution to the final price adjustment. Shares total 100% across all ${market.comp_count} comps.</p>
    ${comparableTable(first,true)}
    ${rest.length?`<details class="remaining-comps"><summary class="drawer-section-title">Show ${rest.length} more comps (${number(rest.reduce((sum,c)=>sum+c.weight,0)*100,1)}% of weight)</summary>${comparableTable(rest,true)}</details>`:''}`;
}

function comparableTable(comps,weighted) {
  return `<div class="panel table-scroll"><table class="comps-table"><thead><tr><th>PLAYER / STATS</th><th>AUCTION</th><th class="right">PAID</th><th class="right">DISTANCE</th>${weighted?'<th class="right">WEIGHT</th><th class="right">ADJUSTMENT</th>':''}</tr></thead><tbody>${comps.map(comp=>`<tr><td>${playerButton(comp)}<small>${esc(seasonLabel(comp.stats_season))} stats${comp.target_season_age!=null?' · age '+number(comp.target_season_age)+' at auction':''}</small></td><td>${esc(seasonLabel(comp.season))}<small>${esc(comp.franchise)}</small>${comp.average_team_budget!=null?`<small>Avg budget ${preciseMoney(comp.average_team_budget)} · ${comp.available_top_30}/30 available</small>`:''}</td><td class="money-cell">${money(comp.actual_price)}</td><td class="right" title="Combined profile distance; smaller is closer">${number(comp.distance,2)}</td>${weighted?`<td class="right comp-weight">${number(comp.weight*100,1)}%</td><td class="right" title="Original bid adjusted to the selected market basis: ${preciseMoney(comp.budget_adjusted_price)}; model price for that profile: ${preciseMoney(comp.model_price)}">${comp.price_adjustment>0?'+':''}${preciseMoney(comp.price_adjustment)}</td>`:''}</tr>`).join('')}</tbody></table></div>`;
}

function renderStatsTable() {
  const valuations = state.view==='valuations';
  const q = ($('#data-search')?.value || '').toLowerCase();
  const sort = $('#data-sort')?.value || (valuations?'fair_value':'pts_pg');
  const availability = $('#draft-filter')?.value || 'all';
  const data = state.dataRows.filter(row => row.player.toLowerCase().includes(q) && (availability==='all' || row.draft_status===availability)).sort((a,b)=>(b[sort]??-Infinity)-(a[sort]??-Infinity) || a.player.localeCompare(b.player));
  $('#data-count').textContent = number(data.length);
  const headings = valuations ? ['PLAYER','SURVIVOR VALUE','EXPECTED LEAGUE PRICE','NEUTRAL AUCTION VALUE'] : ['PLAYER','TEAM','POS','GP','MIN','PTS','REB','AST','STL','BLK','3PM','FG%','FT%'];
  if (valuations) $('#values-export').href = `/api/valuations.csv?run=${encodeURIComponent(state.activeRun)}&availability=${encodeURIComponent(availability)}`;
  $('#data-table').innerHTML = `<div class="table-scroll"><table class="${valuations?'value-table':''}"><thead><tr>${headings.map((h,i)=>`<th ${i>0?'class="right"':''}>${h}</th>`).join('')}</tr></thead><tbody>${data.map(row=>`<tr><td class="player-cell">${playerButton(row)}</td>${valuations ? `<td class="money-cell" data-label="Survivor value">${number(row.survivor_score,2)}<small>value score</small></td><td class="money-cell" data-label="League price"><button class="price-comps-button" data-player="${esc(row.player_id)}" data-focus="comps" aria-label="View price comps for ${esc(row.player)}">${money(row.expected_auction_price)}<span>View comps</span></button><small>${money(row.lower_estimate)}–${money(row.upper_estimate)} price band${row.draft_status==='kept'?' · if available':''}</small></td><td class="money-cell" data-label="Neutral value">${money(row.fair_value)}<small>15 teams · no keepers</small></td>` : `<td class="right">${esc(row.nba_team || '—')}</td><td class="right">${esc(row.positions || '—')}</td>${['games','minutes_pg','pts_pg','reb_pg','ast_pg','stl_pg','blk_pg','fg3m_pg'].map(k=>`<td class="right">${number(row[k],k==='games'?0:1)}</td>`).join('')}<td class="right" title="${number(row.fgm_pg,1)} makes / ${number(row.fga_pg,1)} attempts">${row.fga_pg ? number(row.fgm_pg/row.fga_pg*100,1)+'%' : '—'}</td><td class="right" title="${number(row.ftm_pg,1)} makes / ${number(row.fta_pg,1)} attempts">${row.fta_pg ? number(row.ftm_pg/row.fta_pg*100,1)+'%' : '—'}</td>`}</tr>`).join('') || `<tr><td colspan="${headings.length}"><div class="no-results">No matching players.</div></td></tr>`}</tbody></table></div>`;
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
      } else heading = `<div class="status-banner roster-note">${icon('users')}<span>Opening rosters show draft-day purchases and keepers. Final rosters show who each franchise held when its season ended.</span></div>`;
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
        state.dataRows = data.rows.map(row=>({...row,survivor_score:object(row.category_values_json).score,confirmed_keeper_surplus:row.confirmed_keeper_cost!=null && row.fair_value!=null ? row.fair_value-row.confirmed_keeper_cost : null}));
        state.dataSeason = metadata.season;
        if (valuations) state.activeRun=metadata.run_id;
        else state.activeDataset=metadata.dataset_id;
        $('#view-content').innerHTML = datasetHeading(metadata,state.view)+(valuations?valuationMethod(metadata):'')+`<section class="panel history-panel">${statsToolbar(state.view)}<div id="data-table"></div></section>`+`<p class="table-note">${icon('note')}${valuations?'Click a player for category strengths, actual historical comps, schedule sensitivity and keeper surplus. Neutral dollars are a model estimate, not a personalized bid ceiling.':'FG% and FT% are calculated from projected makes and attempts. Hover a percentage to see its shooting volume.'}</p>`;
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
    const valuation=state.view==='valuations' && state.activeRun ? data.valuations.find(v=>v.run_id===state.activeRun) : data.valuations[0];
    const projectionID=state.view==='projections'?state.activeDataset:valuation?.projection_dataset_id;
    const projection=projectionID ? data.projections.find(p=>p.dataset_id===projectionID) : data.projections[0];
    const keeper=data.confirmed_keeper;
    $('#player-content').innerHTML=`<div class="player-title"><span class="player-initials">${esc(initials(data.display_name))}</span><div><h2 id="player-name">${esc(data.display_name)}</h2><p>${number(data.history.length)} recorded opening seasons · ${number(auctions.length)} auction purchases</p></div></div>
      ${keeper?`<div class="status-banner confirmed-keeper">${icon('check')}<span><strong>${esc(seasonLabel(keeper.season))} keeper · ${esc(keeper.franchise)} · ${money(keeper.keeper_cost)}</strong><br>Unavailable to draft this season.</span></div>`:''}
      <h3 class="drawer-section-title">${valuation?esc(seasonLabel(valuation.season))+' ':''}Valuation</h3>${valuation ? valuationDetail(valuation,keeper) : '<div class="drawer-muted">No calculated valuation is available yet. Historical costs above are recorded prices, not forecasts.</div>'}
      <div class="drawer-metrics"><div><small>LAST RECORDED COST</small><strong>${money(last?.recorded_cost)}</strong></div><div><small>HIGHEST AUCTION BID</small><strong>${money(peak)}</strong></div><div><small>LATEST ENTRY</small><strong style="font-size:18px;margin-top:10px">${last ? esc(seasonLabel(last.season)) : '—'}</strong></div></div>
      <section class="panel drawer-chart"><div class="panel-heading"><h3 class="panel-title">A history in dollars</h3><div class="legend"><span><i></i> Auction</span><span><i class="keeper"></i> Keeper</span></div></div>${playerChart(data.history)}</section>
      <h3 class="drawer-section-title">Opening price history</h3><div class="panel table-scroll"><table class="drawer-table"><thead><tr><th>SEASON</th><th>FRANCHISE</th><th>TYPE</th><th class="right">COST</th></tr></thead><tbody>${[...data.history].reverse().map(row=>`<tr><td>${esc(seasonLabel(row.season))}</td><td>${esc(row.franchise_sheet)}</td><td>${badge(row.acquisition_class)}</td><td class="money-cell">${money(row.recorded_cost)}</td></tr>`).join('') || '<tr><td colspan="4">No opening history recorded.</td></tr>'}</tbody></table></div>
      <h3 class="drawer-section-title">${projection ? esc(seasonLabel(projection.season)) : esc(seasonLabel(state.bootstrap.target_season))} projections</h3>
      ${projection ? `<div class="projection-grid">${[['PTS',projection.pts_pg],['REB',projection.reb_pg],['AST',projection.ast_pg],['STL',projection.stl_pg],['BLK',projection.blk_pg],['3PM',projection.fg3m_pg],['FG%',projection.fga_pg ? projection.fgm_pg/projection.fga_pg*100 : null],['FT%',projection.fta_pg ? projection.ftm_pg/projection.fta_pg*100 : null]].map(([label,value])=>`<div class="projection-stat"><small>${label}</small><strong>${number(value,1)}${label.includes('%')&&value!=null?'%':''}</strong></div>`).join('')}</div><p class="note-title">${esc(projection.source_name)} · ${dateLabel(projection.as_of_date)} · Per game</p>` : '<div class="drawer-muted">No projections have been loaded for this player yet.</div>'}
      ${data.finals.length?`<details><summary class="drawer-section-title">Final roster appearances (${data.finals.length})</summary><div class="panel table-scroll"><table class="drawer-table"><thead><tr><th>SEASON</th><th>FRANCHISE</th><th class="right">FINISH</th><th class="right">RECORDED COST</th></tr></thead><tbody>${data.finals.map(row=>`<tr><td>${esc(seasonLabel(row.season))}</td><td>${esc(row.franchise_sheet)}</td><td class="right">${row.finish}</td><td class="money-cell">${money(row.recorded_cost)}</td></tr>`).join('')}</tbody></table></div></details>`:''}`;
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
  if(event.target.id==='data-sort' || event.target.id==='draft-filter')renderStatsTable();
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
