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

    const fileInput = document.getElementById("fileInput");
    const fileNameSpan = document.getElementById("fileName");
    const fileCountBadge = document.getElementById("fileCount");
    const startBtn = document.getElementById("startBtn");
    const stopBtn = document.getElementById("stopBtn");
    const downloadBtn = document.getElementById("downloadBtn");
    const downloadExcelBtn = document.getElementById("downloadExcelBtn");

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
            } else {
                authBanner.classList.add("hidden");
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
                authStep1.classList.add("hidden");
                authStep2.classList.remove("hidden");
                authMsg.innerText = "Код отправлен в Telegram.";
                authMsg.style.color = "var(--success-color)";
            } else {
                authMsg.innerText = data.detail || "Ошибка отправки кода.";
                authMsg.style.color = "var(--danger-color)";
            }
        } catch (e) {
            authMsg.innerText = "Стевой сбой: " + e.message;
            authMsg.style.color = "var(--danger-color)";
        } finally {
            sendCodeBtn.disabled = false;
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
                body: JSON.stringify({ phone, code, password: password || null })
            });

            const data = await res.json();
            if (res.ok) {
                authBanner.classList.add("hidden");
                alert("Авторизация прошла успешно!");
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

    // 3. File Upload Handler
    fileInput.addEventListener("change", async (e) => {
        const file = e.target.files[0];
        if (!file) return;

        fileNameSpan.innerText = file.name;
        
        const formData = new FormData();
        formData.append("file", file);

        try {
            const res = await fetch("/api/upload", {
                method: "POST",
                body: formData
            });

            const text = await res.text();
            let data;
            try {
                data = JSON.parse(text);
            } catch (jsonErr) {
                alert("Ошибка ответа сервера: " + text.slice(0, 150));
                startBtn.disabled = true;
                return;
            }

            if (res.ok) {
                isFileLoaded = true;
                fileCountBadge.innerText = `${data.count} номеров`;
                fileCountBadge.classList.remove("hidden");
                totalCountSpan.innerText = data.count;
                startBtn.disabled = false;
                resetTable();
            } else {
                alert(data.detail || "Ошибка загрузки файла.");
                startBtn.disabled = true;
            }
        } catch (err) {
            alert("Ошибка сети при загрузке файла: " + err.message);
            startBtn.disabled = true;
        }
    });

    // 3.5 Tab Switching & AI Extraction
    const tabFileBtn = document.getElementById("tabFileBtn");
    const tabAiBtn = document.getElementById("tabAiBtn");
    const fileMethodBox = document.getElementById("fileMethodBox");
    const aiMethodBox = document.getElementById("aiMethodBox");
    const aiRawText = document.getElementById("aiRawText");
    const openaiKeyInput = document.getElementById("openaiKeyInput");
    const aiExtractBtn = document.getElementById("aiExtractBtn");
    const aiMsg = document.getElementById("aiMsg");

    tabFileBtn.addEventListener("click", () => {
        tabFileBtn.classList.add("btn-secondary");
        tabFileBtn.classList.remove("btn-outline");
        tabAiBtn.classList.add("btn-outline");
        tabAiBtn.classList.remove("btn-secondary");
        fileMethodBox.classList.remove("hidden");
        aiMethodBox.classList.add("hidden");
    });

    tabAiBtn.addEventListener("click", () => {
        tabAiBtn.classList.add("btn-secondary");
        tabAiBtn.classList.remove("btn-outline");
        tabFileBtn.classList.add("btn-outline");
        tabFileBtn.classList.remove("btn-secondary");
        aiMethodBox.classList.remove("hidden");
        fileMethodBox.classList.add("hidden");
    });

    aiExtractBtn.addEventListener("click", async () => {
        const text = aiRawText.value.trim();
        const openaiKey = openaiKeyInput.value.trim();

        if (!text) {
            aiMsg.innerText = "Вставьте текст для обработки.";
            aiMsg.style.color = "var(--danger-color)";
            return;
        }

        aiExtractBtn.disabled = true;
        aiMsg.innerText = "Извлечение и нормализация номеров...";
        aiMsg.style.color = "var(--text-muted)";

        try {
            const res = await fetch("/api/ai-extract", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ text, openai_key: openaiKey || null })
            });

            const resText = await res.text();
            let data;
            try {
                data = JSON.parse(resText);
            } catch {
                aiMsg.innerText = "Ошибка ответа: " + resText.slice(0, 100);
                aiMsg.style.color = "var(--danger-color)";
                return;
            }

            if (res.ok) {
                isFileLoaded = true;
                totalCountSpan.innerText = data.count;
                startBtn.disabled = false;
                resetTable();
                aiMsg.innerText = `Успешно извлечено и нормализовано ${data.count} номеров!`;
                aiMsg.style.color = "var(--success-color)";
            } else {
                aiMsg.innerText = data.detail || "Ошибка извлечения номеров.";
                aiMsg.style.color = "var(--danger-color)";
            }
        } catch (e) {
            aiMsg.innerText = "Ошибка сети: " + e.message;
            aiMsg.style.color = "var(--danger-color)";
        } finally {
            aiExtractBtn.disabled = false;
        }
    });

    // 4. Start / Stop Checker
    startBtn.addEventListener("click", async () => {
        if (!isFileLoaded) return;

        try {
            const res = await fetch("/api/start", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ batch_size: 20 })
            });

            const data = await res.json();
            if (res.ok) {
                startBtn.disabled = true;
                startBtn.classList.add("hidden");
                stopBtn.classList.remove("hidden");
                criticalErrorNotice.classList.add("hidden");
                floodWaitNotice.classList.add("hidden");

                resetTable();
                nextOffset = 0;
                allResults = [];
                
                startPolling();
            } else {
                alert(data.detail || "Не удалось начать проверку.");
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
                if (emptyRow) emptyRow.remove();

                data.results.forEach(item => {
                    allResults.push(item);
                    appendRow(item);
                });

                nextOffset = data.next_offset;
                downloadBtn.disabled = false;
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
            }

        } catch (e) {
            console.error("Polling error:", e);
        }
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
            <td>${escapeHtml(item.first_name || '—')}</td>
            <td>${escapeHtml(item.last_name || '—')}</td>
            <td class="text-muted">${escapeHtml(item.error || '—')}</td>
        `;

        tableBody.appendChild(tr);
    }

    function resetTable() {
        tableBody.innerHTML = `
            <tr id="emptyRow">
                <td colspan="7" class="text-center text-muted">Ожидание результатов...</td>
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

    // 6. CSV Export Generator (UTF-8 BOM)
    downloadBtn.addEventListener("click", () => {
        if (allResults.length === 0) return;

        const headers = ["phone", "status", "user_id", "username", "first_name", "last_name", "error"];
        const rows = [headers.join(",")];

        allResults.forEach(item => {
            const row = [
                formatCsvField(item.phone),
                formatCsvField(item.status),
                formatCsvField(item.user_id ? String(item.user_id) : ""),
                formatCsvField(item.username || ""),
                formatCsvField(item.first_name || ""),
                formatCsvField(item.last_name || ""),
                formatCsvField(item.error || "")
            ];
            rows.push(row.join(","));
        });

        // Add UTF-8 BOM (\uFEFF) for native Excel UTF-8 support
        const csvContent = "\uFEFF" + rows.join("\r\n");
        const blob = new Blob([csvContent], { type: "text/csv;charset=utf-8;" });
        const url = URL.createObjectURL(blob);

        const a = document.createElement("a");
        a.href = url;
        a.download = `telegram_check_results_${new Date().toISOString().slice(0, 10)}.csv`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
    });

    // 7. Excel Export Generator (Order: Имя, Фамилия, Номер телефона, Username)
    downloadExcelBtn.addEventListener("click", () => {
        if (allResults.length === 0) return;

        let xmlContent = `<?xml version="1.0" encoding="UTF-8"?>
<?mso-application progid="Excel.Sheet"?>
<Workbook xmlns="urn:schemas-microsoft-com:office:spreadsheet"
 xmlns:o="urn:schemas-microsoft-com:office:office"
 xmlns:x="urn:schemas-microsoft-com:office:excel"
 xmlns:ss="urn:schemas-microsoft-com:office:spreadsheet">
 <Worksheet ss:Name="Контакты Telegram">
  <Table>
   <Row>
    <Cell><Data ss:Type="String">Имя</Data></Cell>
    <Cell><Data ss:Type="String">Фамилия</Data></Cell>
    <Cell><Data ss:Type="String">Номер телефона</Data></Cell>
    <Cell><Data ss:Type="String">Username</Data></Cell>
   </Row>`;

        allResults.forEach(item => {
            const firstName = escapeXml(item.first_name || "—");
            const lastName = escapeXml(item.last_name || "—");
            const phone = escapeXml(item.phone || "—");
            const username = escapeXml(item.username ? (item.username.startsWith("@") ? item.username : "@" + item.username) : "—");

            xmlContent += `
   <Row>
    <Cell><Data ss:Type="String">${firstName}</Data></Cell>
    <Cell><Data ss:Type="String">${lastName}</Data></Cell>
    <Cell><Data ss:Type="String">${phone}</Data></Cell>
    <Cell><Data ss:Type="String">${username}</Data></Cell>
   </Row>`;
        });

        xmlContent += `
  </Table>
 </Worksheet>
</Workbook>`;

        const blob = new Blob([xmlContent], { type: "application/vnd.ms-excel;charset=utf-8" });
        const url = URL.createObjectURL(blob);

        const a = document.createElement("a");
        a.href = url;
        a.download = `telegram_contacts_${new Date().toISOString().slice(0, 10)}.xls`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
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
