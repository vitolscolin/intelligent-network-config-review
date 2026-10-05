"""Optional Playwright browser smoke test; run against a fresh dashboard data dir."""
import argparse
import json
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

parser = argparse.ArgumentParser()
parser.add_argument('--url', default='http://127.0.0.1:8765')
parser.add_argument('--screenshots', type=Path, default=Path('runs/browser-check'))
parser.add_argument('--chrome', help='Optional Chrome/Chromium executable; defaults to Playwright Chromium')
args = parser.parse_args()
args.screenshots.mkdir(parents=True, exist_ok=True)
with sync_playwright() as p:
    launch_options = {'headless': True}
    if args.chrome:
        launch_options['executable_path'] = args.chrome
    browser = p.chromium.launch(**launch_options)
    page = browser.new_page(viewport={'width':1440, 'height':1050}, reduced_motion='reduce')
    errors = []
    page.on('pageerror', lambda e: errors.append(str(e)))
    page.goto(args.url)
    expect(page.locator('#run-button')).to_be_enabled()
    expect(page.locator('#latest-status')).to_have_text('Not run')
    expect(page.locator('#count-high')).to_have_text('—')
    page.screenshot(path=str(args.screenshots / 'empty-desktop.png'), full_page=True)
    for scenario, status, counts in [('combined','REVIEW_REQUIRED',['1','1','1']), ('no_drift','NO_DRIFT',['0','0','0']), ('unsupported','MANUAL_REVIEW',['0','0','0'])]:
        page.locator('#scenario').select_option(scenario)
        held = []
        # A saved no-drift run must remain confirmed if its history refresh fails.
        def intercept(route):
            if route.request.method == 'POST':
                held.append(route)
            elif scenario == 'no_drift':
                route.abort()
            else:
                route.continue_()
        page.route('**/api/runs', intercept)
        page.locator('#run-button').click()
        expect(page.locator('#run-button')).to_be_disabled()
        expect(page.locator('#scenario')).to_be_disabled()
        expect(page.locator('#progress')).to_contain_text('Audit in progress')
        page.wait_for_timeout(50)
        assert len(held) == 1
        held[0].continue_()
        expect(page.locator('#progress')).to_contain_text('Execution saved')
        if scenario == 'no_drift':
            expect(page.locator('#error')).to_contain_text('Audit saved, but history could not refresh')
        page.unroute('**/api/runs')
        expect(page.locator('#latest-status')).to_have_text(status)
        for risk, count in zip(['high','medium','low'], counts):
            expect(page.locator('#count-' + risk)).to_have_text(count)
        record = page.request.get(args.url + '/api/runs').json()[0]
        with page.expect_download() as download:
            page.locator('#download').click()
        downloaded = json.loads(Path(download.value.path()).read_text())
        assert downloaded == record
        if scenario == 'combined':
            expect(page.locator('.finding')).to_have_count(3)
            page.locator('.finding summary').first.click()
            expect(page.locator('.finding').first).to_have_attribute('open','')
            for finding in record['audit']['findings']:
                detail = page.locator('.finding').filter(has_text=finding['category'].upper() + ' configuration changed')
                assert detail.locator('pre').text_content() == '\n'.join(finding['evidence']) + '\n'
            assert page.locator('#patch pre').text_content() == '\n'.join(record['audit']['proposal']['patch']) + '\n'
            configs = page.locator('.config-grid pre').all_text_contents()
            assert configs == [record['configurations']['approved'], record['configurations']['observed']]
            page.evaluate('window.scrollTo(0,0)')
            page.screenshot(path=str(args.screenshots / 'combined-desktop.png'), full_page=True)
        elif scenario == 'no_drift':
            expect(page.locator('.finding')).to_have_count(0)
            expect(page.locator('#patch')).to_contain_text('No baseline-update patch is needed')
        else:
            expect(page.locator('#review-content')).to_contain_text('Raw configuration is withheld')
            expect(page.locator('#overview-note')).to_contain_text('risk was not assessed')
            expect(page.locator('#patch')).to_contain_text('No proposal generated')
            page.screenshot(path=str(args.screenshots / 'manual-desktop.png'), full_page=True)
    expect(page.locator('#history-rows tr')).to_have_count(3)
    page.reload()
    expect(page.locator('#history-rows tr')).to_have_count(3)
    page.get_by_role('button', name='Open Combined changes', exact=False).click()
    expect(page.locator('#selected-meta')).to_contain_text('Combined changes')
    expect(page.locator('#latest-status')).to_have_text('MANUAL_REVIEW')
    page.set_viewport_size({'width':390,'height':844})
    page.locator('.finding summary').first.click()
    page.screenshot(path=str(args.screenshots / 'combined-mobile.png'), full_page=True)
    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
    for code in page.locator('.config-grid pre').all():
        box = code.bounding_box()
        assert box['x'] >= 0 and box['x'] + box['width'] <= 390
    # Simulated network failure must produce an actionable visible error.
    page.route('**/api/runs', lambda route: route.abort())
    page.locator('#refresh').click()
    expect(page.locator('#error')).to_be_visible()
    page.unroute('**/api/runs')
    page.locator('#refresh').click()
    expect(page.locator('#error')).to_be_hidden()
    assert not errors, errors
    browser.close()
print('PASS: empty state; combined/no-change/manual review; evidence/patch/config equality; JSON download; reload/history; mobile overflow; network error recovery; no JS errors.')
