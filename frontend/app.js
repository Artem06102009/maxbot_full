/**
 * ЛИГА ПОМОЩНИКОВ — WebApp Frontend
 * Features: Dark/Light Mode, Multi-Class Switcher, Podium Leaderboard (Top 3), Reward Limits, Confetti
 * No emojis, Material Symbols icons
 */

let currentUserId = 1001;
let currentUserData = null;
let activeClassChatId = null;
let activeClassTitle = "Класс";
let userClasses = [];
let gigachatHistory = [];

document.addEventListener("DOMContentLoaded", () => {
  initTheme();
  initUserFromMAX();
  loadInitialData();
});

/* ============================================================
   THEME (DARK / LIGHT MODE)
   ============================================================ */

function initTheme() {
  const savedTheme = localStorage.getItem("liga_theme");
  let theme = "dark";
  if (savedTheme) {
    theme = savedTheme;
  } else if (window.WebApp?.colorScheme) {
    theme = window.WebApp.colorScheme;
  } else if (window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches) {
    theme = "light";
  }
  applyTheme(theme);
}

function applyTheme(theme) {
  document.documentElement.setAttribute("data-theme", theme);
  localStorage.setItem("liga_theme", theme);
  const icon = document.getElementById("theme-icon");
  if (icon) {
    icon.textContent = theme === "dark" ? "light_mode" : "dark_mode";
  }
}

function toggleTheme() {
  const current = document.documentElement.getAttribute("data-theme") || "dark";
  const next = current === "dark" ? "light" : "dark";
  applyTheme(next);
}

/* ============================================================
   USER & APP INITIALIZATION
   ============================================================ */

function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function initials(name) {
  const parts = String(name || "?").trim().split(/\s+/);
  return ((parts[0]?.[0] || "?") + (parts[1]?.[0] || "")).toUpperCase();
}

function sanitizeIcon(icon) {
  const map = {
    "assignment": "assignment",
    "chair": "chair",
    "task_alt": "task_alt",
    "lightbulb": "lightbulb",
    "local_cafe": "local_cafe",
    "star": "star",
    "📝": "assignment",
    "🪑": "chair",
    "🎯": "task_alt",
    "💡": "lightbulb",
    "☕️": "local_cafe",
    "☕": "local_cafe",
    "⭐": "star",
    "⭐️": "star",
    "🎁": "card_giftcard"
  };
  return map[icon] || "card_giftcard";
}

function initUserFromMAX() {
  const urlParams = new URLSearchParams(window.location.search);
  const urlUserId = urlParams.get("user_id");
  if (window.WebApp?.initDataUnsafe?.user) {
    currentUserId = window.WebApp.initDataUnsafe.user.id;
    const demoBar = document.getElementById("demo-bar");
    if (demoBar) demoBar.style.display = "none";
  } else if (urlUserId) {
    currentUserId = parseInt(urlUserId, 10);
    const demoSelect = document.getElementById("demo-user-select");
    if (demoSelect) demoSelect.value = String(currentUserId);
  }
}

async function loadInitialData() {
  await loadUserClasses();
  await loadProfile();
  loadLeaderboard();
}

function switchDemoUser(newUserId) {
  currentUserId = parseInt(newUserId, 10);
  showToast("Переключён пользователь");
  loadUserClasses().then(() => {
    loadProfile();
    const active = document.querySelector(".tab-pane.active");
    if (active?.id === "tab-leaderboard") loadLeaderboard();
    if (active?.id === "tab-rewards") loadRewards();
    if (active?.id === "tab-achievements") loadAchievements();
    if (active?.id === "tab-teacher") loadTeacherData();
    if (active?.id === "tab-gigachat") {
      const msgs = document.getElementById("gigachat-messages");
      if (msgs) msgs.scrollTop = msgs.scrollHeight;
    }
  });
}

/* ============================================================
   MULTI-CLASS SWITCHER (ВЫБОР РАЗНЫХ ЧАТОВ)
   ============================================================ */

async function loadUserClasses() {
  try {
    const res = await fetch(`/api/user/classes?user_id=${currentUserId}`);
    if (res.ok) {
      const data = await res.json();
      userClasses = data.classes || [];
      if (userClasses.length > 0) {
        if (!activeClassChatId || !userClasses.find(c => c.chat_id === activeClassChatId)) {
          const activeClass = userClasses.find(c => c.chat_id === data.active_class_chat_id) || userClasses[0];
          activeClassChatId = activeClass.chat_id;
          activeClassTitle = activeClass.title || "Чат класса";
        }
      }
    }
  } catch (err) {
    console.error("Classes load failed", err);
  }
  updateClassTitleDisplay();
}

function updateClassTitleDisplay() {
  const titleEl = document.getElementById("active-class-title");
  if (titleEl) {
    titleEl.textContent = activeClassTitle || "Класс не выбран";
  }
}

function openClassPicker() {
  const container = document.getElementById("class-list-container");
  if (!container) return;

  if (!userClasses.length) {
    container.innerHTML = '<p class="empty-hint">Вы пока не состоите в учебных чатах бота.</p>';
  } else {
    container.innerHTML = userClasses.map(c => `
      <div class="class-item ${c.chat_id === activeClassChatId ? "active" : ""}" onclick="selectClass('${c.chat_id}', '${escapeHtml(c.title)}')">
        <div>
          <div class="class-item-title">${escapeHtml(c.title)}</div>
          <div class="class-item-sub">ID: ${escapeHtml(c.chat_id)}</div>
        </div>
        ${c.chat_id === activeClassChatId ? '<span class="material-symbols-outlined" style="color: var(--sf-purple);">check</span>' : ""}
      </div>
    `).join("");
  }

  const modal = document.getElementById("class-modal");
  if (modal) modal.classList.remove("hidden");
}

function closeClassPicker(e) {
  if (e && e.target && e.target.closest(".modal-card") && !e.target.classList.contains("btn-close")) {
    return;
  }
  const modal = document.getElementById("class-modal");
  if (modal) modal.classList.add("hidden");
}

function selectClass(chatId, title) {
  activeClassChatId = chatId;
  activeClassTitle = title;
  updateClassTitleDisplay();
  closeClassPicker();
  showToast(`Выбран класс: ${title}`);
  
  loadProfile();
  const active = document.querySelector(".tab-pane.active");
  if (active?.id === "tab-leaderboard") loadLeaderboard();
  if (active?.id === "tab-rewards") loadRewards();
  if (active?.id === "tab-achievements") loadAchievements();
  if (active?.id === "tab-teacher") loadTeacherData();
  if (active?.id === "tab-gigachat") {
    const msgs = document.getElementById("gigachat-messages");
    if (msgs) msgs.scrollTop = msgs.scrollHeight;
  }
}

/* ============================================================
   NAVIGATION & TABS
   ============================================================ */

function switchTab(tabName, btnElement) {
  document.querySelectorAll(".tab-pane").forEach((pane) => pane.classList.remove("active"));
  document.getElementById(`tab-${tabName}`)?.classList.add("active");

  document.querySelectorAll(".rail-btn, .nav-item").forEach((item) => {
    item.classList.toggle("active", item.dataset.tab === tabName);
  });
  if (btnElement) btnElement.classList.add("active");

  window.scrollTo({ top: 0, behavior: "smooth" });

  if (tabName === "leaderboard") loadLeaderboard();
  if (tabName === "rewards") loadRewards();
  if (tabName === "gigachat") {
    setTimeout(() => {
      const msgs = document.getElementById("gigachat-messages");
      if (msgs) msgs.scrollTop = msgs.scrollHeight;
      document.getElementById("gigachat-input")?.focus();
    }, 100);
  }
  if (tabName === "achievements") loadAchievements();
  if (tabName === "profile") loadProfile();
  if (tabName === "teacher") loadTeacherData();
}

/* ============================================================
   PROFILE
   ============================================================ */

async function loadProfile() {
  try {
    const url = `/api/profile?user_id=${currentUserId}${activeClassChatId ? `&class_id=${activeClassChatId}` : ""}`;
    const res = await fetch(url);
    if (!res.ok) throw new Error("profile");
    const data = await res.json();
    currentUserData = data;
    const isTeacher = data.role === "teacher";

    // Header
    document.getElementById("header-name").textContent = data.name;
    document.getElementById("header-role").textContent = isTeacher ? "Учитель" : "Ученик";
    document.getElementById("header-coins").textContent = data.coins;
    document.getElementById("header-avatar").textContent = initials(data.name);

    // Profile Tab
    document.getElementById("profile-avatar").textContent = initials(data.name);
    document.getElementById("profile-name").textContent = data.name;
    document.getElementById("profile-level-title").textContent = data.level.title;
    document.getElementById("profile-level-num").textContent = `${data.level.level} ур.`;
    document.getElementById("profile-xp-text").textContent = `${data.level.current_xp} / ${data.level.next_xp} XP`;
    document.getElementById("profile-xp-fill").style.width = `${data.level.progress_percent}%`;
    document.getElementById("profile-helped").textContent = data.helped_count;
    document.getElementById("profile-questions").textContent = data.questions_count;

    const streakEl = document.getElementById("profile-streak");
    if (streakEl) streakEl.textContent = `${data.help_streak || 0} дн.`;

    document.getElementById("profile-rank").textContent = `#${data.rank}`;

    // Exam / Night banners
    const examBanner = document.getElementById("exam-banner");
    if (examBanner) {
      examBanner.classList.toggle("hidden", !data.exam_mode);
      examBanner.textContent = data.exam_label || "";
    }

    const night = document.getElementById("night-hint");
    const pending = data.pending_xp || data.pending_coins;
    if (night) {
      night.classList.toggle("hidden", !(data.night_mode || pending));
      const nightText = night.querySelector("span:last-child");
      if (nightText) {
        if (pending) {
          nightText.textContent = `Отложено до утра: ${data.pending_xp} XP и ${data.pending_coins} спасибок`;
        } else if (data.night_mode) {
          nightText.textContent = "Тихий час: опыт за ночную помощь начислится утром в 07:00.";
        }
      }
    }

    // Shop
    const shopCoins = document.getElementById("shop-coins");
    if (shopCoins) shopCoins.textContent = data.coins;

    // Parent link
    const parentLink = document.getElementById("parent-link");
    if (parentLink) parentLink.value = data.report_url || "";

    // Teacher nav visibility
    const railTeacher = document.getElementById("nav-teacher-btn");
    if (railTeacher) railTeacher.style.display = isTeacher ? "flex" : "none";
    const mobileTeacher = document.getElementById("nav-teacher-btn-mobile");
    if (mobileTeacher) mobileTeacher.style.display = isTeacher ? "flex" : "none";

  } catch (err) {
    console.error(err);
    showToast("Не удалось загрузить профиль");
  }
}

/* ============================================================
   LEADERBOARD (ПЬЕДЕСТАЛ ТОП-3 + СТОЛБИК НИЖЕ)
   ============================================================ */

async function loadLeaderboard() {
  const container = document.getElementById("leaderboard-container");
  const podiumEl = document.getElementById("podium-container");
  if (!container) return;
  container.innerHTML = '<div class="loading-spinner">Загрузка рейтинга…</div>';

  try {
    const url = `/api/leaderboard?user_id=${currentUserId}${activeClassChatId ? `&class_id=${activeClassChatId}` : ""}`;
    const leaders = await (await fetch(url)).json();
    if (!leaders || !leaders.length) {
      container.innerHTML = '<p class="empty-hint">Пока нет участников в рейтинге.</p>';
      if (podiumEl) podiumEl.innerHTML = "";
      return;
    }

    // Current user rank banner update
    const cur = leaders.find(l => String(l.max_user_id) === String(currentUserId));
    if (cur) {
      document.getElementById("my-rank-title").textContent = `Ваша позиция: #${cur.rank} · ${cur.name}`;
      document.getElementById("my-rank-sub").textContent = `${cur.level_title} · помог ${cur.helped_count} раз · серия ${cur.help_streak || 0} дн.`;
      document.getElementById("my-rank-xp").textContent = `${cur.xp} XP`;
    }

    // 1. RENDER PODIUM (ТОЛЬКО ТОП-3 МЕСТА)
    const top3 = leaders.slice(0, 3);
    if (podiumEl) {
      const p1 = top3[0];
      const p2 = top3[1];
      const p3 = top3[2];

      const col2 = p2 ? `
        <div class="podium-col rank-2">
          <span class="avatar">${escapeHtml(initials(p2.name))}</span>
          <div class="podium-name">${escapeHtml(p2.name)}</div>
          <div class="podium-xp">${p2.xp} XP</div>
          <div class="podium-pedestal">
            <span class="podium-step-num">2</span>
          </div>
        </div>
      ` : '<div class="podium-col"></div>';

      const col1 = p1 ? `
        <div class="podium-col rank-1">
          <span class="avatar">${escapeHtml(initials(p1.name))}</span>
          <div class="podium-name">${escapeHtml(p1.name)}</div>
          <div class="podium-xp">${p1.xp} XP</div>
          <div class="podium-pedestal">
            <span class="material-symbols-outlined podium-badge-icon" style="color: var(--sf-gold);">workspace_premium</span>
            <span class="podium-step-num">1</span>
          </div>
        </div>
      ` : '<div class="podium-col"></div>';

      const col3 = p3 ? `
        <div class="podium-col rank-3">
          <span class="avatar">${escapeHtml(initials(p3.name))}</span>
          <div class="podium-name">${escapeHtml(p3.name)}</div>
          <div class="podium-xp">${p3.xp} XP</div>
          <div class="podium-pedestal">
            <span class="podium-step-num">3</span>
          </div>
        </div>
      ` : '<div class="podium-col"></div>';

      podiumEl.innerHTML = `${col2}${col1}${col3}`;
    }

    // 2. RENDER REMAINING PLACES (МЕСТА 4 И ДАЛЕЕ В СТОЛБИК)
    const rest = leaders.slice(3);
    if (!rest.length) {
      container.innerHTML = '<p class="empty-hint">Первые три места заняли пьедестал выше.</p>';
    } else {
      container.innerHTML = rest.map((item) => {
        const isCurrent = String(item.max_user_id) === String(currentUserId);
        return `
          <div class="leader-item ${isCurrent ? "current-user" : ""}">
            <div class="leader-rank">${item.rank}</div>
            <span class="avatar">${escapeHtml(initials(item.name))}</span>
            <div class="leader-info">
              <div class="leader-name">${escapeHtml(item.name)}${isCurrent ? " (Вы)" : ""}</div>
              <div class="leader-sub">${escapeHtml(item.level_title)} · помог ${item.helped_count} · серия ${item.help_streak || 0} дн.</div>
            </div>
            <div class="leader-xp">${item.xp} XP</div>
          </div>
        `;
      }).join("");
    }

  } catch (err) {
    container.innerHTML = '<p class="empty-hint">Ошибка загрузки рейтинга.</p>';
  }
}

async function loadAchievements() {
  const container = document.getElementById("achievements-container");
  if (!container) return;
  container.innerHTML = '<div class="loading-spinner">Загрузка достижений…</div>';
  try {
    const achievements = await (await fetch(`/api/achievements?user_id=${currentUserId}`)).json();
    container.innerHTML = achievements.map((ach) => `
      <div class="ach-card ${ach.is_unlocked ? "unlocked" : "locked"}">
        <span class="material-symbols-outlined ach-icon">military_tech</span>
        <div class="ach-title">${escapeHtml(ach.title)}</div>
        <div class="ach-desc">${escapeHtml(ach.description)}</div>
        <div class="ach-reward-tag">+${ach.xp_reward} XP · +${ach.coins_reward} спасибок</div>
        <div class="muted" style="margin-top: 6px; font-size: 11px;">
          ${ach.is_unlocked ? "Открыто " + escapeHtml(ach.unlocked_at) : "Закрыто"}
        </div>
      </div>
    `).join("");
  } catch (err) {
    container.innerHTML = '<p class="empty-hint">Ошибка загрузки достижений.</p>';
  }
}

/* ============================================================
   REWARDS SHOP (С ЛИМИТАМИ И КОНФЕТТИ)
   ============================================================ */

async function loadRewards() {
  const grid = document.getElementById("rewards-container");
  const requestsList = document.getElementById("my-requests-list");
  if (!grid) return;
  grid.innerHTML = '<div class="loading-spinner">Загрузка лавки…</div>';

  try {
    const url = `/api/rewards?user_id=${currentUserId}${activeClassChatId ? `&class_id=${activeClassChatId}` : ""}`;
    const data = await (await fetch(url)).json();
    document.getElementById("shop-coins").textContent = data.user_coins;

    const lockEl = document.getElementById("shop-lock");
    if (lockEl) {
      lockEl.classList.toggle("hidden", !data.shop_locked);
      lockEl.textContent = data.shop_message || "";
    }

    if (!data.rewards || !data.rewards.length) {
      grid.innerHTML = '<p class="empty-hint">Учитель пока не добавил награды в лавку этого класса.</p>';
    } else {
      grid.innerHTML = data.rewards.map((rew) => {
        const isLimitReached = !!rew.is_limit_reached;
        const isOutOfStock = rew.stock !== null && rew.stock !== undefined && rew.stock <= 0;
        const canAfford = !data.shop_locked && data.user_coins >= rew.price_coins;
        const diff = rew.price_coins - data.user_coins;

        let btnDisabled = false;
        let btnText = `Купить за ${rew.price_coins} спасибок`;

        if (data.shop_locked) {
          btnDisabled = true;
          btnText = "Лавка закрыта";
        } else if (isLimitReached) {
          btnDisabled = true;
          btnText = "Лимит исчерпан";
        } else if (isOutOfStock) {
          btnDisabled = true;
          btnText = "Закончились";
        } else if (!canAfford) {
          btnDisabled = true;
          btnText = `Нужно ещё ${diff} спасибок`;
        }

        const limitBadge = rew.max_per_student 
          ? `<span class="limit-badge ${isLimitReached ? "limit-reached" : ""}">Лимит: ${rew.user_claims_count || 0}/${rew.max_per_student} на ученика</span>`
          : "";
        const stockBadge = (rew.stock !== null && rew.stock !== undefined)
          ? `<span class="limit-badge ${isOutOfStock ? "limit-reached" : ""}">Осталось: ${rew.stock} шт.</span>`
          : "";

        const iconSymbol = sanitizeIcon(rew.icon);

        return `
          <div class="reward-card">
            <div style="display: flex; gap: 12px; align-items: flex-start;">
              <div class="reward-icon-box">
                <span class="material-symbols-outlined">${iconSymbol}</span>
              </div>
              <div class="reward-details">
                <div style="font-weight: 700; font-size: 15px;">${escapeHtml(rew.title)}</div>
                <div class="reward-desc">${escapeHtml(rew.description)}</div>
                <div class="reward-limits-row">
                  ${limitBadge}
                  ${stockBadge}
                </div>
              </div>
            </div>
            <button class="btn btn-gold" ${btnDisabled ? "disabled" : ""} onclick="handleBuyReward(${rew.id})" style="margin-top: 10px; width: 100%;">
              ${btnText}
            </button>
          </div>
        `;
      }).join("");
    }

    // My Requests list
    if (requestsList) {
      if (!data.my_requests || !data.my_requests.length) {
        requestsList.innerHTML = '<p class="empty-hint">Пока нет запросов на награды.</p>';
      } else {
        const labels = {
          pending: ["На рассмотрении", "status-pending"],
          approved: ["Одобрено", "status-approved"],
          rejected: ["Отклонено", "status-rejected"],
        };
        requestsList.innerHTML = data.my_requests.map((req) => {
          const [text, cls] = labels[req.status] || labels.pending;
          return `
            <div class="req-item">
              <div>
                <strong>${escapeHtml(req.reward_title)}</strong>
                <div class="muted" style="font-size: 11px;">${escapeHtml(req.created_at)}</div>
              </div>
              <span class="status-badge ${cls}">${text}</span>
            </div>
          `;
        }).join("");
      }
    }
  } catch (err) {
    grid.innerHTML = '<p class="empty-hint">Ошибка загрузки наград.</p>';
  }
}

async function handleBuyReward(rewardId) {
  try {
    const res = await fetch("/api/rewards/buy", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-User-Id": String(currentUserId) },
      body: JSON.stringify({ user_id: currentUserId, reward_id: rewardId }),
    });
    const result = await res.json();
    if (!res.ok) {
      showToast(result.detail || result.error || "Не удалось запросить награду");
      return;
    }
    showToast(result.message || "Запрос отправлен учителю");
    launchConfetti();
    loadProfile();
    loadRewards();
  } catch (err) {
    showToast("Ошибка при покупке награды");
  }
}

/* ============================================================
   GIGACHAT AI TUTOR (ДИАЛОГ С ГИГАЧАТОМ)
   ============================================================ */

async function handleSendGigaChat(event) {
  if (event) event.preventDefault();
  const inputEl = document.getElementById("gigachat-input");
  const sendBtn = document.getElementById("gigachat-send-btn");
  const text = (inputEl?.value || "").trim();
  if (!text) return;

  const msgsBox = document.getElementById("gigachat-messages");
  if (!msgsBox) return;

  // 1. Append user message
  const userDiv = document.createElement("div");
  userDiv.className = "giga-msg user";
  userDiv.innerHTML = `
    <div class="giga-msg-avatar">
      <span class="material-symbols-outlined">person</span>
    </div>
    <div class="giga-msg-bubble">${escapeHtml(text)}</div>
  `;
  msgsBox.appendChild(userDiv);
  inputEl.value = "";
  if (sendBtn) sendBtn.disabled = true;

  // 2. Typing indicator
  const typingId = "giga-typing-" + Date.now();
  const typingDiv = document.createElement("div");
  typingDiv.className = "giga-msg assistant";
  typingDiv.id = typingId;
  typingDiv.innerHTML = `
    <div class="giga-msg-avatar">
      <span class="material-symbols-outlined">smart_toy</span>
    </div>
    <div class="giga-msg-bubble">
      <div class="giga-typing"><span></span><span></span><span></span></div>
    </div>
  `;
  msgsBox.appendChild(typingDiv);
  msgsBox.scrollTop = msgsBox.scrollHeight;

  try {
    const res = await fetch("/api/gigachat/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-User-Id": String(currentUserId) },
      body: JSON.stringify({
        user_id: currentUserId,
        message: text,
        history: gigachatHistory,
        class_id: activeClassChatId,
      }),
    });
    const data = await res.json();
    document.getElementById(typingId)?.remove();

    const reply = data.reply || "Не удалось получить ответ от ГигаЧата. Пожалуйста, попробуйте позже.";

    // 3. Append assistant message
    const replyDiv = document.createElement("div");
    replyDiv.className = "giga-msg assistant";
    replyDiv.innerHTML = `
      <div class="giga-msg-avatar">
        <span class="material-symbols-outlined">smart_toy</span>
      </div>
      <div class="giga-msg-bubble">${escapeHtml(reply)}</div>
    `;
    msgsBox.appendChild(replyDiv);

    // Update history
    gigachatHistory.push({ role: "user", content: text });
    gigachatHistory.push({ role: "assistant", content: reply });
    if (gigachatHistory.length > 12) gigachatHistory = gigachatHistory.slice(-12);

  } catch (err) {
    document.getElementById(typingId)?.remove();
    const errDiv = document.createElement("div");
    errDiv.className = "giga-msg assistant";
    errDiv.innerHTML = `
      <div class="giga-msg-avatar">
        <span class="material-symbols-outlined">smart_toy</span>
      </div>
      <div class="giga-msg-bubble">Сервис временно недоступен. Проверьте соединение с интернетом или повторите позже.</div>
    `;
    msgsBox.appendChild(errDiv);
  } finally {
    if (sendBtn) sendBtn.disabled = false;
    msgsBox.scrollTop = msgsBox.scrollHeight;
    inputEl?.focus();
  }
}

function sendQuickPrompt(promptText) {
  const inputEl = document.getElementById("gigachat-input");
  if (!inputEl) return;
  inputEl.value = promptText;
  handleSendGigaChat();
}

function clearGigaChatHistory() {
  gigachatHistory = [];
  const msgsBox = document.getElementById("gigachat-messages");
  if (msgsBox) {
    msgsBox.innerHTML = `
      <div class="giga-msg assistant">
        <div class="giga-msg-avatar">
          <span class="material-symbols-outlined">smart_toy</span>
        </div>
        <div class="giga-msg-bubble">Здравствуйте! Я ГигаЧат — ваш помощник в учебе. Напишите условие задачи, формулу или тему, и я помогу разобраться шаг за шагом.</div>
      </div>
    `;
  }
  showToast("Диалог с ГигаЧатом начат заново");
}

function copyParentLink() {
  const input = document.getElementById("parent-link");
  if (!input?.value) return;
  navigator.clipboard.writeText(input.value);
  showToast("Ссылка для родителей скопирована");
}

/* ============================================================
   TEACHER CABINET
   ============================================================ */

async function loadTeacherData() {
  const reqList = document.getElementById("teacher-requests-list");
  if (!reqList) return;
  reqList.innerHTML = '<div class="loading-spinner">Загрузка заявок…</div>';

  try {
    const requests = await (await fetch(`/api/teacher/requests?teacher_id=${currentUserId}`)).json();
    if (!Array.isArray(requests)) {
      reqList.innerHTML = '<p class="empty-hint">Нет доступа к учительской.</p>';
      return;
    }
    if (!requests.length) {
      reqList.innerHTML = '<p class="empty-hint">Нет ожидающих заявок на награды.</p>';
    } else {
      reqList.innerHTML = requests.map((req) => `
        <div class="teacher-req-item">
          <div class="teacher-req-header">
            <span>${escapeHtml(req.student_name)}</span>
            <span style="color: var(--sf-gold); font-weight: 700;">${req.price_coins} спасибок</span>
          </div>
          <div style="margin: 4px 0;"><strong>${escapeHtml(req.reward_title)}</strong></div>
          <div class="muted" style="font-size: 11px; margin-bottom: 8px;">${escapeHtml(req.created_at)}</div>
          <div class="teacher-req-actions">
            <button class="btn btn-green flex-1" onclick="handleReviewRequest(${req.id}, 'approve')">
              <span class="material-symbols-outlined">check</span> Одобрить
            </button>
            <button class="btn btn-red flex-1" onclick="handleReviewRequest(${req.id}, 'reject')">
              <span class="material-symbols-outlined">close</span> Отклонить
            </button>
          </div>
        </div>
      `).join("");
    }

    // Unanswered questions
    const hang = document.getElementById("teacher-unanswered-list");
    if (hang) {
      const items = await (await fetch(`/api/teacher/unanswered?teacher_id=${currentUserId}`)).json();
      if (!Array.isArray(items) || !items.length) {
        hang.innerHTML = '<p class="empty-hint">Все вопросы разобраны!</p>';
      } else {
        hang.innerHTML = items.map((q) => `
          <div class="teacher-req-item">
            <div class="teacher-req-header">
              <span>${escapeHtml(q.subject)}</span>
              <span class="muted" style="font-size: 11px;">${escapeHtml(q.created_at)}</span>
            </div>
            <div style="font-weight: 600; margin: 4px 0;">${escapeHtml(q.text)}</div>
            <div class="muted" style="font-size: 11px; margin-bottom: 8px;">Автор: ${escapeHtml(q.author_name)}</div>
            <div class="teacher-req-actions">
              <button class="btn btn-primary flex-1" onclick="handleExemplar(${q.id})">
                <span class="material-symbols-outlined">star</span> В шпаргалку
              </button>
            </div>
          </div>
        `).join("");
      }
    }

    // Exam status
    const cls = await (await fetch(`/api/teacher/class?teacher_id=${currentUserId}`)).json();
    const examStatus = document.getElementById("exam-status");
    if (examStatus) examStatus.textContent = cls.exam_label || "Контрольной нет. При включении лавка закрывается на день.";

    const examToggles = document.getElementById("exam-subjects");
    if (examToggles && cls.subjects) {
      const examAll = (cls.exam_subjects || []).includes("*");
      examToggles.innerHTML = cls.subjects.map((name) => `
        <label class="toggle">
          <input type="checkbox" value="${escapeHtml(name)}" ${examAll || (cls.exam_subjects || []).includes(name) ? "checked" : ""}>
          ${escapeHtml(name)}
        </label>
      `).join("");
    }

    // Weekly export preview
    const preview = document.getElementById("week-export-preview");
    if (preview) {
      const week = await (await fetch(`/api/teacher/export?teacher_id=${currentUserId}`)).json();
      const topics = (week.subjects || []).map(([name, n]) => `<b>${name}</b>: ${n}`).join(" · ") || "нет вопросов";
      preview.innerHTML = `За последние 7 дней: <b>${week.questions || 0}</b> вопросов задано, <b>${week.answered || 0}</b> решено.<br>Темы: ${topics}`;
    }

    // Students select for teacher award
    const students = await (await fetch("/api/students")).json();
    const selectAward = document.getElementById("teacher-award-student");
    if (selectAward) {
      selectAward.innerHTML =
        '<option value="">Выберите ученика для награды…</option>' +
        students.map((s) => `<option value="${s.max_user_id}">${escapeHtml(s.name)} (${s.xp} XP)</option>`).join("");
    }

  } catch (err) {
    reqList.innerHTML = '<p class="empty-hint">Ошибка загрузки данных учительской.</p>';
  }
}

async function handleReviewRequest(requestId, action) {
  try {
    const result = await (await fetch("/api/teacher/request/review", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-User-Id": String(currentUserId) },
      body: JSON.stringify({ teacher_id: currentUserId, request_id: requestId, action }),
    })).json();
    showToast(result.message || "Статус заявки обновлён");
    loadTeacherData();
    loadProfile();
  } catch (err) {
    showToast("Ошибка рассмотрения заявки");
  }
}

async function handleCreateReward(event) {
  event.preventDefault();
  const title = document.getElementById("rew-title").value.trim();
  const desc = document.getElementById("rew-desc").value.trim();
  const price = parseInt(document.getElementById("rew-price").value, 10);
  const limit = parseInt(document.getElementById("rew-limit").value, 10) || 1;
  const stockVal = document.getElementById("rew-stock").value;
  const stock = stockVal ? parseInt(stockVal, 10) : null;
  const icon = document.getElementById("rew-icon").value;

  try {
    const res = await fetch("/api/teacher/reward/create", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-User-Id": String(currentUserId) },
      body: JSON.stringify({
        teacher_id: currentUserId,
        title: title,
        description: desc,
        price_coins: price,
        max_per_student: limit,
        stock: stock,
        icon: icon,
        group_chat_id: activeClassChatId,
      }),
    });
    const result = await res.json();
    if (!res.ok) {
      showToast(result.detail || "Ошибка создания награды");
      return;
    }
    showToast(result.message || "Награда добавлена в лавку");
    document.getElementById("create-reward-form").reset();
    loadRewards();
  } catch (err) {
    showToast("Ошибка создания награды");
  }
}

async function handleExam(enabled) {
  try {
    const result = await (await fetch("/api/teacher/exam", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-User-Id": String(currentUserId) },
      body: JSON.stringify({ teacher_id: currentUserId, enabled }),
    })).json();
    showToast(result.exam_label || (enabled ? "Контрольная включена" : "Контрольная снята"));
    loadTeacherData();
    loadProfile();
  } catch (err) {
    showToast("Ошибка обновления контрольной");
  }
}

async function saveExamSubjects() {
  const checked = [...document.querySelectorAll("#exam-subjects input:checked")].map((el) => el.value);
  try {
    const result = await (await fetch("/api/teacher/exam", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-User-Id": String(currentUserId) },
      body: JSON.stringify({ teacher_id: currentUserId, enabled: true, subjects: checked.length ? checked : ["*"], days: 1 }),
    })).json();
    showToast(result.exam_label || "Контрольная включена на сегодня");
    loadTeacherData();
    loadProfile();
  } catch (err) {
    showToast("Ошибка сохранения тем");
  }
}

function downloadWeekExport() {
  window.location.href = `/api/teacher/export.csv?teacher_id=${currentUserId}`;
}

async function handleExemplar(questionId) {
  const note = prompt("Короткий конспект для шпаргалки (правило, формула, тезис):", "") || "";
  try {
    const result = await (await fetch("/api/teacher/exemplar", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-User-Id": String(currentUserId) },
      body: JSON.stringify({ teacher_id: currentUserId, question_id: questionId, note }),
    })).json();
    showToast(result.message || "Добавлено в шпаргалку");
    loadTeacherData();
    loadCheatsheet();
  } catch (err) {
    showToast("Ошибка добавления в шпаргалку");
  }
}

async function handleAwardTeacherBadge() {
  const studentId = document.getElementById("teacher-award-student")?.value;
  if (!studentId) {
    showToast("Выберите ученика для награждения");
    return;
  }
  try {
    const result = await (await fetch("/api/teacher/award_badge", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-User-Id": String(currentUserId) },
      body: JSON.stringify({ teacher_id: currentUserId, student_id: parseInt(studentId, 10) }),
    })).json();
    showToast(result.message || "Награда «Выбор учителя» вручена");
    launchConfetti();
    loadProfile();
  } catch (err) {
    showToast("Ошибка вручения награды");
  }
}

/* ============================================================
   LIGHTWEIGHT CONFETTI ENGINE
   ============================================================ */

function launchConfetti() {
  const canvas = document.getElementById("confetti-canvas");
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
  canvas.width = window.innerWidth;
  canvas.height = window.innerHeight;

  const colors = ["#7B61FF", "#4BB34B", "#F5A623", "#E64646", "#4AA3FF"];
  const pieces = Array.from({ length: 50 }, () => ({
    x: canvas.width / 2,
    y: canvas.height * 0.4,
    vx: (Math.random() - 0.5) * 14,
    vy: (Math.random() - 0.9) * 14,
    size: Math.random() * 8 + 4,
    color: colors[Math.floor(Math.random() * colors.length)],
    rot: Math.random() * 360,
    rotSpeed: (Math.random() - 0.5) * 10,
    alpha: 1,
  }));

  let frame = 0;
  function animate() {
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    let alive = false;
    for (const p of pieces) {
      p.x += p.vx;
      p.y += p.vy;
      p.vy += 0.4;
      p.rot += p.rotSpeed;
      if (frame > 25) p.alpha -= 0.02;

      if (p.alpha > 0 && p.y < canvas.height) {
        alive = true;
        ctx.save();
        ctx.globalAlpha = Math.max(0, p.alpha);
        ctx.translate(p.x, p.y);
        ctx.rotate((p.rot * Math.PI) / 180);
        ctx.fillStyle = p.color;
        ctx.fillRect(-p.size / 2, -p.size / 2, p.size, p.size * 1.5);
        ctx.restore();
      }
    }
    frame++;
    if (alive && frame < 90) {
      requestAnimationFrame(animate);
    } else {
      ctx.clearRect(0, 0, canvas.width, canvas.height);
    }
  }
  requestAnimationFrame(animate);
}

/* ============================================================
   TOAST NOTIFICATION
   ============================================================ */

function showToast(message) {
  const toast = document.getElementById("toast");
  if (!toast) return;
  toast.textContent = message;
  toast.classList.remove("hidden");
  clearTimeout(showToast._t);
  showToast._t = setTimeout(() => toast.classList.add("hidden"), 3000);
}
