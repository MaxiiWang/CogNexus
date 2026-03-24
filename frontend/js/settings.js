// CogNexus Settings Page
const Settings = (function() {

    // ===== Auth helpers =====
    function getToken() {
        return localStorage.getItem('cog_token');
    }

    function headers() {
        return { 'Authorization': `Bearer ${getToken()}` };
    }

    function jsonHeaders() {
        return { ...headers(), 'Content-Type': 'application/json' };
    }

    function requireAuth() {
        if (!getToken()) {
            location.href = '/?login=1';
            return false;
        }
        return true;
    }

    // ===== Init =====
    function init() {
        if (!requireAuth()) return;
        setupNav();
        setupProviderToggle();
        loadSettings();
        loadAccount();
    }

    // ===== Section Navigation =====
    function setupNav() {
        document.querySelectorAll('.settings-nav-item').forEach(item => {
            item.addEventListener('click', () => {
                const section = item.dataset.section;
                document.querySelectorAll('.settings-nav-item').forEach(i => i.classList.remove('active'));
                item.classList.add('active');
                document.querySelectorAll('.settings-section').forEach(s => s.classList.remove('active'));
                const target = document.getElementById('section-' + section);
                if (target) target.classList.add('active');
            });
        });
    }

    // ===== Provider Toggle =====
    function setupProviderToggle() {
        const select = document.getElementById('llmProvider');
        if (!select) return;
        select.addEventListener('change', () => {
            const customGroup = document.getElementById('customEndpointGroup');
            if (customGroup) {
                customGroup.style.display = select.value === 'custom' ? 'block' : 'none';
            }
        });
    }

    // ===== Load Settings =====
    async function loadSettings() {
        try {
            const resp = await fetch('/api/settings', { headers: headers() });
            if (!resp.ok) return;
            const data = await resp.json();

            if (data.default_llm_provider) {
                document.getElementById('llmProvider').value = data.default_llm_provider;
            }
            if (data.default_model) {
                document.getElementById('llmModel').value = data.default_model;
            }
            // API key is masked server-side
            if (data.default_llm_key_masked) {
                document.getElementById('llmApiKey').placeholder = data.default_llm_key_masked;
            }
            if (data.custom_endpoint) {
                document.getElementById('llmEndpoint').value = data.custom_endpoint;
            }

            // IM config is per-Agent (see Agent Detail → Config tab)

            // Language
            if (data.ui_language) {
                document.getElementById('accountLanguage').value = data.ui_language;
            }

            // Show custom endpoint group if needed
            if (data.default_llm_provider === 'custom') {
                document.getElementById('customEndpointGroup').style.display = 'block';
            }
        } catch {
            // Settings API not available yet
        }
    }

    // ===== Load Account =====
    function loadAccount() {
        const user = JSON.parse(localStorage.getItem('cog_user') || 'null');
        if (!user) return;

        document.getElementById('accountName').textContent = user.username || '—';
        document.getElementById('accountAvatar').textContent = (user.username || '?').charAt(0).toUpperCase();
        document.getElementById('accountMeta').textContent = user.email || `ATP: ${user.atp_balance || 0}`;

        // Set language selector
        const locale = localStorage.getItem('cog_locale') || 'en';
        document.getElementById('accountLanguage').value = locale;
    }

    // ===== Save LLM Settings =====
    async function saveLLM() {
        const payload = {
            default_llm_provider: document.getElementById('llmProvider').value,
            default_model: document.getElementById('llmModel').value,
        };

        const apiKey = document.getElementById('llmApiKey').value;
        if (apiKey) payload.default_llm_key = apiKey;

        if (payload.default_llm_provider === 'custom') {
            payload.custom_endpoint = document.getElementById('llmEndpoint').value;
        }

        try {
            const resp = await fetch('/api/settings/llm', {
                method: 'PUT',
                headers: jsonHeaders(),
                body: JSON.stringify(payload)
            });
            if (!resp.ok) throw new Error('Save failed');
            showToast(t('settings.llm_saved') || 'LLM settings saved');
        } catch {
            showToast(t('common.network_error') || 'Failed to save', 'error');
        }
    }

    // ===== Test LLM Connection =====
    async function testLLM() {
        showToast(t('settings.testing') || 'Testing connection...');
        try {
            const resp = await fetch('/api/settings/llm/test', {
                method: 'POST',
                headers: headers()
            });
            if (!resp.ok) throw new Error('Test failed');
            const result = await resp.json();
            showToast(result.message || 'Connection successful');
        } catch {
            showToast(t('settings.test_failed') || 'Connection test failed', 'error');
        }
    }

    // ===== Save IM Settings =====
    // IM config is per-Agent (see Agent Detail → Config tab)

    // ===== Language =====
    function changeLanguage(lang) {
        localStorage.setItem('cog_locale', lang);
        if (typeof I18N !== 'undefined') {
            I18N._locale = lang;
            I18N.apply();
        }
    }

    // ===== Logout =====
    function doLogout() {
        localStorage.removeItem('cog_token');
        localStorage.removeItem('cog_user');
        location.href = '/';
    }

    // ===== Helpers =====
    function t(key) {
        return (typeof I18N !== 'undefined' && I18N.t) ? I18N.t(key) : null;
    }

    function showToast(message, type) {
        const toast = document.createElement('div');
        toast.style.cssText = `
            position: fixed; bottom: 24px; right: 24px; z-index: 9999;
            padding: 12px 20px; border-radius: 10px;
            background: ${type === 'error' ? 'rgba(184,134,138,0.95)' : 'rgba(109,168,155,0.95)'};
            color: #fff; font-size: 0.88em; font-weight: 500;
            box-shadow: 0 4px 12px rgba(0,0,0,0.3);
            animation: fade-up 0.3s ease both;
        `;
        toast.textContent = message;
        document.body.appendChild(toast);
        setTimeout(() => {
            toast.style.opacity = '0';
            toast.style.transition = 'opacity 0.3s';
            setTimeout(() => toast.remove(), 300);
        }, 3000);
    }

    // ===== Boot =====
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }

    return { saveLLM, testLLM, changeLanguage, logout: doLogout };
})();
