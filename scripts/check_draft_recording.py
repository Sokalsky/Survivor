"""Exercise recording against the explicitly isolated, local in-memory QA server only."""
import argparse, json
from pathlib import Path
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright, expect

ROOT=Path(__file__).resolve().parents[1]
KEY='local-qa-only-recording-key-0123456789'


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--url',default='http://127.0.0.1:8767');parser.add_argument('--browser');args=parser.parse_args()
    if urlparse(args.url).hostname not in ('localhost','127.0.0.1'):
        raise SystemExit('Recording QA only runs on an isolated local preview, never production.')
    out=ROOT/'artifacts/recording-qa';out.mkdir(parents=True,exist_ok=True)
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True,executable_path=args.browser)
        context=browser.new_context(viewport={'width':1440,'height':1100});page=context.new_page();errors=[]
        page.on('pageerror',lambda e:errors.append(str(e)))
        reset=context.request.post(args.url+'/__qa/reset')
        assert reset.ok and reset.json()=={'isolated':True},'Use the in-memory recording QA preview.'
        page.goto(args.url+'/#draft',wait_until='networkidle')
        expect(page.locator('#draft-recording-status')).to_contain_text('Local copy only')
        page.locator('[data-draft="recording"]').click();page.locator('#draft-recording-form input').fill(KEY);page.locator('#draft-recording-form button').click()
        expect(page.locator('#draft-recording-status')).to_contain_text('Saved 0 events')
        info=page.evaluate("""() => {const r=JSON.parse(localStorage.getItem('survivor.draft.v1')),s=r.sessions.live;const available=s.players.filter(p=>!s.keepers.some(k=>k.playerId===p.player_id));return {id:s.id,players:available.slice(0,3),teams:s.teams.map(t=>t.name)};}""")
        def send(target,events,mock=False):
            store='survivor.yahoo-mock.v1' if mock else 'survivor.draft.v1'
            target.evaluate("""({store,events}) => {const s=JSON.parse(localStorage.getItem(store)).sessions.live;window.postMessage({channel:'survivor-draft-extension',type:'hello',nonce:'local-recording-qa'},location.origin);setTimeout(()=>window.postMessage({channel:'survivor-draft-extension',type:'events',nonce:'local-recording-qa',sessionId:s.id,events:events.map(e=>({...e,at:new Date().toISOString()}))},location.origin),0);}""",{'store':store,'events':events})
        def saved(n):
            expect(page.locator('#draft-recording-status')).to_contain_text('Saved '+str(n)+' events',timeout=20000)
        player=info['players'][0]['player'];a,b='Max','Alvin'
        send(page,[dict(id='n1',type='nominate',player=player),dict(id='b1',type='bid',player=player,team=a,amount=1),dict(id='b2',type='bid',player=player,team=b,amount=2),dict(id='b3',type='bid',player=player,team=a,amount=3),dict(id='s1',type='sale',player=player,team=b,amount=4)])
        saved(5)
        data=context.request.get(args.url+'/api/drafts/'+info['id']).json()
        assert len(data['session']['events'])==5
        assert next(t for t in data['teams'] if t['name']==b)['roster'][-1]['playerId']==info['players'][0]['player_id']
        # Corrections retain the original evidence and update all roster projections.
        page.locator('[data-draft="record"]').click();page.locator('#draft-undo-target').select_option('s1');page.locator('[data-draft="undo"]').click()
        page.locator('#draft-record-form [name="type"]').select_option('sale');page.locator('#draft-record-form [name="team"]').select_option(a);page.locator('#draft-record-form [name="amount"]').fill('3');page.locator('#draft-record-form button').click();saved(7)
        data=context.request.get(args.url+'/api/drafts/'+info['id']).json()
        expected=page.evaluate("""() => {const s=JSON.parse(localStorage.getItem('survivor.draft.v1')).sessions.live,st=SurvivorDraft.replay(s);return s.teams.map(t=>{const o=SurvivorDraft.outlook(st,t.name);return {name:t.name,values:o.owned.values,averages:o.owned.averages,points:o.overall.current.points,rank:o.overall.current.rank};});}""")
        for actual in data['teams']:
            want=next(t for t in expected if t['name']==actual['name'])
            for key in want['values']:
                assert abs(actual['values'][key]-want['values'][key])<1e-6,(actual['name'],key)
                assert abs(actual['averages'][key]-want['averages'][key])<1e-6
            assert actual['points']==want['points'] and actual['rank']==want['rank']
        # An interrupted upload cannot stall Yahoo capture; a retry is idempotent.
        page.route('**/api/drafts/*/events',lambda route:route.abort())
        player2=info['players'][1]['player']
        send(page,[dict(id='n2',type='nominate',player=player2),dict(id='b4',type='bid',player=player2,team=b,amount=2),dict(id='s2',type='sale',player=player2,team=b,amount=2)])
        expect(page.locator('#draft-recording-status')).to_contain_text('local events retained',timeout=10000)
        assert page.evaluate("JSON.parse(localStorage.getItem('survivor.draft.v1')).sessions.live.events.length")==10
        assert context.request.get(args.url+'/api/drafts/'+info['id']).json()['record']['revision']==7
        page.unroute('**/api/drafts/*/events');page.locator('[data-draft="recording"]').click();page.locator('[data-record-action="retry"]').click();saved(10)
        page.reload(wait_until='networkidle');saved(10)
        # Archive contains season team totals, every roster, past snapshots and the full bid log.
        page.locator('[data-draft="archives"]').click();page.locator('[data-saved-draft]').click()
        expect(page.locator('#draft-dialog-body')).to_contain_text('Saved team roster');expect(page.locator('#draft-dialog-body')).to_contain_text('Complete saved event log')
        expect(page.locator('.archive-undone')).to_have_count(1)
        page.locator('#archive-snapshot').select_option('0');expect(page.locator('#archive-snapshot')).to_have_value('0')
        page.locator('#draft-dialog').screenshot(path=str(out/'saved-draft-desktop.png'))
        page.locator('[data-draft="close-dialog"]').click();page.locator('[data-draft="managers"]').click()
        expect(page.locator('#draft-dialog-body')).to_contain_text('Bid without winning');page.locator('#draft-dialog').screenshot(path=str(out/'manager-history.png'))
        page.locator('[data-draft="close-dialog"]').click()
        # Another browser profile can read the records with no recording key or local session.
        other=browser.new_context();reader=other.new_page();reader.goto(args.url+'/#draft',wait_until='networkidle');reader.locator('[data-draft="archives"]').click();reader.locator('[data-saved-draft]').click()
        expect(reader.locator('#draft-dialog-body')).to_contain_text('10 saved events');other.close()
        # Yahoo mock shares the site, but uses a different store and issues no recording writes.
        real_before=page.evaluate("localStorage.getItem('survivor.draft.v1')")
        mock=context.new_page();mock.on('pageerror',lambda e:errors.append(str(e)));writes=[]
        def record_write(request):
            # Watch recording writes on any host, excluding unrelated browser/antivirus traffic.
            path=urlparse(request.url).path
            if request.method not in ('GET','HEAD','OPTIONS') and (path=='/api/drafts' or path.startswith('/api/drafts/')):
                writes.append(request.url)
        mock.on('request',record_write)
        mock.goto(args.url+'/?yahooMock=1#draft',wait_until='networkidle');mock.locator('[data-draft="mock-settings"]').click()
        mock.locator('#yahoo-mock-settings [name="teams"]').fill('Mock One\nMock Two');mock.locator('#yahoo-mock-settings [name="rosterSize"]').fill('2');mock.locator('#yahoo-mock-settings button').click()
        send(mock,[dict(id='mn',type='nominate',player=player),dict(id='mb',type='bid',player=player,team='Mock Two',amount=55),dict(id='ms',type='sale',player=player,team='Mock Two',amount=55)],True)
        mock.wait_for_function("JSON.parse(localStorage.getItem('survivor.yahoo-mock.v1')).sessions.live.events.length===3")
        mock.locator('[data-draft="recording"]').evaluate('(el)=>el.click()');expect(mock.locator('#draft-error')).to_contain_text('mock workspace')
        assert page.evaluate("localStorage.getItem('survivor.draft.v1')")==real_before
        assert context.request.get(args.url+'/api/drafts/'+info['id']).json()['record']['revision']==10
        assert not writes,writes
        assert 'Mock Two' not in context.request.get(args.url+'/api/draft-managers').json()['teams']
        mock.screenshot(path=str(out/'yahoo-mock-isolation.png'));mock.close()
        page.locator('[data-draft="recording"]').click();page.locator('[data-record-action="complete"]').click()
        expect(page.locator('#draft-recording-status')).to_contain_text('Draft complete')
        assert context.request.get(args.url+'/api/drafts/'+info['id']).json()['record']['status']=='complete'
        page.set_viewport_size({'width':390,'height':844});page.wait_for_timeout(300);page.locator('[data-draft="archives"]').click();page.locator('[data-saved-draft]').click()
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        page.locator('#draft-dialog').screenshot(path=str(out/'saved-draft-mobile.png'))
        assert not errors,errors
        (out/'browser-report.json').write_text(json.dumps({'checks':'automatic real capture persistence, losing bids, correction journal, Python/JS projection parity, offline capture and retry, reload, public cross-profile archive, Yahoo mock isolation, completion, responsive archive','events':10,'page_errors':errors},indent=2))
        browser.close()
    print('Draft recording browser checks passed')

if __name__=='__main__':main()
