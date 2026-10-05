const $ = id => document.getElementById(id);
let records = [], selected = null, scenarios = [], busy = false;
const date = value => new Date(value).toLocaleString();
const statusOf = record => record.audit?.status || record.execution_status;
const timeOf = record => record.audit?.collected_at || record.created_at;
const node = (tag, text, cls) => { const el = document.createElement(tag); if (text !== undefined) el.textContent = text; if (cls) el.className = cls; return el; };
const statusLabels = {NO_DRIFT: 'No supported changes', REVIEW_REQUIRED: 'Needs your review', MANUAL_REVIEW: 'Needs investigation', RUNNING: 'Audit running', FAILED: 'Audit could not finish', INTERRUPTED: 'Audit interrupted', PENDING: 'Awaiting approval', APPROVED: 'Approved for comparison', REJECTED: 'Not approved', RETIRED: 'Retired'};
const badge = (text, cls) => { const el = node('span', statusLabels[text] || text, `badge ${cls}`); el.dataset.status = text; return el; };
async function api(path, options) {
  let response;
  try { response = await fetch(path, options); }
  catch { throw new TypeError("Cannot reach the local server. Check that python3 dashboard.py is running, then refresh history before retrying."); }
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || 'Request failed. Refresh history and try again.');
  return data;
}
function error(message) { $('error').textContent = message; $('error').hidden = !message; }
function patch(lines) {
  const pre = node('pre', undefined, 'patch');
  lines.forEach(line => { const span = node('span', line + '\n', line.startsWith('---') || line.startsWith('+++') || line.startsWith('@@') ? 'diff-heading' : line.startsWith('+') ? 'diff-add' : line.startsWith('-') ? 'diff-remove' : ''); pre.append(span); });
  return pre;
}
function overview() {
  const latest = records[0]?.audit;
  $('latest-status').textContent = records[0] ? statusLabels[statusOf(records[0])] || statusOf(records[0]) : 'Not run';
  $('latest-status').dataset.status = records[0] ? statusOf(records[0]) : '';
  $('latest-scenario').textContent = records[0]?.scenario_name || 'Choose a scenario to begin';
  $('latest-time').textContent = records[0] ? date(timeOf(records[0])) : 'No executions yet';
  for (const risk of ['high', 'medium', 'low']) $('count-' + risk).textContent = latest ? latest.findings.filter(f => f.risk === risk).length : '—';
  $('overview-note').textContent = records[0] && !latest ? 'Execution incomplete. Findings and risk are unavailable; open its history record for recovery.' : latest?.status === 'MANUAL_REVIEW' ? 'Latest audit stopped at validation. Zero recorded findings does not mean safe; risk was not assessed.' : 'Severity counts reflect the latest stored audit. Open Past audits to revisit earlier results.';
}
function history() {
  $('history-rows').replaceChildren(); $('history-empty').hidden = !!records.length;
  records.forEach(record => {
    const run = record.audit, row = node('tr'); row.dataset.runId = record.id; if (selected?.id === record.id) row.className = 'selected';
    row.append(node('td', date(timeOf(record))), node('td', record.scenario_name));
    const status = node('td'); status.append(badge(statusOf(record), statusOf(record))); row.append(status);
    const risk = node('td'); risk.append(badge(run?.highest_risk?.toUpperCase() || 'NOT ASSESSED', run?.highest_risk || 'none')); row.append(risk);
    const action = node('td'), button = node('button', selected?.id === record.id ? 'Viewing' : 'Open', 'secondary');
    button.setAttribute('aria-label', `Open ${record.scenario_name} from ${date(timeOf(record))}`);
    button.onclick = () => { show(record); goTo('review'); }; action.append(button); row.append(action); $('history-rows').append(row);
  });
}
function show(record) {
  selected = record; const run = record.audit;
  if (typeof showReviewLog === 'function') showReviewLog(record);
  $('selected-meta').textContent = `${record.scenario_name} · ${date(timeOf(record))} · lab-router · ${statusLabels[statusOf(record)] || statusOf(record)}`;
  $('download').hidden = false; $('download').href = `/api/runs/${record.id}/download`;
  const content = $('review-content'); content.className = 'panel'; content.replaceChildren();
  content.append(badge(statusOf(record), statusOf(record)));
  if (!run) {
    content.append(node('h3', 'Execution incomplete · No audit result available'), node('p', record.reason));
    const recover = node('button', 'Recover saved output', 'secondary');
    recover.disabled = record.execution_status === 'RUNNING';
    recover.onclick = async () => {
      recover.disabled = true;
      error('');
      try {
        const recovered = await api(`/api/runs/${record.id}/recover`, {method: 'POST'});
        show(recovered);
        await refresh();
        $('progress').textContent = 'Saved output recovered. The auditor was not rerun.';
      } catch (e) { error(e.message); recover.disabled = false; }
    };
    content.append(recover);
    if (record.scenario) {
      const retry = node('button', 'Prepare a new execution', 'secondary');
      retry.disabled = record.execution_status === 'RUNNING';
      retry.onclick = () => {
        sessionStorage.removeItem('network-review-pending');
        $('scenario').value = record.scenario;
        $('scenario').onchange();
        $('progress').textContent = 'Scenario selected. Run audit to create a new execution; the old record is preserved.';
        goTo('run');
        $('run-button').focus();
      };
      content.append(retry);
    }
    $('patch').replaceChildren(node('p', 'No validated response is available for this incomplete execution.'));
    history();
    return;
  }
  if (run.status === 'MANUAL_REVIEW') {
    content.append(node('h3', 'We could not safely compare these settings'), node('p', run.reason), node('p', 'No risk assessment or response draft is available. Raw configuration is withheld. Inspect the local synthetic fixture and baseline, or run a supported scenario.'));
  } else {
    content.append(node('h3', run.findings.length ? `${run.findings.length} findings to review` : 'No supported changes found'), node('p', run.findings.length ? 'Open a finding to inspect its supporting evidence. High-risk findings are listed first; configuration and ACL line order are preserved.' : 'The parser found no differences in supported categories. This is not a complete device-security assessment.'));
    [...run.findings].sort((a,b) => ({high:0,medium:1,low:2}[a.risk] - {high:0,medium:1,low:2}[b.risk])).forEach(finding => {
      const detail = node('details', undefined, 'finding'), summary = node('summary');
      detail.dataset.category = finding.category;
      const names = {snmp: 'Device location (SNMP)', vlan: 'Network groups (VLANs)', route: 'Traffic paths (routes)', acl: 'Traffic access rules (ACLs)'};
      summary.append(badge(finding.risk.toUpperCase(), finding.risk), node('span', `${names[finding.category] || finding.category} changed`));
      const body = node('div', undefined, 'finding-body'); body.append(node('p', finding.explanation), node('h3', 'What changed · reference → observed'), patch(finding.evidence));
      const link = node('a', 'Review draft response →', 'text-link'); link.href = '#response'; body.append(link); detail.append(summary, body); content.append(detail);
    });
    const grid = node('div', undefined, 'config-grid');
    for (const side of ['approved', 'observed']) { const col = node('div'); col.append(node('h3', side === 'approved' ? (record.baseline_governance?.mode === 'GOVERNED' ? 'Expected · approved baseline' : 'Expected · built-in sample (not approved)') : 'Found · observed sample'), node('pre', record.configurations[side])); grid.append(col); }
    content.append(grid);
  }
  const binding = record.baseline_governance;
  const approval = binding?.mode === 'GOVERNED'
    ? `Registry baseline ${binding.baseline_id} · approved at selection by ${binding.approval_event.actor} · event ${binding.approval_event.event_id}`
    : 'Fixture or legacy reference: no recorded governance approval. Git commit existence is not approval.';
  const next = node('a', run.status === 'REVIEW_REQUIRED' ? 'Next: inspect the draft response →' : 'Next: record your decision →', 'text-link next-step'); next.href = run.status === 'REVIEW_REQUIRED' ? '#response' : '#decisions'; content.append(next);
  const provenance = node('details'), summary = node('summary', 'Technical details & baseline approval'); provenance.append(summary, node('pre', `Status: ${run.status}\n${approval}\nAudit ID: ${run.audit_id}\nParser: ${run.parser}\nMode: ${run.mode}\nBaseline commit: ${run.baseline_commit || 'Unavailable'}\nRecorded stages: ${run.stages.join(' → ')}`)); content.append(provenance);
  $('patch').replaceChildren(run.proposal ? patch(run.proposal.patch) : node('p', run.status === 'NO_DRIFT' ? 'No baseline-update patch is needed: no supported drift was detected.' : 'No proposal generated: resolve the validation issue before assessing a response.'));
  history();
}
async function refresh() {
  records = await api('/api/runs'); overview(); history();
  if (selected) {
    const updated = records.find(record => record.id === selected.id);
    if (updated && JSON.stringify(updated) !== JSON.stringify(selected)) show(updated);
  } else if (records.length) show(records[0]);
}
$('scenario').onchange = () => { $('scenario-description').textContent = scenarios.find(s => s.id === $('scenario').value)?.description || ''; };
$('refresh').onclick = async () => { try { await refresh(); error(''); } catch (e) { error(e.message); } };
$('audit-form').onsubmit = async event => {
  event.preventDefault(); if (busy) return; busy = true; error('');
  $('run-button').disabled = true; $('scenario').disabled = true; $('baseline-selection').disabled = true; $('run-button').textContent = 'Running…'; $('audit-form').setAttribute('aria-busy','true');
  const scenario = $('scenario').value;
  const baseline_id = $('baseline-selection').value || null;
  const pendingKey = 'network-review-pending';
  let requestId = crypto.randomUUID();
  try {
    const pending = JSON.parse(sessionStorage.getItem(pendingKey) || 'null');
    if (pending?.scenario === scenario && (pending.baseline_id || null) === baseline_id) requestId = pending.request_id;
    sessionStorage.setItem(pendingKey, JSON.stringify({scenario, baseline_id, request_id:requestId}));
    $('progress').textContent = 'Audit in progress: preparing fixture, comparing configurations, and persisting the result…';
    const record = await api('/api/runs', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({scenario,baseline_id,request_id:requestId})});
    sessionStorage.removeItem(pendingKey);
    // A saved execution remains successful even if the subsequent history read fails.
    records = [record, ...records.filter(existing => existing.id !== record.id)];
    overview();
    show(record);
    try { await refresh(); }
    catch (e) { error(`Audit saved, but history could not refresh. ${e.message}`); }
    $('progress').textContent = `Execution saved · ${statusLabels[record.audit.status]}. ${record.audit.status === 'MANUAL_REVIEW' ? 'Validation needs administrator inspection.' : 'Open a finding in Review results to continue.'}`;
    goTo('review');
  } catch (e) {
    error(e.message); $('progress').textContent = 'Execution was not confirmed. Refresh history before retrying.';
    // A received rejection is safe to retry with a new ID; network errors retain the ID.
    if (!(e instanceof TypeError)) sessionStorage.removeItem(pendingKey);
  } finally { busy = false; $('run-button').disabled = false; $('scenario').disabled = false; $('baseline-selection').disabled = false; $('run-button').textContent = 'Run audit →'; $('audit-form').removeAttribute('aria-busy'); }
};
(async () => { try { scenarios = await api('/api/scenarios'); scenarios.forEach(s => {const option = node('option', s.name); option.value = s.id; $('scenario').append(option);}); $('scenario').value = 'combined'; $('scenario').onchange(); await refresh(); $('scenario').disabled = false; $('run-button').disabled = false; $('recommended-demo').disabled = false; $('progress').textContent = 'Ready · Each execution is stored locally.'; } catch (e) {error(e.message); $('progress').textContent = 'Could not load the dashboard. Check the server and reload this page.';} })();

$('recommended-demo').onclick = () => { if (busy) return; $('baseline-selection').value = ''; $('scenario').value = 'combined'; $('scenario').onchange(); goTo('run'); $('run-button').focus(); $('progress').textContent = 'Recommended sample selected. Choose Run audit when ready.'; };
