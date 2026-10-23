(function () {
    'use strict';

    const widget = document.getElementById('mailboxWidget');
    if (!widget) return;

    const trigger = document.getElementById('mailboxTrigger');
    const panel = document.getElementById('mailboxPanel');
    const closeButton = document.getElementById('mailboxClose');
    const form = document.getElementById('mailboxForm');
    const message = document.getElementById('mailboxMessage');
    const website = document.getElementById('mailboxWebsite');
    const count = document.getElementById('mailboxCount');
    const submit = document.getElementById('mailboxSubmit');
    const status = document.getElementById('mailboxStatus');
    const retry = document.getElementById('mailboxRetry');
    let maxLength = Number(widget.dataset.maxLength) || 500;
    let ready = false;
    let sending = false;
    let configRequest = null;
    let retryUntil = 0;
    let retryTimer = null;
    let requestId = null;

    function showStatus(text, isError, canRetry) {
        status.textContent = text;
        status.classList.toggle('is-error', Boolean(isError));
        retry.hidden = !canRetry;
    }

    function updateForm() {
        const length = Array.from(message.value).length;
        const tooLong = length > maxLength;
        count.textContent = length + ' / ' + maxLength;
        count.classList.toggle('is-over-limit', tooLong);
        message.setAttribute('aria-invalid', String(tooLong));
        submit.disabled = !ready || sending || !message.value.trim() || tooLong || Date.now() < retryUntil;
    }

    async function request(url, options) {
        const controller = new AbortController();
        const timeout = window.setTimeout(function () { controller.abort(); }, 12000);
        try {
            return await fetch(url, Object.assign({cache: 'no-store', credentials: 'same-origin'}, options, {signal: controller.signal}));
        } finally {
            window.clearTimeout(timeout);
        }
    }

    async function responseData(response) {
        try {
            const data = await response.json();
            return data && typeof data === 'object' && !Array.isArray(data) ? data : {};
        } catch (_) { return {}; }
    }

    function errorMessage(data, fallback) {
        const error = data && data.error;
        return typeof error === 'string' ? error : (error && typeof error.message === 'string' ? error.message : (typeof data.message === 'string' ? data.message : fallback));
    }

    async function loadConfig() {
        if (configRequest) return configRequest;
        ready = false;
        showStatus('连接中…', false, false);
        updateForm();
        configRequest = (async function () {
            try {
                const response = await request('/api/mailbox/config', {headers: {'Accept': 'application/json'}});
                const data = await responseData(response);
                if (!response.ok || typeof data.enabled !== 'boolean' || !Number.isSafeInteger(data.max_length) || data.max_length < 1) throw new Error('unavailable');
                maxLength = data.max_length;
                ready = data.enabled;
                showStatus(ready ? '' : '信箱暂未开放。', !ready, !ready);
            } catch (_) {
                showStatus('信箱暂不可用。', true, true);
            } finally {
                configRequest = null;
                updateForm();
            }
        }());
        return configRequest;
    }

    function openPanel() {
        panel.hidden = false;
        trigger.setAttribute('aria-expanded', 'true');
        message.focus({preventScroll: true});
        if (!sending) loadConfig();
    }

    function closePanel(restoreFocus) {
        if (panel.hidden) return;
        panel.hidden = true;
        trigger.setAttribute('aria-expanded', 'false');
        if (restoreFocus) trigger.focus({preventScroll: true});
    }

    function cooldown(response) {
        const value = response.headers.get('Retry-After');
        const seconds = Number(value);
        const until = value && Number.isFinite(seconds) ? Date.now() + Math.max(1, seconds) * 1000 : Date.parse(value || '');
        retryUntil = Number.isFinite(until) && until > Date.now() ? until : Date.now() + 60000;
        window.clearTimeout(retryTimer);
        retryTimer = window.setTimeout(function () { retryUntil = 0; updateForm(); }, Math.min(retryUntil - Date.now(), 2147483647));
    }

    trigger.addEventListener('click', function () {
        if (panel.hidden) openPanel();
        else closePanel(false);
    });
    closeButton.addEventListener('click', function () { closePanel(true); });
    document.addEventListener('keydown', function (event) {
        if (event.key === 'Escape' && !panel.hidden) {
            event.preventDefault();
            closePanel(true);
        }
    });
    document.addEventListener('pointerdown', function (event) {
        if (!panel.hidden && !widget.contains(event.target)) closePanel(false);
    });
    message.addEventListener('input', function () {
        requestId = null;
        if (!sending && ready && Date.now() >= retryUntil) showStatus('', false, false);
        updateForm();
    });
    retry.addEventListener('click', loadConfig);
    form.addEventListener('submit', async function (event) {
        event.preventDefault();
        if (sending || !ready || Date.now() < retryUntil) return;
        const text = message.value;
        if (!text.trim()) {
            showStatus('请填写留言内容。', true, false);
            message.focus();
            return;
        }
        if (Array.from(text).length > maxLength) {
            showStatus('留言最多 ' + maxLength + ' 字。', true, false);
            message.focus();
            return;
        }

        sending = true;
        if (!requestId) requestId = window.crypto.randomUUID();
        message.readOnly = true;
        submit.textContent = '发送中…';
        form.setAttribute('aria-busy', 'true');
        showStatus('', false, false);
        updateForm();
        try {
            const response = await request('/api/mailbox/messages', {
                method: 'POST',
                headers: {'Content-Type': 'application/json', 'Accept': 'application/json'},
                body: JSON.stringify({message: text, page: window.location.pathname, website: website.value, request_id: requestId})
            });
            const data = await responseData(response);
            if (response.status === 201 && data.ok === true) {
                message.value = '';
                requestId = null;
                website.value = '';
                showStatus('已收到，谢谢你的留言。', false, false);
            } else if (response.status === 429) {
                cooldown(response);
                showStatus(errorMessage(data, '发送频繁，请稍后再试。'), true, false);
            } else if (response.status === 400 || response.status === 413) {
                showStatus(errorMessage(data, '留言内容不符合要求，请检查后重试。'), true, false);
            } else if (response.status === 503) {
                showStatus(errorMessage(data, '信箱暂无法接收留言，请稍后再试。'), true, false);
            } else {
                showStatus('留言未发送，请重试。', true, false);
            }
        } catch (_) {
            showStatus('未确认发送成功，请稍后重试。', true, false);
        } finally {
            sending = false;
            message.readOnly = false;
            submit.textContent = '发送留言';
            form.removeAttribute('aria-busy');
            updateForm();
        }
    });
    updateForm();
}());
