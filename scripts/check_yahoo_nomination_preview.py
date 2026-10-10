"""A selected-but-unsubmitted player must never become a live auction."""
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[1]
with sync_playwright() as p:
    browser=p.chromium.launch(headless=True,executable_path='C:/Program Files/Google/Chrome/Application/chrome.exe')
    page=browser.new_page()
    page.set_content((ROOT/'tests/fixtures/yahoo-auction.html').read_text(encoding='utf-8'))
    page.add_script_tag(path=str(ROOT/'extensions/yahoo-draft-watcher/yahoo-reader.js'))
    page.evaluate("""() => {
      window.players=['CJ McCollum','Dillon Brooks','Brandon Ingram'].map(player=>({player}));
      document.querySelector('#player').textContent='C. MCCOLLUM';
      document.querySelector('#price').textContent='$1';
      document.querySelector('#bidder').textContent='You';
      window.offer=[...document.querySelectorAll('button')].find(b=>b.textContent.startsWith('Offer'));
      offer.textContent='Nominate $1';
      window.messages=[];window.captureListener=null;
      if(!crypto.randomUUID)crypto.randomUUID=()=>Math.random().toString();
      window.chrome={runtime:{sendMessage:async m=>messages.push(m),onMessage:{addListener:f=>captureListener=f}}};
    }""")
    assert page.evaluate('SurvivorYahooReader.scan(document,players).auctionActive') is False
    page.add_script_tag(path=str(ROOT/'extensions/yahoo-draft-watcher/capture.js'))
    page.evaluate("captureListener({type:'configure',selectors:{},players,watching:true,captureId:'preview-test'})")
    page.wait_for_timeout(650)
    assert page.evaluate("messages.filter(m=>m.type==='observation').length")==0
    page.evaluate("""() => {
      document.querySelector('#player').textContent='D. BROOKS';
      document.querySelector('#price').textContent='$9';
      document.querySelector('#bidder').textContent='Emre';
      offer.textContent='Offer $10';
    }""")
    page.wait_for_function("messages.some(m=>m.type==='observation'&&m.events.some(e=>e.type==='bid'&&e.player==='Dillon Brooks'&&e.amount===9))")
    events=page.evaluate("messages.filter(m=>m.type==='observation').flatMap(m=>m.events)")
    assert any(e['type']=='nominate' and e['player']=='Dillon Brooks' for e in events),events
    assert not any(e.get('player')=='CJ McCollum' for e in events),events
    browser.close()
print('Unsubmitted McCollum preview ignored; subsequent Dillon Brooks nomination and bid captured.')
