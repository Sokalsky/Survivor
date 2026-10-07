"""Import the reviewed historical CSV snapshot bundled with the Railway image."""
from __future__ import annotations

import argparse
from datetime import date
import hashlib
import json
from pathlib import Path
import re

from survivor.catalog import ROOT
from survivor.database import railway_database
from survivor.import_stats import import_stats, validate_csv

SNAPSHOT = ROOT / 'output/stats'


def csv_hash(path):
    # Git normalizes CSV line endings. Hash the text consistently on Windows/Linux.
    text = Path(path).read_text(encoding='utf-8-sig')
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def validated_sources(folder=SNAPSHOT):
    folder = Path(folder).resolve()
    manifest = json.loads((folder/'manifest.json').read_text(encoding='utf-8'))
    if manifest.get('errors'):
        raise ValueError('Historical snapshot has source errors; refusing startup import.')
    sources, seasons = [], set()
    for source in manifest['sources']:
        # Projection snapshots have a separate completeness/approval workflow.
        if source['kind'] != 'actual':
            continue
        season = source['season']
        if not re.fullmatch(r'20\d{2}-\d{2}', season) or int(season[-2:]) != (int(season[:4])+1) % 100:
            raise ValueError('Invalid season in historical snapshot.')
        if season in seasons or source.get('ready_for_import') is not True:
            raise ValueError('Duplicate or unready season in historical snapshot.')
        seasons.add(season)
        if source['file'] != f'actuals-{season}.csv':
            raise ValueError('Unexpected historical snapshot filename.')
        path = (folder/source['file']).resolve()
        if path.parent != folder:
            raise ValueError('Historical snapshot file is outside its directory.')
        date.fromisoformat(source['as_of_date'])
        if not source['source_name'].strip() or not source['url'].startswith('https://'):
            raise ValueError('Historical source metadata is incomplete.')
        if csv_hash(path) != source.get('csv_sha256'):
            raise ValueError(f'Historical CSV hash mismatch: {season}.')
        records = validate_csv(path)
        if len(records) != source['players']:
            raise ValueError(f'Historical CSV row count mismatch: {season}.')
        sources.append((source, path))
    if not sources or len(sources) != manifest['historical_seasons'] or sum(s['players'] for s,_ in sources) != manifest['historical_rows']:
        raise ValueError('Historical snapshot totals do not reconcile.')
    return sources


def sync_bundled_stats(db, folder=SNAPSHOT):
    # Validate every file before importing the first. Each season commits atomically;
    # an interrupted import can resume safely without re-adding completed seasons.
    sources = validated_sources(folder)
    total = 0
    for source, path in sources:
        dataset_id = import_stats(db, path, kind='actual', season=source['season'],
                                 source_name=source['source_name'], as_of_date=source['as_of_date'],
                                 source_url=source['url'],
                                 notes='Regular-season source totals converted to per-game values. Retrieval date is not a preseason snapshot.')
        count = db.execute('SELECT COUNT(*) FROM player_stats WHERE dataset_id=?', (dataset_id,)).fetchone()[0]
        if count != source['players']:
            raise ValueError(f'Stored statistics do not match the snapshot: {source["season"]}.')
        total += count
        print(f'Historical stats {source["season"]}: {count} rows ready', flush=True)
    print(f'Historical stats ready: {len(sources)} seasons / {total:,} rows', flush=True)
    return {'seasons':len(sources), 'rows':total}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--validate-only', action='store_true')
    args = parser.parse_args()
    if args.validate_only:
        sources = validated_sources()
        print(f'Validated {len(sources)} historical seasons / {sum(s["players"] for s,_ in sources):,} rows')
        return
    db = railway_database()
    try:
        sync_bundled_stats(db)
    finally:
        db.close()


if __name__ == '__main__':
    main()
