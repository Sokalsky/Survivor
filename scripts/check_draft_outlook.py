"""Check category impacts and team outlook against the real local dashboard."""
import argparse, json
from pathlib import Path
from playwright.sync_api import sync_playwright, expect
ROOT=Path(__file__).resolve().parents[1]

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--url',default='http://127.0.0.1:8766')
    parser.add_argument('--browser')
    parser.add_argument('--local-assets',action='store_true',help='Preview local draft assets against the supplied dashboard')
    args=parser.parse_args()
    out=ROOT/'artifacts'/'draft-outlook-qa'
    out.mkdir(parents=True,exist_ok=True)
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True,executable_path=args.browser)
        page=browser.new_page(viewport={'width':1440,'height':1100})
        errors=[]
        page.on('pageerror',lambda e:errors.append(str(e)))
        if args.local_assets:
            for asset in ['draft-engine.js','draft-room.js','draft-room.css']:
                mime='text/css' if asset.endswith('.css') else 'text/javascript'
                page.route('**/static/'+asset+'*',lambda route,request,asset=asset,mime=mime:route.fulfill(path=str(ROOT/'survivor/web/static'/asset),content_type=mime))
        page.goto(args.url,wait_until='networkidle')
        expect(page.locator('.outlook-category')).to_have_count(8)
        expect(page.locator('[data-overall-place]')).to_be_visible()
        expect(page.locator('[data-overall-points]')).to_contain_text('/ 120')
        expect(page.locator('.wallet-standing')).to_contain_text('roto pts')
        expect(page.locator('.outlook-heading')).to_contain_text('2 / 15 players drafted')
        live=page.evaluate("JSON.stringify(JSON.parse(localStorage.getItem('survivor.draft.v1')).sessions.live)")
        page.locator('.draft-toolbar [data-draft="mode"]').click()
        expect(page.locator('.stat-impact')).to_have_count(8)
        expect(page.locator('.stat-priority')).to_have_count(page.locator('.outlook-category.need').count())
        expected=page.evaluate("""() => {
          const r=JSON.parse(localStorage.getItem('survivor.draft.v1')),s=r.sessions.practice;
          const owned=s.keepers.filter(k=>k.team===r.team).map(k=>s.players.find(p=>p.player_id===k.playerId));
          const nominated=s.players.find(p=>p.player_id===s.events.find(e=>e.type==='nominate').playerId);
          const average=ps=>ps.reduce((n,p)=>n+p.blk_pg*p.games,0)/ps.reduce((n,p)=>n+p.games,0);
          return [average(owned).toFixed(2),average([...owned,nominated]).toFixed(2)];
        }""")
        blocks=page.locator('[data-stat-category="BLK"] .stat-impact')
        expect(blocks).to_contain_text(expected[0]+' → '+expected[1])
        # Independently compare current roster rates with the median team rate.
        rates=page.evaluate("""() => {
          const r=JSON.parse(localStorage.getItem('survivor.draft.v1')),s=r.sessions.practice;
          const fields={PTS:'pts_pg',REB:'reb_pg',AST:'ast_pg',STL:'stl_pg',BLK:'blk_pg','3PM':'fg3m_pg'};
          const profiles=s.teams.map(({name})=>{
            const ps=s.keepers.filter(k=>k.team===name).map(k=>s.players.find(p=>p.player_id===k.playerId));
            const games=ps.reduce((n,p)=>n+p.games,0),sum=k=>ps.reduce((n,p)=>n+p[k]*p.games,0);
            return {name,games,players:ps.length,values:Object.fromEntries(Object.entries(fields).map(([k,f])=>[k,sum(f)/games]))};
          });
          const me=profiles.find(t=>t.name===r.team),median=vs=>vs.sort((a,b)=>a-b)[Math.floor(vs.length/2)];
          return {games:me.games,players:me.players,medianGames:median(profiles.map(t=>t.games)),categories:Object.keys(fields).map(k=>({category:k,value:me.values[k].toFixed(2),median:median(profiles.map(t=>t.values[k])).toFixed(2)}))};
        }""")
        for rate in rates['categories']:
            card=page.locator('.outlook-category[data-outlook-category="'+rate['category']+'"]')
            expect(card.locator('.category-average b')).to_contain_text(rate['value']+' / player-game')
            expect(card.locator('.category-average>small')).to_contain_text('League median '+rate['median'])
            expect(card.locator('em')).to_contain_text('Total:')
            expect(card.locator('.category-average>span')).to_contain_text('Average:')
        expect(page.locator('.outlook-volume')).to_contain_text(str(rates['players'])+' players')
        expect(page.locator('.outlook-volume')).to_contain_text(str(round(rates['games']))+' projected games')
        expect(page.locator('.outlook-volume')).to_contain_text('League median '+str(round(rates['medianGames'])))
        for category in ['FG%','FT%']:
            expect(page.locator('.outlook-category[data-outlook-category="'+category+'"] .category-average')).to_contain_text('Attempt-weighted rate')
        page.locator('[data-draft="outlook-details"]').click()
        expect(page.locator('.outlook-table-scroll .outlook-table tbody tr')).to_have_count(8)
        expect(page.locator('.outlook-note').first).to_contain_text('to your current roster')
        expect(page.locator('.overall-preview')).to_be_visible()
        expect(page.locator('.overall-table tbody tr')).to_have_count(15)
        expect(page.locator('.overall-table .your-standing')).to_contain_text('Max · YOU')
        total=page.evaluate("""() => {
          const r=JSON.parse(localStorage.getItem('survivor.draft.v1')),o=SurvivorDraft.outlook(SurvivorDraft.replay(r.sessions.practice),r.team);
          return o.overall.current.points;
        }""")
        expect(page.locator('[data-overall-points]')).to_have_text(str(int(total) if float(total).is_integer() else total)+' / 120')
        assert sum(float(x) for x in page.locator('.outlook-table-scroll tbody tr td:nth-child(4)').all_text_contents())==total
        expect(page.locator('[data-roto-total]')).to_have_text(str(int(total) if float(total).is_integer() else total))
        expect(page.locator('.outlook-table-scroll .outlook-table')).to_contain_text('Season total')
        expect(page.locator('.outlook-table-scroll .outlook-table')).to_contain_text('Rank now')
        expect(page.locator('.outlook-table-scroll .outlook-table')).to_contain_text('Roto points')
        expect(page.locator('#draft-outlook')).not_to_contain_text('Estimated finish')
        expect(page.locator('#draft-outlook')).not_to_contain_text('estimated fill')
        owned_pts=page.evaluate("""() => {
          const r=JSON.parse(localStorage.getItem('survivor.draft.v1')),s=r.sessions.practice;
          return Math.round(s.keepers.filter(k=>k.team===r.team).reduce((n,k)=>{
            const p=s.players.find(p=>p.player_id===k.playerId);return n+p.games*p.pts_pg;
          },0)).toLocaleString('en-US');
        }""")
        expect(page.locator('.outlook-table-scroll tbody tr').first.locator('td').first).to_have_text(owned_pts)
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
            page.wait_for_timeout(300)
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'),label
            page.locator('.nomination').scroll_into_view_if_needed()
            page.screenshot(path=str(out/(label+'-nomination.png')),full_page=False)
            page.locator('#draft-outlook').screenshot(path=str(out/(label+'-outlook.png')))
        page.set_viewport_size({'width':1440,'height':1100})
        page.locator('[data-draft="settings"]').click()
        page.locator('[name="gamesCap"]').fill('500')
        page.locator('#draft-settings-form button').click()
        expect(page.locator('.outlook-foot')).to_contain_text('500 limit')
        assert page.evaluate("""() => {const r=JSON.parse(localStorage.getItem('survivor.draft.v1'));return SurvivorDraft.outlook(SurvivorDraft.replay(r.sessions.practice),r.team).owned.games<=500;}""")
        assert page.evaluate("JSON.stringify(JSON.parse(localStorage.getItem('survivor.draft.v1')).sessions.live)")==live
        assert not errors,errors
        (out/'browser-report.json').write_text(json.dumps({'checks':'all eight impacts, independent rate/median/volume checks, total footer, standings, comparison, keyboard category sort, team switch, bid refresh, responsive layouts, games cap and live isolation','page_errors':errors},indent=2))
        browser.close()
    print('Draft outlook browser checks passed')

if __name__=='__main__':
    main()
