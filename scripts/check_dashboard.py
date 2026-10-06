"""Browser checks against a running dashboard preview; screenshots in artifacts/."""
import argparse
from pathlib import Path

from playwright.sync_api import expect, sync_playwright


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url',default='http://127.0.0.1:8080')
    parser.add_argument('--browser')
    args=parser.parse_args()
    output=Path('artifacts')
    output.mkdir(exist_ok=True)
    with sync_playwright() as playwright:
        browser=playwright.chromium.launch(headless=True,executable_path=args.browser)
        context=browser.new_context(viewport={'width':1440,'height':1050},device_scale_factor=1)
        page=context.new_page()
        errors=[]
        page.on('pageerror',lambda error:errors.append(str(error)))
        page.goto(args.url,wait_until='networkidle')
        expect(page.locator('#results-count')).to_have_text('225')
        expect(page.locator('.stat-value').nth(1)).not_to_have_text('$0')
        page.screenshot(path=str(output/'dashboard-desktop.png'),full_page=False)
        page.locator('[data-kind="keeper"]').click()
        expect(page.locator('#results-count')).to_have_text('30')
        page.locator('#player-search').fill('Jokic')
        expect(page.locator('#results-count')).to_have_text('1')
        page.locator('#history-table .player-button').first.click()
        expect(page.locator('#player-name')).to_have_text('Nikola Jokic')
        expect(page.locator('#player-content')).to_contain_text('$85')
        expect(page.locator('#player-content')).to_contain_text('$84')
        expect(page.locator('.confirmed-keeper')).to_contain_text('Max · $88')
        page.screenshot(path=str(output/'dashboard-player.png'),full_page=True)
        page.keyboard.press('Escape')
        expect(page.locator('#player-dialog')).not_to_be_visible()
        page.locator('#season-select').select_option('all')
        page.locator('[data-kind="auction"]').click()
        expect(page.locator('#history-table')).to_contain_text('2024–25')
        expect(page.locator('#history-table')).not_to_contain_text('2025–26')
        page.locator('#player-search').fill('')
        expect(page.locator('#results-count')).to_have_text('2,173')
        expect(page.locator('.stat-value').first).to_have_text('2,173')
        with page.expect_download() as download:
            page.locator('#export-link').click()
        download.value.save_as(str(output/'browser-export.csv'))
        page.locator('[data-page="2"]').click()
        expect(page.locator('.pager')).to_contain_text('2 /')
        page.locator('[data-view="projections"]').click()
        expect(page.locator('.empty-state')).to_contain_text('No projection set has been loaded')
        page.screenshot(path=str(output/'dashboard-projections.png'),full_page=True)
        page.locator('[data-view="valuations"]').click()
        expect(page.locator('.empty-state')).to_contain_text('No calculated valuations')
        page.locator('[data-view="rosters"]').click()
        page.locator('#season-select').select_option('2025-26')
        page.locator('#team-filter').select_option('Max')
        expect(page.locator('#results-count')).to_have_text('15')
        page.locator('[data-kind="final"]').click()
        expect(page.locator('#results-count')).to_have_text('17')
        page.locator('[data-view="notes"]').click()
        expect(page.locator('.issue-list .issue-row')).to_have_count(27)
        page.locator('[data-view="keepers"]').click()
        expect(page.locator('.keeper-card')).to_have_count(15)
        expect(page.locator('.keeper-player')).to_have_count(30)
        expect(page.locator('.stat-value').nth(1)).to_have_text('$790')
        expect(page.locator('.stat-value').nth(2)).to_have_text('$2,210')
        expect(page.locator('.keeper-card').first).to_contain_text('Alvin')
        page.screenshot(path=str(output/'keepers-desktop.png'),full_page=True,animations='disabled')
        page.locator('#keeper-search').fill('Jokic')
        expect(page.locator('.keeper-card')).to_have_count(1)
        expect(page.locator('[data-franchise="Max"] .keeper-budget')).to_contain_text('$109')
        page.locator('#keeper-search').fill('')
        page.locator('#keeper-sort').select_option('team')
        expect(page.locator('.keeper-card').nth(1)).to_contain_text('Arthur')
        page.set_viewport_size({'width':390,'height':844})
        assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth'), 'Keeper page overflows mobile'
        page.screenshot(path=str(output/'keepers-mobile.png'),full_page=False,animations='disabled')
        page.locator('#menu-button').click()
        expect(page.locator('#sidebar')).to_have_class('sidebar open')
        page.locator('[data-view="history"]').click()
        expect(page.locator('#sidebar')).not_to_have_class('sidebar open')
        page.locator('#team-filter').select_option('')
        expect(page.locator('#results-count')).to_have_text('225')
        expect(page.locator('#nav-scrim')).not_to_be_visible()
        expect(page.locator('#sidebar')).to_have_css('transform','matrix(1, 0, 0, 1, -244, 0)')
        assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth'), 'Mobile layout overflows viewport'
        assert page.locator('#history-table .table-scroll').evaluate('(el) => el.scrollWidth <= el.clientWidth'), 'Mobile prices require sideways scrolling'
        page.screenshot(path=str(output/'dashboard-mobile.png'),full_page=True,animations='disabled')
        page.locator('#history-table .player-button').first.click()
        expect(page.locator('#player-dialog')).to_be_visible()
        expect(page.locator('#player-name')).not_to_have_text('Loading player…')
        page.locator('#close-player').click()
        expect(page.locator('#player-dialog')).not_to_be_visible()
        # Synthetic data stays inside browser request interception; nothing is
        # written to the preview database or the production database.
        bootstrap=page.request.get(args.url+'/api/bootstrap').json()
        dataset={'dataset_id':'browser-test','kind':'projection','season':'2026-27','source_name':'SYNTHETIC BROWSER TEST','as_of_date':'2026-10-01','coverage':'full_season','players':1}
        run={'run_id':'browser-run','season':'2026-27','model_version':'test-only','source_name':'SYNTHETIC BROWSER TEST','created_at':'2026-10-02','players':1}
        bootstrap['datasets']=[dataset]
        bootstrap['runs']=[run]
        projection={'player_id':'nikolajokic','player':'Nikola Jokic','nba_team':'TEST','positions':'C','games':65,'minutes_pg':30,'pts_pg':20,'reb_pg':10,'ast_pg':3,'stl_pg':1,'blk_pg':2,'fg3m_pg':1,'fgm_pg':8,'fga_pg':15,'ftm_pg':3,'fta_pg':4,'draft_status':'kept','keeper_franchise':'Max','confirmed_keeper_cost':88}
        available_projection={**projection,'player_id':'giannisantetokounmpo','player':'Giannis Antetokounmpo','draft_status':'available','keeper_franchise':None,'confirmed_keeper_cost':None}
        valuation={'player_id':'nikolajokic','player':'Nikola Jokic','fair_value':90,'expected_auction_price':88,'recommended_bid_ceiling':89,'lower_estimate':80,'upper_estimate':95,'keeper_cost':85,'keeper_surplus':5,'draft_status':'kept','keeper_franchise':'Max','confirmed_keeper_cost':88}
        available_value={**valuation,'player_id':'giannisantetokounmpo','player':'Giannis Antetokounmpo','draft_status':'available','keeper_franchise':None,'confirmed_keeper_cost':None,'fair_value':65,'recommended_bid_ceiling':68}
        page.route('**/api/bootstrap',lambda route:route.fulfill(json=bootstrap))
        page.route('**/api/projections?*',lambda route:route.fulfill(json={'dataset':dataset,'rows':[projection,available_projection]}))
        page.route('**/api/valuations?*',lambda route:route.fulfill(json={'run':run,'rows':[valuation,available_value]}))
        page.set_viewport_size({'width':1440,'height':1050})
        page.goto(args.url+'/?browser-test=1#projections',wait_until='networkidle')
        expect(page.locator('#data-table')).to_contain_text('53.3%')
        expect(page.locator('#data-table')).to_contain_text('75.0%')
        expect(page.locator('#draft-filter')).to_have_value('available')
        expect(page.locator('#data-table')).not_to_contain_text('Nikola Jokic')
        expect(page.locator('#data-table')).to_contain_text('Giannis Antetokounmpo')
        page.locator('#draft-filter').select_option('kept')
        expect(page.locator('#data-table')).to_contain_text('Kept · Max · $88')
        expect(page.locator('#data-table')).not_to_contain_text('Giannis')
        page.locator('#draft-filter').select_option('all')
        expect(page.locator('#data-count')).to_have_text('2')
        page.locator('#data-search').fill('no-match')
        expect(page.locator('#data-table')).to_contain_text('No matching players')
        page.locator('[data-view="valuations"]').click()
        expect(page.locator('#draft-filter')).to_have_value('available')
        expect(page.locator('#data-table')).not_to_contain_text('Nikola Jokic')
        expect(page.locator('#data-table')).to_contain_text('$68')
        page.locator('#draft-filter').select_option('kept')
        expect(page.locator('#data-table')).to_contain_text('$90')
        expect(page.locator('#data-table')).to_contain_text('$88')
        expect(page.locator('#data-table tbody td').nth(3)).to_have_text('—')
        expect(page.locator('#data-table tbody td').nth(6)).to_have_text('$2')
        expect(page.locator('#dataset-select')).to_have_value('browser-run')
        player=page.request.get(args.url+'/api/players/nikolajokic').json()
        player['valuations']=[{**valuation,'season':'2026-27','risk_notes':'Synthetic browser test only'}]
        page.route('**/api/players/nikolajokic',lambda route:route.fulfill(json=player))
        page.locator('#data-table .player-button').click()
        expect(page.locator('#player-name')).to_have_text('Nikola Jokic')
        expect(page.locator('.drawer-metrics').nth(1).locator('strong').nth(2)).to_have_text('—')
        page.locator('#close-player').click()
        older={**dataset,'dataset_id':'older-browser-test','season':'2025-26'}
        unknown_projection={**projection,'draft_status':'unknown','keeper_franchise':None,'confirmed_keeper_cost':None}
        page.route('**/api/projections?*',lambda route:route.fulfill(json={'dataset':older,'rows':[unknown_projection]}))
        page.locator('[data-view="projections"]').click()
        expect(page.locator('#draft-filter')).to_have_value('all')
        expect(page.locator('.availability-note')).to_contain_text('2025–26')
        expect(page.locator('#data-table')).to_contain_text('Keeper list not supplied')
        assert not errors, errors
        print('PASS: desktop/mobile layouts, history filters, player drawer, CSV, pagination, empty/populated boards, keeper ownership and budgets, availability filters, season isolation, rosters and notes. No browser errors.')
        browser.close()


if __name__=='__main__':
    main()
