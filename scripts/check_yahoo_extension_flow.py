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
            runtime:{getManifest:()=>({version:'0.3.1'}),onMessage:{addListener:f=>window._workerListener=f}},
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
        for amount,team in [(7,'You'),(8,'Emre'),(41,'Scott')]:
            await yahoo.evaluate("""([price,team])=>{document.querySelector('#price').textContent='$'+price;document.querySelector('#bidder').textContent=team;}""",[amount,team])
            await expect(app.locator('.draft-bidline')).to_contain_text('$'+str(amount))
            await expect(app.locator('.draft-bidline')).to_contain_text(team)
        await yahoo.evaluate("document.querySelector('#sold').textContent='Sold!'")
        await expect(app.locator('#draft-controls')).to_contain_text('1 sales')
        await expect(app.locator('#draft-teams')).to_contain_text('$159')
        await yahoo.evaluate("""() => {document.querySelector('#last-pick').innerHTML='<b>G. ANTETOKOUNMPO</b><span> Scott </span><b>$41</b>';document.querySelector('#sold').textContent='';document.querySelector('#player').textContent='N. JOKIC';document.querySelector('#price').textContent='$1';document.querySelector('#bidder').textContent='Emre';}""")
        await expect(app.locator('#draft-nomination')).to_contain_text('Nikola Jokic')
        await expect(app.locator('.draft-bidline')).to_contain_text('$1')
        state=await app.evaluate("JSON.parse(localStorage.getItem('survivor.yahoo-mock.v1')).sessions.live")
        bids=[e for e in state['events'] if e['type']=='bid']
        assert [(e['amount'],e['team']) for e in bids[:4]]==[(6,'Scott'),(7,'You'),(8,'Emre'),(41,'Scott')],bids
        assert len([e for e in state['events'] if e['type']=='sale'])==1,state['events']
        assert await app.evaluate("localStorage.getItem('survivor.draft.v1')")==original
        assert (await (await context.request.get(BASE+'/api/drafts')).json())['drafts']==[]
        diagnostic=await worker.evaluate("()=>new Promise(resolve=>_workerListener({type:'diagnostics'},{},resolve))")
        assert diagnostic['capture']['readerVersion']=='0.3.1'
        assert diagnostic['capture']['cataloguePlayers']>300
        assert diagnostic['capture']['observation']['player']=='Nikola Jokic'
        assert not errors,errors
        await app.screenshot(path=str(OUT/'extension-flow-nomination.png'))
        (OUT/'extension-flow-report.json').write_text(json.dumps({'fixture':'illustrative HTML with nested controls, projected price and bid badges','realExtensionScripts':True,'chromeMessaging':'emulated','importedTeams':14,'rosterSize':13,'observedBids':[(e['amount'],e['team']) for e in bids],'sales':1,'winningBudget':159,'firstBidRenderMs':latency,'realStorageUnchanged':True,'recordedRealDrafts':0,'pageErrors':errors},indent=2),encoding='utf-8')
        await browser.close()
    print('Full local extension flow passed: auto import, nomination, bid ladder, sale, next nomination, mock isolation')

if __name__=='__main__':
    asyncio.run(main())
