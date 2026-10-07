import contextlib
import io
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest

from survivor.bundled_stats import SNAPSHOT, csv_hash, sync_bundled_stats, validated_sources
from survivor.catalog import ROOT, build_catalog
from survivor.dashboard import create_app
from survivor.database import preview_database
from survivor.import_stats import import_stats
from survivor.keepers import sync_bundled_keepers


class BundledStatsTests(unittest.TestCase):
    def snapshot_copy(self, folder):
        manifest = json.loads((SNAPSHOT/'manifest.json').read_text(encoding='utf-8'))
        actuals = [s for s in manifest['sources'] if s['kind'] == 'actual'][:2]
        # No projection file is copied, matching the Docker image.
        manifest['sources'] = actuals + [s for s in manifest['sources'] if s['kind'] == 'projection']
        manifest['historical_seasons'] = len(actuals)
        manifest['historical_rows'] = sum(s['players'] for s in actuals)
        for source in actuals:
            shutil.copyfile(SNAPSHOT/source['file'], folder/source['file'])
        self.write_manifest(folder, manifest)
        return manifest

    def write_manifest(self, folder, manifest):
        (folder/'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')

    def test_real_snapshot_import_restart_and_existing_league_data(self):
        db = preview_database()
        self.addCleanup(db.close)
        build_catalog(ROOT/'Survivor keeper log 2025.xlsx', db)
        sync_bundled_keepers(db)
        historical_prices = [tuple(row) for row in db.execute('SELECT * FROM roster_entries ORDER BY entry_id')]
        current_keepers = [tuple(row) for row in db.execute('SELECT * FROM keeper_selections ORDER BY player_id')]
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(sync_bundled_stats(db), {'seasons':12, 'rows':6460})
            before = [tuple(row) for row in db.execute('SELECT * FROM stat_datasets ORDER BY dataset_id')]
            self.assertEqual(sync_bundled_stats(db), {'seasons':12, 'rows':6460})
        self.assertEqual(before, [tuple(row) for row in db.execute('SELECT * FROM stat_datasets ORDER BY dataset_id')])
        self.assertEqual(db.execute('SELECT COUNT(*) FROM player_stats').fetchone()[0], 6460)
        self.assertEqual(db.execute("SELECT COUNT(*) FROM stat_datasets WHERE kind='projection'").fetchone()[0], 0)
        self.assertEqual(db.execute('SELECT COUNT(*) FROM projected_values').fetchone()[0], 0)
        self.assertEqual(historical_prices, [tuple(row) for row in db.execute('SELECT * FROM roster_entries ORDER BY entry_id')])
        self.assertEqual(current_keepers, [tuple(row) for row in db.execute('SELECT * FROM keeper_selections ORDER BY player_id')])
        self.assertFalse(db.execute('PRAGMA foreign_key_check').fetchall())
        client = create_app(db).test_client()
        datasets = client.get('/api/bootstrap').json['datasets']
        self.assertEqual((len(datasets), sum(d['players'] for d in datasets)), (12, 6460))
        self.assertEqual(client.get('/api/projections').json, {'dataset':None, 'rows':[]})
        self.assertEqual(client.get('/api/valuations').json, {'run':None, 'rows':[]})
        self.assertEqual(db.execute("SELECT COUNT(*) FROM player_stats WHERE player_id='nikolajokic'").fetchone()[0], 11)

    def test_projections_are_excluded_even_when_the_file_is_absent(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            self.snapshot_copy(folder)
            self.assertEqual(len(validated_sources(folder)), 2)

    def test_damaged_later_file_fails_before_any_database_writes(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            manifest = self.snapshot_copy(folder)
            path = folder/manifest['sources'][1]['file']
            path.write_bytes(path.read_bytes()+b'changed')
            db = preview_database()
            self.addCleanup(db.close)
            with self.assertRaisesRegex(ValueError, 'hash mismatch'):
                sync_bundled_stats(db, folder)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM stat_datasets').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM players').fetchone()[0], 0)

    def test_manifest_counts_and_readiness_must_reconcile(self):
        for mutation, error in [('players','row count'), ('historical_rows','totals'), ('ready_for_import','unready')]:
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temp:
                folder = Path(temp)
                manifest = self.snapshot_copy(folder)
                if mutation == 'historical_rows':
                    manifest[mutation] += 1
                elif mutation == 'players':
                    manifest['sources'][0][mutation] += 1
                else:
                    manifest['sources'][0][mutation] = False
                self.write_manifest(folder, manifest)
                with self.assertRaisesRegex(ValueError, error):
                    validated_sources(folder)

    def test_csv_integrity_hash_survives_git_line_endings(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/'test.csv'
            path.write_bytes(b'player,games\r\nExample,10\r\n')
            windows = csv_hash(path)
            path.write_bytes(b'player,games\nExample,10\n')
            self.assertEqual(windows, csv_hash(path))

    def test_failed_batch_rolls_back_players_aliases_and_dataset_and_can_retry(self):
        db = preview_database()
        self.addCleanup(db.close)
        db.execute("CREATE TRIGGER fail_stats BEFORE INSERT ON player_stats BEGIN SELECT RAISE(ABORT, 'simulated failure'); END")
        kwargs = dict(kind='actual', season='2014-15', source_name='Basketball Reference', as_of_date='2026-10-06')
        path = SNAPSHOT/'actuals-2014-15.csv'
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'simulated failure'):
            import_stats(db, path, **kwargs)
        for table in ('stat_datasets', 'player_stats', 'players', 'player_aliases'):
            self.assertEqual(db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0], 0)
        db.execute('DROP TRIGGER fail_stats')
        import_stats(db, path, **kwargs)
        self.assertEqual(db.execute('SELECT COUNT(*) FROM player_stats').fetchone()[0], 492)


if __name__ == '__main__':
    unittest.main()
