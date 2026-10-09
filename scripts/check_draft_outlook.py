"""Check category impacts and team outlook against the real local dashboard."""
import argparse, json
from pathlib import Path
from playwright.sync_api import sync_playwright, expect
ROOT=Path(__file__).resolve().parents[1]

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--url',default='http://127.0.0.1:8766')
    parser.add_argument('--browser')
    args=parser.parse_args()
    out=ROOT/'artifacts'/'draft-outlook-qa'
    out.mkdir(parents=True,exist_ok=True)
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True,executable_path=args.browser)
        page=browser.new_page(viewport={'width':1440,'height':1100})
        errors=[]
        page.on('pageerror',lambda e:errors.append(str(e)))
        page.goto(args.url,wait_until='networkidle')
        expect(page.locator('.outlook-category')).to_have_count(8)
        expect(page.locator('[data-overall-place]')).to_be_visible()
        expect(page.locator('[data-overall-points]')).to_contain_text('/ 120')
        expect(page.locator('.wallet-standing')).to_contain_text('roto pts')
        expect(page.locator('.outlook-heading')).to_contain_text('Provisional')
        live=page.evaluate("JSON.stringify(JSON.parse(localStorage.getItem('survivor.draft.v1')).sessions.live)")
        page.locator('.draft-toolbar [data-draft="mode"]').click()
        expect(page.locator('.stat-impact')).to_have_count(8)
        expect(page.locator('.stat-priority').first).to_be_visible()
        expected=page.evaluate("""() => {
          const r=JSON.parse(localStorage.getItem('survivor.draft.v1')),s=r.sessions.practice;
          const owned=s.keepers.filter(k=>k.team===r.team).map(k=>s.players.find(p=>p.player_id===k.playerId));
          const nominated=s.players.find(p=>p.player_id===s.events.find(e=>e.type==='nominate').playerId);
          const average=ps=>ps.reduce((n,p)=>n+p.blk_pg*p.games,0)/ps.reduce((n,p)=>n+p.games,0);
          return [average(owned).toFixed(2),average([...owned,nominated]).toFixed(2)];
        }""")
        blocks=page.locator('[data-stat-category="BLK"] .stat-impact')
        expect(blocks).to_contain_text(expected[0]+' → '+expected[1])
        page.locator('[data-draft="outlook-details"]').click()
        expect(page.locator('.outlook-table-scroll .outlook-table tbody tr')).to_have_count(8)
        expect(page.locator('.outlook-note').first).to_contain_text('replaces one estimated slot')
        expect(page.locator('.overall-preview')).to_be_visible()
        expect(page.locator('.overall-table tbody tr')).to_have_count(15)
        expect(page.locator('.overall-table .your-standing')).to_contain_text('Max · YOU')
        total=page.evaluate("""() => {
          const r=JSON.parse(localStorage.getItem('survivor.draft.v1')),o=SurvivorDraft.outlook(SurvivorDraft.replay(r.sessions.practice),r.team);
          return o.overall.current.points;
        }""")
        expect(page.locator('[data-overall-points]')).to_have_text(str(int(total) if float(total).is_integer() else total)+' / 120')
        expect(page.locator('.outlook-table-scroll .outlook-table')).to_contain_text('Owned players')
        page.locator('.outlook-category[data-outlook-category="BLK"]').focus()
        page.keyboard.press('Enter')
        expect(page.locator('#draft-sort')).to_have_value('need:BLK')
        expect(page.locator('.outlook-category[data-outlook-category="BLK"]')).to_have_attribute('aria-pressed','true')
        first=page.locator('.draft-player-table tbody .draft-player').first.inner_text().splitlines()[0]
        wanted=page.evaluate("""() => {
          const r=JSON.parse(localStorage.getItem('survivor.draft.v1')),b=SurvivorDraft.board(SurvivorDraft.replay(r.sessions.practice),r.team);
          return b.rows.sort((a,b)=>b.fit.impact.BLK.change-a.fit.impact.BLK.change)[0].player;
        }""")
        assert first==wanted,(first,wanted)
        before=page.locator('.outlook-table-scroll .outlook-table tbody tr').first.locator('td').first.inner_text()
        page.locator('#draft-team').select_option('Alvin')
        assert page.locator('.outlook-table-scroll .outlook-table tbody tr').first.locator('td').first.inner_text()!=before
        expect(page.locator('.outlook-heading p')).to_contain_text('Alvin')
        expect(page.locator('.overall-table .your-standing')).to_contain_text('Alvin · YOU')
        page.locator('#draft-team').select_option('Max')
        page.locator('#mock-play').click()
        expect(page.locator('.draft-bidline')).not_to_contain_text('Awaiting first bid')
        page.locator('#mock-play').click()
        expect(page.locator('.outlook-details')).to_be_visible()
        expect(page.locator('#draft-sort')).to_have_value('need:BLK')
        for width,height,label in [(1920,1100,'wide'),(1440,1100,'desktop'),(900,1000,'split'),(390,844,'mobile')]:
            page.set_viewport_size({'width':width,'height':height})
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'),label
            page.locator('.nomination').scroll_into_view_if_needed()
            page.screenshot(path=str(out/(label+'-nomination.png')),full_page=False)
            page.locator('#draft-outlook').screenshot(path=str(out/(label+'-outlook.png')))
        page.set_viewport_size({'width':1440,'height':1100})
        page.locator('[data-draft="settings"]').click()
        page.locator('[name="gamesCap"]').fill('500')
        page.locator('#draft-settings-form button').click()
        expect(page.locator('.outlook-foot')).to_contain_text('500 limit')
        assert page.evaluate("""() => {const r=JSON.parse(localStorage.getItem('survivor.draft.v1'));return SurvivorDraft.outlook(SurvivorDraft.replay(r.sessions.practice),r.team).projected.games<=500;}""")
        assert page.evaluate("JSON.stringify(JSON.parse(localStorage.getItem('survivor.draft.v1')).sessions.live)")==live
        assert not errors,errors
        (out/'browser-report.json').write_text(json.dumps({'checks':'all eight impacts, independent average check, standings, comparison, keyboard category sort, team switch, bid refresh, responsive layouts, games cap and live isolation','page_errors':errors},indent=2))
        browser.close()
    print('Draft outlook browser checks passed')

if __name__=='__main__':
    main()
