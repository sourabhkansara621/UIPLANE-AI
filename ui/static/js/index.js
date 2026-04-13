    const API = '';  // same origin
    let token = localStorage.getItem('k8sai_token') || '';
    let chatSessionId = localStorage.getItem('k8sai_session_id') || null;
    let currentUser = null;
    let namespacesExpanded = (localStorage.getItem('k8sai_namespaces_expanded') ?? 'true') === 'true';
    let namespaceFilter = '';
    let cachedNamespaces = [];
    let selectedNamespace = null;
    let selectedApp = localStorage.getItem('k8sai_selected_app') || null;
    let selectedCluster = localStorage.getItem('k8sai_selected_cluster') || null;
    let cachedClusterEntries = [];
    let chatMode = localStorage.getItem('k8sai_chat_mode') || 'k8-info';

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

      try {
        const res = await fetch(`${API}/api/auth/login`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ username, password }),
        });
        if (!res.ok) throw new Error('Invalid');
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
        document.getElementById('login-error').style.display = 'block';
        auditLog('LOGIN', 'user', username, {
          result_summary: 'Failed login attempt',
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
      addMsg('ai', `Authenticated as ${currentUser.username} (${currentUser.role}).\nApps: ${currentUser.allowed_apps.join(', ') || 'none'}\n\nStart with a namespace query, then ask for pods/quota/HPA/ingress in that namespace.`);
    }

    function setChatMode(mode) {
      chatMode = mode;
      localStorage.setItem('k8sai_chat_mode', mode);
      renderChatMode();
    }

    function renderChatMode() {
      const allowed = ['k8-info', 'k8-agent', 'k8-autofix'];
      if (!allowed.includes(chatMode)) chatMode = 'k8-info';

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
        note.textContent = 'k8-autofix: remediation-focused mode with safe deployment actions.';
      } else {
        note.textContent = 'k8-info: read-only cluster and namespace insights.';
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
        return;
      }

      selectedCluster = clusterName;
      localStorage.setItem('k8sai_selected_cluster', clusterName);
      await loadClusters();
      await loadNamespaces();
      await loadSuggestions();

      // Open cluster-specific details by querying for the first app mapped to this cluster.
      const mapped = cachedClusterEntries.find(e => e.cluster_name === clusterName);
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
        selectedNamespace = data.selected_namespace || null;
        renderNamespaces();
      } catch { }
    }

    function renderNamespaces() {
      const list = document.getElementById('namespace-list');
      const toggleBtn = document.getElementById('ns-toggle-btn');
      const filter = namespaceFilter.trim().toLowerCase();
      const filteredNamespaces = filter
        ? cachedNamespaces.filter(ns => ns.toLowerCase().includes(filter))
        : [...cachedNamespaces];
      const namespaces = namespacesExpanded
        ? filteredNamespaces
        : (selectedNamespace && filteredNamespaces.includes(selectedNamespace)
          ? [selectedNamespace]
          : filteredNamespaces.slice(0, 1));

      if (toggleBtn) toggleBtn.textContent = namespacesExpanded ? '-' : '+';

      if (!namespaces.length) {
        list.innerHTML = '<div class="namespace-empty">No matching namespaces</div>';
        return;
      }

      list.innerHTML = namespaces.map(ns =>
        `<div class="app-chip namespace-chip ${selectedNamespace === ns ? 'active' : ''}" onclick="selectNamespace('${ns}')"><span class="dot-s"></span>${ns}${selectedNamespace === ns ? '<span class="namespace-check" aria-hidden="true"></span>' : ''}</div>`
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
      if (!chatSessionId) return;
      try {
        await fetch(`${API}/api/chat/namespace/clear?session_id=${encodeURIComponent(chatSessionId)}`, {
          method: 'POST',
          headers: authHeaders(),
        });
      } catch { }
      selectedNamespace = null;
      await loadSuggestions();
      await loadNamespaces();
    }

    async function selectNamespace(ns) {
      if (selectedNamespace === ns) {
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
      fillQuery(ns);
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
    <div class="msg-meta">${role === 'user' ? currentUser.username : 'K8S-AI'} | ${time}</div>
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

    async function sendQuery() {
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
      logout();
      renderChatMode();

      const upgradeBtn = document.getElementById('upgrade-btn');
      const upgradeCloseBtn = document.getElementById('upgrade-close-btn');
      const upgradeCancelBtn = document.getElementById('upgrade-cancel-btn');
      const upgradeActionBtn = document.getElementById('upgrade-action-btn');
      if (upgradeBtn) upgradeBtn.addEventListener('click', openUpgradeModal);
      if (upgradeCloseBtn) upgradeCloseBtn.addEventListener('click', closeUpgradeModal);
      if (upgradeCancelBtn) upgradeCancelBtn.addEventListener('click', closeUpgradeModal);
      if (upgradeActionBtn) upgradeActionBtn.addEventListener('click', confirmUpgrade);

      document.getElementById('chat-input').addEventListener('keydown', e => {
        if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendQuery(); }
      });
      document.getElementById('password').addEventListener('keydown', e => {
        if (e.key === 'Enter') doLogin();
      });
    });

    // Ensure inline onclick handlers always resolve these actions.
    window.openUpgradeModal = openUpgradeModal;
    window.closeUpgradeModal = closeUpgradeModal;
    window.confirmUpgrade = confirmUpgrade;
    window.selectUpgradeVersion = selectUpgradeVersion;
  
