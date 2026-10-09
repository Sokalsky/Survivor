"""Check completed purchase cards independently of the live auction controls."""
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, executable_path='C:/Program Files/Google/Chrome/Application/chrome.exe')
    page = browser.new_page()
    page.set_content((ROOT/'tests/fixtures/yahoo-auction.html').read_text(encoding='utf-8'))
    page.evaluate("""() => {
      document.querySelector('#teams').innerHTML=['You','Ozzy','Swag Management','The KAT in the Hat','JYK','Fantasy Sports Czar','Pretty Savage','Morant and Gang'].map(name=>'<div><span>'+name+'</span><b>$109</b><span>2/15</span></div>').join('');
      document.querySelector('#auction footer').innerHTML='<span>Max Offer <b>$97</b> Budget <b>$109</b></span><time>00:12</time>';
      document.querySelector('#auction button:last-child').textContent='Nominate $';
      document.querySelector('#player').textContent='SHUT DOWN IS NOMINATING A PLAYER';
      document.querySelector('#price').textContent='$–';
      document.querySelector('#last-pick').textContent='Last: D. LILLARD Fantasy Sports Czar';
      const feed=document.createElement('aside');feed.id='updates';
      feed.innerHTML='<nav><button>All</button><button>Chat</button><button>Updates</button></nav>'+
        [['Ozzy','Giannis Antetokounmpo',44],['Swag Management','Stephen Curry',41],['The KAT in the Hat','Anthony Edwards',58],['JYK','Karl-Anthony Towns',57],['Fantasy Sports Czar','Donovan Mitchell',57],['Pretty Savage','Kevin Durant',51],['Morant and Gang','Trae Young',40]].map(([t,p,a])=>'<article><small>'+t+'</small><div><b>'+p+'</b><strong>$'+a+'</strong></div><span>PF,C - MIA</span></article>').join('')+
        '<div class="chat"><small>JYK</small><p>I would pay $60 for James Harden</p></div>';
      document.querySelector('main').append(feed);
      window.players=['Giannis Antetokounmpo','Stephen Curry','Anthony Edwards','Karl-Anthony Towns','Donovan Mitchell','Kevin Durant','Trae Young','James Harden','Damian Lillard'].map(player=>({player}));
    }""")
    page.add_script_tag(path=str(ROOT/'extensions/yahoo-draft-watcher/yahoo-reader.js'))
    state = page.evaluate('SurvivorYahooReader.scan(document,players)')
    assert state['detected'] and state['player'] is None, state
    assert len(state['results']) == 7, state
    assert {(r['player'],r['team'],r['amount']) for r in state['results']} == {
        ('Giannis Antetokounmpo','Ozzy',44),('Stephen Curry','Swag Management',41),
        ('Anthony Edwards','The KAT in the Hat',58),('Karl-Anthony Towns','JYK',57),
        ('Donovan Mitchell','Fantasy Sports Czar',57),('Kevin Durant','Pretty Savage',51),
        ('Trae Young','Morant and Gang',40)}
    assert all(r['recovered'] for r in state['results'])
    # A later scan retains observed results even if Yahoo hides the Updates panel.
    page.evaluate("document.querySelector('#updates').hidden=true")
    assert len(page.evaluate('SurvivorYahooReader.scan(document,players)')['results']) == 7
    # Actual capture forwards each result once even after multiple polls.
    page.evaluate("""() => {
      window.messages=[];window.chrome={runtime:{sendMessage:async m=>messages.push(m),onMessage:{addListener:f=>window.configure=f}}};
    }""")
    page.add_script_tag(path=str(ROOT/'extensions/yahoo-draft-watcher/capture.js'))
    page.evaluate("configure({type:'configure',captureId:'updates-test',players,selectors:{},watching:true})")
    page.wait_for_function("messages.filter(m=>m.type==='observation').flatMap(m=>m.events).filter(e=>e.type==='sale').length===7")
    page.wait_for_timeout(550)
    sales=page.evaluate("messages.filter(m=>m.type==='observation').flatMap(m=>m.events).filter(e=>e.type==='sale')")
    assert len(sales)==7 and all(s['recovered'] for s in sales)
    browser.close()
print('Yahoo Updates: seven explicit purchases recovered during nomination, chat rejected, results retained and sent once.')
