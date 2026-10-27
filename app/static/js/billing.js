// Point-of-sale page. The server recalculates everything on submit; this
// mirrors its rules for a live preview (2-dp rounding per line, amount due
// rounded down, change taken only from the drawer's notes and coins).
(() => {
    const form = document.getElementById("billing-form");
    if (!form) return;

    const DENOMS = [500, 50, 20, 10, 5, 2, 1];
    const HUES = [232, 262, 292, 330, 12, 38, 160, 190]; // same as templating.avatar_hue

    const rowsEl = document.getElementById("item-rows");
    const template = document.getElementById("row-template");
    const cartEmpty = document.getElementById("cart-empty");
    const catalog = new Map(JSON.parse(document.getElementById("catalog").textContent).map((p) => [p.id, p]));
    const currency = window.CURRENCY || "";
    const fmt2 = new Intl.NumberFormat(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    const fmt0 = new Intl.NumberFormat(undefined, { maximumFractionDigits: 0 });
    const money = (paise) => `${paise < 0 ? "-" : ""}${currency}${fmt2.format(Math.abs(paise) / 100)}`;
    const rupees = (paise) => `${currency}${fmt0.format(paise / 100)}`;
    const toPaise = (n) => Math.round(Number(n) * 100);
    const $ = (sel) => form.querySelector(sel);
    const totals = Object.fromEntries([...document.querySelectorAll("[data-total]")].map((el) => [el.dataset.total, el]));
    const cashInput = form.elements.cash_paid;

    const hue = (key) => HUES[[...key].reduce((s, c) => s + c.charCodeAt(0), 0) % HUES.length];
    const initials = (name) => {
        const words = name.split("@")[0].split(/[\s._-]+/).filter(Boolean);
        return words.length > 1 ? (words[0][0] + words[1][0]).toUpperCase() : (words[0] || "?").slice(0, 2).toUpperCase();
    };

    // ---------- change (mirrors app/billing.calculate_change) ----------
    function drawerStock() {
        const stock = {};
        for (const input of form.querySelectorAll("[data-denom]")) {
            stock[input.dataset.denom] = Math.max(0, parseInt(input.value, 10) || 0);
        }
        return stock;
    }

    function makeChange(amount, stock) {
        const total = DENOMS.reduce((s, v) => s + v * (stock[v] || 0), 0);
        const target = Math.min(amount, total);
        const greedy = {};
        let left = target;
        for (const v of DENOMS) {
            const use = Math.min(Math.floor(left / v), stock[v] || 0);
            if (use) { greedy[v] = use; left -= use * v; }
        }
        if (left === 0 || target > 20000) return greedy;

        // Exact search: fewest pieces for the largest reachable amount <= target.
        // Bounded knapsack with binary-split counts, as on the server.
        const parts = [];
        for (const v of DENOMS) {
            let count = stock[v] || 0, chunk = 1;
            while (count > 0) {
                const take = Math.min(chunk, count);
                parts.push([v * take, take, v]);
                count -= take;
                chunk *= 2;
            }
        }
        const best = new Array(target + 1).fill(Infinity);
        best[0] = 0;
        const used = parts.map(([pv, pieces]) => {
            const took = new Uint8Array(target + 1);
            for (let x = target; x >= pv; x--) {
                if (best[x - pv] + pieces < best[x]) { best[x] = best[x - pv] + pieces; took[x] = 1; }
            }
            return took;
        });
        let x = target;
        while (x > 0 && best[x] === Infinity) x--;
        const out = {};
        for (let i = parts.length - 1; i >= 0 && x > 0; i--) {
            if (used[i][x]) {
                const [pv, pieces, v] = parts[i];
                out[v] = (out[v] || 0) + pieces;
                x -= pv;
            }
        }
        return out;
    }

    function tokenHtml(value, count) {
        const kind = value <= 5 ? "coin" : "note";
        return `<span class="token ${kind} v${value}"><span class="token-face">${currency}${value}</span><span class="token-count">× ${count}</span></span>`;
    }

    // ---------- rows ----------
    function rowFor(id) {
        return [...rowsEl.querySelectorAll(".item-row")].find((r) => r.querySelector("select").value === id);
    }

    function addRow(id = "", qty = 1) {
        rowsEl.appendChild(template.content.cloneNode(true));
        const row = rowsEl.lastElementChild;
        row.querySelector("select").value = id;
        row.querySelector("input").value = qty;
        return row;
    }

    function addProduct(id) {
        const row = rowFor(id);
        if (row) {
            const input = row.querySelector("input");
            input.value = (parseInt(input.value, 10) || 0) + 1;
            flash(row.querySelector(".line-total"));
        } else {
            addRow(id, 1);
        }
        recalc();
    }

    function flash(el) {
        el.classList.remove("bump");
        void el.offsetWidth;
        el.classList.add("bump");
    }

    // ---------- recalc ----------
    function recalc() {
        const rows = [...rowsEl.querySelectorAll(".item-row")];
        cartEmpty.hidden = rows.length > 0;

        const requested = new Map();
        for (const row of rows) {
            const id = row.querySelector("select").value;
            const qty = parseInt(row.querySelector("input").value, 10) || 0;
            if (id) requested.set(id, (requested.get(id) || 0) + qty);
        }

        let subtotal = 0, tax = 0, pieces = 0;
        for (const row of rows) {
            const select = row.querySelector("select");
            const qtyInput = row.querySelector("input");
            const hint = row.querySelector(".row-hint");
            const av = row.querySelector("[data-avatar]");
            const product = catalog.get(select.value);
            const qty = parseInt(qtyInput.value, 10) || 0;

            let line = 0;
            hint.classList.remove("warn");
            qtyInput.removeAttribute("aria-invalid");
            if (product) {
                const price = toPaise(product.price) * qty;
                const lineTax = Math.round((price * product.tax) / 100);
                line = price + lineTax;
                subtotal += price;
                tax += lineTax;
                pieces += qty;
                av.textContent = initials(product.name);
                av.style.setProperty("--h", hue(product.id));
                if ((requested.get(product.id) || 0) > product.stock) {
                    hint.textContent = `Only ${product.stock} in stock`;
                    hint.classList.add("warn");
                    qtyInput.setAttribute("aria-invalid", "true");
                } else {
                    hint.textContent = `${money(toPaise(product.price))} × ${qty} + ${product.tax}% tax`;
                }
            } else {
                av.textContent = "?";
                av.style.removeProperty("--h");
                hint.textContent = "";
            }
            row.querySelector(".line-total").textContent = money(line);
        }

        // Product tiles show how many are on the bill.
        for (const tile of document.querySelectorAll(".product-tile")) {
            const n = requested.get(tile.dataset.id) || 0;
            const badge = tile.querySelector(".tile-qty");
            badge.hidden = n === 0;
            badge.textContent = n;
            tile.classList.toggle("in-cart", n > 0);
        }

        const net = subtotal + tax;
        const due = Math.floor(net / 100) * 100;
        totals.subtotal.textContent = money(subtotal);
        totals.tax.textContent = money(tax);
        totals.net.textContent = money(net);
        totals.net2.textContent = money(net);
        totals.due.textContent = rupees(due);
        totals.items.textContent = `${pieces} item${pieces === 1 ? "" : "s"}`;
        document.getElementById("items-sub").textContent = rows.length
            ? `${rows.length} line${rows.length === 1 ? "" : "s"} · ${pieces} item${pieces === 1 ? "" : "s"}`
            : "No items yet";

        renderQuickCash(due);
        renderChange(due);
        renderDrawerTotal();
    }

    function renderQuickCash(due) {
        const box = document.getElementById("quick-cash");
        const key = String(due);
        if (box.dataset.for === key) return;
        box.dataset.for = key;
        box.innerHTML = "";
        if (due <= 0) return;
        const r = due / 100;
        const options = [r];
        for (const step of [10, 50, 100, 500, 2000]) {
            const v = Math.ceil(r / step) * step;
            if (v > r && !options.includes(v)) options.push(v);
            if (options.length === 4) break;
        }
        for (const v of options) {
            const b = document.createElement("button");
            b.type = "button";
            b.textContent = v === r ? `Exact ${currency}${fmt0.format(v)}` : `${currency}${fmt0.format(v)}`;
            b.addEventListener("click", () => { cashInput.value = v; recalc(); });
            box.appendChild(b);
        }
    }

    function renderChange(due) {
        const box = form.querySelector("[data-change]");
        const label = form.querySelector("[data-change-label]");
        const tokens = form.querySelector("[data-change-tokens]");
        const hint = form.querySelector("[data-change-hint]");
        const raw = cashInput.value.trim();
        const balance = toPaise(raw || 0) - due;

        totals.balance.textContent = money(balance);
        box.classList.toggle("ok", raw !== "" && due > 0 && balance >= 0);
        box.classList.toggle("short", raw !== "" && balance < 0);
        label.textContent = raw !== "" && balance < 0 ? "Still owed" : "Change to return";
        if (raw === "") totals.balance.textContent = money(0);
        tokens.innerHTML = "";
        hint.hidden = true;

        if (raw === "" || balance <= 0) return;
        const whole = Math.floor(balance / 100);
        const change = makeChange(whole, drawerStock());
        const given = Object.entries(change).reduce((s, [v, c]) => s + v * c, 0);
        tokens.innerHTML = Object.entries(change)
            .sort((a, b) => b[0] - a[0])
            .map(([v, c]) => tokenHtml(Number(v), c))
            .join("");
        const short = balance - given * 100;
        if (short > 0) {
            hint.hidden = false;
            hint.textContent = `${money(short)} can't be given from the drawer.`;
        }
    }

    function renderDrawerTotal() {
        const stock = drawerStock();
        const total = DENOMS.reduce((s, v) => s + v * stock[v], 0);
        document.getElementById("drawer-total").textContent = `${currency}${fmt0.format(total)} in drawer`;
    }

    // ---------- events ----------
    document.getElementById("product-grid")?.addEventListener("click", (e) => {
        const tile = e.target.closest(".product-tile");
        if (tile && !tile.disabled) addProduct(tile.dataset.id);
    });

    document.getElementById("product-search")?.addEventListener("input", (e) => {
        const q = e.target.value.trim().toLowerCase();
        let shown = 0;
        for (const tile of document.querySelectorAll(".product-tile")) {
            const match = !q || tile.dataset.search.includes(q);
            tile.hidden = !match;
            shown += match;
        }
        document.getElementById("no-match").hidden = shown > 0;
    });

    document.getElementById("add-row").addEventListener("click", () => {
        addRow().querySelector("select").focus();
        recalc();
    });

    rowsEl.addEventListener("click", (e) => {
        const step = e.target.closest("[data-step]");
        if (step) {
            const input = step.closest(".item-row").querySelector("input");
            const next = (parseInt(input.value, 10) || 0) + Number(step.dataset.step);
            if (next < 1) step.closest(".item-row").remove();
            else input.value = next;
            recalc();
            return;
        }
        const remove = e.target.closest(".remove-row");
        if (remove) {
            remove.closest(".item-row").remove();
            recalc();
        }
    });

    // Typing in the search box shouldn't recalc; everything else should.
    form.addEventListener("input", (e) => { if (e.target.id !== "product-search") recalc(); });
    form.addEventListener("change", recalc);

    form.addEventListener("submit", (e) => {
        const submit = $('[type="submit"]');
        if (!rowsEl.querySelector(".item-row")) {
            e.preventDefault();
            flash(cartEmpty);
            cartEmpty.scrollIntoView({ behavior: "smooth", block: "center" });
            return;
        }
        if (!form.checkValidity()) return;
        if (submit.disabled) { e.preventDefault(); return; }
        submit.disabled = true;
        submit.querySelector("span").textContent = "Generating…";
    });

    window.addEventListener("pageshow", () => {
        const submit = $('[type="submit"]');
        submit.disabled = false;
        submit.querySelector("span").textContent = "Generate bill";
    });

    // ---------- live updates from other tills/tabs ----------
    // Drawer counts the cashier typed are theirs; everything else follows the server.
    for (const input of form.querySelectorAll("[data-denom]")) {
        input.addEventListener("input", () => { input.dataset.dirty = "1"; });
    }

    function stockBadge(stock) {
        const badge = document.createElement("span");
        badge.className = stock === 0 ? "badge badge-danger" : stock < 10 ? "badge badge-warn" : "badge";
        badge.textContent = stock === 0 ? "Sold out" : stock < 10 ? `${stock} left` : `${stock} in stock`;
        return badge;
    }

    const untouched = () =>
        !rowsEl.querySelector(".item-row") && !form.elements.customer_email.value && !cashInput.value;

    async function syncProducts() {
        const response = await fetch("/api/products", { cache: "no-store" });
        if (!response.ok) return;
        const products = await response.json();
        const ids = new Set(products.map((p) => p.product_id));
        const catalogChanged = ids.size !== catalog.size || [...catalog.keys()].some((id) => !ids.has(id));
        if (catalogChanged) {
            if (untouched()) location.reload();
            else document.getElementById("catalog-changed").hidden = false;
        }
        for (const p of products) {
            const item = catalog.get(p.product_id);
            if (!item) continue;
            const before = item.stock;
            Object.assign(item, { name: p.name, price: p.price, tax: p.tax_percentage, stock: p.available_stock });
            const tile = document.querySelector(`.product-tile[data-id="${CSS.escape(p.product_id)}"]`);
            if (tile) {
                tile.disabled = item.stock === 0;
                tile.querySelector(".tile-stock").replaceChildren(stockBadge(item.stock));
                if (before !== item.stock) flash(tile.querySelector(".tile-stock"));
            }
            for (const option of form.querySelectorAll(`select option[value="${CSS.escape(p.product_id)}"]`)) {
                option.disabled = item.stock === 0 && !option.selected;
                option.textContent = `${item.name} (${p.product_id})${item.stock === 0 ? " - sold out" : ""}`;
            }
        }
        recalc();
    }

    async function syncDrawer() {
        const response = await fetch("/api/denominations", { cache: "no-store" });
        if (!response.ok) return;
        for (const { value, count } of await response.json()) {
            const input = form.querySelector(`[data-denom="${value}"]`);
            if (!input || input.dataset.dirty || document.activeElement === input) continue;
            if (Number(input.value) !== count) {
                input.value = count;
                flash(input);
            }
        }
        recalc();
    }

    if (window.Live) {
        const submitting = () => $('[type="submit"]').disabled;
        const guard = (fn) => () => { if (!submitting()) fn().catch((err) => console.warn(err)); };
        Live.on("products", guard(syncProducts));
        Live.on("drawer", guard(syncDrawer));
        Live.on("resync", guard(() => Promise.all([syncProducts(), syncDrawer()])));
    }

    recalc();
})();
