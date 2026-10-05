"""Exercise recovery controls against real persisted synthetic output."""
import argparse
import json
from pathlib import Path
import uuid
from datetime import datetime, timezone
from playwright.sync_api import sync_playwright, expect

parser = argparse.ArgumentParser()
parser.add_argument('--url', required=True)
parser.add_argument('--data-dir', type=Path, required=True)
parser.add_argument('--chrome')
args = parser.parse_args()
with sync_playwright() as p:
    browser = p.chromium.launch(**({'executable_path':args.chrome} if args.chrome else {}), headless=True)
    page = browser.new_page()
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    key = str(uuid.uuid4())
    record = page.request.post(args.url + '/api/runs', data={'scenario':'combined','request_id':key}).json()
    # Simulate the final dashboard JSON write being interrupted after audit persistence.
    (args.data_dir / key / 'record.json').unlink()
    page.goto(args.url)
    expect(page.locator('#latest-status')).to_have_attribute('data-status', 'INTERRUPTED')
    expect(page.locator('#count-high')).to_have_text('—')
    page.get_by_role('link', name='Review results', exact=True).click()
    page.get_by_role('button', name='Recover saved output', exact=True).click()
    expect(page.locator('#progress')).to_contain_text('Saved output recovered')
    expect(page.locator('#review-content .finding')).to_have_count(3)
    recovered = page.request.get(args.url + '/api/runs/' + key).json()
    assert recovered == record
    interrupted = str(uuid.uuid4())
    folder = args.data_dir / interrupted
    folder.mkdir()
    (folder / 'request.json').write_text(json.dumps({'scenario':'route','audit_id':interrupted,
        'created_at':datetime.now(timezone.utc).isoformat(),'state':'RUNNING'}))
    page.reload()
    expect(page.locator('#latest-status')).to_have_attribute('data-status', 'INTERRUPTED')
    page.get_by_role('link', name='Review results', exact=True).click()
    page.get_by_role('button', name='Recover saved output', exact=True).click()
    expect(page.locator('#error')).to_contain_text('No complete auditor output was saved')
    page.get_by_role('button', name='Prepare a new execution', exact=True).click()
    expect(page.locator('#scenario')).to_have_value('route')
    page.locator('#run-button').click()
    expect(page.locator('#progress')).to_contain_text('Execution saved')
    records = page.request.get(args.url + '/api/runs').json()
    assert any(r['id'] == interrupted and r['audit'] is None for r in records)
    assert records[0]['scenario'] == 'route' and records[0]['id'] != interrupted
    assert not errors, errors
    page.screenshot(path='runs/ci-browser-screenshots/recovery.png', full_page=True)
    browser.close()
print('PASS: browser recovery, unavailable counts, missing-output error, new execution preserves interrupted record.')
