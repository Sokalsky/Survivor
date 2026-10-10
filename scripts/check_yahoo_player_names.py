"""Exercise the Jabari Smith Jr. nomination and bid with the competing Jalen Smith alias."""
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[1]
with sync_playwright() as p:
    browser=p.chromium.launch(headless=True,executable_path='C:/Program Files/Google/Chrome/Application/chrome.exe')
    page=browser.new_page()
    page.set_content((ROOT/'tests/fixtures/yahoo-auction.html').read_text(encoding='utf-8'))
    page.add_script_tag(path=str(ROOT/'extensions/yahoo-draft-watcher/yahoo-reader.js'))
    page.evaluate("""() => {
      window.players=['Jabari Smith Jr.','Jalen Smith','Jalen Williams','Jaylin Williams'].map(player=>({player}));
      document.querySelector('#player').textContent='J. SMITH JR.';
      document.querySelector('#price').textContent='$10';
      document.querySelector('#bidder').textContent='Emre';
      window.messages=[];window.captureListener=null;
      if(!crypto.randomUUID)crypto.randomUUID=()=> 'suffix-nomination'; // about:blank fixture is not a secure origin
      window.chrome={runtime:{sendMessage:async m=>messages.push(m),onMessage:{addListener:f=>captureListener=f}}};
    }""")
    observed=page.evaluate('SurvivorYahooReader.scan(document,players)')
    assert observed['player']=='Jabari Smith Jr.' and observed['amount']==10 and observed['team']=='Emre',observed
    page.add_script_tag(path=str(ROOT/'extensions/yahoo-draft-watcher/capture.js'))
    page.evaluate("captureListener({type:'configure',selectors:{},players,watching:true,captureId:'suffix-check'})")
    page.wait_for_function("messages.some(m=>m.type==='observation'&&m.events.some(e=>e.type==='bid'&&e.player==='Jabari Smith Jr.'&&e.amount===10))")
    events=page.evaluate("messages.filter(m=>m.type==='observation').flatMap(m=>m.events)")
    assert any(e['type']=='nominate' and e['player']=='Jabari Smith Jr.' for e in events),events
    assert not any(e['player']=='Jalen Smith' for e in events),events
    browser.close()
print('Jabari Smith Jr.: live nomination and $10 bid captured despite Jalen Smith sharing the shorter alias.')
