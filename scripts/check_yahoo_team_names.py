"""Verify live-name cutover against a local copy of the recorded draft, never production writes."""
import json
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

BASE='http://127.0.0.1:8767'
KEY='local-qa-only-recording-key-0123456789'
session=json.loads(Path('artifacts/yahoo-auto-qa/live-name-audit.json').read_text(encoding='utf-8'))['session']
with sync_playwright() as p:
    browser=p.chromium.launch(headless=True,executable_path='C:/Program Files/Google/Chrome/Application/chrome.exe')
    page=browser.new_page();errors=[]
    page.on('pageerror',lambda e:errors.append(str(e)))
    assert page.request.post(BASE+'/__qa/reset').ok
    headers={'Authorization':'Bearer '+KEY}
    baseline={**session,'events':[]}
    response=page.request.post(BASE+'/api/drafts',data={'session':baseline},headers=headers)
    assert response.ok,response.text()
    for offset in range(0,len(session['events']),100):
        response=page.request.post(BASE+'/api/drafts/'+session['id']+'/events',data={'offset':offset,'events':session['events'][offset:offset+100]},headers=headers)
        assert response.ok,response.text()
    room=dict(version=1,mode='live',team='Max',sessions=dict(live=session),pending=[],ignored=[],teamMap={'Ozzy':'Joe','Shut Down':'Joe','3-four-1':'Mark','You':'Max'},playerMap={},stars=[])
    init="""if(!localStorage.getItem('name-test-seeded')){
      localStorage.setItem('name-test-seeded','1');
      localStorage.setItem('survivor.draft.v1',JSON.stringify(ROOM));
      localStorage.setItem('survivor.recording.enabled',ROOM.sessions.live.id);
      sessionStorage.setItem('survivor.recording.key','KEY');
    }""".replace('ROOM',json.dumps(room)).replace('KEY',KEY)
    page.add_init_script(init)
    page.goto(BASE+'/#draft',wait_until='networkidle')
    expect(page.locator('#draft-recording-status')).to_contain_text('Saved '+str(len(session['events'])+1)+' events')
    expect(page.locator('#draft-team')).to_have_value('Cookin in Jokicin')
    expect(page.locator('#draft-wallet')).to_contain_text('$37')
    options=page.locator('#draft-team option').all_text_contents()
    assert 'Ozzy' in options and 'Shut Down' in options and 'Joe' not in options,options
    saved=page.request.get(BASE+'/api/drafts/'+session['id']).json()
    assert saved['session']['events'][:len(session['events'])]==session['events']
    assert saved['session']['teams']==session['teams']
    assert next(t for t in saved['teams'] if t['name']=='Ozzy')['remaining']==90
    assert next(t for t in saved['teams'] if t['name']=='3-four-1')['remaining']==105
    assert next(t for t in saved['teams'] if t['name']=='Shut Down')['remaining']==179
    # Restore a deliberately wrong legacy owner map, then reload and observe another bid.
    page.evaluate("""() => {const r=JSON.parse(localStorage.getItem('survivor.draft.v1'));r.teamMap={Ozzy:'Joe','Shut Down':'Joe'};localStorage.setItem('survivor.draft.v1',JSON.stringify(r));}""")
    page.reload(wait_until='networkidle')
    page.evaluate("""() => {
      const s=JSON.parse(localStorage.getItem('survivor.draft.v1')).sessions.live,st=SurvivorDraft.replay(s),n=st.nomination;
      window.postMessage({channel:'survivor-draft-extension',type:'hello',nonce:'names-qa'},location.origin);
      setTimeout(()=>window.postMessage({channel:'survivor-draft-extension',type:'events',nonce:'names-qa',sessionId:s.id,events:[{id:'names-bid',type:'bid',player:st.byId[n.playerId].player,team:'Ozzy',amount:n.amount+1}]},location.origin),0);
    }""")
    expect(page.locator('#draft-recording-status')).to_contain_text('Saved '+str(len(session['events'])+2)+' events')
    latest=page.request.get(BASE+'/api/drafts/'+session['id']).json()
    assert latest['session']['events'][-1]['team']=='Ozzy'
    assert page.evaluate("JSON.parse(localStorage.getItem('survivor.draft.v1')).pending.length")==0
    expect(page.locator('#draft-wallet')).to_contain_text('$37')
    page.locator('[data-draft="teams"]').click()
    expect(page.locator('#draft-dialog-body')).to_contain_text('match these Yahoo team names directly')
    assert page.locator('#draft-dialog-body select').count()==0
    assert not errors,errors
    browser.close()
print('Live Yahoo names: full saved history retained; wrong owner maps ignored before/after reload; Max roster and cash preserved; recording resumed.')
