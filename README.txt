SURVIVOR BASKETBALL

Railway Postgres is the runtime database. The import job also runs on Railway.
There is no local database to maintain. Export-only previews and tests use memory.

READY NOW

- All 26 source sheets cataloged, with original player names, cells, notes and costs.
- 2015-16 through 2025-26 history inferred from the annual tracking sheets.
- Auction purchases, keeper costs and final rosters stored separately.
- Excel and CSV review exports in output/; original workbook remains untouched.
- PostgreSQL tables for dated actual statistics, projections and valuation runs.
- Validated statistical CSV importer; forecasting/model fitting is not yet built.
- Searchable dashboard: price history, player detail charts, projections,
  valuations, opening/final rosters, data notes and filtered CSV downloads.
- Dockerfile and Railway configuration for the persistent dashboard web service.
- Confirmed 2026-27 keepers, team budgets and draft availability filters.
- 6,460 historical NBA player-season records (2014-15 through 2025-26), imported
  automatically from the bundled CSV snapshot when the dashboard starts.

RAILWAY SETUP

1. Create/use a Railway project and add a PostgreSQL service.
2. Add a dashboard service using this project's source. Dockerfile is supplied.
3. On the dashboard service, add a reference variable:
     DATABASE_URL=${{Postgres.DATABASE_URL}}
   Use the actual database service name if it is not "Postgres".
4. Deploy. The configured start command is:
     python -m survivor.dashboard
5. The website listens on 0.0.0.0 and Railway's PORT. In the Survivor service,
   open Settings > Networking > Public Networking > Generate Domain. Use the
   generated HTTPS URL to access the dashboard. Healthcheck path is /health.
   Restart policy is ON_FAILURE. The website stays running after startup.
6. Inspect survivor.auction_sales, survivor.keeper_costs, survivor.roster_history
   and survivor.data_issues from the database.

The initial historical import succeeded on Railway on 2026-10-06, as confirmed
by the deployment logs: 2,173 auction purchases, 300 keepers and 2,621 final entries.
The dashboard reads those records directly from Postgres. Startup does not rerun
the workbook import. Each web request uses a read-only database connection.
Startup syncs config/keepers/*.json into draft_seasons and keeper_selections.
Identical lists are no-ops; corrected complete lists replace only that season's
keeper selections in one transaction. Historical prices and saved model runs
remain untouched. No separate import command is needed after deploying updates.
Startup also imports the verified output/stats/actuals-*.csv snapshot, recording
each season's source and retrieval date. Already imported datasets are skipped;
new snapshots are stored as separate versions. It does not fetch websites during
startup. In Data notes, the historical-stats banner reports the seasons and rows
actually loaded from the database (latest dataset per season).
Projection and valuation pages display stored datasets and model runs; when none
are loaded they show explicit empty states. No forecast or price is invented.
The public website provides browsing and CSV downloads, with no write/import API.

For a NEW empty database, run the historical import once as a separate job:
  python -m survivor.catalog
Then use the dashboard start command for the website. Existing imports are retained.

The source workbook is included in the Docker build so the job has its input.
Exports written by the catalog job to a Railway container's output/ directory are
ephemeral; Postgres is authoritative. The dashboard's Export CSV button generates
downloads directly from the database using the active season/team/type/search
filters. Current full Excel review exports are also committed in output/.

An identical workbook reimport is a no-op. A different workbook is deliberately
rejected if history is already loaded: revised-workbook reconciliation must be
implemented explicitly, without silently replacing existing history. Changes to
name aliases likewise need explicit reconciliation after the first cloud import.
Imports use transactions; interrupted history imports roll back together.

Updates are pushed to GitHub. Railway deploys the connected repository and runs
the startup imports using its existing DATABASE_URL. Direct Railway access from
the development workspace is not part of this workflow.

PREVIEW AND TESTS

  python -m pip install -r requirements.txt
  python -m survivor.catalog --preview
  python -m survivor.dashboard --preview --port 8080
  python -m unittest discover -s tests -v

Preview mode never connects to Postgres and creates no database file. It produces
output/survivor_catalog.xlsx, CSV tables and catalog_summary.json/txt.
The tests verify real-workbook counts, keeper/auction separation, source
discrepancies, identity collisions, export completeness and stat-input validation.
Dashboard tests cover filtering, keeper/snapshot separation, projection/valuation
datasets, pagination, exports and error responses. Browser checks:
  python -m pip install -r requirements-dev.txt
  python -m playwright install chromium
  python scripts/check_dashboard.py
The browser check expects the preview web server to be running on port 8080.
It supports --browser <path-to-chromium> when using an existing browser install.
Preview mode loads the real workbook into memory; no database file is created.
Screenshots are written to artifacts/, which is excluded from Git.

STATISTICS AND PROJECTIONS

Free data to Excel (no subscriptions or API keys):
  python -m survivor.stats_workbook

This fills output/stats/survivor-player-stats.xlsx with regular-season totals and
per-game stats for 2014-15 through 2025-26, public 2026-27 FantasyPros projections,
historical auction prices, confirmed keepers, team budgets and source provenance.
2014-15 supplies the prior-year context for the first auction in 2015-16.
Filters and frozen columns make every data tab searchable in Excel.

Historical data comes from Basketball Reference's public season totals pages.
Only the combined row is used for traded players. Postseason rows are excluded.
Source HTML is cached in output/stats/cache/ (excluded from Git). Use --refresh
to fetch again. A source failure appears in Issues/manifest.json and exits nonzero.

FantasyPros' public table supplies projected totals, games, minutes, FG% and FT%.
The script converts totals to per-game stats, but leaves missing shooting makes
and attempts EMPTY. Projections are labeled INCOMPLETE and cannot pass the current
statistics importer until shooting volume is supplied from a verified source or
an explicitly labeled model. No paid projection source or fabricated stats are used.

The script also produces validated actuals CSVs and a manifest with source and
CSV integrity hashes. These files are included in the Docker image. On the next
deployment, the normal dashboard start command automatically imports the bundled
historical seasons into Postgres. No separate Railway job or command is needed.
Restarting with identical files does not duplicate rows. All snapshot files are
checked before writing; each season imports in one transaction, so interrupted
deployments can resume. Historical auction prices and keepers remain untouched.
The deployment log confirms completion with:
  Historical stats ready: 12 seasons / 6,460 rows
Data notes on the website also displays the loaded count. Live import completion
must be confirmed there or in Railway logs; local tests do not verify Railway.

To validate the bundled snapshot without any database connection:
  python -m survivor.bundled_stats --validate-only
To refresh future data, rerun stats_workbook, review the changed CSVs and manifest,
then commit and push them. The Excel file is an export; editing it alone does not
update Postgres. This is an import on deployment, not a scheduled data refresh.
The incomplete projections are excluded from both the image and startup import.
--history-only skips collecting projections. The optional --import-history flag
remains available for a separate in-Railway collection job, but is not required.

Start with output/stats_import_template.csv. Supply one row per NBA player;
combine traded-player team splits before import. All *_pg columns are per game.
games is projected/actual games for the specified coverage window. Preserve both
makes and attempts: FG% and FT% alone cannot measure a player's shooting impact.
Provide zeroes explicitly where appropriate; blanks are not silently imputed.

Run inside the Railway worker environment with the input file available:
  python -m survivor.import_stats projections.csv --kind projection --season 2026-27 --source "provider name" --as-of 2026-10-06
  python -m survivor.import_stats actuals.csv --kind actual --season 2025-26 --source "provider name" --as-of 2026-06-30

The dates above illustrate metadata fields, not imported data. Use the actual
publication/snapshot date, source and season. Optional --coverage, --source-url
and --notes retain provenance. A new dated release is a separate dataset.
An identical file/source/date combination does not create duplicates.
--validate-only checks CSV rows without a database connection.
For manual projection imports, supply the complete CSV to the worker separately.
The free partial projection snapshot in output/stats/ is for review only. No paid
data has been purchased and no projected shooting volumes have been invented.

CONFIRMED 2026-27 KEEPERS

config/keepers/2026-27.json records only the 30 successful selections and their
costs from the supplied 2026 summary. Unsuccessful preferences are excluded.
Keeper spend totals $790, leaving $2,210 of the $3,000 league budget for auction.
Max keeps Dejounte Murray for $3 and Nikola Jokic for $88, leaving $109.
Every team's remaining budget reconciles to the supplied summary.

The Keepers & budgets page lists all 15 teams, searchable by team or player and
sortable by budget. Player files show current ownership/cost alongside history.
Projection and valuation boards default to available players when that season's
complete keeper list is known. All players and Keepers only remain selectable.
An older dataset never uses a newer season's keepers; missing lists are labeled
unknown. API filtering is available via ?availability=available or kept on
/api/projections and /api/valuations; the API default is all players.

survivor.draft_team_budgets exposes starting, committed and remaining budget.
The valuation board computes keeper surplus from saved fair value minus the
confirmed cost, and hides bid ceilings for kept players. It does not rewrite
saved model inputs or apply an invented keeper inflation factor. The future
valuation model should allocate the remaining $2,210 across available players
using their projected category contributions and remaining roster spots.

NOTES ABOUT THE DATA

The first two opening players are keepers in Years 2-11. In Year 1 all entries
have contract year 1 and are treated as auction purchases. Positional keeper
classification matches the recorded contract-year column throughout this file.

Current sheet names label franchise histories. Older managers may differ:
tracking-sheet participant names are retained verbatim without an invented map.
Tracking marker numbers and column G notes are archived, not treated as prices.
The raw_cells table preserves all populated workbook cells and comments.

The player catalog uses text identities, not yet verified NBA IDs. Obvious
spelling variants are editable in config/player_aliases.json. Closely named
players remain distinct. "Jalen Jackson Jr." is flagged and left unresolved.
Before joining external statistics, reconcile provider IDs and unmatched names.

League assumptions and missing rules are explicit in config/league.json.
The model approach and outstanding inputs are in valuation_design.txt.

REFERENCE DOCUMENTATION

Railway Postgres and service connection variables:
https://docs.railway.com/databases/postgresql
Railway configuration as code:
https://docs.railway.com/config-as-code
Psycopg transaction behavior:
https://www.psycopg.org/psycopg3/docs/basic/transactions.html
