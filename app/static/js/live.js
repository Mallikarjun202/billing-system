// Live updates over Server-Sent Events (GET /api/events).
//
// - Elements marked data-live="name" data-live-on="event event" are re-fetched
//   from the server and swapped in place when one of those events arrives.
// - Pages can listen with Live.on("purchase" | "email" | "products" | "drawer" | "resync", fn).
// - New bills and email results pop up as toasts on every page.
(() => {
    const TYPES = ["purchase", "email", "products", "drawer"];
    const HIDDEN_GRACE_MS = 10000; // close the stream after a tab has been hidden this long
    // Keep in step with the email_badge macro in templates/_macros.html.
    const EMAIL_BADGES = {
        pending: ["mail", "Sending…"],
        sent: ["check", "Emailed"],
        saved: ["mail", "Saved to outbox"],
        failed: ["alert", "Email failed"],
    };

    const listeners = new Map();
    const statusEl = document.getElementById("live-status");
    const toastsEl = document.getElementById("toasts");
    const currency = window.CURRENCY || "";
    const fmt2 = new Intl.NumberFormat(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    const money = (n) => `${currency}${fmt2.format(n)}`;

    let source = null;
    let connectedBefore = false;
    let hideTimer = null;
    let retryTimer = null;

    // ---------- small helpers ----------
    function emit(type, data) {
        for (const fn of listeners.get(type) || []) {
            try { fn(data); } catch (err) { console.error(err); }
        }
    }

    function el(tag, cls, text) {
        const node = document.createElement(tag);
        if (cls) node.className = cls;
        if (text != null) node.textContent = text;
        return node;
    }

    function iconSvg(name) {
        const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
        svg.setAttribute("class", "i");
        svg.setAttribute("aria-hidden", "true");
        const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
        use.setAttribute("href", `#i-${name}`);
        svg.appendChild(use);
        return svg;
    }

    function emailBadge(status) {
        const [icon, label] = EMAIL_BADGES[status] || ["mail", status];
        const badge = el("span", `badge badge-${status}`);
        badge.append(iconSvg(icon), label);
        return badge;
    }

    function setStatus(state) {
        if (!statusEl) return;
        const labels = { live: "Live", connecting: "Connecting", offline: "Reconnecting…" };
        const titles = {
            live: "Live: this page updates by itself.",
            connecting: "Connecting for live updates…",
            offline: "Lost the live connection. Retrying…",
        };
        statusEl.dataset.state = state;
        statusEl.title = titles[state];
        statusEl.querySelector(".live-label").textContent = labels[state];
        emit("status", state);
    }

    // ---------- toasts ----------
    function toast({ tone = "info", icon = "check", title, body, href, linkText = "View", timeout = 6000 }) {
        if (!toastsEl) return;
        const node = el("div", `toast toast-${tone}`);
        node.setAttribute("role", tone === "error" ? "alert" : "status");
        const text = el("div", "toast-text");
        text.append(el("strong", null, title));
        if (body) text.append(el("span", null, body));
        node.append(el("span", "toast-icon"), text);
        node.firstChild.append(iconSvg(icon));
        if (href) {
            const link = el("a", "toast-link", linkText);
            link.href = href;
            node.append(link);
        }
        const close = el("button", "toast-close");
        close.type = "button";
        close.setAttribute("aria-label", "Dismiss");
        close.append(iconSvg("x"));
        node.append(close);

        const dismiss = () => {
            node.classList.add("leaving");
            setTimeout(() => node.remove(), 200);
        };
        close.addEventListener("click", dismiss);
        let timer = setTimeout(dismiss, timeout);
        node.addEventListener("mouseenter", () => clearTimeout(timer));
        node.addEventListener("mouseleave", () => { timer = setTimeout(dismiss, 2500); });

        toastsEl.append(node);
        while (toastsEl.children.length > 4) toastsEl.firstElementChild.remove();
    }

    function onInvoicePage(id) {
        const box = document.getElementById("email-status");
        return box && Number(box.dataset.purchase) === id;
    }

    function toastFor(type, data) {
        if (type === "purchase") {
            toast({
                icon: "receipt",
                title: `New bill #${data.id} · ${money(data.amount)}`,
                body: `${data.items} item${data.items === 1 ? "" : "s"} · ${data.customer_email}`,
                href: `/purchases/${data.id}`,
            });
        } else if (type === "email" && !onInvoicePage(data.id)) {
            if (data.status === "sent") {
                toast({ tone: "success", icon: "mail", title: `Invoice #${data.id} emailed`, body: data.customer_email, timeout: 4500 });
            } else if (data.status === "saved") {
                toast({ icon: "mail", title: `Invoice #${data.id} saved to outbox`, body: "Email isn't set up, so it was saved as a file.", timeout: 4500 });
            } else if (data.status === "failed") {
                toast({
                    tone: "error", icon: "alert", title: `Invoice #${data.id} email failed`,
                    body: data.error || data.customer_email, href: `/purchases/${data.id}`, linkText: "Open", timeout: 12000,
                });
            }
        }
    }

    // ---------- live regions ----------
    const pauseChecks = [];
    let refreshing = false;
    let refreshAgain = false;
    let refreshTimer = null;
    let pendingWhilePaused = false;

    const paused = () => pauseChecks.some((fn) => fn());

    function scheduleRefresh() {
        clearTimeout(refreshTimer);
        refreshTimer = setTimeout(refresh, 250); // one refresh for a burst of events
    }

    async function refresh() {
        if (!document.querySelector("[data-live]")) return;
        if (paused()) { pendingWhilePaused = true; return; }
        if (refreshing) { refreshAgain = true; return; }
        refreshing = true;
        try {
            const response = await fetch(location.href, { headers: { Accept: "text/html" }, cache: "no-store" });
            if (!response.ok) return;
            const doc = new DOMParser().parseFromString(await response.text(), "text/html");
            if (paused()) { pendingWhilePaused = true; return; }
            for (const old of document.querySelectorAll("[data-live]")) {
                const fresh = doc.querySelector(`[data-live="${CSS.escape(old.dataset.live)}"]`);
                if (fresh) swap(old, fresh);
            }
        } catch (err) {
            console.warn("Live refresh failed", err);
        } finally {
            refreshing = false;
            if (refreshAgain) { refreshAgain = false; scheduleRefresh(); }
        }
    }

    function swap(old, fresh) {
        // Keep what the viewer opened, mark what's new, and flash what changed.
        const openDetails = [...old.querySelectorAll("details")].map((d) => d.open);
        fresh.querySelectorAll("details").forEach((d, i) => { if (i < openDetails.length) d.open = openDetails[i]; });
        const oldKeys = new Set([...old.querySelectorAll("[data-key]")].map((n) => n.dataset.key));
        const oldFlash = [...old.querySelectorAll("[data-flash]")].map((n) => n.textContent.trim());

        fresh.classList.add("live-swapped");
        old.replaceWith(fresh);

        if (oldKeys.size) {
            for (const n of fresh.querySelectorAll("[data-key]")) {
                if (!oldKeys.has(n.dataset.key)) n.classList.add("live-new");
            }
        }
        fresh.querySelectorAll("[data-flash]").forEach((n, i) => {
            if (oldFlash[i] !== undefined && oldFlash[i] !== n.textContent.trim()) n.classList.add("live-changed");
        });
    }

    function regionEvents() {
        const types = new Set();
        for (const node of document.querySelectorAll("[data-live-on]")) {
            for (const t of node.dataset.liveOn.split(/\s+/)) if (t) types.add(t);
        }
        return types;
    }

    // ---------- connection ----------
    function connect() {
        if (source || !("EventSource" in window)) return;
        clearTimeout(retryTimer);
        setStatus(connectedBefore ? "offline" : "connecting");
        source = new EventSource("/api/events");

        source.addEventListener("open", () => {
            setStatus("live");
            if (connectedBefore) resync(); // catch up on anything missed while disconnected
            connectedBefore = true;
        });
        source.addEventListener("error", () => {
            setStatus("offline");
            if (source && source.readyState === EventSource.CLOSED) {
                // The browser gave up (e.g. the server was down); try again shortly.
                source = null;
                retryTimer = setTimeout(connect, 5000);
            }
        });
        for (const type of TYPES) {
            source.addEventListener(type, (event) => {
                let data = {};
                try { data = JSON.parse(event.data); } catch { /* empty */ }
                toastFor(type, data);
                if (regionEvents().has(type)) scheduleRefresh();
                emit(type, data);
            });
        }
    }

    function disconnect() {
        if (source) source.close();
        source = null;
    }

    function resync() {
        scheduleRefresh();
        emit("resync", {});
    }

    document.addEventListener("visibilitychange", () => {
        clearTimeout(hideTimer);
        if (document.hidden) {
            hideTimer = setTimeout(disconnect, HIDDEN_GRACE_MS);
        } else if (!source) {
            connect(); // its "open" handler re-syncs the page
        }
    });
    window.addEventListener("pagehide", disconnect);
    window.addEventListener("pageshow", (event) => { if (event.persisted) connect(); });

    window.Live = {
        on(type, fn) {
            if (!listeners.has(type)) listeners.set(type, []);
            listeners.get(type).push(fn);
        },
        refresh,
        pauseWhile(fn) { pauseChecks.push(fn); },
        resume() {
            if (pendingWhilePaused && !paused()) { pendingWhilePaused = false; refresh(); }
        },
        toast,
        emailBadge,
        get connected() { return statusEl?.dataset.state === "live"; },
    };

    connect();
})();
