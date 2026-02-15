/* ================================================================
   LLM Gateway Admin Panel — SPA Logic
   Hash-based routing, API calls, table rendering, modal forms
   ================================================================ */

(() => {
    "use strict";

    const API = "/admin/api";

    // ── Utility ─────────────────────────────────────────────────

    async function api(path, opts = {}) {
        const res = await fetch(`${API}${path}`, {
            headers: { "Content-Type": "application/json", ...opts.headers },
            ...opts,
        });

        if (res.status === 401) {
            window.location.href = "/admin/login";
            return null;
        }

        // Handle 409 (Conflict) - e.g. Duplicate User
        if (res.status === 409) {
            const err = await res.json().catch(() => ({}));
            throw new Error(err.detail || "データが重複しています");
        }

        // Handle 422 (Validation Error)
        if (res.status === 422) {
            const err = await res.json().catch(() => ({}));
            if (err.detail && Array.isArray(err.detail)) {
                // Construct a readable error message from Pydantic details
                const messages = err.detail.map(e => {
                    const loc = e.loc[e.loc.length - 1];
                    const msg = e.msg;
                    return `・${loc}: ${msg}`;
                });
                throw new Error("入力エラー:\n" + messages.join("\n"));
            }
            throw new Error(err.detail || "入力内容に誤りがあります");
        }

        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            throw new Error(err.detail || `HTTP ${res.status}`);
        }
        return res.json();
    }

    function toast(message, type = "success") {
        const c = document.getElementById("toast-container");
        const el = document.createElement("div");
        el.className = `toast toast-${type}`;
        el.textContent = message;
        c.appendChild(el);
        setTimeout(() => el.remove(), 3500);
    }

    function $(id) { return document.getElementById(id); }

    function esc(str) {
        if (str == null) return "";
        const d = document.createElement("div");
        d.textContent = String(str);
        return d.innerHTML;
    }

    function fmtDate(iso) {
        if (!iso) return "—";
        const d = new Date(iso);
        return d.toLocaleString("ja-JP", { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
    }

    function fmtCost(v) {
        if (v == null) return "—";
        return `¥${Number(v).toLocaleString("ja-JP", { minimumFractionDigits: 2, maximumFractionDigits: 4 })}`;
    }

    function badge(text, type = "muted") {
        return `<span class="badge badge-${type}">${esc(text)}</span>`;
    }

    function statusBadge(s) {
        const m = { active: "success", trial: "info", expired: "warning", banned: "danger" };
        return badge(s, m[s] || "muted");
    }

    function healthBadge(s) {
        return `<span class="health-dot ${s}"></span>${esc(s)}`;
    }

    function reqStatusBadge(s) {
        const m = { completed: "success", pending: "warning", failed: "danger", cancelled: "muted" };
        return badge(s, m[s] || "muted");
    }

    // ── Modal ───────────────────────────────────────────────────

    function openModal(title, bodyHtml, footerHtml = "") {
        $("modal-title").textContent = title;
        $("modal-body").innerHTML = bodyHtml;
        $("modal-footer").innerHTML = footerHtml;
        $("modal-overlay").classList.add("active");
    }

    function closeModal() {
        $("modal-overlay").classList.remove("active");
    }

    $("modal-close").addEventListener("click", closeModal);
    $("modal-overlay").addEventListener("click", (e) => {
        if (e.target === $("modal-overlay")) closeModal();
    });

    // ── Router ──────────────────────────────────────────────────

    const pages = {
        dashboard: { title: "ダッシュボード", render: renderDashboard },
        users: { title: "ユーザー管理", render: renderUsers },
        apps: { title: "アプリ管理", render: renderApps },
        "api-keys": { title: "APIキー管理", render: renderApiKeys },

        billing: { title: "月次請求", render: renderBilling },
        models: { title: "モデル管理", render: renderModels },
        endpoints: { title: "エンドポイント管理", render: renderEndpoints },
        "usage-logs": { title: "利用ログ", render: renderUsageLogs },
        "audit-logs": { title: "監査ログ", render: renderAuditLogs },
    };

    function navigate() {
        const hash = location.hash.replace("#", "") || "dashboard";
        const page = pages[hash] || pages.dashboard;

        $("page-title").textContent = page.title;

        document.querySelectorAll(".nav-item").forEach((el) => {
            el.classList.toggle("active", el.dataset.page === hash);
        });

        $("page-content").innerHTML = `<div class="loading"><div class="spinner"></div><br>読み込み中...</div>`;
        page.render();
    }

    window.addEventListener("hashchange", navigate);

    // ── Logout ──────────────────────────────────────────────────

    $("logout-btn").addEventListener("click", async () => {
        await api("/logout", { method: "POST" }).catch(() => { });
        window.location.href = "/admin/login";
    });

    // ── Dashboard ───────────────────────────────────────────────

    async function renderDashboard() {
        try {
            const d = await api("/dashboard");
            if (!d) return;

            let html = `
                <div class="kpi-grid">
                    <div class="kpi-card"><div class="kpi-label">総ユーザー数</div><div class="kpi-value">${d.users_count}</div></div>
                    <div class="kpi-card"><div class="kpi-label">アクティブAPIキー</div><div class="kpi-value">${d.active_api_keys}</div></div>
                    <div class="kpi-card"><div class="kpi-label">本日のリクエスト</div><div class="kpi-value">${d.today_requests}</div></div>
                    <div class="kpi-card"><div class="kpi-label">本日のコスト</div><div class="kpi-value">${fmtCost(d.today_cost)}</div></div>
                </div>`;

            // Endpoints health
            if (d.endpoints.length > 0) {
                html += `<div class="section-card"><h3>エンドポイント稼働状況</h3><div class="table-wrapper"><table>
                    <thead><tr><th>モデル</th><th>URL</th><th>ヘルス</th><th>レイテンシ</th><th>リクエスト数</th></tr></thead><tbody>`;
                for (const ep of d.endpoints) {
                    html += `<tr>
                        <td>${esc(ep.model_id)}</td>
                        <td class="text-mono truncate">${esc(ep.base_url)}</td>
                        <td>${healthBadge(ep.health_status)}</td>
                        <td>${ep.avg_latency_ms}ms</td>
                        <td>${Number(ep.total_requests).toLocaleString()}</td>
                    </tr>`;
                }
                html += `</tbody></table></div></div>`;
            }

            // Recent logs
            if (d.recent_logs.length > 0) {
                html += `<div class="section-card"><h3>直近リクエスト</h3><div class="table-wrapper"><table>
                    <thead><tr><th>日時</th><th>ユーザー</th><th>モデル</th><th>トークン</th><th>コスト</th><th>レイテンシ</th><th>ステータス</th></tr></thead><tbody>`;
                for (const l of d.recent_logs) {
                    html += `<tr>
                        <td>${fmtDate(l.created_at)}</td>
                        <td class="text-mono">${esc(l.user_oid)}</td>
                        <td>${esc(l.actual_model)}</td>
                        <td>${l.input_tokens} / ${l.output_tokens}</td>
                        <td>${fmtCost(l.cost)}</td>
                        <td>${l.latency_ms != null ? l.latency_ms + "ms" : "—"}</td>
                        <td>${reqStatusBadge(l.status)}</td>
                    </tr>`;
                }
                html += `</tbody></table></div></div>`;
            }

            $("page-content").innerHTML = html;
        } catch (e) {
            $("page-content").innerHTML = `<div class="empty-state"><p>データの取得に失敗しました: ${esc(e.message)}</p></div>`;
        }
    }

    // ── Billing ─────────────────────────────────────────────────

    let billingMonth = new Date().toISOString().slice(0, 7); // YYYY-MM

    async function renderBilling() {
        try {
            const res = await api(`/billing?month=${billingMonth}`);
            if (!res) return;

            let html = `
                <div class="filter-bar">
                    <div class="form-group">
                        <label>対象月</label>
                        <input class="form-control" id="bl-month" type="month" value="${esc(billingMonth)}">
                    </div>
                    <div class="form-group">
                        <label>&nbsp;</label>
                        <button class="btn btn-primary btn-sm" id="bl-go">表示</button>
                    </div>
                </div>`;

            // Summary KPIs
            html += `
                <div class="kpi-grid">
                    <div class="kpi-card"><div class="kpi-label">対象月</div><div class="kpi-value">${esc(res.month)}</div></div>
                    <div class="kpi-card"><div class="kpi-label">アクティブユーザー</div><div class="kpi-value">${res.users.length}</div></div>
                    <div class="kpi-card"><div class="kpi-label">合計リクエスト</div><div class="kpi-value">${Number(res.total_requests).toLocaleString()}</div></div>
                    <div class="kpi-card"><div class="kpi-label">合計コスト</div><div class="kpi-value">${fmtCost(res.total_cost)}</div></div>
                </div>`;

            if (res.users.length === 0) {
                html += `<div class="empty-state"><div class="empty-icon">💰</div><p>この月の利用データはありません</p></div>`;
            } else {
                html += `<div class="section-card"><h3>ユーザー別利用明細</h3><div class="table-wrapper"><table>
                    <thead><tr>
                        <th>ユーザーOID</th><th>メール</th><th>表示名</th>
                        <th>リクエスト数</th><th>入力トークン</th><th>出力トークン</th><th>コスト</th>
                    </tr></thead><tbody>`;
                for (const u of res.users) {
                    html += `<tr>
                        <td class="text-mono">${esc(u.user_oid)}</td>
                        <td>${esc(u.email) || "—"}</td>
                        <td>${esc(u.display_name) || "—"}</td>
                        <td>${Number(u.requests).toLocaleString()}</td>
                        <td>${Number(u.input_tokens).toLocaleString()}</td>
                        <td>${Number(u.output_tokens).toLocaleString()}</td>
                        <td>${fmtCost(u.total_cost)}</td>
                    </tr>`;
                }
                // Totals row
                html += `</tbody><tfoot><tr style="font-weight:700;border-top:2px solid var(--border)">
                    <td colspan="3">合計</td>
                    <td>${Number(res.total_requests).toLocaleString()}</td>
                    <td>${Number(res.users.reduce((s, u) => s + u.input_tokens, 0)).toLocaleString()}</td>
                    <td>${Number(res.users.reduce((s, u) => s + u.output_tokens, 0)).toLocaleString()}</td>
                    <td>${fmtCost(res.total_cost)}</td>
                </tr></tfoot></table></div></div>`;
            }

            $("page-content").innerHTML = html;
            $("bl-go")?.addEventListener("click", () => {
                billingMonth = $("bl-month").value;
                renderBilling();
            });
        } catch (e) {
            $("page-content").innerHTML = `<div class="empty-state"><p>${esc(e.message)}</p></div>`;
        }
    }

    // ── Users ───────────────────────────────────────────────────

    async function renderUsers() {
        try {
            const rows = await api("/users");
            if (!rows) return;

            let html = `<div class="toolbar">
                <button class="btn btn-primary" id="btn-add-user">＋ ユーザー追加</button>
                <button class="btn btn-warning" id="btn-sync-expiry" style="margin-left:8px">🔄 期限切れ一括チェック</button>
            </div>`;

            if (rows.length === 0) {
                html += `<div class="empty-state"><div class="empty-icon">👤</div><p>ユーザーが登録されていません</p></div>`;
            } else {
                html += `<div class="section-card"><div class="table-wrapper"><table>
                    <thead><tr><th>OID</th><th>メール</th><th>表示名</th><th>支払い</th><th>有効期限</th><th>累計コスト</th><th>操作</th></tr></thead><tbody>`;
                for (const u of rows) {
                    html += `<tr>
                        <td class="text-mono">${esc(u.oid)}</td>
                        <td>${esc(u.email)}</td>
                        <td>${esc(u.display_name) || "—"}</td>
                        <td>${statusBadge(u.payment_status)}</td>
                        <td>${esc(u.payment_valid_until)}</td>
                        <td>${fmtCost(u.total_cost_cache)}</td>
                        <td>
                            <button class="btn btn-sm btn-edit-user" data-oid="${esc(u.oid)}" data-name="${esc(u.display_name || "")}" data-webhook="${esc(u.webhook_url || "")}" data-until="${esc(u.payment_valid_until)}">編集</button>
                            <button class="btn btn-sm btn-status-user" data-oid="${esc(u.oid)}" data-status="${esc(u.payment_status)}">ステータス</button>
                            <button class="btn btn-sm btn-danger btn-delete-user" data-oid="${esc(u.oid)}" data-email="${esc(u.email)}">削除</button>
                        </td>
                    </tr>`;
                }
                html += `</tbody></table></div></div>`;
            }

            $("page-content").innerHTML = html;

            $("btn-add-user").addEventListener("click", () => {
                openModal("ユーザー追加", `
                    <div class="form-group"><label>OID</label><input class="form-control" id="f-oid" required></div>
                    <div class="form-group"><label>メール</label><input class="form-control" id="f-email" type="email" required></div>
                    <div class="form-group"><label>表示名</label><input class="form-control" id="f-name"></div>
                    <div class="form-row">
                        <div class="form-group"><label>支払い有効期限</label><input class="form-control" id="f-until" type="date" required></div>
                        <div class="form-group"><label>ステータス</label><select class="form-control" id="f-pstatus"><option value="active">active</option><option value="trial">trial</option></select></div>
                    </div>`,
                    `<button class="btn" onclick="closeModal()">キャンセル</button><button class="btn btn-primary" id="f-submit">作成</button>`
                );
                $("f-submit").addEventListener("click", async () => {
                    try {
                        await api("/users", {
                            method: "POST", body: JSON.stringify({
                                oid: $("f-oid").value, email: $("f-email").value,
                                display_name: $("f-name").value || null,
                                payment_valid_until: $("f-until").value,
                                payment_status: $("f-pstatus").value,
                            })
                        });
                        closeModal(); toast("ユーザーを作成しました"); renderUsers();
                    } catch (e) { toast(e.message, "error"); }
                });
            });

            document.querySelectorAll(".btn-edit-user").forEach((btn) => {
                btn.addEventListener("click", () => {
                    const oid = btn.dataset.oid;
                    openModal("ユーザー編集", `
                        <div class="form-group"><label>表示名</label><input class="form-control" id="f-name" value="${esc(btn.dataset.name)}"></div>
                        <div class="form-group"><label>Webhook URL</label><input class="form-control" id="f-webhook" value="${esc(btn.dataset.webhook)}"></div>
                        <div class="form-group"><label>支払い有効期限</label><input class="form-control" id="f-until" type="date" value="${esc(btn.dataset.until)}"></div>`,
                        `<button class="btn" onclick="closeModal()">キャンセル</button><button class="btn btn-primary" id="f-submit">更新</button>`
                    );
                    $("f-submit").addEventListener("click", async () => {
                        try {
                            await api(`/users/${oid}`, {
                                method: "PUT", body: JSON.stringify({
                                    display_name: $("f-name").value, webhook_url: $("f-webhook").value,
                                    payment_valid_until: $("f-until").value || null,
                                })
                            });
                            closeModal(); toast("ユーザーを更新しました"); renderUsers();
                        } catch (e) { toast(e.message, "error"); }
                    });
                });
            });

            document.querySelectorAll(".btn-status-user").forEach((btn) => {
                btn.addEventListener("click", () => {
                    const oid = btn.dataset.oid;
                    openModal("支払いステータス変更", `
                        <div class="form-group"><label>新しいステータス</label>
                            <select class="form-control" id="f-status">
                                <option value="active" ${btn.dataset.status === "active" ? "selected" : ""}>active</option>
                                <option value="trial" ${btn.dataset.status === "trial" ? "selected" : ""}>trial</option>
                                <option value="expired" ${btn.dataset.status === "expired" ? "selected" : ""}>expired</option>
                                <option value="banned" ${btn.dataset.status === "banned" ? "selected" : ""}>banned</option>
                            </select>
                        </div>`,
                        `<button class="btn" onclick="closeModal()">キャンセル</button><button class="btn btn-primary" id="f-submit">変更</button>`
                    );
                    $("f-submit").addEventListener("click", async () => {
                        try {
                            await api(`/users/${oid}/status`, { method: "PATCH", body: JSON.stringify({ payment_status: $("f-status").value }) });
                            closeModal(); toast("ステータスを変更しました"); renderUsers();
                        } catch (e) { toast(e.message, "error"); }
                    });
                });
            });

            // Delete user
            document.querySelectorAll(".btn-delete-user").forEach((btn) => {
                btn.addEventListener("click", async () => {
                    const oid = btn.dataset.oid;
                    const email = btn.dataset.email;

                    // Step 1: Fetch related data counts
                    let check;
                    try {
                        check = await api(`/users/${oid}/delete-check`);
                    } catch (e) {
                        toast(e.message, "error"); return;
                    }

                    const r = check.related;
                    const lines = [];
                    if (r.api_keys > 0)   lines.push(`  ・APIキー: ${r.api_keys}件`);
                    if (r.apps > 0)        lines.push(`  ・アプリ: ${r.apps}件`);
                    if (r.usage_logs > 0)  lines.push(`  ・利用ログ: ${r.usage_logs}件`);
                    if (r.audit_logs > 0)  lines.push(`  ・監査ログ: ${r.audit_logs}件`);

                    const needsForce = check.has_blockers;
                    const relatedText = lines.length > 0
                        ? `\n\n【削除される関連データ】\n${lines.join("\n")}`
                        : "";

                    // Step 2: Confirmation dialog
                    if (needsForce) {
                        if (!confirm(
                            `ユーザー「${email || oid}」を強制削除しますか？\n` +
                            `以下の関連データもすべて完全に削除されます。この操作は取り消せません。` +
                            relatedText
                        )) return;
                        // Step 3: Force delete
                        try {
                            await api(`/users/${oid}?force=true`, { method: "DELETE" });
                            toast("ユーザーと関連データを削除しました"); renderUsers();
                        } catch (e) { toast(e.message, "error"); }
                    } else {
                        if (!confirm(
                            `ユーザー「${email || oid}」を削除しますか？` +
                            (r.api_keys > 0 ? `\n\n※ APIキー ${r.api_keys}件も削除されます。` : "") +
                            `\nこの操作は取り消せません。`
                        )) return;
                        // Step 3: Normal delete
                        try {
                            await api(`/users/${oid}`, { method: "DELETE" });
                            toast("ユーザーを削除しました"); renderUsers();
                        } catch (e) { toast(e.message, "error"); }
                    }
                });
            });

            // Bulk sync expiry
            $("btn-sync-expiry")?.addEventListener("click", async () => {
                const btn = $("btn-sync-expiry");
                const origText = btn.textContent;
                btn.textContent = "チェック中...";
                btn.disabled = true;
                try {
                    const res = await api("/users/sync/bulk-expiry", { method: "POST" });
                    toast(`期限切れチェック完了: ${res.expired}件を更新 (全${res.checked}件チェック)`);
                    renderUsers();
                } catch (e) {
                    toast(e.message, "error");
                    btn.textContent = origText;
                    btn.disabled = false;
                }
            });
        } catch (e) {
            $("page-content").innerHTML = `<div class="empty-state"><p>${esc(e.message)}</p></div>`;
        }
    }

    // ── Apps ────────────────────────────────────────────────────

    async function renderApps() {
        try {
            const rows = await api("/apps");
            if (!rows) return;

            let html = `<div class="toolbar"><button class="btn btn-primary" id="btn-add-app">＋ アプリ登録</button></div>`;

            if (rows.length === 0) {
                html += `<div class="empty-state"><div class="empty-icon">📱</div><p>アプリが登録されていません</p></div>`;
            } else {
                html += `<div class="section-card"><div class="table-wrapper"><table>
                    <thead><tr><th>アプリID</th><th>名前</th><th>所有者</th><th>説明</th><th>ステータス</th><th>作成日</th><th>操作</th></tr></thead><tbody>`;
                for (const app of rows) {
                    html += `<tr${!app.is_active ? ' style="opacity:0.5"' : ""}>
                        <td class="text-mono">${esc(app.app_id)}</td>
                        <td>${esc(app.name)}</td>
                        <td class="text-mono">${esc(app.owner_id)}</td>
                        <td>${esc(app.description) || "—"}</td>
                        <td>${app.is_active ? badge("有効", "success") : badge("無効", "danger")}</td>
                        <td>${fmtDate(app.created_at)}</td>
                        <td>
                            <button class="btn btn-sm btn-toggle-app" data-id="${esc(app.app_id)}">${app.is_active ? "無効化" : "有効化"}</button>
                            <button class="btn btn-sm btn-danger btn-delete-app" data-id="${esc(app.app_id)}">削除</button>
                        </td>
                    </tr>`;
                }
                html += `</tbody></table></div></div>`;
            }

            $("page-content").innerHTML = html;

            $("btn-add-app").addEventListener("click", async () => {
                // Fetch users for dropdown
                let userOptions = '<option value="">選択してください</option>';
                try {
                    const users = await api("/users");
                    if (users && users.length > 0) {
                        userOptions += users.map(u => `<option value="${esc(u.oid)}">${esc(u.display_name || u.email)} (${esc(u.oid)})</option>`).join("");
                    }
                } catch (e) {
                    console.error("Failed to load users for dropdown", e);
                }

                openModal("アプリ登録", `
                    <div class="form-group"><label>アプリID (一意)</label><input class="form-control" id="f-aid" required placeholder="example-chat-v1"></div>
                    <div class="form-group"><label>名前</label><input class="form-control" id="f-name" required></div>
                    <div class="form-group"><label>説明</label><input class="form-control" id="f-desc"></div>
                    <div class="form-group"><label>所有者 (User OID)</label>
                        <select class="form-control" id="f-owner" required>
                            ${userOptions}
                        </select>
                    </div>`,
                    `<button class="btn" onclick="closeModal()">キャンセル</button><button class="btn btn-primary" id="f-submit">登録</button>`
                );
                $("f-submit").addEventListener("click", async () => {
                    try {
                        const owner = $("f-owner").value;
                        if (!owner) throw new Error("所有者を指定してください");
                        await api("/apps?owner_id=" + encodeURIComponent(owner), {
                            method: "POST", body: JSON.stringify({
                                app_id: $("f-aid").value,
                                name: $("f-name").value,
                                description: $("f-desc").value || null,
                            })
                        });
                        closeModal(); toast("アプリを登録しました"); renderApps();
                    } catch (e) { toast(e.message, "error"); }
                });
            });

            document.querySelectorAll(".btn-toggle-app").forEach((btn) => {
                btn.addEventListener("click", async () => {
                    try {
                        await api(`/apps/${btn.dataset.id}/toggle`, { method: "PATCH" });
                        toast("ステータスを変更しました"); renderApps();
                    } catch (e) { toast(e.message, "error"); }
                });
            });

            document.querySelectorAll(".btn-delete-app").forEach((btn) => {
                btn.addEventListener("click", async () => {
                    if (!confirm("本当にこのアプリを削除しますか？")) return;
                    try {
                        await api(`/apps/${btn.dataset.id}`, { method: "DELETE" });
                        toast("アプリを削除しました"); renderApps();
                    } catch (e) { toast(e.message, "error"); }
                });
            });

        } catch (e) {
            $("page-content").innerHTML = `<div class="empty-state"><p>${esc(e.message)}</p></div>`;
        }
    }

    // ── API Keys ────────────────────────────────────────────────

    async function renderApiKeys() {
        try {
            const rows = await api("/api-keys");
            if (!rows) return;

            let html = `<div class="toolbar"><button class="btn btn-primary" id="btn-add-key">＋ APIキー発行</button></div>`;

            if (rows.length === 0) {
                html += `<div class="empty-state"><div class="empty-icon">🔑</div><p>APIキーが発行されていません</p></div>`;
            } else {
                html += `<div class="section-card"><div class="table-wrapper"><table>
                    <thead><tr><th>プレフィックス</th><th>ユーザー</th><th>ラベル</th><th>レート制限</th><th>予算</th><th>ステータス</th><th>最終使用</th><th>操作</th></tr></thead><tbody>`;
                for (const k of rows) {
                    const active = k.is_active;
                    const st = active ? badge("有効", "success") : badge("無効", "danger");
                    html += `<tr${!active ? ' style="opacity:0.5"' : ""}>
                        <td class="text-mono">${esc(k.display_prefix)}</td>
                        <td>${esc(k.user_email || k.user_oid)}</td>
                        <td>${esc(k.label) || "—"}</td>
                        <td>${k.rate_limit_rpm} RPM</td>
                        <td>${k.budget_monthly != null ? `${fmtCost(k.usage_current_month)} / ${fmtCost(k.budget_monthly)}` : "制限なし"}</td>
                        <td>${st}</td>
                        <td>${fmtDate(k.last_used_at)}</td>
                        <td>
                            ${active ? `<button class="btn btn-sm btn-danger btn-deactivate-key" data-id="${esc(k.id)}">無効化</button>` : ""}
                            <button class="btn btn-sm btn-danger btn-delete-key" data-id="${esc(k.id)}">削除</button>
                        </td>
                    </tr>`;
                }
                html += `</tbody></table></div></div>`;
            }

            $("page-content").innerHTML = html;

            $("btn-add-key").addEventListener("click", async () => {
                // Fetch users for dropdown
                let userOptions = '<option value="">選択してください</option>';
                try {
                    const users = await api("/users");
                    if (users && users.length > 0) {
                        userOptions += users.map(u => `<option value="${esc(u.oid)}">${esc(u.display_name || u.email)} (${esc(u.oid)})</option>`).join("");
                    }
                } catch (e) {
                    console.error("Failed to load users for dropdown", e);
                }

                openModal("APIキー発行", `
                    <div class="form-group"><label>ユーザーOID</label>
                        <select class="form-control" id="f-uid" required>
                            ${userOptions}
                        </select>
                    </div>
                    <div class="form-group"><label>ラベル</label><input class="form-control" id="f-label"></div>
                    <div class="form-row">
                        <div class="form-group"><label>レート制限 (RPM)</label><input class="form-control" id="f-rpm" type="number" value="60"></div>
                        <div class="form-group"><label>月額予算 (¥)</label><input class="form-control" id="f-budget" type="number" placeholder="空=制限なし"></div>
                    </div>
                    <div class="form-group"><label>許可モデル</label><input class="form-control" id="f-models" placeholder="カンマ区切り (空=全モデル)"></div>
                    <div class="form-group"><label>許可IP</label><input class="form-control" id="f-ips" placeholder="カンマ区切り (空=制限なし)"></div>`,
                    `<button class="btn" onclick="closeModal()">キャンセル</button><button class="btn btn-primary" id="f-submit">発行</button>`
                );
                $("f-submit").addEventListener("click", async () => {
                    try {
                        const models = $("f-models").value.trim();
                        const ips = $("f-ips").value.trim();
                        const budget = $("f-budget").value.trim();
                        const data = await api("/api-keys", {
                            method: "POST", body: JSON.stringify({
                                user_oid: $("f-uid").value, label: $("f-label").value || null,
                                rate_limit_rpm: parseInt($("f-rpm").value) || 60,
                                budget_monthly: budget ? parseFloat(budget) : null,
                                allowed_models: models ? models.split(",").map(s => s.trim()) : null,
                                allowed_ips: ips ? ips.split(",").map(s => s.trim()) : null,
                            })
                        });
                        closeModal();
                        openModal("APIキーが発行されました", `
                            <p style="margin-bottom:12px">以下のキーを安全に保管してください。再表示はできません。</p>
                            <div style="background:var(--bg-input);padding:14px;border-radius:8px;word-break:break-all;font-family:monospace;font-size:0.85rem">${esc(data.key)}</div>`,
                            `<button class="btn btn-primary" onclick="closeModal()">閉じる</button>`
                        );
                        renderApiKeys();
                    } catch (e) { toast(e.message, "error"); }
                });
            });

            document.querySelectorAll(".btn-deactivate-key").forEach((btn) => {
                btn.addEventListener("click", async () => {
                    if (!confirm("このAPIキーを無効化しますか？")) return;
                    try {
                        await api(`/api-keys/${btn.dataset.id}/deactivate`, { method: "PATCH" });
                        toast("APIキーを無効化しました"); renderApiKeys();
                    } catch (e) { toast(e.message, "error"); }
                });
            });

            document.querySelectorAll(".btn-delete-key").forEach((btn) => {
                btn.addEventListener("click", async () => {
                    const id = (btn.dataset.id || "").trim();
                    console.log("[Admin] Deleting API Key:", id);
                    if (!id) { alert("IDが見つかりません"); return; }

                    if (!confirm("このAPIキーを完全に削除しますか？この操作は取り消せません。")) return;
                    try {
                        await api(`/api-keys/${id}`, { method: "DELETE" });
                        toast("APIキーを削除しました"); renderApiKeys();
                    } catch (e) {
                        console.error("[Admin] Delete failed:", e);
                        toast(e.message, "error");
                    }
                });
            });
        } catch (e) {
            $("page-content").innerHTML = `<div class="empty-state"><p>${esc(e.message)}</p></div>`;
        }
    }

    // ── Models ──────────────────────────────────────────────────

    async function renderModels() {
        try {
            const rows = await api("/models");
            if (!rows) return;

            let html = `<div class="toolbar"><button class="btn btn-primary" id="btn-add-model">＋ モデル追加</button></div>`;

            if (rows.length === 0) {
                html += `<div class="empty-state"><div class="empty-icon">🤖</div><p>モデルが登録されていません</p></div>`;
            } else {
                html += `<div class="section-card"><div class="table-wrapper"><table>
                    <thead><tr><th>ID</th><th>LiteLLM名</th><th>プロバイダー</th><th>Input / Output</th><th>コンテキスト</th><th>機能</th><th>ステータス</th><th>操作</th></tr></thead><tbody>`;
                for (const m of rows) {
                    const caps = [];
                    if (m.supports_streaming) caps.push(badge("stream", "info"));
                    if (m.supports_functions) caps.push(badge("func", "info"));
                    if (m.supports_vision) caps.push(badge("vision", "info"));
                    html += `<tr${!m.is_active ? ' style="opacity:0.5"' : ""}>
                        <td class="text-mono">${esc(m.id)}</td>
                        <td>${esc(m.litellm_name)}</td>
                        <td>${esc(m.provider)}</td>
                        <td>${fmtCost(m.input_cost)} / ${fmtCost(m.output_cost)}</td>
                        <td>${Number(m.context_window).toLocaleString()}</td>
                        <td>${caps.join(" ") || "—"}</td>
                        <td>${m.is_active ? badge("有効", "success") : badge("無効", "danger")}</td>
                        <td>
                            <button class="btn btn-sm btn-edit-model" data-id="${esc(m.id)}" data-json='${JSON.stringify(m).replace(/'/g, "&#39;")}'>編集</button>
                            <button class="btn btn-sm btn-toggle-model" data-id="${esc(m.id)}">${m.is_active ? "無効化" : "有効化"}</button>
                        </td>
                    </tr>`;
                }
                html += `</tbody></table></div></div>`;
            }

            $("page-content").innerHTML = html;

            $("btn-add-model").addEventListener("click", () => {
                openModal("モデル追加", modelForm(), `<button class="btn" onclick="closeModal()">キャンセル</button><button class="btn btn-primary" id="f-submit">作成</button>`);
                $("f-submit").addEventListener("click", async () => {
                    try {
                        await api("/models", { method: "POST", body: JSON.stringify(collectModelForm()) });
                        closeModal(); toast("モデルを作成しました"); renderModels();
                    } catch (e) { toast(e.message, "error"); }
                });
            });

            document.querySelectorAll(".btn-edit-model").forEach((btn) => {
                btn.addEventListener("click", () => {
                    const m = JSON.parse(btn.dataset.json);
                    openModal("モデル編集", modelForm(m), `<button class="btn" onclick="closeModal()">キャンセル</button><button class="btn btn-primary" id="f-submit">更新</button>`);
                    $("f-id").disabled = true;
                    $("f-submit").addEventListener("click", async () => {
                        try {
                            const data = collectModelForm();
                            delete data.id;
                            await api(`/models/${m.id}`, { method: "PUT", body: JSON.stringify(data) });
                            closeModal(); toast("モデルを更新しました"); renderModels();
                        } catch (e) { toast(e.message, "error"); }
                    });
                });
            });

            document.querySelectorAll(".btn-toggle-model").forEach((btn) => {
                btn.addEventListener("click", async () => {
                    try {
                        await api(`/models/${btn.dataset.id}/toggle`, { method: "PATCH" });
                        toast("ステータスを変更しました"); renderModels();
                    } catch (e) { toast(e.message, "error"); }
                });
            });
        } catch (e) {
            $("page-content").innerHTML = `<div class="empty-state"><p>${esc(e.message)}</p></div>`;
        }
    }

    function modelForm(m = {}) {
        return `
            <div class="form-row">
                <div class="form-group"><label>Model ID</label><input class="form-control" id="f-id" value="${esc(m.id || "")}" required></div>
                <div class="form-group"><label>LiteLLM名</label><input class="form-control" id="f-litellm" value="${esc(m.litellm_name || "")}" required></div>
            </div>
            <div class="form-group"><label>プロバイダー</label><input class="form-control" id="f-provider" value="${esc(m.provider || "")}" required></div>
            <div class="form-row">
                <div class="form-group"><label>Input Cost (¥/1M)</label><input class="form-control" id="f-icost" type="number" step="0.0001" value="${m.input_cost ?? ""}"></div>
                <div class="form-group"><label>Output Cost (¥/1M)</label><input class="form-control" id="f-ocost" type="number" step="0.0001" value="${m.output_cost ?? ""}"></div>
            </div>
            <div class="form-row">
                <div class="form-group"><label>コンテキスト窓</label><input class="form-control" id="f-ctx" type="number" value="${m.context_window ?? 4096}"></div>
                <div class="form-group"><label>最大出力トークン</label><input class="form-control" id="f-maxout" type="number" value="${m.max_output_tokens ?? 2048}"></div>
            </div>
            <div class="form-group">
                <label>機能</label>
                <div style="display:flex;gap:16px;margin-top:4px">
                    <label><input type="checkbox" id="f-stream" ${m.supports_streaming !== false ? "checked" : ""}> Streaming</label>
                    <label><input type="checkbox" id="f-func" ${m.supports_functions ? "checked" : ""}> Function Call</label>
                    <label><input type="checkbox" id="f-vision" ${m.supports_vision ? "checked" : ""}> Vision</label>
                </div>
            </div>
            <div class="form-group"><label>説明</label><input class="form-control" id="f-desc" value="${esc(m.description || "")}"></div>`;
    }

    function collectModelForm() {
        return {
            id: $("f-id").value,
            litellm_name: $("f-litellm").value,
            provider: $("f-provider").value,
            input_cost: parseFloat($("f-icost").value) || 0,
            output_cost: parseFloat($("f-ocost").value) || 0,
            context_window: parseInt($("f-ctx").value) || 4096,
            max_output_tokens: parseInt($("f-maxout").value) || 2048,
            supports_streaming: $("f-stream").checked,
            supports_functions: $("f-func").checked,
            supports_vision: $("f-vision").checked,
            description: $("f-desc").value || null,
        };
    }

    // ── Endpoints ───────────────────────────────────────────────

    async function renderEndpoints() {
        try {
            const rows = await api("/endpoints");
            if (!rows) return;

            let html = `<div class="toolbar"><button class="btn btn-primary" id="btn-add-ep">＋ エンドポイント追加</button></div>`;

            if (rows.length === 0) {
                html += `<div class="empty-state"><div class="empty-icon">🌐</div><p>エンドポイントが登録されていません</p></div>`;
            } else {
                html += `<div class="section-card"><div class="table-wrapper"><table>
                    <thead><tr><th>モデル</th><th>タイプ</th><th>URL</th><th>ルーティング</th><th>ヘルス</th><th>レイテンシ</th><th>リクエスト数</th><th>操作</th></tr></thead><tbody>`;
                for (const ep of rows) {
                    html += `<tr${!ep.is_active ? ' style="opacity:0.5"' : ""}>
                        <td>${esc(ep.model_id)}</td>
                        <td>${badge(ep.endpoint_type, "info")}</td>
                        <td class="text-mono truncate">${esc(ep.base_url)}</td>
                        <td>${esc(ep.routing_strategy)} (P${ep.routing_priority})</td>
                        <td>${healthBadge(ep.health_status)}</td>
                        <td>${ep.avg_latency_ms}ms</td>
                        <td>${Number(ep.total_requests).toLocaleString()}</td>
                        <td>
                            <button class="btn btn-sm btn-edit-ep" data-id="${esc(ep.id)}" data-json='${JSON.stringify(ep).replace(/'/g, "&#39;")}'>編集</button>
                            <button class="btn btn-sm btn-trigger-health" data-id="${esc(ep.id)}">ヘルス</button>
                            <button class="btn btn-sm btn-toggle-ep" data-id="${esc(ep.id)}">${ep.is_active ? "無効化" : "有効化"}</button>
                        </td>
                    </tr>`;
                }
                html += `</tbody></table></div></div>`;
            }

            $("page-content").innerHTML = html;

            $("btn-add-ep").addEventListener("click", async () => {
                // Fetch models for dropdown
                let modelOptions = '<option value="">選択してください</option>';
                try {
                    const models = await api("/models");
                    if (models && models.length > 0) {
                        modelOptions += models.map(m => `<option value="${esc(m.id)}">${esc(m.litellm_name || m.id)} (${esc(m.id)})</option>`).join("");
                    }
                } catch (e) {
                    console.error("Failed to load models for dropdown", e);
                }

                openModal("エンドポイント追加", endpointForm({}, modelOptions),
                    `<button class="btn" onclick="closeModal()">キャンセル</button><button class="btn btn-primary" id="f-submit">作成</button>`);
                $("f-submit").addEventListener("click", async () => {
                    try {
                        await api("/endpoints", { method: "POST", body: JSON.stringify(collectEndpointForm()) });
                        closeModal(); toast("エンドポイントを作成しました"); renderEndpoints();
                    } catch (e) { toast(e.message, "error"); }
                });
            });

            document.querySelectorAll(".btn-edit-ep").forEach((btn) => {
                btn.addEventListener("click", async () => {
                    const ep = JSON.parse(btn.dataset.json);

                    // Fetch models for dropdown (even for edit, to show correct name)
                    let modelOptions = '<option value="">選択してください</option>';
                    try {
                        const models = await api("/models");
                        if (models && models.length > 0) {
                            modelOptions += models.map(m => `<option value="${esc(m.id)}" ${m.id === ep.model_id ? "selected" : ""}>${esc(m.litellm_name || m.id)} (${esc(m.id)})</option>`).join("");
                        }
                    } catch (e) {
                        console.error("Failed to load models for dropdown", e);
                        // Fallback if fetch fails or model not found in list (though unlikely if ref integrity holds)
                        modelOptions += `<option value="${esc(ep.model_id)}" selected>${esc(ep.model_id)}</option>`;
                    }

                    openModal("エンドポイント編集", endpointForm(ep, modelOptions),
                        `<button class="btn" onclick="closeModal()">キャンセル</button><button class="btn btn-primary" id="f-submit">更新</button>`);
                    $("f-model-id").disabled = true;
                    $("f-submit").addEventListener("click", async () => {
                        try {
                            const data = collectEndpointForm();
                            delete data.model_id;
                            await api(`/endpoints/${ep.id}`, { method: "PUT", body: JSON.stringify(data) });
                            closeModal(); toast("エンドポイントを更新しました"); renderEndpoints();
                        } catch (e) { toast(e.message, "error"); }
                    });
                });
            });

            document.querySelectorAll(".btn-toggle-ep").forEach((btn) => {
                btn.addEventListener("click", async () => {
                    try {
                        await api(`/endpoints/${btn.dataset.id}/toggle`, { method: "PATCH" });
                        toast("ステータスを変更しました"); renderEndpoints();
                    } catch (e) { toast(e.message, "error"); }
                });
            });

            document.querySelectorAll(".btn-trigger-health").forEach((btn) => {
                btn.addEventListener("click", async () => {
                    const originalText = btn.textContent;
                    btn.textContent = "確認中...";
                    btn.disabled = true;
                    try {
                        const res = await api(`/endpoints/${btn.dataset.id}/health-check`, { method: "POST" });
                        toast(`ヘルスチェック完了: ${res.health_status} (${res.avg_latency_ms}ms)`);
                        renderEndpoints();
                    } catch (e) {
                        toast(e.message, "error");
                        btn.textContent = originalText;
                        btn.disabled = false;
                    }
                });
            });
        } catch (e) {
            $("page-content").innerHTML = `<div class="empty-state"><p>${esc(e.message)}</p></div>`;
        }
    }

    function endpointForm(ep = {}, modelOptions = "") {
        const modelInput = modelOptions
            ? `<select class="form-control" id="f-model-id" required>${modelOptions}</select>`
            : `<input class="form-control" id="f-model-id" value="${esc(ep.model_id || "")}" required>`;

        return `
            <div class="form-group"><label>モデルID</label>${modelInput}</div>
            <div class="form-row">
                <div class="form-group"><label>タイプ</label>
                    <select class="form-control" id="f-type">
                        ${["vllm", "ollama", "tgi", "custom"].map(t => `<option value="${t}" ${ep.endpoint_type === t ? "selected" : ""}>${t}</option>`).join("")}
                    </select>
                </div>
                <div class="form-group"><label>ルーティング戦略</label>
                    <select class="form-control" id="f-strategy">
                        ${["round-robin", "usage-based", "latency-based", "random"].map(s => `<option value="${s}" ${ep.routing_strategy === s ? "selected" : ""}>${s}</option>`).join("")}
                    </select>
                </div>
            </div>
            <div class="form-group"><label>Base URL</label><input class="form-control" id="f-url" value="${esc(ep.base_url || "")}" required></div>
            <div class="form-row">
                <div class="form-group"><label>優先度</label><input class="form-control" id="f-priority" type="number" value="${ep.routing_priority ?? 100}"></div>
                <div class="form-group"><label>タイムアウト (秒)</label><input class="form-control" id="f-timeout" type="number" value="${ep.timeout_seconds ?? 120}"></div>
            </div>
            <div class="form-group"><label>API Key参照ID</label><input class="form-control" id="f-keyref" value="${esc(ep.api_key_ref || "")}"></div>`;
    }

    function collectEndpointForm() {
        return {
            model_id: $("f-model-id").value,
            endpoint_type: $("f-type").value,
            base_url: $("f-url").value,
            routing_strategy: $("f-strategy").value,
            routing_priority: parseInt($("f-priority").value) || 100,
            timeout_seconds: parseInt($("f-timeout").value) || 120,
            api_key_ref: $("f-keyref").value || null,
        };
    }

    // ── Usage Logs ──────────────────────────────────────────────

    let usageLogsPage = 1;

    async function renderUsageLogs() {
        try {
            const params = new URLSearchParams({ page: usageLogsPage, per_page: 30 });
            const uid = $("ulf-user")?.value;
            const model = $("ulf-model")?.value;
            const status = $("ulf-status")?.value;
            const dateFrom = $("ulf-from")?.value;
            const dateTo = $("ulf-to")?.value;
            if (uid) params.set("user_oid", uid);
            if (model) params.set("model", model);
            if (status) params.set("status", status);
            if (dateFrom) params.set("date_from", dateFrom);
            if (dateTo) params.set("date_to", dateTo);

            const res = await api(`/usage-logs?${params}`);
            if (!res) return;

            let html = `
                <div class="filter-bar" id="usage-filter">
                    <div class="form-group"><label>ユーザー</label><input class="form-control" id="ulf-user" value="${esc(uid || "")}"></div>
                    <div class="form-group"><label>モデル</label><input class="form-control" id="ulf-model" value="${esc(model || "")}"></div>
                    <div class="form-group"><label>ステータス</label>
                        <select class="form-control" id="ulf-status">
                            <option value="">全て</option>
                            ${["completed", "pending", "failed", "cancelled"].map(s => `<option value="${s}" ${status === s ? "selected" : ""}>${s}</option>`).join("")}
                        </select>
                    </div>
                    <div class="form-group"><label>開始日</label><input class="form-control" id="ulf-from" type="date" value="${esc(dateFrom || "")}"></div>
                    <div class="form-group"><label>終了日</label><input class="form-control" id="ulf-to" type="date" value="${esc(dateTo || "")}"></div>
                    <div class="form-group"><label>&nbsp;</label><button class="btn btn-primary btn-sm" id="ulf-go">検索</button></div>
                </div>`;

            if (res.data.length === 0) {
                html += `<div class="empty-state"><div class="empty-icon">📋</div><p>ログが見つかりません</p></div>`;
            } else {
                html += `<div class="section-card"><div class="table-wrapper"><table>
                    <thead><tr><th>日時</th><th>ユーザー</th><th>モデル</th><th>トークン</th><th>コスト</th><th>レイテンシ</th><th>ステータス</th></tr></thead><tbody>`;
                for (const l of res.data) {
                    html += `<tr>
                        <td>${fmtDate(l.created_at)}</td>
                        <td class="text-mono">${esc(l.user_oid)}</td>
                        <td>${esc(l.actual_model || l.requested_model)}</td>
                        <td>${l.input_tokens} / ${l.output_tokens}</td>
                        <td>${fmtCost(l.cost)}</td>
                        <td>${l.latency_ms != null ? l.latency_ms + "ms" : "—"}</td>
                        <td>${reqStatusBadge(l.status)}</td>
                    </tr>`;
                }
                html += `</tbody></table></div></div>`;
                html += renderPagination(res.total, res.page, res.per_page, "usageLogs");
            }

            $("page-content").innerHTML = html;
            $("ulf-go")?.addEventListener("click", () => { usageLogsPage = 1; renderUsageLogs(); });
        } catch (e) {
            $("page-content").innerHTML = `<div class="empty-state"><p>${esc(e.message)}</p></div>`;
        }
    }

    // ── Audit Logs ──────────────────────────────────────────────

    let auditLogsPage = 1;

    async function renderAuditLogs() {
        try {
            const params = new URLSearchParams({ page: auditLogsPage, per_page: 30 });
            const action = $("alf-action")?.value;
            const dateFrom = $("alf-from")?.value;
            const dateTo = $("alf-to")?.value;
            if (action) params.set("action", action);
            if (dateFrom) params.set("date_from", dateFrom);
            if (dateTo) params.set("date_to", dateTo);

            const res = await api(`/audit-logs?${params}`);
            if (!res) return;

            let html = `
                <div class="filter-bar" id="audit-filter">
                    <div class="form-group"><label>アクション</label><input class="form-control" id="alf-action" value="${esc(action || "")}"></div>
                    <div class="form-group"><label>開始日</label><input class="form-control" id="alf-from" type="date" value="${esc(dateFrom || "")}"></div>
                    <div class="form-group"><label>終了日</label><input class="form-control" id="alf-to" type="date" value="${esc(dateTo || "")}"></div>
                    <div class="form-group"><label>&nbsp;</label><button class="btn btn-primary btn-sm" id="alf-go">検索</button></div>
                </div>`;

            if (res.data.length === 0) {
                html += `<div class="empty-state"><div class="empty-icon">🔍</div><p>監査ログが見つかりません</p></div>`;
            } else {
                html += `<div class="section-card"><div class="table-wrapper"><table>
                    <thead><tr><th>日時</th><th>管理者</th><th>アクション</th><th>対象</th><th>メタデータ</th></tr></thead><tbody>`;
                for (const a of res.data) {
                    const meta = a.metadata ? JSON.stringify(a.metadata, null, 1) : "—";
                    html += `<tr>
                        <td>${fmtDate(a.timestamp)}</td>
                        <td class="text-mono">${esc(a.admin_oid)}</td>
                        <td>${badge(a.action, "info")}</td>
                        <td>${esc(a.target_type || "")} ${esc(a.target_id || "")}</td>
                        <td class="text-mono" style="max-width:300px;overflow:hidden;text-overflow:ellipsis" title="${esc(meta)}">${esc(meta)}</td>
                    </tr>`;
                }
                html += `</tbody></table></div></div>`;
                html += renderPagination(res.total, res.page, res.per_page, "auditLogs");
            }

            $("page-content").innerHTML = html;
            $("alf-go")?.addEventListener("click", () => { auditLogsPage = 1; renderAuditLogs(); });
        } catch (e) {
            $("page-content").innerHTML = `<div class="empty-state"><p>${esc(e.message)}</p></div>`;
        }
    }

    // ── Pagination Helper ───────────────────────────────────────

    function renderPagination(total, page, perPage, kind) {
        const totalPages = Math.ceil(total / perPage) || 1;
        let html = `<div class="pagination">
            <button ${page <= 1 ? "disabled" : ""} onclick="window._paging('${kind}', ${page - 1})">← 前</button>
            <span class="page-info">${page} / ${totalPages}（全${total}件）</span>
            <button ${page >= totalPages ? "disabled" : ""} onclick="window._paging('${kind}', ${page + 1})">次 →</button>
        </div>`;
        return html;
    }

    window._paging = (kind, page) => {
        if (kind === "usageLogs") { usageLogsPage = page; renderUsageLogs(); }
        if (kind === "auditLogs") { auditLogsPage = page; renderAuditLogs(); }
    };

    // Expose closeModal globally for inline onclick
    window.closeModal = closeModal;

    // ── Init ────────────────────────────────────────────────────

    navigate();
})();
