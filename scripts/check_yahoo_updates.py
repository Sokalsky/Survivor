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
    # An incorrect shared app-owner mapping cannot corrupt Yahoo's actual name.
    aliases=[{'name':'Ozzy','team':'Joe'},{'name':'JYK','team':'Joe'}]
    repeated=page.evaluate('aliases=>SurvivorYahooReader.scan(document,players,aliases)',aliases)
    assert {(r['player'],r['team'],r['amount']) for r in repeated['results']} == {(r['player'],r['team'],r['amount']) for r in state['results']}
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
    page.close()
    for missing in [None, 'bid', 'cash', 'slot', 'wrong_winner']:
        page=browser.new_page()
        page.set_content((ROOT/'tests/fixtures/yahoo-auction.html').read_text(encoding='utf-8'))
        page.add_script_tag(path=str(ROOT/'extensions/yahoo-draft-watcher/yahoo-reader.js'))
        page.evaluate("""() => {
          window.players=[{player:'Amen Thompson'},{player:'Evan Mobley'}];
          window.scan=()=>SurvivorYahooReader.scan(document,players);
          document.querySelector('#teams').innerHTML='<div><span>You</span><b>$109</b><span>2/15</span></div><div><span>JYK</span><b>$73</b><span>3/15</span></div>';
          document.querySelector('#auction footer').innerHTML='<span>Max Offer <b>$97</b> Budget <b>$109</b></span><time>00:10</time>';
          scan();
          document.querySelector('#player').textContent='A. THOMPSON';
          document.querySelector('#price').textContent='$36';
          document.querySelector('#bidder').textContent='max';
          document.querySelector('#teams').firstElementChild.insertAdjacentHTML('afterbegin','<small>$36</small>');
        }""")
        if missing != 'bid':
            assert page.evaluate('scan()')['team']=='You'
        page.evaluate("""missing => {
          const row=document.querySelector('#teams').firstElementChild;
          if(missing!=='cash')row.querySelector('b').textContent='$73';
          if(missing!=='slot')row.lastElementChild.textContent='3/15';
          document.querySelector('#auction footer').innerHTML='<span>Max Offer <b>$62</b> Budget <b>'+row.querySelector('b').textContent+'</b></span><time>00:08</time>';
          document.querySelector('#player').textContent='E. MOBLEY';document.querySelector('#price').textContent='$28';document.querySelector('#bidder').textContent='JYK';
          document.querySelector('main').insertAdjacentHTML('beforeend','<aside><nav><button>Updates</button></nav><article><small>'+(missing==='wrong_winner'?'JYK':'Cookin n Jokic')+'</small><b>Amen Thompson</b><strong>$36</strong><span>PG - HOU</span></article></aside>');
        }""", missing)
        results=page.evaluate('scan()')['results']
        mine=[r for r in results if r['team']=='You']
        assert bool(mine)==(missing is None),(missing,results)
        if mine:
            assert mine[0]['player']=='Amen Thompson' and mine[0]['amount']==36
        if missing=='bid':
            # Explicit owner aliases recover an older purchase without a bid
            # observed by this capture session. No hidden bid is invented.
            aliases=[{'name':'You','team':'Max'},{'name':'Cookin n Jokic','team':'Max'}]
            mapped=page.evaluate('aliases=>SurvivorYahooReader.scan(document,players,aliases)',aliases)
            assert mapped['results']==[{'player':'Amen Thompson','team':'You','amount':36,'recovered':True}],mapped
        page.close()
    browser.close()
print('Yahoo Updates: seven purchases recovered; chat rejected; own-name mismatch confirmed by bid, cash and roster, with incomplete evidence rejected.')
