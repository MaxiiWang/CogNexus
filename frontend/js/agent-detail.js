// CogNexus Agent Detail — Native Rendering (1:1 from brain-visual)
const AgentDetail = (function() {
    let agentId = null;
    let agentData = null;
    let currentView = null;
    let viewCleanup = null;

    function getToken() { return localStorage.getItem('cog_token'); }
    function hdrs() { return { 'Authorization': 'Bearer ' + getToken() }; }
    function jsonHdrs() { return { ...hdrs(), 'Content-Type': 'application/json' }; }
    function requireAuth() { if (!getToken()) { location.href = '/?login=1'; return false; } return true; }
    function getNs() { return (agentData && agentData.namespace) || 'default'; }
    function esc(s) { const d = document.createElement('div'); d.textContent = s; return d.innerHTML; }

    // ===== Init =====
    function init() {
        if (!requireAuth()) return;
        const p = new URLSearchParams(location.search);
        agentId = p.get('id');
        if (!agentId) { location.href = '/dashboard'; return; }
        setupTabs();
        setupViewSwitcher();
        loadAgent();
        // Auto-switch tab if ?tab=xxx
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
                if (tab.dataset.tab === 'chat' && !chatInitialized) initChatTab();
            });
        });
    }

    function initChatTab() {
        chatInitialized = true;
        const box = document.getElementById('chatArea');
        renderChat(box, {style:{}}, {style:{}});
        initLive2D();
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
        try {
            const token = getToken();
            if (!token) return;
            const payload = JSON.parse(atob(token.split('.')[1]));
            const userId = payload.sub || payload.user_id;
            if (agentData.owner_id && agentData.owner_id === userId) {
                document.getElementById('ovEditBtn').style.display = '';
                document.getElementById('ovTokenMgmt').style.display = '';
                loadTokens();
            }
        } catch {}
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
        const note = document.getElementById('ovTokenNote').value.trim();
        // For CogNexus, we prompt user to paste token values
        const tokenValue = prompt('请粘贴要添加的 Token 值（可粘贴多个，每行一个）：');
        if (!tokenValue) return;
        const tokens = tokenValue.split('\n').map(t => t.trim()).filter(Boolean);
        if (!tokens.length) return;
        try {
            const r = await fetch('/api/agents/' + agentId + '/tokens', {
                method: 'POST', headers: jsonHdrs(),
                body: JSON.stringify({ tokens: tokens })
            });
            if (!r.ok) throw new Error();
            toast('Token 添加成功');
            loadTokens();
        } catch { toast('添加失败', 'error'); }
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

        box.style.display = 'flex';
        box.style.flexDirection = 'column';
        box.style.height = '100%';

        box.innerHTML =
            '<div style="padding:16px 20px;border-bottom:1px solid rgba(255,255,255,0.06);display:flex;align-items:center;gap:10px;">' +
            '<span style="font-size:1.2em;">💬</span>' +
            '<span style="font-family:Playfair Display,Georgia,serif;color:#e2b96a;font-weight:700;">知识问答</span>' +
            '<span style="color:#6b665e;font-size:0.8em;">基于已存储知识回答</span></div>' +
            '<div id="avatarContainer" style="display:flex;justify-content:center;padding:16px 0;background:linear-gradient(180deg,rgba(30,32,37,1) 0%,rgba(26,26,34,0.5) 100%);border-bottom:1px solid rgba(255,255,255,0.04);min-height:200px;max-height:280px;overflow:hidden;' + (agentData && agentData.avatar_model_url ? '' : 'display:none;') + '">' +
            '<canvas id="avatarCanvas" style="max-height:260px;"></canvas>' +
            '</div>' +
            '<div id="chatMessages" style="flex:1;overflow-y:auto;padding:20px;display:flex;flex-direction:column;gap:12px;">' +
            '<div style="text-align:center;color:#6b665e;padding:40px 0;font-size:0.88em;">输入问题，开始对话</div></div>' +
            '<div style="padding:12px 16px;border-top:1px solid rgba(255,255,255,0.06);display:flex;gap:10px;">' +
            '<input id="chatInput" type="text" placeholder="输入你的问题..." style="flex:1;background:rgba(255,255,255,0.04);border:1px solid rgba(255,255,255,0.1);border-radius:8px;padding:10px 14px;color:#e8e4df;font-size:0.9em;outline:none;">' +
            '<button id="chatSendBtn" style="background:#6da89b;color:#fff;border:none;border-radius:8px;padding:10px 18px;cursor:pointer;font-weight:600;font-size:0.9em;transition:background 0.15s;" onmouseover="this.style.background=\'#5e9488\'" onmouseout="this.style.background=\'#6da89b\'">发送</button></div>';

        document.getElementById('chatSendBtn').addEventListener('click', sendChat);
        document.getElementById('chatInput').addEventListener('keydown', e => {
            if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendChat(); }
        });
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
        if (msgs.querySelector('div[style*="text-align:center"]')) msgs.innerHTML = '';

        // User bubble
        msgs.innerHTML += '<div style="align-self:flex-end;background:rgba(109,168,155,0.15);color:#e8e4df;padding:10px 14px;border-radius:12px 12px 2px 12px;max-width:80%;font-size:0.9em;line-height:1.5;">' + esc(q) + '</div>';

        // AI bubble (will be streamed into)
        const lid = 'ld-' + Date.now();
        msgs.innerHTML += '<div id="' + lid + '" style="align-self:flex-start;background:rgba(255,255,255,0.03);color:#e8e4df;padding:12px 16px;border-radius:12px 12px 12px 2px;max-width:80%;font-size:0.9em;line-height:1.7;border:1px solid rgba(255,255,255,0.04);min-height:20px;"></div>';
        msgs.scrollTop = msgs.scrollHeight;

        const el = document.getElementById(lid);
        let fullText = '';

        try {
            const url = '/api/knowledge/' + getNs() + '/chat/stream?q=' + encodeURIComponent(q);
            const resp = await fetch(url, { headers: hdrs() });

            if (!resp.ok) {
                // Fallback to non-stream
                const r2 = await fetch('/api/knowledge/' + getNs() + '/chat', {
                    method: 'POST', headers: jsonHdrs(),
                    body: JSON.stringify({ message: q })
                });
                const d = await r2.json();
                el.innerHTML = esc(d.response || d.answer || '').replace(/\n/g, '<br>');
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
                buffer = lines.pop(); // keep incomplete line

                for (const line of lines) {
                    if (!line.startsWith('data: ')) continue;
                    try {
                        const ev = JSON.parse(line.slice(6));
                        if (ev.type === 'content') {
                            if (window.avatarCallback) window.avatarCallback('speaking');
                            fullText += ev.text;
                            el.innerHTML = esc(fullText).replace(/\n/g, '<br>');
                            msgs.scrollTop = msgs.scrollHeight;
                        } else if (ev.type === 'error') {
                            el.innerHTML += '<br><span style="color:#b8868a;">' + esc(ev.message) + '</span>';
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
            if (window.avatarCallback) window.avatarCallback('idle');
            btn.disabled = false;
            btn.textContent = '发送';
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
        // Load persona
        loadPersona();
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
                    im_config: JSON.stringify({ telegram: { bot_token: document.getElementById('configTgToken').value, chat_id: document.getElementById('configTgChatId').value } })
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
        if (!confirm('确定要删除该 Agent 吗？此操作不可撤销。')) return;
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

        // Disable all research buttons
        const btns = document.querySelectorAll('#personaFields button[onclick*="startResearch"]');
        btns.forEach(b => b.disabled = true);
        statusEl.style.display = 'block';
        statusEl.style.color = 'var(--text-muted)';
        const depthLabel = depth === 'deep' ? '深度' : '普通';
        statusEl.textContent = '🔍 正在进行' + depthLabel + '调研（搜索 → 提取 → LLM 生成），预计 1-3 分钟...';

        try {
            const controller = new AbortController();
            const timeoutId = setTimeout(() => controller.abort(), 180000); // 3 min timeout

            const r = await fetch('/api/knowledge/' + ns + '/research-character', {
                method: 'POST', headers: jsonHdrs(),
                body: JSON.stringify({ reference_names: names, depth: depth }),
                signal: controller.signal
            });
            clearTimeout(timeoutId);

            const d = await r.json();
            if (r.ok) {
                statusEl.style.color = '#6da89b';
                statusEl.innerHTML = '✅ ' + esc(d.message || '调研完成') +
                    (d.persona_summary?.traits ? '<br><span style="font-size:0.85em;color:var(--text-muted);">特征: ' + esc(d.persona_summary.traits.join(', ')) + '</span>' : '');
                toast('调研完成，Persona 和知识已更新');
                loadPersona();
            } else {
                statusEl.style.color = '#b8868a';
                statusEl.textContent = '❌ ' + (d.detail || '调研失败');
            }
        } catch (e) {
            if (e.name === 'AbortError') {
                statusEl.style.color = '#d4a574';
                statusEl.textContent = '⏱️ 调研时间较长，请稍后刷新页面查看结果（后台仍在处理）';
            } else {
                statusEl.style.color = '#b8868a';
                statusEl.textContent = '❌ 请求失败: ' + (e.message || '网络错误');
            }
        }
        btns.forEach(b => b.disabled = false);
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
        if (!confirm('确定删除此知识条目？此操作不可撤销，将同时删除图谱中的关联。')) return;
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
            if (!confirm('确定要将此条目' + action + '？')) return;

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

        const container = document.getElementById('avatarContainer');
        const canvas = document.getElementById('avatarCanvas');
        if (!container || !canvas) return;

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
            const app = new PIXI.Application({
                view: canvas,
                autoStart: true,
                backgroundAlpha: 0,
                width: 280,
                height: 260,
                antialias: true,
            });

            const model = await PIXI.live2d.Live2DModel.from(agentData.avatar_model_url);

            // Scale and position
            const scale = Math.min(260 / model.height, 280 / model.width) * 0.8;
            model.scale.set(scale);
            model.x = (280 - model.width * scale) / 2;
            model.y = (260 - model.height * scale) / 2;

            // Enable auto interaction (eye tracking)
            model.anchor.set(0.5, 0.5);
            model.x = 140;
            model.y = 140;

            app.stage.addChild(model);

            // Start idle motion
            try {
                model.motion('Idle', 0, PIXI.live2d.MotionPriority.IDLE);
            } catch(e) {}

            // Store reference for chat integration
            window._live2dModel = model;
            window._live2dApp = app;

            // Set up avatar state callback
            window.avatarCallback = function(state) {
                if (!window._live2dModel) return;
                const m = window._live2dModel;
                const cm = m.internalModel?.coreModel;
                if (!cm) return;

                switch(state) {
                    case 'thinking':
                        stopMouthAnimation();
                        try { cm.setParameterValueById('ParamMouthOpenY', 0); } catch(e) {}
                        break;
                    case 'speaking':
                        startMouthAnimation();
                        break;
                    case 'idle':
                        stopMouthAnimation();
                        try { cm.setParameterValueById('ParamMouthOpenY', 0); } catch(e) {}
                        try { m.motion('Idle', 0, PIXI.live2d.MotionPriority.IDLE); } catch(e) {}
                        break;
                }
            };

        } catch(e) {
            console.error('Live2D init error:', e);
            container.style.display = 'none';
        }
    }

    let _mouthAnimFrame = null;

    function startMouthAnimation() {
        const cm = window._live2dModel?.internalModel?.coreModel;
        if (!cm) return;
        let t = 0;
        function animate() {
            t += 0.15;
            const openness = (Math.sin(t * 3.5) * 0.3 + Math.sin(t * 7.1) * 0.2 + 0.5) * 0.8;
            try { cm.setParameterValueById('ParamMouthOpenY', Math.max(0, Math.min(1, openness))); } catch(e) {}
            _mouthAnimFrame = requestAnimationFrame(animate);
        }
        stopMouthAnimation();
        animate();
    }

    function stopMouthAnimation() {
        if (_mouthAnimFrame) {
            cancelAnimationFrame(_mouthAnimFrame);
            _mouthAnimFrame = null;
        }
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

    return { saveConfig, resetConfig, deleteAgent, sendChat, goView, openEditModal, closeEditModal, saveProfile, addTokens, startResearch, showDetail, closeDetail, editFact, saveFactEdit, cancelEdit, deleteFact, togglePrivacy };
})();
