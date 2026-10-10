const $ = (id) => document.getElementById(id);
let selectedAction = "";

const fmt = (n) => Number(n || 0).toLocaleString();

function notice(message) {
  const el = $("notice");
  el.textContent = message;
  el.classList.add("show");
  setTimeout(() => el.classList.remove("show"), 6000);
}

async function api(url, options) {
  const response = await fetch(url, { headers: { "Content-Type": "application/json" }, ...options });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.detail || `Request failed (${response.status})`);
  return body;
}

function renderActionOptions(actions) {
  const select = $("action-select");
  const ids = actions.map((action) => action.actionId);
  if (!ids.includes(selectedAction)) selectedAction = ids[0] || "";
  select.innerHTML = "";
  if (!actions.length) select.add(new Option("No actions yet", ""));
  for (const action of actions) {
    select.add(new Option(`${action.actionId} · ${action.status}`, action.actionId));
  }
  select.value = selectedAction;
}

function renderAccounts(overview) {
  const items = overview.items || [];
  const select = $("account-id");
  const selected = select.value;
  select.innerHTML = "";
  if (!items.length) select.add(new Option("Create an account first", ""));
  for (const account of items) {
    select.add(new Option(`${account.name} · ${account.accountId}`, account.accountId));
  }
  if (items.some((account) => account.accountId === selected)) select.value = selected;
  else if (items.length) select.value = items[0].accountId;

  const limit = Number(overview.limitPerMinute || 0);
  $("account-rows").innerHTML = items.length
    ? items.map((account) => {
      const used = Number(account.eventsReserved || 0);
      const remaining = Number(account.remaining || 0);
      const percent = limit ? Math.min(used / limit * 100, 100) : 0;
      return `<tr>
        <td><strong>${escapeHtml(account.name)}</strong><small class="account-id-label">${escapeHtml(account.accountId)}</small></td>
        <td>${fmt(account.contacts)}</td>
        <td><div class="limit-cell"><span>${fmt(used)} / ${fmt(limit)}</span><div class="limit-track"><i style="width:${percent}%"></i></div></div></td>
        <td>${fmt(remaining)}</td>
        <td><button class="button secondary account-use" data-account-id="${escapeHtml(account.accountId)}" type="button">Use</button></td>
      </tr>`;
    }).join("")
    : '<tr><td colspan="5" class="empty">No accounts yet. Add one above, then seed or add contacts.</td></tr>';
}

function renderWorkers(workers, database, postgres) {
  const enabled = workers.enabled;
  const slider = $("worker-range");
  slider.max = postgres ? (workers.maxWorkers || 8) : 1;
  slider.disabled = !enabled;
  $("scale-button").disabled = !enabled;
  $("scale-button").title = enabled ? "" : "Set ENABLE_LOCAL_WORKER_CONTROL=true in .env and restart the API";
  $("worker-note").textContent = `${database}${postgres ? " · PostgreSQL" : " · single worker mode"}${enabled ? " · local control enabled" : " · local control disabled"}`;
  const managed = (workers.managedWorkers || []).filter((worker) => worker.running);
  $("worker-count").textContent = managed.length || "—";
  $("worker-list").innerHTML = managed.length
    ? managed.map((worker) => `<div class="worker-row"><span>${escapeHtml(worker.workerId)}</span><span>● PID ${worker.pid}</span></div>`).join("")
    : `<div class="worker-row"><span>External worker process</span><span>${postgres ? "managed outside UI" : "single worker"}</span></div>`;
}

async function refresh() {
  try {
    const overview = await api("/api/accounts/overview");
    renderAccounts(overview);
    const accountId = $("account-id").value;
    const state = await api(accountId
      ? `/api/dashboard?accountId=${encodeURIComponent(accountId)}`
      : "/api/dashboard");
    $("source-count").textContent = fmt(state.contacts);
    $("last-updated").textContent = `Updated ${new Date(state.refreshedAt).toLocaleTimeString()}`;
    renderActionOptions(state.actions || []);
    renderWorkers(state.workers, state.database, state.multiWorkerSupported);
    const queue = state.queue || {};
    $("queue-summary").textContent = `Queue · ${fmt(queue.queued || 0)} queued / ${fmt(queue.running || 0)} running`;
    if (selectedAction) await refreshAction(selectedAction);
    else resetStats();
  } catch (error) {
    $("last-updated").textContent = "Disconnected";
    notice(error.message);
  }
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[character]);
}

function paint(done, running, pending, total, staged) {
  const percent = (value) => total ? `${(value / total * 100).toFixed(1)}%` : "0.0%";
  $("completed-count").textContent = fmt(done);
  $("progress-count").textContent = fmt(running);
  $("pending-count").textContent = fmt(pending);
  $("completed-percent").textContent = `${percent(done)} of selected`;
  $("progress-percent").textContent = `${percent(running)} of selected`;
  $("pending-percent").textContent = `${percent(pending)} of selected`;
  $("overall-progress").textContent = percent(done);
  $("seg-done").style.width = percent(done);
  $("seg-running").style.width = percent(running);
  $("seg-waiting").style.width = percent(pending);
  $("staged-count").textContent = `${fmt(staged)} staged`;
}

function resetStats() {
  $("completed-count").textContent = "—";
  $("progress-count").textContent = "—";
  $("pending-count").textContent = "—";
  $("throughput").textContent = "—";
  $("elapsed-time").textContent = "Records per second";
  $("selected-action-label").textContent = "Select an action to inspect live progress";
  $("batch-rows").innerHTML = '<tr><td colspan="5" class="empty">Create an action to see persisted batches.</td></tr>';
  $("queue-records").classList.add("hidden");
  $("toggle-queue").textContent = "＋ Show pending record IDs";
  paint(0, 0, 0, 0, 0);
}

async function refreshAction(actionId) {
  try {
    const [stats, batches] = await Promise.all([
      api(`/bulk-actions/${actionId}/stats`),
      api(`/bulk-actions/${actionId}/batches?limit=100`),
    ]);
    paint(stats.processed, stats.inProgress, stats.pending, stats.total, stats.staged);
    $("throughput").textContent = `${Number(stats.recordsPerSecond || 0).toLocaleString()} /s`;
    $("elapsed-time").textContent = `${Number(stats.elapsedSeconds || 0).toLocaleString()}s elapsed (average)`;
    const stage = stats.selectionComplete ? "selection complete" : stats.countReady ? "scanning source" : "counting source";
    $("selected-action-label").textContent = `${actionId} · ${stage} · ${fmt(stats.total)} selected`;
    const items = batches.items || [];
    $("batch-rows").innerHTML = items.length
      ? items.map((batch) => `<tr>
          <td>${escapeHtml(batch.batchId)}</td><td>${fmt(batch.itemCount)}</td>
          <td><span class="state ${batch.status}">${batch.status}</span></td>
          <td>${batch.workerId ? `<span class="worker-tag">${escapeHtml(batch.workerId)}</span>` : "<span class='result'>queued</span>"}</td>
          <td><span class="result">${fmt(batch.success)} ✓ &nbsp; ${fmt(batch.failed)} ! &nbsp; ${fmt(batch.pending)} …</span></td>
        </tr>`).join("")
      : '<tr><td colspan="5" class="empty">No batches staged yet. The selection worker is scanning the source.</td></tr>';
    if (!$("queue-records").classList.contains("hidden")) await refreshQueue(actionId);
  } catch (error) {
    notice(error.message);
  }
}

async function refreshQueue(actionId) {
  const queue = await api(`/bulk-actions/${actionId}/queue?limit=100`);
  $("queue-rows").innerHTML = queue.items.length
    ? queue.items.map((item) => `<tr><td>${fmt(item.entityId)}</td><td>${escapeHtml(item.batchId)}</td>
        <td><span class="state ${item.batchStatus}">${item.batchStatus}</span></td>
        <td>${item.workerId ? `<span class="worker-tag">${escapeHtml(item.workerId)}</span>` : "<span class='result'>queued</span>"}</td></tr>`).join("")
    : '<tr><td colspan="4" class="empty">No pending staged records. More may still be unstaged.</td></tr>';
}

$("action-select").addEventListener("change", (event) => {
  selectedAction = event.target.value;
  if (selectedAction) refreshAction(selectedAction);
  else resetStats();
});
$("refresh").addEventListener("click", refresh);
$("toggle-queue").addEventListener("click", async () => {
  const panel = $("queue-records");
  panel.classList.toggle("hidden");
  const expanded = !panel.classList.contains("hidden");
  $("toggle-queue").textContent = expanded ? "− Hide pending record IDs" : "＋ Show pending record IDs";
  if (expanded && selectedAction) {
    try { await refreshQueue(selectedAction); } catch (error) { notice(error.message); }
  }
});
$("account-id").addEventListener("change", refresh);
$("account-rows").addEventListener("click", (event) => {
  const button = event.target.closest("[data-account-id]");
  if (!button) return;
  $("account-id").value = button.dataset.accountId;
  refresh();
});
$("account-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const result = await api("/accounts", {
      method: "POST",
      body: JSON.stringify({ id: $("new-account-id").value.trim(), name: $("new-account-name").value.trim() }),
    });
    $("new-account-id").value = "";
    $("new-account-name").value = "";
    notice(`Account ${result.id} created. Add contacts with the API or seed command.`);
    await refresh();
    $("account-id").value = result.id;
    await refresh();
  } catch (error) { notice(error.message); }
});
$("worker-range").addEventListener("input", (event) => { $("worker-output").textContent = event.target.value; });
$("scale-button").addEventListener("click", async () => {
  try {
    const result = await api("/api/workers/scale", {
      method: "POST", body: JSON.stringify({ count: Number($("worker-range").value) }),
    });
    notice(`Worker pool updated: ${result.managedCount} managed worker(s).`);
    await refresh();
  } catch (error) { notice(error.message); }
});
$("action-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const action = await api("/bulk-actions", {
      method: "POST",
      body: JSON.stringify({
        accountId: $("account-id").value.trim(), entity: "contact", action: "update",
        filter: { status: $("filter-status").value }, updates: { status: $("update-status").value },
      }),
    });
    selectedAction = action.actionId;
    notice(`Created ${action.actionId}. Workers will stage and process matching records.`);
    await refresh();
  } catch (error) { notice(error.message); }
});

refresh();
setInterval(refresh, 1000);
