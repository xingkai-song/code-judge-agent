/* 前端逻辑：推荐 → 看题 → 提交 → 轮询判题 → 展示诊断 → 看档案。
   没有构建步骤，也不依赖框架；CodeMirror 只是渐进增强，加载不到就退回普通文本框。 */

const $ = (sel) => document.querySelector(sel);

const USER_RE = /^[A-Za-z0-9_-]{1,32}$/;

const state = {
  cm: null,
  current: null,
  recommendations: [],
  timer: null,
};

const STREAM_TEMPLATE = [
  "import sys",
  "",
  "",
  "def main():",
  "    data = sys.stdin.read().split()",
  "    # TODO: 在这里写你的解法",
  "    print(data)",
  "",
  "",
  'if __name__ == "__main__":',
  "    main()",
  "",
].join("\n");

// ---------------------------------------------------------------- 基础工具

function escapeHtml(text) {
  return String(text ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function setStatus(text, isError = false) {
  const el = $("#status");
  el.textContent = text;
  el.style.color = isError ? "var(--bad)" : "";
}

async function api(path, options) {
  const res = await fetch(path, options);
  let data = {};
  try {
    data = await res.json();
  } catch (err) {
    data = {};
  }
  if (!res.ok) throw new Error(data.detail || `请求失败（HTTP ${res.status}）`);
  return data;
}

function currentUser() {
  const value = $("#user").value.trim();
  if (!USER_RE.test(value)) {
    throw new Error("名字只能用字母、数字、下划线、短横线（1–32 个字符）");
  }
  return value;
}

function getCode() {
  return state.cm ? state.cm.getValue() : $("#editor").value;
}

function setCode(text) {
  if (state.cm) state.cm.setValue(text);
  else $("#editor").value = text;
}

// ---------------------------------------------------------------- 初始化

function initEditor() {
  if (window.CodeMirror) {
    state.cm = CodeMirror.fromTextArea($("#editor"), {
      mode: "python",
      lineNumbers: true,
      indentUnit: 4,
      tabSize: 4,
      viewportMargin: Infinity,
    });
  }
}

async function initHealth() {
  try {
    const info = await api("/api/health");
    const flags = [];
    if (!info.sandbox_enforces_memory) flags.push("无内存上限");
    if (!info.sandbox_enforces_network_isolation) flags.push("无网络隔离");
    const suffix = flags.length ? `（${flags.join("、")}）` : "";
    setStatus(`题库 ${info.problems} 道 · 技能 ${info.skills} 项 · 沙箱 ${info.sandbox}${suffix}`);
  } catch (err) {
    setStatus(err.message, true);
  }
}

// ---------------------------------------------------------------- 推荐列表

function renderRecos() {
  const list = $("#reco");
  list.innerHTML = "";
  if (!state.recommendations.length) {
    list.innerHTML = '<li class="muted">暂时没有可推荐的题目</li>';
    return;
  }
  state.recommendations.forEach((rec, index) => {
    const li = document.createElement("li");
    li.dataset.pid = rec.problem_id;
    li.innerHTML = `
      <div class="pid">${index + 1}. ${escapeHtml(rec.problem_id)}</div>
      <div class="target">针对「${escapeHtml(rec.target_label)}」</div>
      <div class="reason">${escapeHtml(rec.reason)}</div>`;
    li.addEventListener("click", () => openProblem(rec.problem_id));
    list.appendChild(li);
  });
}

async function loadRecommendations() {
  try {
    const user = currentUser();
    const data = await api(`/api/recommend?user=${encodeURIComponent(user)}&limit=3`);
    state.recommendations = data.recommendations || [];
    renderRecos();
  } catch (err) {
    setStatus(err.message, true);
  }
}

// ---------------------------------------------------------------- 打开题目

async function openProblem(problemId) {
  try {
    const user = currentUser();
    const problem = await api(
      `/api/problem/${encodeURIComponent(problemId)}?user=${encodeURIComponent(user)}`
    );
    state.current = problem;

    $("#empty").hidden = true;
    $("#problem").hidden = false;
    $("#result").hidden = true;
    $("#p-title").textContent = `${problem.id}　${problem.title}`;
    $("#p-meta").textContent = [
      problem.source,
      problem.difficulty,
      `${problem.case_count} 条用例`,
      `${problem.time_limit_ms}ms / ${problem.memory_limit_mb}MB`,
      problem.attempted ? "做过" : "没做过",
    ]
      .filter(Boolean)
      .join(" · ");
    $("#p-statement").textContent = problem.statement || "（无题面）";

    if (problem.skills && problem.skills.length) {
      const tags = problem.skills.map((s) => s.label).join("、");
      $("#p-meta").textContent += ` · 考察：${tags}`;
    }

    setCode(problem.starter_code || STREAM_TEMPLATE);
    if (state.cm) setTimeout(() => state.cm.refresh(), 0);

    document.querySelectorAll("#reco li").forEach((li) => {
      li.classList.toggle("active", li.dataset.pid === problem.id);
    });
    setStatus("");
  } catch (err) {
    setStatus(err.message, true);
  }
}

// ---------------------------------------------------------------- 提交判题

async function submitCode() {
  if (!state.current) return;
  let user;
  try {
    user = currentUser();
  } catch (err) {
    setStatus(err.message, true);
    return;
  }
  const source = getCode();
  if (!source.trim()) {
    setStatus("先写点代码再提交", true);
    return;
  }

  const button = $("#submit");
  button.disabled = true;
  $("#result").hidden = true;
  $("#submit-hint").textContent = "判题中…";

  try {
    const task = await api("/api/submit", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ user, problem_id: state.current.id, source }),
    });
    pollTask(task.task_id);
  } catch (err) {
    button.disabled = false;
    $("#submit-hint").textContent = "";
    setStatus(err.message, true);
  }
}

function pollTask(taskId, attempt = 0) {
  clearTimeout(state.timer);
  state.timer = setTimeout(async () => {
    try {
      const task = await api(`/api/task/${encodeURIComponent(taskId)}`);
      if (task.status === "done") {
        renderResult(task.result);
        finishSubmit();
        return;
      }
      if (task.status === "error") {
        setStatus(`判题失败：${task.error}`, true);
        finishSubmit();
        return;
      }
      if (attempt > 120) {
        setStatus("判题超时，请重试", true);
        finishSubmit();
        return;
      }
      if (attempt === 3) $("#submit-hint").textContent = "诊断中…（会实跑几十个修复候选）";
      pollTask(taskId, attempt + 1);
    } catch (err) {
      setStatus(err.message, true);
      finishSubmit();
    }
  }, attempt === 0 ? 300 : 600);
}

function finishSubmit() {
  $("#submit").disabled = false;
  $("#submit-hint").textContent = "";
}

function renderResult(result) {
  if (!result) return;
  const box = $("#result");
  const cls = result.accepted ? "ok" : result.verdict === "IE" ? "warn" : "bad";
  const parts = [
    `<div class="verdict ${cls}">${escapeHtml(result.verdict)}</div>`,
    `<div class="passed">通过 ${result.passed}/${result.total} 条用例</div>`,
  ];

  const diagnosis = result.diagnosis;
  if (diagnosis && diagnosis.repair) {
    const r = diagnosis.repair;
    parts.push(
      `<p style="margin:12px 0 0">` +
        `<span class="pill">定位到第 ${r.lineno} 行</span>` +
        `<code>${escapeHtml(r.before)}</code> → <code>${escapeHtml(r.after)}</code></p>`
    );
  }
  if (diagnosis) {
    const meta = [];
    meta.push(`尝试 ${diagnosis.candidates_tried}/${diagnosis.candidates_total} 个修复候选`);
    meta.push(`耗时 ${diagnosis.elapsed_ms} ms`);
    if (diagnosis.alternative_repairs) {
      meta.push(`另有 ${diagnosis.alternative_repairs} 种改法也能通过`);
    }
    parts.push(
      `<p style="margin:10px 0 0">${meta
        .map((m) => `<span class="pill">${escapeHtml(m)}</span>`)
        .join("")}</p>`
    );
  }
  if (result.explanation) {
    parts.push(`<pre class="explain">${escapeHtml(result.explanation)}</pre>`);
  }
  if (result.recommendations && result.recommendations.length) {
    parts.push(
      '<div class="next"><strong>接下来可以练：</strong><ul>' +
        result.recommendations
          .map(
            (r) =>
              `<li>${escapeHtml(r.problem_id)}　针对「${escapeHtml(r.target_label)}」</li>`
          )
          .join("") +
        "</ul></div>"
    );
  }
  box.innerHTML = parts.join("");
  box.hidden = false;

  if (result.accepted) {
    loadRecommendations();
  }
}

// ---------------------------------------------------------------- 档案

function masteryCell(value, uncertainty) {
  const percent = Math.round(value * 100);
  return (
    `<div style="display:flex;align-items:center;gap:8px">` +
    `<div class="bar"><span style="width:${percent}%"></span></div>` +
    `<span>${percent}%</span>` +
    `<span class="tag">±${Math.round(uncertainty * 100)}%</span>` +
    `</div>`
  );
}

async function openProfile() {
  let user;
  try {
    user = currentUser();
  } catch (err) {
    setStatus(err.message, true);
    return;
  }
  try {
    const data = await api(`/api/profile?user=${encodeURIComponent(user)}`);
    $("#profile-title").textContent = `${data.user} 的档案`;

    const rows = data.skills
      .filter((s) => s.opportunities > 0)
      .map(
        (s) => `<tr>
          <td>${escapeHtml(s.label)}</td>
          <td>${masteryCell(s.mastery, s.uncertainty)}</td>
          <td>${s.opportunities}</td>
          <td>${s.errors}</td>
          <td>${escapeHtml(s.level || "")}</td>
        </tr>`
      )
      .join("");

    const unseen = data.skills.filter((s) => s.opportunities === 0).map((s) => s.label);

    const history = (data.history || [])
      .map(
        (h) => `<tr>
          <td>${escapeHtml(h.problem_id)}</td>
          <td class="${h.verdict === "AC" ? "ok" : "bad"}">${escapeHtml(h.verdict)}</td>
          <td>${h.passed}/${h.total}</td>
          <td>${escapeHtml(h.fault_label || "—")}</td>
          <td class="tag">${escapeHtml(h.ts)}</td>
        </tr>`
      )
      .join("");

    $("#profile-body").innerHTML = `
      <p>累计练习 <strong>${data.attempts}</strong> 次，一次通过 <strong>${data.solved}</strong> 次。</p>
      ${
        data.weakest
          ? `<p>当前最需要加强：<strong>${escapeHtml(data.weakest.label)}</strong>
             （掌握度 ${Math.round(data.weakest.mastery * 100)}%，练过 ${data.weakest.opportunities} 次）</p>`
          : ""
      }
      ${
        rows
          ? `<table>
               <thead><tr><th>技能</th><th>掌握度</th><th>练过</th><th>犯错</th><th>评价</th></tr></thead>
               <tbody>${rows}</tbody>
             </table>`
          : `<p class="muted">还没有练过的技能。做几道题之后这里会出现你的画像。</p>`
      }
      ${
        unseen.length
          ? `<p class="tag" style="margin-top:12px">还没练过（${unseen.length} 项）：${escapeHtml(unseen.join("、"))}</p>`
          : ""
      }
      ${
        history
          ? `<h2 style="margin:18px 0 8px">最近作答</h2>
             <table>
               <thead><tr><th>题目</th><th>判定</th><th>用例</th><th>诊断出的错因</th><th>时间</th></tr></thead>
               <tbody>${history}</tbody>
             </table>`
          : ""
      }`;
    $("#profile-dialog").showModal();
  } catch (err) {
    setStatus(err.message, true);
  }
}

// ---------------------------------------------------------------- 事件绑定

function bindEvents() {
  $("#submit").addEventListener("click", submitCode);
  $("#reset").addEventListener("click", () => {
    if (state.current) setCode(state.current.starter_code || STREAM_TEMPLATE);
  });
  $("#refresh").addEventListener("click", loadRecommendations);
  $("#profile-btn").addEventListener("click", openProfile);
  $("#profile-close").addEventListener("click", () => $("#profile-dialog").close());
  $("#user").addEventListener("change", () => {
    localStorage.setItem("cj_user", $("#user").value.trim());
    state.current = null;
    $("#problem").hidden = true;
    $("#empty").hidden = false;
    loadRecommendations();
  });
}

function boot() {
  const saved = localStorage.getItem("cj_user") || "guest";
  $("#user").value = saved;
  initEditor();
  bindEvents();
  initHealth();
  if (USER_RE.test(saved)) loadRecommendations();
  else setStatus("先填一个名字，然后从左边挑题", true);
}

document.addEventListener("DOMContentLoaded", boot);
