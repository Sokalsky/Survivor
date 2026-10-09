"""Exercise human bidding, the ten-second clock and pause/reload against a local preview."""
import argparse, json
from pathlib import Path
from playwright.sync_api import sync_playwright, expect
ROOT = Path(__file__).resolve().parents[1]

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', default='http://127.0.0.1:8766')
    parser.add_argument('--browser')
    args = parser.parse_args()
    out = ROOT / 'artifacts' / 'mock-draft-qa'
    out.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, executable_path=args.browser)
        context = browser.new_context(viewport={'width':1440,'height':1050})
        page = context.new_page()
        errors = []
        page.on('pageerror', lambda e: errors.append(str(e)))
        page.goto(args.url, wait_until='networkidle')
        expect(page.locator('.draft-empty')).to_be_visible()
        live_before = page.evaluate("JSON.stringify(JSON.parse(localStorage.getItem('survivor.draft.v1')).sessions.live)")
        page.locator('.draft-toolbar [data-draft="mode"]').click()
        expect(page.locator('#mock-clock')).to_have_text('10s')
        expect(page.locator('#mock-play')).to_have_text('Start mock draft')
        expect(page.locator('#mock-bid')).to_be_disabled()
        first = page.locator('.nomination h2').inner_text()
        assert page.evaluate("JSON.parse(localStorage.getItem('survivor.draft.v1')).sessions.practice.events.filter(e=>e.type==='sale').length") == 0
        page.locator('#mock-play').click()
        expect(page.locator('.draft-bidline')).not_to_contain_text('Awaiting first bid')
        assert page.evaluate("JSON.parse(localStorage.getItem('survivor.draft.v1')).sessions.practice.events.filter(e=>e.type==='bid').every(e=>e.team!=='Max')")
        page.locator('#mock-pass').click()
        expect(page.locator('#mock-bid')).to_be_disabled()
        page.locator('#mock-pass').click()
        page.locator('#mock-bid').click()
        expect(page.locator('.draft-bidline')).to_contain_text('Max')
        page.locator('#mock-bid-amount').fill('97')
        page.locator('#draft-mock-bid-form button').click()
        expect(page.locator('.draft-bidline')).to_contain_text('$97')
        expect(page.locator('.draft-bidline')).to_contain_text('Max')
        expect(page.locator('#mock-pass')).to_be_disabled()
        page.locator('#mock-play').click()
        frozen = page.locator('#mock-clock').inner_text()
        page.wait_for_timeout(1300)
        expect(page.locator('#mock-clock')).to_have_text(frozen)
        page.reload(wait_until='networkidle')
        expect(page.locator('#mock-play')).to_have_text('Resume mock')
        expect(page.locator('#mock-clock')).to_have_text(frozen)
        page.locator('#mock-play').click()
        expect(page.locator('.mock-result')).to_contain_text('You won', timeout=12000)
        expect(page.locator('.draft-wallet strong')).to_have_text('$12')
        page.screenshot(path=str(out/'mock-sale.png'), full_page=False)
        expect(page.locator('.nomination h2')).to_be_visible(timeout=5000)
        assert page.locator('.nomination h2').inner_text() != first
        page.locator('#mock-play').click()
        expect(page.locator('#mock-play')).to_have_text('Resume mock')
        page.screenshot(path=str(out/'mock-desktop.png'), full_page=True)
        page.set_viewport_size({'width':900,'height':1000})
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        page.screenshot(path=str(out/'mock-split.png'), full_page=True)
        page.set_viewport_size({'width':390,'height':844})
        expect(page.locator('#mock-clock')).to_be_visible()
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        page.screenshot(path=str(out/'mock-mobile.png'), full_page=True)
        assert page.evaluate("JSON.stringify(JSON.parse(localStorage.getItem('survivor.draft.v1')).sessions.live)") == live_before
        page.set_viewport_size({'width':1440,'height':1050})
        page.locator('#mock-play').click()
        page.locator('[data-draft="settings"]').click()
        expect(page.locator('#mock-play')).to_have_text('Resume mock')
        page.locator('[data-draft="close-dialog"]').click()
        page.locator('#mock-play').click()
        page.locator('.draft-toolbar [data-draft="mode"]').click()
        expect(page.locator('.draft-empty h2')).to_have_text('Ready when the room is.')
        page.wait_for_timeout(1000)
        page.locator('.draft-toolbar [data-draft="mode"]').click()
        expect(page.locator('#mock-play')).to_have_text('Resume mock')
        page.locator('[data-draft="reset-practice"]').click()
        expect(page.locator('#mock-clock')).to_have_text('10s')
        expect(page.locator('.draft-wallet strong')).to_have_text('$109')
        assert page.locator('.nomination h2').inner_text() == first
        assert page.evaluate("JSON.stringify(JSON.parse(localStorage.getItem('survivor.draft.v1')).sessions.live)") == live_before
        assert not errors, errors
        (out/'browser-report.json').write_text(json.dumps({'checks':'mock human bid, opponent bids, deadline sale, next nomination, pause, reload, settings, mode switching, restart and responsive layout','page_errors':errors}, indent=2))
        browser.close()
    print('Mock draft browser checks passed')

if __name__ == '__main__':
    main()
