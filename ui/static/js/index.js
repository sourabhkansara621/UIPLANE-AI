    const API = '';  // same origin
    let token = localStorage.getItem('k8sai_token') || '';
    let chatSessionId = localStorage.getItem('k8sai_session_id') || null;
    let currentUser = null;
    let namespacesExpanded = (localStorage.getItem('k8sai_namespaces_expanded') ?? 'true') === 'true';
    let namespaceFilter = '';
    let cachedNamespaces = [];
    let selectedNamespace = localStorage.getItem('k8sai_selected_namespace') || null;
    let selectedApp = localStorage.getItem('k8sai_selected_app') || null;
    let selectedCluster = localStorage.getItem('k8sai_selected_cluster') || null;
    let cachedClusterEntries = [];
    let chatMode = localStorage.getItem('k8sai_chat_mode') || 'k8-info';
    let autofixDatadogRangeHours = Number(localStorage.getItem('k8sai_autofix_dd_range') || '6');
    let autofixDatadogSource = localStorage.getItem('k8sai_autofix_dd_source') || 'live';
    let autofixDatadogSeverity = localStorage.getItem('k8sai_autofix_dd_severity') || 'all';
    let autofixDatadogAutoSeconds = Number(localStorage.getItem('k8sai_autofix_dd_auto') || '0');
    let autofixDatadogAutoTimer = null;
    let autofixDatadogCurrentIssues = [];
    let autofixDatadogLastSource = 'live';
    let autofixPodSearchQuery = '';
    let autofixSelectedNamespace = localStorage.getItem('k8sai_selected_namespace') || null;
    let themeMode = localStorage.getItem('k8sai_theme') || 'dark';

    function getCurrentNamespace() {
      if (chatMode === 'k8-autofix') {
        return autofixSelectedNamespace || selectedNamespace || null;
      }
      return selectedNamespace || null;
    }

    function applyTheme(theme) {
      themeMode = theme === 'light' ? 'light' : 'dark';
      document.body.classList.toggle('light-theme', themeMode === 'light');
      const toggle = document.getElementById('theme-toggle');
      if (toggle) toggle.checked = themeMode === 'light';
      const toggleText = document.getElementById('theme-toggle-text');
      const toggleWrap = document.getElementById('theme-toggle-wrap');
      if (toggleText) toggleText.textContent = themeMode === 'light' ? 'Day View' : 'Dark View';
      if (toggleWrap) toggleWrap.title = themeMode === 'light' ? 'Theme: Day View (Light)' : 'Theme: Dark View (Night)';
    }

    function toggleTheme(isOn) {
      const nextTheme = isOn ? 'light' : 'dark';
      applyTheme(nextTheme);
      localStorage.setItem('k8sai_theme', nextTheme);
    }

    // Deployment editor state
    let deploymentEditorState = {
      deploymentName: '',
      resourceKind: 'Deployment',
      namespace: '',
      appName: '',
      originalYaml: '',
      editedYaml: ''
    };
    let modalConfirmStep = 0;

    // ----------------------------------------
    async function doLogin() {
      const username = document.getElementById('username').value.trim();
      const password = document.getElementById('password').value;
      if (!username || !password) return;
      const loginErrorEl = document.getElementById('login-error');
      if (loginErrorEl) loginErrorEl.style.display = 'none';

      try {
        const res = await fetch(`${API}/api/auth/login`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ username, password }),
        });
        if (!res.ok) {
          let detail = 'Invalid credentials. Try again.';
          try {
            const errData = await res.json();
            if (errData?.detail) detail = String(errData.detail);
          } catch { }
          throw new Error(detail);
        }
        const data = await res.json();
        token = data.access_token;
        currentUser = data.user;
        localStorage.setItem('k8sai_token', token);
        auditLog('LOGIN', 'user', username, {
          result_summary: 'Successful login',
          success: true
        });
        showApp();
      } catch (e) {
        if (loginErrorEl) {
          loginErrorEl.textContent = e?.message || 'Login failed. Please try again.';
          loginErrorEl.style.display = 'block';
        }
        auditLog('LOGIN', 'user', username, {
          result_summary: `Failed login attempt: ${e?.message || 'unknown error'}`,
          success: false
        });
      }
    }

    async function logout() {
      // Call backend logout endpoint (if exists) to invalidate session
      if (token) {
        try {
          await fetch('/api/auth/logout', {
            method: 'POST',
            headers: {
              'Authorization': `Bearer ${token}`,
              'Content-Type': 'application/json'
            }
          });
        } catch (err) {
          console.warn('Logout API call failed:', err);
        }
      }
      
      // Clear frontend state
      token = '';
      currentUser = null;
      chatSessionId = null;
      localStorage.removeItem('k8sai_token');
      localStorage.removeItem('k8sai_session_id');
      
      // Reset UI
      document.getElementById('main-view').classList.add('hidden');
      document.getElementById('login-view').classList.remove('hidden');
      document.getElementById('user-chip').classList.add('hidden');
      
      // Clear and reset input fields
      const usernameField = document.getElementById('username');
      const passwordField = document.getElementById('password');
      usernameField.value = '';
      passwordField.value = '';
      usernameField.autocomplete = 'off';
      passwordField.autocomplete = 'off';
      
      // Force browser to clear any cached values
      setTimeout(() => {
        usernameField.autocomplete = 'username';
        passwordField.autocomplete = 'current-password';
      }, 100);
    }

    async function checkExistingToken() {
      if (!token) return;
      try {
        const res = await fetch(`${API}/api/auth/me`, { headers: authHeaders() });
        if (!res.ok) throw new Error();
        currentUser = await res.json();
        showApp();
      } catch {
        token = '';
        localStorage.removeItem('k8sai_token');
      }
    }

    function authHeaders() {
      return { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` };
    }

    // ----------------------------------------
    async function auditLog(action, resourceType, resourceName, details = {}) {
      if (!token || !currentUser) return;
      try {
        await fetch(`${API}/api/audit/log`, {
          method: 'POST',
          headers: authHeaders(),
          body: JSON.stringify({
            action: action,
            resource_type: resourceType,
            resource_name: resourceName,
            app_name: details.app_name || null,
            namespace: details.namespace || selectedNamespace || null,
            query_text: details.query_text || null,
            result_summary: details.result_summary || null,
            success: details.success !== false,
            extra: details.extra || {}
          })
        });
      } catch (e) {
        console.warn('Audit log failed:', e.message);
      }
    }

    // ----------------------------------------
    async function showApp() {
      document.getElementById('login-view').classList.add('hidden');
      document.getElementById('main-view').classList.remove('hidden');
      document.getElementById('user-chip').classList.remove('hidden');
      document.getElementById('chip-name').textContent = currentUser.username;
      document.getElementById('chip-role').textContent = currentUser.role;
      document.getElementById('status-dot').style.background = 'var(--success)';
      document.getElementById('status-dot').style.boxShadow = '0 0 6px var(--success)';

      // Clear chat messages for fresh start
      document.getElementById('messages').innerHTML = '';
      
      renderApps();
      await loadClusters();
      await loadSuggestions();
      await loadNamespaces();
      renderChatMode();
      if (chatMode !== 'k8-autofix') {
        addMsg('ai', `Authenticated as ${currentUser.username} (${currentUser.role}).\nApps: ${currentUser.allowed_apps.join(', ') || 'none'}\n\nStart with a namespace query, then ask for pods/quota/HPA/ingress in that namespace.`);
      }
    }

    function modeWelcomeText(mode) {
      const external = window.K8ModeHandlers?.[mode]?.welcome;
      if (external) return external;
      if (mode === 'k8-agent') return 'k8-agent loaded fresh. Select namespace, then manage deployments.';
      if (mode === 'k8-autofix') return 'k8-autofix loaded fresh. Select namespace to inspect Datadog issues.';
      return 'k8-info loaded fresh. Select namespace and ask read-only Kubernetes questions.';
    }

    function modeShowWelcomeInChat(mode) {
      const external = window.K8ModeHandlers?.[mode]?.showWelcomeInChat;
      if (external === false) return false;
      return true;
    }

    async function loadFreshModeScreen(mode) {
      const messages = document.getElementById('messages');
      if (messages) messages.innerHTML = '';

      // Close any transient autofix UI state whenever mode is clicked.
      closeAutofixModal();
      _afxHistoryOpen = false;
      _afxExamplesOpen = false;
      renderAutofixHistoryPanel();
      renderAutofixExamplePanel();

      if (autofixDatadogAutoTimer) {
        clearInterval(autofixDatadogAutoTimer);
        autofixDatadogAutoTimer = null;
      }

      autofixPodSearchQuery = '';
      const podInput = document.getElementById('autofix-dd-pod-search');
      if (podInput) podInput.value = '';
      clearAutofixPodDetails();

      // Keep one shared namespace context across all modes.
      await Promise.all([loadSuggestions(), loadNamespaces()]);

      if (mode === 'k8-autofix') {
        renderNamespaces();
        refreshAutofixDatadogIssues();
        renderAutofixSeverityButtons();
        renderAutofixHistoryPanel();
        renderAutofixExamplePanel();
      }

      if (modeShowWelcomeInChat(mode)) addMsg('ai', modeWelcomeText(mode));
    }

    async function setChatMode(mode) {
      const allowed = ['k8-info', 'k8-agent', 'k8-autofix'];
      const nextMode = allowed.includes(mode) ? mode : 'k8-info';
      chatMode = nextMode;
      localStorage.setItem('k8sai_chat_mode', nextMode);
      renderChatMode();
      await loadFreshModeScreen(nextMode);
    }

    function renderChatMode() {
      const allowed = ['k8-info', 'k8-agent', 'k8-autofix'];
      if (!allowed.includes(chatMode)) chatMode = 'k8-info';
      const isAutofixMode = chatMode === 'k8-autofix';

      const btnInfo = document.getElementById('mode-k8-info');
      const btnAgent = document.getElementById('mode-k8-agent');
      const btnAutofix = document.getElementById('mode-k8-autofix');
      if (btnInfo) btnInfo.classList.toggle('active', chatMode === 'k8-info');
      if (btnAgent) btnAgent.classList.toggle('active', chatMode === 'k8-agent');
      if (btnAutofix) btnAutofix.classList.toggle('active', chatMode === 'k8-autofix');

      const note = document.getElementById('chat-mode-note');
      if (!note) return;
      if (chatMode === 'k8-agent') {
        note.textContent = 'k8-agent: shows deployments and allows deployment edits.';
      } else if (chatMode === 'k8-autofix') {
        note.textContent = 'k8-autofix: Datadog issues view for selected namespace and range.';
      } else {
        note.textContent = 'k8-info: read-only cluster and namespace insights.';
      }

      const suggestionsRow = document.getElementById('suggestions');
      if (suggestionsRow) {
        suggestionsRow.classList.toggle('hidden', isAutofixMode);
      }

      const messagesPane = document.getElementById('messages');
      if (messagesPane) {
        messagesPane.classList.toggle('hidden', isAutofixMode);
      }

      const inputRow = document.querySelector('.input-row');
      if (inputRow) {
        inputRow.classList.toggle('hidden', isAutofixMode);
      }

      const chatInput = document.getElementById('chat-input');
      if (chatInput) {
        if (!chatInput.dataset.defaultPlaceholder) {
          chatInput.dataset.defaultPlaceholder = chatInput.getAttribute('placeholder') || '';
        }
        if (isAutofixMode) {
          chatInput.value = '';
          chatInput.disabled = true;
          chatInput.setAttribute('placeholder', 'Autofix mode uses Datadog issue cards. Switch to k8-info or k8-agent for chat.');
        } else {
          chatInput.disabled = false;
          chatInput.setAttribute('placeholder', chatInput.dataset.defaultPlaceholder || 'Start with: list namespaces for sandbox');
        }
      }

      const sendBtn = document.getElementById('send-btn');
      if (sendBtn) {
        sendBtn.disabled = isAutofixMode;
      }

      const ddPanel = document.getElementById('autofix-datadog-panel');
      if (ddPanel) {
        ddPanel.classList.toggle('hidden', !isAutofixMode);
      }

      const chatWrap = document.querySelector('.chat-wrap');
      if (chatWrap) {
        chatWrap.classList.toggle('autofix-mode', isAutofixMode);
      }

      const inputArea = document.querySelector('.input-area');
      if (inputArea) {
        inputArea.classList.toggle('mode-footer', true);
      }

      if (isAutofixMode) {
        refreshAutofixDatadogIssues();
        renderAutofixSeverityButtons();
        renderAutofixHistoryPanel();
      }
    }
    function formatTimestamp(ts) {
      if (!ts) return 'last seen: unknown';
      const dt = new Date(ts);
      if (Number.isNaN(dt.getTime())) return `last seen: ${ts}`;
      return `last seen: ${dt.toLocaleString()}`;
    }

    function wildcardToRegex(pattern) {
      const escaped = String(pattern || '')
        .replace(/[.+?^${}()|[\]\\]/g, '\\$&')
        .replace(/\*/g, '.*');
      return new RegExp(`^${escaped}$`, 'i');
    }

    function podMatchesQuery(podName, query) {
      const pod = String(podName || '').toLowerCase();
      const q = String(query || '').trim().toLowerCase();
      if (!q) return true;
      if (q.includes('*')) {
        try {
          return wildcardToRegex(q).test(pod);
        } catch {
          return false;
        }
      }
      // strict prefix matching for normal search terms
      return pod.startsWith(q);
    }

    function findMatchingPodIssues(query) {
      const sourceIssues = Array.isArray(autofixDatadogCurrentIssues) ? autofixDatadogCurrentIssues : [];
      return sourceIssues.filter((it) => podMatchesQuery(it?.pod_name || '', query));
    }

    function setAutofixPodSearch(podName) {
      const input = document.getElementById('autofix-dd-pod-search');
      if (input) input.value = podName || '';
      autofixPodSearchQuery = podName || '';
      searchAutofixPodDetails();
    }

    async function searchAutofixPodDetails() {
      const input = document.getElementById('autofix-dd-pod-search');
      const query = (input?.value || '').trim();
      autofixPodSearchQuery = query;

      if (!query) {
        clearAutofixPodDetails();
        return;
      }
      if (!selectedNamespace) {
        renderAutofixPodDetails('<div>Select namespace first.</div>');
        return;
      }

      const matches = findMatchingPodIssues(query);
      const exact = matches.find((it) => String(it?.pod_name || '').toLowerCase() === query.toLowerCase()) || null;
      const issue = exact || (matches.length === 1 ? matches[0] : null);

      let matchListHtml = '';
      if (matches.length > 1) {
        const chips = matches.slice(0, 10).map((m) => {
          const pod = String(m?.pod_name || '');
          const safePod = pod.replace(/\\/g, '\\\\').replace(/'/g, "\\'");
          return `<button type="button" class="pod-match-btn" onclick="setAutofixPodSearch('${safePod}')">${escapeHtml(pod)}</button>`;
        }).join('');
        matchListHtml = `<div class="pod-details-row"><span class="pod-details-chip">${matches.length} matches</span></div><div class="pod-match-list">${chips}</div>`;
      }

      const issueHtml = issue
        ? `<div><strong>${escapeHtml(issue.pod_name)}</strong> ${issue.severity ? `<span class="pod-details-chip">${escapeHtml(issue.severity)}</span>` : ''}</div>
           <div>${escapeHtml(issue.issue || 'No Datadog issue text')}</div>
           <div class="pod-details-row"><span class="pod-details-chip">${escapeHtml(formatTimestamp(issue.last_seen))}</span><span class="pod-details-chip">occurrences ${Number(issue.occurrences || 0)}</span></div>${matchListHtml}`
        : matches.length
          ? `<div>${matchListHtml}</div>`
          : `<div>No pod match for <strong>${escapeHtml(query)}</strong>.</div>`;

      renderAutofixDatadogList(autofixDatadogCurrentIssues || []);

      if (!selectedApp) {
        renderAutofixPodDetails(`${issueHtml}<div class="pod-details-row"><span class="pod-details-chip">Tip: select an app to fetch live pod describe.</span></div>`);
        return;
      }

      if (!issue) {
        renderAutofixPodDetails(`${issueHtml}<div class="pod-details-row"><span class="pod-details-chip">Pick one pod above for live describe.</span></div>`);
        return;
      }

      renderAutofixPodDetails(`${issueHtml}<div class="pod-details-row"><span class="pod-details-chip">Fetching live pod details...</span></div>`);

      const podName = issue.pod_name;
      try {
        const url = `${API}/api/k8s/${encodeURIComponent(selectedApp)}/pods/${encodeURIComponent(podName)}/describe?namespace=${encodeURIComponent(selectedNamespace)}`;
        const res = await fetch(url, { headers: authHeaders() });
        const data = await res.json();
        if (!res.ok) throw new Error(data?.detail || 'Pod describe failed');

        const containerStates = Array.isArray(data?.containers)
          ? data.containers.slice(0, 3).map((c) => `${c.name}:${c.state || 'unknown'}`).join(' | ')
          : 'n/a';

        const liveHtml = `
          <div class="pod-details-row">
            <span class="pod-details-chip">status ${escapeHtml(data?.status || 'unknown')}</span>
            <span class="pod-details-chip">node ${escapeHtml(data?.node || 'n/a')}</span>
            <span class="pod-details-chip">ip ${escapeHtml(data?.pod_ip || 'n/a')}</span>
          </div>
          <div class="pod-details-row">
            <span class="pod-details-chip">containers ${escapeHtml(containerStates)}</span>
          </div>
        `;
        renderAutofixPodDetails(`${issueHtml}${liveHtml}`);
      } catch (err) {
        const msg = err?.message || 'Unable to fetch pod describe';
        renderAutofixPodDetails(`${issueHtml}<div class="pod-details-row"><span class="pod-details-chip">${escapeHtml(msg)}</span></div>`);
      }
    }

    function renderAutofixDatadogList(issues) {
      const list = document.getElementById('autofix-dd-list');
      if (!list) return;

      const sourceIssues = Array.isArray(issues) ? issues : [];
      const severityFiltered = autofixDatadogSeverity === 'all'
        ? sourceIssues
        : sourceIssues.filter((item) => (item?.severity || '').toLowerCase() === autofixDatadogSeverity);

      const query = (autofixPodSearchQuery || '').trim();
      const filtered = query
        ? severityFiltered.filter((item) => podMatchesQuery(item?.pod_name || '', query))
        : severityFiltered;

      if (sourceIssues.length && !filtered.length) {
        const msg = query
          ? `No pods match "${escapeHtml(query)}" with current filters.`
          : 'No issues match the selected severity filter.';
        list.innerHTML = `<div class="autofix-dd-item">${msg}</div>`;
        return;
      }

      if (!filtered.length) {
        list.innerHTML = '<div class="autofix-dd-item">No pod issues found in selected range.</div>';
        return;
      }

      list.innerHTML = filtered.slice(0, 20).map((item, idx) => {
        const severity = (item.severity || 'info').toLowerCase();
        const sevClass = severity === 'critical' ? 'sev-critical'
          : severity === 'error' ? 'sev-error'
          : severity === 'warning' ? 'sev-warning' : '';
        const issue = (item.issue || 'No issue message').replace(/</g, '&lt;').replace(/>/g, '&gt;');
        const pod = (item.pod_name || 'unknown-pod').replace(/</g, '&lt;').replace(/>/g, '&gt;');
        const count = Number(item.occurrences || 0);
        const safeIdx = idx;
        return `
          <div class="autofix-dd-item">
            <div class="autofix-dd-item-head">
              <span class="autofix-dd-pod">${pod}</span>
              <span class="autofix-dd-badges">
                <span class="autofix-dd-severity ${sevClass}">${severity}</span>
                <span class="autofix-dd-count">x${count}</span>
              </span>
            </div>
            <div class="autofix-dd-issue">${issue}</div>
            <div class="autofix-dd-time">${formatTimestamp(item.last_seen)}</div>
            <div class="autofix-dd-item-actions">
              <button class="afx-btn" type="button" onclick="openAutofixModal(autofixDatadogCurrentIssues[${safeIdx}])">⚡ Autofix</button>
            </div>
          </div>
        `;
      }).join('');
    }

    // ── Autofix History ─────────────────────────────────────────────────────

    function _afxHistoryKey(ns) {
      return `k8sai_afx_history_${ns || 'unknown'}`;
    }

    function _getAfxHistory(ns) {
      const key = _afxHistoryKey(ns);
      try {
        const stored = localStorage.getItem(key);
        return stored ? JSON.parse(stored) : [];
      } catch (e) {
        console.warn('Failed to parse autofix history:', e);
        return [];
      }
    }

    function _afxCountsByNamespace(ns) {
      const counts = { all: 0, critical: 0, error: 0, warning: 0 };
      const history = _getAfxHistory(ns);
      // Count from current issues (Datadog panel)
      const issues = autofixDatadogCurrentIssues || [];
      issues.forEach((issue) => {
        const sev = (issue.severity || '').toLowerCase();
        if (counts[sev] !== undefined) counts[sev]++;
        counts.all++;
      });
      // Also count from history
      history.forEach(h => {
        const sev = (h.severity || '').toLowerCase();
        if (counts[sev] !== undefined) counts[sev]++;
        counts.all++;
      });
      return counts;
    }

    function _deleteAfxHistoryEntry(ns, index) {
      const history = _getAfxHistory(ns);
      history.splice(index, 1);
      localStorage.setItem(_afxHistoryKey(ns), JSON.stringify(history));
    }

    function _updateAfxHistoryEntry(ns, index, updates) {
      const history = _getAfxHistory(ns);
      if (!history[index]) return;
      history[index] = { ...history[index], ...updates };
      localStorage.setItem(_afxHistoryKey(ns), JSON.stringify(history));
    }

    let _afxHistoryOpen = false;
    let _afxExamplesOpen = false;

    function setAutofixSeverityFilter(severity) {
      autofixDatadogSeverity = (severity || 'all').toLowerCase();
      localStorage.setItem('k8sai_autofix_dd_severity', autofixDatadogSeverity);
      _afxHistoryOpen = false;
      _afxExamplesOpen = false;
      syncAutofixFilterButtonState();
      renderAutofixDatadogList(autofixDatadogCurrentIssues || []);
    }

    function syncAutofixFilterButtonState() {
      const severityButtons = document.querySelectorAll('.autofix-dd-sev-btn[data-severity]');
      const historyBtn = document.getElementById('autofix-history-toggle');
      const exampleBtn = document.getElementById('autofix-example-toggle');

      severityButtons.forEach((btn) => btn.classList.remove('active'));
      if (historyBtn) historyBtn.classList.remove('active');
      if (exampleBtn) exampleBtn.classList.remove('active');

      if (_afxHistoryOpen) {
        if (historyBtn) historyBtn.classList.add('active');
        return;
      }
      if (_afxExamplesOpen) {
        if (exampleBtn) exampleBtn.classList.add('active');
        return;
      }

      severityButtons.forEach((btn) => {
        const severity = (btn.getAttribute('data-severity') || '').toLowerCase();
        if (severity === autofixDatadogSeverity) btn.classList.add('active');
      });
    }

    function toggleAutofixHistory() {
      _afxHistoryOpen = !_afxHistoryOpen;
      _afxExamplesOpen = false;
      const panel = document.getElementById('autofix-history-panel');
      const examples = document.getElementById('autofix-example-panel');
      const body = document.getElementById('afx-history-body');
      const historyBtn = document.getElementById('autofix-history-toggle');
      const exampleBtn = document.getElementById('autofix-example-toggle');
      if (panel) panel.classList.toggle('hidden', !_afxHistoryOpen);
      if (examples) examples.classList.add('hidden');
      if (body) body.classList.toggle('open', _afxHistoryOpen);
      syncAutofixFilterButtonState();
    }

    function toggleAutofixExamples() {
      _afxExamplesOpen = !_afxExamplesOpen;
      _afxHistoryOpen = false;
      const examples = document.getElementById('autofix-example-panel');
      const panel = document.getElementById('autofix-history-panel');
      const body = document.getElementById('afx-history-body');
      const historyBtn = document.getElementById('autofix-history-toggle');
      const exampleBtn = document.getElementById('autofix-example-toggle');
      if (examples) examples.classList.toggle('hidden', !_afxExamplesOpen);
      if (panel) panel.classList.add('hidden');
      if (body) body.classList.remove('open');
      syncAutofixFilterButtonState();
    }

    function renderAutofixHistoryPanel() {
      const panel = document.getElementById('autofix-history-panel');
      const body = document.getElementById('afx-history-body');
      const toggleBtn = document.getElementById('autofix-history-toggle');
      if (!panel || !body) return;

      const ns = selectedNamespace;
      if (chatMode !== 'k8-autofix' || !ns) {
        panel.classList.add('hidden');
        if (toggleBtn) toggleBtn.textContent = 'Autofix History (0)';
        const examples = document.getElementById('autofix-example-panel');
        const historyBtn = document.getElementById('autofix-history-toggle');
        const exampleBtn = document.getElementById('autofix-example-toggle');
        if (examples) examples.classList.add('hidden');
        syncAutofixFilterButtonState();
        return;
      }
      if (_afxHistoryOpen) {
        panel.classList.remove('hidden');
      } else {
        panel.classList.add('hidden');
      }

      const history = _getAfxHistory(ns);
      if (toggleBtn) toggleBtn.textContent = `Autofix History (${history.length})`;

      if (!history.length) {
        body.innerHTML = '<div class="afx-history-empty">No autofix history yet.</div>';
        return;
      }

      const ACTION_LABELS = {
        restart: '🔄 Restart',
        scale_up: '📦 Scale Up',
        increase_memory: '📈 Memory',
        increase_cpu: '⚡ CPU',
        patch_config: '⚙️ Config',
      };

      body.innerHTML = history.map((h, i) => {
        const label = ACTION_LABELS[h.action] || h.action;
        const sev = (h.severity || '').toLowerCase();
        const sevClass = sev === 'critical' ? 'sev-critical' : sev === 'error' ? 'sev-error' : sev === 'warning' ? 'sev-warning' : '';
        const pod = (h.pod || '').replace(/</g, '&lt;').replace(/>/g, '&gt;');
        const when = h.ts ? new Date(h.ts).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '';
        const paramsText = h.params && Object.keys(h.params).length
          ? Object.entries(h.params).map(([k, v]) => `${k}=${v}`).join('  ')
          : '';
        return `
          <div class="afx-history-row">
            <div class="afx-history-info">
              <div class="afx-history-row-head">
                <span class="afx-history-pod" title="${pod}">${pod}</span>
                <span class="afx-history-action-tag">${label}</span>
                ${sev ? `<span class="autofix-dd-severity ${sevClass}" style="font-size:10px;padding:.1rem .4rem;">${sev}</span>` : ''}
              </div>
              ${paramsText ? `<div class="afx-history-params">${paramsText}</div>` : ''}
              <div class="afx-history-time">${when}</div>
            </div>
            <div class="afx-history-actions">
              <button class="afx-history-reapply-btn" type="button" onclick="reapplyAutofixFromHistory(${i})">Re-apply / Edit</button>
              <button class="afx-history-delete-btn" type="button" onclick="deleteAutofixHistoryEntry(${i})">Delete</button>
            </div>
          </div>
        `;
      }).join('');

      // keep body open state in sync
      if (_afxHistoryOpen) body.classList.add('open');
      else body.classList.remove('open');
    }

    function deleteAutofixHistoryEntry(index) {
      _deleteAfxHistoryEntry(selectedNamespace, index);
      renderAutofixHistoryPanel();
      renderAutofixSeverityButtons();
    }

    function clearAllAutofixHistory() {
      if (!selectedNamespace) return;
      localStorage.removeItem(_afxHistoryKey(selectedNamespace));
      renderAutofixHistoryPanel();
      renderAutofixSeverityButtons();
    }

    function reapplyAutofixFromHistory(index) {
      const history = _getAfxHistory(selectedNamespace);
      const entry = history[index];
      if (!entry) return;

      // Build a synthetic issue item that can open the autofix modal
      const syntheticItem = {
        pod_name: entry.pod,
        severity: entry.severity,
        issue: entry.issue || '',
        last_seen: entry.ts,
        occurrences: entry.occurrences || 0,
        _historyIndex: index,
        _prefillAction: entry.action,
        _prefillParams: entry.params || {},
      };
      openAutofixModal(syntheticItem);
    }

    // ── Autofix Modal ───────────────────────────────────────────────────────

    function _detectIssueType(issueText) {
      const t = (issueText || '').toLowerCase();
      if (/oomkill|memory limit|exit code 137/.test(t)) return 'oom';
      if (/crashloopbackoff|startup probe|liveness probe|crash/.test(t)) return 'crash';
      if (/5xx|upstream timeout|timeout|connection refused|502|503/.test(t)) return 'traffic';
      if (/restart count|db connection|database|connection reset/.test(t)) return 'config';
      return 'generic';
    }

    function _buildAutofixActions(item) {
      const type = _detectIssueType(item.issue);
      const actions = [];

      if (type === 'oom') {
        actions.push({
          id: 'increase_memory',
          title: '📈 Increase Memory Limit',
          desc: 'Pod was OOMKilled — increase the container memory limit to prevent future restarts.',
          fields: [
            { label: 'Memory Limit', key: 'memory_limit', default: '512Mi', placeholder: 'e.g. 512Mi, 1Gi' },
            { label: 'Memory Request', key: 'memory_request', default: '256Mi', placeholder: 'e.g. 256Mi' },
          ],
          action: 'increase_memory',
        });
        actions.push({
          id: 'restart',
          title: '🔄 Rollout Restart',
          desc: 'Perform a rolling restart of the deployment to clear the crashed pod.',
          fields: [],
          action: 'restart',
        });
      } else if (type === 'crash') {
        actions.push({
          id: 'restart',
          title: '🔄 Rollout Restart',
          desc: 'Restart the deployment — clears transient CrashLoopBackOff caused by init issues.',
          fields: [],
          action: 'restart',
        });
        actions.push({
          id: 'increase_memory',
          title: '📈 Increase Memory Limit',
          desc: 'If crash is memory-related, bump the memory limit.',
          fields: [
            { label: 'Memory Limit', key: 'memory_limit', default: '512Mi', placeholder: 'e.g. 512Mi' },
          ],
          action: 'increase_memory',
        });
        actions.push({
          id: 'patch_config',
          title: '⚙️ Patch ConfigMap Key',
          desc: 'Update a configuration key to fix a misconfigured startup value.',
          fields: [
            { label: 'ConfigMap Name', key: 'configmap', default: '', placeholder: 'my-app-config' },
            { label: 'Key', key: 'key', default: '', placeholder: 'e.g. DB_RETRY_LIMIT' },
            { label: 'Value', key: 'value', default: '', placeholder: 'new value' },
          ],
          action: 'patch_config',
        });
      } else if (type === 'traffic') {
        actions.push({
          id: 'scale_up',
          title: '📦 Scale Up Replicas',
          desc: 'Reduce 5xx/timeout spikes by increasing the number of replicas.',
          fields: [
            { label: 'Target Replicas', key: 'replicas', default: '3', placeholder: 'e.g. 3' },
          ],
          action: 'scale_up',
        });
        actions.push({
          id: 'increase_cpu',
          title: '⚡ Increase CPU Limit',
          desc: 'Timeout could be CPU throttling — increase the CPU limit for the container.',
          fields: [
            { label: 'CPU Limit', key: 'cpu_limit', default: '500m', placeholder: 'e.g. 500m, 1' },
            { label: 'CPU Request', key: 'cpu_request', default: '200m', placeholder: 'e.g. 200m' },
          ],
          action: 'increase_cpu',
        });
      } else if (type === 'config') {
        actions.push({
          id: 'patch_config',
          title: '⚙️ Patch ConfigMap Key',
          desc: 'Fix DB/config-related restarts by updating a ConfigMap value.',
          fields: [
            { label: 'ConfigMap Name', key: 'configmap', default: '', placeholder: 'my-app-config' },
            { label: 'Key', key: 'key', default: '', placeholder: 'e.g. DB_POOL_SIZE' },
            { label: 'Value', key: 'value', default: '', placeholder: 'new value' },
          ],
          action: 'patch_config',
        });
        actions.push({
          id: 'restart',
          title: '🔄 Rollout Restart',
          desc: 'Restart to pick up config changes or clear stale DB connections.',
          fields: [],
          action: 'restart',
        });
      } else {
        actions.push({
          id: 'restart',
          title: '🔄 Rollout Restart',
          desc: 'Restart the owning deployment to clear the pod issue.',
          fields: [],
          action: 'restart',
        });
        actions.push({
          id: 'scale_up',
          title: '📦 Scale Up Replicas',
          desc: 'Add more replicas to spread load and improve availability.',
          fields: [
            { label: 'Target Replicas', key: 'replicas', default: '2', placeholder: 'e.g. 2' },
          ],
          action: 'scale_up',
        });
      }

      return actions;
    }

    let _afxCurrentItem = null;

    function openAutofixModal(item) {
      if (!item) return;
      _afxCurrentItem = item;
      const modal = document.getElementById('autofix-action-modal');
      if (!modal) return;

      document.getElementById('afx-pod-name').textContent = item.pod_name || 'unknown';
      document.getElementById('afx-issue-banner').textContent = item.issue || '';

      const sev = (item.severity || 'info').toLowerCase();
      const sevClass = sev === 'critical' ? 'sev-critical' : sev === 'error' ? 'sev-error' : sev === 'warning' ? 'sev-warning' : '';
      const sevRow = document.getElementById('afx-severity-row');
      sevRow.innerHTML = `<span class="afx-sev-tag autofix-dd-severity ${sevClass}">${sev}</span>
        <span class="afx-occ-label">${Number(item.occurrences || 0)} occurrences · ${formatTimestamp(item.last_seen)}</span>`;

      const actions = _buildAutofixActions(item);
      const list = document.getElementById('afx-actions-list');
      list.innerHTML = actions.map((a, ai) => {
        const fieldsHtml = a.fields.map(f => `
          <label style="font-size:.78rem;color:var(--muted);">${f.label}</label>
          <input class="afx-action-input" id="afx-field-${ai}-${f.key}" type="text" value="${f.default}" placeholder="${f.placeholder}" />
        `).join('');
        return `
          <div class="afx-action-card">
            <div class="afx-action-title">${a.title}</div>
            <div class="afx-action-desc">${a.desc}</div>
            ${a.fields.length ? `<div class="afx-action-row">${fieldsHtml}</div>` : ''}
            <div class="afx-action-row">
              <button class="afx-apply-btn" id="afx-apply-${ai}" type="button" onclick="applyAutofixAction(${ai})">Apply Fix</button>
            </div>
          </div>
        `;
      }).join('');

      const result = document.getElementById('afx-result');
      result.style.display = 'none';
      result.className = 'afx-result';
      result.textContent = '';

      modal.classList.add('open');
    }

    function closeAutofixModal() {
      const modal = document.getElementById('autofix-action-modal');
      if (modal) modal.classList.remove('open');
      _afxCurrentItem = null;
    }

    async function applyAutofixAction(actionIndex) {
      if (!_afxCurrentItem) return;
      const item = _afxCurrentItem;
      const actions = _buildAutofixActions(item);
      const action = actions[actionIndex];
      if (!action) return;

      const btn = document.getElementById(`afx-apply-${actionIndex}`);
      if (btn) { btn.disabled = true; btn.classList.add('loading'); }

      const params = {};
      action.fields.forEach(f => {
        const el = document.getElementById(`afx-field-${actionIndex}-${f.key}`);
        if (el) params[f.key] = el.value.trim();
      });

      const resultEl = document.getElementById('afx-result');
      resultEl.style.display = 'none';
      resultEl.className = 'afx-result';

      try {
        const body = {
          pod_name: item.pod_name,
          namespace: selectedNamespace,
          cluster_name: selectedCluster || null,
          app_name: selectedApp || null,
          action: action.action,
          params,
        };

        const res = await fetch(`${API}/api/mcp/autofix/apply`, {
          method: 'POST',
          headers: { ...authHeaders(), 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        });
        const data = await res.json();

        if (!res.ok) {
          throw new Error(data.detail || 'Fix failed');
        }

        resultEl.className = 'afx-result ok';
        resultEl.textContent = `✓ ${data.detail || 'Fix applied successfully.'}`;
        resultEl.style.display = 'block';

        // Record to history
        _recordAfxHistory(selectedNamespace, {
          pod: item.pod_name,
          severity: item.severity,
          issue: item.issue,
          occurrences: item.occurrences,
          action: action.action,
          params,
          namespace: selectedNamespace,
        });
        renderAutofixSeverityButtons();
        renderAutofixHistoryPanel();

        // Refresh the issues list after a short delay
        setTimeout(() => refreshAutofixDatadogIssues(), 3000);
      } catch (err) {
        resultEl.className = 'afx-result err';
        resultEl.textContent = `✗ ${err?.message || 'Failed to apply fix. Check cluster connectivity.'}`;
        resultEl.style.display = 'block';
      } finally {
        if (btn) { btn.disabled = false; btn.classList.remove('loading'); }
      }
    }

    // ── End Autofix Modal ───────────────────────────────────────────────────

    function renderAutofixDatadogUpdated(sourceLabel) {      const updated = document.getElementById('autofix-dd-updated');
      if (!updated) return;
      const when = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
      const autoText = autofixDatadogAutoSeconds > 0 ? `${autofixDatadogAutoSeconds}s` : 'off';
      updated.textContent = `${sourceLabel} | auto ${autoText} | ${when}`;
    }

    function renderAutofixSeverityButtons() {
      const buttons = document.querySelectorAll('.autofix-dd-sev-btn[data-severity]');
      const counts = _afxCountsByNamespace(getCurrentNamespace());
      buttons.forEach((btn) => {
        const severity = (btn.getAttribute('data-severity') || '').toLowerCase();
        const label = btn.getAttribute('data-label') || (severity.charAt(0).toUpperCase() + severity.slice(1));
        const n = counts[severity] !== undefined ? counts[severity] : 0;
        btn.textContent = n > 0 ? `${label} (${n})` : label;
      });
      syncAutofixFilterButtonState();
    }

    function setupAutofixAutoRefresh() {
      if (autofixDatadogAutoTimer) {
        clearInterval(autofixDatadogAutoTimer);
        autofixDatadogAutoTimer = null;
      }
      if (chatMode !== 'k8-autofix') return;
      if (!autofixDatadogAutoSeconds || autofixDatadogAutoSeconds <= 0) return;

      autofixDatadogAutoTimer = setInterval(() => {
        refreshAutofixDatadogIssues();
      }, autofixDatadogAutoSeconds * 1000);
    }

    function buildDatadogExampleIssues(namespace) {
      const ns = namespace || 'default';
      const now = Date.now();
      const iso = (minsAgo) => new Date(now - minsAgo * 60 * 1000).toISOString();
      return [
        {
          pod_name: `payments-api-6d8d7f7f9c-2v9tq`,
          severity: 'critical',
          issue: `OOMKilled detected in ${ns} - container restarted due to memory limit breach (exit code 137).`,
          last_seen: iso(4),
          occurrences: 7,
        },
        {
          pod_name: `payments-worker-57dcf87f6c-h2lmn`,
          severity: 'error',
          issue: `CrashLoopBackOff observed in ${ns} - startup probe failed repeatedly on /health endpoint.`,
          last_seen: iso(11),
          occurrences: 12,
        },
        {
          pod_name: `gateway-79c6f8f6b5-k9r2x`,
          severity: 'error',
          issue: `5xx spike from pod in ${ns} - upstream timeout to inventory service exceeded 5s threshold.`,
          last_seen: iso(19),
          occurrences: 21,
        },
        {
          pod_name: `inventory-api-6f44b8b4c7-pm8zz`,
          severity: 'warning',
          issue: `High restart count in ${ns} - intermittent DB connection reset errors detected.`,
          last_seen: iso(27),
          occurrences: 5,
        },
      ];
    }

    async function refreshAutofixDatadogIssues() {
      if (chatMode !== 'k8-autofix') return;

      const meta = document.getElementById('autofix-dd-meta');
      const list = document.getElementById('autofix-dd-list');
      if (!meta || !list) return;

      const ns = getCurrentNamespace();
      if (!ns) {
        meta.textContent = 'Select a namespace to view Datadog pod issues.';
        list.innerHTML = '<div class="autofix-dd-item">Namespace is not selected.</div>';
        clearAutofixPodDetails();
        renderAutofixDatadogUpdated(autofixDatadogSource === 'sample' ? 'sample' : 'live');
        return;
      }

      // Temporary behavior: always show sample issues for the selected namespace.
      // This keeps k8-autofix fully usable even when Datadog is not configured.
      autofixDatadogLastSource = 'sample';
      autofixDatadogCurrentIssues = buildDatadogExampleIssues(ns);
      meta.textContent = `${ns}${selectedCluster ? ` | ${selectedCluster}` : ''} | ${autofixDatadogRangeHours}h | sample issues: ${autofixDatadogCurrentIssues.length}`;
      renderAutofixDatadogList(autofixDatadogCurrentIssues);
      renderAutofixDatadogUpdated('sample');
      return;

      meta.textContent = `Loading ${autofixDatadogSource} issues for namespace ${selectedNamespace}${selectedCluster ? ` on ${selectedCluster}` : ''}...`;
      list.innerHTML = '<div class="autofix-dd-item">Fetching Datadog data...</div>';

      if (autofixDatadogSource === 'sample') {
        autofixDatadogLastSource = 'sample';
        autofixDatadogCurrentIssues = buildDatadogExampleIssues(selectedNamespace);
        meta.textContent = `${selectedNamespace}${selectedCluster ? ` | ${selectedCluster}` : ''} | ${autofixDatadogRangeHours}h | sample issues: ${autofixDatadogCurrentIssues.length}`;
        renderAutofixDatadogList(autofixDatadogCurrentIssues);
        renderAutofixDatadogUpdated('sample');
        return;
      }

      try {
        const params = new URLSearchParams({
          namespace: selectedNamespace,
          range_hours: String(autofixDatadogRangeHours || 6),
          limit: '150',
        });
        if (selectedCluster) params.set('cluster_name', selectedCluster);

        const res = await fetch(`${API}/api/mcp/datadog/issues?${params.toString()}`, {
          headers: authHeaders(),
        });
        const data = await res.json();

        if (!res.ok) {
          throw new Error(data.detail || 'Failed to fetch Datadog issues');
        }

        if (data.configured === false) {
          autofixDatadogLastSource = 'sample';
          autofixDatadogCurrentIssues = buildDatadogExampleIssues(selectedNamespace);
          meta.textContent = 'Datadog not configured. Using sample issues.';
          renderAutofixDatadogList(autofixDatadogCurrentIssues);
          renderAutofixDatadogUpdated('sample');
          return;
        }

        const count = Number(data.total_hits || 0);
        const issueCount = Array.isArray(data.issues) ? data.issues.length : 0;
        meta.textContent = `${selectedNamespace}${selectedCluster ? ` | ${selectedCluster}` : ''} | ${autofixDatadogRangeHours}h | raw logs: ${count} | issue groups: ${issueCount}`;
        autofixDatadogLastSource = 'live';
        autofixDatadogCurrentIssues = Array.isArray(data.issues) ? data.issues : [];
        renderAutofixDatadogList(autofixDatadogCurrentIssues);
        renderAutofixDatadogUpdated('live');
      } catch (error) {
        const msg = String(error && error.message ? error.message : error || 'Unable to load Datadog issues.');
        if (/not configured/i.test(msg)) {
          autofixDatadogLastSource = 'sample';
          autofixDatadogCurrentIssues = buildDatadogExampleIssues(selectedNamespace);
          meta.textContent = 'Datadog not configured. Using sample issues.';
          renderAutofixDatadogList(autofixDatadogCurrentIssues);
          renderAutofixDatadogUpdated('sample');
          return;
        }
        meta.textContent = msg;
        list.innerHTML = `<div class="autofix-dd-item">${msg}</div>`;
        renderAutofixDatadogUpdated(autofixDatadogLastSource || 'live');
      }
    }

    function renderApps() {
      const list = document.getElementById('app-list');
      const apps = currentUser.allowed_apps.includes('*')
        ? ['all apps (admin)'] : currentUser.allowed_apps;
      list.innerHTML = apps.map(a => {
        const active = selectedApp === a ? ' active' : '';
        const appEsc = (a || '').replace(/'/g, "\\'");
        return `<div class="app-chip${active}" onclick="selectApp('${appEsc}')">
      <span class="dot-s"></span>${a}
    </div>`;
      }).join('');
    }

    async function selectApp(appName) {
      if (appName === 'all apps (admin)') {
        selectedApp = null;
        localStorage.removeItem('k8sai_selected_app');
        selectedCluster = null;
        localStorage.removeItem('k8sai_selected_cluster');
        await loadClusters();
        renderApps();
        await loadNamespaces();
        await loadSuggestions();
        if (chatMode === 'k8-autofix') {
          selectedNamespace = null;
          renderNamespaces();
          refreshAutofixDatadogIssues();
          renderAutofixSeverityButtons();
          renderAutofixHistoryPanel();
        }
        return;
      }

      if (selectedApp === appName) {
        selectedApp = null;
        localStorage.removeItem('k8sai_selected_app');
        selectedCluster = null;
        localStorage.removeItem('k8sai_selected_cluster');
        await loadClusters();
        renderApps();
        await loadNamespaces();
        await loadSuggestions();
        if (chatMode === 'k8-autofix') {
          selectedNamespace = null;
          renderNamespaces();
          refreshAutofixDatadogIssues();
          renderAutofixSeverityButtons();
          renderAutofixHistoryPanel();
        }
        return;
      }

      selectedApp = appName;
      localStorage.setItem('k8sai_selected_app', appName);

      // Keep cluster highlight in sync when app has a mapped cluster.
      const mapped = cachedClusterEntries.find(e => e.app_name === appName);
      if (mapped?.cluster_name) {
        selectedCluster = mapped.cluster_name;
        localStorage.setItem('k8sai_selected_cluster', selectedCluster);
        await loadClusters();
      }

      renderApps();
      if (chatMode === 'k8-autofix') {
        await loadNamespaces();
        selectedNamespace = null;
        renderNamespaces();
        const nsBadge = document.getElementById('namespace-badge');
        if (nsBadge) {
          const appText = selectedApp ? ` | App: ${selectedApp}` : '';
          const clusterText = selectedCluster ? ` | Cluster: ${selectedCluster}` : '';
          nsBadge.textContent = `Active Namespace: not selected${appText}${clusterText}`;
        }
        refreshAutofixDatadogIssues();
        renderAutofixSeverityButtons();
        renderAutofixHistoryPanel();
        return;
      }
      fillQuery(`list namespaces for ${appName}`);
      await sendQuery();
    }

    async function loadClusters() {
      try {
        const res = await fetch(`${API}/api/registry/clusters`, { headers: authHeaders() });
        const entries = await res.json();
        cachedClusterEntries = Array.isArray(entries) ? entries : [];
        const byCluster = new Map();
        const filteredEntries = selectedApp
          ? cachedClusterEntries.filter(entry => entry.app_name === selectedApp)
          : cachedClusterEntries;
        for (const entry of filteredEntries) {
          if (!byCluster.has(entry.cluster_name)) byCluster.set(entry.cluster_name, entry);
        }
        const clusterRows = Array.from(byCluster.values()).slice(0, 20);

        // Reset invalid persisted cluster selection.
        if (selectedCluster && !byCluster.has(selectedCluster)) {
          selectedCluster = null;
          localStorage.removeItem('k8sai_selected_cluster');
        }

        const list = document.getElementById('cluster-list');
        list.innerHTML = clusterRows.map(e => {
          const active = selectedCluster === e.cluster_name ? ' active' : '';
          const clusterNameEsc = (e.cluster_name || '').replace(/'/g, "\\'");
          return `<div class="cluster-entry${active}" onclick="selectCluster('${clusterNameEsc}')">
        <div class="cluster-top-row">
          <div class="cluster-name">${e.cluster_name} <span class="env-tag env-${e.environment === 'prod' ? 'prod' : 'nonprod'}">${e.environment}</span></div>
          ${selectedCluster === e.cluster_name ? '<span class="cluster-check" aria-hidden="true"></span>' : ''}
        </div>
        <div class="cluster-meta">${e.cloud_provider} | ${e.region}</div>
      </div>`;
        }).join('');
      } catch { /* silently skip if registry empty */ }
    }

    async function selectCluster(clusterName) {
      // Toggle off if user clicks the same selected cluster.
      if (selectedCluster === clusterName) {
        selectedCluster = null;
        localStorage.removeItem('k8sai_selected_cluster');
        selectedApp = null;
        localStorage.removeItem('k8sai_selected_app');
        await loadClusters();
        renderApps();
        await loadNamespaces();
        await loadSuggestions();
        if (chatMode === 'k8-autofix') {
          selectedNamespace = null;
          renderNamespaces();
          refreshAutofixDatadogIssues();
          renderAutofixSeverityButtons();
          renderAutofixHistoryPanel();
        }
        return;
      }

      selectedCluster = clusterName;
      localStorage.setItem('k8sai_selected_cluster', clusterName);
      await loadClusters();
      await loadNamespaces();
      await loadSuggestions();

      // Open cluster-specific details by querying for the first app mapped to this cluster.
      const mapped = cachedClusterEntries.find(e => e.cluster_name === clusterName);
      if (chatMode === 'k8-autofix') {
        if (mapped?.app_name) {
          selectedApp = mapped.app_name;
          localStorage.setItem('k8sai_selected_app', selectedApp);
          renderApps();
        }
        selectedNamespace = null;
        renderNamespaces();
        const nsBadge = document.getElementById('namespace-badge');
        if (nsBadge) {
          const appText = selectedApp ? ` | App: ${selectedApp}` : '';
          const clusterText = selectedCluster ? ` | Cluster: ${selectedCluster}` : '';
          nsBadge.textContent = `Active Namespace: not selected${appText}${clusterText}`;
        }
        refreshAutofixDatadogIssues();
        renderAutofixSeverityButtons();
        renderAutofixHistoryPanel();
        return;
      }
      if (mapped?.app_name) {
        selectedApp = mapped.app_name;
        localStorage.setItem('k8sai_selected_app', selectedApp);
        renderApps();
        fillQuery(`list namespaces for ${mapped.app_name}`);
        await sendQuery();
      }
    }

    async function loadSuggestions() {
      try {
        const params = [];
        if (chatSessionId) params.push(`session_id=${encodeURIComponent(chatSessionId)}`);
        params.push(`chat_mode=${encodeURIComponent(chatMode || 'k8-info')}`);
        const qp = params.length ? `?${params.join('&')}` : '';
        const res = await fetch(`${API}/api/chat/suggestions${qp}`, { headers: authHeaders() });
        const data = await res.json();
        const nsBadge = document.getElementById('namespace-badge');
        const nsText = data.selected_namespace || 'not selected';
        const appText = data.selected_app ? ` | App: ${data.selected_app}` : '';
        const clusterText = selectedCluster ? ` | Cluster: ${selectedCluster}` : '';
        if (nsBadge) nsBadge.textContent = `Active Namespace: ${nsText}${appText}${clusterText}`;

        if (data.selected_app) {
          selectedApp = data.selected_app;
          localStorage.setItem('k8sai_selected_app', selectedApp);
          renderApps();
        }
        const row = document.getElementById('suggestions');
        const source = Array.isArray(data.suggestions) ? data.suggestions : [];
        const menuCmd = 'main menu';
        const withoutMenu = source.filter(s => (s || '').toLowerCase() !== menuCmd);
        const visible = [menuCmd, ...withoutMenu].slice(0, 6);
        row.innerHTML = visible.map(s => {
          const lower = (s || '').toLowerCase();
          const cls = lower === menuCmd ? 'sug-btn sug-main' : 'sug-btn';
          return `<button class="${cls}" onclick="fillQuery('${s}')">${s}</button>`;
        }).join('');
      } catch { }
    }

    async function openGeneralQuestion() {
      const input = document.getElementById('chat-input');
      if (!input) return;
      const current = (input.value || '').trim();
      if (!current) {
        input.value = 'general: ';
      } else if (!/^\s*(general|llm)\s*:/i.test(current)) {
        input.value = `general: ${current}`;
      }
      input.focus();
      input.setSelectionRange(input.value.length, input.value.length);
      addMsg('ai', 'General question mode enabled. Type your question and press Send.');
    }

    // ── Upgrade K8s Modal Functions ────
    let selectedUpgradeVersion = null;

    async function openUpgradeModal() {
      if (!selectedCluster) {
        // Recover from stale UI state by auto-selecting first available cluster.
        if (!cachedClusterEntries.length) {
          await loadClusters();
        }
        const fallbackCluster = cachedClusterEntries[0]?.cluster_name || null;
        if (fallbackCluster) {
          selectedCluster = fallbackCluster;
          localStorage.setItem('k8sai_selected_cluster', selectedCluster);
          await loadClusters();
          await loadNamespaces();
          await loadSuggestions();
        } else {
          alert('Please select a cluster first');
          return;
        }
      }
      
      selectedUpgradeVersion = null;
      document.getElementById('upgrade-modal').style.display = 'flex';
      document.getElementById('upgrade-cluster-name').textContent = selectedCluster;
      
      // Load current version and available versions
      await loadUpgradeVersions();
    }

    function closeUpgradeModal() {
      document.getElementById('upgrade-modal').style.display = 'none';
      selectedUpgradeVersion = null;
      document.getElementById('selected-upgrade-version').textContent = 'No version selected';
      document.getElementById('upgrade-action-btn').disabled = true;
      document.getElementById('upgrade-action-btn').textContent = 'Select Version';
    }

    async function loadUpgradeVersions() {
      try {
        const response = await fetch(`${API}/api/k8s/upgrade/${encodeURIComponent(selectedCluster)}/versions`, {
          headers: authHeaders(),
        });
        
        if (!response.ok) throw new Error('Failed to fetch versions');
        
        const data = await response.json();
        const currentVersion = data.current_version || 'unknown';
        const availableVersions = data.available_versions || [];
        
        // Display current version in header
        const currentVersionDiv = document.getElementById('current-version');
        if (currentVersion === 'unknown') {
          currentVersionDiv.innerHTML = `
            <div style="display:flex; align-items:center; gap:8px;">
              <span style="color:var(--muted); font-size:0.85rem;">Current:</span>
              <strong style="font-size:1.1rem; color:var(--danger);">Unavailable</strong>
            </div>
          `;
          document.getElementById('available-versions').innerHTML =
            '<div style="color:var(--muted); font-size:0.9rem; text-align:center; padding:20px;">Unable to detect current cluster version. Please try again.</div>';
          return;
        }

        currentVersionDiv.innerHTML = `
          <div style="display:flex; align-items:center; gap:8px;">
            <span style="color:var(--muted); font-size:0.85rem;">Current:</span>
            <strong style="font-size:1.1rem;">${currentVersion}</strong>
          </div>
        `;
        
        // Display available upgrade versions
        const versionsContainer = document.getElementById('available-versions');
        if (availableVersions.length === 0) {
          versionsContainer.innerHTML = '<div style="color:var(--muted); font-size:0.9rem; text-align:center; padding:20px;">✓ Already on latest version</div>';
        } else {
          versionsContainer.innerHTML = `
            <div style="color:var(--muted); font-size:0.85rem; margin-bottom:12px; padding:0 4px;">Available upgrades:</div>
            ${availableVersions.map(v => `
              <button class="version-btn" onclick="selectUpgradeVersion('${v}', this)">
                <strong style="font-size:1rem;">v${v}</strong>
                <span style="display:block; color:var(--muted); font-size:0.8rem; margin-top:2px;">Newer than ${currentVersion}</span>
              </button>
            `).join('')}
          `;
        }
      } catch (error) {
        console.error('Error loading upgrade versions:', error);
        document.getElementById('available-versions').innerHTML = '<div style="color:var(--danger); font-size:0.9rem;">Failed to load versions</div>';
      }
    }

    function selectUpgradeVersion(version, element) {
      // Deselect previous
      document.querySelectorAll('.version-btn.selected').forEach(btn => btn.classList.remove('selected'));
      
      // Select new
      selectedUpgradeVersion = version;
      element.classList.add('selected');
      document.getElementById('selected-upgrade-version').textContent = `v${version}`;
      
      // Enable upgrade button
      const upgradeBtn = document.getElementById('upgrade-action-btn');
      upgradeBtn.disabled = false;
      upgradeBtn.textContent = `Upgrade to v${version}`;
    }

    async function confirmUpgrade() {
      if (!selectedUpgradeVersion || !selectedCluster) {
        alert('Please select a version');
        return;
      }
      
      const confirmed = confirm(`Are you sure you want to upgrade cluster "${selectedCluster}" to version v${selectedUpgradeVersion}?\n\nThis operation may cause brief downtime.`);
      if (!confirmed) return;
      
      try {
        document.getElementById('upgrade-action-btn').disabled = true;
        document.getElementById('upgrade-action-btn').textContent = 'Upgrading...';
        
        const response = await fetch(`${API}/api/k8s/upgrade/${encodeURIComponent(selectedCluster)}`, {
          method: 'POST',
          headers: authHeaders(),
          body: JSON.stringify({
            target_version: selectedUpgradeVersion,
          }),
        });
        
        if (!response.ok) {
          const error = await response.json();
          throw new Error(error.detail || 'Upgrade failed');
        }
        
        const result = await response.json();
        alert(`Upgrade initiated successfully!\n\nStatus: ${result.status}\n\nCheck cluster status for progress.`);
        
        // Log audit
        auditLog('UPGRADE_CLUSTER', 'cluster', selectedCluster, {
          target_version: selectedUpgradeVersion,
          result_summary: `Cluster upgrade to v${selectedUpgradeVersion} initiated`,
          success: true
        });
        
        closeUpgradeModal();
      } catch (error) {
        console.error('Upgrade error:', error);
        alert(`Upgrade failed: ${error.message}`);
        
        auditLog('UPGRADE_CLUSTER', 'cluster', selectedCluster, {
          target_version: selectedUpgradeVersion,
          result_summary: `Cluster upgrade failed: ${error.message}`,
          success: false
        });
        
        document.getElementById('upgrade-action-btn').disabled = false;
        document.getElementById('upgrade-action-btn').textContent = `Upgrade to v${selectedUpgradeVersion}`;
      }
    }

    async function loadNamespaces() {
      try {
        const params = [];
        if (chatSessionId) params.push(`session_id=${encodeURIComponent(chatSessionId)}`);
        if (selectedCluster) params.push(`cluster_name=${encodeURIComponent(selectedCluster)}`);
        const qp = params.length ? `?${params.join('&')}` : '';
        const res = await fetch(`${API}/api/chat/namespaces${qp}`, { headers: authHeaders() });
        const data = await res.json();
        cachedNamespaces = data.namespaces || [];
        const desiredNs = autofixSelectedNamespace || selectedNamespace || data.selected_namespace || null;
        selectedNamespace = (desiredNs && cachedNamespaces.includes(desiredNs)) ? desiredNs : null;
        autofixSelectedNamespace = selectedNamespace;
        renderNamespaces();
        refreshAutofixDatadogIssues();
      } catch { }
    }

    function renderNamespaces() {
      const list = document.getElementById('namespace-list');
      const toggleBtn = document.getElementById('ns-toggle-btn');
      const currentNs = getCurrentNamespace();
      const filter = namespaceFilter.trim().toLowerCase();
      const filteredNamespaces = filter
        ? cachedNamespaces.filter(ns => ns.toLowerCase().includes(filter))
        : [...cachedNamespaces];
      const namespaces = namespacesExpanded
        ? filteredNamespaces
        : (currentNs && filteredNamespaces.includes(currentNs)
          ? [currentNs]
          : filteredNamespaces.slice(0, 1));

      if (toggleBtn) toggleBtn.textContent = namespacesExpanded ? '-' : '+';

      if (!namespaces.length) {
        list.innerHTML = '<div class="namespace-empty">No matching namespaces</div>';
        return;
      }

      list.innerHTML = namespaces.map(ns =>
        `<div class="app-chip namespace-chip ${currentNs === ns ? 'active' : ''}" onclick="selectNamespace('${ns}')"><span class="dot-s"></span>${ns}${currentNs === ns ? '<span class="namespace-check" aria-label="selected">✓</span>' : ''}</div>`
      ).join('');
    }

    function onNamespaceSearch(value) {
      namespaceFilter = value || '';
      renderNamespaces();
    }

    function toggleNamespaces() {
      namespacesExpanded = !namespacesExpanded;
      localStorage.setItem('k8sai_namespaces_expanded', String(namespacesExpanded));
      renderNamespaces();
    }

    async function clearNamespaceSelection() {
      if (chatSessionId) {
        try {
          await fetch(`${API}/api/chat/namespace/clear?session_id=${encodeURIComponent(chatSessionId)}`, {
            method: 'POST',
            headers: authHeaders(),
          });
        } catch { }
      }
      selectedNamespace = null;
      autofixSelectedNamespace = null;
        localStorage.removeItem('k8sai_selected_namespace');
      autofixPodSearchQuery = '';
      const podInput = document.getElementById('autofix-dd-pod-search');
      if (podInput) podInput.value = '';
      clearAutofixPodDetails();
      await Promise.all([loadSuggestions(), loadNamespaces()]);

      const nsBadge = document.getElementById('namespace-badge');
      if (nsBadge) {
        const appText = selectedApp ? ` | App: ${selectedApp}` : '';
        const clusterText = selectedCluster ? ` | Cluster: ${selectedCluster}` : '';
        nsBadge.textContent = `Active Namespace: not selected${appText}${clusterText}`;
      }
      refreshAutofixDatadogIssues();
    }

    async function selectNamespace(ns) {
      if (getCurrentNamespace() === ns) {
        await clearNamespaceSelection();
        auditLog('READ', 'namespace', ns, {
          result_summary: 'Namespace deselected',
          extra: { action: 'deselect' }
        });
        return;
      }
      auditLog('READ', 'namespace', ns, {
        result_summary: 'Namespace selected',
        extra: { action: 'select' }
      });

      selectedNamespace = ns;
      autofixSelectedNamespace = ns;
        localStorage.setItem('k8sai_selected_namespace', ns);
      renderNamespaces();
      const nsBadge = document.getElementById('namespace-badge');
      if (nsBadge) {
        const appText = selectedApp ? ` | App: ${selectedApp}` : '';
        const clusterText = selectedCluster ? ` | Cluster: ${selectedCluster}` : '';
        nsBadge.textContent = `Active Namespace: ${ns}${appText}${clusterText}`;
      }

      if (chatMode === 'k8-autofix') {
        autofixPodSearchQuery = '';
        const podInput = document.getElementById('autofix-dd-pod-search');
        if (podInput) podInput.value = '';
        clearAutofixPodDetails();
        refreshAutofixDatadogIssues();
        renderAutofixSeverityButtons();
        renderAutofixHistoryPanel();
        
        // Sync namespace to backend session so k8-info/k8-agent queries will have context
        syncNamespaceToBackendSession(ns);
        return;
      }

      fillQuery(buildNamespaceSelectionQuery(ns));
      sendQuery();
    }
    function extractPodNamesFromText(text) {
      const names = [];
      const seen = new Set();
      const lines = (text || '').split('\n');
      let inSection = false;
      for (const rawLine of lines) {
        const line = rawLine.trim();
        if (/^Pods\s+[-|:]/i.test(line)) { inSection = true; continue; }
        if (inSection && /^\w[\w\s&-]*\s+[-|:]/.test(line) && !/^Pods\s+[-|:]/i.test(line)) { inSection = false; }
        if (!inSection) continue;
        const m = line.match(/^-\s+(?:\[WARN\]\s+)?([a-z0-9][a-z0-9._-]*)/i);
        if (!m) continue;
        const name = m[1];
        if (!seen.has(name)) {
          seen.add(name);
          names.push(name);
        }
      }
      return names;
    }

    function selectPodFromChip(podName) {
      fillQuery(`select pod ${podName}`);
      sendQuery();
    }

    function selectResourceFromChip(kind, name) {
      fillQuery(`describe ${kind} ${name}`);
      sendQuery();
    }

    function extractDeploymentNamesFromText(text) {
      const names = [];
      const seen = new Set();
      const lines = (text || '').split('\n');
      let inSection = false;
      for (const rawLine of lines) {
        const line = rawLine.trim();
        if (/^Deployments\s+[-|:]/i.test(line)) { inSection = true; continue; }
        if (inSection && /^\w[\w\s&-]*\s+[-|:]/.test(line) && !/^Deployments\s+[-|:]/i.test(line)) { inSection = false; }
        if (!inSection) continue;
        if (/^(Age|Pods|Try|or|Ready|Image|Strategy|WARN)\s*/i.test(line)) continue;
        const m = line.match(/^-\s+(?:\[WARN\]\s+)?([a-z0-9][a-z0-9._-]*)/i);
        if (m) {
          const name = m[1];
          if (!seen.has(name)) { seen.add(name); names.push(name); }
        }
      }
      return names;
    }

    function extractSimpleNamesFromSection(text, sectionRegex) {
      const names = [];
      const seen = new Set();
      const lines = (text || '').split('\n');
      let inSection = false;
      for (const rawLine of lines) {
        const line = rawLine.trim();
        if (sectionRegex.test(line)) { inSection = true; continue; }
        if (inSection && /^\w[\w\s&-]*\s+[-|:]/.test(line) && !sectionRegex.test(line)) { inSection = false; }
        if (!inSection) continue;
        if (/^(Try|or|Keys|Key|Age|Type|External)\s*/i.test(line)) continue;
        const m = line.match(/^-\s+([a-z0-9][a-z0-9._-]*)/i);
        if (m) {
          const name = m[1];
          if (!seen.has(name)) { seen.add(name); names.push(name); }
        }
      }
      return names;
    }

    function extractQuotaNamesFromText(text) {
      const names = [];
      const seen = new Set();
      const lines = (text || '').split('\n');
      let inSection = false;
      for (const rawLine of lines) {
        const line = rawLine.trim();
        if (/^Resource\s+Quota\s+[-|:]/i.test(line)) { inSection = true; continue; }
        if (inSection && /^\w[\w\s&-]*\s+[-|:]/.test(line) && !/^Resource\s+Quota\s+[-|:]/i.test(line)) { inSection = false; }
        if (!inSection) continue;
        const m = line.match(/^Name\s*:\s*([a-z0-9][a-z0-9._-]*)/i);
        if (m) {
          const name = m[1];
          if (!seen.has(name)) { seen.add(name); names.push(name); }
        }
      }
      return names;
    }

    function extractIngressNamesFromText(text) {
      const names = [];
      const seen = new Set();
      const lines = (text || '').split('\n');
      let inSection = false;
      for (const rawLine of lines) {
        const line = rawLine.trim();
        if (/^Ingress\s*&\s*Network\s+[-|:]/i.test(line)) { inSection = true; continue; }
        if (inSection && /^\w[\w\s&-]*\s+[-|:]/.test(line) && !/^Ingress\s*&\s*Network\s+[-|:]/i.test(line)) { inSection = false; }
        if (!inSection) continue;
        if (/^(Try|or|Host|TLS|Address)\s*/i.test(line)) continue;
        const m = line.match(/^-\s+([a-z0-9][a-z0-9._-]*)/i);
        if (m) {
          const name = m[1];
          if (!seen.has(name)) { seen.add(name); names.push(name); }
        }
      }
      return names;
    }

    // ----------------------------------------
    function addMsg(role, text) {
      const messages = document.getElementById('messages');
      const div = document.createElement('div');
      div.className = `msg msg-${role === 'user' ? 'user' : 'ai'}`;
      const time = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
      let pickerHtml = '';
      if (role !== 'user' && text.includes('Namespaces you can use:')) {
        const namespaces = text
          .split('\n')
          .map(l => l.trim())
          .filter(l => l.startsWith('- '))
          .map(l => l.slice(2).trim())
          .filter(Boolean);
        if (namespaces.length) {
          pickerHtml = `
            <div style="margin-top:10px; display:flex; gap:8px; flex-wrap:wrap;">
              ${namespaces.map(ns => `<button class="sug-btn" onclick="fillQuery('${ns}')">${ns}</button>`).join('')}
            </div>`;
        }
      }
      if (role !== 'user' && /^Pods\s+[-|:]/im.test(text)) {
        const pods = extractPodNamesFromText(text);
        if (pods.length) {
          pickerHtml += `
            <div style="margin-top:10px; display:flex; gap:8px; flex-wrap:wrap;">
              ${pods.slice(0, 20).map(p => `<button class="sug-btn" onclick="selectPodFromChip('${p}')">${p}</button>`).join('')}
            </div>`;
        }
      }
      if (role !== 'user' && /Deployments\s+[-|:]/i.test(text)) {
        const names = extractDeploymentNamesFromText(text);
        if (names.length) {
          const describeChips = names.slice(0, 40).map(n => `<button class="sug-btn" onclick="selectResourceFromChip('deployment','${n}')">describe ${n}</button>`).join('');
          const editChips = (chatMode === 'k8-agent' || chatMode === 'k8-autofix')
            ? names.slice(0, 40).map(n => `<button class="sug-btn" onclick="fillQuery('edit deployment ${n}')">edit ${n}</button>`).join('')
            : '';
          pickerHtml += `
            <div style="margin-top:10px; display:flex; gap:8px; flex-wrap:wrap;">
              ${describeChips}${editChips}
            </div>`;
        }
      }
      if (role !== 'user' && /Services\s+[-|:]/i.test(text)) {
        const names = extractSimpleNamesFromSection(text, /^Services\s+[-|:]/i);
        if (names.length) {
          const describeChips = names.slice(0, 40).map(n => `<button class="sug-btn" onclick="selectResourceFromChip('service','${n}')">describe ${n}</button>`).join('');
          const editChips = (chatMode === 'k8-agent' || chatMode === 'k8-autofix')
            ? names.slice(0, 40).map(n => `<button class="sug-btn" onclick="fillQuery('edit service ${n}')">edit ${n}</button>`).join('')
            : '';
          pickerHtml += `
            <div style="margin-top:10px; display:flex; gap:8px; flex-wrap:wrap;">
              ${describeChips}${editChips}
            </div>`;
        }
      }
      if (role !== 'user' && /Secrets\s+\(metadata only\)\s+[-|:]/i.test(text)) {
        const names = extractSimpleNamesFromSection(text, /^Secrets\s+\(metadata only\)\s+[-|:]/i);
        if (names.length) {
          const describeChips = names.slice(0, 40).map(n => `<button class="sug-btn" onclick="selectResourceFromChip('secret','${n}')">describe ${n}</button>`).join('');
          const editChips = (chatMode === 'k8-agent' || chatMode === 'k8-autofix')
            ? names.slice(0, 40).map(n => `<button class="sug-btn" onclick="fillQuery('edit secret ${n}')">edit ${n}</button>`).join('')
            : '';
          pickerHtml += `
            <div style="margin-top:10px; display:flex; gap:8px; flex-wrap:wrap;">
              ${describeChips}${editChips}
            </div>`;
        }
      }
      if (role !== 'user' && /Ingress\s*&\s*Network\s+[-|:]/i.test(text)) {
        const names = extractIngressNamesFromText(text);
        if (names.length) {
          const describeChips = names.slice(0, 40).map(n => `<button class="sug-btn" onclick="selectResourceFromChip('ingress','${n}')">describe ${n}</button>`).join('');
          const editChips = (chatMode === 'k8-agent' || chatMode === 'k8-autofix')
            ? names.slice(0, 40).map(n => `<button class="sug-btn" onclick="fillQuery('edit ingress ${n}')">edit ${n}</button>`).join('')
            : '';
          pickerHtml += `
            <div style="margin-top:10px; display:flex; gap:8px; flex-wrap:wrap;">
              ${describeChips}${editChips}
            </div>`;
        }
      }
      if (role !== 'user' && /Resource\s+Quota\s+[-|:]/i.test(text) && (chatMode === 'k8-agent' || chatMode === 'k8-autofix')) {
        const names = extractQuotaNamesFromText(text);
        if (names.length) {
          pickerHtml += `
            <div style="margin-top:10px; display:flex; gap:8px; flex-wrap:wrap;">
              ${names.slice(0, 10).map(n => `<button class="sug-btn" onclick="fillQuery('edit resourcequota ${n}')">edit ${n}</button>`).join('')}
            </div>`;
        }
      }
      div.innerHTML = `
    <div class="bubble bubble-${role === 'user' ? 'user' : 'ai'}">${text}</div>
    ${pickerHtml}
    <div class="msg-meta">${role === 'user' ? currentUser.username : 'UNIPLANE AI'} | ${time}</div>
  `;
      messages.appendChild(div);
      messages.scrollTop = messages.scrollHeight;
    }

    function showThinking() {
      const messages = document.getElementById('messages');
      const div = document.createElement('div');
      div.id = 'thinking-indicator';
      div.className = 'msg msg-ai';
      div.innerHTML = '<div class="thinking"><span class="td"></span><span class="td"></span><span class="td"></span></div>';
      messages.appendChild(div);
      messages.scrollTop = messages.scrollHeight;
    }

    function removeThinking() {
      const el = document.getElementById('thinking-indicator');
      if (el) el.remove();
    }

    // ----------------------------------------

    function openDeploymentEditor(deploymentName, yamlContent, namespace, appName, resourceKind = 'Deployment') {
      deploymentEditorState = {
        deploymentName,
        resourceKind,
        namespace: namespace || selectedNamespace || 'default',
        appName: appName || '',
        originalYaml: yamlContent,
        editedYaml: yamlContent
      };
      document.getElementById('deployment-yaml').value = yamlContent;
      document.getElementById('editor-title').textContent = `Edit ${resourceKind}: ${deploymentName}`;
      document.getElementById('editor-modal').style.display = 'flex';
    }

    function closeDeploymentEditor() {
      document.getElementById('editor-modal').style.display = 'none';
      deploymentEditorState = { deploymentName: '', resourceKind: 'Deployment', namespace: '', appName: '', originalYaml: '', editedYaml: '' };
    }

    function saveDeploymentClick() {
      const edited = document.getElementById('deployment-yaml').value.trim();
      if (!edited) {
        alert('YAML content cannot be empty');
        return;
      }
      if (edited === deploymentEditorState.originalYaml) {
        alert('No changes were made');
        return;
      }
      deploymentEditorState.editedYaml = edited;
      showConfirmSave(`Save changes to deployment "${deploymentEditorState.deploymentName}"?`, 'executeDeploymentSave');
    }

    function saveAndExitDeployment() {
      saveDeploymentClick();
    }

    function showConfirmSave(message, actionFunc) {
      document.getElementById('confirm-title').textContent = 'Confirm Save';
      document.getElementById('confirm-message').textContent = `${message} (Step 1 of 2)`;
      const confirmBtn = document.getElementById('confirm-btn');
      confirmBtn.textContent = 'Confirm (1/2)';
      modalConfirmStep = 1;
      confirmBtn.onclick = () => handleModalConfirm(actionFunc, message);
      document.getElementById('confirm-modal').style.display = 'flex';
    }

    function handleModalConfirm(actionFunc, baseMessage) {
      const confirmBtn = document.getElementById('confirm-btn');
      if (modalConfirmStep === 1) {
        modalConfirmStep = 2;
        document.getElementById('confirm-title').textContent = 'Final Confirmation';
        document.getElementById('confirm-message').textContent = `${baseMessage} (Step 2 of 2)`;
        confirmBtn.textContent = 'Confirm (2/2)';
        return;
      }
      if (typeof actionFunc === 'string' && typeof window[actionFunc] === 'function') {
        window[actionFunc]();
      }
    }

    function closeConfirmModal() {
      document.getElementById('confirm-modal').style.display = 'none';
      modalConfirmStep = 0;
      const confirmBtn = document.getElementById('confirm-btn');
      if (confirmBtn) confirmBtn.textContent = 'Confirm Save';
    }

    async function executeDeploymentSave() {
      const { deploymentName, resourceKind, namespace, appName, editedYaml } = deploymentEditorState;

      if (!deploymentName || !namespace) {
        alert('Missing deployment context (name or namespace)');
        closeConfirmModal();
        return;
      }

      closeConfirmModal();
      showThinking();

      try {
        const payload = {
          session_id: chatSessionId,
          deployment_name: deploymentName,
          resource_name: deploymentName,
          resource_kind: resourceKind,
          yaml_content: editedYaml,
          namespace: namespace,
          app_name: appName || null
        };

        const res = await fetch(`${API}/api/chat/save-deployment`, {
          method: 'POST',
          headers: authHeaders(),
          body: JSON.stringify(payload)
        });

        removeThinking();
        const data = await res.json();

        if (res.ok && data.status === 'success') {
          const replicasInfo = (data.applied_replicas !== null && data.applied_replicas !== undefined)
            ? `\nDesired replicas: ${data.applied_replicas} | Ready: ${data.ready_replicas ?? 0}`
            : '';
          const imageInfo = data.applied_image ? `\nImage: ${data.applied_image}` : '';
          addMsg('ai', `[OK] ${resourceKind} "${deploymentName}" saved successfully!${replicasInfo}${imageInfo}\n\nChanges have been applied to the cluster. The controller may take a moment to reconcile.`);
          auditLog('MUTATE', resourceKind.toLowerCase(), deploymentName, {
            app_name: appName || null,
            namespace: namespace,
            result_summary: `${resourceKind} updated`,
            success: true,
            extra: { applied_replicas: data.applied_replicas, applied_image: data.applied_image }
          });
          closeDeploymentEditor();
          await loadSuggestions();
        } else if (res.status === 403) {
          addMsg('ai', `403: ${data.detail || 'You do not have permission to save this deployment.'}`);
          auditLog('MUTATE', resourceKind.toLowerCase(), deploymentName, {
            app_name: appName || null,
            namespace: namespace,
            result_summary: 'Access denied',
            success: false
          });
        } else {
          addMsg('ai', `Error: ${data.detail || 'Failed to save resource'}`);
          auditLog('MUTATE', resourceKind.toLowerCase(), deploymentName, {
            app_name: appName || null,
            namespace: namespace,
            result_summary: `Failed: ${data.detail || 'unknown error'}`,
            success: false
          });
        }
      } catch (e) {
        removeThinking();
        addMsg('ai', `Error: ${e.message}`);
      }
    }

    async function syncNamespaceToBackendSession(ns) {
      // Silently sync namespace to backend session by sending a namespace-select query
      // This allows the backend to store the namespace in SESSION_CONTEXT
      // so subsequent queries in k8-info/k8-agent will have namespace context
      try {
        const query = buildNamespaceSelectionQuery(ns);
        const res = await fetch(`${API}/api/chat/query`, {
          method: 'POST',
          headers: authHeaders(),
          body: JSON.stringify({ query, session_id: chatSessionId, chat_mode: chatMode }),
        });
        if (res.ok) {
          const data = await res.json();
          if (data.session_id) {
            chatSessionId = data.session_id;
            localStorage.setItem('k8sai_session_id', chatSessionId);
          }
        }
      } catch (e) {
        console.warn('Failed to sync namespace to backend session:', e);
        // Don't show error to user - this is a silent sync operation
      }
    }

    function buildNamespaceSelectionQuery(ns) {
      const namespace = String(ns || '').trim();
      const app = String(selectedApp || '').trim();
      if (!namespace) return '';
      if (app) return `namespace ${namespace} for ${app}`;
      return namespace;
    }

    async function sendQuery() {
      if (chatMode === 'k8-autofix') {
        return;
      }
      const input = document.getElementById('chat-input');
      const query = input.value.trim();
      if (!query) return;

      input.value = '';
      document.getElementById('send-btn').disabled = true;
      addMsg('user', query);
      showThinking();

      try {
        const res = await fetch(`${API}/api/chat/query`, {
          method: 'POST',
          headers: authHeaders(),
          body: JSON.stringify({ query, session_id: chatSessionId, chat_mode: chatMode }),
        });
        const data = await res.json();
        removeThinking();

        if (data.session_id) {
          chatSessionId = data.session_id;
          localStorage.setItem('k8sai_session_id', chatSessionId);
        }

        addMsg('ai', data.answer || data.detail || 'No response');
        auditLog('READ', 'query', query.split(' ')[0] || 'unknown', {
          query_text: query,
          namespace: selectedNamespace,
          result_summary: data.answer?.substring(0, 100) || 'Query processed',
          success: res.ok,
          extra: { chat_mode: chatMode }
        });

        const editPayload = extractDeploymentEditPayload(data.data);
        if (editPayload) {
          const deploymentName = editPayload.deployment_name || 'unknown';
          const namespace = selectedNamespace || editPayload.namespace || 'default';
          const yamlStr = editPayload.deployment_manifest_yaml || convertToYaml(editPayload.deployment_manifest);
          setTimeout(() => {
            openDeploymentEditor(deploymentName, yamlStr, namespace, '', editPayload.resource_kind || 'Deployment');
          }, 300);
        }

        await Promise.all([loadSuggestions(), loadNamespaces()]);
      } catch (e) {
        removeThinking();
        addMsg('ai', `Error: ${e.message}`);
      } finally {
        document.getElementById('send-btn').disabled = false;
        input.focus();
      }
    }

    // Parser-safe formatter for editor content.
    // YAML parser on backend accepts JSON, which avoids malformed YAML edge cases.
    function convertToYaml(obj) {
      try {
        return JSON.stringify(obj, null, 2);
      } catch (e) {
        return '{}';
      }
    }

    function extractDeploymentEditPayload(dataObj) {
      if (!dataObj || !dataObj.open_editor_modal) return null;
      if (dataObj.deployment_manifest) {
        return {
          resource_kind: 'Deployment',
          deployment_name: dataObj.deployment_name,
          namespace: dataObj.namespace,
          deployment_manifest_yaml: dataObj.deployment_manifest_yaml,
          deployment_manifest: dataObj.deployment_manifest,
        };
      }
      if (dataObj.service_manifest) {
        return {
          resource_kind: 'Service',
          deployment_name: dataObj.service_name,
          namespace: dataObj.namespace,
          deployment_manifest_yaml: dataObj.service_manifest_yaml,
          deployment_manifest: dataObj.service_manifest,
        };
      }
      if (dataObj.ingress_manifest) {
        return {
          resource_kind: 'Ingress',
          deployment_name: dataObj.ingress_name,
          namespace: dataObj.namespace,
          deployment_manifest_yaml: dataObj.ingress_manifest_yaml,
          deployment_manifest: dataObj.ingress_manifest,
        };
      }
      if (dataObj.secret_manifest) {
        return {
          resource_kind: 'Secret',
          deployment_name: dataObj.secret_name,
          namespace: dataObj.namespace,
          deployment_manifest_yaml: dataObj.secret_manifest_yaml,
          deployment_manifest: dataObj.secret_manifest,
        };
      }
      if (dataObj.resourcequota_manifest) {
        return {
          resource_kind: 'ResourceQuota',
          deployment_name: dataObj.resourcequota_name,
          namespace: dataObj.namespace,
          deployment_manifest_yaml: dataObj.resourcequota_manifest_yaml,
          deployment_manifest: dataObj.resourcequota_manifest,
        };
      }

      for (const value of Object.values(dataObj)) {
        if (value && typeof value === 'object' && value.deployment_manifest) {
          return {
            resource_kind: 'Deployment',
            deployment_name: value.deployment_name,
            namespace: value.namespace,
            deployment_manifest_yaml: value.deployment_manifest_yaml,
            deployment_manifest: value.deployment_manifest,
          };
        }
        if (value && typeof value === 'object' && value.service_manifest) {
          return {
            resource_kind: 'Service',
            deployment_name: value.service_name,
            namespace: value.namespace,
            deployment_manifest_yaml: value.service_manifest_yaml,
            deployment_manifest: value.service_manifest,
          };
        }
        if (value && typeof value === 'object' && value.ingress_manifest) {
          return {
            resource_kind: 'Ingress',
            deployment_name: value.ingress_name,
            namespace: value.namespace,
            deployment_manifest_yaml: value.ingress_manifest_yaml,
            deployment_manifest: value.ingress_manifest,
          };
        }
        if (value && typeof value === 'object' && value.secret_manifest) {
          return {
            resource_kind: 'Secret',
            deployment_name: value.secret_name,
            namespace: value.namespace,
            deployment_manifest_yaml: value.secret_manifest_yaml,
            deployment_manifest: value.secret_manifest,
          };
        }
        if (value && typeof value === 'object' && value.resourcequota_manifest) {
          return {
            resource_kind: 'ResourceQuota',
            deployment_name: value.resourcequota_name,
            namespace: value.namespace,
            deployment_manifest_yaml: value.resourcequota_manifest_yaml,
            deployment_manifest: value.resourcequota_manifest,
          };
        }
      }
      return null;
    }

    function fillQuery(text) {
      document.getElementById('chat-input').value = text;
      document.getElementById('chat-input').focus();
    }

    document.addEventListener('DOMContentLoaded', () => {
      applyTheme(themeMode);
      logout();
      renderChatMode();

      const ddSource = document.getElementById('autofix-dd-source');
      const ddRange = document.getElementById('autofix-dd-range');
      const ddAuto = document.getElementById('autofix-dd-auto');
      const ddRefresh = document.getElementById('autofix-dd-refresh');
      const ddPodSearch = document.getElementById('autofix-dd-pod-search');
      const ddPodFind = document.getElementById('autofix-dd-pod-find');
      const ddPodClear = document.getElementById('autofix-dd-pod-clear');
      const ddSeverityButtons = document.querySelectorAll('.autofix-dd-sev-btn[data-severity]');
      if (ddSource) {
        ddSource.value = autofixDatadogSource === 'sample' ? 'sample' : 'live';
        ddSource.addEventListener('change', () => {
          autofixDatadogSource = (ddSource.value || 'live') === 'sample' ? 'sample' : 'live';
          localStorage.setItem('k8sai_autofix_dd_source', autofixDatadogSource);
          refreshAutofixDatadogIssues();
        });
      }
      if (ddRange) {
        ddRange.value = String(autofixDatadogRangeHours || 6);
        ddRange.addEventListener('change', () => {
          autofixDatadogRangeHours = Number(ddRange.value || '6');
          localStorage.setItem('k8sai_autofix_dd_range', String(autofixDatadogRangeHours));
          refreshAutofixDatadogIssues();
        });
      }
      if (ddAuto) {
        ddAuto.value = String(autofixDatadogAutoSeconds || 0);
        ddAuto.addEventListener('change', () => {
          autofixDatadogAutoSeconds = Number(ddAuto.value || '0');
          localStorage.setItem('k8sai_autofix_dd_auto', String(autofixDatadogAutoSeconds));
          setupAutofixAutoRefresh();
          renderAutofixDatadogUpdated(autofixDatadogLastSource || autofixDatadogSource);
        });
      }
      if (ddSeverityButtons && ddSeverityButtons.length) {
        ddSeverityButtons.forEach((btn) => {
          btn.addEventListener('click', () => {
            const severity = (btn.getAttribute('data-severity') || 'all').toLowerCase();
            autofixDatadogSeverity = severity;
            _afxHistoryOpen = false;
            _afxExamplesOpen = false;
            const panel = document.getElementById('autofix-history-panel');
            const examples = document.getElementById('autofix-example-panel');
            const body = document.getElementById('afx-history-body');
            if (panel) panel.classList.add('hidden');
            if (examples) examples.classList.add('hidden');
            if (body) body.classList.remove('open');
            localStorage.setItem('k8sai_autofix_dd_severity', autofixDatadogSeverity);
            renderAutofixSeverityButtons();
            renderAutofixDatadogList(autofixDatadogCurrentIssues || []);
          });
        });
      }
      renderAutofixSeverityButtons();
      if (ddRefresh) {
        ddRefresh.addEventListener('click', () => refreshAutofixDatadogIssues());
      }
      if (ddPodSearch) {
        ddPodSearch.value = autofixPodSearchQuery || '';
        ddPodSearch.addEventListener('keydown', (evt) => {
          if (evt.key === 'Enter') {
            evt.preventDefault();
            searchAutofixPodDetails();
          }
        });
      }
      if (ddPodFind) {
        ddPodFind.addEventListener('click', () => searchAutofixPodDetails());
      }
      if (ddPodClear) {
        ddPodClear.addEventListener('click', () => {
          autofixPodSearchQuery = '';
          if (ddPodSearch) ddPodSearch.value = '';
          clearAutofixPodDetails();
          renderAutofixDatadogList(autofixDatadogCurrentIssues || []);
        });
      }

      const upgradeBtn = document.getElementById('upgrade-btn');
      const upgradeCloseBtn = document.getElementById('upgrade-close-btn');
      const upgradeCancelBtn = document.getElementById('upgrade-cancel-btn');
      const upgradeActionBtn = document.getElementById('upgrade-action-btn');
      if (upgradeBtn) upgradeBtn.addEventListener('click', openUpgradeModal);
      if (upgradeCloseBtn) upgradeCloseBtn.addEventListener('click', closeUpgradeModal);
      if (upgradeCancelBtn) upgradeCancelBtn.addEventListener('click', closeUpgradeModal);
      if (upgradeActionBtn) upgradeActionBtn.addEventListener('click', confirmUpgrade);

      const chatInput = document.getElementById('chat-input');
      if (chatInput) {
        chatInput.addEventListener('keydown', (e) => {
          if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
            e.preventDefault();
            sendQuery();
          }
        });
      }

      const passwordInput = document.getElementById('password');
      if (passwordInput) {
        passwordInput.addEventListener('keydown', (e) => {
          if (e.key === 'Enter') doLogin();
        });
      }
    });

    // Fallback: if direct chat-input binding is ever skipped, delegate Enter handling.
    document.addEventListener('keydown', (e) => {
      const t = e.target;
      if (!(t instanceof HTMLElement)) return;
      if (t.id !== 'chat-input') return;
      if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
        e.preventDefault();
        sendQuery();
      }
    });

    // Ensure inline onclick handlers always resolve these actions.
    window.openUpgradeModal = openUpgradeModal;
    window.closeUpgradeModal = closeUpgradeModal;
    window.confirmUpgrade = confirmUpgrade;
    window.selectUpgradeVersion = selectUpgradeVersion;
  

      const prefillAction = item._prefillAction || null;
      const prefillParams = item._prefillParams || {};

      const actions = _buildAutofixActions(item);
      const list = document.getElementById('afx-actions-list');
      list.innerHTML = actions.map((a, ai) => {
        const isMatch = prefillAction && a.action === prefillAction;
        const fieldsHtml = a.fields.map(f => {
          const val = (prefillParams && prefillParams[f.key] !== undefined) ? prefillParams[f.key] : f.default;
          return `
            <label style="font-size:.78rem;color:var(--muted);">${f.label}</label>
            <input class="afx-action-input" id="afx-field-${ai}-${f.key}" type="text" value="${val}" placeholder="${f.placeholder}" />
          `;
        }).join('');
        return `
          <div class="afx-action-card${isMatch ? ' afx-action-card--highlighted' : ''}">
            <div class="afx-action-title">${a.title}${isMatch ? ' <span style="font-size:10px;padding:.1rem .4rem;border-radius:4px;background:var(--accent);color:#fff;vertical-align:middle;">last used</span>' : ''}</div>
            <div class="afx-action-desc">${a.desc}</div>
            ${a.fields.length ? `<div class="afx-action-row">${fieldsHtml}</div>` : ''}
            <div class="afx-action-row">
              <button class="afx-apply-btn" id="afx-apply-${ai}" type="button" onclick="applyAutofixAction(${ai})">Apply Fix</button>
            </div>
          </div>
        `;
      }).join('');

      // Scroll to highlighted card
      if (prefillAction) {
        setTimeout(() => {
          const highlighted = list.querySelector('.afx-action-card--highlighted');
          if (highlighted) highlighted.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        }, 80);
      }

