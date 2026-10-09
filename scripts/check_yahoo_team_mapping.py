"""Recover an aliased live queue against the isolated in-memory recording preview."""
from playwright.sync_api import sync_playwright, expect

BASE = 'http://127.0.0.1:8767'

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, executable_path='C:/Program Files/Google/Chrome/Application/chrome.exe')
    page = browser.new_page()
    errors = []
    page.on('pageerror', lambda e: errors.append(str(e)))
    assert page.request.post(BASE+'/__qa/reset').json() == {'isolated': True}
    page.goto(BASE+'/#draft', wait_until='networkidle')
    page.locator('[data-draft="recording"]').click()
    page.locator('#draft-recording-form input').fill('local-qa-only-recording-key-0123456789')
    page.locator('#draft-recording-form button').click()
    expect(page.locator('#draft-recording-status')).to_contain_text('Saved 0 events')
    initial = page.evaluate("JSON.parse(localStorage.getItem('survivor.draft.v1')).sessions.live")

    def send(events):
        page.evaluate("""events => {
          const s=JSON.parse(localStorage.getItem('survivor.draft.v1')).sessions.live;
          window.postMessage({channel:'survivor-draft-extension',type:'hello',nonce:'mapping-qa'},location.origin);
          setTimeout(()=>window.postMessage({channel:'survivor-draft-extension',type:'events',nonce:'mapping-qa',sessionId:s.id,events},location.origin),0);
        }""", events)

    send([
        dict(id='n1', type='nominate', player='Giannis Antetokounmpo'),
        dict(id='b1', type='bid', player='Giannis Antetokounmpo', team='JYK', amount=23),
        dict(id='b2', type='bid', player='Giannis Antetokounmpo', team='You', amount=24),
        dict(id='s1', type='sale', player='Giannis Antetokounmpo', team='JYK', amount=25),
        dict(id='n2', type='nominate', player='Anthony Edwards'),
        dict(id='b3', type='bid', player='Anthony Edwards', team='Brunson Burners', amount=1),
    ])
    page.wait_for_function("JSON.parse(localStorage.getItem('survivor.draft.v1')).pending.length===5")
    page.locator('[data-draft="review"]').click()
    for alias, owner in [('JYK','Jason'), ('You','Max'), ('Brunson Burners','Isaac')]:
        page.locator('[data-yahoo-team="'+alias+'"]').select_option(owner)
    page.locator('#draft-teams-form button').click()
    expect(page.locator('#draft-recording-status')).to_contain_text('Saved 6 events')
    state = page.evaluate("JSON.parse(localStorage.getItem('survivor.draft.v1'))")
    assert state['pending'] == []
    assert state['sessions']['live']['id'] == initial['id']
    for key in ('players','teams','keepers','settings'):
        assert state['sessions']['live'][key] == initial[key], key
    saved = page.request.get(BASE+'/api/drafts/'+initial['id']).json()
    assert saved['session']['events'][3]['team'] == 'Jason'
    assert saved['session']['events'][3]['sourceTeam'] == 'JYK'
    assert next(t for t in saved['teams'] if t['name']=='Jason')['remaining'] == 107
    page.reload(wait_until='networkidle')
    send([dict(id='b4', type='bid', player='Anthony Edwards', team='jyk', amount=2)])
    expect(page.locator('#draft-recording-status')).to_contain_text('Saved 7 events')
    assert page.evaluate("JSON.parse(localStorage.getItem('survivor.draft.v1')).pending.length") == 0
    assert not errors, errors
    browser.close()
print('Yahoo team mapping: queued bids/sale/next nomination recovered, recorded, baseline preserved, aliases survive reload.')
