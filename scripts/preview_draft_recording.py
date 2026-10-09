"""Local-only recording QA server. All draft writes go to an in-memory database."""
import argparse
import json
import os
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from flask import jsonify
from survivor.catalog import build_catalog
from survivor.database import preview_database
from survivor.dashboard import create_app
from survivor.keepers import sync_bundled_keepers
from survivor.bundled_stats import sync_bundled_stats,sync_bundled_projections
from survivor.valuation import sync_valuations


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port',type=int,default=8767)
    parser.add_argument('--fixture',type=Path,help='Optional saved bootstrap/valuations JSON for faster UI checks')
    args=parser.parse_args()
    os.environ['DRAFT_RECORDING_KEY']='local-qa-only-recording-key-0123456789'
    db=preview_database(threaded=True)
    if not args.fixture:
        build_catalog(ROOT/'Survivor keeper log 2025.xlsx',db)
        sync_bundled_keepers(db);sync_bundled_stats(db);sync_bundled_projections(db);sync_valuations(db)
    app=create_app(db)
    if args.fixture:
        fixture=json.loads(args.fixture.read_text(encoding='utf-8'))
        app.view_functions['bootstrap']=lambda:jsonify(fixture['bootstrap'])
        app.view_functions['valuations']=lambda:jsonify(fixture['valuations'])

    @app.post('/__qa/reset')
    def reset_qa():
        # This route exists only in this loopback-only, in-memory QA process.
        with db:
            db.execute('DELETE FROM recorded_team_snapshots')
            db.execute('DELETE FROM recorded_draft_events')
            db.execute('DELETE FROM recorded_drafts')
        return jsonify(isolated=True)

    from waitress import serve
    print(f'Isolated in-memory recording QA on http://127.0.0.1:{args.port}',flush=True)
    serve(app,host='127.0.0.1',port=args.port,threads=4)

if __name__=='__main__':main()
