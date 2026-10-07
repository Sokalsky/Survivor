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
from pathlib import Path
import threading

from flask import Flask, Response, jsonify, render_template, request
from werkzeug.exceptions import BadRequest, HTTPException

from survivor.catalog import build_catalog, name_key, rows
from survivor.database import Postgres, preview_database, railway_database
from survivor.keepers import annotate_availability, keeper_summary, sync_bundled_keepers
from survivor.bundled_stats import sync_bundled_stats

ROOT = Path(__file__).resolve().parents[1]
RULES = json.loads((ROOT / 'config/league.json').read_text(encoding='utf-8'))


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
        order = {'price_desc': 'recorded_cost DESC,player,season DESC',
                 'price_asc': 'recorded_cost ASC,player,season DESC',
                 'player': 'player,season DESC', 'season': 'season DESC,recorded_cost DESC,player',
                 'team': 'franchise_sheet,recorded_cost DESC,player'}.get(request.args.get('sort'), 'recorded_cost DESC,player,season DESC')
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
            entries = rows(db, f'SELECT season,player,player_id,franchise_sheet,recorded_cost,contract_year_recorded,acquisition_class,finish,source_cell FROM {table} WHERE {where} ORDER BY {order} LIMIT ? OFFSET ?', (*parameters, limit, (page-1)*limit))
        return jsonify(rows=entries, total=count, page=page, limit=limit)

    @app.get('/api/history.csv')
    def history_csv():
        with database() as db:
            try:
                table, where, parameters, order = history_query(db)
            except ValueError:
                return jsonify(error='Unknown entry type.'), 400
            entries = rows(db, f'SELECT season,player,franchise_sheet,recorded_cost,acquisition_class,contract_year_recorded,source_cell FROM {table} WHERE {where} ORDER BY {order}', parameters)
        stream = io.StringIO(newline='')
        writer = csv.writer(stream)
        writer.writerow(['Season','Player','Franchise','Recorded cost','Entry type','Contract year','Source cell'])
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
            projections = rows(db, "SELECT s.*,d.season,d.source_name,d.as_of_date FROM player_stats s JOIN stat_datasets d USING(dataset_id) WHERE s.player_id=? AND d.kind='projection' ORDER BY d.as_of_date DESC,d.imported_at DESC", (player_id,))
            values = rows(db, 'SELECT v.*,r.model_version,r.created_at,d.season FROM projected_values v JOIN valuation_runs r USING(run_id) JOIN stat_datasets d ON r.projection_dataset_id=d.dataset_id WHERE v.player_id=? ORDER BY r.created_at DESC', (player_id,))
            current = rows(db, 'SELECT season,franchise,keeper_cost FROM keeper_selections WHERE player_id=? AND season=?', (player_id, RULES['target_season']))
        return jsonify(**player[0], history=history, finals=finals, projections=projections, valuations=values,
                       confirmed_keeper=current[0] if current else None)

    @app.get('/api/projections')
    def projections():
        with database() as db:
            dataset_id = request.args.get('dataset')
            if not dataset_id:
                latest = db.execute("SELECT dataset_id FROM stat_datasets WHERE kind='projection' AND season=? ORDER BY as_of_date DESC,imported_at DESC LIMIT 1", (request.args.get('season', RULES['target_season']),)).fetchone()
                dataset_id = latest[0] if latest else ''
            metadata = rows(db, "SELECT dataset_id,season,source_name,source_url,as_of_date,coverage FROM stat_datasets WHERE kind='projection' AND dataset_id=?", (dataset_id,))
            entries = rows(db, "SELECT s.*,p.display_name AS player FROM player_stats s JOIN players p USING(player_id) JOIN stat_datasets d USING(dataset_id) WHERE s.dataset_id=? AND d.kind='projection' ORDER BY s.pts_pg DESC,p.display_name", (dataset_id,))
            entries = available_entries(db, entries, metadata[0]['season'] if metadata else '')
        return jsonify(dataset=metadata[0] if metadata else None, rows=entries)

    @app.get('/api/valuations')
    def valuations():
        with database() as db:
            run_id = request.args.get('run')
            if not run_id:
                latest = db.execute('SELECT r.run_id FROM valuation_runs r JOIN stat_datasets d ON r.projection_dataset_id=d.dataset_id WHERE d.season=? ORDER BY r.created_at DESC LIMIT 1', (request.args.get('season', RULES['target_season']),)).fetchone()
                run_id = latest[0] if latest else ''
            metadata = rows(db, 'SELECT r.run_id,r.created_at,r.model_version,r.training_cutoff,r.validation_json,d.season,d.source_name,d.as_of_date FROM valuation_runs r JOIN stat_datasets d ON d.dataset_id=r.projection_dataset_id WHERE run_id=?', (run_id,))
            entries = rows(db, 'SELECT v.*,p.display_name AS player FROM projected_values v JOIN players p USING(player_id) WHERE v.run_id=? ORDER BY v.fair_value DESC NULLS LAST,p.display_name', (run_id,))
            entries = available_entries(db, entries, metadata[0]['season'] if metadata else '')
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
        app = create_app(db)
    else:
        # Apply only additive schema/index changes once at startup. History is untouched.
        db = railway_database()
        try:
            sync_bundled_keepers(db)
            sync_bundled_stats(db)
        finally:
            db.close()
        app = create_app()
    from waitress import serve
    logging.basicConfig(level=logging.INFO)
    print(f'Survivor dashboard listening on 0.0.0.0:{args.port}', flush=True)
    serve(app, host='0.0.0.0', port=args.port, threads=4)


if __name__ == '__main__':
    main()
