"""Reproduce the owner's Yahoo bid and price-less Last pick screenshots in a local fixture."""
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]


def main():
    fixture = (ROOT / 'tests/fixtures/yahoo-auction.html').read_text(encoding='utf-8')
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path='C:/Program Files/Google/Chrome/Application/chrome.exe', headless=True)
        def setup():
            page = browser.new_page()
            page.set_content(fixture)
            page.add_script_tag(path=str(ROOT / 'extensions/yahoo-draft-watcher/yahoo-reader.js'))
            page.evaluate("""() => {
                window.players=[{player:'Nikola Jokic'},{player:'Victor Wembanyama'}];
                window.scan=()=>SurvivorYahooReader.scan(document,players);
                scan();
                document.querySelector('#player').textContent='N. JOKIC';
                document.querySelector('#price').textContent='$74';
                document.querySelector('#bidder').textContent='Emre';
                document.querySelector('#teams').children[1].insertAdjacentHTML('afterbegin','<small class="badge">$74</small> ');
                scan();
                document.querySelector('#price').textContent='$75';
                document.querySelector('#bidder').textContent='max';
                document.querySelector('#teams').firstElementChild.insertAdjacentHTML('afterbegin','<small class="badge">$75</small> ');
            }""")
            return page

        page = setup()
        own = page.evaluate('scan()')
        assert own['team'] == 'You' and own['amount'] == 75, ('Own bid was not identified', own)
        # Yahoo can advance without a Sold label or a price in the Last pick strip.
        page.evaluate("""() => {
            document.querySelector('#last-pick').innerHTML='<span>Last: </span><b>N. JOKIC (C - DEN)</b><span> max</span>';
            const row=document.querySelector('#teams').firstElementChild;
            row.querySelector('b').textContent='$125';row.lastElementChild.textContent='1/13';
            document.querySelector('#auction footer').innerHTML='<span>Max Offer <b>$114</b> · Budget <b>$125</b></span><time>00:07</time>';
            document.querySelector('#player').textContent='V. WEMBANYAMA';
            document.querySelector('#price').textContent='$72';document.querySelector('#bidder').textContent='Emre';
            document.querySelector('#teams').children[1].querySelector('.badge').textContent='$72';
        }""")
        sale = page.evaluate('scan()')
        expected = {'player':'Nikola Jokic','team':'You','amount':75}
        assert expected in sale['results'], ('Explicit last pick plus wallet/roster confirmation was missed', sale)
        assert sale['player'] == 'Victor Wembanyama' and sale['team'] == 'Emre'
        assert expected in page.evaluate('scan()')['results'], 'Confirmed result must survive another poll'
        page.close()

        # Incomplete or ambiguous evidence must not turn a bid into a purchase.
        for missing in ['last_pick', 'cash', 'roster', 'winning_bid', 'wrong_price', 'wrong_winner', 'ambiguous_badge']:
            page = setup()
            if missing == 'ambiguous_badge':
                page.evaluate("document.querySelector('#teams').children[1].querySelector('.badge').textContent='$75'")
                assert page.evaluate('scan()')['team'] is None
                page.close()
                continue
            if missing != 'winning_bid':
                page.evaluate('scan()')
            page.evaluate("""missing => {
                if(missing!=='last_pick')document.querySelector('#last-pick').innerHTML='<span>Last: </span><b>N. JOKIC (C - DEN)</b><span> max</span>';
                if(missing==='wrong_winner')document.querySelector('#last-pick').lastElementChild.textContent='Emre';
                const row=document.querySelector('#teams').firstElementChild;
                if(missing!=='cash')row.querySelector('b').textContent=missing==='wrong_price'?'$124':'$125';
                if(missing!=='roster')row.lastElementChild.textContent='1/13';
                document.querySelector('#auction footer').innerHTML='<span>Max Offer <b>$114</b> · Budget <b>'+row.querySelector('b').textContent+'</b></span><time>00:07</time>';
                document.querySelector('#player').textContent='V. WEMBANYAMA';
                document.querySelector('#price').textContent='$72';document.querySelector('#bidder').textContent='Emre';
                document.querySelector('#teams').children[1].querySelector('.badge').textContent='$72';
            }""", missing)
            assert not page.evaluate('scan()')['results'], ('Sale inferred from incomplete evidence', missing)
            page.close()
        browser.close()
    print('Yahoo self-bid checks passed: You/max identity, price-less last pick, wallet/slot confirmation, and ambiguous evidence')


if __name__ == '__main__':
    main()
