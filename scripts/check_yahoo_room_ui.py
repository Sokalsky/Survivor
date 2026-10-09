"""Check automatic mock import against the isolated local preview only."""
import json
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

ROOT=Path(__file__).resolve().parents[1]
URL='http://127.0.0.1:8767'
OUT=ROOT/'artifacts/yahoo-auto-qa'

def main():
    OUT.mkdir(parents=True,exist_ok=True)
    with sync_playwright() as pw:
        browser=pw.chromium.launch(executable_path='C:/Program Files/Google/Chrome/Application/chrome.exe',headless=True)
        context=browser.new_context(viewport={'width':1100,'height':900})
        assert context.request.post(URL+'/__qa/reset').json().get('isolated')
        page=context.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
        page.goto(URL+'/#draft');expect(page.locator('#draft-team')).to_be_visible()
        original=page.evaluate("localStorage.getItem('survivor.draft.v1')")
        page.goto(URL+'/?yahooMock=1#draft');expect(page.locator('#draft-team')).to_be_visible()
        metadata={'key':'https://basketball.fantasysports.yahoo.com/draft?mockId=42','purpose':'mock','complete':True,'rosterSize':13,'ownTeam':'You','teams':[{'name':n,'budget':200,'cash':200,'owned':0} for n in ['You','Emre','Yiğit Uğur','Simon']]}
        def connect(room):
            page.evaluate("window.postMessage({channel:'survivor-draft-extension',type:'hello',nonce:'fixture'},location.origin)")
            page.evaluate("r=>{const s=JSON.parse(localStorage.getItem('survivor.yahoo-mock.v1')).sessions.live;window.postMessage({channel:'survivor-draft-extension',type:'room',nonce:'fixture',sessionId:s.id,room:r},location.origin)}",room)
            expect(page.locator('#draft-team')).to_have_value('You')
        connect(metadata)
        expect(page.locator('#draft-wallet')).to_contain_text('13 slots left')
        expect(page.locator('#draft-wallet')).to_contain_text('Legal max $188')
        assert page.locator('#draft-team option').count()==4
        state=page.evaluate("JSON.parse(localStorage.getItem('survivor.yahoo-mock.v1'))")
        p=next(p for p in state['sessions']['live']['players'] if p['player']=='Nikola Jokic')
        events=[{'id':'nom','type':'nominate','player':p['player']},{'id':'bid','type':'bid','player':p['player'],'team':'Emre','amount':45},{'id':'sale','type':'sale','player':p['player'],'team':'Emre','amount':45}]
        page.evaluate("events=>{const s=JSON.parse(localStorage.getItem('survivor.yahoo-mock.v1')).sessions.live;window.postMessage({channel:'survivor-draft-extension',type:'events',nonce:'fixture',sessionId:s.id,events},location.origin)}",events)
        expect(page.locator('#draft-controls')).to_contain_text('1 sales')
        expect(page.locator('#draft-teams')).to_contain_text('$155')
        page.reload();expect(page.locator('#draft-team')).to_be_visible();connect(metadata)
        expect(page.locator('#draft-controls')).to_contain_text('1 sales')
        old_id=state['sessions']['live']['id']
        connect({**metadata,'key':metadata['key']+'9'})
        expect(page.locator('#draft-controls')).to_contain_text('0 sales')
        assert page.evaluate("id=>JSON.parse(localStorage.getItem('survivor.yahoo-mock.backup.'+id)).sessions.live.events.length",old_id)==3
        assert page.evaluate("localStorage.getItem('survivor.draft.v1')")==original
        assert context.request.get(URL+'/api/drafts').json()['drafts']==[]
        page.screenshot(path=str(OUT/'automatic-mock-desktop.png'),full_page=True)
        page.set_viewport_size({'width':390,'height':844});assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        page.screenshot(path=str(OUT/'automatic-mock-mobile.png'),full_page=True)
        assert not errors,errors
        (OUT/'ui-report.json').write_text(json.dumps({'automaticImportedTeams':4,'rosterSize':13,'cashAfterSale':155,'reloadPreservedEvents':3,'previousMockBackedUp':True,'realWorkspaceUnchanged':True,'realRecordedDrafts':0,'pageErrors':errors},indent=2),encoding='utf-8')
        browser.close()
    print('Automatic mock room UI checks passed')

if __name__=='__main__':main()
