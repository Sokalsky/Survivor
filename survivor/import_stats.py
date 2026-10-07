"""Import a dated actual-statistics or projection CSV into Railway Postgres."""
from __future__ import annotations

import argparse
import csv
from datetime import date
import hashlib
import json
import math
from pathlib import Path
import re

from survivor.catalog import STAT_HEADERS, load_aliases, now, resolve_name
from survivor.database import railway_database


def validate_csv(path):
    aliases = load_aliases()
    result, seen = [], set()
    with Path(path).open(encoding='utf-8-sig', newline='') as stream:
        reader = csv.DictReader(stream)
        missing = set(STAT_HEADERS) - set(reader.fieldnames or [])
        if missing:
            raise ValueError('Missing columns: ' + ', '.join(sorted(missing)))
        for line, row in enumerate(reader, 2):
            name = (row.get('player') or '').strip()
            if not name:
                raise ValueError(f'Line {line}: missing player.')
            key = resolve_name(name, aliases)[0]
            if key in seen:
                raise ValueError(f'Line {line}: duplicate player {name}. Supply one combined season row per player, not team splits.')
            seen.add(key)
            parsed = {k: row[k] for k in STAT_HEADERS[:3]}
            for field in STAT_HEADERS[3:]:
                try:
                    value = float(row[field])
                except (TypeError, ValueError):
                    raise ValueError(f'Line {line}: {field} must be numeric.') from None
                if not math.isfinite(value) or value < 0:
                    raise ValueError(f'Line {line}: {field} must be finite and nonnegative.')
                parsed[field] = value
            if parsed['fgm_pg'] > parsed['fga_pg'] or parsed['ftm_pg'] > parsed['fta_pg'] or parsed['fg3m_pg'] > parsed['fgm_pg']:
                raise ValueError(f'Line {line}: made shots exceed attempted shots or threes exceed total made field goals.')
            if parsed['minutes_pg'] > 70:
                raise ValueError(f'Line {line}: minutes_pg exceeds 70; check per-game versus total units.')
            result.append(parsed)
    if not result:
        raise ValueError('CSV contains no statistical rows. The provided template has headers only.')
    return result


def import_stats(db, path, *, kind, season, source_name, as_of_date, coverage='full_season', source_url=None, notes=None):
    if kind not in ('actual', 'projection'):
        raise ValueError('kind must be actual or projection.')
    if not re.fullmatch(r'20\d{2}-\d{2}', season) or int(season[-2:]) != (int(season[:4])+1) % 100:
        raise ValueError('season must be YYYY-YY, for example 2026-27.')
    date.fromisoformat(as_of_date)
    if not source_name.strip() or not coverage.strip():
        raise ValueError('Source and coverage must be specified.')
    records = validate_csv(path)
    digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    identity = json.dumps([kind, season, source_name, as_of_date, coverage, source_url, digest])
    dataset_id = hashlib.sha256(identity.encode()).hexdigest()
    aliases = load_aliases()
    with db:
        if getattr(db, 'dialect', None) == 'postgres':
            db.execute("SELECT pg_advisory_xact_lock(hashtext('survivor-stats-import'))")
        if db.execute('SELECT dataset_id FROM stat_datasets WHERE dataset_id=?', (dataset_id,)).fetchone():
            return dataset_id
        db.execute('INSERT INTO stat_datasets VALUES (?,?,?,?,?,?,?,?,?,?)',
                   (dataset_id, kind, season, source_name, source_url, as_of_date, now(), digest, coverage, notes))
        players, names, statistics = [], [], []
        for row in records:
            player_id, canonical, method = resolve_name(row['player'], aliases)
            players.append((player_id, canonical, 'local_name_identity; NBA ID not yet matched'))
            names.append((row['player'], player_id, method))
            values = [dataset_id, player_id, row['player'], row['nba_team'], row['positions']]
            values.extend(row[field] for field in STAT_HEADERS[3:])
            statistics.append(values)
        # Batch database writes to keep startup fast over Railway's private network.
        db.executemany('INSERT INTO players VALUES (?,?,NULL,?) ON CONFLICT(player_id) DO NOTHING', players)
        db.executemany('INSERT INTO player_aliases VALUES (?,?,?) ON CONFLICT(raw_name) DO NOTHING', names)
        db.executemany('INSERT INTO player_stats VALUES (' + ','.join('?' for _ in statistics[0]) + ')', statistics)
    return dataset_id


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('csv')
    parser.add_argument('--kind', required=True, choices=['actual', 'projection'])
    parser.add_argument('--season', required=True)
    parser.add_argument('--source', required=True)
    parser.add_argument('--as-of', required=True)
    parser.add_argument('--coverage', default='full_season')
    parser.add_argument('--source-url')
    parser.add_argument('--notes')
    parser.add_argument('--database-url-env', default='DATABASE_URL')
    parser.add_argument('--validate-only', action='store_true')
    args = parser.parse_args()
    if args.validate_only:
        print(f'Valid statistical rows: {len(validate_csv(args.csv))}')
        return
    db = railway_database(args.database_url_env)
    try:
        dataset_id = import_stats(db, args.csv, kind=args.kind, season=args.season,
                                  source_name=args.source, as_of_date=args.as_of, coverage=args.coverage,
                                  source_url=args.source_url, notes=args.notes)
        print(json.dumps({'dataset_id': dataset_id, 'status': 'imported_or_already_present'}))
    finally:
        db.close()


if __name__ == '__main__':
    main()
