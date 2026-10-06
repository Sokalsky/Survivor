import csv
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from survivor.catalog import build_catalog, STAT_HEADERS
from survivor.dashboard import create_app
from survivor.database import preview_database
from survivor.import_stats import import_stats
from survivor.keepers import sync_bundled_keepers

ROOT = Path(__file__).resolve().parents[1]


class DashboardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = preview_database()
        build_catalog(ROOT / 'Survivor keeper log 2025.xlsx', cls.db)
        sync_bundled_keepers(cls.db)
        cls.app = create_app(cls.db)
        cls.client = cls.app.test_client()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def test_health_and_page_assets(self):
        self.assertEqual(self.client.get('/health').json, {'status':'ok'})
        page = self.client.get('/')
        self.assertEqual(page.status_code, 200)
        self.assertIn(b'Price history', page.data)
        self.assertIn("frame-ancestors 'none'", page.headers['Content-Security-Policy'])
        with self.client.get('/static/dashboard.js') as asset:
            self.assertEqual(asset.status_code, 200)

    def test_bootstrap_and_empty_future_data_are_honest(self):
        data = self.client.get('/api/bootstrap').json
        self.assertEqual(len(data['seasons']),11)
        self.assertEqual(len(data['teams']),15)
        self.assertEqual(data['issue_count'],26)
        self.assertEqual(self.client.get('/api/projections?season=2030-31').json,{'dataset':None,'rows':[]})
        self.assertEqual(self.client.get('/api/valuations?season=2030-31').json,{'run':None,'rows':[]})

    def test_history_filters_separate_cost_types_and_snapshots(self):
        auctions = self.client.get('/api/history?season=2025-26&kind=auction&limit=100').json
        keepers = self.client.get('/api/history?season=2025-26&kind=keeper').json
        finals = self.client.get('/api/history?season=2025-26&kind=final&team=Max').json
        self.assertEqual(auctions['total'],195)
        self.assertEqual(keepers['total'],30)
        self.assertEqual(finals['total'],17)
        self.assertTrue(all(row['acquisition_class']=='auction' for row in auctions['rows']))
        self.assertTrue(all(row['acquisition_class']=='keeper' for row in keepers['rows']))
        self.assertTrue(all(row['acquisition_class']=='final_snapshot' for row in finals['rows']))

    def test_all_seasons_summary_and_distribution_reconcile(self):
        summary = self.client.get('/api/overview?season=all').json
        self.assertEqual(summary['opening_players'],2473)
        self.assertEqual(summary['auction_players'],2173)
        self.assertEqual(summary['keepers'],300)
        self.assertEqual(sum(row['players'] for row in summary['distribution']),2173)
        latest = self.client.get('/api/overview?season=2025-26').json
        self.assertEqual(latest['opening_spend'],2961)
        self.assertEqual(latest['auction_spend']+latest['keeper_spend'],2961)

    def test_player_history_and_csv_have_real_prices(self):
        player = self.client.get('/api/players/nikolajokic').json
        latest = player['history'][-1]
        self.assertEqual(latest['recorded_cost'],85)
        self.assertEqual(latest['acquisition_class'],'keeper')
        self.assertEqual(player['confirmed_keeper'], {'season':'2026-27','franchise':'Max','keeper_cost':88})
        prior = next(row for row in player['history'] if row['season']=='2024-25')
        self.assertEqual((prior['recorded_cost'],prior['acquisition_class']),(84,'auction'))
        response = self.client.get('/api/history.csv?season=all&kind=auction&q=Nikola%20Joki%C4%87')
        records = list(csv.DictReader(io.StringIO(response.data.decode('utf-8-sig'))))
        self.assertTrue(records)
        self.assertTrue(all(row['Player']=='Nikola Jokic' and row['Entry type']=='auction' for row in records))
        self.assertEqual(self.client.get('/api/players/not-a-player').status_code,404)

    def test_bound_filters_pagination_and_read_only_routes(self):
        self.assertEqual(self.client.get('/api/history?team=%27%20OR%201%3D1--').json['total'],0)
        self.assertEqual(self.client.get('/api/history?kind=invalid').status_code,400)
        self.assertEqual(self.client.get('/api/history?page=bad').status_code,400)
        page = self.client.get('/api/history?season=2025-26&kind=keeper&page=999&limit=10').json
        self.assertEqual(page['page'],3)
        self.assertEqual(len(page['rows']),10)
        self.assertEqual(self.client.post('/api/history').status_code,405)
        self.assertEqual(self.client.post('/api/keepers').status_code,405)

    def test_keeper_summary_and_unknown_season(self):
        draft = self.client.get('/api/keepers').json['draft']
        self.assertEqual((draft['keeper_count'],draft['keeper_spend'],draft['remaining_budget']),(30,790,2210))
        self.assertEqual(len(draft['teams']),15)
        self.assertEqual(self.client.get('/api/keepers?season=2030-31').json,{'draft':None})
        self.assertEqual(self.client.get('/api/bootstrap').json['draft'],draft)
        self.assertEqual(self.client.get('/api/projections?availability=invalid').status_code,400)

    def test_saved_projection_and_valuation_data_display_without_actuals_leaking(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'stats.csv'
            with path.open('w',newline='',encoding='utf-8') as stream:
                writer=csv.writer(stream);writer.writerow(STAT_HEADERS)
                writer.writerow(['Nikola Jokic','TEST','C',65,30,20,10,3,1,2,1,8,15,3,4])
                writer.writerow(['Giannis Antetokounmpo','TEST','PF',65,30,19,10,3,1,2,1,8,15,3,4])
            projection=import_stats(self.db,path,kind='projection',season='2026-27',source_name='SYNTHETIC TEST ONLY',as_of_date='2026-10-01')
            actual=import_stats(self.db,path,kind='actual',season='2025-26',source_name='SYNTHETIC TEST ONLY',as_of_date='2026-06-01')
            older=import_stats(self.db,path,kind='projection',season='2025-26',source_name='SYNTHETIC TEST ONLY',as_of_date='2025-10-01')
            with self.db:
                self.db.execute('INSERT INTO valuation_runs VALUES (?,?,?,?,?,?,?,?)',('test-run',projection,'2026-10-02','test-model','{}','2025-09-01','{}','synthetic test only'))
                self.db.execute('INSERT INTO projected_values VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',('test-run','nikolajokic',90,88,89,80,95,85,5,'{}','[]','synthetic test only'))
                self.db.execute('INSERT INTO projected_values VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',('test-run','giannisantetokounmpo',65,70,68,60,80,None,None,'{}','[]','synthetic test only'))
            try:
                data=self.client.get('/api/projections').json
                self.assertEqual(data['dataset']['dataset_id'],projection)
                self.assertEqual(data['rows'][0]['fgm_pg'],8)
                for path in ('/api/projections','/api/valuations'):
                    kept = self.client.get(path+'?availability=kept').json['rows']
                    self.assertEqual(len(kept),1)
                    self.assertEqual(kept[0]['player_id'],'nikolajokic')
                    self.assertEqual((kept[0]['confirmed_keeper_cost'],kept[0]['keeper_franchise']),(88,'Max'))
                    available = self.client.get(path+'?availability=available').json['rows']
                    self.assertEqual([r['player_id'] for r in available],['giannisantetokounmpo'])
                prior = self.client.get('/api/projections?dataset='+older).json
                self.assertTrue(all(row['draft_status']=='unknown' and row['confirmed_keeper_cost'] is None for row in prior['rows']))
                self.assertEqual(self.client.get('/api/projections?dataset='+older+'&availability=available').json['rows'],[])
                self.assertEqual(self.client.get('/api/projections?dataset='+actual).json,{'dataset':None,'rows':[]})
                values=self.client.get('/api/valuations').json
                self.assertEqual(values['rows'][0]['fair_value'],90)
                self.assertEqual(values['rows'][0]['keeper_cost'],85)  # Saved model input is untouched.
                self.assertEqual(values['run']['training_cutoff'],'2025-09-01')
                self.assertEqual(len(self.client.get('/api/bootstrap').json['runs']),1)
            finally:
                with self.db:
                    self.db.execute("DELETE FROM projected_values WHERE run_id='test-run'")
                    self.db.execute("DELETE FROM valuation_runs WHERE run_id='test-run'")
                    self.db.execute('DELETE FROM player_stats WHERE dataset_id IN (?,?,?)',(projection,actual,older))
                    self.db.execute('DELETE FROM stat_datasets WHERE dataset_id IN (?,?,?)',(projection,actual,older))

    def test_connection_failures_do_not_expose_connection_strings(self):
        with patch('survivor.dashboard.Postgres',side_effect=RuntimeError('postgresql://private:secret@host/db')):
            with patch.dict('os.environ',{'DATABASE_URL':'postgresql://private:secret@host/db'}):
                response=create_app().test_client().get('/api/bootstrap')
        self.assertEqual(response.status_code,503)
        self.assertNotIn(b'secret',response.data)


if __name__=='__main__':
    unittest.main()
