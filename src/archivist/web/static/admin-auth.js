(function () {
    'use strict';

    const widget = document.getElementById('adminAuthWidget');
    if (!widget) return;
    const trigger = document.getElementById('adminAuthTrigger');
    const panel = document.getElementById('adminAuthPanel');
    const closeButton = document.getElementById('adminAuthClose');
    const title = document.getElementById('adminAuthTitle');
    const form = document.getElementById('adminAuthForm');
    const usernameInput = document.getElementById('adminAuthUsername');
    const passwordInput = document.getElementById('adminAuthPassword');
    const submit = document.getElementById('adminAuthSubmit');
    const account = document.getElementById('adminAuthAccount');
    const accountName = document.getElementById('adminAuthAccountName');
    const logout = document.getElementById('adminAuthLogout');
    const status = document.getElementById('adminAuthStatus');
    const main = document.querySelector('main');
    const isPrivateDocument = window.location.pathname.startsWith('/docs/');
    const isLoginPage = /^\/login\/?$/.test(window.location.pathname) || Boolean(document.querySelector('[data-auth-login-page]'));
    let authenticated = false;
    let csrfToken = '';
    let username = '';
    let sending = false;
    let generation = 0;
    let retryUntil = 0;
    let retryTimer;
    let channel;

    function statusText(text, isError) {
        status.textContent = text;
        status.classList.toggle('is-error', Boolean(isError));
    }

    function updateButtons() {
        submit.disabled = sending || Date.now() < retryUntil;
        logout.disabled = sending || !csrfToken;
        usernameInput.readOnly = sending;
        passwordInput.readOnly = sending;
        form.setAttribute('aria-busy', String(sending));
    }

    function renderAccount() {
        form.hidden = authenticated;
        account.hidden = !authenticated;
        title.textContent = authenticated ? '管理员' : '管理员登录';
        accountName.textContent = authenticated ? username : '';
        const label = authenticated ? '管理员账户' : '管理员登录';
        trigger.setAttribute('aria-label', label);
        trigger.title = label;
        trigger.classList.toggle('is-authenticated', authenticated);
        updateButtons();
    }

    function notifyAuthChange() {
        document.dispatchEvent(new CustomEvent('archivist:auth-change', {detail: {authenticated: authenticated}}));
    }

    function clearPrivate() {
        document.querySelectorAll('[data-admin-rating], [data-admin-rating-reason]').forEach(function (element) {
            element.replaceChildren();
            element.hidden = true;
        });
        document.querySelectorAll('[data-rating]').forEach(function (element) { delete element.dataset.rating; });
        document.querySelectorAll('[data-admin-doc-link]').forEach(function (element) { element.hidden = true; });
        document.querySelectorAll('[data-admin-doc-count]').forEach(function (element) { element.textContent = ''; });
        document.querySelectorAll('[data-admin-score-reasons]').forEach(function (element) {
            element.hidden = element.dataset.publicReasons !== 'true';
            if (element.hidden) element.open = false;
        });
        const sort = document.getElementById('sortSelect');
        if (sort) {
            const privateOption = sort.querySelector('option[value="rating"]');
            if (privateOption) {
                if (sort.value === 'rating') sort.value = 'published_date';
                privateOption.remove();
            }
        }
        if (isPrivateDocument && main) main.hidden = true;
        notifyAuthChange();
    }

    function fillPrivate(data) {
        const papers = data && data.papers && typeof data.papers === 'object' ? data.papers : {};
        document.querySelectorAll('.paper-card[data-slug], [data-private-paper-slug]').forEach(function (root) {
            const slug = root.dataset.privatePaperSlug || root.dataset.slug;
            const paper = Object.prototype.hasOwnProperty.call(papers, slug) ? papers[slug] : null;
            const rating = paper && Number.isFinite(paper.rating) ? paper.rating : null;
            const ratingText = rating === null ? '—' : String(rating);
            root.querySelectorAll('[data-admin-rating]').forEach(function (element) {
                const number = document.createElement('b');
                number.textContent = ratingText;
                element.replaceChildren(document.createTextNode('用户 '), number);
                element.hidden = false;
            });
            if (root.classList.contains('paper-card') && rating !== null) root.dataset.rating = String(rating);
            root.querySelectorAll('[data-admin-rating-reason]').forEach(function (element) {
                const reason = paper && typeof paper.rating_reason === 'string' ? paper.rating_reason : '';
                element.replaceChildren();
                element.hidden = !reason;
                if (reason) {
                    const label = document.createElement('strong');
                    label.textContent = '用户 ' + ratingText + '：';
                    element.replaceChildren(label, document.createTextNode(reason));
                    const details = element.closest('[data-admin-score-reasons]');
                    if (details) details.hidden = false;
                }
            });
        });
        document.querySelectorAll('[data-admin-doc-link]').forEach(function (element) { element.hidden = false; });
        document.querySelectorAll('[data-admin-doc-count]').forEach(function (element) {
            element.textContent = Number.isSafeInteger(data.doc_count) && data.doc_count >= 0 ? String(data.doc_count) : '';
        });
        if (document.getElementById('paperCards')) {
            const sort = document.getElementById('sortSelect');
            if (sort && !sort.querySelector('option[value="rating"]')) {
                const option = document.createElement('option');
                option.value = 'rating';
                option.textContent = '用户评分 ↓';
                sort.appendChild(option);
            }
        }
        if (isPrivateDocument && main) main.hidden = false;
        notifyAuthChange();
    }

    async function request(path, body, token) {
        const controller = new AbortController();
        const timeout = window.setTimeout(function () { controller.abort(); }, 12000);
        try {
            const headers = {'Content-Type': 'application/json', 'Accept': 'application/json'};
            if (token) headers['X-CSRF-Token'] = token;
            const response = await fetch(path, {
                method: 'POST', credentials: 'same-origin', cache: 'no-store',
                headers: headers, body: JSON.stringify(body || {}), signal: controller.signal
            });
            let data;
            try { data = await response.json(); } catch (_) { data = {}; }
            if (!data || typeof data !== 'object' || Array.isArray(data)) data = {};
            return {response: response, data: data};
        } finally { window.clearTimeout(timeout); }
    }

    function errorMessage(data, fallback) {
        const error = data && data.error;
        return typeof error === 'string' ? error : (error && typeof error.message === 'string' ? error.message : (typeof data.message === 'string' ? data.message : fallback));
    }

    function cooldown(response) {
        const header = response.headers.get('Retry-After');
        const seconds = Number(header);
        const until = header && Number.isFinite(seconds) ? Date.now() + Math.max(1, seconds) * 1000 : Date.parse(header || '');
        retryUntil = Number.isFinite(until) && until > Date.now() ? until : Date.now() + 60000;
        window.clearTimeout(retryTimer);
        retryTimer = window.setTimeout(function () { retryUntil = 0; updateButtons(); }, Math.min(retryUntil - Date.now(), 2147483647));
    }

    function safeNext() {
        const next = new URLSearchParams(window.location.search).get('next') || '/reading/';
        if (!next.startsWith('/') || next.startsWith('//') || /[\\\u0000-\u0020\u007f]/.test(next)) return '/reading/';
        try {
            const target = new URL(next, window.location.origin);
            if (target.origin !== window.location.origin || /^\/login\/?$/.test(target.pathname)) return '/reading/';
            return target.pathname + target.search + target.hash;
        } catch (_) { return '/reading/'; }
    }

    function privateDocumentExpired() {
        if (!isPrivateDocument) return;
        if (main) main.replaceChildren();
        window.location.replace('/login/?next=' + encodeURIComponent(window.location.pathname + window.location.search));
    }

    async function refreshSession() {
        if (sending || document.visibilityState === 'hidden') return;
        const version = ++generation;
        authenticated = false;
        csrfToken = '';
        clearPrivate();
        updateButtons();
        try {
            const session = await request('/api/auth/session');
            if (version !== generation) return;
            if (session.response.status === 401) {
                username = '';
                passwordInput.value = '';
                renderAccount();
                privateDocumentExpired();
                return;
            }
            if (!session.response.ok || typeof session.data.authenticated !== 'boolean') throw new Error('session unavailable');
            if (!session.data.authenticated) {
                username = '';
                renderAccount();
                if (!session.data.enabled && !panel.hidden) statusText('登录暂不可用。', true);
                privateDocumentExpired();
                return;
            }
            csrfToken = typeof session.data.csrf_token === 'string' ? session.data.csrf_token : '';
            const privateData = await request('/api/admin/data', {}, csrfToken);
            if (version !== generation) return;
            if (privateData.response.status === 401) {
                csrfToken = '';
                username = '';
                passwordInput.value = '';
                renderAccount();
                privateDocumentExpired();
                return;
            }
            if (!privateData.response.ok) throw new Error('private data unavailable');
            authenticated = true;
            username = typeof session.data.username === 'string' ? session.data.username : '管理员';
            fillPrivate(privateData.data);
            renderAccount();
            if (isLoginPage) window.location.replace(safeNext());
        } catch (_) {
            if (version !== generation) return;
            authenticated = false;
            csrfToken = '';
            renderAccount();
            if (!panel.hidden) statusText('登录状态暂无法确认，请重试。', true);
        }
    }

    function openPanel() {
        document.dispatchEvent(new CustomEvent('archivist:corner-open', {detail: 'auth'}));
        panel.hidden = false;
        trigger.setAttribute('aria-expanded', 'true');
        statusText('', false);
        renderAccount();
        (authenticated ? logout : usernameInput).focus({preventScroll: true});
        refreshSession();
    }

    function closePanel(restoreFocus) {
        if (panel.hidden) return;
        panel.hidden = true;
        trigger.setAttribute('aria-expanded', 'false');
        passwordInput.value = '';
        if (restoreFocus) trigger.focus({preventScroll: true});
    }

    trigger.addEventListener('click', function () {
        if (panel.hidden) openPanel(); else closePanel(false);
    });
    closeButton.addEventListener('click', function () { closePanel(true); });
    document.addEventListener('keydown', function (event) {
        if (event.key === 'Escape' && !panel.hidden) { event.preventDefault(); closePanel(true); }
    });
    document.addEventListener('pointerdown', function (event) {
        if (!panel.hidden && !widget.contains(event.target)) closePanel(false);
    });
    document.addEventListener('archivist:corner-open', function (event) {
        if (event.detail !== 'auth') closePanel(false);
    });

    form.addEventListener('submit', async function (event) {
        event.preventDefault();
        if (sending || Date.now() < retryUntil || !form.reportValidity()) return;
        const version = ++generation;
        sending = true;
        statusText('', false);
        submit.textContent = '登录中…';
        updateButtons();
        try {
            const result = await request('/api/auth/login', {username: usernameInput.value.trim(), password: passwordInput.value});
            if (version !== generation) return;
            if (result.response.status === 429) {
                cooldown(result.response);
                statusText(errorMessage(result.data, '尝试频繁，请稍后重试。'), true);
            } else if (result.response.ok && result.data.authenticated === true) {
                passwordInput.value = '';
                if (channel) channel.postMessage({type: 'session-changed'});
                sending = false;
                await refreshSession();
                if (authenticated && !isLoginPage) closePanel(true);
            } else {
                statusText(errorMessage(result.data, '账号或密码不正确。'), true);
            }
        } catch (_) {
            if (version === generation) statusText('登录未完成，请重试。', true);
        } finally {
            passwordInput.value = '';
            sending = false;
            submit.textContent = '登录';
            updateButtons();
        }
    });

    logout.addEventListener('click', async function () {
        if (sending) return;
        const token = csrfToken;
        const version = ++generation;
        sending = true;
        authenticated = false;
        csrfToken = '';
        clearPrivate();
        passwordInput.value = '';
        statusText('', false);
        logout.textContent = '退出中…';
        updateButtons();
        try {
            const result = await request('/api/auth/logout', {}, token);
            if (version !== generation) return;
            if (result.response.ok || result.response.status === 401) {
                username = '';
                if (channel) channel.postMessage({type: 'logout'});
                renderAccount();
                closePanel(true);
                if (isPrivateDocument) { if (main) main.replaceChildren(); window.location.replace('/reading/'); }
            } else {
                authenticated = true;
                csrfToken = token;
                statusText(errorMessage(result.data, '退出未完成，请重试。'), true);
            }
        } catch (_) {
            if (version === generation) {
                authenticated = true;
                csrfToken = token;
                statusText('退出未完成，请重试。', true);
            }
        } finally {
            sending = false;
            logout.textContent = '退出登录';
            updateButtons();
        }
    });

    try {
        channel = new BroadcastChannel('archivist-admin-session');
        channel.addEventListener('message', function (event) {
            if (!event.data || !['logout', 'session-changed'].includes(event.data.type)) return;
            ++generation;
            authenticated = false;
            csrfToken = '';
            passwordInput.value = '';
            clearPrivate();
            renderAccount();
            if (event.data.type === 'logout') privateDocumentExpired();
            else refreshSession();
        });
    } catch (_) { /* Focus and visibility checks also cover browsers without BroadcastChannel. */ }
    window.addEventListener('pagehide', function () {
        ++generation;
        authenticated = false;
        csrfToken = '';
        passwordInput.value = '';
        clearPrivate();
        renderAccount();
    });
    window.addEventListener('pageshow', function (event) { if (event.persisted) refreshSession(); });
    window.addEventListener('focus', function () { refreshSession(); });
    document.addEventListener('visibilitychange', function () {
        if (document.visibilityState === 'hidden') {
            ++generation;
            authenticated = false;
            csrfToken = '';
            passwordInput.value = '';
            clearPrivate();
            renderAccount();
        } else refreshSession();
    });
    authenticated = false;
    clearPrivate();
    renderAccount();
    if (isLoginPage) openPanel();
    else refreshSession();
}());
