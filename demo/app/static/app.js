/* app.js — TravelAI SSE client */
(function () {
  "use strict";

  const form       = document.getElementById("chat-form");
  const messagesEl = document.getElementById("messages");
  const ledgerBody = document.getElementById("ledger-body");
  const footerEl   = document.getElementById("ledger-footer");
  const submitBtn  = form.querySelector("button[type='submit']");

  let billed   = 0;
  let noCharge = 0;

  /* ── Helpers ────────────────────────────────────────── */

  function escapeHtml(str) {
    return String(str)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  }

  function scrollBottom() {
    messagesEl.scrollTop = messagesEl.scrollHeight;
  }

  /* ── DOM builders ───────────────────────────────────── */

  function appendUserMessage(text) {
    const div = document.createElement("div");
    div.className = "message user";
    div.textContent = text;
    messagesEl.appendChild(div);
    scrollBottom();
  }

  function createAgentMessage() {
    const wrap = document.createElement("div");
    wrap.className = "message agent";

    const statusEl = document.createElement("div");
    statusEl.className = "status-indicator";
    wrap.appendChild(statusEl);

    const planEl = document.createElement("div");
    wrap.appendChild(planEl);

    messagesEl.appendChild(wrap);
    scrollBottom();
    return { wrap, statusEl, planEl };
  }

  function setStatus(statusEl, text) {
    statusEl.textContent = text;
    scrollBottom();
  }

  function appendBillingCard(parentEl, outcome, units, reason, evalSnapshot) {
    const card = document.createElement("div");
    card.className = "billing-card";

    const outcomeLabel =
      outcome === "BILL"
        ? "BILL \u00B7 " + units + " unit" + (units !== 1 ? "s" : "")
        : outcome;
    const badgeClass =
      outcome === "BILL" ? "bill" : outcome === "NO_CHARGE" ? "no-charge" : "hold";

    const dq = evalSnapshot && evalSnapshot.deal_quality;
    const evalLine = dq
      ? "deal_quality = " +
        escapeHtml(dq.label || "?") +
        " (" +
        (dq.score !== null && dq.score !== undefined
          ? Number(dq.score).toFixed(2)
          : "\u2014") +
        ")"
      : escapeHtml(reason || "");

    card.innerHTML =
      '<span class="outcome-badge ' +
      badgeClass +
      '">' +
      escapeHtml(outcomeLabel) +
      "</span>" +
      '<div class="eval-line">' +
      evalLine +
      "</div>";

    parentEl.appendChild(card);
    scrollBottom();
  }

  function addLedgerRow(destination, outcome, score, units) {
    const tr = document.createElement("tr");
    const cellClass =
      outcome === "BILL"
        ? "bill-cell"
        : outcome === "NO_CHARGE"
        ? "no-charge-cell"
        : "hold-cell";
    const scoreStr =
      score !== null && score !== undefined
        ? Number(score).toFixed(2)
        : "\u2014";
    tr.innerHTML =
      "<td>" + escapeHtml(destination) + "</td>" +
      '<td class="' + cellClass + '">' + escapeHtml(outcome) + "</td>" +
      "<td>" + scoreStr + "</td>" +
      "<td>" + escapeHtml(String(units)) + "</td>";
    ledgerBody.appendChild(tr);

    if (outcome === "BILL") billed++;
    else if (outcome === "NO_CHARGE") noCharge++;

    footerEl.textContent =
      billed + " billed \u00B7 " + noCharge + " no charge \u00B7 $0 (shadow)";
  }

  /* ── SSE stream consumer ────────────────────────────── */

  async function streamPlan(destination, userId) {
    const { statusEl, planEl } = createAgentMessage();
    let evalSnapshot = null;

    const response = await fetch("/api/plan", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ destination: destination, user_id: userId }),
    });

    if (!response.ok) {
      setStatus(statusEl, "Error: " + response.status);
      submitBtn.disabled = false;
      return;
    }

    const reader  = response.body.getReader();
    const decoder = new TextDecoder();
    let   buffer  = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop(); // keep incomplete last line

      for (const line of lines) {
        if (!line.startsWith("data: ")) continue;
        let evt;
        try {
          evt = JSON.parse(line.slice(6));
        } catch (_) {
          continue;
        }

        if (evt.type === "status") {
          setStatus(statusEl, evt.message);
        } else if (evt.type === "chunk") {
          statusEl.textContent = "";
          planEl.textContent   = evt.text;
          scrollBottom();
        } else if (evt.type === "eval") {
          evalSnapshot = evt.evals;
        } else if (evt.type === "decision") {
          setStatus(statusEl, "");
          const dqScore =
            evalSnapshot && evalSnapshot.deal_quality
              ? evalSnapshot.deal_quality.score
              : null;
          appendBillingCard(
            planEl.parentElement,
            evt.outcome,
            evt.units,
            evt.reason,
            evalSnapshot
          );
          addLedgerRow(destination, evt.outcome, dqScore, evt.units);
        } else if (evt.type === "done") {
          setStatus(statusEl, "");
          submitBtn.disabled = false;
        }
      }
    }

    submitBtn.disabled = false;
  }

  /* ── Page init — restore ledger from server ─────────── */

  async function restoreLedger() {
    try {
      const res  = await fetch("/api/decisions");
      const data = await res.json();
      for (const d of data) {
        const dq    = d.eval_snapshot && d.eval_snapshot.deal_quality;
        const score = dq ? dq.score : null;
        addLedgerRow(
          d.destination || d.customer_id,
          d.decision,
          score,
          d.units || 0
        );
      }
    } catch (_) {
      /* ignore — ledger starts empty */
    }
  }

  /* ── Form handler ───────────────────────────────────── */

  form.addEventListener("submit", function (e) {
    e.preventDefault();
    const destination = document.getElementById("destination").value.trim();
    const userId      = document.getElementById("user-id").value.trim() || "demo-user";
    if (!destination) return;

    submitBtn.disabled = true;
    document.getElementById("destination").value = "";

    appendUserMessage("Plan a trip to " + destination);
    streamPlan(destination, userId).catch(function (err) {
      console.error("streamPlan error:", err);
      submitBtn.disabled = false;
    });
  });

  restoreLedger();
})();
