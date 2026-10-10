"""Recover an older watcher's private preview using a local copy of the saved draft."""
import json
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright, expect
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

BASE='http://127.0.0.1:8767'
s=json.loads(Path('artifacts/yahoo-auto-qa/live-stall-audit.json').read_text())['session']
room=dict(version=1,mode='live',team='Cookin in Jokicin',sessions=dict(live=s),pending=[dict(id='recovery-n',type='nominate',player='Dillon Brooks'),dict(id='recovery-b',type='bid',player='Dillon Brooks',team='Ozzy',amount=9)],ignored=[],teamMap={},playerMap={},stars=[])
with sync_playwright() as p:
    browser=p.chromium.launch(headless=True,executable_path='C:/Program Files/Google/Chrome/Application/chrome.exe')
    page=browser.new_page();errors=[]
    page.on('pageerror',lambda e:errors.append(str(e)))
    page.add_init_script("if(!localStorage.getItem('survivor.draft.v1'))localStorage.setItem('survivor.draft.v1',JSON.stringify("+json.dumps(room)+"))")
    page.goto(BASE+'/#draft',wait_until='networkidle')
    expect(page.locator('#draft-nomination')).to_contain_text('Dillon Brooks')
    expect(page.locator('.draft-bidline')).to_contain_text('$9')
    state=page.evaluate("JSON.parse(localStorage.getItem('survivor.draft.v1'))")
    assert state['pending']==[]
    events=state['sessions']['live']['events']
    assert events[:len(s['events'])]==s['events']
    assert [(e['type'],e.get('playerId')) for e in events[len(s['events']):]]==[('withdraw','cjmccollum'),('nominate','dillonbrooks'),('bid','dillonbrooks')]
    from survivor.draft_records import replay
    before=replay(s,s['events']);after=replay(s,events)
    assert len(after['sales'])==len(before['sales'])
    assert after['teams']['Cookin in Jokicin']['remaining']==before['teams']['Cookin in Jokicin']['remaining']
    assert not errors,errors
    browser.close()
print('Stuck McCollum preview recovered; queued Dillon Brooks auction resumed; every prior event, purchase and budget preserved.')
