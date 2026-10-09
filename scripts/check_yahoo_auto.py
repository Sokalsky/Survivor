"""Isolated browser checks; illustrative auction fixture, never the actual Yahoo service."""
import json
from pathlib import Path
import sys
from playwright.sync_api import sync_playwright, expect

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'artifacts/yahoo-auto-qa'

def main():
    OUT.mkdir(parents=True,exist_ok=True)
    with sync_playwright() as pw:
        browser=pw.chromium.launch(executable_path='C:/Program Files/Google/Chrome/Application/chrome.exe',headless=True)
        page=browser.new_page(viewport={'width':1280,'height':900})
        errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
        page.set_content((ROOT/'tests/fixtures/yahoo-auction.html').read_text(encoding='utf-8'))
        page.add_script_tag(path=str(ROOT/'extensions/yahoo-draft-watcher/yahoo-reader.js'))
        page.evaluate("window.players=[{player:'Nikola Jokic'},{player:'Victor Wembanyama'},{player:'Jalen Williams'},{player:'Jaylin Williams'}]")
        scan=lambda:page.evaluate('SurvivorYahooReader.scan(document,players)')
        initial=scan();assert initial['complete'] and len(initial['teams'])==14,initial
        assert initial['rosterSize']==13 and initial['ownTeam']=='You' and initial['player'] is None,initial
        # Automatic fields must distinguish current bid from offer input, max offer and wallet.
        page.evaluate("document.querySelector('#player').textContent='N. JOKIĆ';document.querySelector('#price').textContent='$37';document.querySelector('#bidder').textContent='Emre';document.querySelector('#teams').children[1].insertAdjacentHTML('afterbegin','<span>$37</span> ')")
        current=scan();assert current['player']=='Nikola Jokic' and current['amount']==37 and current['team']=='Emre',current
        assert len(current['teams'])==14,current
        page.evaluate('window.readerBeforeReinjection=SurvivorYahooReader')
        page.add_script_tag(path=str(ROOT/'extensions/yahoo-draft-watcher/yahoo-reader.js'))
        assert page.evaluate('SurvivorYahooReader===readerBeforeReinjection')
        page.evaluate("document.querySelector('#last-pick').innerHTML='<strong>N. JOKIĆ</strong><span> Emre </span><b>$45</b>';document.querySelector('#player').textContent='V. WEMBANYAMA';document.querySelector('#price').textContent='$1';document.querySelector('#bidder').textContent='You'")
        result=scan();assert result['results']==[{'player':'Nikola Jokic','team':'Emre','amount':45}],result
        assert result['player']=='Victor Wembanyama' and result['amount']==1,result
        page.evaluate("document.querySelector('#player').textContent='J. WILLIAMS'")
        assert scan()['player'] is None
        page.evaluate("document.querySelector('#player').textContent='Victor Wembanyama';document.querySelector('#sold').textContent='Sold!'")
        assert scan()['sold'] is True
        # A virtualized team list with an explicit larger count is not treated as complete.
        page.evaluate("document.querySelector('#teams').setAttribute('aria-rowcount','16')")
        assert scan()['complete'] is False
        page.evaluate("document.querySelector('#teams').removeAttribute('aria-rowcount')")
        timing=page.evaluate("() => {let times=[];for(let i=0;i<40;i++){let start=performance.now();SurvivorYahooReader.scan(document,players);times.push(performance.now()-start);}return {maxMs:Math.max(...times),averageMs:times.reduce((a,b)=>a+b)/times.length};}")
        # Exercise capture timing and explicit completed-sale evidence, without selectors.
        page.evaluate("window.messages=[];window.captureListener=null;window.chrome={runtime:{sendMessage:async m=>messages.push(m),onMessage:{addListener:f=>captureListener=f}}};document.querySelector('#sold').textContent='';document.querySelector('#last-pick').textContent='Last Pick will appear here'")
        page.add_script_tag(path=str(ROOT/'extensions/yahoo-draft-watcher/capture.js'))
        page.evaluate("captureListener({type:'configure',selectors:{},players,watching:true,captureId:'fixture'})")
        page.wait_for_function("messages.some(m=>m.type==='observation'&&m.events.some(e=>e.type==='bid'&&e.amount===1))")
        page.evaluate("document.querySelector('#price').textContent='$2';document.querySelector('#bidder').textContent='Emre'")
        page.wait_for_function("messages.some(m=>m.type==='observation'&&m.events.some(e=>e.type==='bid'&&e.amount===2&&e.team==='Emre'))")
        page.evaluate("document.querySelector('#sold').textContent='Sold!'")
        page.wait_for_function("messages.some(m=>m.type==='observation'&&m.events.some(e=>e.type==='sale'&&e.amount===2&&e.team==='Emre'))")
        captured=page.evaluate("messages.filter(m=>m.type==='observation').flatMap(m=>m.events)")
        assert len([e for e in captured if e['type']=='sale'])==1,captured
        assert not errors,errors
        (OUT/'reader-report.json').write_text(json.dumps({'fixture':'illustrative screenshot-derived HTML, not actual Yahoo DOM','initialTeams':len(initial['teams']),'rosterSize':13,'captureEvents':captured,'scanTiming':timing,'pageErrors':errors},indent=2),encoding='utf-8')
        browser.close()
    print('Automatic Yahoo reader and capture fixture checks passed')

if __name__=='__main__':main()
