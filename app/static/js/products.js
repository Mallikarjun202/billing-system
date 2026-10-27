// Add / edit / delete products through the JSON API.
(() => {
    const dialog = document.getElementById("product-dialog");
    const form = document.getElementById("product-form");
    const title = document.getElementById("dialog-title");
    const formError = document.getElementById("form-error");
    const pageError = document.getElementById("page-error");
    let editingId = null;

    // Live updates wait while the dialog is open so the form isn't swapped mid-edit.
    const reload = () => (window.Live ? Live.refresh() : location.reload());
    if (window.Live) {
        Live.pauseWhile(() => dialog.open);
        dialog.addEventListener("close", () => Live.resume());
    }

    function showError(el, message) {
        el.textContent = message;
        el.hidden = !message;
    }

    async function errorMessage(response) {
        try {
            const body = await response.json();
            if (typeof body.detail === "string") return body.detail;
            if (Array.isArray(body.detail)) {
                return body.detail
                    .map((e) => `${String(e.loc[e.loc.length - 1]).replaceAll("_", " ")}: ${e.msg}`)
                    .join(" · ");
            }
        } catch {
            /* not JSON */
        }
        return `Request failed (${response.status}).`;
    }

    function open(product) {
        editingId = product ? product.product_id : null;
        form.reset();
        showError(formError, "");
        title.textContent = product ? `Edit ${product.product_id}` : "Add product";
        form.elements.product_id.readOnly = Boolean(product);
        if (product) {
            for (const [key, value] of Object.entries(product)) {
                if (form.elements[key]) form.elements[key].value = value;
            }
        }
        dialog.showModal();
        (product ? form.elements.name : form.elements.product_id).focus();
    }

    document.addEventListener("click", async (event) => {
        const button = event.target.closest("[data-action]");
        if (!button) return;
        const row = button.closest("tr[data-product]");
        const product = row ? JSON.parse(row.dataset.product) : null;

        switch (button.dataset.action) {
            case "new":
                open(null);
                break;
            case "edit":
                open(product);
                break;
            case "cancel":
                dialog.close();
                break;
            case "delete": {
                if (!confirm(`Delete ${product.name} (${product.product_id})? Past bills keep their copy of it.`)) return;
                button.disabled = true;
                const response = await fetch(`/api/products/${encodeURIComponent(product.product_id)}`, { method: "DELETE" });
                if (response.ok) {
                    reload();
                } else {
                    button.disabled = false;
                    showError(pageError, await errorMessage(response));
                }
                break;
            }
        }
    });

    form.addEventListener("submit", async (event) => {
        event.preventDefault();
        if (!form.reportValidity()) return;

        const data = Object.fromEntries(new FormData(form));
        const payload = {
            name: data.name.trim(),
            price: Number(data.price),
            tax_percentage: Number(data.tax_percentage || 0),
            available_stock: Number(data.available_stock),
        };
        if (!editingId) payload.product_id = data.product_id.trim().toUpperCase();

        const submit = form.querySelector('[type="submit"]');
        submit.disabled = true;
        const response = await fetch(
            editingId ? `/api/products/${encodeURIComponent(editingId)}` : "/api/products",
            {
                method: editingId ? "PATCH" : "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload),
            }
        );
        submit.disabled = false;

        if (response.ok) {
            dialog.close();
            reload();
        } else {
            showError(formError, await errorMessage(response));
        }
    });
})();
