"""Read-only league dashboard served on Railway's PORT."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import csv
from datetime import datetime, timezone
import io
import json
import logging
import os
import re
from pathlib import Path
import threading

from flask import Flask, Response, jsonify, render_template, request
from werkzeug.exceptions import BadRequest, HTTPException

from survivor.catalog import build_catalog, name_key, rows
from survivor.database import Postgres, preview_database, railway_database
from survivor.keepers import annotate_availability, keeper_summary, sync_bundled_keepers
from survivor.bundled_stats import sync_bundled_stats, sync_bundled_projections
from survivor.valuation import sync_valuations
from survivor.historical_projections import archive_metadata, stored_archives
from survivor.draft_targets import TARGET_FIELDS,VALUE_FILTERS,PROFILE_FILTERS,target_metrics,target_matches

ROOT = Path(__file__).resolve().parents[1]
RULES = json.loads((ROOT / 'config/league.json').read_text(encoding='utf-8'))
STAT_COLUMNS = ('games','minutes_pg','pts_pg','reb_pg','ast_pg','stl_pg','blk_pg','fg3m_pg','fgm_pg','fga_pg','ftm_pg','fta_pg')
TABLE_SORTS = {'player','positions','nba_team','survivor_score','expected_auction_price','fair_value',
               'confirmed_keeper_surplus','fg_pct','ft_pct',*STAT_COLUMNS,
               'value_gap','value_gap_pct','positive_categories','category_floor','fg_impact','ft_impact','shooting_floor'}
POSITIONS = {'PG','SG','SF','PF','C','G','F','unknown'}


def position_matches(positions, selected):
    tokens = set(re.findall(r'[A-Z]+', (positions or '').upper()))
    if not selected:
        return True
    if selected=='unknown':
        return not tokens
    return bool(tokens & ({'G','PG','SG'} if selected=='G' else {'F','SF','PF'} if selected=='F' else {selected}))


def table_metrics(entries):
    for row in entries:
        details = json.loads(row.get('category_values_json') or '{}')
        row['survivor_score'] = details.get('score')
        row['confirmed_keeper_surplus'] = row['fair_value']-row['confirmed_keeper_cost'] if row.get('fair_value') is not None and row.get('confirmed_keeper_cost') is not None else None
        row['fg_pct'] = row['fgm_pg']/row['fga_pg'] if row.get('fga_pg') else None
        row['ft_pct'] = row['ftm_pg']/row['fta_pg'] if row.get('fta_pg') else None
        row.update(target_metrics(row))
    return entries


def create_app(preview_db=None):
    app = Flask(__name__, template_folder='web/templates', static_folder='web/static', static_url_path='/static')
    app.json.sort_keys = False
    preview_lock = threading.RLock()

    @contextmanager
    def database():
        if preview_db is not None:
            with preview_lock:
                yield preview_db
            return
        url = os.environ.get('DATABASE_URL')
        if not url:
            raise RuntimeError('Database unavailable')
        db = Postgres(url, initialize=False, readonly=True)
        try:
            yield db
        finally:
            db.close()

    @app.after_request
    def response_headers(response):
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'same-origin'
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'"
        if request.path.startswith('/api/') or request.path == '/health':
            response.headers['Cache-Control'] = 'no-store'
        return response

    @app.errorhandler(Exception)
    def failure(error):
        if isinstance(error, HTTPException):
            return jsonify(error=error.description), error.code
        app.logger.error('Dashboard request failed: %s', type(error).__name__)
        return jsonify(error='The league database is temporarily unavailable. Please try again.'), 503

    @app.get('/')
    def home():
        return render_template('index.html')

    @app.get('/health')
    def health():
        with database() as db:
            db.execute('SELECT COUNT(*) FROM source_workbooks').fetchone()
        return jsonify(status='ok')

    @app.get('/api/bootstrap')
    def bootstrap():
        with database() as db:
            seasons = rows(db, 'SELECT season,league_year FROM seasons ORDER BY start_year DESC')
            teams = [r[0] for r in db.execute('SELECT DISTINCT franchise_sheet FROM team_seasons ORDER BY franchise_sheet')]
            datasets = rows(db, "SELECT d.dataset_id,d.kind,d.season,d.source_name,d.source_url,d.as_of_date,d.coverage,COUNT(p.player_id) AS players FROM stat_datasets d LEFT JOIN player_stats p USING(dataset_id) GROUP BY d.dataset_id ORDER BY d.as_of_date DESC,d.imported_at DESC")
            datasets += [archive_metadata(a) for a in stored_archives(db)]
            runs = rows(db, "SELECT r.run_id,r.created_at,r.model_version,r.projection_dataset_id,d.season,d.source_name,COUNT(v.player_id) AS players FROM valuation_runs r JOIN stat_datasets d ON d.dataset_id=r.projection_dataset_id LEFT JOIN projected_values v USING(run_id) GROUP BY r.run_id,d.season,d.source_name ORDER BY r.created_at DESC")
            imported = db.execute('SELECT MAX(imported_at) FROM source_workbooks').fetchone()[0]
            player_count = db.execute('SELECT COUNT(*) FROM players').fetchone()[0]
            issue_count = db.execute("SELECT COUNT(*) FROM data_issues WHERE severity='review'").fetchone()[0]
            draft = keeper_summary(db, RULES['target_season'])
        return jsonify(seasons=seasons, teams=teams, datasets=datasets, runs=runs,
                       imported_at=imported, player_count=player_count, issue_count=issue_count,
                       target_season=RULES['target_season'], categories=RULES['categories'],
                       team_count=RULES['teams'], draft=draft, preview=preview_db is not None)

    @app.get('/api/keepers')
    def keepers():
        with database() as db:
            summary = keeper_summary(db, request.args.get('season', RULES['target_season']))
        return jsonify(draft=summary)

    def available_entries(db, entries, season):
        availability = request.args.get('availability', 'all')
        if availability not in ('all', 'available', 'kept'):
            raise BadRequest('Unknown draft availability filter.')
        annotate_availability(db, entries, season)
        return entries if availability == 'all' else [row for row in entries if row['draft_status'] == availability]

    def table_entries(entries, default_sort):
        position = request.args.get('position','')
        if position and position not in POSITIONS:
            raise BadRequest('Unknown position filter.')
        sort = request.args.get('sort',default_sort)
        direction = request.args.get('direction','desc')
        if sort not in TABLE_SORTS or direction not in ('asc','desc'):
            raise BadRequest('Unknown table sort.')
        query = name_key(request.args.get('q','')[:100])
        value=request.args.get('value','all');profile=request.args.get('profile','all')
        if value not in VALUE_FILTERS or profile not in PROFILE_FILTERS:
            raise BadRequest('Unknown draft value or category profile filter.')
        entries = [r for r in table_metrics(entries) if position_matches(r.get('positions'),position)
                   and (not query or query in name_key(r['player'])) and target_matches(r,value,profile)]
        # Keep absent statistics/values last in both directions.
        present = sorted((r for r in entries if r.get(sort) is not None),key=lambda r:r['player'])
        present.sort(key=lambda r:r[sort].casefold() if isinstance(r[sort],str) else r[sort],reverse=direction=='desc')
        return present+sorted((r for r in entries if r.get(sort) is None),key=lambda r:r['player'])

    def selected_season(db):
        season = request.args.get('season')
        if season:
            return season
        latest = db.execute('SELECT season FROM seasons ORDER BY start_year DESC LIMIT 1').fetchone()
        return latest[0] if latest else ''

    @app.get('/api/overview')
    def overview():
        with database() as db:
            season = selected_season(db)
            condition, parameters = ('1=1', ()) if season == 'all' else ('season=?', (season,))
            totals = rows(db, f"SELECT COUNT(*) AS opening_players,COALESCE(SUM(recorded_cost),0) AS opening_spend,SUM(CASE WHEN acquisition_class='keeper' THEN 1 ELSE 0 END) AS keepers,COALESCE(SUM(CASE WHEN acquisition_class='keeper' THEN recorded_cost ELSE 0 END),0) AS keeper_spend FROM roster_history WHERE stage='opening' AND {condition}", parameters)[0]
            sales = rows(db, f'SELECT COUNT(*) AS auction_players,COALESCE(SUM(recorded_cost),0) AS auction_spend,COALESCE(MAX(recorded_cost),0) AS top_bid FROM auction_sales WHERE {condition}', parameters)[0]
            leaders = rows(db, f'SELECT season,player,player_id,franchise_sheet,recorded_cost FROM auction_sales WHERE {condition} ORDER BY recorded_cost DESC,player LIMIT 5', parameters)
            trend = rows(db, "SELECT season,COUNT(*) AS players,SUM(recorded_cost) AS spend,AVG(recorded_cost) AS average_price,MAX(recorded_cost) AS top_bid FROM auction_sales GROUP BY season ORDER BY season")
            distribution = rows(db, f"SELECT CASE WHEN recorded_cost<=5 THEN '1-5' WHEN recorded_cost<=15 THEN '6-15' WHEN recorded_cost<=30 THEN '16-30' WHEN recorded_cost<=50 THEN '31-50' ELSE '51+' END AS band,COUNT(*) AS players FROM auction_sales WHERE {condition} GROUP BY band", parameters)
        return jsonify(season=season, **totals, **sales, leaders=leaders, trend=trend, distribution=distribution)

    def history_query(db):
        season = selected_season(db)
        kind = request.args.get('kind', 'all')
        if kind not in ('all', 'auction', 'keeper', 'final'):
            raise ValueError('Invalid entry type')
        table = 'auction_sales' if kind == 'auction' else 'roster_history'
        # Same-season recorded NBA positions; never use today's eligibility for old rosters.
        table = f'''(SELECT h.*,s.positions FROM {table} h
            LEFT JOIN player_stats s ON s.player_id=h.player_id AND s.dataset_id=(
                SELECT dataset_id FROM stat_datasets WHERE kind='actual' AND season=h.season
                ORDER BY as_of_date DESC,imported_at DESC,dataset_id LIMIT 1)) history_rows'''
        conditions, parameters = [], []
        if kind != 'auction':
            conditions.append("stage='final'" if kind == 'final' else "stage='opening'")
        if kind == 'keeper':
            conditions.append("acquisition_class='keeper'")
        if season != 'all':
            conditions.append('season=?')
            parameters.append(season)
        team = request.args.get('team', '')
        if team:
            conditions.append('franchise_sheet=?')
            parameters.append(team)
        query = name_key(request.args.get('q', '')[:100])
        if query:
            conditions.append('player_id LIKE ?')
            parameters.append('%' + query + '%')
        position = request.args.get('position','')
        if position and position not in POSITIONS:
            raise BadRequest('Unknown position filter.')
        if position=='unknown':
            conditions.append("COALESCE(positions,'')=''")
        elif position:
            tokens = ('G','PG','SG') if position=='G' else ('F','SF','PF') if position=='F' else (position,)
            normalized = "(',' || REPLACE(REPLACE(REPLACE(COALESCE(positions,''),'-',','),'/',','),' ','') || ',')"
            conditions.append('('+' OR '.join(normalized+' LIKE ?' for _ in tokens)+')')
            parameters.extend('%,'+token+',%' for token in tokens)
        order = {'price_desc': 'recorded_cost DESC,player,season DESC',
                 'price_asc': 'recorded_cost ASC,player,season DESC',
                 'player': 'player,season DESC', 'season': 'season DESC,recorded_cost DESC,player',
                 'team': 'franchise_sheet,recorded_cost DESC,player'}.get(request.args.get('sort'), 'recorded_cost DESC,player,season DESC')
        column = {'player':'player','season':'season','team':'franchise_sheet','position':'positions',
                  'type':'acquisition_class','price':'recorded_cost','contract':'contract_year_recorded'}.get(request.args.get('sort'))
        if column:
            direction = request.args.get('direction','asc' if column in ('player','franchise_sheet','positions','acquisition_class') else 'desc')
            if direction not in ('asc','desc'):
                raise BadRequest('Unknown table sort direction.')
            order = f'{column} {direction} NULLS LAST,player,season DESC'
        where = ' AND '.join(conditions) or '1=1'
        return table, where, parameters, order

    @app.get('/api/history')
    def history():
        try:
            limit = max(1, min(100, int(request.args.get('limit', 30))))
            page = max(1, int(request.args.get('page', 1)))
        except ValueError:
            return jsonify(error='Page and limit must be integers.'), 400
        with database() as db:
            try:
                table, where, parameters, order = history_query(db)
            except ValueError:
                return jsonify(error='Unknown entry type.'), 400
            count = db.execute(f'SELECT COUNT(*) FROM {table} WHERE {where}', parameters).fetchone()[0]
            page = min(page, max(1, (count + limit - 1) // limit))
            entries = rows(db, f'SELECT season,player,player_id,positions,franchise_sheet,recorded_cost,contract_year_recorded,acquisition_class,finish,source_cell FROM {table} WHERE {where} ORDER BY {order} LIMIT ? OFFSET ?', (*parameters, limit, (page-1)*limit))
        return jsonify(rows=entries, total=count, page=page, limit=limit)

    @app.get('/api/history.csv')
    def history_csv():
        with database() as db:
            try:
                table, where, parameters, order = history_query(db)
            except ValueError:
                return jsonify(error='Unknown entry type.'), 400
            entries = rows(db, f'SELECT season,player,franchise_sheet,recorded_cost,acquisition_class,contract_year_recorded,source_cell,positions FROM {table} WHERE {where} ORDER BY {order}', parameters)
        stream = io.StringIO(newline='')
        writer = csv.writer(stream)
        writer.writerow(['Season','Player','Franchise','Recorded cost','Entry type','Contract year','Source cell','Position'])
        for row in entries:
            # Prevent spreadsheet programs from executing source text as formulas.
            writer.writerow(["'"+v if isinstance(v,str) and v.startswith(('=','+','-','@','\t','\r')) else v for v in row.values()])
        return Response('\ufeff' + stream.getvalue(), mimetype='text/csv', headers={'Content-Disposition': 'attachment; filename=survivor-prices.csv'})

    @app.get('/api/players/<player_id>')
    def player_detail(player_id):
        with database() as db:
            player = rows(db, 'SELECT player_id,display_name FROM players WHERE player_id=?', (player_id,))
            if not player:
                return jsonify(error='Player not found.'), 404
            history = rows(db, "SELECT season,franchise_sheet,recorded_cost,contract_year_recorded,acquisition_class,finish,source_cell FROM roster_history WHERE player_id=? AND stage='opening' ORDER BY season", (player_id,))
            finals = rows(db, "SELECT season,franchise_sheet,recorded_cost,finish FROM roster_history WHERE player_id=? AND stage='final' ORDER BY season DESC,finish", (player_id,))
            projections = rows(db, "SELECT s.*,d.season,d.source_name,d.source_url,d.as_of_date,d.notes FROM player_stats s JOIN stat_datasets d USING(dataset_id) WHERE s.player_id=? AND d.kind='projection' ORDER BY d.as_of_date DESC,d.imported_at DESC", (player_id,))
            for archive in stored_archives(db):
                projections += [{**p,**archive_metadata(archive)} for p in archive['records'] if p['player_id']==player_id]
            values = rows(db, 'SELECT v.*,r.model_version,r.created_at,r.projection_dataset_id,d.season,e.payload_json AS keeper_evidence_json FROM projected_values v JOIN valuation_runs r USING(run_id) JOIN stat_datasets d ON r.projection_dataset_id=d.dataset_id LEFT JOIN keeper_value_evidence e ON e.run_id=v.run_id AND e.player_id=v.player_id WHERE v.player_id=? ORDER BY r.created_at DESC', (player_id,))
            current = rows(db, 'SELECT season,franchise,keeper_cost FROM keeper_selections WHERE player_id=? AND season=?', (player_id, RULES['target_season']))
            for value in values:
                value.update(target_metrics(value))
        return jsonify(**player[0], history=history, finals=finals, projections=projections, valuations=values,
                       confirmed_keeper=current[0] if current else None)

    @app.get('/api/projections')
    @app.get('/api/projections.csv')
    def projections():
        with database() as db:
            dataset_id = request.args.get('dataset')
            if not dataset_id:
                latest = db.execute("SELECT dataset_id FROM stat_datasets WHERE kind='projection' AND season=? ORDER BY as_of_date DESC,imported_at DESC LIMIT 1", (request.args.get('season', RULES['target_season']),)).fetchone()
                dataset_id = latest[0] if latest else ''
            metadata = rows(db, "SELECT dataset_id,season,source_name,source_url,as_of_date,coverage,notes FROM stat_datasets WHERE kind='projection' AND dataset_id=?", (dataset_id,))
            entries = rows(db, "SELECT s.*,p.display_name AS player FROM player_stats s JOIN players p USING(player_id) JOIN stat_datasets d USING(dataset_id) WHERE s.dataset_id=? AND d.kind='projection' ORDER BY s.pts_pg DESC,p.display_name", (dataset_id,))
            if not metadata:
                archive = next((a for a in stored_archives(db) if a['dataset_id']==dataset_id or
                                (not dataset_id and a['season']==request.args.get('season'))),None)
                if archive:
                    dataset_id = archive['dataset_id']
                    metadata = [archive_metadata(archive)]
                    entries = [{k:v for k,v in p.items() if k!='original'} | {'dataset_id':dataset_id} for p in archive['records']]
            matching = rows(db, '''SELECT v.player_id,v.run_id,v.fair_value,v.expected_auction_price,v.category_values_json
                FROM projected_values v WHERE v.run_id=(SELECT run_id FROM valuation_runs
                WHERE projection_dataset_id=? ORDER BY created_at DESC,run_id LIMIT 1)''',(dataset_id,))
            matching = {v['player_id']:v for v in matching}
            for entry in entries:
                entry.update(matching.get(entry['player_id'],{}))
            entries = available_entries(db, entries, metadata[0]['season'] if metadata else '')
            entries = table_entries(entries,'pts_pg')
        if request.path.endswith('.csv'):
            stream = io.StringIO(newline='')
            writer = csv.writer(stream)
            fields = ['player','positions','nba_team',*STAT_COLUMNS,'fg_pct','ft_pct','survivor_score',
                      'expected_auction_price','fair_value','draft_status','dataset_id','run_id',*TARGET_FIELDS]
            writer.writerow(fields)
            for row in entries:
                writer.writerow(["'"+v if isinstance(v,str) and v.startswith(('=','+','-','@','\t','\r')) else v for v in [row.get(k) for k in fields]])
            return Response('\ufeff'+stream.getvalue(),mimetype='text/csv',headers={'Content-Disposition':'attachment; filename=survivor-projections.csv'})
        return jsonify(dataset=metadata[0] if metadata else None, rows=entries)

    @app.get('/api/valuations')
    @app.get('/api/valuations.csv')
    def valuations():
        with database() as db:
            run_id = request.args.get('run')
            if not run_id:
                latest = db.execute('SELECT r.run_id FROM valuation_runs r JOIN stat_datasets d ON r.projection_dataset_id=d.dataset_id WHERE d.season=? ORDER BY r.created_at DESC LIMIT 1', (request.args.get('season', RULES['target_season']),)).fetchone()
                run_id = latest[0] if latest else ''
            metadata = rows(db, 'SELECT r.run_id,r.created_at,r.model_version,r.training_cutoff,r.validation_json,r.league_settings_json,r.notes,d.season,d.source_name,d.as_of_date FROM valuation_runs r JOIN stat_datasets d ON d.dataset_id=r.projection_dataset_id WHERE run_id=?', (run_id,))
            # Detailed comparisons are fetched for one player in the drawer,
            # rather than sending 10,000 comparable records with every board.
            entries = rows(db, '''SELECT v.run_id,v.player_id,v.fair_value,v.expected_auction_price,v.recommended_bid_ceiling,v.lower_estimate,v.upper_estimate,v.keeper_cost,v.keeper_surplus,v.category_values_json,v.risk_notes,p.display_name AS player,
                s.positions,s.nba_team,'''+','.join('s.'+k for k in STAT_COLUMNS)+'''
                FROM projected_values v JOIN players p USING(player_id) JOIN valuation_runs r USING(run_id)
                LEFT JOIN player_stats s ON s.dataset_id=r.projection_dataset_id AND s.player_id=v.player_id
                WHERE v.run_id=? ORDER BY v.fair_value DESC NULLS LAST,p.display_name''', (run_id,))
            entries = available_entries(db, entries, metadata[0]['season'] if metadata else '')
            entries = table_entries(entries,'fair_value')
        if metadata:
            metadata[0]['validation'] = json.loads(metadata[0]['validation_json'])
            metadata[0]['settings'] = json.loads(metadata[0]['league_settings_json'])
        if request.path.endswith('.csv'):
            stream = io.StringIO(newline='')
            writer = csv.writer(stream)
            writer.writerow(['Player','Survivor value score','Expected league price','Neutral auction value',
                             'Price band low','Price band high','Draft status','Keeper owner','Confirmed keeper cost',
                             'Neutral faster cuts','Neutral slower cuts','Run ID','Market season age','Price before comps','Comp adjustment',
                             'Position','NBA team',*STAT_COLUMNS,'FG%','FT%',
                             'Auction estimate','Keeper contribution','Keeper share','Supporting keeper decisions','Supporting keeper claims',
                             'Value tier','Value gap','Value gap percent','Positive categories','Weakest category impact',
                             'FG impact','FT impact','Weaker percentage impact','All eight positive','Both percentages positive'])
            for row in entries:
                details = json.loads(row['category_values_json'])
                scenarios = details.get('scenario_values',{})
                market = details.get('market',{})
                cells = [row['player'],details.get('score'),row['expected_auction_price'],row['fair_value'],
                         row['lower_estimate'],row['upper_estimate'],row['draft_status'],row['keeper_franchise'],
                         row['confirmed_keeper_cost'],scenarios.get('faster'),scenarios.get('slower'),run_id,
                         (market.get('age_source') or {}).get('target_season_age'),market.get('base_price'),market.get('comp_adjustment'),
                         row.get('positions'),row.get('nba_team'),*[row.get(k) for k in STAT_COLUMNS],row['fg_pct'],row['ft_pct'],
                         market.get('auction_estimate'),market.get('keeper_adjustment'),market.get('keeper_share'),market.get('keeper_decisions'),market.get('keeper_claims'),
                         *[row.get(k) for k in TARGET_FIELDS]]
                writer.writerow(["'"+v if isinstance(v,str) and v.startswith(('=','+','-','@','\t','\r')) else v for v in cells])
            return Response('\ufeff'+stream.getvalue(),mimetype='text/csv',
                            headers={'Content-Disposition':'attachment; filename=survivor-valuations.csv'})
        return jsonify(run=metadata[0] if metadata else None, rows=entries)

    @app.get('/api/notes')
    def notes():
        with database() as db:
            issues = rows(db, 'SELECT severity,code,season,sheet,source_cell,detail FROM data_issues ORDER BY season DESC,code,sheet')
        return jsonify(rows=issues)

    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--preview', action='store_true')
    parser.add_argument('--port', type=int, default=int(os.environ.get('PORT', 8080)))
    args = parser.parse_args()
    if args.preview:
        db = preview_database(threaded=True)
        build_catalog(ROOT / 'Survivor keeper log 2025.xlsx', db)
        sync_bundled_keepers(db)
        sync_bundled_stats(db)
        sync_bundled_projections(db)
        sync_valuations(db)
        app = create_app(db)
    else:
        # Apply only additive schema/index changes once at startup. History is untouched.
        db = railway_database()
        try:
            sync_bundled_keepers(db)
            sync_bundled_stats(db)
            sync_bundled_projections(db)
            sync_valuations(db)
        finally:
            db.close()
        app = create_app()
    from waitress import serve
    logging.basicConfig(level=logging.INFO)
    print(f'Survivor dashboard listening on 0.0.0.0:{args.port}', flush=True)
    serve(app, host='0.0.0.0', port=args.port, threads=4)


if __name__ == '__main__':
    main()
