import csv
from pathlib import Path
import tempfile
import unittest

from survivor.catalog import STAT_HEADERS, build_catalog, export_catalog, load_aliases, resolve_name, rows
from survivor.database import preview_database
from survivor.import_stats import import_stats, validate_csv

ROOT = Path(__file__).resolve().parents[1]


class WorkbookIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = preview_database()
        cls.source = ROOT / 'Survivor keeper log 2025.xlsx'
        build_catalog(cls.source, cls.db)

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def test_entire_workbook_coverage(self):
        expected = {'source_sheets': 26, 'seasons': 11, 'team_seasons': 165,
                    'roster_entries': 5094, 'tracking_entries': 2400, 'raw_cells': 21354}
        for table, count in expected.items():
            with self.subTest(table=table):
                self.assertEqual(self.db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0], count)
        self.assertFalse(self.db.execute('PRAGMA foreign_key_check').fetchall())

    def test_keeper_prices_do_not_become_auction_sales(self):
        jokic = rows(self.db, "SELECT season,recorded_cost,acquisition_class FROM roster_history WHERE player_id='nikolajokic' AND franchise_sheet='Max' AND stage='opening' AND season IN ('2024-25','2025-26') ORDER BY season")
        self.assertEqual([(r['recorded_cost'],r['acquisition_class']) for r in jokic], [(84,'auction'),(85,'keeper')])
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM auction_sales').fetchone()[0],2173)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM keeper_costs').fetchone()[0],300)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM keeper_costs WHERE season='2015-16'").fetchone()[0],0)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM data_issues WHERE code='keeper_position_contract_conflict'").fetchone()[0],0)

    def test_known_source_discrepancies_are_preserved(self):
        missing = rows(self.db, "SELECT season,sheet FROM data_issues WHERE code='opening_roster_size' ORDER BY season")
        self.assertEqual(missing,[{'season':'2017-18','sheet':'Joe'}, {'season':'2022-23','sheet':'Benji'}])
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM data_issues WHERE code='final_cost_differs_from_opening'").fetchone()[0],20)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM roster_history WHERE season='2024-25' AND franchise_sheet='Max' AND stage='final' AND player_id='karlanthonytowns' AND recorded_cost=84").fetchone()[0],1)

    def test_reimport_does_not_duplicate_data(self):
        build_catalog(self.source, self.db)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM roster_entries').fetchone()[0],5094)

    def test_export_counts_and_workbook(self):
        with tempfile.TemporaryDirectory() as folder:
            report = export_catalog(self.db, folder)
            self.assertEqual(report['metrics']['clean_auction_sales'],2173)
            self.assertTrue((Path(folder)/'survivor_catalog.xlsx').is_file())
            with (Path(folder)/'csv/auction_purchases.csv').open(encoding='utf-8-sig') as stream:
                self.assertEqual(len(list(csv.DictReader(stream))),2173)


class IdentityAndStatisticsTests(unittest.TestCase):
    def test_similar_but_distinct_names_stay_distinct(self):
        aliases = load_aliases()
        key = lambda name: resolve_name(name,aliases)[0]
        self.assertNotEqual(key('Jalen Williams'),key('Jaylin Wiliams'))
        self.assertNotEqual(key('Bojan Bogdanovic'),key('Bogdon Bogdanovic'))
        self.assertNotEqual(key('Jalen McDaniels'),key('Jaden McDaniels'))
        self.assertEqual(key('C. J. McCollum'),key('CJ McCollum'))
        self.assertEqual(key('Jaleb Brunson'),key('Jalen Brunson'))

    def write_csv(self, path, values):
        with path.open('w',newline='',encoding='utf-8') as stream:
            writer=csv.writer(stream)
            writer.writerow(STAT_HEADERS)
            writer.writerows(values)

    def test_stats_inputs_preserve_versions_and_are_idempotent(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'stats.csv'
            self.write_csv(path,[['Test Player','TEST','C',65,30,20,10,3,1,2,1,8,15,3,4]])
            db=preview_database()
            try:
                kwargs={'kind':'projection','season':'2026-27','source_name':'synthetic_test_only','as_of_date':'2026-10-01'}
                first=import_stats(db,path,**kwargs)
                self.assertEqual(first,import_stats(db,path,**kwargs))
                kwargs['as_of_date']='2026-10-02'
                self.assertNotEqual(first,import_stats(db,path,**kwargs))
                self.assertEqual(db.execute('SELECT COUNT(*) FROM player_stats').fetchone()[0],2)
                self.assertEqual(db.execute('SELECT COUNT(*) FROM projected_values').fetchone()[0],0)
            finally:
                db.close()

    def test_invalid_stats_and_duplicate_aliases_are_rejected(self):
        valid=['CJ McCollum','TEST','G',65,30,20,4,5,1,0.5,2,7,15,4,5]
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'stats.csv'
            for index,value in [(6,float('nan')),(11,16),(14,3)]:
                invalid=valid.copy()
                invalid[index]=value
                self.write_csv(path,[invalid])
                with self.assertRaises(ValueError):
                    validate_csv(path)
            duplicate=valid.copy()
            duplicate[0]='C. J. McCollum'
            self.write_csv(path,[valid,duplicate])
            with self.assertRaises(ValueError):
                validate_csv(path)


if __name__ == '__main__':
    unittest.main()
