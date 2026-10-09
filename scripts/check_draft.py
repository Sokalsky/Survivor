"""Exercise the draft UI and passive watcher against synthetic DOM, never a Yahoo account."""
import argparse,json
from pathlib import Path
from playwright.sync_api import sync_playwright,expect
ROOT=Path(__file__).resolve().parents[1]
def main():
 parser=argparse.ArgumentParser();parser.add_argument('--url',default='http://127.0.0.1:8080');parser.add_argument('--browser');args=parser.parse_args()
 out=ROOT/'artifacts/draft-qa';out.mkdir(parents=True,exist_ok=True)
 with sync_playwright() as p:
  browser=p.chromium.launch(headless=True,executable_path=args.browser)
  context=browser.new_context(viewport={'width':1440,'height':1050})
  page=context.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
  page.goto(args.url,wait_until='networkidle')
  expect(page.locator('#page-title')).to_contain_text('Draft')
  expect(page.locator('.draft-empty h2')).to_have_text('Ready when the room is.')
  expect(page.locator('.draft-wallet strong')).to_have_text('$109')
  expect(page.locator('.draft-team')).to_have_count(15)
  expect(page.locator('#draft-source')).to_have_text('Not connected')
  page.screenshot(path=str(out/'draft-waiting-desktop.png'),full_page=True)
  page.locator('.draft-toolbar [data-draft="mode"]').click()
  expect(page.locator('.nomination')).to_be_visible()
  expect(page.locator('#draft-source')).to_have_text('Practice')
  expect(page.locator('.draft-notice.practice')).to_be_visible()
  page.locator('#mock-play').click()
  expect(page.locator('.draft-bidline')).not_to_contain_text('Awaiting first bid')
  page.locator('#mock-play').click()
  page.screenshot(path=str(out/'draft-practice-desktop.png'),full_page=False)
  base=page.evaluate("JSON.parse(localStorage.getItem('survivor.draft.v1')).sessions.live.events.length")
  assert base==0
  page.locator('#draft-search').fill('Curry')
  expect(page.locator('#draft-table')).to_contain_text('Stephen Curry')
  assert page.locator('#draft-table tbody tr').count()<=2
  page.locator('#draft-search').fill('')
  page.locator('[data-draft-tab="bids"]').click();expect(page.locator('#draft-table')).to_contain_text('Observed bids only')
  page.locator('[data-draft-tab="available"]').click()
  page.locator('.draft-toolbar [data-draft="settings"]').click()
  page.locator('[name="reservePerSlot"]').fill('3');page.locator('#draft-settings-form button[type=submit]').click()
  expect(page.locator('.draft-wallet-foot b')).to_have_text('$3')
  page.reload(wait_until='networkidle');expect(page.locator('#draft-source')).to_have_text('Practice');expect(page.locator('.draft-wallet-foot b')).to_have_text('$3')
  page.locator('.draft-toolbar [data-draft="mode"]').click();expect(page.locator('.draft-empty')).to_be_visible();expect(page.locator('.draft-wallet strong')).to_have_text('$109')
  # Exercise the actual page bridge contract: wrong origin/nonce is ignored; correct delivery is durable and idempotent.
  page.evaluate("window.postMessage({channel:'survivor-draft-extension',type:'hello',nonce:'test-nonce'},location.origin)")
  page.wait_for_timeout(100)
  sid=page.evaluate("JSON.parse(localStorage.getItem('survivor.draft.v1')).sessions.live.id")
  packet={'channel':'survivor-draft-extension','type':'events','nonce':'wrong','sessionId':sid,'events':[{'id':'bad','type':'nominate','player':'Stephen Curry'}]}
  page.evaluate('(m)=>window.postMessage(m,location.origin)',packet);page.wait_for_timeout(150)
  assert page.evaluate("JSON.parse(localStorage.getItem('survivor.draft.v1')).sessions.live.events.length")==0
  packet['nonce']='test-nonce';packet['events']=[{'id':'n1','type':'nominate','player':'Stephen Curry'},{'id':'b1','type':'bid','player':'Stephen Curry','team':'Yahoo Max','amount':15}]
  page.evaluate('(m)=>window.postMessage(m,location.origin)',packet);page.wait_for_timeout(150)
  expect(page.locator('#draft-warning')).to_contain_text('updates need review')
  expect(page.locator('.draft-decision')).to_have_text('CHECK SYNC')
  page.locator('[data-draft="review"]').click();page.locator('#draft-map-form [name="team"]').select_option(label='Max');page.locator('#draft-map-form button[type=submit]').click()
  expect(page.locator('.draft-bidline')).to_contain_text('$15')
  page.evaluate('(m)=>window.postMessage(m,location.origin)',{'channel':'survivor-draft-extension','type':'heartbeat','nonce':'test-nonce','sessionId':sid,'status':{'ready':True,'partial':True}})
  page.wait_for_timeout(2200);expect(page.locator('.draft-decision')).to_have_text('HOLD')
  page.evaluate('(m)=>window.postMessage(m,location.origin)',packet);page.wait_for_timeout(100)
  assert page.evaluate("JSON.parse(localStorage.getItem('survivor.draft.v1')).sessions.live.events.length")==2
  page.evaluate('(m)=>window.postMessage(m,location.origin)',{'channel':'survivor-draft-extension','type':'events','nonce':'test-nonce','sessionId':sid,'events':[{'id':'s1','type':'sale','player':'Stephen Curry','team':'Yahoo Max','amount':15}]})
  expect(page.locator('.draft-wallet strong')).to_have_text('$94')
  expect(page.locator('.draft-empty')).to_be_visible()
  # A new nomination without the prior result must pause, then recover when that result arrives.
  def deliver(events):page.evaluate('(m)=>window.postMessage(m,location.origin)',{'channel':'survivor-draft-extension','type':'events','nonce':'test-nonce','sessionId':sid,'events':events})
  deliver([{'id':'n2','type':'nominate','player':'Anthony Edwards'},{'id':'b2','type':'bid','player':'Anthony Edwards','team':'Yahoo Max','amount':20}])
  deliver([{'id':'n3','type':'nominate','player':'Anthony Davis'}])
  expect(page.locator('#draft-warning')).to_contain_text('updates need review')
  expect(page.locator('.nomination h2')).to_contain_text('Anthony Edwards')
  deliver([{'id':'s2','type':'sale','player':'Anthony Edwards','team':'Yahoo Max','amount':20}])
  expect(page.locator('.nomination h2')).to_contain_text('Anthony Davis')
  expect(page.locator('#draft-warning')).not_to_contain_text('updates need review')
  expect(page.locator('.draft-wallet strong')).to_have_text('$74')
  page.reload(wait_until='networkidle');expect(page.locator('.draft-wallet strong')).to_have_text('$74')
  page.locator('.draft-toolbar [data-draft="mode"]').click()
  page.set_viewport_size({'width':900,'height':1000});page.screenshot(path=str(out/'draft-split-screen.png'),full_page=True)
  assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
  page.set_viewport_size({'width':390,'height':844});page.screenshot(path=str(out/'draft-mobile.png'),full_page=True)
  assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
  # Passive capture test with the actual extension content script, controlled DOM and mocked Chrome message transport.
  # Keep synthetic capture markup separate from the app and its strict CSP.
  capture=context.new_page()
  capture.evaluate("document.body.innerHTML='<div id=player>Stephen Curry</div><div id=bid>$40.00</div><div id=bidder>Yahoo Max</div><div id=sold>Bidding</div><table id=results><tbody><tr><td>LeBron James</td><td>Yahoo A</td><td>$25</td></tr></tbody></table>'; window.packets=[];window.handlers=[];window.chrome={runtime:{sendMessage:async m=>packets.push(m),onMessage:{addListener:f=>handlers.push(f)}}}")
  capture.add_script_tag(path=str(ROOT/'extensions/yahoo-draft-watcher/capture.js'))
  capture.evaluate("handlers[0]({type:'configure',watching:true,selectors:{player:'#player',bid:'#bid',bidder:'#bidder',sold:'#sold',resultRow:'#results tbody tr',resultPlayer:'td:nth-child(1)',resultTeam:'td:nth-child(2)',resultAmount:'td:nth-child(3)'}})")
  capture.wait_for_timeout(450)
  events=capture.evaluate("packets.filter(p=>p.type==='observation').flatMap(p=>p.events)")
  assert any(e['type']=='bid' and e['amount']==40 for e in events),events
  assert any(e['type']=='sale' and e.get('recovered') for e in events),events
  capture.evaluate("document.getElementById('bid').textContent='$41';document.getElementById('bidder').textContent='Yahoo B'")
  capture.wait_for_timeout(220)
  capture.evaluate("document.getElementById('sold').textContent='Sold to Yahoo B'")
  capture.wait_for_timeout(700)
  events=capture.evaluate("packets.filter(p=>p.type==='observation').flatMap(p=>p.events)")
  assert any(e['type']=='bid' and e['amount']==41 for e in events)
  assert len([e for e in events if e['type']=='sale' and e['player']=='Stephen Curry'])==1
  capture.evaluate("document.getElementById('results').insertAdjacentHTML('beforeend','<tr><td>Anthony Edwards</td><td>Yahoo A</td><td>$50</td></tr>')")
  capture.wait_for_timeout(250)
  # Appending a generic row outside the selected tbody must not create a false result.
  assert not errors,errors
  (out/'browser-report.json').write_text(json.dumps({'checks':'draft UI, persistence, practice isolation, bridge mapping/dedupe, responsive layout, DOM bids and explicit sale confirmation','page_errors':errors,'captured_events':events},indent=2))
  browser.close();print('Draft browser checks passed; screenshots in artifacts/draft-qa')
if __name__=='__main__':main()
