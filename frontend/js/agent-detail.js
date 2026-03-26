// CogNexus Agent Detail — Native Rendering (1:1 from brain-visual)
const AgentDetail = (function() {
    let agentId = null;
    let agentData = null;
    let currentView = null;
    let viewCleanup = null;
    let currentSessionId = null;
    let sessionsCache = [];

    function getToken() { return localStorage.getItem('cog_token'); }
    function hdrs() { return { 'Authorization': 'Bearer ' + getToken() }; }
    function jsonHdrs() { return { ...hdrs(), 'Content-Type': 'application/json' }; }
    function requireAuth() { if (!getToken()) { location.href = '/?login=1&redirect=' + encodeURIComponent(location.pathname + location.search); return false; } return true; }
    function getNs() { return (agentData && agentData.namespace) || 'default'; }
    function esc(s) { const d = document.createElement('div'); d.textContent = s; return d.innerHTML; }
    function renderMarkdown(text) {
        if (typeof marked !== 'undefined' && marked.parse) {
            try {
                marked.setOptions({ breaks: true, gfm: true, sanitize: false });
                return marked.parse(text);
            } catch(e) {}
        }
        return esc(text).replace(/\n/g, '<br>');
    }

    // ===== 通用自定义弹窗 =====
    let _modalResolve = null;

    function showModal(options) {
        return new Promise((resolve) => {
            _modalResolve = resolve;
            const modal = document.getElementById('globalModal');
            document.getElementById('modalTitle').textContent = options.title || '提示';
            document.getElementById('modalBody').textContent = options.message || '';

            const inputGroup = document.getElementById('modalInputGroup');
            const input = document.getElementById('modalInput');
            if (options.type === 'prompt') {
                inputGroup.style.display = 'block';
                document.getElementById('modalInputLabel').textContent = options.inputLabel || '';
                input.value = options.inputDefault || '';
                input.type = options.inputType || 'text';
                setTimeout(() => input.focus(), 200);
            } else {
                inputGroup.style.display = 'none';
            }

            const confirmBtn = document.getElementById('modalConfirmBtn');
            confirmBtn.textContent = options.confirmText || '确认';
            if (options.confirmStyle === 'danger') {
                confirmBtn.style.background = 'rgba(184,134,138,0.9)';
            } else {
                confirmBtn.style.background = 'rgba(109,168,155,0.9)';
            }

            input.onkeydown = (e) => { if (e.key === 'Enter') confirmModal(); };

            modal.classList.add('show');
        });
    }

    let _mobileAvatarVisible = false;
    function toggleMobileAvatar() {
        const panel = document.getElementById('avatarPanel');
        const btn = document.getElementById('mobileChatToggle');
        if (!panel || !btn) return;
        _mobileAvatarVisible = !_mobileAvatarVisible;
        if (_mobileAvatarVisible) {
            panel.classList.add('mobile-show');
            btn.textContent = '💬';
            btn.title = '返回对话';
        } else {
            panel.classList.remove('mobile-show');
            btn.textContent = '🎭';
            btn.title = '查看虚拟形象';
        }
    }

    function closeModal() {
        document.getElementById('globalModal').classList.remove('show');
        if (_modalResolve) { _modalResolve(null); _modalResolve = null; }
    }

    function confirmModal() {
        const modal = document.getElementById('globalModal');
        const inputGroup = document.getElementById('modalInputGroup');
        let result;
        if (inputGroup.style.display !== 'none') {
            result = document.getElementById('modalInput').value;
        } else {
            result = true;
        }
        modal.classList.remove('show');
        if (_modalResolve) { _modalResolve(result); _modalResolve = null; }
    }

    // ===== Init =====
    async function init() {
        if (!requireAuth()) return;
        const p = new URLSearchParams(location.search);
        agentId = p.get('id');
        if (!agentId) { location.href = '/dashboard'; return; }
        setupTabs();
        setupViewSwitcher();
        await loadAgent();
        // Auto-switch tab if ?tab=xxx (after agent data loaded)
        const initTab = p.get('tab');
        if (initTab) {
            const tabBtn = document.querySelector(`.detail-tab[data-tab="${initTab}"]`);
            if (tabBtn) tabBtn.click();
        }
        // LLM provider toggle
        const llmSel = document.getElementById('configLlmProvider');
        if (llmSel) llmSel.addEventListener('change', toggleLlmEndpoint);
        // configType already has inline onchange in HTML
    }

    let chatInitialized = false;

    function setupTabs() {
        document.querySelectorAll('.detail-tab').forEach(tab => {
            tab.addEventListener('click', () => {
                document.querySelectorAll('.detail-tab').forEach(t => t.classList.remove('active'));
                tab.classList.add('active');
                document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
                const el = document.getElementById('tab-' + tab.dataset.tab);
                if (el) el.classList.add('active');
                if (tab.dataset.tab === 'knowledge' && !currentView) loadKnowledgeView('graph');
                if (tab.dataset.tab === 'chat') {
                    if (!chatInitialized) initChatTab();
                    else if (isCurrentUserOwner()) startKEPolling();
                }
                if (tab.dataset.tab !== 'chat') stopKEPolling();
                if (tab.dataset.tab === 'usage') loadUsage();
                if (tab.dataset.tab === 'insights') { loadInsights(true); _updateInsightBadge(); }
                if (tab.dataset.tab === 'config') {
                    const ct = document.getElementById('tab-config');
                    if (ct && !document.getElementById('taskConfigContainer')) {
                        const sec = document.createElement('div');
                        sec.className = 'config-section task-config-section';
                        sec.innerHTML = '<div class="config-section-title">⏰ 定时任务</div><div id="taskConfigContainer"><div class="empty" style="font-size:0.82em;color:var(--text-muted);">加载中...</div></div>';
                        ct.appendChild(sec);
                    }
                    loadTaskConfig();
                }
            });
        });
    }

    function initChatTab() {
        chatInitialized = true;
        const box = document.getElementById('chatArea');
        renderChat(box, {style:{}}, {style:{}});
        initLive2D();
        // Start knowledge suggestion polling for owners
        if (isCurrentUserOwner()) startKEPolling();
    }

    function setupViewSwitcher() {
        document.querySelectorAll('.view-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                document.querySelectorAll('.view-btn').forEach(b => b.classList.remove('active'));
                btn.classList.add('active');
                loadKnowledgeView(btn.dataset.view);
            });
        });
    }

    // ===== Load Agent =====
    async function loadAgent() {
        try {
            const r = await fetch('/api/agents/' + agentId, { headers: hdrs() });
            if (!r.ok) throw new Error();
            agentData = await r.json();
            renderHeader();
            updatePublishButton();
            renderOverview();
            loadStats();
            fillConfig();
        } catch (e) {
            document.querySelector('.agent-title').textContent = 'Agent Not Found';
        }
    }

    function renderHeader() {
        const a = agentData;
        document.getElementById('agentName').textContent = a.name || a.agent_id;
        document.title = (a.name || a.agent_id) + ' — Wielding.ai';
        const s = document.getElementById('agentStatus');
        const on = a.status === 'active';
        s.className = 'status-badge ' + (on ? 'online' : 'offline');
        s.querySelector('.status-dot').className = 'status-dot ' + (on ? 'online' : 'offline');
        s.querySelector('span:last-child').textContent = a.status || 'unknown';
        const tb = document.getElementById('agentTypeBadge');
        tb.className = 'type-badge ' + (a.agent_type || '');
        tb.textContent = a.agent_type || '—';
        document.getElementById('agentNamespace').textContent = a.namespace || 'default';
    }

    function renderOverview() {
        const a = agentData;
        // Profile
        const avatarEl = document.getElementById('ovAvatar');
        if (a.avatar_url) {
            avatarEl.innerHTML = '<img src="' + esc(a.avatar_url) + '" alt="">';
        } else {
            avatarEl.textContent = a.agent_type === 'character' ? '🎭' : '🧠';
        }
        document.getElementById('ovName').textContent = a.name || a.agent_id;
        document.getElementById('ovDesc').textContent = a.description || '';

        // Show edit button if owner
        checkOwnership();
    }

    function checkOwnership() {
        let isOwner = false;
        try {
            const token = getToken();
            if (!token) return;
            const payload = JSON.parse(atob(token.split('.')[1]));
            const userId = payload.sub || payload.user_id;
            if (agentData.owner_id && agentData.owner_id === userId) {
                isOwner = true;
                document.getElementById('ovEditBtn').style.display = '';
                document.getElementById('ovTokenMgmt').style.display = '';
                loadTokens();
            }
        } catch {}

        // 所有者：显示所有 owner-only 元素
        if (isOwner) {
            document.querySelectorAll('.owner-only').forEach(el => {
                el.style.display = '';
            });
        }
    }

    async function loadStats() {
        try {
            const r = await fetch('/api/knowledge/' + getNs() + '/stats', { headers: hdrs() });
            if (!r.ok) return;
            const s = await r.json();
            document.getElementById('statFacts').textContent = s.total_facts ?? s.graph_nodes ?? '—';
            document.getElementById('statEdges').textContent = s.graph_edges ?? '—';
            document.getElementById('statAbstracts').textContent = s.abstracts ?? '—';
            // Data scope: show date range if available
            const scope = s.date_range || s.data_scope || '—';
            document.getElementById('statScope').textContent = scope;
        } catch {}
    }

    // ===== View Card Navigation =====
    function goView(view) {
        // Switch to Knowledge tab
        document.querySelectorAll('.detail-tab').forEach(t => t.classList.remove('active'));
        const knowledgeTab = document.querySelector('.detail-tab[data-tab="knowledge"]');
        if (knowledgeTab) knowledgeTab.classList.add('active');
        document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
        const knowledgePane = document.getElementById('tab-knowledge');
        if (knowledgePane) knowledgePane.classList.add('active');
        // Activate the right view button
        document.querySelectorAll('.view-btn').forEach(b => {
            b.classList.toggle('active', b.dataset.view === view);
        });
        loadKnowledgeView(view);
    }

    // ===== Token Management =====
    async function loadTokens() {
        const el = document.getElementById('ovTokenList');
        const ld = document.getElementById('ovTokenLoading');
        try {
            const r = await fetch('/api/agents/' + agentId + '/tokens', { headers: hdrs() });
            if (!r.ok) throw new Error();
            const d = await r.json();
            const tokens = d.tokens || [];
            if (ld) ld.style.display = 'none';
            if (!tokens.length) {
                el.innerHTML = '<div class="knowledge-empty" style="padding:20px;"><div class="empty-icon">🔑</div><div class="empty-text">暂无 Token</div></div>';
                return;
            }
            el.innerHTML = tokens.map(t => {
                const scopeClass = t.scope || '';
                const scopeLabels = { 'full': '🔓 全量', 'qa_public': '💬 问答', 'browse_public': '👁️ 浏览' };
                const scopeLabel = scopeLabels[t.scope] || t.scope_label || t.scope || '';
                const val = t.token_value || t.token_id;
                const display = val.length > 20 ? val.slice(0, 8) + '...' + val.slice(-6) : val;
                let meta = scopeLabel;
                if (t.expires_at) meta += ' · 到期: ' + t.expires_at.slice(0, 10);
                if (t.qa_limit) meta += ' · 问答: ' + t.qa_limit + '次';
                if (t.is_sold) meta += ' · 已售出';
                return '<div class="ov-token-item ' + esc(scopeClass) + '">' +
                    '<div class="ov-token-info">' +
                    '<div class="ov-token-id">' + esc(display) + '</div>' +
                    '<div class="ov-token-meta">' + esc(meta) + '</div>' +
                    (t.note ? '<div class="ov-token-note">' + esc(t.note) + '</div>' : '') +
                    '</div>' +
                    '<div class="ov-token-actions">' +
                    '<button onclick="navigator.clipboard.writeText(\'' + esc(val) + '\');this.textContent=\'已复制\';setTimeout(()=>this.textContent=\'复制\',1500)">复制</button>' +
                    '</div></div>';
            }).join('');
        } catch {
            if (ld) ld.style.display = 'none';
            el.innerHTML = '<div class="knowledge-empty" style="padding:20px;"><div class="empty-text">加载 Token 失败</div></div>';
        }
    }

    async function addTokens() {
        // 自动生成 Token，无需用户输入
        try {
            const r = await fetch('/api/agents/' + agentId + '/tokens/generate', {
                method: 'POST', headers: jsonHdrs(),
                body: JSON.stringify({})
            });
            if (!r.ok) throw new Error();
            const d = await r.json();
            toast('Token 已生成');
            loadTokens();
        } catch { toast('生成失败', 'error'); }
    }

    // ===== Profile Edit Modal =====
    function openEditModal() {
        const m = document.getElementById('editProfileModal');
        document.getElementById('editProfileName').value = agentData.name || '';
        document.getElementById('editProfileDesc').value = agentData.description || '';
        document.getElementById('editProfileAvatar').value = agentData.avatar_url || '';
        m.classList.add('show');
    }

    function closeEditModal() {
        document.getElementById('editProfileModal').classList.remove('show');
    }

    async function saveProfile() {
        try {
            const r = await fetch('/api/agents/' + agentId, {
                method: 'PUT', headers: jsonHdrs(),
                body: JSON.stringify({
                    name: document.getElementById('editProfileName').value,
                    description: document.getElementById('editProfileDesc').value,
                    avatar_url: document.getElementById('editProfileAvatar').value
                })
            });
            if (!r.ok) throw new Error();
            agentData = await r.json();
            renderHeader();
            renderOverview();
            closeEditModal();
            toast('资料已更新');
        } catch { toast('保存失败', 'error'); }
    }

    // =============================================
    // Knowledge Views — 1:1 brain-visual
    // =============================================
    function loadKnowledgeView(view) {
        if (viewCleanup) { viewCleanup(); viewCleanup = null; }
        currentView = view;
        const box = document.getElementById('knowledgeView');
        const ld = document.getElementById('knowledgeLoading');
        const em = document.getElementById('knowledgeEmpty');
        box.innerHTML = '';
        box.style.cssText = '';
        ld.style.display = 'flex';
        em.style.display = 'none';

        // Set CSS class for proper sizing
        box.className = 'knowledge-view kv-' + view;

        if (view === 'graph') renderGraph(box, ld, em);
        else if (view === 'tree') renderTree(box, ld, em);
        else if (view === 'timeline') renderTimelineView(box, ld, em);
        else if (view === 'chat') renderChat(box, ld, em);
    }

    // ===== Graph View — D3 Force-Directed (like brain-visual/graph.html) =====
    async function renderGraph(box, ld, em) {
        const ns = getNs();
        try {
            const r = await fetch('/api/knowledge/' + ns + '/graph?limit=300', { headers: hdrs() });
            if (!r.ok) throw new Error();
            const data = await r.json();
            const nodes = data.nodes || [];
            const edges = data.edges || [];
            if (!nodes.length) { ld.style.display = 'none'; em.style.display = 'block'; return; }
            ld.style.display = 'none';

            // Graph uses CSS class kv-graph for sizing

            // Load D3
            if (!window.d3) {
                await new Promise((res, rej) => {
                    const s = document.createElement('script');
                    s.src = 'https://cdn.jsdelivr.net/npm/d3@7';
                    s.onload = res; s.onerror = rej;
                    document.head.appendChild(s);
                });
            }

            const w = box.clientWidth, h = 600;
            const svg = d3.select(box).append('svg').attr('width', w).attr('height', h);
            const g = svg.append('g');

            // Zoom
            const zoom = d3.zoom().scaleExtent([0.2, 5]).on('zoom', e => g.attr('transform', e.transform));
            svg.call(zoom);

            // Arrow marker
            svg.append('defs').append('marker')
                .attr('id', 'arrow').attr('viewBox', '0 -5 10 10')
                .attr('refX', 20).attr('refY', 0)
                .attr('markerWidth', 6).attr('markerHeight', 6)
                .attr('orient', 'auto')
                .append('path').attr('d', 'M0,-5L10,0L0,5')
                .attr('fill', 'rgba(109,168,155,0.4)');

            const colorMap = {
                '观点': '#6da89b', '事件': '#d4a574', '资讯': '#7eb8d4',
                '决策': '#b8868a', '情绪': '#c4956a'
            };

            // Build link data
            const nodeById = {};
            nodes.forEach(n => nodeById[n.id] = n);
            const links = edges.filter(e => nodeById[e.source || e.from] && nodeById[e.target || e.to])
                .map(e => ({ source: e.source || e.from, target: e.target || e.to, label: e.label || e.type || '' }));

            // Simulation
            const simulation = d3.forceSimulation(nodes)
                .force('link', d3.forceLink(links).id(d => d.id).distance(100))
                .force('charge', d3.forceManyBody().strength(-200))
                .force('center', d3.forceCenter(w / 2, h / 2))
                .force('collision', d3.forceCollide().radius(20));

            // Links
            const link = g.append('g').selectAll('line').data(links).join('line')
                .attr('stroke', 'rgba(109,168,155,0.2)')
                .attr('stroke-width', 1)
                .attr('marker-end', 'url(#arrow)');

            // Edge labels
            const edgeLabel = g.append('g').selectAll('text').data(links).join('text')
                .text(d => d.label)
                .attr('font-size', '8px')
                .attr('fill', 'rgba(255,255,255,0.25)')
                .attr('text-anchor', 'middle');

            // Nodes
            const node = g.append('g').selectAll('circle').data(nodes).join('circle')
                .attr('r', d => 5 + Math.min((d.degree || 0) * 0.8, 6))
                .attr('fill', d => colorMap[d.type] || '#6da89b')
                .attr('stroke', 'rgba(255,255,255,0.1)')
                .attr('stroke-width', 1)
                .attr('cursor', 'pointer')
                .call(d3.drag()
                    .on('start', (e, d) => { if (!e.active) simulation.alphaTarget(0.3).restart(); d.fx = d.x; d.fy = d.y; })
                    .on('drag', (e, d) => { d.fx = e.x; d.fy = e.y; })
                    .on('end', (e, d) => { if (!e.active) simulation.alphaTarget(0); d.fx = null; d.fy = null; })
                );

            // Node labels
            const labels = g.append('g').selectAll('text').data(nodes).join('text')
                .text(d => (d.label || d.id || '').slice(0, 16))
                .attr('font-size', '9px')
                .attr('fill', 'rgba(232,228,223,0.6)')
                .attr('dx', 10).attr('dy', 4);

            // Tooltip
            const tip = document.createElement('div');
            tip.style.cssText = 'position:absolute;display:none;background:rgba(15,15,25,0.95);color:#e8e4df;padding:12px 16px;border-radius:8px;font-size:0.82em;max-width:340px;pointer-events:none;border:1px solid rgba(109,168,155,0.3);z-index:10;line-height:1.6;';
            box.appendChild(tip);

            // Cache for detail panel
            _graphNodes = nodes;
            _graphEdges = links;

            node.on('mouseover', function(e, d) {
                d3.select(this).attr('stroke', '#fff').attr('stroke-width', 2);
                tip.style.display = 'block';
                tip.innerHTML = '<div style="color:' + (colorMap[d.type] || '#6da89b') + ';font-weight:600;margin-bottom:4px;">' + esc(d.type || '') + '</div>' +
                    '<div>' + esc((d.full_content || d.label || d.id).slice(0, 250)) + '</div>' +
                    '<div style="font-size:0.75em;color:#6b665e;margin-top:6px;">点击查看详情</div>';
            }).on('mousemove', function(e) {
                const rect = box.getBoundingClientRect();
                tip.style.left = (e.clientX - rect.left + 15) + 'px';
                tip.style.top = (e.clientY - rect.top + 15) + 'px';
            }).on('mouseout', function() {
                d3.select(this).attr('stroke', 'rgba(255,255,255,0.1)').attr('stroke-width', 1);
                tip.style.display = 'none';
            }).on('click', function(e, d) {
                tip.style.display = 'none';
                showDetail(d);
            });

            simulation.on('tick', () => {
                link.attr('x1', d => d.source.x).attr('y1', d => d.source.y)
                    .attr('x2', d => d.target.x).attr('y2', d => d.target.y);
                edgeLabel.attr('x', d => (d.source.x + d.target.x) / 2)
                    .attr('y', d => (d.source.y + d.target.y) / 2);
                node.attr('cx', d => d.x).attr('cy', d => d.y);
                labels.attr('x', d => d.x).attr('y', d => d.y);
            });

            // Legend
            const legend = document.createElement('div');
            legend.style.cssText = 'position:absolute;bottom:12px;left:12px;display:flex;gap:12px;flex-wrap:wrap;';
            Object.entries(colorMap).forEach(([t, c]) => {
                legend.innerHTML += '<div style="display:flex;align-items:center;gap:4px;"><div style="width:8px;height:8px;border-radius:50%;background:' + c + ';"></div><span style="color:rgba(232,228,223,0.5);font-size:0.72em;">' + t + '</span></div>';
            });
            box.appendChild(legend);

            viewCleanup = () => { simulation.stop(); box.innerHTML = ''; };
        } catch (e) {
            console.error('Graph error:', e);
            ld.style.display = 'none'; em.style.display = 'block';
        }
    }

    // ===== Tree View — Abstract hierarchy (like brain-visual/tree.html) =====
    async function renderTree(box, ld, em) {
        const ns = getNs();
        try {
            const [treeR, graphR] = await Promise.all([
                fetch('/api/knowledge/' + ns + '/tree', { headers: hdrs() }),
                fetch('/api/knowledge/' + ns + '/graph?limit=300', { headers: hdrs() })
            ]);
            if (!treeR.ok || !graphR.ok) throw new Error();
            const treeData = await treeR.json();
            const graphData = await graphR.json();
            const abstracts = treeData.abstracts || [];
            const graphNodes = {};
            (graphData.nodes || []).forEach(n => graphNodes[n.id] = n);

            ld.style.display = 'none';
            if (!abstracts.length) { em.style.display = 'block'; return; }

            // Tree uses CSS class kv-tree for sizing

            // Summary stats
            const totalFacts = (graphData.nodes || []).length;
            box.innerHTML = '<div class="tree-summary" style="display:flex;gap:14px;margin-bottom:24px;flex-wrap:wrap;">' +
                statBox(abstracts.length, '抽象层') +
                statBox(totalFacts, '事实') +
                statBox((graphData.edges || []).length, '关联') +
                '</div>' +
                '<div id="treeRoot">' + abstracts.map((ab, i) => renderAbstractCard(ab, graphNodes, i)).join('') + '</div>';

            // Toggle expand
            box.querySelectorAll('.abstract-card').forEach(card => {
                card.querySelector('.abstract-header').addEventListener('click', () => {
                    const sf = card.querySelector('.source-facts');
                    const arrow = card.querySelector('.expand-arrow');
                    if (sf) {
                        const isHidden = sf.style.display === 'none';
                        sf.style.display = isHidden ? 'block' : 'none';
                        if (arrow) arrow.textContent = isHidden ? '▾' : '▸';
                    }
                });
            });

            viewCleanup = () => { box.innerHTML = ''; box.style.cssText = ''; };
        } catch (e) {
            console.error('Tree error:', e);
            ld.style.display = 'none'; em.style.display = 'block';
        }
    }

    function statBox(val, label) {
        return '<div style="background:rgba(255,255,255,0.03);border:1px solid rgba(255,255,255,0.06);border-radius:10px;padding:16px 22px;text-align:center;min-width:100px;">' +
            '<div style="font-family:Playfair Display,Georgia,serif;font-size:1.8em;font-weight:700;color:#e2b96a;">' + val + '</div>' +
            '<div style="font-size:0.72em;color:#6b665e;margin-top:4px;text-transform:uppercase;letter-spacing:0.04em;">' + label + '</div></div>';
    }

    function renderAbstractCard(ab, graphNodes, idx) {
        const statusClass = ab.status === 'confirmed' ? 'confirmed' : 'draft';
        const statusLabel = ab.status === 'confirmed' ? '✅ 已确认' : '📝 草稿';
        const facts = (ab.source_facts || []).map(fid => graphNodes[fid]).filter(Boolean);
        const typeColors = { '观点':'#6da89b','事件':'#7c99b8','情绪':'#9d8bb8','资讯':'#b8a66e','决策':'#b8868a' };

        return '<div class="abstract-card" style="background:#1e2025;border:1px solid rgba(255,255,255,0.06);border-radius:10px;margin-bottom:12px;overflow:hidden;animation:fade-up 0.4s ease both;animation-delay:' + (idx * 0.05) + 's;">' +
            '<div class="abstract-header" style="padding:18px 20px;cursor:pointer;display:flex;justify-content:space-between;align-items:flex-start;">' +
            '<div><div style="font-family:Playfair Display,Georgia,serif;color:#e2b96a;font-size:1em;font-weight:700;">' + esc(ab.name) + '</div>' +
            '<div style="display:flex;gap:8px;margin-top:6px;font-size:0.72em;">' +
            '<span style="background:rgba(' + (statusClass === 'confirmed' ? '109,168,155' : '184,166,110') + ',0.15);color:' + (statusClass === 'confirmed' ? '#6da89b' : '#b8a66e') + ';padding:2px 8px;border-radius:4px;">' + statusLabel + '</span>' +
            '<span style="color:#6b665e;">来源: ' + (ab.source_count || facts.length) + ' 条事实</span></div>' +
            (ab.description ? '<div style="margin-top:10px;color:#a8a299;font-size:0.88em;line-height:1.6;">' + esc(ab.description.slice(0, 200)) + '</div>' : '') +
            '</div><div class="expand-arrow" style="color:#6b665e;font-size:1.2em;flex-shrink:0;margin-left:12px;transition:transform 0.2s;">▸</div></div>' +
            '<div class="source-facts" style="border-top:1px solid rgba(255,255,255,0.06);padding:14px 20px;background:rgba(0,0,0,0.15);display:none;">' +
            '<div style="font-size:0.72em;color:#6b665e;margin-bottom:10px;text-transform:uppercase;letter-spacing:0.06em;">来源事实</div>' +
            facts.map(f => {
                const c = typeColors[f.type] || '#6da89b';
                return '<div style="padding:10px 14px;background:rgba(255,255,255,0.02);border-radius:6px;margin-bottom:6px;border-left:2px solid ' + c + ';">' +
                    '<div style="font-size:0.68em;color:#6b665e;margin-bottom:3px;text-transform:uppercase;letter-spacing:0.04em;">' + esc(f.type || '') + '</div>' +
                    '<div style="font-size:0.85em;color:#a8a299;line-height:1.5;">' + esc((f.full_content || f.label || '').slice(0, 200)) + '</div></div>';
            }).join('') +
            '</div></div>';
    }

    // ===== Timeline View (like brain-visual/timeline.html) =====
    async function renderTimelineView(box, ld, em) {
        const ns = getNs();
        try {
            const r = await fetch('/api/knowledge/' + ns + '/timeline', { headers: hdrs() });
            if (!r.ok) throw new Error();
            const data = await r.json();
            const facts = data.facts || [];
            if (!facts.length) { ld.style.display = 'none'; em.style.display = 'block'; return; }
            ld.style.display = 'none';

            // Timeline uses CSS class kv-timeline for sizing

            const typeColors = { '观点':'#6da89b','事件':'#d4a574','资讯':'#7eb8d4','决策':'#b8868a','情绪':'#c4956a' };
            const typeCount = {};
            facts.forEach(f => { typeCount[f.type] = (typeCount[f.type] || 0) + 1; });

            // Stats bar
            let statsHtml = '<div style="display:flex;gap:14px;margin-bottom:24px;flex-wrap:wrap;">';
            statsHtml += statBox(facts.length, '总计');
            Object.entries(typeCount).forEach(([t, c]) => { statsHtml += statBox(c, t); });
            statsHtml += '</div>';

            // Filter buttons
            let filterHtml = '<div style="display:flex;gap:8px;margin-bottom:20px;flex-wrap:wrap;">';
            filterHtml += '<button class="tl-filter active" data-filter="all" style="padding:5px 14px;border-radius:6px;border:1px solid rgba(109,168,155,0.3);background:rgba(109,168,155,0.15);color:#6da89b;cursor:pointer;font-size:0.8em;">全部</button>';
            Object.keys(typeCount).forEach(t => {
                filterHtml += '<button class="tl-filter" data-filter="' + t + '" style="padding:5px 14px;border-radius:6px;border:1px solid rgba(255,255,255,0.1);background:transparent;color:#a8a299;cursor:pointer;font-size:0.8em;">' + t + '</button>';
            });
            filterHtml += '</div>';

            // Group by date
            function renderItems(filterType) {
                const filtered = filterType === 'all' ? facts : facts.filter(f => f.type === filterType);
                const groups = {};
                filtered.forEach(f => {
                    const d = (f.created_at || f.timestamp || '').slice(0, 10) || '未知';
                    if (!groups[d]) groups[d] = [];
                    groups[d].push(f);
                });

                return Object.keys(groups).sort().reverse().map(date => {
                    const dateLabel = formatDateCN(date);
                    return '<div style="margin-bottom:20px;">' +
                        '<div style="display:flex;align-items:center;gap:10px;margin-bottom:10px;">' +
                        '<span style="color:#e2b96a;font-family:Playfair Display,serif;font-weight:700;font-size:0.95em;">' + dateLabel + '</span>' +
                        '<span style="color:#6b665e;font-size:0.75em;">' + groups[date].length + ' 条</span>' +
                        '<div style="flex:1;height:1px;background:rgba(255,255,255,0.06);"></div></div>' +
                        '<div style="position:relative;padding-left:24px;">' +
                        '<div style="position:absolute;left:5px;top:0;bottom:0;width:2px;background:rgba(109,168,155,0.15);"></div>' +
                        groups[date].map(item => {
                            const c = typeColors[item.type] || '#6da89b';
                            const time = (item.created_at || '').slice(11, 16) || '';
                            const fullText = item.full_content || item.label || '';
                            const preview = fullText.slice(0, 120);
                            const hasMore = fullText.length > 120;
                            const itemId = 'tl-' + (item.id || item.full_id || Math.random().toString(36).slice(2));
                            return '<div data-item-id="' + esc(item.id || item.full_id || '') + '" style="position:relative;margin-bottom:10px;padding:12px 16px;background:rgba(255,255,255,0.02);border-radius:8px;border:1px solid rgba(255,255,255,0.04);transition:background 0.15s;cursor:pointer;" onmouseover="this.style.background=\'rgba(255,255,255,0.04)\';var a=this.querySelector(\'.tl-item-actions\');if(a)a.style.display=\'flex\'" onmouseout="this.style.background=\'rgba(255,255,255,0.02)\';var a=this.querySelector(\'.tl-item-actions\');if(a)a.style.display=\'none\'">' +
                                '<div style="position:absolute;left:-19px;top:16px;width:8px;height:8px;border-radius:50%;background:' + c + ';border:2px solid #0a0a0f;"></div>' +
                                '<div class="tl-item-actions" style="position:absolute;right:8px;top:8px;display:none;gap:6px;">' +
                                '<button onclick="event.stopPropagation();AgentDetail.editFact(\'' + esc(item.full_id || item.id) + '\')" style="padding:4px 8px;border-radius:4px;background:rgba(109,168,155,0.2);color:#6da89b;border:none;cursor:pointer;font-size:0.75em;" title="编辑">✏️</button>' +
                                '<button onclick="event.stopPropagation();AgentDetail.togglePrivacy(\'' + esc(item.full_id || item.id) + '\')" style="padding:4px 8px;border-radius:4px;background:rgba(226,185,106,0.2);color:#e2b96a;border:none;cursor:pointer;font-size:0.75em;" title="隐私">🔒</button>' +
                                '<button onclick="event.stopPropagation();AgentDetail.deleteFact(\'' + esc(item.full_id || item.id) + '\')" style="padding:4px 8px;border-radius:4px;background:rgba(184,134,138,0.2);color:#b8868a;border:none;cursor:pointer;font-size:0.75em;" title="删除">🗑️</button>' +
                                '</div>' +
                                '<div style="display:flex;align-items:center;gap:8px;margin-bottom:4px;">' +
                                '<span style="font-size:0.68em;color:#6b665e;text-transform:uppercase;letter-spacing:0.04em;background:' + c + '22;color:' + c + ';padding:1px 6px;border-radius:3px;">' + esc(item.type || '') + '</span>' +
                                '<span style="color:#6b665e;font-size:0.72em;">' + time + '</span></div>' +
                                '<div style="color:#a8a299;font-size:0.88em;line-height:1.6;">' + esc(preview) + (hasMore ? '<span style="color:#6da89b;margin-left:4px;">... 查看全文</span>' : '') + '</div></div>';
                        }).join('') +
                        '</div></div>';
                }).join('');
            }

            box.innerHTML = statsHtml + filterHtml + '<div id="tlItems">' + renderItems('all') + '</div>';

            // Filter click handlers
            box.querySelectorAll('.tl-filter').forEach(btn => {
                btn.addEventListener('click', () => {
                    box.querySelectorAll('.tl-filter').forEach(b => {
                        b.style.background = 'transparent';
                        b.style.borderColor = 'rgba(255,255,255,0.1)';
                        b.style.color = '#a8a299';
                        b.classList.remove('active');
                    });
                    btn.style.background = 'rgba(109,168,155,0.15)';
                    btn.style.borderColor = 'rgba(109,168,155,0.3)';
                    btn.style.color = '#6da89b';
                    btn.classList.add('active');
                    document.getElementById('tlItems').innerHTML = renderItems(btn.dataset.filter);
                    bindTimelineClicks();
                });
            });

            function bindTimelineClicks() {
                box.querySelectorAll('[data-item-id]').forEach(el => {
                    el.addEventListener('click', () => {
                        const id = el.dataset.itemId;
                        const item = facts.find(f => (f.id === id || f.full_id === id));
                        if (item) showDetail(item);
                    });
                });
            }
            bindTimelineClicks();

            viewCleanup = () => { box.innerHTML = ''; box.style.cssText = ''; };
        } catch (e) {
            console.error('Timeline error:', e);
            ld.style.display = 'none'; em.style.display = 'block';
        }
    }

    function formatDateCN(ds) {
        if (!ds || ds === '未知') return ds;
        try {
            const d = new Date(ds);
            return d.getFullYear() + '年' + (d.getMonth() + 1) + '月' + d.getDate() + '日';
        } catch { return ds; }
    }

    // ===== Chat View (like brain-visual/chat.html) =====
    function renderChat(box, ld, em) {
        if (ld && ld.style) ld.style.display = 'none';

        // Show avatar panel if model available
        const avatarPanel = document.getElementById('avatarPanel');
        const mobileToggle = document.getElementById('mobileChatToggle');
        if (avatarPanel && agentData && agentData.avatar_model_url) {
            avatarPanel.style.display = 'flex';
            const nameEl = document.getElementById('avatarNameText');
            if (nameEl) nameEl.textContent = agentData.name || '';
        } else {
            // No avatar — hide mobile toggle button
            if (mobileToggle) mobileToggle.style.display = 'none';
        }

        box.style.display = 'flex';
        box.style.flexDirection = 'column';
        box.style.height = '100%';

        box.innerHTML =
            // Top bar: session selector + new button
            '<div style="padding:12px 16px;border-bottom:1px solid rgba(255,255,255,0.04);display:flex;align-items:center;gap:10px;background:rgba(255,255,255,0.015);">' +
            '<select id="sessionSelect" style="flex:1;background:rgba(255,255,255,0.04);border:1px solid rgba(255,255,255,0.08);border-radius:8px;padding:8px 12px;color:#e8e4df;font-family:\'DM Sans\',sans-serif;font-size:0.85em;outline:none;cursor:pointer;appearance:none;-webkit-appearance:none;background-image:url(\'data:image/svg+xml;utf8,<svg xmlns=%22http://www.w3.org/2000/svg%22 width=%2212%22 height=%2212%22 viewBox=%220 0 24 24%22 fill=%22none%22 stroke=%22%236b665e%22 stroke-width=%222%22><path d=%22M6 9l6 6 6-6%22/></svg>\');background-repeat:no-repeat;background-position:right 10px center;">' +
            '<option value="">加载中...</option></select>' +
            '<button id="newSessionBtn" style="padding:8px 14px;border-radius:8px;background:rgba(109,168,155,0.12);color:#6da89b;border:1px solid rgba(109,168,155,0.2);cursor:pointer;font-size:0.82em;font-weight:600;white-space:nowrap;transition:all 0.15s cubic-bezier(0.16,1,0.3,1);" onmouseover="this.style.background=\'rgba(109,168,155,0.2)\'" onmouseout="this.style.background=\'rgba(109,168,155,0.12)\'">+ 新对话</button>' +
            '<button id="deleteSessionBtn" style="padding:8px;border-radius:8px;background:none;color:#6b665e;border:1px solid rgba(255,255,255,0.06);cursor:pointer;font-size:0.82em;transition:all 0.15s;" onmouseover="this.style.color=\'#b8868a\';this.style.borderColor=\'rgba(184,134,138,0.3)\'" onmouseout="this.style.color=\'#6b665e\';this.style.borderColor=\'rgba(255,255,255,0.06)\'" title="删除当前会话">🗑️</button>' +
            '</div>' +
            // Messages area
            '<div id="chatMessages" style="flex:1;overflow-y:auto;padding:24px;display:flex;flex-direction:column;gap:16px;"></div>' +
            // Price + balance info (non-owner only)
            (function() {
                try {
                    const token = getToken();
                    if (!token) return '';
                    const payload = JSON.parse(atob(token.split('.')[1]));
                    const userId = payload.sub || payload.user_id;
                    if (agentData && agentData.owner_id === userId) return '';
                    const price = agentData ? (agentData.price_per_chat || 0) : 0;
                    const user = JSON.parse(localStorage.getItem('cog_user') || '{}');
                    const balance = user.atp_balance || 0;
                    if (price <= 0) return '';
                    return '<div id="chatPriceBar" style="padding:8px 20px;border-top:1px solid rgba(255,255,255,0.03);display:flex;justify-content:space-between;align-items:center;font-size:0.78em;">' +
                        '<span style="color:rgba(226,185,106,0.7);">⚡ 每次对话 ' + price + ' ATP</span>' +
                        '<span style="color:rgba(109,168,155,0.7);">余额: <span id="chatBalanceAmount">' + balance + '</span> ATP</span>' +
                        '</div>';
                } catch(e) { return ''; }
            })() +
            // Input area
            '<div style="padding:16px 20px;border-top:1px solid rgba(255,255,255,0.04);background:rgba(255,255,255,0.015);">' +
            '<div style="display:flex;gap:12px;align-items:flex-end;">' +
            '<input style="display:none" type="text" name="fakeusernameremembered"><input style="display:none" type="password" name="fakepasswordremembered">' +
            '<input id="chatInput" type="text" placeholder="输入你的问题..." autocomplete="new-password" autocorrect="off" autocapitalize="off" spellcheck="false" data-lpignore="true" data-1p-ignore="true" name="chat_q_' + Date.now() + '" style="flex:1;background:rgba(255,255,255,0.04);border:1px solid rgba(255,255,255,0.08);border-radius:12px;padding:12px 16px;color:#e8e4df;font-family:\'DM Sans\',sans-serif;font-size:0.9em;outline:none;transition:border-color 0.15s cubic-bezier(0.16,1,0.3,1);" onfocus="this.style.borderColor=\'rgba(109,168,155,0.4)\'" onblur="this.style.borderColor=\'rgba(255,255,255,0.08)\'">' +
            '<button id="chatSendBtn" style="background:rgba(109,168,155,0.9);color:#fff;border:none;border-radius:12px;padding:12px 20px;cursor:pointer;font-family:\'DM Sans\',sans-serif;font-weight:600;font-size:0.88em;transition:all 0.15s cubic-bezier(0.16,1,0.3,1);" onmouseover="this.style.background=\'rgba(109,168,155,1)\'" onmouseout="this.style.background=\'rgba(109,168,155,0.9)\'">发送</button>' +
            '</div></div>';

        document.getElementById('chatSendBtn').addEventListener('click', sendChat);
        document.getElementById('chatInput').addEventListener('keydown', e => {
            if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendChat(); }
        });
        document.getElementById('newSessionBtn').addEventListener('click', createNewSession);
        document.getElementById('deleteSessionBtn').addEventListener('click', deleteCurrentSession);
        document.getElementById('sessionSelect').addEventListener('change', (e) => {
            if (e.target.value) switchSession(e.target.value);
        });

        // Load sessions
        loadSessions();
    }

    async function loadSessions() {
        try {
            const r = await fetch('/api/agents/' + agentId + '/chat/sessions?limit=50', { headers: hdrs() });
            if (!r.ok) throw new Error();
            const data = await r.json();
            sessionsCache = data.sessions || [];
            renderSessionSelect();

            if (sessionsCache.length > 0) {
                // Load most recent session
                switchSession(sessionsCache[0].session_id);
            } else {
                // Auto-create first session
                await createNewSession();
            }
        } catch(e) {
            console.error('Load sessions failed:', e);
            // Still create a session even if load fails
            await createNewSession();
        }
    }

    function renderSessionSelect() {
        const select = document.getElementById('sessionSelect');
        if (!select) return;

        if (sessionsCache.length === 0) {
            select.innerHTML = '<option value="">暂无对话</option>';
            return;
        }

        select.innerHTML = sessionsCache.map(s => {
            const title = s.title || '新对话';
            const preview = title.length > 25 ? title.slice(0, 25) + '...' : title;
            const count = s.message_count || 0;
            const label = preview + (count > 0 ? ' (' + count + '条)' : '');
            return '<option value="' + esc(s.session_id) + '"' + (s.session_id === currentSessionId ? ' selected' : '') + '>' + esc(label) + '</option>';
        }).join('');
    }

    async function switchSession(sessionId) {
        currentSessionId = sessionId;
        renderSessionSelect();
        await loadMessages(sessionId);
    }

    async function loadMessages(sessionId) {
        const msgs = document.getElementById('chatMessages');
        if (!msgs) return;

        try {
            const r = await fetch('/api/agents/' + agentId + '/chat/sessions/' + sessionId + '/messages?limit=100', { headers: hdrs() });
            if (!r.ok) throw new Error();
            const data = await r.json();
            const messages = data.messages || [];

            if (messages.length === 0) {
                // Empty session welcome
                const agentName = agentData ? agentData.name : 'AI';
                msgs.innerHTML = '<div style="display:flex;flex-direction:column;align-items:center;justify-content:center;padding:48px 24px;gap:12px;">' +
                    '<div style="width:48px;height:48px;border-radius:50%;background:rgba(109,168,155,0.12);display:flex;align-items:center;justify-content:center;font-size:1.4em;">💬</div>' +
                    '<div style="font-family:\'DM Sans\',sans-serif;font-size:0.95em;color:rgba(232,228,223,0.5);text-align:center;line-height:1.6;">与 <span style="color:rgba(226,185,106,0.8);font-weight:600;">' + esc(agentName) + '</span> 开始对话</div>' +
                    '</div>';
                return;
            }

            // Render all messages
            const agentName = agentData ? agentData.name : 'AI';
            msgs.innerHTML = messages.map(m => {
                if (m.role === 'user') {
                    return '<div style="align-self:flex-end;max-width:75%;animation:fadeUp 0.2s cubic-bezier(0.16,1,0.3,1);">' +
                        '<div style="background:rgba(109,168,155,0.12);color:rgba(232,228,223,0.95);padding:12px 16px;border-radius:16px 16px 4px 16px;font-family:\'DM Sans\',sans-serif;font-size:0.9em;line-height:1.6;">' + esc(m.content) + '</div></div>';
                } else {
                    return '<div style="align-self:flex-start;max-width:80%;">' +
                        '<div style="font-size:0.72em;color:rgba(226,185,106,0.6);margin-bottom:6px;font-weight:600;letter-spacing:0.03em;">' + esc(agentName) + '</div>' +
                        '<div class="md-content" style="background:rgba(255,255,255,0.03);color:rgba(232,228,223,0.9);padding:14px 18px;border-radius:4px 16px 16px 16px;font-family:\'DM Sans\',sans-serif;font-size:0.9em;line-height:1.7;border:1px solid rgba(255,255,255,0.04);">' + renderMarkdown(m.content) + '</div></div>';
                }
            }).join('');

            msgs.scrollTop = msgs.scrollHeight;
        } catch(e) {
            msgs.innerHTML = '<div style="text-align:center;color:#b8868a;padding:20px;">加载消息失败</div>';
        }
    }

    async function createNewSession() {
        try {
            const r = await fetch('/api/agents/' + agentId + '/chat/sessions', {
                method: 'POST', headers: jsonHdrs(),
                body: JSON.stringify({})
            });
            if (!r.ok) throw new Error();
            const data = await r.json();

            // Add to cache and switch
            sessionsCache.unshift({
                session_id: data.session_id,
                title: data.title,
                message_count: 0,
                created_at: data.created_at,
                updated_at: data.created_at,
            });
            switchSession(data.session_id);
            toast('新对话已创建');
        } catch(e) {
            toast('创建失败', 'error');
        }
    }

    async function deleteCurrentSession() {
        if (!currentSessionId) return;
        const confirmed = await showModal({ title: '删除对话', message: '确定删除当前对话？所有消息将被永久删除。', confirmText: '删除', confirmStyle: 'danger' });
        if (!confirmed) return;

        try {
            const r = await fetch('/api/agents/' + agentId + '/chat/sessions/' + currentSessionId, {
                method: 'DELETE', headers: hdrs()
            });
            if (!r.ok) throw new Error();

            // Remove from cache
            sessionsCache = sessionsCache.filter(s => s.session_id !== currentSessionId);
            currentSessionId = null;

            if (sessionsCache.length > 0) {
                switchSession(sessionsCache[0].session_id);
            } else {
                await createNewSession();
            }
            toast('对话已删除');
        } catch(e) {
            toast('删除失败', 'error');
        }
    }

    async function sendChat() {
        const inp = document.getElementById('chatInput');
        const msgs = document.getElementById('chatMessages');
        const btn = document.getElementById('chatSendBtn');
        const q = inp.value.trim();
        if (!q) return;
        inp.value = '';
        btn.disabled = true;
        btn.textContent = '...';
        if (window.avatarCallback) window.avatarCallback('thinking');

        // Clear welcome message on first send
        if (msgs.querySelector('div[style*="flex-direction:column"]')) msgs.innerHTML = '';

        // User bubble
        msgs.innerHTML += '<div style="align-self:flex-end;max-width:75%;animation:fadeUp 0.2s cubic-bezier(0.16,1,0.3,1);">' +
            '<div style="background:rgba(109,168,155,0.12);color:rgba(232,228,223,0.95);padding:12px 16px;border-radius:16px 16px 4px 16px;font-family:\'DM Sans\',\'Inter\',sans-serif;font-size:0.9em;line-height:1.6;letter-spacing:0.01em;">' + esc(q) + '</div></div>';

        // AI bubble (will be streamed into)
        const lid = 'ld-' + Date.now();
        const agentName = agentData ? agentData.name : 'AI';
        msgs.innerHTML += '<div style="align-self:flex-start;max-width:80%;animation:fadeUp 0.25s cubic-bezier(0.16,1,0.3,1);">' +
            '<div style="font-size:0.72em;color:rgba(226,185,106,0.6);margin-bottom:6px;font-weight:600;letter-spacing:0.03em;">' + esc(agentName) + '</div>' +
            '<div id="' + lid + '" style="background:rgba(255,255,255,0.03);color:rgba(232,228,223,0.9);padding:14px 18px;border-radius:4px 16px 16px 16px;font-family:\'DM Sans\',\'Inter\',sans-serif;font-size:0.9em;line-height:1.7;border:1px solid rgba(255,255,255,0.04);min-height:24px;letter-spacing:0.01em;"><span class="typing-dots"><span>.</span><span>.</span><span>.</span></span></div></div>';
        msgs.scrollTop = msgs.scrollHeight;

        const el = document.getElementById(lid);
        let fullText = '';
        let speaking = false;
        let speakingTimer = null;

        // Mark avatar speaking, auto-stop after 400ms of no new content
        function markSpeaking() {
            if (!speaking) { speaking = true; if (window.avatarCallback) window.avatarCallback('speaking'); }
            clearTimeout(speakingTimer);
            speakingTimer = setTimeout(() => { speaking = false; if (window.avatarCallback) window.avatarCallback('idle'); }, 400);
        }

        try {
            const url = '/api/knowledge/' + getNs() + '/chat/stream?q=' + encodeURIComponent(q) + (currentSessionId ? '&session_id=' + currentSessionId : '');
            const resp = await fetch(url, { headers: hdrs() });

            if (!resp.ok) {
                const r2 = await fetch('/api/knowledge/' + getNs() + '/chat', {
                    method: 'POST', headers: jsonHdrs(),
                    body: JSON.stringify({ message: q })
                });
                const d = await r2.json();
                el.innerHTML = renderMarkdown(d.response || d.answer || '');
                el.classList.add('md-content');
                return;
            }

            const reader = resp.body.getReader();
            const decoder = new TextDecoder();
            let buffer = '';

            while (true) {
                const { done, value } = await reader.read();
                if (done) break;
                buffer += decoder.decode(value, { stream: true });
                const lines = buffer.split('\n');
                buffer = lines.pop();

                for (const line of lines) {
                    if (!line.startsWith('data: ')) continue;
                    try {
                        const ev = JSON.parse(line.slice(6));
                        if (ev.type === 'thinking') {
                            el.innerHTML = '<span style="color:rgba(226,185,106,0.6);font-size:0.85em;">💭 ' + esc((ev.text || '思考中').replace(/\.{2,}$/, '')) + '<span class="typing-dots"><span>.</span><span>.</span><span>.</span></span></span>';
                            if (window.avatarCallback) window.avatarCallback('thinking');
                        } else if (ev.type === 'content' && ev.text) {
                            markSpeaking();
                            if (!fullText) el.innerHTML = ''; // clear thinking indicator
                            fullText += ev.text;
                            el.innerHTML = renderMarkdown(fullText);
                            el.classList.add('md-content');
                            msgs.scrollTop = msgs.scrollHeight;
                        } else if (ev.type === 'error') {
                            el.innerHTML += '<br><span style="color:#b8868a;">' + esc(ev.message) + '</span>';
                        } else if (ev.type === 'done') {
                            // Trigger a suggestion poll after chat completes
                            if (typeof pollSuggestions === 'function') setTimeout(pollSuggestions, 3000);
                        }
                    } catch {}
                }
            }
        } catch (e) {
            if (!fullText) {
                el.style.color = '#b8868a';
                el.textContent = '请求失败，请重试';
            }
        } finally {
            clearTimeout(speakingTimer);
            if (window.avatarCallback) window.avatarCallback('idle');
            btn.disabled = false;
            btn.textContent = '发送';
            // 刷新余额（header + chat 页面）
            if (window.refreshAtpBalance) window.refreshAtpBalance();
            // Update session cache
            if (currentSessionId) {
                const s = sessionsCache.find(s => s.session_id === currentSessionId);
                if (s) {
                    s.message_count = (s.message_count || 0) + 2;
                    if (s.title === '新对话' && q) s.title = q.slice(0, 20);
                    s.updated_at = new Date().toISOString();
                    renderSessionSelect();
                }
            }
            msgs.scrollTop = msgs.scrollHeight;
        }
    }

    // ===== Config =====
    let personaData = null; // cached profile data

    function fillConfig() {
        const a = agentData;
        document.getElementById('configName').value = a.name || '';
        document.getElementById('configNamespace').value = a.namespace || 'default';
        document.getElementById('configStatus').value = a.status || 'active';
        document.getElementById('configType').value = a.agent_type || 'human';
        // Trigger persona fields toggle
        const isChar = (a.agent_type === 'character');
        document.getElementById('personaFields').style.display = isChar ? 'block' : 'none';
        document.getElementById('descLabel').textContent = isChar ? '背景故事' : '描述';
        document.getElementById('configDescription').placeholder = isChar ? '用第一人称描述角色背景...' : '描述这个 Agent...';
        document.getElementById('configEndpoint').value = a.endpoint_url || '';
        document.getElementById('configDescription').value = a.description || '';
        document.getElementById('configIsPublic').checked = a.is_public !== 0;
        // LLM config
        const llm = typeof a.llm_config === 'string' ? JSON.parse(a.llm_config || '{}') : (a.llm_config || {});
        document.getElementById('configLlmProvider').value = llm.provider || '';
        document.getElementById('configLlmModel').value = llm.model || '';
        document.getElementById('configLlmKey').value = '';
        document.getElementById('configLlmKey').placeholder = llm.has_key ? '••••••• (already set)' : 'sk-...';
        document.getElementById('configLlmEndpoint').value = llm.endpoint || '';
        toggleLlmEndpoint();
        // IM config
        const im = typeof a.im_config === 'string' ? JSON.parse(a.im_config || '{}') : (a.im_config || {});
        const tg = im.telegram || {};
        document.getElementById('configTgToken').value = tg.bot_token || '';
        document.getElementById('configTgChatId').value = tg.chat_id || '';
        // Chat config
        const chatCfg = typeof a.chat_config === 'string' ? JSON.parse(a.chat_config || '{}') : (a.chat_config || {});
        document.getElementById('configVoiceProfile').value = chatCfg.voice_profile || '';
        document.getElementById('configTone').value = chatCfg.tone || '';
        document.getElementById('configMetaThinking').checked = !!chatCfg.enable_meta_thinking;
        document.getElementById('configReasoningChain').checked = !!chatCfg.enable_reasoning_chain;
        document.getElementById('configContradiction').checked = !!chatCfg.enable_contradiction;
        document.getElementById('configWebSearch').checked = !!chatCfg.enable_web_search;
        document.getElementById('configRetrievalTopK').value = chatCfg.retrieval_top_k || '5';
        document.getElementById('configRetrievalMinScore').value = chatCfg.retrieval_min_score || '0.5';
        document.getElementById('configMaxTokens').value = chatCfg.max_tokens || '2000';
        document.getElementById('configContextRounds').value = chatCfg.context_rounds || '10';
        // Load persona
        loadPersona();
        loadPresetAvatars();
    }

    async function loadPersona() {
        const ns = getNs();
        const isChar = (agentData && agentData.agent_type === 'character');
        // Sync UI state
        document.getElementById('personaFields').style.display = isChar ? 'block' : 'none';
        document.getElementById('descLabel').textContent = isChar ? '背景故事' : '描述';
        if (isChar) document.getElementById('configDescription').placeholder = '用第一人称描述角色背景...';

        try {
            const r = await fetch('/api/knowledge/' + ns + '/profile', { headers: hdrs() });
            if (!r.ok) return;
            personaData = await r.json();
            // DB agent_type is authoritative, profile.type may be stale
            const pt = isChar ? 'character' : (personaData.type || 'human');
            document.getElementById('configType').value = pt;
            document.getElementById('personaFields').style.display = pt === 'character' ? 'block' : 'none';
            document.getElementById('descLabel').textContent = pt === 'character' ? '背景故事' : '描述';
            if (pt === 'character') {
                document.getElementById('configDescription').placeholder = '用第一人称描述角色背景...';
                const p = personaData.persona || {};
                const id = personaData.identity || {};
                // 背景故事回填到 configDescription（如果当前为空，优先用 persona.background）
                if (!document.getElementById('configDescription').value && p.background) {
                    document.getElementById('configDescription').value = p.background;
                }
                document.getElementById('configPersonaTitle').value = id.title || '';
                document.getElementById('configPersonaEra').value = p.era || '';
                document.getElementById('configPersonaTraits').value = (p.traits || []).join(', ');
                document.getElementById('configPersonaStyle').value = p.speaking_style || '';
                document.getElementById('configPersonaBeliefs').value = (p.core_beliefs || []).join('\n');
                document.getElementById('configPersonaGreeting').value = p.greeting || '';
            }
        } catch (e) { console.error('Load persona failed:', e); }
    }

    function toggleLlmEndpoint() {
        const provider = document.getElementById('configLlmProvider').value;
        document.getElementById('configLlmEndpointGroup').style.display = provider === 'custom' ? 'block' : 'none';
    }

    async function saveConfig() {
        if (!agentData) return;
        // Build LLM config
        const llmConfig = {
            provider: document.getElementById('configLlmProvider').value || null,
            model: document.getElementById('configLlmModel').value || null,
            endpoint: document.getElementById('configLlmEndpoint').value || null
        };
        const llmKey = document.getElementById('configLlmKey').value;
        if (llmKey) llmConfig.api_key = llmKey;  // Only send if changed
        // Build chat_config
        const chatConfig = {
            voice_profile: document.getElementById('configVoiceProfile').value.trim(),
            tone: document.getElementById('configTone').value,
            enable_meta_thinking: document.getElementById('configMetaThinking').checked,
            enable_reasoning_chain: document.getElementById('configReasoningChain').checked,
            enable_contradiction: document.getElementById('configContradiction').checked,
            enable_web_search: document.getElementById('configWebSearch').checked,
            retrieval_top_k: parseInt(document.getElementById('configRetrievalTopK').value) || 5,
            retrieval_min_score: parseFloat(document.getElementById('configRetrievalMinScore').value) || 0.5,
            max_tokens: parseInt(document.getElementById('configMaxTokens').value) || 2000,
            context_rounds: parseInt(document.getElementById('configContextRounds').value) || 10,
        };
        try {
            const r = await fetch('/api/agents/' + agentId, {
                method: 'PUT', headers: jsonHdrs(),
                body: JSON.stringify({
                    name: document.getElementById('configName').value,
                    namespace: document.getElementById('configNamespace').value,
                    status: document.getElementById('configStatus').value,
                    agent_type: document.getElementById('configType').value,
                    endpoint_url: document.getElementById('configEndpoint').value,
                    description: document.getElementById('configDescription').value,
                    is_public: document.getElementById('configIsPublic').checked ? 1 : 0,
                    llm_config: JSON.stringify(llmConfig),
                    im_config: JSON.stringify({ telegram: { bot_token: document.getElementById('configTgToken').value, chat_id: document.getElementById('configTgChatId').value } }),
                    chat_config: JSON.stringify(chatConfig)
                })
            });
            if (!r.ok) throw new Error();
            agentData = await r.json();
            renderHeader(); renderOverview();

            // Save persona profile if character type
            await savePersona();

            toast('配置已保存');
        } catch { toast('保存失败', 'error'); }
    }

    async function savePersona() {
        const ns = getNs();
        const pType = document.getElementById('configType').value;
        const payload = {};
        const nameVal = document.getElementById('configName').value.trim();
        const descVal = document.getElementById('configDescription').value.trim();

        if (pType === 'character') {
            payload.identity = {
                name: nameVal,
                title: document.getElementById('configPersonaTitle').value.trim(),
                bio: descVal,
            };
            payload.persona = {
                based_on: nameVal ? [nameVal] : [],
                era: document.getElementById('configPersonaEra').value.trim(),
                background: descVal,  // 背景故事 = 描述字段
                traits: document.getElementById('configPersonaTraits').value.split(',').map(s => s.trim()).filter(Boolean),
                speaking_style: document.getElementById('configPersonaStyle').value.trim(),
                core_beliefs: document.getElementById('configPersonaBeliefs').value.split('\n').map(s => s.trim()).filter(Boolean),
                greeting: document.getElementById('configPersonaGreeting').value.trim(),
            };
        } else {
            payload.identity = { name: nameVal, bio: descVal };
        }

        // Sync LLM config to profile
        const llmProvider = document.getElementById('configLlmProvider').value;
        const llmModel = document.getElementById('configLlmModel').value;
        const llmKey = document.getElementById('configLlmKey').value;
        const llmEndpoint = document.getElementById('configLlmEndpoint').value;
        if (llmProvider || llmModel) {
            payload.llm = { provider: llmProvider || undefined, model: llmModel || undefined, endpoint: llmEndpoint || undefined };
            if (llmKey) payload.llm.api_key = llmKey;
        }

        try {
            await fetch('/api/knowledge/' + ns + '/profile', {
                method: 'PUT', headers: jsonHdrs(),
                body: JSON.stringify(payload)
            });
        } catch (e) { console.error('Save persona failed:', e); }
    }

    function resetConfig() { if (agentData) fillConfig(); }

    async function deleteAgent() {
        const confirmed = await showModal({ title: '删除 Agent', message: '确定要删除该 Agent 吗？此操作不可撤销。', confirmText: '删除', confirmStyle: 'danger' });
        if (!confirmed) return;
        try {
            const r = await fetch('/api/agents/' + agentId, { method: 'DELETE', headers: hdrs() });
            if (!r.ok) throw new Error();
            location.href = '/dashboard';
        } catch { toast('删除失败', 'error'); }
    }

    // ===== Helpers =====
    function timeAgo(ds) {
        const d = Date.now() - new Date(ds).getTime();
        const m = Math.floor(d / 60000);
        if (m < 1) return '刚刚';
        if (m < 60) return m + '分钟前';
        const h = Math.floor(m / 60);
        if (h < 24) return h + '小时前';
        return Math.floor(h / 24) + '天前';
    }

    function toast(msg, type) {
        const t = document.createElement('div');
        t.style.cssText = 'position:fixed;bottom:24px;right:24px;z-index:9999;padding:12px 20px;border-radius:10px;color:#fff;font-size:0.88em;font-weight:500;box-shadow:0 4px 12px rgba(0,0,0,0.3);background:' + (type === 'error' ? 'rgba(184,134,138,0.95)' : 'rgba(109,168,155,0.95)') + ';';
        t.textContent = msg;
        document.body.appendChild(t);
        setTimeout(() => { t.style.opacity = '0'; t.style.transition = 'opacity 0.3s'; setTimeout(() => t.remove(), 300); }, 3000);
    }

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
    else init();

    async function startResearch(depth) {
        const ns = getNs();
        const refInput = document.getElementById('researchRefNames');
        const statusEl = document.getElementById('researchStatus');
        const names = refInput.value.split(',').map(s => s.trim()).filter(Boolean);
        if (!names.length) { toast('请输入参考人物名称', 'error'); return; }

        const btns = document.querySelectorAll('#personaFields button[onclick*="startResearch"]');
        btns.forEach(b => b.disabled = true);
        statusEl.style.display = 'block';
        statusEl.style.color = 'var(--text-muted)';
        statusEl.textContent = '🔍 提交调研任务...';

        try {
            // 提交任务（立即返回）
            const r = await fetch('/api/knowledge/' + ns + '/research-character', {
                method: 'POST', headers: jsonHdrs(),
                body: JSON.stringify({ reference_names: names, depth: depth }),
                timeout: 15000,
            });
            const d = await r.json();

            if (!r.ok) {
                statusEl.style.color = '#b8868a';
                statusEl.textContent = '❌ ' + (d.detail || '提交失败');
                btns.forEach(b => b.disabled = false);
                return;
            }

            const taskId = d.task_id;
            statusEl.textContent = '🔍 ' + (d.progress || '任务已启动，后台执行中...');

            // 轮询进度
            const pollInterval = setInterval(async () => {
                try {
                    const sr = await fetch('/api/knowledge/' + ns + '/research-character/status?task_id=' + taskId, { headers: hdrs() });
                    if (!sr.ok) { clearInterval(pollInterval); btns.forEach(b => b.disabled = false); return; }
                    const st = await sr.json();

                    statusEl.textContent = st.progress || st.status;

                    if (st.status === 'completed') {
                        clearInterval(pollInterval);
                        const res = st.result || {};
                        statusEl.style.color = '#6da89b';
                        statusEl.innerHTML = '✅ ' + esc(res.message || '调研完成') +
                            (res.persona_summary?.traits ? '<br><span style="font-size:0.85em;color:var(--text-muted);">特征: ' + esc(res.persona_summary.traits.join(', ')) + '</span>' : '');
                        toast('调研完成，Persona 和知识已更新');
                        loadPersona();
                        btns.forEach(b => b.disabled = false);
                    } else if (st.status === 'failed') {
                        clearInterval(pollInterval);
                        statusEl.style.color = '#b8868a';
                        statusEl.textContent = '❌ ' + (st.error || '调研失败');
                        btns.forEach(b => b.disabled = false);
                    }
                } catch (e) {
                    // 网络错误继续轮询
                }
            }, 3000); // 每 3 秒查一次

            // 5 分钟超时保护
            setTimeout(() => {
                clearInterval(pollInterval);
                btns.forEach(b => b.disabled = false);
                if (statusEl.style.color !== 'rgb(109, 168, 155)') {
                    statusEl.style.color = '#d4a574';
                    statusEl.textContent = '⏱️ 调研仍在后台进行，可稍后刷新页面查看结果';
                }
            }, 300000);

        } catch (e) {
            statusEl.style.color = '#b8868a';
            statusEl.textContent = '❌ 请求失败: ' + (e.message || '网络错误');
            btns.forEach(b => b.disabled = false);
        }
    }

    // ===== Detail Panel =====
    let _graphNodes = []; // cache for graph data
    let _graphEdges = [];

    function showDetail(item) {
        const panel = document.getElementById('knowledgeDetailPanel');
        const overlay = document.getElementById('detailPanelOverlay');
        const content = document.getElementById('detailPanelContent');
        const title = document.getElementById('detailPanelTitle');

        const typeColors = {'观点':'#6da89b','事件':'#d4a574','资讯':'#7eb8d4','决策':'#b8868a','情绪':'#c4956a'};
        const color = typeColors[item.type] || '#6da89b';

        title.textContent = item.type || '详情';

        // Find related edges
        let relHtml = '';
        if (_graphEdges.length && item.id) {
            const related = _graphEdges.filter(e => e.source === item.id || e.target === item.id ||
                (e.source && e.source.id === item.id) || (e.target && e.target.id === item.id));
            if (related.length) {
                relHtml = '<div style="margin-top:20px;"><div style="font-size:0.72em;color:#6b665e;text-transform:uppercase;letter-spacing:0.06em;margin-bottom:8px;">关联 (' + related.length + ')</div>';
                related.forEach(e => {
                    const otherId = (e.source === item.id || (e.source && e.source.id === item.id))
                        ? (e.target.id || e.target) : (e.source.id || e.source);
                    const otherNode = _graphNodes.find(n => n.id === otherId);
                    const otherLabel = otherNode ? (otherNode.full_content || otherNode.label || '').slice(0, 60) : otherId.slice(0, 8);
                    relHtml += '<div style="padding:8px 12px;background:rgba(255,255,255,0.02);border-radius:6px;margin-bottom:4px;font-size:0.85em;border-left:2px solid rgba(109,168,155,0.3);">' +
                        '<span style="color:#6b665e;">' + esc(e.type || 'RELATES_TO') + '</span> → ' +
                        '<span style="color:#a8a299;">' + esc(otherLabel) + '</span></div>';
                });
                relHtml += '</div>';
            }
        }

        const actionHtml = '<div style="margin-top:24px;padding-top:16px;border-top:1px solid rgba(255,255,255,0.06);display:flex;gap:10px;flex-wrap:wrap;">' +
            '<button onclick="AgentDetail.editFact(\'' + esc(item.full_id || item.id) + '\')" style="padding:8px 16px;border-radius:6px;background:rgba(109,168,155,0.15);color:#6da89b;border:1px solid rgba(109,168,155,0.3);cursor:pointer;font-size:0.85em;">✏️ 编辑</button>' +
            '<button onclick="AgentDetail.togglePrivacy(\'' + esc(item.full_id || item.id) + '\')" style="padding:8px 16px;border-radius:6px;background:rgba(226,185,106,0.15);color:#e2b96a;border:1px solid rgba(226,185,106,0.3);cursor:pointer;font-size:0.85em;" id="privacyBtn-' + esc(item.full_id || item.id) + '">🔒 隐私设置</button>' +
            '<button onclick="AgentDetail.deleteFact(\'' + esc(item.full_id || item.id) + '\')" style="padding:8px 16px;border-radius:6px;background:rgba(184,134,138,0.15);color:#b8868a;border:1px solid rgba(184,134,138,0.3);cursor:pointer;font-size:0.85em;">🗑️ 删除</button>' +
            '</div>';

        content.innerHTML =
            '<div style="margin-bottom:16px;"><span style="background:' + color + '22;color:' + color + ';padding:3px 10px;border-radius:4px;font-size:0.78em;font-weight:600;">' + esc(item.type || '') + '</span></div>' +
            '<div style="color:#e8e4df;line-height:1.8;font-size:0.92em;white-space:pre-wrap;word-break:break-word;">' + esc(item.full_content || item.label || item.content || '') + '</div>' +
            '<div style="margin-top:16px;padding-top:12px;border-top:1px solid rgba(255,255,255,0.06);font-size:0.8em;color:#6b665e;line-height:1.8;">' +
            'ID: ' + esc((item.id || '').slice(0, 8)) + '<br>' +
            '时间: ' + esc(item.timestamp || item.created_at || '未知') +
            (item.degree !== undefined ? '<br>连接度: ' + item.degree : '') +
            (item.context ? '<br>情境: ' + esc(item.context) : '') +
            '</div>' +
            relHtml +
            actionHtml;

        panel.style.display = 'flex';
        overlay.style.display = 'block';
        requestAnimationFrame(() => { panel.style.transform = 'translateX(0)'; });
    }

    function closeDetail() {
        const panel = document.getElementById('knowledgeDetailPanel');
        const overlay = document.getElementById('detailPanelOverlay');
        panel.style.transform = 'translateX(100%)';
        setTimeout(() => { panel.style.display = 'none'; overlay.style.display = 'none'; }, 250);
    }

    // ===== Fact CRUD & Privacy =====
    async function editFact(factId) {
        const ns = getNs();
        try {
            const r = await fetch('/api/knowledge/' + ns + '/fact/' + factId, { headers: hdrs() });
            if (!r.ok) throw new Error();
            const fact = await r.json();

            const content = document.getElementById('detailPanelContent');
            content.innerHTML =
                '<div style="margin-bottom:16px;"><label style="color:#6b665e;font-size:0.8em;text-transform:uppercase;letter-spacing:0.04em;">内容类型</label>' +
                '<select id="editFactType" style="width:100%;margin-top:6px;padding:10px;background:rgba(255,255,255,0.04);border:1px solid rgba(255,255,255,0.1);border-radius:6px;color:#e8e4df;font-size:0.9em;">' +
                ['观点','事件','资讯','决策','情绪'].map(t => '<option value="' + t + '"' + (t === fact.content_type ? ' selected' : '') + '>' + t + '</option>').join('') +
                '</select></div>' +
                '<div style="margin-bottom:16px;"><label style="color:#6b665e;font-size:0.8em;text-transform:uppercase;letter-spacing:0.04em;">内容</label>' +
                '<textarea id="editFactSummary" rows="8" style="width:100%;margin-top:6px;padding:12px;background:rgba(255,255,255,0.04);border:1px solid rgba(255,255,255,0.1);border-radius:6px;color:#e8e4df;font-size:0.9em;line-height:1.6;resize:vertical;">' + esc(fact.summary || '') + '</textarea></div>' +
                '<div style="display:flex;gap:10px;">' +
                '<button onclick="AgentDetail.saveFactEdit(\'' + esc(factId) + '\')" style="flex:1;padding:10px;background:#6da89b;color:#fff;border:none;border-radius:6px;cursor:pointer;font-weight:600;">保存</button>' +
                '<button onclick="AgentDetail.cancelEdit()" style="flex:1;padding:10px;background:rgba(255,255,255,0.06);color:#a8a299;border:1px solid rgba(255,255,255,0.1);border-radius:6px;cursor:pointer;">取消</button>' +
                '</div>';

            document.getElementById('detailPanelTitle').textContent = '编辑知识条目';
        } catch(e) { toast('加载失败', 'error'); }
    }

    async function saveFactEdit(factId) {
        const ns = getNs();
        const summary = document.getElementById('editFactSummary').value.trim();
        const contentType = document.getElementById('editFactType').value;
        if (!summary) { toast('内容不能为空', 'error'); return; }
        try {
            const r = await fetch('/api/knowledge/' + ns + '/fact/' + factId, {
                method: 'PUT', headers: jsonHdrs(),
                body: JSON.stringify({ summary, content_type: contentType })
            });
            if (!r.ok) throw new Error();
            toast('已更新');
            closeDetail();
            if (currentView) loadKnowledgeView(currentView);
        } catch { toast('保存失败', 'error'); }
    }

    function cancelEdit() {
        closeDetail();
    }

    async function deleteFact(factId) {
        const confirmed = await showModal({ title: '删除知识条目', message: '确定删除此知识条目？此操作不可撤销，将同时删除图谱中的关联。', confirmText: '删除', confirmStyle: 'danger' });
        if (!confirmed) return;
        const ns = getNs();
        try {
            const r = await fetch('/api/knowledge/' + ns + '/fact/' + factId, {
                method: 'DELETE', headers: hdrs()
            });
            if (!r.ok) throw new Error();
            toast('已删除');
            closeDetail();
            if (currentView) loadKnowledgeView(currentView);
        } catch { toast('删除失败', 'error'); }
    }

    async function togglePrivacy(factId) {
        const ns = getNs();
        try {
            const r = await fetch('/api/knowledge/' + ns + '/privacy/' + factId, { headers: hdrs() });
            if (!r.ok) throw new Error();
            const status = await r.json();
            const currentPrivate = status.is_private;
            const newPrivate = !currentPrivate;
            const action = newPrivate ? '设为私有（Token 访客不可见）' : '设为公开';
            const confirmed = await showModal({ title: '隐私设置', message: '确定要将此条目' + action + '？' });
            if (!confirmed) return;

            const r2 = await fetch('/api/knowledge/' + ns + '/privacy', {
                method: 'POST', headers: jsonHdrs(),
                body: JSON.stringify({ entity_id: factId, is_private: newPrivate, cascade: false })
            });
            if (!r2.ok) throw new Error();
            toast(newPrivate ? '已设为私有' : '已设为公开');
            if (currentView) loadKnowledgeView(currentView);
        } catch { toast('设置失败', 'error'); }
    }

    // ===== Live2D Avatar =====
    async function initLive2D() {
        if (!agentData || !agentData.avatar_model_url) return;

        const panel = document.getElementById('avatarPanel');
        const canvas = document.getElementById('avatarCanvas');
        if (!panel || !canvas) return;

        // Load Cubism 4 Core + pixi.js + pixi-live2d-display (order matters!)
        if (!window.Live2DCubismCore) {
            await loadScript('https://cubism.live2d.com/sdk-web/cubismcore/live2dcubismcore.min.js');
        }
        if (!window.PIXI) {
            await loadScript('https://cdn.jsdelivr.net/npm/pixi.js@7.3.3/dist/pixi.min.js');
        }
        if (!window.PIXI.live2d) {
            await loadScript('https://cdn.jsdelivr.net/npm/pixi-live2d-display@0.4.0/dist/cubism4.min.js');
        }

        try {
            const panelW = panel.clientWidth || 320;
            const panelH = panel.clientHeight || 500;

            const app = new PIXI.Application({
                view: canvas,
                autoStart: true,
                backgroundAlpha: 0,
                width: panelW,
                height: panelH,
                antialias: true,
            });

            const model = await PIXI.live2d.Live2DModel.from(agentData.avatar_model_url);

            // Scale to fill panel nicely
            const scale = Math.min(panelH / model.height, panelW / model.width) * 0.85;
            model.scale.set(scale);
            model.anchor.set(0.5, 0.5);
            model.x = panelW / 2;
            model.y = panelH / 2;

            app.stage.addChild(model);

            // Interactive — eyes follow mouse
            model.interactive = true;

            // Start idle motion
            try {
                model.motion('Idle', 0, PIXI.live2d.MotionPriority.IDLE);
            } catch(e) {}

            // Store reference for chat integration
            window._live2dModel = model;
            window._live2dApp = app;

            // Set up avatar state callback
            const statusEl = document.getElementById('avatarStatusText');
            window.avatarCallback = function(state) {
                if (!window._live2dModel) return;
                const m = window._live2dModel;
                const cm = m.internalModel?.coreModel;
                if (!cm) return;

                switch(state) {
                    case 'thinking':
                        stopMouthAnimation();
                        if (statusEl) { statusEl.textContent = '● 思考中...'; statusEl.style.color = 'rgba(226,185,106,0.5)'; }
                        break;
                    case 'speaking':
                        startMouthAnimation();
                        if (statusEl) { statusEl.textContent = '● 回答中'; statusEl.style.color = 'rgba(109,168,155,0.6)'; }
                        break;
                    case 'idle':
                        stopMouthAnimation();
                        try { m.motion('Idle', 0, PIXI.live2d.MotionPriority.IDLE); } catch(e) {}
                        if (statusEl) { statusEl.textContent = '● 在线'; statusEl.style.color = 'rgba(255,255,255,0.35)'; }
                        break;
                }
            };

        } catch(e) {
            console.error('Live2D init error:', e);
            panel.style.display = 'none';
        }
    }

    let _mouthAnimFrame = null;
    let _mouthTarget = 0;
    let _mouthCurrent = 0;

    function startMouthAnimation() {
        const model = window._live2dModel;
        if (!model) return;

        stopMouthAnimation();
        let t = 0;

        function animate() {
            t += 1;
            // Generate natural-looking mouth movement
            _mouthTarget = (Math.sin(t * 0.25) * 0.35 + Math.sin(t * 0.41) * 0.25 + 0.5) * 0.9;
            // Smooth interpolation
            _mouthCurrent += (_mouthTarget - _mouthCurrent) * 0.3;
            const val = Math.max(0, Math.min(1, _mouthCurrent));

            // Method 1: Try direct coreModel parameter by index
            try {
                const cm = model.internalModel.coreModel;
                // Find ParamMouthOpenY index
                if (!window._mouthParamIdx && window._mouthParamIdx !== 0) {
                    const count = cm.getParameterCount();
                    for (let i = 0; i < count; i++) {
                        if (cm.getParameterId(i) === 'ParamMouthOpenY') {
                            window._mouthParamIdx = i;
                            break;
                        }
                    }
                }
                if (window._mouthParamIdx !== undefined) {
                    cm.setParameterValueByIndex(window._mouthParamIdx, val);
                }
            } catch(e) {}

            // Method 2: Also try the lipSync property (some builds support this)
            try {
                if (model.internalModel.lipSync !== undefined) {
                    model.internalModel.lipSync = true;
                    model.internalModel.lipSyncValue = val;
                }
            } catch(e) {}

            _mouthAnimFrame = requestAnimationFrame(animate);
        }
        animate();
    }

    function stopMouthAnimation() {
        if (_mouthAnimFrame) {
            cancelAnimationFrame(_mouthAnimFrame);
            _mouthAnimFrame = null;
        }
        _mouthTarget = 0;
        _mouthCurrent = 0;
        // Reset mouth to closed
        try {
            const model = window._live2dModel;
            if (model) {
                const cm = model.internalModel.coreModel;
                if (window._mouthParamIdx !== undefined) {
                    cm.setParameterValueByIndex(window._mouthParamIdx, 0);
                }
                if (model.internalModel.lipSync !== undefined) {
                    model.internalModel.lipSyncValue = 0;
                }
            }
        } catch(e) {}
    }

    function loadScript(src) {
        return new Promise((resolve, reject) => {
            const s = document.createElement('script');
            s.src = src;
            s.onload = resolve;
            s.onerror = reject;
            document.head.appendChild(s);
        });
    }

    // ===== Avatar Model Management =====

    async function loadPresetAvatars() {
        const grid = document.getElementById('presetAvatarGrid');
        if (!grid) return;

        try {
            const r = await fetch('/api/avatars/presets');
            if (!r.ok) throw new Error();
            const data = await r.json();
            const presets = data.presets || [];

            const currentUrl = agentData ? agentData.avatar_model_url : '';

            grid.innerHTML = presets.map(p => {
                const isSelected = currentUrl && currentUrl.includes('/presets/' + p.id + '/');
                return '<div onclick="AgentDetail.selectPreset(\'' + esc(p.id) + '\')" style="' +
                    'padding:16px 12px;border-radius:10px;text-align:center;cursor:pointer;transition:all 0.15s cubic-bezier(0.16,1,0.3,1);' +
                    'background:' + (isSelected ? 'rgba(109,168,155,0.15)' : 'rgba(255,255,255,0.03)') + ';' +
                    'border:1px solid ' + (isSelected ? 'rgba(109,168,155,0.4)' : 'rgba(255,255,255,0.06)') + ';' +
                    '" onmouseover="if(!this.dataset.selected)this.style.background=\'rgba(255,255,255,0.06)\'" ' +
                    'onmouseout="if(!this.dataset.selected)this.style.background=\'rgba(255,255,255,0.03)\'" ' +
                    'data-selected="' + (isSelected ? '1' : '') + '">' +
                    '<div style="font-size:1.6em;margin-bottom:8px;">' +
                        (p.style === '萌系' ? '🐱' : p.style === 'Q版' ? '🍙' : p.style === '简约' ? '👤' : p.style === '知性' ? '👩‍💼' : p.style === '甜美' ? '🌸' : '🎭') +
                    '</div>' +
                    '<div style="font-size:0.85em;color:' + (isSelected ? '#6da89b' : '#e8e4df') + ';font-weight:600;">' + esc(p.name) + '</div>' +
                    '<div style="font-size:0.72em;color:#6b665e;margin-top:4px;">' + esc(p.description) + '</div>' +
                    (isSelected ? '<div style="font-size:0.7em;color:#6da89b;margin-top:6px;">✓ 当前使用</div>' : '') +
                    '</div>';
            }).join('');
        } catch(e) {
            grid.innerHTML = '<div style="color:#6b665e;font-size:0.85em;padding:12px;">加载预置模型失败</div>';
        }

        // Update current label
        updateAvatarLabel();
    }

    function updateAvatarLabel() {
        const label = document.getElementById('currentAvatarLabel');
        if (!label) return;
        if (agentData && agentData.avatar_model_url) {
            label.textContent = '当前: ' + agentData.avatar_model_url.split('/').pop();
        } else {
            label.textContent = '未设置虚拟形象';
        }
    }

    async function selectPreset(presetId) {
        try {
            const r = await fetch('/api/agents/' + agentId + '/avatar', {
                method: 'PUT', headers: jsonHdrs(),
                body: JSON.stringify({ preset: presetId })
            });
            if (!r.ok) throw new Error();
            const data = await r.json();
            agentData.avatar_model_url = data.avatar_model_url;
            loadPresetAvatars();
            toast('虚拟形象已更新，切换到对话 Tab 查看效果');
        } catch(e) { toast('设置失败', 'error'); }
    }

    async function uploadAvatar(input) {
        const file = input.files[0];
        if (!file) return;

        const status = document.getElementById('avatarUploadStatus');
        status.textContent = '上传中...';
        status.style.color = '#e2b96a';

        try {
            const formData = new FormData();
            formData.append('file', file);

            const r = await fetch('/api/agents/' + agentId + '/avatar/upload', {
                method: 'POST',
                headers: { 'Authorization': 'Bearer ' + getToken() },
                body: formData
            });

            if (!r.ok) {
                const err = await r.json();
                throw new Error(err.detail || '上传失败');
            }

            const data = await r.json();
            agentData.avatar_model_url = data.avatar_model_url;
            status.textContent = '✅ 上传成功 (' + (data.files_count || 0) + ' 个文件)';
            status.style.color = '#6da89b';
            loadPresetAvatars();
            toast('自定义模型已上传');
        } catch(e) {
            status.textContent = '❌ ' + (e.message || '上传失败');
            status.style.color = '#b8868a';
        }

        input.value = '';
    }

    // ===== Knowledge Suggestion Notification System =====
    let kePollingTimer = null;
    let keSuggestions = [];
    let kePanelOpen = false;

    function isCurrentUserOwner() {
        try {
            const token = getToken();
            if (!token || !agentData) return false;
            const payload = JSON.parse(atob(token.split('.')[1]));
            const userId = payload.sub || payload.user_id;
            return agentData.owner_id && agentData.owner_id === userId;
        } catch { return false; }
    }

    function startKEPolling() {
        if (kePollingTimer) return;
        pollSuggestions(); // immediate first poll
        kePollingTimer = setInterval(pollSuggestions, 15000);
    }

    function stopKEPolling() {
        if (kePollingTimer) { clearInterval(kePollingTimer); kePollingTimer = null; }
    }

    async function pollSuggestions() {
        if (!agentData || !isCurrentUserOwner()) return;
        try {
            const resp = await fetch('/api/knowledge/' + getNs() + '/suggestions?status=pending', {
                headers: { 'Authorization': 'Bearer ' + getToken() }
            });
            if (!resp.ok) return;
            const data = await resp.json();
            keSuggestions = data.suggestions || [];
            updateKEBadge();
        } catch {}
    }

    function ensureKENotification() {
        if (document.getElementById('ke-notification')) return;
        const notif = document.createElement('div');
        notif.id = 'ke-notification';
        notif.style.cssText = 'display:none;position:absolute;top:12px;left:50%;transform:translateX(-50%);z-index:100;';
        notif.innerHTML =
            '<div id="ke-badge" style="cursor:pointer;display:inline-flex;align-items:center;gap:6px;padding:6px 14px;background:rgba(212,160,84,0.12);border:1px solid rgba(212,160,84,0.25);border-radius:20px;font-size:0.8em;color:rgba(226,185,106,0.9);transition:all 0.3s;backdrop-filter:blur(8px);">' +
            '🧠 <span id="ke-count">0</span>条新知识</div>';
        notif.querySelector('#ke-badge').addEventListener('click', toggleKEPanel);

        // Place in avatar panel if avatar present, otherwise in chatMessages parent
        const avatarPanel = document.getElementById('avatarPanel');
        if (avatarPanel && agentData && agentData.avatar_model_url) {
            avatarPanel.appendChild(notif);
            notif.style.top = '12px';
        } else {
            const chatArea = document.getElementById('chatArea');
            if (chatArea) {
                chatArea.style.position = 'relative';
                chatArea.appendChild(notif);
                notif.style.top = '56px'; // below session bar
            }
        }
    }

    function updateKEBadge() {
        ensureKENotification();
        const notif = document.getElementById('ke-notification');
        const count = document.getElementById('ke-count');
        if (!notif) return;
        if (keSuggestions.length > 0) {
            count.textContent = keSuggestions.length;
            notif.style.display = 'block';
        } else {
            notif.style.display = 'none';
            if (kePanelOpen) closeKEPanel();
        }
    }

    function toggleKEPanel() {
        if (kePanelOpen) { closeKEPanel(); } else { openKEPanel(); }
    }

    function openKEPanel() {
        closeKEPanel(); // remove existing
        kePanelOpen = true;
        const panel = document.createElement('div');
        panel.id = 'ke-panel';
        panel.style.cssText = 'position:fixed;top:50%;left:50%;transform:translate(-50%,-50%);z-index:1001;width:min(560px,92vw);max-height:70vh;overflow-y:auto;background:rgba(22,22,30,0.98);border:1px solid rgba(212,160,84,0.2);border-radius:14px;backdrop-filter:blur(16px);box-shadow:0 12px 48px rgba(0,0,0,0.5);';

        // Add backdrop overlay
        const overlay = document.createElement('div');
        overlay.id = 'ke-overlay';
        overlay.style.cssText = 'position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.4);z-index:1000;';
        overlay.addEventListener('click', closeKEPanel);
        document.body.appendChild(overlay);
        document.body.appendChild(panel);

        renderKEPanel();

        // Close on outside click
        setTimeout(() => {
            document.addEventListener('click', _kePanelOutsideClick);
        }, 100);
    }

    function _kePanelOutsideClick(e) {
        const panel = document.getElementById('ke-panel');
        const badge = document.getElementById('ke-badge');
        if (panel && !panel.contains(e.target) && badge && !badge.contains(e.target)) {
            closeKEPanel();
        }
    }

    function closeKEPanel() {
        kePanelOpen = false;
        const panel = document.getElementById('ke-panel');
        if (panel) panel.remove();
        const overlay = document.getElementById('ke-overlay');
        if (overlay) overlay.remove();
        document.removeEventListener('click', _kePanelOutsideClick);
    }

    function renderKEPanel() {
        const panel = document.getElementById('ke-panel');
        if (!panel) return;

        if (keSuggestions.length === 0) {
            panel.innerHTML = '<div style="padding:20px;text-align:center;color:rgba(168,162,153,0.6);font-size:0.85em;">暂无待处理知识</div>';
            return;
        }

        let html = '<div style="padding:12px 16px;display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid rgba(255,255,255,0.06);">' +
            '<span style="font-size:0.85em;color:rgba(226,185,106,0.9);font-weight:600;">🧠 待处理知识</span>' +
            '<button id="ke-dismiss-all" style="font-size:0.72em;color:rgba(168,162,153,0.6);background:none;border:none;cursor:pointer;padding:4px 8px;border-radius:4px;transition:all 0.15s;" onmouseover="this.style.color=\'rgba(184,134,138,0.8)\';this.style.background=\'rgba(184,134,138,0.1)\'" onmouseout="this.style.color=\'rgba(168,162,153,0.6)\';this.style.background=\'none\'">全部忽略</button></div>';

        keSuggestions.forEach(s => {
            const ctype = s.content_type || '事实';
            const summary = s.summary || '';
            const reason = s.reason || '';
            html += '<div style="padding:12px 16px;border-bottom:1px solid rgba(255,255,255,0.03);">' +
                '<div style="display:flex;align-items:center;gap:6px;margin-bottom:6px;">' +
                '<span style="font-size:0.68em;padding:1px 6px;border-radius:3px;background:rgba(109,168,155,0.1);color:rgba(109,168,155,0.7);">' + esc(ctype) + '</span>' +
                '</div>' +
                '<div style="font-size:0.82em;color:rgba(232,228,223,0.8);line-height:1.5;margin-bottom:4px;word-break:break-word;overflow-wrap:break-word;">' + esc(summary) + '</div>' +
                (reason ? '<div style="font-size:0.72em;color:rgba(168,162,153,0.5);line-height:1.4;margin-bottom:8px;word-break:break-word;">💡 ' + esc(reason) + '</div>' : '') +
                '<div style="display:flex;gap:8px;justify-content:flex-end;">' +
                '<button data-sug-accept="' + esc(s.id) + '" style="padding:4px 12px;border-radius:6px;background:rgba(109,168,155,0.15);color:#6da89b;border:1px solid rgba(109,168,155,0.2);cursor:pointer;font-size:0.75em;font-weight:600;transition:all 0.15s;" onmouseover="this.style.background=\'rgba(109,168,155,0.25)\'" onmouseout="this.style.background=\'rgba(109,168,155,0.15)\'">存入</button>' +
                '<button data-sug-dismiss="' + esc(s.id) + '" style="padding:4px 12px;border-radius:6px;background:none;color:rgba(168,162,153,0.6);border:1px solid rgba(255,255,255,0.06);cursor:pointer;font-size:0.75em;transition:all 0.15s;" onmouseover="this.style.color=\'rgba(184,134,138,0.7)\';this.style.borderColor=\'rgba(184,134,138,0.2)\'" onmouseout="this.style.color=\'rgba(168,162,153,0.6)\';this.style.borderColor=\'rgba(255,255,255,0.06)\'">忽略</button>' +
                '</div></div>';
        });

        panel.innerHTML = html;

        // Bind events
        panel.querySelector('#ke-dismiss-all').addEventListener('click', dismissAllSuggestions);
        panel.querySelectorAll('[data-sug-accept]').forEach(btn => {
            btn.addEventListener('click', () => acceptSuggestion(btn.dataset.sugAccept));
        });
        panel.querySelectorAll('[data-sug-dismiss]').forEach(btn => {
            btn.addEventListener('click', () => dismissSuggestion(btn.dataset.sugDismiss));
        });
    }

    async function acceptSuggestion(sugId) {
        const btn = document.querySelector('[data-sug-accept="' + sugId + '"]');
        if (btn) { btn.disabled = true; btn.textContent = '存入中...'; btn.style.opacity = '0.6'; }
        try {
            const resp = await fetch('/api/knowledge/' + getNs() + '/suggestions/' + sugId + '/accept', {
                method: 'POST',
                headers: { 'Authorization': 'Bearer ' + getToken() }
            });
            if (resp.ok) {
                keSuggestions = keSuggestions.filter(s => s.id !== sugId);
                updateKEBadge();
                renderKEPanel();
            } else {
                if (btn) { btn.disabled = false; btn.textContent = '重试'; btn.style.opacity = '1'; }
            }
        } catch { if (btn) { btn.disabled = false; btn.textContent = '重试'; btn.style.opacity = '1'; } }
    }

    async function dismissSuggestion(sugId) {
        const btn = document.querySelector('[data-sug-dismiss="' + sugId + '"]');
        if (btn) { btn.disabled = true; btn.textContent = '忽略中...'; btn.style.opacity = '0.6'; }
        try {
            const resp = await fetch('/api/knowledge/' + getNs() + '/suggestions/' + sugId + '/dismiss', {
                method: 'POST',
                headers: { 'Authorization': 'Bearer ' + getToken() }
            });
            if (resp.ok) {
                keSuggestions = keSuggestions.filter(s => s.id !== sugId);
                updateKEBadge();
                renderKEPanel();
            } else {
                if (btn) { btn.disabled = false; btn.textContent = '忽略'; btn.style.opacity = '1'; }
            }
        } catch { if (btn) { btn.disabled = false; btn.textContent = '忽略'; btn.style.opacity = '1'; } }
    }

    async function dismissAllSuggestions() {
        try {
            const resp = await fetch('/api/knowledge/' + getNs() + '/suggestions/dismiss-all', {
                method: 'POST',
                headers: { 'Authorization': 'Bearer ' + getToken() }
            });
            if (resp.ok) {
                keSuggestions = [];
                updateKEBadge();
                renderKEPanel();
            }
        } catch {}
    }

    // Visibility change: pause/resume polling
    document.addEventListener('visibilitychange', () => {
        if (document.hidden) {
            stopKEPolling();
        } else if (chatInitialized && isCurrentUserOwner()) {
            startKEPolling();
        }
    });

    async function clearAvatar() {
        const confirmed = await showModal({ title: '移除虚拟形象', message: '确定移除当前虚拟形象？' });
        if (!confirmed) return;
        try {
            const r = await fetch('/api/agents/' + agentId + '/avatar', {
                method: 'PUT', headers: jsonHdrs(),
                body: JSON.stringify({ clear: true })
            });
            if (!r.ok) throw new Error();
            agentData.avatar_model_url = null;
            loadPresetAvatars();
            toast('虚拟形象已移除');
        } catch(e) { toast('操作失败', 'error'); }
    }

    // ===== Publish / Unpublish =====

    function updatePublishButton() {
        const btn = document.getElementById('publishBtn');
        if (!btn || !agentData) return;
        
        // Only show for owner
        try {
            const token = getToken();
            if (!token) return;
            const payload = JSON.parse(atob(token.split('.')[1]));
            const userId = payload.sub || payload.user_id;
            if (agentData.owner_id !== userId) return;
        } catch { return; }
        
        btn.style.display = '';
        if (agentData.is_public === 1) {
            btn.textContent = '✅ 已发布';
            btn.style.background = 'rgba(109,168,155,0.12)';
            btn.style.color = '#6da89b';
            btn.style.border = '1px solid rgba(109,168,155,0.3)';
        } else {
            btn.textContent = '🚀 发布';
            btn.style.background = 'rgba(226,185,106,0.12)';
            btn.style.color = '#e2b96a';
            btn.style.border = '1px solid rgba(226,185,106,0.3)';
        }
    }

    async function togglePublish() {
        if (!agentData) return;
        
        if (agentData.is_public === 1) {
            // Unpublish
            const confirmed = await showModal({ title: '取消发布', message: '确定将此 Agent 设为私有？其他用户将无法访问。' });
            if (!confirmed) return;
            try {
                const r = await fetch('/api/agents/' + agentId + '/unpublish', { method: 'POST', headers: hdrs() });
                if (!r.ok) throw new Error();
                agentData.is_public = 0;
                updatePublishButton();
                toast('已设为私有');
            } catch { toast('操作失败', 'error'); }
        } else {
            // Publish — ask for price
            const priceStr = await showModal({ title: '发布 Agent', message: '设置其他用户每次对话需要支付的 ATP 数量', type: 'prompt', inputLabel: '每次对话价格 (ATP)', inputDefault: '1', inputType: 'number', confirmText: '发布' });
            if (priceStr === null) return;
            const price = parseInt(priceStr);
            if (isNaN(price) || price < 0) { toast('请输入有效的价格', 'error'); return; }
            
            try {
                const r = await fetch('/api/agents/' + agentId + '/publish', {
                    method: 'POST', headers: jsonHdrs(),
                    body: JSON.stringify({ price_per_chat: price })
                });
                if (!r.ok) throw new Error();
                agentData.is_public = 1;
                agentData.price_per_chat = price;
                updatePublishButton();
                toast('Agent 已发布' + (price > 0 ? '，每次对话 ' + price + ' ATP' : '（免费）'));
            } catch { toast('发布失败', 'error'); }
        }
    }

    // ===== Usage Tab =====

    async function loadUsage() {
        try {
            const r = await fetch('/api/agents/' + agentId + '/usage', { headers: hdrs() });
            if (!r.ok) throw new Error();
            const data = await r.json();
            
            document.getElementById('usageTotalChats').textContent = data.total_chats || 0;
            document.getElementById('usageTotalAtp').textContent = data.total_atp || 0;
            document.getElementById('usageUniqueUsers').textContent = data.unique_users || 0;
            
            const records = data.records || [];
            const el = document.getElementById('usageRecords');
            
            if (!records.length) {
                el.innerHTML = '<div style="text-align:center;color:#6b665e;padding:30px;font-size:0.9em;">暂无使用记录</div>';
                return;
            }
            
            el.innerHTML = '<table style="width:100%;border-collapse:collapse;">' +
                '<thead><tr style="border-bottom:1px solid rgba(255,255,255,0.06);">' +
                '<th style="text-align:left;padding:10px 12px;color:#6b665e;font-size:0.78em;font-weight:600;text-transform:uppercase;letter-spacing:0.04em;">用户</th>' +
                '<th style="text-align:left;padding:10px 12px;color:#6b665e;font-size:0.78em;font-weight:600;">时间</th>' +
                '<th style="text-align:right;padding:10px 12px;color:#6b665e;font-size:0.78em;font-weight:600;">ATP</th>' +
                '</tr></thead><tbody>' +
                records.map(r => {
                    const username = r.username || '匿名';
                    // 隐私保护：只显示首字 + 星号
                    const masked = username.length > 1 ? username[0] + '***' + username[username.length - 1] : username[0] + '***';
                    const time = (r.created_at || '').slice(0, 16).replace('T', ' ');
                    return '<tr style="border-bottom:1px solid rgba(255,255,255,0.03);">' +
                        '<td style="padding:10px 12px;color:#a8a299;font-size:0.85em;">' + esc(masked) + '</td>' +
                        '<td style="padding:10px 12px;color:#6b665e;font-size:0.82em;">' + esc(time) + '</td>' +
                        '<td style="padding:10px 12px;text-align:right;color:#6da89b;font-size:0.85em;font-weight:600;">' + (r.atp_amount || 0) + '</td>' +
                        '</tr>';
                }).join('') +
                '</tbody></table>';
        } catch(e) {
            document.getElementById('usageRecords').innerHTML = '<div style="color:#b8868a;padding:20px;text-align:center;">加载失败</div>';
        }
    }

    // ==================== Knowledge Import ====================

    let _importModal = null;
    let _importPollTimer = null;

    function openImportModal() {
        if (_importModal) { _importModal.remove(); }

        const overlay = document.createElement('div');
        overlay.id = 'importModalOverlay';
        overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.6);z-index:1002;display:flex;align-items:center;justify-content:center;';

        const panel = document.createElement('div');
        panel.style.cssText = 'background:#1e2025;border:1px solid rgba(255,255,255,0.08);border-radius:16px;width:560px;max-width:92vw;max-height:85vh;overflow-y:auto;padding:0;box-shadow:0 24px 80px rgba(0,0,0,0.5);';

        panel.innerHTML = `
            <div style="padding:20px 24px;border-bottom:1px solid rgba(255,255,255,0.06);display:flex;justify-content:space-between;align-items:center;">
                <span style="font-family:var(--font-display);font-weight:700;color:#e2b96a;font-size:1.1em;">📥 导入外部知识</span>
                <button onclick="AgentDetail.closeImportModal()" style="background:none;border:none;color:#6b665e;font-size:1.3em;cursor:pointer;padding:4px 8px;border-radius:4px;" onmouseover="this.style.background='rgba(255,255,255,0.05)'" onmouseout="this.style.background='none'">×</button>
            </div>
            <div style="padding:20px 24px;">
                <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px;margin-bottom:20px;">
                    <div class="import-src-card" onclick="document.getElementById('importFileInput').click()" style="background:rgba(255,255,255,0.03);border:1px solid rgba(255,255,255,0.06);border-radius:12px;padding:16px;text-align:center;cursor:pointer;transition:all 0.2s;" onmouseover="this.style.borderColor='#e2b96a';this.style.transform='translateY(-2px)'" onmouseout="this.style.borderColor='rgba(255,255,255,0.06)';this.style.transform='none'">
                        <div style="font-size:1.8em;margin-bottom:6px;">📄</div>
                        <div style="font-size:0.82em;color:#a8a299;font-weight:600;">文件</div>
                        <div style="font-size:0.7em;color:#6b665e;margin-top:4px;">.md .txt .csv .pdf</div>
                    </div>
                    <div class="import-src-card" onclick="AgentDetail._openNotionPanel()" style="background:rgba(255,255,255,0.03);border:1px solid rgba(255,255,255,0.06);border-radius:12px;padding:16px;text-align:center;cursor:pointer;transition:all 0.2s;" onmouseover="this.style.borderColor='#e2b96a';this.style.transform='translateY(-2px)'" onmouseout="this.style.borderColor='rgba(255,255,255,0.06)';this.style.transform='none'">
                        <div style="font-size:1.8em;margin-bottom:6px;">📓</div>
                        <div style="font-size:0.82em;color:#a8a299;font-weight:600;">Notion</div>
                        <div style="font-size:0.7em;color:#6b665e;margin-top:4px;">API 连接</div>
                    </div>
                    <div class="import-src-card" onclick="document.getElementById('importZipInput').click()" style="background:rgba(255,255,255,0.03);border:1px solid rgba(255,255,255,0.06);border-radius:12px;padding:16px;text-align:center;cursor:pointer;transition:all 0.2s;" onmouseover="this.style.borderColor='#e2b96a';this.style.transform='translateY(-2px)'" onmouseout="this.style.borderColor='rgba(255,255,255,0.06)';this.style.transform='none'">
                        <div style="font-size:1.8em;margin-bottom:6px;">💎</div>
                        <div style="font-size:0.82em;color:#a8a299;font-weight:600;">Obsidian</div>
                        <div style="font-size:0.7em;color:#6b665e;margin-top:4px;">.zip 导出包</div>
                    </div>
                </div>

                <div id="importDropZone" style="border:2px dashed rgba(255,255,255,0.1);border-radius:12px;padding:40px;text-align:center;transition:all 0.2s;margin-bottom:20px;cursor:pointer;" onclick="document.getElementById('importFileInput').click()">
                    <div style="font-size:2em;margin-bottom:8px;opacity:0.5;">📂</div>
                    <div style="color:#6b665e;font-size:0.85em;">拖拽文件到这里，或点击选择</div>
                    <div style="color:#4a4640;font-size:0.72em;margin-top:6px;">支持 .md, .txt, .csv, .pdf, .json, .zip</div>
                </div>

                <input type="file" id="importFileInput" accept=".md,.txt,.csv,.pdf,.json" style="display:none;" onchange="AgentDetail._handleImportFile(this)">
                <input type="file" id="importZipInput" accept=".zip" style="display:none;" onchange="AgentDetail._handleImportZip(this)">

                <div id="importUploadStatus" style="display:none;margin-bottom:16px;"></div>

                <div style="border-top:1px solid rgba(255,255,255,0.06);padding-top:16px;">
                    <div style="font-size:0.82em;color:#6b665e;font-weight:600;margin-bottom:10px;">导入历史</div>
                    <div id="importHistoryList" style="max-height:240px;overflow-y:auto;">
                        <div style="text-align:center;color:#4a4640;font-size:0.8em;padding:20px;">加载中...</div>
                    </div>
                </div>
            </div>
        `;

        overlay.appendChild(panel);
        overlay.addEventListener('click', function(e) { if (e.target === overlay) closeImportModal(); });
        document.body.appendChild(overlay);
        _importModal = overlay;

        // Setup drag-and-drop
        const dropZone = document.getElementById('importDropZone');
        dropZone.addEventListener('dragover', function(e) {
            e.preventDefault();
            this.style.borderColor = '#e2b96a';
            this.style.background = 'rgba(226,185,106,0.05)';
        });
        dropZone.addEventListener('dragleave', function(e) {
            e.preventDefault();
            this.style.borderColor = 'rgba(255,255,255,0.1)';
            this.style.background = 'none';
        });
        dropZone.addEventListener('drop', function(e) {
            e.preventDefault();
            this.style.borderColor = 'rgba(255,255,255,0.1)';
            this.style.background = 'none';
            if (e.dataTransfer.files.length > 0) {
                const file = e.dataTransfer.files[0];
                if (file.name.endsWith('.zip')) {
                    _uploadImportFile(file, true);
                } else {
                    _uploadImportFile(file, false);
                }
            }
        });

        _loadImportHistory();
    }

    function closeImportModal() {
        if (_importPollTimer) { clearInterval(_importPollTimer); _importPollTimer = null; }
        if (_importModal) { _importModal.remove(); _importModal = null; }
    }

    function _handleImportFile(input) {
        if (input.files.length > 0) _uploadImportFile(input.files[0], false);
        input.value = '';
    }

    function _handleImportZip(input) {
        if (input.files.length > 0) _uploadImportFile(input.files[0], true);
        input.value = '';
    }

    async function _uploadImportFile(file, isZip) {
        const statusEl = document.getElementById('importUploadStatus');
        if (!statusEl) return;
        statusEl.style.display = 'block';
        statusEl.innerHTML = '<div style="background:rgba(226,185,106,0.08);border:1px solid rgba(226,185,106,0.2);border-radius:8px;padding:12px 16px;display:flex;align-items:center;gap:10px;"><div class="loading-spinner" style="width:16px;height:16px;border-width:2px;"></div><span style="color:#e2b96a;font-size:0.85em;">正在上传 ' + esc(file.name) + '...</span></div>';

        const formData = new FormData();
        formData.append('file', file);

        const endpoint = isZip ? 'upload-zip' : 'upload';
        try {
            const resp = await fetch('/api/knowledge/' + getNs() + '/imports/' + endpoint, {
                method: 'POST',
                headers: hdrs(),
                body: formData,
            });
            const data = await resp.json();
            if (!resp.ok) {
                statusEl.innerHTML = '<div style="background:rgba(184,134,138,0.08);border:1px solid rgba(184,134,138,0.2);border-radius:8px;padding:12px 16px;color:#b8868a;font-size:0.85em;">❌ ' + esc(data.detail || '上传失败') + '</div>';
                return;
            }
            statusEl.innerHTML = '<div style="background:rgba(109,168,155,0.08);border:1px solid rgba(109,168,155,0.2);border-radius:8px;padding:12px 16px;color:#6da89b;font-size:0.85em;">✅ 已上传，正在处理...</div>';

            // Start polling
            _startImportPoll(data.import_id);
            _loadImportHistory();
        } catch (e) {
            statusEl.innerHTML = '<div style="background:rgba(184,134,138,0.08);border:1px solid rgba(184,134,138,0.2);border-radius:8px;padding:12px 16px;color:#b8868a;font-size:0.85em;">❌ 网络错误</div>';
        }
    }

    function _startImportPoll(importId) {
        if (_importPollTimer) clearInterval(_importPollTimer);
        _importPollTimer = setInterval(async function() {
            try {
                const resp = await fetch('/api/knowledge/' + getNs() + '/imports/' + importId, { headers: hdrs() });
                if (!resp.ok) return;
                const data = await resp.json();
                _updateImportProgress(data);
                if (data.status === 'completed' || data.status === 'failed') {
                    clearInterval(_importPollTimer);
                    _importPollTimer = null;
                    _loadImportHistory();
                }
            } catch(e) {}
        }, 3000);
    }

    let _currentImportId = null;

    function _updateImportProgress(data) {
        const statusEl = document.getElementById('importUploadStatus');
        if (!statusEl) return;
        statusEl.style.display = 'block';
        _currentImportId = data.id || _currentImportId;

        if (data.status === 'completed') {
            statusEl.innerHTML = '<div style="background:rgba(109,168,155,0.08);border:1px solid rgba(109,168,155,0.2);border-radius:8px;padding:12px 16px;color:#6da89b;font-size:0.85em;">✅ 导入完成！提取了 <b>' + data.total_suggestions + '</b> 条知识建议</div>';
            _loadImportSuggestions(_currentImportId);
        } else if (data.status === 'failed') {
            statusEl.innerHTML = '<div style="background:rgba(184,134,138,0.08);border:1px solid rgba(184,134,138,0.2);border-radius:8px;padding:12px 16px;color:#b8868a;font-size:0.85em;">❌ ' + esc(data.error_message || '处理失败') + '</div>';
        } else {
            const pct = data.total_chunks > 0 ? Math.round(data.processed_chunks / data.total_chunks * 100) : 0;
            const label = data.status === 'extracting' ? '提取知识中' : '解析文件中';
            statusEl.innerHTML = `
                <div style="background:rgba(226,185,106,0.08);border:1px solid rgba(226,185,106,0.2);border-radius:8px;padding:12px 16px;">
                    <div style="display:flex;justify-content:space-between;margin-bottom:6px;">
                        <span style="color:#e2b96a;font-size:0.82em;">${esc(label)}</span>
                        <span style="color:#6b665e;font-size:0.78em;">${data.processed_chunks}/${data.total_chunks} chunks · ${data.total_suggestions} 条建议</span>
                    </div>
                    <div style="background:rgba(255,255,255,0.06);border-radius:4px;height:6px;overflow:hidden;">
                        <div style="background:linear-gradient(90deg,#e2b96a,#d4a85a);height:100%;width:${pct}%;transition:width 0.3s;border-radius:4px;"></div>
                    </div>
                </div>`;
            // Fetch suggestions in real-time during extraction
            if (data.total_suggestions > 0) {
                _loadImportSuggestions(_currentImportId);
            }
        }
    }

    async function _loadImportSuggestions(importId) {
        if (!importId) return;
        let container = document.getElementById('importSuggestionsPanel');
        if (!container) {
            container = document.createElement('div');
            container.id = 'importSuggestionsPanel';
            container.style.cssText = 'margin-top:12px;max-height:300px;overflow-y:auto;';
            const statusEl = document.getElementById('importUploadStatus');
            if (statusEl) statusEl.parentNode.insertBefore(container, statusEl.nextSibling);
        }
        try {
            const resp = await fetch('/api/knowledge/' + getNs() + '/suggestions?status=pending&limit=50', { headers: hdrs() });
            if (!resp.ok) return;
            const data = await resp.json();
            const items = (data.suggestions || []).filter(function(s) { return s.import_id === importId; });
            if (!items.length) { container.innerHTML = ''; return; }

            let html = '<div style="border:1px solid rgba(226,185,106,0.15);border-radius:10px;background:rgba(226,185,106,0.03);overflow:hidden;">';
            html += '<div style="padding:10px 14px;border-bottom:1px solid rgba(255,255,255,0.04);display:flex;justify-content:space-between;align-items:center;">';
            html += '<span style="font-size:0.8em;color:rgba(226,185,106,0.8);font-weight:600;">🧠 已提取 ' + items.length + ' 条知识</span>';
            html += '<div style="display:flex;gap:8px;">';
            html += '<button id="impAcceptAll" style="font-size:0.72em;padding:3px 10px;border-radius:4px;background:rgba(109,168,155,0.12);color:#6da89b;border:1px solid rgba(109,168,155,0.2);cursor:pointer;">全部存入</button>';
            html += '<button id="impDismissAll" style="font-size:0.72em;padding:3px 10px;border-radius:4px;background:none;color:rgba(168,162,153,0.5);border:1px solid rgba(255,255,255,0.06);cursor:pointer;">全部忽略</button>';
            html += '</div></div>';

            items.forEach(function(s) {
                html += '<div style="padding:10px 14px;border-bottom:1px solid rgba(255,255,255,0.02);">';
                html += '<div style="display:flex;align-items:center;gap:6px;margin-bottom:4px;">';
                html += '<span style="font-size:0.65em;padding:1px 5px;border-radius:3px;background:rgba(109,168,155,0.1);color:rgba(109,168,155,0.7);">' + esc(s.content_type || '事实') + '</span>';
                html += '</div>';
                html += '<div style="font-size:0.8em;color:rgba(232,228,223,0.8);line-height:1.5;word-break:break-word;">' + esc(s.summary) + '</div>';
                if (s.reason) html += '<div style="font-size:0.7em;color:rgba(168,162,153,0.45);margin-top:3px;word-break:break-word;">💡 ' + esc(s.reason) + '</div>';
                html += '<div style="display:flex;gap:6px;justify-content:flex-end;margin-top:6px;">';
                html += '<button data-imp-accept="' + esc(s.id) + '" style="font-size:0.7em;padding:3px 10px;border-radius:5px;background:rgba(109,168,155,0.12);color:#6da89b;border:1px solid rgba(109,168,155,0.2);cursor:pointer;">存入</button>';
                html += '<button data-imp-dismiss="' + esc(s.id) + '" style="font-size:0.7em;padding:3px 10px;border-radius:5px;background:none;color:rgba(168,162,153,0.5);border:1px solid rgba(255,255,255,0.06);cursor:pointer;">忽略</button>';
                html += '</div></div>';
            });
            html += '</div>';
            container.innerHTML = html;

            // Bind events
            container.querySelectorAll('[data-imp-accept]').forEach(function(btn) {
                btn.addEventListener('click', function() { acceptSuggestion(btn.dataset.impAccept); setTimeout(function(){ _loadImportSuggestions(importId); }, 1000); });
            });
            container.querySelectorAll('[data-imp-dismiss]').forEach(function(btn) {
                btn.addEventListener('click', function() { dismissSuggestion(btn.dataset.impDismiss); setTimeout(function(){ _loadImportSuggestions(importId); }, 1000); });
            });
            const acceptAllBtn = container.querySelector('#impAcceptAll');
            if (acceptAllBtn) acceptAllBtn.addEventListener('click', async function() {
                acceptAllBtn.disabled = true; acceptAllBtn.textContent = '存入中...';
                for (const s of items) {
                    try { await fetch('/api/knowledge/' + getNs() + '/suggestions/' + s.id + '/accept', { method: 'POST', headers: { 'Authorization': 'Bearer ' + getToken() } }); } catch {}
                }
                _loadImportSuggestions(importId);
                pollSuggestions();
            });
            const dismissAllBtn = container.querySelector('#impDismissAll');
            if (dismissAllBtn) dismissAllBtn.addEventListener('click', async function() {
                dismissAllBtn.disabled = true; dismissAllBtn.textContent = '忽略中...';
                try { await fetch('/api/knowledge/' + getNs() + '/suggestions/dismiss-all', { method: 'POST', headers: { 'Authorization': 'Bearer ' + getToken() } }); } catch {}
                _loadImportSuggestions(importId);
                pollSuggestions();
            });
        } catch {}
    }

    async function _loadImportHistory() {
        const el = document.getElementById('importHistoryList');
        if (!el) return;
        try {
            const resp = await fetch('/api/knowledge/' + getNs() + '/imports', { headers: hdrs() });
            if (!resp.ok) { el.innerHTML = '<div style="color:#b8868a;font-size:0.8em;text-align:center;padding:10px;">加载失败</div>'; return; }
            const data = await resp.json();
            const imports = data.imports || [];
            if (!imports.length) {
                el.innerHTML = '<div style="color:#4a4640;font-size:0.8em;text-align:center;padding:20px;">暂无导入记录</div>';
                return;
            }
            el.innerHTML = imports.map(function(imp) {
                const statusColors = { pending: '#6b665e', processing: '#e2b96a', extracting: '#e2b96a', completed: '#6da89b', failed: '#b8868a' };
                const statusLabels = { pending: '等待中', processing: '解析中', extracting: '提取中', completed: '已完成', failed: '失败' };
                const color = statusColors[imp.status] || '#6b665e';
                const label = statusLabels[imp.status] || imp.status;
                const time = (imp.created_at || '').slice(0, 16).replace('T', ' ');
                const pct = imp.total_chunks > 0 ? Math.round(imp.processed_chunks / imp.total_chunks * 100) : 0;
                const isActive = imp.status === 'processing' || imp.status === 'extracting';

                let progressBar = '';
                if (isActive || imp.status === 'completed') {
                    progressBar = '<div style="background:rgba(255,255,255,0.06);border-radius:3px;height:4px;overflow:hidden;margin-top:6px;"><div style="background:' + color + ';height:100%;width:' + (imp.status === 'completed' ? 100 : pct) + '%;border-radius:3px;transition:width 0.3s;"></div></div>';
                }

                return '<div style="padding:10px 12px;border-bottom:1px solid rgba(255,255,255,0.03);display:flex;align-items:flex-start;gap:10px;">' +
                    '<div style="flex:1;min-width:0;">' +
                        '<div style="display:flex;align-items:center;gap:8px;">' +
                            '<span style="font-size:0.82em;color:#a8a299;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:200px;" title="' + esc(imp.source_name || '') + '">' + esc(imp.source_name || '未知文件') + '</span>' +
                            '<span style="font-size:0.68em;color:' + color + ';background:' + color + '18;padding:1px 6px;border-radius:4px;white-space:nowrap;">' + esc(label) + '</span>' +
                        '</div>' +
                        '<div style="font-size:0.72em;color:#4a4640;margin-top:3px;">' + esc(time) + (imp.total_suggestions > 0 ? ' · ' + imp.total_suggestions + ' 条建议' : '') + '</div>' +
                        progressBar +
                    '</div>' +
                    '<button onclick="AgentDetail._deleteImport(\'' + esc(imp.id) + '\')" style="background:none;border:none;color:#4a4640;cursor:pointer;font-size:0.8em;padding:4px;flex-shrink:0;" title="删除" onmouseover="this.style.color=\'#b8868a\'" onmouseout="this.style.color=\'#4a4640\'">🗑</button>' +
                '</div>';
            }).join('');

            // If any active imports, start polling
            const active = imports.find(function(i) { return i.status === 'processing' || i.status === 'extracting'; });
            if (active && !_importPollTimer) _startImportPoll(active.id);
        } catch(e) {
            el.innerHTML = '<div style="color:#b8868a;font-size:0.8em;text-align:center;padding:10px;">加载失败</div>';
        }
    }

    async function _deleteImport(importId) {
        const confirmed = await showModal({ title: '删除导入记录', message: '删除此导入记录及其待处理的建议？', confirmText: '删除', confirmStyle: 'danger' });
        if (!confirmed) return;
        try {
            await fetch('/api/knowledge/' + getNs() + '/imports/' + importId, {
                method: 'DELETE',
                headers: hdrs(),
            });
            _loadImportHistory();
        } catch(e) {}
    }

    // ==================== Notion Integration ====================

    async function _openNotionPanel() {
        const mainContent = document.querySelector('#importModalOverlay > div > div:nth-child(2)');
        if (!mainContent) return;

        // Save original content for back button
        if (!mainContent.dataset.originalHtml) {
            mainContent.dataset.originalHtml = mainContent.innerHTML;
        }

        // Show loading state
        mainContent.innerHTML = '<div style="text-align:center;padding:40px;color:#6b665e;font-size:0.85em;"><div class="loading-spinner" style="width:20px;height:20px;border-width:2px;margin:0 auto 12px;"></div>检查 Notion 连接状态...</div>';

        // Check if already connected
        try {
            const resp = await fetch('/api/knowledge/' + getNs() + '/imports/notion/pages', { headers: hdrs() });
            if (resp.ok) {
                const data = await resp.json();
                _showNotionPageList(mainContent, data.pages, data.workspace_name);
                return;
            }
        } catch(e) {}

        // Not connected — show connection form
        _showNotionConnectForm(mainContent);
    }

    function _showNotionConnectForm(container) {
        container.innerHTML = `
            <div style="display:flex;align-items:center;gap:10px;margin-bottom:20px;">
                <button onclick="AgentDetail._notionGoBack()" style="background:none;border:none;color:#6b665e;cursor:pointer;font-size:1.1em;padding:4px 8px;border-radius:4px;" onmouseover="this.style.background='rgba(255,255,255,0.05)'" onmouseout="this.style.background='none'">←</button>
                <span style="font-family:var(--font-display);font-weight:700;color:#e2b96a;font-size:1em;">📓 连接 Notion</span>
            </div>
            <div style="background:rgba(255,255,255,0.03);border:1px solid rgba(255,255,255,0.06);border-radius:12px;padding:20px;margin-bottom:20px;">
                <div style="font-size:0.82em;color:#a8a299;line-height:1.8;">
                    <div style="margin-bottom:8px;"><span style="color:#e2b96a;font-weight:600;">步骤 1</span>　前往 <a href="https://www.notion.com/my-integrations" target="_blank" style="color:#6da89b;text-decoration:underline;">notion.com/my-integrations</a> 创建 Internal Integration</div>
                    <div style="margin-bottom:8px;padding-left:52px;">→ 点击 <b>New integration</b>，创建 Internal Integration</div>
                    <div style="margin-bottom:8px;"><span style="color:#e2b96a;font-weight:600;">步骤 2</span>　复制 Integration Token（以 <code style="background:rgba(255,255,255,0.06);padding:1px 5px;border-radius:3px;font-size:0.9em;">secret_</code> 开头）</div>
                    <div><span style="color:#e2b96a;font-weight:600;">步骤 3</span>　在 Notion 中打开要导入的页面 → <b>Share</b> → 将该 Integration 添加为连接</div>
                </div>
            </div>
            <div style="display:flex;gap:10px;align-items:center;">
                <input id="notionTokenInput" type="password" placeholder="secret_xxxxxxxxxxxxxxxxx" style="flex:1;background:rgba(255,255,255,0.04);border:1px solid rgba(255,255,255,0.1);border-radius:8px;padding:10px 14px;color:#e8e4df;font-size:0.88em;font-family:monospace;outline:none;" onfocus="this.style.borderColor='#e2b96a'" onblur="this.style.borderColor='rgba(255,255,255,0.1)'">
                <button id="notionConnectBtn" onclick="AgentDetail._notionConnect()" style="background:linear-gradient(135deg,#e2b96a,#d4a85a);color:#1a1b1e;border:none;border-radius:8px;padding:10px 20px;font-weight:700;font-size:0.85em;cursor:pointer;white-space:nowrap;">连接</button>
            </div>
            <div id="notionConnectStatus" style="margin-top:12px;"></div>
        `;
    }

    async function _notionConnect() {
        const tokenInput = document.getElementById('notionTokenInput');
        const statusEl = document.getElementById('notionConnectStatus');
        const btn = document.getElementById('notionConnectBtn');
        if (!tokenInput || !statusEl || !btn) return;

        const token = tokenInput.value.trim();
        if (!token) {
            statusEl.innerHTML = '<div style="color:#b8868a;font-size:0.82em;">请输入 Token</div>';
            return;
        }

        btn.disabled = true;
        btn.textContent = '连接中...';
        statusEl.innerHTML = '';

        try {
            const resp = await fetch('/api/knowledge/' + getNs() + '/imports/notion/connect', {
                method: 'POST',
                headers: { ...hdrs(), 'Content-Type': 'application/json' },
                body: JSON.stringify({ token: token }),
            });
            const data = await resp.json();
            if (!resp.ok) {
                statusEl.innerHTML = '<div style="color:#b8868a;font-size:0.82em;">❌ ' + esc(data.detail || '连接失败') + '</div>';
                btn.disabled = false;
                btn.textContent = '连接';
                return;
            }

            // Success — load pages
            statusEl.innerHTML = '<div style="color:#6da89b;font-size:0.82em;">✅ 已连接: ' + esc(data.workspace_name) + '</div>';
            setTimeout(function() { _openNotionPanel(); }, 800);
        } catch(e) {
            statusEl.innerHTML = '<div style="color:#b8868a;font-size:0.82em;">❌ 网络错误</div>';
            btn.disabled = false;
            btn.textContent = '连接';
        }
    }

    function _showNotionPageList(container, pages, workspaceName) {
        let pagesHtml = '';
        if (!pages || pages.length === 0) {
            pagesHtml = '<div style="text-align:center;color:#4a4640;font-size:0.82em;padding:30px;">未找到可访问的页面。请确保已在 Notion 中将页面 Share 给该 Integration。</div>';
        } else {
            pagesHtml = pages.map(function(p, i) {
                const edited = (p.last_edited || '').slice(0, 10);
                return '<label style="display:flex;align-items:center;gap:10px;padding:10px 14px;border-bottom:1px solid rgba(255,255,255,0.03);cursor:pointer;transition:background 0.15s;" onmouseover="this.style.background=\'rgba(255,255,255,0.02)\'" onmouseout="this.style.background=\'none\'">' +
                    '<input type="checkbox" class="notion-page-cb" value="' + esc(p.id) + '" style="accent-color:#e2b96a;width:16px;height:16px;cursor:pointer;" onchange="AgentDetail._notionUpdateCount()">' +
                    '<span style="font-size:1.1em;">' + esc(p.icon || '📄') + '</span>' +
                    '<span style="flex:1;font-size:0.85em;color:#e8e4df;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">' + esc(p.title) + '</span>' +
                    '<span style="font-size:0.72em;color:#4a4640;white-space:nowrap;">' + esc(edited) + '</span>' +
                '</label>';
            }).join('');
        }

        container.innerHTML = `
            <div style="display:flex;align-items:center;gap:10px;margin-bottom:16px;">
                <button onclick="AgentDetail._notionGoBack()" style="background:none;border:none;color:#6b665e;cursor:pointer;font-size:1.1em;padding:4px 8px;border-radius:4px;" onmouseover="this.style.background='rgba(255,255,255,0.05)'" onmouseout="this.style.background='none'">←</button>
                <span style="font-family:var(--font-display);font-weight:700;color:#e2b96a;font-size:1em;">📓 Notion</span>
                <span style="font-size:0.78em;color:#6da89b;margin-left:auto;">✅ ${esc(workspaceName || 'Connected')}</span>
            </div>
            <div style="font-size:0.78em;color:#6b665e;margin-bottom:10px;">选择要导入的页面：</div>
            <div style="border:1px solid rgba(255,255,255,0.06);border-radius:10px;max-height:320px;overflow-y:auto;margin-bottom:16px;">
                ${pagesHtml}
            </div>
            <div style="display:flex;justify-content:space-between;align-items:center;">
                <button id="notionImportBtn" onclick="AgentDetail._notionImportSelected()" style="background:linear-gradient(135deg,#e2b96a,#d4a85a);color:#1a1b1e;border:none;border-radius:8px;padding:10px 20px;font-weight:700;font-size:0.85em;cursor:pointer;opacity:0.4;pointer-events:none;" disabled>导入选中 (0)</button>
                <button onclick="AgentDetail._notionDisconnect()" style="background:none;border:none;color:#b8868a;font-size:0.78em;cursor:pointer;text-decoration:underline;opacity:0.6;" onmouseover="this.style.opacity='1'" onmouseout="this.style.opacity='0.6'">断开连接</button>
            </div>
            <div id="notionImportStatus" style="margin-top:12px;"></div>
        `;
    }

    function _notionUpdateCount() {
        const checked = document.querySelectorAll('.notion-page-cb:checked');
        const btn = document.getElementById('notionImportBtn');
        if (btn) {
            const count = checked.length;
            btn.textContent = '导入选中 (' + count + ')';
            btn.disabled = count === 0;
            btn.style.opacity = count === 0 ? '0.4' : '1';
            btn.style.pointerEvents = count === 0 ? 'none' : 'auto';
        }
    }

    async function _notionImportSelected() {
        const checked = document.querySelectorAll('.notion-page-cb:checked');
        if (!checked.length) return;

        const pageIds = Array.from(checked).map(function(cb) { return cb.value; });
        const btn = document.getElementById('notionImportBtn');
        const statusEl = document.getElementById('notionImportStatus');
        if (btn) { btn.disabled = true; btn.textContent = '导入中...'; btn.style.opacity = '0.5'; }

        try {
            const resp = await fetch('/api/knowledge/' + getNs() + '/imports/notion/import', {
                method: 'POST',
                headers: { ...hdrs(), 'Content-Type': 'application/json' },
                body: JSON.stringify({ page_ids: pageIds }),
            });
            const data = await resp.json();
            if (!resp.ok) {
                if (statusEl) statusEl.innerHTML = '<div style="color:#b8868a;font-size:0.82em;">❌ ' + esc(data.detail || '导入失败') + '</div>';
                if (btn) { btn.disabled = false; btn.textContent = '导入选中 (' + pageIds.length + ')'; btn.style.opacity = '1'; }
                return;
            }

            const names = (data.imports || []).map(function(i) { return i.page_title; }).join(', ');
            if (statusEl) statusEl.innerHTML = '<div style="color:#6da89b;font-size:0.82em;">✅ 已开始导入: ' + esc(names) + '</div>';

            // Start polling for the first import
            if (data.imports && data.imports.length > 0) {
                // Go back to main view to show progress
                setTimeout(function() {
                    _notionGoBack();
                    _startImportPoll(data.imports[0].import_id);
                    _loadImportHistory();
                }, 1200);
            }
        } catch(e) {
            if (statusEl) statusEl.innerHTML = '<div style="color:#b8868a;font-size:0.82em;">❌ 网络错误</div>';
            if (btn) { btn.disabled = false; btn.textContent = '导入选中 (' + pageIds.length + ')'; btn.style.opacity = '1'; }
        }
    }

    async function _notionDisconnect() {
        const confirmed = await showModal({ title: '断开 Notion', message: '确定断开 Notion 连接？', confirmText: '断开', confirmStyle: 'danger' });
        if (!confirmed) return;
        try {
            await fetch('/api/knowledge/' + getNs() + '/imports/notion/disconnect', {
                method: 'DELETE',
                headers: hdrs(),
            });
        } catch(e) {}
        _notionGoBack();
    }

    function _notionGoBack() {
        const mainContent = document.querySelector('#importModalOverlay > div > div:nth-child(2)');
        if (mainContent && mainContent.dataset.originalHtml) {
            mainContent.innerHTML = mainContent.dataset.originalHtml;
            delete mainContent.dataset.originalHtml;
            _loadImportHistory();
        }
    }

    // ==================== Insights (思考 Tab) ====================

    let _insightsOffset = 0;
    let _insightsFilter = 'all';
    const _INSIGHTS_LIMIT = 20;

    async function loadInsights(reset = true) {
        if (reset) { _insightsOffset = 0; }
        const list = document.getElementById('insightsList');
        const loadMoreBtn = document.getElementById('insightsLoadMore');
        if (reset) list.innerHTML = '<div class="empty" style="padding:60px 0;text-align:center;color:var(--text-muted);">加载中...</div>';

        const params = new URLSearchParams({ limit: _INSIGHTS_LIMIT, offset: _insightsOffset });
        if (_insightsFilter !== 'all') params.set('task_type', _insightsFilter);

        try {
            const res = await fetch(`/api/agents/${agentId}/insights?${params}`, { headers: hdrs() });
            const data = await res.json();
            const insights = data.insights || [];

            if (reset) list.innerHTML = '';

            if (insights.length === 0 && _insightsOffset === 0) {
                list.innerHTML = '<div class="empty" style="padding:60px 0;text-align:center;color:var(--text-muted);">暂无思考记录<br><span style="font-size:0.85em;">配置定时任务后，Agent 会定期生成洞察</span></div>';
                if (loadMoreBtn) loadMoreBtn.style.display = 'none';
                return;
            }

            for (const ins of insights) {
                const card = document.createElement('div');
                card.className = `insight-card ${ins.status === 'unread' ? 'unread' : ''}`;
                card.dataset.id = ins.insight_id;

                const meta = JSON.parse(ins.metadata || '{}');
                const metaTags = [];
                if (meta.facts_count !== undefined) metaTags.push(`📥 ${meta.facts_count} 条`);
                if (meta.sources_count !== undefined) metaTags.push(`🔗 ${meta.sources_count} 源`);
                if (meta.tensions_found) metaTags.push(`⚡ ${meta.tensions_found} 张力`);
                if (meta.health_overall) metaTags.push(`${meta.health_overall === 'good' ? '🟢' : '🟡'} ${meta.health_overall}`);

                const timeAgo = _timeAgo(ins.created_at);

                card.innerHTML = `
                    <div class="insight-card-header">
                        <div class="insight-card-title">${_esc(ins.title)}</div>
                        <div class="insight-card-time">${timeAgo}</div>
                    </div>
                    <div class="insight-card-summary">${_esc(ins.summary || '')}</div>
                    <div class="insight-card-meta">
                        ${metaTags.map(t => `<span class="insight-meta-tag">${t}</span>`).join('')}
                        ${ins.push_status === 'pushed' ? '<span class="insight-meta-tag">📤 已推送</span>' : ''}
                    </div>
                    <div class="insight-content" id="insight-content-${ins.insight_id}"></div>
                `;

                card.addEventListener('click', () => _toggleInsight(ins));
                list.appendChild(card);
            }

            _insightsOffset += insights.length;
            if (loadMoreBtn) loadMoreBtn.style.display = (insights.length >= _INSIGHTS_LIMIT) ? '' : 'none';
        } catch (e) {
            console.error('loadInsights error:', e);
            if (reset) list.innerHTML = '<div class="empty" style="color:#b8868a;">加载失败</div>';
        }
    }

    function _toggleInsight(ins) {
        const el = document.getElementById(`insight-content-${ins.insight_id}`);
        if (!el) return;

        if (el.classList.contains('expanded')) {
            el.classList.remove('expanded');
            return;
        }

        // Render content
        if (typeof marked !== 'undefined' && marked.parse) {
            el.innerHTML = marked.parse(ins.content || '');
        } else {
            el.innerHTML = '<pre style="white-space:pre-wrap;">' + _esc(ins.content || '') + '</pre>';
        }
        el.classList.add('expanded');

        // Mark as read
        if (ins.status === 'unread') {
            const card = el.closest('.insight-card');
            if (card) card.classList.remove('unread');
            ins.status = 'read';
            fetch(`/api/agents/${agentId}/insights/${ins.insight_id}/read`, {
                method: 'PUT', headers: hdrs()
            }).then(() => _updateInsightBadge());
        }
    }

    function filterInsights(type, btn) {
        _insightsFilter = type;
        document.querySelectorAll('.insight-filters .filter-btn').forEach(b => b.classList.remove('active'));
        if (btn) btn.classList.add('active');
        loadInsights(true);
    }

    function loadMoreInsights() {
        loadInsights(false);
    }

    async function _updateInsightBadge() {
        try {
            const res = await fetch(`/api/agents/${agentId}/insights/unread-count`, { headers: hdrs() });
            const data = await res.json();
            const badge = document.getElementById('insightBadge');
            if (badge) {
                if (data.count > 0) {
                    badge.textContent = data.count;
                    badge.style.display = '';
                } else {
                    badge.style.display = 'none';
                }
            }
        } catch (e) {}
    }

    function _timeAgo(dateStr) {
        if (!dateStr) return '';
        const now = new Date();
        const d = new Date(dateStr);
        const diff = Math.floor((now - d) / 1000);
        if (diff < 60) return '刚刚';
        if (diff < 3600) return Math.floor(diff / 60) + '分钟前';
        if (diff < 86400) return Math.floor(diff / 3600) + '小时前';
        if (diff < 604800) return Math.floor(diff / 86400) + '天前';
        return dateStr.substring(0, 10);
    }

    function _esc(s) { return (s || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }

    // ==================== Task Config ====================

    let _taskTypes = {};
    let _agentTasks = [];

    async function loadTaskConfig() {
        const container = document.getElementById('taskConfigContainer');
        if (!container) return;

        try {
            // Load task types + current tasks in parallel
            const [typesRes, tasksRes] = await Promise.all([
                fetch('/api/agents/meta/task-types'),
                fetch(`/api/agents/${agentId}/tasks`, { headers: hdrs() }),
            ]);
            _taskTypes = (await typesRes.json()).task_types || {};
            _agentTasks = (await tasksRes.json()).tasks || [];
        } catch (e) {
            container.innerHTML = '<div style="color:#b8868a;font-size:0.82em;">加载失败</div>';
            return;
        }

        _renderTaskConfig(container);
    }

    function _renderTaskConfig(container) {
        const existingMap = {};
        for (const t of _agentTasks) existingMap[t.task_type] = t;

        let html = '';
        for (const [type, meta] of Object.entries(_taskTypes)) {
            const task = existingMap[type];
            const enabled = task ? !!task.enabled : false;
            const schedule = task ? task.schedule : meta.default_schedule;
            const config = task ? JSON.parse(task.config || '{}') : meta.default_config;
            const lastRun = task ? task.last_run_at : null;
            const lastStatus = task ? task.last_status : null;

            const { freq, dow, hour, minute } = _parseCronForUI(schedule);

            html += `
                <div class="task-item" data-task-type="${type}" data-task-id="${task?.task_id || ''}">
                    <input type="checkbox" class="task-item-toggle" ${enabled ? 'checked' : ''}
                           onchange="AgentDetail._onTaskToggle('${type}', this.checked)">
                    <div class="task-item-body">
                        <div class="task-item-header">
                            <span class="task-item-name">${meta.icon} ${meta.name}</span>
                            <button class="task-run-btn" onclick="AgentDetail._testRunTask('${type}')" title="立即试运行一次">▶ 试运行</button>
                        </div>
                        <div class="task-item-desc">${meta.description}</div>
                        <div class="task-item-controls">
                            <select onchange="AgentDetail._onTaskFreqChange('${type}', this.value)" data-field="freq">
                                <option value="daily" ${freq==='daily'?'selected':''}>每天</option>
                                <option value="weekly" ${freq==='weekly'?'selected':''}>每周</option>
                                <option value="monthly" ${freq==='monthly'?'selected':''}>每月</option>
                            </select>
                            <select class="task-dow" onchange="AgentDetail._onTaskScheduleChange('${type}')" data-field="dow" style="${freq==='weekly'?'':'display:none;'}">
                                ${['日','一','二','三','四','五','六'].map((d,i) => `<option value="${i}" ${dow==i?'selected':''}>${d}</option>`).join('')}
                            </select>
                            <input type="time" value="${String(hour).padStart(2,'0')}:${String(minute).padStart(2,'0')}"
                                   onchange="AgentDetail._onTaskScheduleChange('${type}')" data-field="time">
                            ${lastRun ? `<span class="task-item-status">${lastStatus==='success'?'✅':'❌'} ${_timeAgo(lastRun)}</span>` : ''}
                        </div>
                    </div>
                </div>
            `;
        }

        container.innerHTML = html;
    }

    function _parseCronForUI(cron) {
        const p = (cron || '0 8 * * *').split(/\s+/);
        const minute = parseInt(p[0]) || 0;
        const hour = parseInt(p[1]) || 8;
        const dom = p[2];
        const dow = p[4];

        let freq = 'daily';
        let dowVal = 0;
        if (dow !== '*') { freq = 'weekly'; dowVal = parseInt(dow) || 0; }
        else if (dom !== '*') { freq = 'monthly'; }

        return { freq, dow: dowVal, hour, minute };
    }

    function _buildCron(type) {
        const item = document.querySelector(`.task-item[data-task-type="${type}"]`);
        if (!item) return '0 8 * * *';

        const freq = item.querySelector('[data-field="freq"]').value;
        const timeVal = item.querySelector('[data-field="time"]').value || '08:00';
        const [h, m] = timeVal.split(':').map(Number);
        const dow = item.querySelector('[data-field="dow"]')?.value || '0';

        if (freq === 'weekly') return `${m} ${h} * * ${dow}`;
        if (freq === 'monthly') return `${m} ${h} 1 * *`;
        return `${m} ${h} * * *`;
    }

    async function _onTaskToggle(type, enabled) {
        const item = document.querySelector(`.task-item[data-task-type="${type}"]`);
        const taskId = item?.dataset.taskId;
        const cron = _buildCron(type);
        const meta = _taskTypes[type];

        try {
            if (taskId) {
                await fetch(`/api/agents/${agentId}/tasks/${taskId}`, {
                    method: 'PUT', headers: { ...hdrs(), 'Content-Type': 'application/json' },
                    body: JSON.stringify({ enabled, schedule: cron })
                });
            } else if (enabled) {
                const res = await fetch(`/api/agents/${agentId}/tasks`, {
                    method: 'POST', headers: { ...hdrs(), 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        task_type: type, schedule: cron, enabled: true,
                        config: meta?.default_config || {}
                    })
                });
                const data = await res.json();
                if (data.task_id) item.dataset.taskId = data.task_id;
            }
        } catch (e) {
            console.error('task toggle error:', e);
        }
    }

    function _onTaskFreqChange(type, freq) {
        const item = document.querySelector(`.task-item[data-task-type="${type}"]`);
        const dowSel = item?.querySelector('.task-dow');
        if (dowSel) dowSel.style.display = freq === 'weekly' ? '' : 'none';
        _onTaskScheduleChange(type);
    }

    async function _onTaskScheduleChange(type) {
        const item = document.querySelector(`.task-item[data-task-type="${type}"]`);
        const taskId = item?.dataset.taskId;
        if (!taskId) return;

        const cron = _buildCron(type);
        try {
            await fetch(`/api/agents/${agentId}/tasks/${taskId}`, {
                method: 'PUT', headers: { ...hdrs(), 'Content-Type': 'application/json' },
                body: JSON.stringify({ schedule: cron })
            });
        } catch (e) {}
    }

    async function _runTaskNow(taskId) {
        try {
            await fetch(`/api/agents/${agentId}/tasks/${taskId}/run`, {
                method: 'POST', headers: hdrs()
            });
            alert('任务已触发，请稍后查看「思考」Tab');
        } catch (e) {}
    }

    async function _testRunTask(taskType) {
        const btn = event.target;
        const origText = btn.textContent;
        btn.disabled = true;
        btn.textContent = '⏳ 触发中...';
        try {
            const meta = _taskTypes[taskType] || {};
            const res = await fetch(`/api/agents/${agentId}/tasks/test-run`, {
                method: 'POST',
                headers: { ...hdrs(), 'Content-Type': 'application/json' },
                body: JSON.stringify({ task_type: taskType, config: meta.default_config || {} })
            });
            if (!res.ok) {
                const err = await res.json().catch(() => ({}));
                alert('触发失败: ' + (err.detail || res.status));
                btn.disabled = false;
                btn.textContent = origText;
                return;
            }

            // Show toast
            const toast = document.createElement('div');
            toast.style.cssText = 'position:fixed;top:20px;left:50%;transform:translateX(-50%);background:var(--bg-card,#1e1e3a);border:1px solid rgba(109,168,155,0.4);color:var(--text-primary,#e0e0e0);padding:14px 24px;border-radius:10px;z-index:9999;font-size:0.88em;box-shadow:0 4px 20px rgba(0,0,0,0.3);';
            toast.innerHTML = `✅ 任务已在后台执行，完成后可在「<a href="#" onclick="document.querySelector(\'[data-tab=insights]\').click();this.closest(\'div\').remove();return false;" style="color:#6da89b;text-decoration:underline;">💭 思考</a>」Tab 查看结果`;
            document.body.appendChild(toast);
            setTimeout(() => toast.remove(), 8000);

            btn.textContent = '⏳ 后台执行中';
            setTimeout(() => { btn.disabled = false; btn.textContent = origText; }, 10000);

            // Background poll to update badge when done
            (async () => {
                for (let i = 0; i < 40; i++) {
                    await new Promise(r => setTimeout(r, 3000));
                    try {
                        const cr = await fetch(`/api/agents/${agentId}/insights/unread-count`, { headers: hdrs() });
                        const cd = await cr.json();
                        if (cd.count > 0) { _updateInsightBadge(); break; }
                    } catch(e) {}
                }
            })();

        } catch (e) {
            alert('网络错误: ' + e.message);
            btn.disabled = false;
            btn.textContent = origText;
        }
    }

    // ==================== Telegram Helpers ====================

    async function _detectTgChatId() {
        const token = document.getElementById('configTgToken').value.trim();
        if (!token) { alert('请先填写 Bot Token'); return; }

        const btn = document.getElementById('detectChatIdBtn');
        const hint = document.getElementById('tgChatIdHint');
        btn.disabled = true; btn.textContent = '🔍 检测中...';

        try {
            const res = await fetch(`/api/agents/${agentId}/telegram/detect-chat-id`, {
                method: 'POST',
                headers: { ...hdrs(), 'Content-Type': 'application/json' },
                body: JSON.stringify({ bot_token: token })
            });
            const data = await res.json();

            if (data.status === 'found') {
                document.getElementById('configTgChatId').value = data.chat_id;
                hint.innerHTML = `✅ 检测到用户: <b>${data.first_name || ''} ${data.username ? '@'+data.username : ''}</b> (${data.chat_id})`;
                hint.style.color = '#6da89b';
            } else {
                hint.textContent = data.message || data.detail || '未找到';
                hint.style.color = '#e2b96a';
            }
        } catch (e) {
            hint.textContent = '网络错误: ' + e.message;
            hint.style.color = '#b8868a';
        } finally {
            btn.disabled = false; btn.textContent = '🔍 自动获取';
        }
    }

    async function _testTgPush() {
        const token = document.getElementById('configTgToken').value.trim();
        const chatId = document.getElementById('configTgChatId').value.trim();
        if (!token || !chatId) { alert('请先填写 Bot Token 和 Chat ID'); return; }

        const btn = document.getElementById('testTgBtn');
        const result = document.getElementById('tgTestResult');
        btn.disabled = true; btn.textContent = '📤 发送中...';

        try {
            const res = await fetch(`/api/agents/${agentId}/telegram/test-push`, {
                method: 'POST',
                headers: { ...hdrs(), 'Content-Type': 'application/json' },
                body: JSON.stringify({ bot_token: token, chat_id: chatId })
            });
            const data = await res.json();
            if (res.ok) {
                result.textContent = '✅ 发送成功，请查看 Telegram';
                result.style.color = '#6da89b';
            } else {
                result.textContent = '❌ ' + (data.detail || '发送失败');
                result.style.color = '#b8868a';
            }
        } catch (e) {
            result.textContent = '❌ 网络错误';
            result.style.color = '#b8868a';
        } finally {
            btn.disabled = false; btn.textContent = '📤 发送测试消息';
        }
    }

    // Load insight badge on init
    setTimeout(() => _updateInsightBadge(), 500);

    return { saveConfig, resetConfig, deleteAgent, sendChat, goView, openEditModal, closeEditModal, saveProfile, addTokens, startResearch, showDetail, closeDetail, editFact, saveFactEdit, cancelEdit, deleteFact, togglePrivacy, createNewSession, deleteCurrentSession, switchSession, selectPreset, uploadAvatar, clearAvatar, togglePublish, closeModal, confirmModal, toggleMobileAvatar, openImportModal, closeImportModal, _handleImportFile, _handleImportZip, _deleteImport, _openNotionPanel, _notionConnect, _notionGoBack, _notionUpdateCount, _notionImportSelected, _notionDisconnect, loadInsights, filterInsights, loadMoreInsights, loadTaskConfig, _onTaskToggle, _onTaskFreqChange, _onTaskScheduleChange, _runTaskNow, _testRunTask, _detectTgChatId, _testTgPush };
})();
