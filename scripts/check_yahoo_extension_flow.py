"""Replay the actual extension scripts against a local app and an intercepted Yahoo fixture.
Chrome messaging is emulated; no request is sent to Yahoo and no real draft is written.
"""
import asyncio
import json
import time
from pathlib import Path
from urllib.parse import urlsplit
from playwright.async_api import async_playwright, expect

ROOT=Path(__file__).resolve().parents[1]
BASE='http://127.0.0.1:8767'
YAHOO='https://basketball.fantasysports.yahoo.com/draft?mockId=extension-flow'
OUT=ROOT/'artifacts/yahoo-auto-qa'

async def main():
    OUT.mkdir(parents=True,exist_ok=True)
    async with async_playwright() as pw:
        browser=await pw.chromium.launch(executable_path='C:/Program Files/Google/Chrome/Application/chrome.exe',headless=True)
        context=await browser.new_context(viewport={'width':1200,'height':850})
        response=await context.request.post(BASE+'/__qa/reset')
        assert (await response.json()).get('isolated')
        fixture=(ROOT/'tests/fixtures/yahoo-auction.html').read_text(encoding='utf-8').replace("'Team 10'","'Scott'")
        await context.route('https://basketball.fantasysports.yahoo.com/**',lambda route:route.fulfill(body=fixture,content_type='text/html'))
        await context.route(BASE+'/__qa/worker',lambda route:route.fulfill(body='<html><body>Local worker harness</body></html>',content_type='text/html'))
        app=await context.new_page();yahoo=await context.new_page();worker=await context.new_page()
        pages={1:app,2:yahoo};errors=[]
        for page in [app,yahoo,worker]:
            page.on('pageerror',lambda e:errors.append(str(e)))
        async def host(source,req):
            page=pages[req['id']]
            if req['op']=='tab':
                return {'id':req['id'],'url':page.url,'incognito':False}
            if req['op']=='script':
                if req.get('marker'):
                    return [{'result':await page.locator('meta[name="survivor-app"]').get_attribute('content')}]
                for name in req.get('files',[]):
                    await page.evaluate((ROOT/'extensions/yahoo-draft-watcher'/name).read_text(encoding='utf-8'))
                return [{'result':None}]
            if req['op']=='message':
                return await page.evaluate("""m=>{let answer=null;for(const fn of window._extensionListeners||[])fn(m,{},r=>answer=r);return answer}""",req['message'])
        async def deliver(source,message):
            page=source['page'];ident=next(i for i,p in pages.items() if p==page)
            parsed=urlsplit(page.url);origin=parsed.scheme+'://'+parsed.netloc
            sender={'tab':{'id':ident,'url':page.url,'incognito':False},'url':page.url,'origin':origin}
            return await worker.evaluate('({message,sender})=>new Promise(resolve=>window._workerListener(message,sender,resolve))',{'message':message,'sender':sender})
        await context.expose_binding('_chromeHost',host)
        await context.expose_binding('_toBackground',deliver)
        await worker.goto(BASE+'/__qa/worker')
        await worker.evaluate("""() => {
          window._storage={};
          window.chrome={
            storage:{local:{get:async()=>structuredClone(_storage),set:async v=>Object.assign(_storage,structuredClone(v))}},
            runtime:{getManifest:()=>({version:'0.3.3'}),onMessage:{addListener:f=>window._workerListener=f}},
            tabs:{get:id=>_chromeHost({op:'tab',id}),sendMessage:(id,message)=>_chromeHost({op:'message',id,message}),update:async()=>{},onUpdated:{addListener:()=>{}},onRemoved:{addListener:()=>{}}},
            scripting:{executeScript:o=>_chromeHost({op:'script',id:o.target.tabId,files:o.files,marker:!!o.func})},
            alarms:{create:()=>{},onAlarm:{addListener:()=>{}}}
          };
        }""")
        background=(ROOT/'extensions/yahoo-draft-watcher/background.js').read_text(encoding='utf-8').replace("const primaryApp='https://survivor-production-bdd5.up.railway.app'","const primaryApp='"+BASE+"'")
        await worker.evaluate(background)
        await app.goto(BASE+'/#draft');await expect(app.locator('#draft-team')).to_be_visible()
        original=await app.evaluate("localStorage.getItem('survivor.draft.v1')")
        await app.goto(BASE+'/?yahooMock=1#draft');await expect(app.locator('#draft-team')).to_be_visible()
        await yahoo.goto(YAHOO)
        for page in [app,yahoo]:
            await page.evaluate("""() => {window._extensionListeners=[];window.chrome={runtime:{sendMessage:m=>_toBackground(m),onMessage:{addListener:f=>_extensionListeners.push(f)}}};}""")
        await app.evaluate((ROOT/'extensions/yahoo-draft-watcher/bridge.js').read_text(encoding='utf-8'))
        await yahoo.evaluate((ROOT/'extensions/yahoo-draft-watcher/yahoo-reader.js').read_text(encoding='utf-8'))
        await yahoo.evaluate((ROOT/'extensions/yahoo-draft-watcher/capture.js').read_text(encoding='utf-8'))
        await expect(app.locator('#draft-team')).to_have_value('You')
        await expect(app.locator('#draft-team option')).to_have_count(14)
        await expect(app.locator('#draft-wallet')).to_contain_text('13 slots left')
        await worker.wait_for_function('_storage.draftWatcher?.roomReady')
        began=time.monotonic()
        await yahoo.evaluate("""() => {document.querySelector('#player').textContent='G. ANTETOKOUNMPO';document.querySelector('#price').textContent='$6';document.querySelector('#bidder').textContent='Scott';document.querySelector('#teams').lastElementChild.insertAdjacentHTML('afterbegin','<span class="bid-badge">$6</span> ');}""")
        await expect(app.locator('#draft-nomination')).to_contain_text('Giannis Antetokounmpo')
        await expect(app.locator('.draft-bidline')).to_contain_text('Scott')
        await expect(app.locator('.draft-bidline')).to_contain_text('$6')
        latency=(time.monotonic()-began)*1000
        await expect(app.locator('#draft-source')).to_have_text('Watching Yahoo')
        # Clock ticks must render even without any new bid or roster event.
        journal_size=await app.evaluate("JSON.parse(localStorage.getItem('survivor.yahoo-mock.v1')).sessions.live.events.length")
        await yahoo.evaluate("document.querySelector('#auction time').textContent='00:06'")
        await expect(app.locator('#live-draft-clock')).to_have_text('00:06',timeout=1200)
        assert await app.evaluate("JSON.parse(localStorage.getItem('survivor.yahoo-mock.v1')).sessions.live.events.length")==journal_size
        for amount,team in [(7,'You'),(8,'Emre'),(41,'Scott')]:
            await yahoo.evaluate("""([price,team])=>{document.querySelector('#price').textContent='$'+price;document.querySelector('#bidder').textContent=team;document.querySelector('#auction time').textContent='00:10';}""",[amount,team])
            await expect(app.locator('.draft-bidline')).to_contain_text('$'+str(amount))
            await expect(app.locator('.draft-bidline')).to_contain_text(team)
            await expect(app.locator('#live-draft-clock')).to_have_text('00:10',timeout=1200)
        await yahoo.evaluate("document.querySelector('#auction time').textContent='00:09'")
        await expect(app.locator('#live-draft-clock')).to_have_text('00:09',timeout=1200)
        await expect(app.locator('#draft-controls')).to_contain_text('0 sales')
        await yahoo.evaluate("document.querySelector('#sold').textContent='Sold!'")
        await expect(app.locator('#draft-controls')).to_contain_text('1 sales')
        await expect(app.locator('#draft-teams')).to_contain_text('$159')
        await yahoo.evaluate("""() => {document.querySelector('#last-pick').innerHTML='<b>G. ANTETOKOUNMPO</b><span> Scott </span><b>$41</b>';document.querySelector('#sold').textContent='';document.querySelector('#player').textContent='N. JOKIC';document.querySelector('#price').textContent='$1';document.querySelector('#bidder').textContent='Emre';}""")
        await expect(app.locator('#draft-nomination')).to_contain_text('Nikola Jokic')
        await expect(app.locator('.draft-bidline')).to_contain_text('$1')
        # Screenshot regression: the own-team row says You, but the card and
        # price-less Last pick show the manager's display name.
        await yahoo.evaluate("""() => {
            document.querySelector('#price').textContent='$75';document.querySelector('#bidder').textContent='max';
            document.querySelector('#teams').firstElementChild.insertAdjacentHTML('afterbegin','<small class="bid-badge">$75</small> ');
        }""")
        await expect(app.locator('.draft-bidline')).to_contain_text('$75')
        await expect(app.locator('.draft-bidline')).to_contain_text('You')
        await expect(app.locator('.draft-decision')).to_have_text('HOLD')
        await yahoo.evaluate("""() => {
            document.querySelector('#last-pick').innerHTML='<span>Last: </span><b>N. JOKIC (C - DEN)</b><span> max</span>';
            const row=document.querySelector('#teams').firstElementChild;
            row.querySelector('b').textContent='$125';row.lastElementChild.textContent='1/13';
            document.querySelector('#auction footer').innerHTML='<span>Max Offer <b>$114</b> · Budget <b>$125</b></span><time>00:07</time>';
            document.querySelector('#player').textContent='V. WEMBANYAMA';document.querySelector('#price').textContent='$72';document.querySelector('#bidder').textContent='Scott';
            document.querySelector('#teams').lastElementChild.querySelector('.bid-badge').textContent='$72';
        }""")
        await expect(app.locator('#draft-controls')).to_contain_text('2 sales')
        await expect(app.locator('#draft-wallet')).to_contain_text('$125')
        await expect(app.locator('#draft-wallet')).to_contain_text('12 slots left')
        await expect(app.locator('#draft-nomination')).to_contain_text('Victor Wembanyama')
        await expect(app.locator('.draft-bidline')).to_contain_text('$72')
        await expect(app.locator('.draft-decision')).not_to_have_text('CHECK SYNC')
        state=await app.evaluate("JSON.parse(localStorage.getItem('survivor.yahoo-mock.v1')).sessions.live")
        bids=[e for e in state['events'] if e['type']=='bid']
        assert [(e['amount'],e['team']) for e in bids[:4]]==[(6,'Scott'),(7,'You'),(8,'Emre'),(41,'Scott')],bids
        assert len([e for e in state['events'] if e['type']=='sale'])==2,state['events']
        assert any(e['type']=='sale' and e['team']=='You' and e['amount']==75 for e in state['events'])
        assert not await app.evaluate("JSON.parse(localStorage.getItem('survivor.yahoo-mock.v1')).pending")
        assert await app.evaluate("localStorage.getItem('survivor.draft.v1')")==original
        assert (await (await context.request.get(BASE+'/api/drafts')).json())['drafts']==[]
        diagnostic=await worker.evaluate("()=>new Promise(resolve=>_workerListener({type:'diagnostics'},{},resolve))")
        assert diagnostic['capture']['readerVersion']=='0.3.3'
        assert diagnostic['capture']['cataloguePlayers']>300
        assert diagnostic['capture']['observation']['player']=='Victor Wembanyama'
        assert not errors,errors
        await app.screenshot(path=str(OUT/'extension-flow-nomination.png'))
        (OUT/'extension-flow-report.json').write_text(json.dumps({'fixture':'illustrative HTML with nested controls, projected price, bid badges, You/max identity and price-less Last pick','realExtensionScripts':True,'chromeMessaging':'emulated','importedTeams':14,'rosterSize':13,'observedBids':[(e['amount'],e['team']) for e in bids],'sales':2,'ownWinningPrice':75,'ownRemainingBudget':125,'nextNomination':'Victor Wembanyama','firstBidRenderMs':latency,'realStorageUnchanged':True,'recordedRealDrafts':0,'pageErrors':errors},indent=2),encoding='utf-8')
        await browser.close()
    print('Full local extension flow passed: auto import, nomination, bid ladder, sale, next nomination, mock isolation')

if __name__=='__main__':
    asyncio.run(main())
