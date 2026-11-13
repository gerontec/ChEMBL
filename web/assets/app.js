// Query form of the ChEMBL query service: mode switch, suggestions, table sorting, batch jobs.
// Plain JavaScript, no libraries, talks to the API on the same origin.
(function () {
  "use strict";
  const form = document.getElementById("qform");
  const q = document.getElementById("q");
  const help = document.getElementById("qhelp");
  const sugg = document.getElementById("sugg");

  function mode() {
    const m = form.querySelector("input[name=mode]:checked");
    return m ? m.value : "compound";
  }

  // ---------------------------------------------------------------- mode switch
  function applyMode() {
    const m = mode();
    document.querySelectorAll(".opt").forEach(el => { el.hidden = !el.classList.contains("opt-" + m); });
    q.placeholder = q.dataset["ph" + m[0].toUpperCase() + m.slice(1)] || "";
    help.textContent = help.dataset["help" + m[0].toUpperCase() + m.slice(1)] || "";
    sugg.innerHTML = "";
  }
  form.querySelectorAll("input[name=mode]").forEach(r => r.addEventListener("change", applyMode));
  applyMode();

  // ---------------------------------------------------------------- suggestions
  let timer = null, last = "";
  q.addEventListener("input", () => {
    clearTimeout(timer);
    const v = q.value.trim();
    if (v.length < 3 || v === last) return;
    timer = setTimeout(() => suggest(v), 350);
  });

  async function suggest(v) {
    last = v;
    const m = mode();
    const url = m === "disease" ? "indications/terms?limit=15&q=" + encodeURIComponent(v)
      : m === "compound"
      ? "compounds/search?limit=15&q=" + encodeURIComponent(v)
      : "targets/search?limit=15&q=" + encodeURIComponent(v) +
        (document.getElementById("org").value ? "&organism=" + encodeURIComponent(document.getElementById("org").value) : "");
    try {
      const r = await fetch(url, { headers: { Accept: "application/json" } });
      if (!r.ok) return;
      const data = await r.json();
      const rows = m === "disease" ? data.terms : data;
      sugg.innerHTML = "";
      rows.forEach(x => {
        const o = document.createElement("option");
        if (m === "disease") {
          o.value = x.term;
          o.label = x.n_compounds + " / " + x.n_approved;
        } else if (m === "compound") {
          o.value = x.pref_name || x.chembl_id;
          o.label = x.chembl_id;
        } else {
          o.value = x.target_chembl_id;
          o.label = (x.target || "") + " – " + (x.organism || "") + (x.genes ? " (" + x.genes + ")" : "");
        }
        sugg.appendChild(o);
      });
    } catch (e) { /* suggestions are optional */ }
  }

  // ---------------------------------------------------------------- table sorting
  document.querySelectorAll("table.sortable").forEach(table => {
    table.querySelectorAll("thead th").forEach((th, i) => {
      th.classList.add("sort");
      th.addEventListener("click", () => {
        const num = th.hasAttribute("data-num");
        const dir = th.dataset.dir === "asc" ? "desc" : "asc";
        table.querySelectorAll("thead th").forEach(o => delete o.dataset.dir);
        th.dataset.dir = dir;
        const body = table.tBodies[0];
        const rows = Array.from(body.rows);
        const key = r => {
          const c = r.cells[i];
          if (!num) return (c.textContent || "").trim().toLowerCase();
          const v = parseFloat(c.dataset.v);
          return isNaN(v) ? -Infinity : v;
        };
        rows.sort((a, b) => {
          const x = key(a), y = key(b);
          return (x < y ? -1 : x > y ? 1 : 0) * (dir === "asc" ? 1 : -1);
        });
        rows.forEach(r => body.appendChild(r));
      });
    });
  });

  // ---------------------------------------------------------------- batch jobs
  const bform = document.getElementById("batchform");
  const bstat = document.getElementById("batchstatus");
  if (!bform) return;
  const t = k => bform.dataset["t" + k[0].toUpperCase() + k.slice(1)] || k;

  function params() {
    const m = mode(), p = {};
    const lv = Array.from(form.querySelectorAll("input[name='lv[]']:checked")).map(x => x.value).join("");
    if (m !== "disease" && lv) p.levels = lv;
    if (m === "compound" && document.getElementById("human").checked) p.human = true;
    if (m === "target") {
      const org = document.getElementById("org").value.trim();
      if (org) p.organism = org;
      if (document.getElementById("named").checked) p.named = true;
    }
    if (m !== "compound" && document.getElementById("approved").checked) p.min_phase = 4;
    if (m === "disease" && document.getElementById("res").checked) p.with_results = true;
    if (m === "disease" && document.getElementById("bm").checked) p.biomarker = "dna";
    return p;
  }

  function show(cls, html) {
    bstat.innerHTML = '<div class="alert alert-' + cls + ' mb-0">' + html + "</div>";
  }

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  async function poll(id) {
    let s;
    try {
      s = await (await fetch("jobs/" + id, { cache: "no-store" })).json();
    } catch (e) { setTimeout(() => poll(id), 10000); return; }
    const link = location.origin + location.pathname.replace(/[^/]*$/, "") + "jobs/" + id + "/result";
    const keep = esc(t("keep")) + ' <code class="user-select-all">' + esc(link) + "</code>";
    if (s.status === "done") {
      show("success", "<strong>" + esc(t("done")) + "</strong>: " + esc(s.n_rows) + " " + esc(t("rows")) +
           (s.n_errors ? " · " + esc(s.n_errors) + " ✗" : "") +
           '<div class="mt-2"><a class="btn btn-success" href="jobs/' + esc(id) + '/result">' + esc(t("download")) + "</a></div>");
      return;
    }
    if (s.status === "failed") { show("danger", "<strong>" + esc(t("failed")) + "</strong>: " + esc(s.error)); return; }
    const pct = s.n_names ? Math.round(100 * (s.n_done || 0) / s.n_names) : 0;
    show("info", "<strong>" + esc(t(s.status)) + "</strong>" +
         (s.queue_position > 1 ? " (#" + esc(s.queue_position) + ")" : "") +
         " · " + esc(s.n_done || 0) + "/" + esc(s.n_names) + " " + esc(t("names")) + ", " + esc(s.n_rows || 0) + " " + esc(t("rows")) +
         '<div class="progress my-2" role="progressbar"><div class="progress-bar" data-pct="' + pct + '"></div></div>' + keep);
    bstat.querySelectorAll(".progress-bar").forEach(b => { b.style.width = b.dataset.pct + "%"; });
    setTimeout(() => poll(id), 5000);
  }

  bform.addEventListener("submit", async ev => {
    ev.preventDefault();
    const names = document.getElementById("bnames").value.split(/\r?\n/).map(s => s.trim()).filter(Boolean);
    const body = { endpoint: bform.dataset["endpoint" + mode()[0].toUpperCase() + mode().slice(1)],
                   names: names, params: params(), format: "xlsx" };
    const mail = document.getElementById("bmail").value.trim();
    if (mail) body.email = mail;
    try {
      const r = await fetch("jobs", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      const d = await r.json();
      if (!r.ok) { show("warning", esc(typeof d.detail === "string" ? d.detail : JSON.stringify(d.detail))); return; }
      poll(d.job_id);
    } catch (e) { show("danger", esc(e)); }
  });
})();
