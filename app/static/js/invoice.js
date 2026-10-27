// Invoice page: keep the email badge current without reloading.
// Polls while the email is being sent (and listens for live events, which
// usually arrive first), then stops. Failed or sent invoices can be resent.
(() => {
    const box = document.getElementById("email-status");
    if (!box) return;

    const id = Number(box.dataset.purchase);
    const badgeSlot = box.querySelector("[data-email-badge]");
    const errorEl = box.querySelector("[data-email-error]");
    const resend = box.querySelector("[data-email-resend]");
    const POLL_MS = 1500;
    const MAX_POLLS = 80; // about two minutes; SMTP itself times out after 20 s

    let pollTimer = null;
    let polls = 0;

    function render(status, error) {
        const changed = box.dataset.status !== status;
        box.dataset.status = status;
        badgeSlot.replaceChildren(window.Live ? Live.emailBadge(status) : document.createTextNode(status));
        if (changed) {
            badgeSlot.classList.remove("bump");
            void badgeSlot.offsetWidth;
            badgeSlot.classList.add("bump");
        }
        errorEl.textContent = error || "";
        errorEl.hidden = !error;
        resend.hidden = status === "pending";
        resend.disabled = false;
        resend.querySelector("span").textContent = status === "failed" ? "Try again" : "Resend";
        if (status === "pending") startPolling();
        else stopPolling();
    }

    function stopPolling() {
        clearTimeout(pollTimer);
        pollTimer = null;
    }

    function startPolling() {
        if (pollTimer) return;
        polls = 0;
        pollTimer = setTimeout(poll, POLL_MS);
    }

    async function poll() {
        pollTimer = null;
        try {
            const response = await fetch(`/api/purchases/${id}/email`, { cache: "no-store" });
            if (response.ok) {
                const data = await response.json();
                if (data.email_status !== "pending") return render(data.email_status, data.email_error);
            }
        } catch { /* network blip; try again */ }
        if (++polls < MAX_POLLS) {
            pollTimer = setTimeout(poll, POLL_MS);
        } else {
            errorEl.textContent = "Still sending. This is taking longer than usual.";
            errorEl.hidden = false;
        }
    }

    resend.addEventListener("click", async () => {
        resend.disabled = true;
        try {
            const response = await fetch(`/api/purchases/${id}/email`, { method: "POST" });
            if (response.ok || response.status === 409) {
                const data = response.ok ? await response.json() : { email_status: "pending" };
                render(data.email_status, data.email_error);
                return;
            }
            render(box.dataset.status, `Couldn't resend (error ${response.status}).`);
        } catch {
            render(box.dataset.status, "Couldn't reach the server. Check your connection.");
        }
    });

    if (window.Live) {
        Live.on("email", (data) => { if (data.id === id) render(data.status, data.error); });
        Live.on("resync", () => { if (box.dataset.status === "pending") poll(); });
    }

    if (box.dataset.status === "pending") startPolling();
})();
