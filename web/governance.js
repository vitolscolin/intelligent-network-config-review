// Decision records are independent of auditor output and device execution.
const reviewLabels = {
  ACCEPT_OBSERVED: ['Accept observed configuration for consideration', 'Records that the observation is intended. Propose and approve a separate baseline candidate to use it in future audits.'],
  REJECT_CHANGE: ['Reject the observed change', 'Records an unwanted change. Restoration must follow a separate authorized procedure; no device commands are sent.'],
  INVESTIGATE: ['Request investigation', 'Records that further evidence or investigation is required. It does not resolve validation errors.'],
  DEFER: ['Defer a decision', 'Records why the decision is deferred. Findings and drafts remain unchanged.'],
  ACKNOWLEDGE: ['Acknowledge no supported drift', 'Acknowledges this audit result. It is not a complete device-security assessment.'],
};
let reviewContext = null;

function eventList(events) {
  const list = node('ol', undefined, 'decision-events');
  events.forEach(event => {
    const item = node('li');
    item.append(node('strong', `${event.action} · ${event.actor}`), node('p', event.reason),
      node('small', `${date(event.recorded_at)} · Revision ${event.revision} · ${event.event_id}`));
    list.append(item);
  });
  return list;
}

async function saveDecision(form, path, body) {
  const button = form.querySelector('button[type="submit"]');
  if (button.disabled) return null;
  button.disabled = true;
  const key = `governance-pending:${path}`;
  const encoded = JSON.stringify(body);
  try {
    const saved = JSON.parse(sessionStorage.getItem(key) || 'null');
    const requestId = saved?.body === encoded ? saved.request_id : crypto.randomUUID();
    sessionStorage.setItem(key, JSON.stringify({body: encoded, request_id: requestId}));
    const result = await api(path, {method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({...body, request_id: requestId})});
    sessionStorage.removeItem(key);
    return result;
  } finally {
    button.disabled = false;
  }
}

function eligibleSource(record, context) {
  const source = $('baseline-source');
  source.replaceChildren();
  const fixture = node('option', 'Built-in synthetic reference');
  fixture.value = 'fixture';
  source.append(fixture);
  if (context?.evidence_matches && context.events.at(-1)?.action === 'ACCEPT_OBSERVED') {
    const observed = node('option', `Accepted observation · ${record.scenario_name} · ${record.audit.audit_id}`);
    observed.value = record.id;
    source.append(observed);
  }
}

async function showReviewLog(record) {
  reviewContext = null;
  $('review-decision-form').hidden = true;
  $('review-download').hidden = true;
  $('review-message').textContent = '';
  eligibleSource(record, null);
  const log = $('review-log');
  if (!record.audit) {
    log.replaceChildren(node('p', 'Recover a complete audit before recording administrator decisions.'));
    return;
  }
  log.replaceChildren(node('p', 'Loading administrator decisions…'));
  try {
    const context = await api(`/api/runs/${record.id}/reviews`);
    if (selected?.id !== record.id) return;
    reviewContext = {record, ...context};
    log.replaceChildren(node('p', `${record.scenario_name} · Audit ${context.audit_id} · Decisions cover the entire audit.`));
    log.append(context.events.length ? eventList(context.events) : node('p', 'No administrator decision has been recorded.'));
    log.append(node('p', 'Later decisions supersede earlier decisions in this view; all records remain in the ledger. Audit findings and original drafts are unchanged.', 'muted'));
    if (!context.evidence_matches) {
      log.append(node('p', 'Evidence differs from the recorded reviews. Further decisions are blocked; inspect local storage.'));
      return;
    }
    const actions = $('review-action');
    actions.replaceChildren();
    context.allowed_actions.forEach(action => {
      const option = node('option', reviewLabels[action][0]); option.value = action; actions.append(option);
    });
    $('review-action').onchange();
    $('review-decision-form').hidden = false;
    $('review-download').hidden = false;
    $('review-download').href = `/api/runs/${record.id}/reviews/download`;
    eligibleSource(record, context);
  } catch (e) {
    if (selected?.id === record.id) log.replaceChildren(node('p', e.message));
  }
}

$('review-action').onchange = () => {
  $('review-action-help').textContent = reviewLabels[$('review-action').value]?.[1] || '';
};
$('review-decision-form').onsubmit = async event => {
  event.preventDefault();
  if (!reviewContext) return;
  const context = reviewContext;
  try {
    const result = await saveDecision(event.currentTarget, `/api/runs/${context.record.id}/reviews`, {
      action: $('review-action').value, actor: $('review-actor').value, reason: $('review-reason').value,
      expected_revision: context.revision, expected_audit_sha256: context.audit_sha256,
    });
    if (!result) return;
    if (selected?.id === context.record.id) {
      await showReviewLog(context.record);
      $('review-message').textContent = 'Decision recorded. Audit output and device configuration are unchanged.';
    }
  } catch (e) { $('review-message').textContent = e.message + ' Reopen this audit to refresh its decision history.'; }
};

function baselineCard(candidate) {
  const detail = node('details', undefined, 'finding baseline-card');
  const summary = node('summary');
  summary.append(badge(candidate.status, candidate.status), node('span', `${candidate.sha256.slice(0, 12)} · ${candidate.source.kind === 'BUILT_IN_FIXTURE' ? 'Fixture reference' : 'Accepted audit observation'}`));
  detail.append(summary);
  const body = node('div', undefined, 'finding-body');
  body.append(node('p', `Version ${candidate.baseline_id} · SHA-256 ${candidate.sha256}`, 'provenance-note'),
    node('pre', candidate.configuration), eventList(candidate.events));
  const allowed = candidate.status === 'PENDING' ? ['APPROVE', 'REJECT'] : candidate.status === 'APPROVED' ? ['RETIRE'] : [];
  if (allowed.length) {
    const form = node('form', undefined, 'governance-form');
    const action = node('select'), actor = node('input'), reason = node('textarea');
    action.id = 'action-' + candidate.baseline_id;
    actor.id = 'actor-' + candidate.baseline_id;
    reason.id = 'reason-' + candidate.baseline_id;
    actor.required = true; actor.maxLength = 100; reason.required = true; reason.maxLength = 2000;
    allowed.forEach(value => { const option = node('option', value === 'APPROVE' ? 'Approve this exact version' : value === 'REJECT' ? 'Reject this candidate' : 'Retire from future audits'); option.value = value; action.append(option); });
    [[action, 'Baseline decision'], [actor, 'Decision-maker name (self-declared)'], [reason, 'Reason / change reference']].forEach(([input, title]) => {
      const label = node('label', title); label.htmlFor = input.id; form.append(label, input);
    });
    const button = node('button', 'Record baseline decision'); button.type = 'submit'; form.append(button);
    const message = node('p'); message.setAttribute('role', 'status');
    form.onsubmit = async event => {
      event.preventDefault();
      try {
        const result = await saveDecision(form, `/api/baselines/${candidate.baseline_id}/decisions`, {
          action: action.value, actor: actor.value, reason: reason.value,
          expected_revision: candidate.revision, expected_sha256: candidate.sha256,
        });
        if (!result) return;
        $('baseline-message').textContent = 'Baseline decision recorded. Future audits require an explicit baseline selection.';
        await refreshBaselines();
      } catch (e) { message.textContent = e.message + ' Refresh the registry before retrying a stale decision.'; }
    };
    body.append(node('p', 'Approval makes this immutable version eligible for future selection. It does not select it automatically, overwrite past baselines, or change a device.', 'muted'), form, message);
  }
  detail.append(body);
  return detail;
}

async function refreshBaselines() {
  const baselines = await api('/api/baselines');
  const list = $('baseline-list');
  list.replaceChildren();
  if (!baselines.length) list.append(node('p', 'No baseline candidates yet. Fixture references are not automatically approved.'));
  baselines.forEach(candidate => list.append(baselineCard(candidate)));
  const select = $('baseline-selection'), previous = select.value;
  select.replaceChildren();
  const fixture = node('option', 'Fixture reference · not governed'); fixture.value = ''; select.append(fixture);
  baselines.filter(candidate => candidate.status === 'APPROVED').forEach(candidate => {
    const option = node('option', `Approved · ${candidate.sha256.slice(0, 12)} · ${candidate.baseline_id}`);
    option.value = candidate.baseline_id; select.append(option);
  });
  if ([...select.options].some(option => option.value === previous)) select.value = previous;
  else if (previous) {
    const unavailable = node('option', `Unavailable baseline · ${previous}`);
    unavailable.value = previous; unavailable.disabled = true; select.append(unavailable); select.value = previous;
    $('baseline-message').textContent += ' Your previous selection is no longer approved; explicitly choose another baseline before running an audit.';
  }
}

$('baseline-proposal-form').onsubmit = async event => {
  event.preventDefault();
  try {
    const result = await saveDecision(event.currentTarget, '/api/baselines', {
      source: $('baseline-source').value, actor: $('baseline-actor').value, reason: $('baseline-reason').value,
    });
    if (!result) return;
    $('baseline-message').textContent = `Candidate ${result.baseline_id} recorded. Open it below to inspect and decide.`;
    await refreshBaselines();
  } catch (e) { $('baseline-message').textContent = e.message; }
};
const refreshRegistry = node('button', 'Refresh registry', 'secondary');
refreshRegistry.type = 'button';
refreshRegistry.onclick = () => refreshBaselines().catch(e => { $('baseline-message').textContent = e.message; });
$('baseline-list').before(refreshRegistry);
refreshBaselines().catch(e => { $('baseline-message').textContent = e.message; $('baseline-list').textContent = 'Registry unavailable. Refresh to retry.'; });
