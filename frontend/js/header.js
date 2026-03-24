// CogNexus Unified Header
(function() {
    const currentPath = window.location.pathname;

    function isActive(path) {
        if (path === '/' && currentPath === '/') return true;
        if (path !== '/' && currentPath.startsWith(path)) return true;
        return false;
    }

    const headerHTML = `
    <header>
        <div class="container">
            <nav>
                <a href="/" class="logo">CogNexus<span class="dot">.</span></a>
                <div class="nav-links">
                    <a href="/marketplace"${isActive('/marketplace') ? ' class="active"' : ''}><span data-i18n="nav.market">市场</span></a>
                    <a href="/simulation"${isActive('/simulation') ? ' class="active"' : ''}><span data-i18n="nav.simulation">Simulation</span></a>
                    <a href="/guide"${isActive('/guide') ? ' class="active"' : ''}><span data-i18n="nav.docs">文档</span></a>
                    <div class="auth-buttons" id="authButtons">
                        <a href="/" class="btn btn-secondary btn-small" id="headerLoginBtn"><span data-i18n="nav.login">登录</span></a>
                        <a href="/" class="btn btn-primary btn-small" id="headerRegisterBtn"><span data-i18n="nav.register">注册</span></a>
                    </div>
                    <div class="user-menu" id="userMenu">
                        <div class="user-dropdown">
                            <div class="user-dropdown-trigger">
                                <div class="avatar" id="userAvatar">M</div>
                                <span class="username" id="username"></span>
                                <span class="chevron">▼</span>
                            </div>
                            <div class="user-dropdown-menu">
                                <div class="user-dropdown-header">
                                    <div class="name" id="userDisplayName"></div>
                                    <div class="balance">
                                        <span class="balance-icon">⚡</span>
                                        <span class="atp-tooltip" id="atpBalance">0 ATP</span>
                                    </div>
                                </div>
                                <a href="/dashboard" class="user-dropdown-item">
                                    <span class="icon">📊</span>
                                    <span data-i18n="nav.dashboard">仪表盘</span>
                                </a>
                                <a href="/settings" class="user-dropdown-item">
                                    <span class="icon">⚙️</span>
                                    <span data-i18n="nav.settings">设置</span>
                                </a>
                                <div class="user-dropdown-divider"></div>
                                <button class="user-dropdown-item danger" onclick="logout()">
                                    <span class="icon">🚪</span>
                                    <span data-i18n="nav.logout">退出登录</span>
                                </button>
                            </div>
                        </div>
                    </div>
                </div>
            </nav>
        </div>
    </header>`;

    // Find and replace existing header, or insert at top of body
    const existingHeader = document.querySelector('header');
    if (existingHeader) {
        existingHeader.outerHTML = headerHTML;
    } else {
        document.body.insertAdjacentHTML('afterbegin', headerHTML);
    }

    // Auth check helper (pages can override)
    window._headerInitAuth = function() {
        const token = localStorage.getItem('cog_token');
        const user = JSON.parse(localStorage.getItem('cog_user') || 'null');
        if (token && user) {
            const authBtns = document.getElementById('authButtons');
            if (authBtns) authBtns.style.display = 'none';
            const menu = document.getElementById('userMenu');
            if (menu) menu.classList.add('active');
            const uname = document.getElementById('username');
            if (uname) uname.textContent = user.username;
            const dname = document.getElementById('userDisplayName');
            if (dname) dname.textContent = user.username;
            const avatar = document.getElementById('userAvatar');
            if (avatar) avatar.textContent = user.username.charAt(0).toUpperCase();
            const atp = document.getElementById('atpBalance');
            if (atp) atp.textContent = (user.atp_balance || 0) + ' ATP';
            return { token, user };
        }
        return null;
    };

    // Default logout
    if (!window.logout) {
        window.logout = function() {
            localStorage.removeItem('cog_token');
            localStorage.removeItem('cog_user');
            location.href = '/';
        };
    }

    // Auto-init auth state in header
    window._headerInitAuth && window._headerInitAuth();

    // Re-apply i18n to header if I18N is loaded
    if (typeof I18N !== 'undefined' && I18N.apply) {
        I18N.apply();
    }
})();
