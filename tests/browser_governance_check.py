"""Browser baseline approvals and separate audit decisions using synthetic identities."""
import argparse
import json
from pathlib import Path
import uuid
from playwright.sync_api import sync_playwright, expect

parser = argparse.ArgumentParser()
parser.add_argument('--url', required=True)
parser.add_argument('--chrome')
args = parser.parse_args()
with sync_playwright() as p:
    browser = p.chromium.launch(**({'executable_path': args.chrome} if args.chrome else {}), headless=True)
    page = browser.new_page(viewport={'width':1440, 'height':1050}, reduced_motion='reduce')
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto(args.url)
    expect(page.locator('#baseline-list')).to_contain_text('No baseline candidates yet')
    page.get_by_role('link', name='Baselines', exact=True).click()
    page.locator('#baseline-actor').fill('Demo proposer')
    page.locator('#baseline-reason').fill('Initial lab reference GOV-DEMO-1')
    page.get_by_role('button', name='Propose baseline candidate', exact=True).click()
    expect(page.locator('.baseline-card')).to_have_count(1)
    candidate = page.request.get(args.url + '/api/baselines').json()[0]
    assert candidate['status'] == 'PENDING'
    card = page.locator('.baseline-card').first
    card.locator('summary').first.click()
    expect(card.locator('pre')).to_have_text(candidate['configuration'])
    card.get_by_label('Decision-maker name (self-declared)').fill('Demo administrator')
    card.get_by_label('Reason / change reference').fill('Reviewed exact reference text and hash')
    card.get_by_role('button', name='Record baseline decision', exact=True).click()
    expect(page.locator('.baseline-card').first.locator('summary').first).to_contain_text('Approved for comparison')
    page.get_by_role('link', name='Run an audit', exact=True).click()
    page.locator('#baseline-selection').select_option(candidate['baseline_id'])
    page.locator('#scenario').select_option('combined')
    page.locator('#run-button').click()
    expect(page.locator('#progress')).to_contain_text('Execution saved')
    expect(page.locator('#latest-status')).to_have_attribute('data-status', 'REVIEW_REQUIRED')
    expect(page.locator('#review-content')).to_contain_text('Registry baseline ' + candidate['baseline_id'])
    first = page.request.get(args.url + '/api/runs').json()[0]
    expect(page.locator('#review-decision-form')).to_be_visible()
    page.locator('#review-actor').fill('Demo reviewer')
    page.locator('#review-reason').fill('All three observed changes are intended for this lab scenario')
    page.get_by_role('button', name='Record review decision', exact=True).click()
    expect(page.locator('#review-log')).to_contain_text('Accept observed settings for consideration')
    expect(page.locator('#review-message')).to_contain_text('Decision recorded')
    assert page.request.get(args.url + '/api/runs/' + first['id']).json() == first
    page.get_by_role('link', name='Baselines', exact=True).click()
    page.locator('#baseline-source').select_option(first['id'])
    page.locator('#baseline-reason').fill('Propose accepted observation as a separate version')
    page.get_by_role('button', name='Propose baseline candidate', exact=True).click()
    expect(page.locator('.baseline-card')).to_have_count(2)
    observed = page.request.get(args.url + '/api/baselines').json()[0]
    assert observed['configuration'] == first['configurations']['observed']
    card = page.locator('.baseline-card').first
    card.locator('summary').first.click()
    card.get_by_label('Decision-maker name (self-declared)').fill('Demo administrator')
    card.get_by_label('Reason / change reference').fill('Approved intended observation as a new immutable version')
    card.get_by_role('button', name='Record baseline decision', exact=True).click()
    expect(page.locator('.baseline-card').first.locator('summary').first).to_contain_text('Approved for comparison')
    page.get_by_role('link', name='Run an audit', exact=True).click()
    page.locator('#baseline-selection').select_option(observed['baseline_id'])
    page.locator('#run-button').click()
    expect(page.locator('#progress')).to_contain_text('Execution saved')
    expect(page.locator('#latest-status')).to_have_attribute('data-status', 'NO_DRIFT')
    clean = page.request.get(args.url + '/api/runs').json()[0]
    expect(page.locator('#review-action')).to_have_value('ACKNOWLEDGE')
    # Another tab records a decision; the displayed revision must not overwrite it.
    history = page.request.get(args.url + '/api/runs/' + clean['id'] + '/reviews').json()
    response = page.request.post(args.url + '/api/runs/' + clean['id'] + '/reviews', data={
        'request_id':str(uuid.uuid4()), 'action':'DEFER', 'actor':'Second demo reviewer', 'reason':'Need a second check',
        'expected_revision':history['revision'], 'expected_audit_sha256':history['audit_sha256']})
    assert response.status == 200
    page.locator('#review-reason').fill('Stale acknowledgement must be rejected')
    page.get_by_role('button', name='Record review decision', exact=True).click()
    expect(page.locator('#review-message')).to_contain_text('Another decision was recorded')
    page.get_by_role('link', name='Past audits', exact=True).click()
    page.locator(f'tr[data-run-id="{first["id"]}"]').get_by_role('button').click()
    expect(page.locator('#review-log')).to_contain_text('Accept observed settings for consideration')
    with page.expect_download() as download:
        page.locator('#review-download').click()
    exported = json.loads(Path(download.value.path()).read_text())
    assert exported['audit_id'] == first['audit']['audit_id']
    assert exported['events'][0]['action'] == 'ACCEPT_OBSERVED'
    # Retiring the selected baseline must not silently switch to an ungoverned fixture.
    page.get_by_role('link', name='Run an audit', exact=True).click()
    page.locator('#baseline-selection').select_option(candidate['baseline_id'])
    page.get_by_role('link', name='Baselines', exact=True).click()
    card = page.locator('.baseline-card').nth(1)
    card.locator('summary').first.click()
    expect(card.get_by_label('Baseline decision')).to_have_value('RETIRE')
    card.get_by_label('Decision-maker name (self-declared)').fill('Demo administrator')
    card.get_by_label('Reason / change reference').fill('Use the new version for future audits')
    card.get_by_role('button', name='Record baseline decision', exact=True).click()
    expect(page.locator('.baseline-card').nth(1).locator('summary').first).to_contain_text('Retired')
    expect(page.locator('#baseline-selection')).to_have_value(candidate['baseline_id'])
    page.get_by_role('link', name='Run an audit', exact=True).click()
    count = len(page.request.get(args.url + '/api/runs').json())
    page.locator('#run-button').click()
    expect(page.locator('#error')).to_contain_text('Only currently approved baselines')
    assert len(page.request.get(args.url + '/api/runs').json()) == count
    assert page.request.get(args.url + '/api/runs/' + first['id']).json() == first
    page.get_by_role('link', name='Run an audit', exact=True).click()
    page.locator('#baseline-selection').select_option('')
    page.locator('#scenario').select_option('unsupported')
    page.locator('#run-button').click()
    expect(page.locator('#latest-status')).to_have_attribute('data-status', 'MANUAL_REVIEW')
    expect(page.locator('#review-action')).to_have_value('INVESTIGATE')
    assert page.locator('#review-action option').all_text_contents() == ['Request investigation', 'Defer a decision']
    page.locator('#review-reason').fill('Inspect unsupported fixture syntax locally')
    page.get_by_role('button', name='Record review decision', exact=True).click()
    expect(page.locator('#review-log')).to_contain_text('Request investigation')
    page.reload()
    expect(page.locator('#review-log')).to_contain_text('Request investigation')
    page.locator('#decisions').screenshot(path='runs/ci-browser-screenshots/governance-review.png')
    page.get_by_role('link', name='Baselines', exact=True).click()
    page.locator('.baseline-card').first.locator('summary').first.click()
    page.locator('#baselines').screenshot(path='runs/ci-browser-screenshots/governance-baselines.png')
    page.set_viewport_size({'width':390,'height':844})
    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
    page.get_by_role('link', name='Review results', exact=True).click()
    page.locator('#decisions').screenshot(path='runs/ci-browser-screenshots/governance-mobile.png')
    assert not errors, errors
    browser.close()
print('PASS: baseline proposal/approval/retirement, governed audits, immutable evidence, review exports, stale decisions, manual-review restrictions, restart visibility, and mobile layout.')
