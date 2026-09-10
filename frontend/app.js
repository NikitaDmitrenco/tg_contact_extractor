document.addEventListener("DOMContentLoaded", () => {
    // UI Elements
    const configAlert = document.getElementById("configAlert");
    const configAlertText = document.getElementById("configAlertText");
    const authBanner = document.getElementById("authBanner");
    const authStep1 = document.getElementById("authStep1");
    const authStep2 = document.getElementById("authStep2");
    const auth2faGroup = document.getElementById("auth2faGroup");
    const authPhoneInput = document.getElementById("authPhone");
    const authCodeInput = document.getElementById("authCode");
    const authPasswordInput = document.getElementById("authPassword");
    const sendCodeBtn = document.getElementById("sendCodeBtn");
    const loginBtn = document.getElementById("loginBtn");
    const authMsg = document.getElementById("authMsg");

    const startBtn = document.getElementById("startBtn");
    const stopBtn = document.getElementById("stopBtn");
    const downloadExcelBtn = document.getElementById("downloadExcelBtn");
    const mainAppCard = document.getElementById("mainAppCard");

    const floodWaitNotice = document.getElementById("floodWaitNotice");
    const floodWaitSecondsSpan = document.getElementById("floodWaitSeconds");
    const criticalErrorNotice = document.getElementById("criticalErrorNotice");
    const criticalErrorText = document.getElementById("criticalErrorText");

    const processedCountSpan = document.getElementById("processedCount");
    const totalCountSpan = document.getElementById("totalCount");
    const progressBar = document.getElementById("progressBar");

    const foundCountSpan = document.getElementById("foundCount");
    const notFoundCountSpan = document.getElementById("notFoundCount");
    const errorCountSpan = document.getElementById("errorCount");

    const tableBody = document.getElementById("tableBody");
    const emptyRow = document.getElementById("emptyRow");

    // State Variables
    let pollInterval = null;
    let nextOffset = 0;
    let allResults = [];
    let isFileLoaded = false;
    let currentPhoneCodeHash = null;

    // 1. Initial Checks
    checkConfiguration();
    checkAuthStatus();

    async function checkConfiguration() {
        try {
            const res = await fetch("/api/config");
            const data = await res.json();
            if (!data.valid) {
                configAlert.classList.remove("hidden");
                configAlertText.innerText = data.message || data.detail || "Ошибка конфигурации .env";
            } else {
                configAlert.classList.add("hidden");
            }
        } catch (e) {
            console.error("Failed to check config:", e);
        }
    }

    async function checkAuthStatus() {
        try {
            const res = await fetch("/api/auth/status");
            const data = await res.json();
            if (!data.authorized) {
                authBanner.classList.remove("hidden");
                if (mainAppCard) mainAppCard.classList.add("hidden");
            } else {
                authBanner.classList.add("hidden");
                if (mainAppCard) mainAppCard.classList.remove("hidden");
            }
        } catch (e) {
            console.error("Failed to check auth status:", e);
        }
    }

    // 2. Telegram Auth Flow
    sendCodeBtn.addEventListener("click", async () => {
        const phone = authPhoneInput.value.trim();
        if (!phone) {
            authMsg.innerText = "Введите номер телефона.";
            authMsg.style.color = "var(--danger-color)";
            return;
        }

        sendCodeBtn.disabled = true;
        authMsg.innerText = "Отправка кода...";
        authMsg.style.color = "var(--text-muted)";

        try {
            const res = await fetch("/api/auth/send-code", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ phone })
            });

            const data = await res.json();
            if (res.ok) {
                currentPhoneCodeHash = data.phone_code_hash || null;
                authStep1.classList.add("hidden");
                authStep2.classList.remove("hidden");
                authMsg.innerText = "Код отправлен в Telegram.";
                authMsg.style.color = "var(--success-color)";
            } else {
                authMsg.innerText = data.detail || "Ошибка отправки кода.";
                authMsg.style.color = "var(--danger-color)";
            }
        } catch (e) {
            authMsg.innerText = "Сетевой сбой: " + e.message;
            authMsg.style.color = "var(--danger-color)";
        } finally {
            sendCodeBtn.disabled = false;
        }
    });

    authPhoneInput.addEventListener("keydown", (e) => {
        if (e.key === "Enter") {
            e.preventDefault();
            sendCodeBtn.click();
        }
    });

    authCodeInput.addEventListener("keydown", (e) => {
        if (e.key === "Enter") {
            e.preventDefault();
            loginBtn.click();
        }
    });

    authPasswordInput.addEventListener("keydown", (e) => {
        if (e.key === "Enter") {
            e.preventDefault();
            loginBtn.click();
        }
    });

    loginBtn.addEventListener("click", async () => {
        const phone = authPhoneInput.value.trim();
        const code = authCodeInput.value.trim();
        const password = authPasswordInput.value.trim();

        if (!code) {
            authMsg.innerText = "Введите код из Telegram.";
            authMsg.style.color = "var(--danger-color)";
            return;
        }

        loginBtn.disabled = true;
        authMsg.innerText = "Авторизация...";

        try {
            const res = await fetch("/api/auth/login", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    phone,
                    code,
                    password: password || null,
                    phone_code_hash: currentPhoneCodeHash
                })
            });

            const data = await res.json();
            if (res.ok) {
                authBanner.classList.add("hidden");
                if (mainAppCard) mainAppCard.classList.remove("hidden");
                showToast("✅ Успешный вход в Telegram!", 5000);
            } else {
                if (data.detail === "2FA_PASSWORD_REQUIRED") {
                    auth2faGroup.classList.remove("hidden");
                    authMsg.innerText = "Для входа требуется пароль двухэтапной аутентификации (2FA).";
                    authMsg.style.color = "var(--warning-color)";
                } else {
                    authMsg.innerText = data.detail || "Ошибка авторизации.";
                    authMsg.style.color = "var(--danger-color)";
                }
            }
        } catch (e) {
            authMsg.innerText = "Ошибка: " + e.message;
            authMsg.style.color = "var(--danger-color)";
        } finally {
            loginBtn.disabled = false;
        }
    });

    // 3. Single-Click Extract & Start Check Handler
    const aiRawText = document.getElementById("aiRawText");
    const aiMsg = document.getElementById("aiMsg");

    startBtn.addEventListener("click", async () => {
        const text = aiRawText.value.trim();

        if (!text) {
            aiMsg.innerText = "Вставьте текст с номерами в поле ввода.";
            aiMsg.style.color = "var(--danger-color)";
            return;
        }

        startBtn.disabled = true;
        aiMsg.innerText = "Извлечение и нормализация номеров...";
        aiMsg.style.color = "var(--text-muted)";

        try {
            // Step 1: Extract & normalize phone numbers
            const extractRes = await fetch("/api/ai-extract", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ text, openai_key: null })
            });

            const extractText = await extractRes.text();
            let extractData;
            try {
                extractData = JSON.parse(extractText);
            } catch {
                aiMsg.innerText = "Ошибка ответа сервера: " + extractText.slice(0, 100);
                aiMsg.style.color = "var(--danger-color)";
                startBtn.disabled = false;
                return;
            }

            if (!extractRes.ok) {
                aiMsg.innerText = extractData.detail || "Ошибка извлечения номеров.";
                aiMsg.style.color = "var(--danger-color)";
                startBtn.disabled = false;
                return;
            }

            // Step 2: Start Telegram check process immediately
            aiMsg.innerText = `Извлечено ${extractData.count} номеров. Запуск проверки...`;
            aiMsg.style.color = "var(--success-color)";

            const startRes = await fetch("/api/start", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ batch_size: 20 })
            });

            const startData = await startRes.json();
            if (startRes.ok) {
                startBtn.classList.add("hidden");
                stopBtn.classList.remove("hidden");
                criticalErrorNotice.classList.add("hidden");
                floodWaitNotice.classList.add("hidden");

                resetTable();
                nextOffset = 0;
                allResults = [];
                
                startPolling();
            } else {
                alert(startData.detail || "Не удалось начать проверку.");
            }
        } catch (e) {
            alert("Ошибка отправки запроса: " + e.message);
        }
    });

    stopBtn.addEventListener("click", async () => {
        try {
            await fetch("/api/stop", { method: "POST" });
            stopPolling();
            stopBtn.classList.add("hidden");
            startBtn.classList.remove("hidden");
            startBtn.disabled = false;
        } catch (e) {
            console.error("Failed to stop:", e);
        }
    });

    // 5. Status Polling & Rendering
    function startPolling() {
        if (pollInterval) clearInterval(pollInterval);
        pollInterval = setInterval(fetchStatus, 500);
    }

    function stopPolling() {
        if (pollInterval) {
            clearInterval(pollInterval);
            pollInterval = null;
        }
    }

    async function fetchStatus() {
        try {
            const res = await fetch(`/api/status?offset=${nextOffset}`);
            if (!res.ok) return;

            const data = await res.json();

            // Update Counters
            processedCountSpan.innerText = data.processed;
            totalCountSpan.innerText = data.total;
            foundCountSpan.innerText = data.found;
            notFoundCountSpan.innerText = data.not_found;
            errorCountSpan.innerText = data.error;

            // Update Progress Bar
            const percent = data.total > 0 ? Math.min(100, Math.round((data.processed / data.total) * 100)) : 0;
            progressBar.style.width = percent + "%";

            // FloodWait Notice
            if (data.flood_wait_seconds > 0) {
                floodWaitNotice.classList.remove("hidden");
                floodWaitSecondsSpan.innerText = data.flood_wait_seconds;
            } else {
                floodWaitNotice.classList.add("hidden");
            }

            // Append New Results to Table
            if (data.results && data.results.length > 0) {
                const tableBody = document.getElementById("tableBody");
                const emptyRow = document.getElementById("emptyRow");
                if (emptyRow) emptyRow.remove();

                data.results.forEach(item => {
                    allResults.push(item);
                    appendRow(item);
                });

                nextOffset = data.next_offset;
            }

            if (allResults.length > 0) {
                downloadExcelBtn.disabled = false;
            }

            // Handle Terminal States
            if (data.status === "critical_error") {
                stopPolling();
                criticalErrorNotice.classList.remove("hidden");
                criticalErrorText.innerText = data.critical_error_msg || "CRITICAL ERROR: Telegram API connection failed.";
                stopBtn.classList.add("hidden");
                startBtn.classList.remove("hidden");
                startBtn.disabled = false;
            } else if (data.status === "completed" || data.status === "stopped") {
                stopPolling();
                stopBtn.classList.add("hidden");
                startBtn.classList.remove("hidden");
                startBtn.disabled = false;
                if (allResults.length > 0) {
                    downloadExcelBtn.disabled = false;
                }
            }

        } catch (e) {
            console.error("Polling error:", e);
        }
    }

    // Toast Helper Function
    let toastTimeout = null;
    function showToast(message, duration = 5000) {
        const toast = document.getElementById("toastNotification");
        const toastText = document.getElementById("toastText");
        if (!toast || !toastText) return;

        toastText.innerText = message;
        toast.classList.remove("hidden");

        if (toastTimeout) clearTimeout(toastTimeout);
        toastTimeout = setTimeout(() => {
            toast.classList.add("hidden");
        }, duration);
    }

    function appendRow(item) {
        const tr = document.createElement("tr");

        const statusClass = `status-${item.status}`;
        const formattedStatus = item.status === "NOT_FOUND" ? "NOT FOUND" : item.status;

        tr.innerHTML = `
            <td><code>${escapeHtml(item.phone)}</code></td>
            <td><span class="status-tag ${statusClass}">${escapeHtml(formattedStatus)}</span></td>
            <td style="text-align: right;">${item.user_id ? item.user_id : '—'}</td>
            <td>${escapeHtml(item.username || '—')}</td>
            <td>${escapeHtml(item.birthday || '—')}</td>
            <td>${escapeHtml(item.first_name || '—')}</td>
            <td>${escapeHtml(item.last_name || '—')}</td>
            <td class="text-muted">${escapeHtml(item.error || '—')}</td>
        `;

        tableBody.appendChild(tr);
    }

    function resetTable() {
        tableBody.innerHTML = `
            <tr id="emptyRow">
                <td colspan="8" class="text-center text-muted">Ожидание результатов...</td>
            </tr>
        `;
    }

    function escapeHtml(str) {
        if (!str) return "";
        return String(str)
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#039;");
    }

    // 6. Excel Export Generator (Streams server-side .xlsx file)
    downloadExcelBtn.addEventListener("click", () => {
        const a = document.createElement("a");
        a.href = "/api/download-excel";
        a.download = `telegram_contacts_${new Date().toISOString().slice(0, 10)}.xlsx`;
        document.body.appendChild(a);
        a.click();
        setTimeout(() => {
            document.body.removeChild(a);
        }, 500);
    });

    function escapeXml(str) {
        if (!str) return "";
        return String(str)
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&apos;");
    }

    function formatCsvField(field) {
        if (field === null || field === undefined) return '""';
        let str = String(field);
        if (str.includes('"') || str.includes(',') || str.includes('\n') || str.includes('\r')) {
            str = '"' + str.replace(/"/g, '""') + '"';
        }
        return str;
    }
});
