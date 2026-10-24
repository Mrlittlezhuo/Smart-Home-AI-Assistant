/* 星火终端前端(阶段零):两种展示模式 + 八种终端状态。
 * 终端只渲染结构化交互状态,不由 LLM 生成页面(§4.4)。 */
"use strict";

const $ = (id) => document.getElementById(id);
const body = document.body;

// ---- 会话(聊天记录默认以当前会话为范围)----
const SESSION = localStorage.getItem("spark-session") ||
  (() => { const s = "s-web-" + Math.random().toString(36).slice(2, 10); localStorage.setItem("spark-session", s); return s; })();

// ---- 终端状态(§4.4 推荐状态)----
const STATE_STYLE = {
  idle:                  { color: "#6ea8fe", speed: 0.4, particles: 14, label: "空闲" },
  listening:             { color: "#35d0ba", speed: 1.2, particles: 20, label: "正在聆听" },
  thinking:              { color: "#b48cff", speed: 2.6, particles: 26, label: "正在思考" },
  speaking:              { color: "#6ea8fe", speed: 1.6, particles: 22, label: "正在播报" },
  executing:             { color: "#f0a050", speed: 2.0, particles: 24, label: "正在执行设备任务" },
  confirmation_required: { color: "#f0a050", speed: 0.8, particles: 16, label: "等待你的确认", ring: true },
  completed:             { color: "#79d39a", speed: 1.0, particles: 18, label: "执行完成" },
  error:                 { color: "#ef7d7d", speed: 0.6, particles: 12, label: "执行失败" },
};
let termState = "idle";
let lastActivity = Date.now();

function setState(s, hint) {
  termState = s;
  body.dataset.state = s;
  body.style.setProperty("--state-color", STATE_STYLE[s].color);
  $("state-label").textContent = STATE_STYLE[s].label;
  if (hint !== undefined) $("spark-hint").textContent = hint;
  lastActivity = Date.now();
}

// ---- 模式切换:默认/无操作 → 星火;确认或失败 → 聊天记录 ----
function setMode(mode) {
  body.dataset.mode = mode;
  $("tab-spark").classList.toggle("active", mode === "spark");
  $("tab-chat").classList.toggle("active", mode === "chat");
  lastActivity = Date.now();
}
$("tab-spark").onclick = () => setMode("spark");
$("tab-chat").onclick = () => setMode("chat");
setInterval(() => {
  if (Date.now() - lastActivity > 12000 && termState === "idle") setMode("spark");
}, 2000);

// ---- 星火动画(canvas 粒子)----
const canvas = $("spark"), ctx = canvas.getContext("2d");
let angle = 0, prev = performance.now();
function drawSpark(now) {
  const dt = (now - prev) / 1000; prev = now;
  const style = STATE_STYLE[termState];
  angle += style.speed * dt;
  const w = canvas.width, h = canvas.height, cx = w / 2, cy = h / 2;
  ctx.clearRect(0, 0, w, h);

  const breathe = 0.75 + 0.25 * Math.sin(now / (termState === "idle" ? 900 : 350));
  const coreR = 26 * breathe;
  const grad = ctx.createRadialGradient(cx, cy, 0, cx, cy, coreR * 3.2);
  grad.addColorStop(0, style.color);
  grad.addColorStop(0.35, style.color + "55");
  grad.addColorStop(1, "transparent");
  ctx.fillStyle = grad;
  ctx.beginPath(); ctx.arc(cx, cy, coreR * 3.2, 0, Math.PI * 2); ctx.fill();

  for (let i = 0; i < style.particles; i++) {
    const orbit = 60 + (i % 4) * 26 + 8 * Math.sin(now / 700 + i);
    const a = angle * (1 + (i % 3) * 0.25) + (i * Math.PI * 2) / style.particles;
    const x = cx + orbit * Math.cos(a), y = cy + orbit * Math.sin(a);
    ctx.fillStyle = style.color + (i % 2 ? "aa" : "66");
    ctx.beginPath(); ctx.arc(x, y, 2.4, 0, Math.PI * 2); ctx.fill();
  }
  if (style.ring) {
    ctx.strokeStyle = style.color; ctx.setLineDash([6, 8]); ctx.lineWidth = 2;
    ctx.beginPath(); ctx.arc(cx, cy, 150, angle, angle + Math.PI * 2); ctx.stroke();
    ctx.setLineDash([]);
  }
  requestAnimationFrame(drawSpark);
}
requestAnimationFrame(drawSpark);

// ---- 聊天记录 ----
function addMsg(role, text, pending) {
  const div = document.createElement("div");
  div.className = "msg " + role + (pending ? " pending" : "");
  div.textContent = text;
  $("messages").appendChild(div);
  $("messages").scrollTop = $("messages").scrollHeight;
  return div;
}
const STATUS_TEXT = { succeeded: "完成", verified: "已核实", scheduled: "已定时", skipped: "跳过", failed: "失败" };
function renderProgress(results) {
  const box = $("progress");
  box.innerHTML = "";
  for (const r of results || []) {
    const chip = document.createElement("span");
    chip.className = "chip " + r.status;
    const who = (r.device_names || []).join("、") || r.type;
    chip.textContent = `${who} · ${STATUS_TEXT[r.status] || r.status}${r.error ? "(" + r.error + ")" : ""}`;
    box.appendChild(chip);
  }
}

async function loadHistory() {
  const r = await fetch(`/v1/sessions/${SESSION}/messages`);
  const d = await r.json();
  for (const m of d.messages) addMsg(m.role, m.content);
}

// ---- 语音播报 ----
let ttsOn = localStorage.getItem("spark-tts") !== "off";
function refreshTtsButton() { $("tts-toggle").textContent = ttsOn ? "🔊 播报" : "🔇 播报"; $("tts-toggle").classList.toggle("on", ttsOn); }
$("tts-toggle").onclick = () => { ttsOn = !ttsOn; localStorage.setItem("spark-tts", ttsOn ? "on" : "off"); refreshTtsButton(); };
refreshTtsButton();

function speak(text) {
  if (!ttsOn || !("speechSynthesis" in window)) return;
  speechSynthesis.cancel();
  const u = new SpeechSynthesisUtterance(text);
  u.lang = "zh-CN";
  u.onstart = () => setState("speaking");
  u.onend = () => setState("idle");
  speechSynthesis.speak(u);
}

// ---- 对话主流程 ----
let pendingConfirm = null;
function showConfirm(prompt, plan) {
  pendingConfirm = plan;
  $("confirm-prompt").textContent = prompt;
  $("confirm-bar").hidden = false;
  setMode("chat"); // 确认 → 聊天记录模式或明确提示
}
function hideConfirm() { pendingConfirm = null; $("confirm-bar").hidden = true; }

function renderReply(d) {
  addMsg("assistant", d.reply);
  renderProgress(d.results);
  const failed = (d.results || []).some((r) => r.status === "failed");
  setState(failed ? "error" : "completed", d.reply);
  speak(d.reply);
  if (!("speechSynthesis" in window) || !ttsOn) setTimeout(() => setState("idle"), 2500);
}

async function postChat(payload) {
  const r = await fetch("/v1/chat", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return r.json();
}

async function send() {
  const text = $("input").value.trim();
  if (!text || pendingConfirm) return;
  $("input").value = "";
  addMsg("user", text);
  setMode("chat");
  setState("thinking", "正在理解你的指令…");
  // 规划阶段显示“思考”;请求超过 1.5s 说明进入设备执行,切换“执行中”
  const execTimer = setTimeout(() => { if (termState === "thinking") setState("executing", "正在执行设备任务…"); }, 1500);
  try {
    const d = await postChat({ session_id: SESSION, message: text });
    clearTimeout(execTimer);
    if (d.awaiting_confirmation) {
      addMsg("assistant", d.reply, true);
      setState("confirmation_required", d.reply);
      showConfirm(d.reply, { plan_id: d.plan_id, plan_hash: d.plan_hash });
    } else {
      renderReply(d);
    }
  } catch (e) {
    clearTimeout(execTimer);
    addMsg("assistant", "网络错误:" + e, true);
    setState("error", "无法连接服务");
  }
}
$("send").onclick = send;
$("input").addEventListener("keydown", (e) => { if (e.key === "Enter") send(); });

async function resolveConfirm(confirmed) {
  if (!pendingConfirm) return;
  const { plan_id, plan_hash } = pendingConfirm;
  hideConfirm();
  setState("thinking");
  const r = await fetch(`/v1/plans/${plan_id}/confirm`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ plan_hash, confirmed }),
  });
  const d = await r.json();
  addMsg("user", confirmed ? "确认执行" : "取消");
  renderReply(d);
}
$("confirm-yes").onclick = () => resolveConfirm(true);
$("confirm-no").onclick = () => resolveConfirm(false);

// ---- 麦克风语音输入 ----
const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
const micBtn = $("mic");
if (!SR) {
  micBtn.disabled = true;
  micBtn.title = "当前浏览器不支持语音输入(建议使用 Chrome/Edge)";
} else {
  const rec = new SR();
  rec.lang = "zh-CN";
  rec.continuous = false;
  rec.interimResults = false;
  let recording = false;
  rec.onstart = () => { recording = true; micBtn.classList.add("listening"); setMode("spark"); setState("listening", "请说…"); };
  rec.onresult = (e) => { $("input").value = e.results[0][0].transcript; };
  rec.onend = () => {
    recording = false; micBtn.classList.remove("listening");
    if ($("input").value.trim()) send();
    else if (termState === "listening") setState("idle");
  };
  rec.onerror = () => { recording = false; micBtn.classList.remove("listening"); setState("error", "语音识别失败"); };
  micBtn.onclick = () => { if (recording) rec.stop(); else rec.start(); };
}

loadHistory();
