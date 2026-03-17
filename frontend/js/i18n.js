// CogNexus i18n
const I18N = {
    _locale: (localStorage.getItem('cog_locale') || (navigator.language.startsWith('zh') ? 'zh' : 'en')),

    messages: {
        en: {
            // Nav
            'nav.market': 'Market',
            'nav.simulation': 'Simulation',
            'nav.docs': 'Docs',
            'nav.dashboard': 'Dashboard',
            'nav.login': 'Login',
            'nav.register': 'Register',
            'nav.login_register': 'Login / Register',
            'nav.logout': 'Logout',

            // Common
            'common.save': 'Save',
            'common.cancel': 'Cancel',
            'common.create': 'Create',
            'common.edit': 'Edit',
            'common.delete': 'Delete',
            'common.loading': 'Loading...',
            'common.confirm': 'Confirm',

            // Simulation
            'sim.title': 'Title',
            'sim.question': 'Core Question',
            'sim.description': 'Description',
            'sim.resolution': 'Resolution Criteria',
            'sim.create': 'Create Simulation',
            'sim.configure': 'Configure your multi-agent simulation',
            'sim.mode.standard': 'Standard',
            'sim.mode.monte_carlo': 'Monte Carlo',
            'sim.recruit': 'Recruit',
            'sim.smart_recruit': 'Smart Recruit',
            'sim.execute_round': 'Execute Round',
            'sim.retry': 'Retry Failed',
            'sim.close_round': 'Close Round',
            'sim.resolve': 'Resolve',
            'sim.env_injection': 'Environment Injection',
            'sim.env_placeholder': 'e.g., China announces counter-tariffs, US stock futures drop 3%...',
            'sim.env_skip': 'Skip (no changes)',
            'sim.env_inject': 'Inject & Execute',
            'sim.estimated_cost': 'Estimated cost',
            'sim.classification': 'Classification',
            'sim.outcome': 'Outcome',
            'sim.parameters': 'Parameters',
            'sim.binary': 'Binary (Yes/No)',
            'sim.multi': 'Multiple Choice',
            'sim.rounds': 'Rounds',
            'sim.stake_agent': 'Stake/Agent',
            'sim.min_agents': 'Min Agents',
            'sim.max_agents': 'Max Agents',
            'sim.target_population': 'Target Population',
            'sim.llm_calls': '~15-25 LLM calls',
            'sim.mode': 'Mode',

            // Dashboard
            'dash.my_agents': 'My Agents',
            'dash.my_sims': 'My Simulations',
            'dash.atp_balance': 'ATP Balance',
            'dash.tokens_purchased': 'Tokens Purchased',
            'dash.publish_agent': '+ Publish Agent',
            'dash.create_sim': '+ Create Simulation',

            // Status
            'status.draft': 'Draft',
            'status.recruiting': 'Recruiting',
            'status.active': 'Active',
            'status.closed': 'Closed',
            'status.resolved': 'Resolved',
            'status.settled': 'Settled',
        },
        zh: {
            'nav.market': '市场',
            'nav.simulation': 'Simulation',
            'nav.docs': '文档',
            'nav.dashboard': '仪表盘',
            'nav.login': '登录',
            'nav.register': '注册',
            'nav.login_register': '登录 / 注册',
            'nav.logout': '退出登录',

            'common.save': '保存',
            'common.cancel': '取消',
            'common.create': '创建',
            'common.edit': '编辑',
            'common.delete': '删除',
            'common.loading': '加载中...',
            'common.confirm': '确认',

            'sim.title': '标题',
            'sim.question': '核心问题',
            'sim.description': '描述',
            'sim.resolution': '判定标准',
            'sim.create': '创建 Simulation',
            'sim.configure': '配置多 Agent 推演参数',
            'sim.mode.standard': '标准模式',
            'sim.mode.monte_carlo': '蒙特卡洛模式',
            'sim.recruit': '招募',
            'sim.smart_recruit': '🤖 智能招募',
            'sim.execute_round': '▶ 执行轮次',
            'sim.retry': '🔄 重试失败',
            'sim.close_round': '手动关闭轮次',
            'sim.resolve': '⚖ 判定结果',
            'sim.env_injection': '环境注入',
            'sim.env_placeholder': '例如：中国宣布对等反制措施，美股期货跌3%...',
            'sim.env_skip': '跳过（无新环境）',
            'sim.env_inject': '注入并执行',
            'sim.estimated_cost': '预计费用',
            'sim.classification': '分类',
            'sim.outcome': '结果类型',
            'sim.parameters': '参数',
            'sim.binary': '二元 (Yes/No)',
            'sim.multi': '多选项',
            'sim.rounds': '轮次',
            'sim.stake_agent': '质押/Agent',
            'sim.min_agents': '最少 Agent',
            'sim.max_agents': '最多 Agent',
            'sim.target_population': '目标模拟人数',
            'sim.llm_calls': '实际 LLM 调用约 15-25 次',
            'sim.mode': '模式',

            'dash.my_agents': '我的 Agent',
            'dash.my_sims': '我的 Simulation',
            'dash.atp_balance': 'ATP 余额',
            'dash.tokens_purchased': '购买的 Token',
            'dash.publish_agent': '+ 发布 Agent',
            'dash.create_sim': '+ 创建 Simulation',

            'status.draft': '草稿',
            'status.recruiting': '招募中',
            'status.active': '进行中',
            'status.closed': '已关闭',
            'status.resolved': '已判定',
            'status.settled': '已结算',
        }
    },

    t(key) {
        return this.messages[this._locale]?.[key] || this.messages['en']?.[key] || key;
    },

    setLocale(locale) {
        this._locale = locale;
        localStorage.setItem('cog_locale', locale);
        this.apply();
    },

    getLocale() {
        return this._locale;
    },

    apply() {
        document.querySelectorAll('[data-i18n]').forEach(el => {
            const key = el.getAttribute('data-i18n');
            const text = this.t(key);
            if (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA') {
                el.placeholder = text;
            } else {
                el.textContent = text;
            }
        });
    }
};

// Auto-apply on load
document.addEventListener('DOMContentLoaded', () => I18N.apply());

// Add language switcher to header
document.addEventListener('DOMContentLoaded', () => {
    const nav = document.querySelector('.nav-links');
    if (nav) {
        const switcher = document.createElement('button');
        switcher.className = 'btn btn-ghost btn-small';
        switcher.style.cssText = 'font-size:0.85em;padding:4px 8px;margin-left:4px;';
        switcher.textContent = I18N.getLocale() === 'zh' ? 'EN' : '中';
        switcher.onclick = () => {
            const newLocale = I18N.getLocale() === 'zh' ? 'en' : 'zh';
            I18N.setLocale(newLocale);
            switcher.textContent = newLocale === 'zh' ? 'EN' : '中';
        };
        nav.insertBefore(switcher, nav.querySelector('.auth-buttons') || nav.querySelector('.user-menu'));
    }
});
