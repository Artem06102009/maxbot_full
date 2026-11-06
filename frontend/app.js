let currentUserId = 1001;
let currentUserData = null;
let questionsCache = [];

document.addEventListener("DOMContentLoaded", () => {
  initUserFromMAX();
  loadProfile();
  loadQuestions();
});

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

function switchDemoUser(newUserId) {
  currentUserId = parseInt(newUserId, 10);
  showToast("Переключён пользователь");
  loadProfile();
  const active = document.querySelector(".tab-pane.active");
  if (active?.id === "tab-class") loadQuestions();
  if (active?.id === "tab-rewards") loadRewards();
  if (active?.id === "tab-achievements") loadAchievements();
  if (active?.id === "tab-teacher") loadTeacherData();
  if (active?.id === "tab-cheatsheet") loadCheatsheet();
}

async function loadProfile() {
  try {
    const res = await fetch(`/api/profile?user_id=${currentUserId}`);
    if (!res.ok) throw new Error("profile");
    const data = await res.json();
    currentUserData = data;
    const isTeacher = data.role === "teacher";

    document.getElementById("header-name").textContent = data.name;
    document.getElementById("header-role").textContent = isTeacher ? "Учитель" : "Ученик";
    document.getElementById("header-coins").textContent = data.coins;
    document.getElementById("header-avatar").textContent = initials(data.name);
    document.getElementById("profile-avatar").textContent = initials(data.name);
    document.getElementById("profile-name").textContent = data.name;
    document.getElementById("profile-level-title").textContent = data.level.title;
    document.getElementById("profile-level-num").textContent = `${data.level.level} ур.`;
    document.getElementById("profile-xp-text").textContent = `${data.level.current_xp} / ${data.level.next_xp} XP`;
    document.getElementById("profile-xp-fill").style.width = `${data.level.progress_percent}%`;
    document.getElementById("profile-helped").textContent = data.helped_count;
    document.getElementById("profile-questions").textContent = data.questions_count;
    const streakEl = document.getElementById("profile-streak");
    if (streakEl) streakEl.textContent = data.help_streak || 0;
    document.getElementById("profile-rank").textContent = `#${data.rank}`;
    const examBanner = document.getElementById("exam-banner");
    if (examBanner) {
      examBanner.classList.toggle("hidden", !data.exam_mode);
      examBanner.textContent = data.exam_label || "";
    }
    document.getElementById("shop-coins").textContent = data.coins;
    document.getElementById("header-class").textContent = data.class_chat_id
      ? (isTeacher ? "Кабинет педагога · чат класса" : `Чат класса в MAX · место #${data.rank}`)
      : "Добавьте бота в чат класса в MAX";

    const night = document.getElementById("night-hint");
    const pending = data.pending_xp || data.pending_coins;
    night.classList.toggle("hidden", !(data.night_mode || pending));
    if (pending) {
      night.textContent = `🌙 Отложено до утра: ${data.pending_xp} XP и ${data.pending_coins} спасибок`;
    } else if (data.night_mode) {
      night.textContent = "🌙 Тихий час: опыт за помощь начислится утром.";
    }

    const parentLink = document.getElementById("parent-link");
    if (parentLink) parentLink.value = data.report_url || "";

    document.getElementById("nav-teacher-btn").style.display = isTeacher ? "flex" : "none";
    const mobileTeacher = document.getElementById("nav-teacher-btn-mobile");
    if (mobileTeacher) mobileTeacher.style.display = isTeacher ? "block" : "none";
  } catch (err) {
    console.error(err);
    showToast("Не удалось загрузить профиль");
  }
}

function switchTab(tabName, btnElement) {
  document.querySelectorAll(".tab-pane").forEach((pane) => pane.classList.remove("active"));
  document.getElementById(`tab-${tabName}`)?.classList.add("active");
  document.querySelectorAll(".rail-btn, .nav-item").forEach((item) => {
    item.classList.toggle("active", item.dataset.tab === tabName);
  });
  if (btnElement) btnElement.classList.add("active");
  if (tabName === "profile") loadProfile();
  if (tabName === "class") loadQuestions();
  if (tabName === "leaderboard") loadLeaderboard();
  if (tabName === "achievements") loadAchievements();
  if (tabName === "rewards") loadRewards();
  if (tabName === "teacher") loadTeacherData();
  if (tabName === "cheatsheet") loadCheatsheet();
}

async function loadQuestions() {
  const feed = document.getElementById("questions-feed");
  feed.innerHTML = '<div class="loading-spinner">Загрузка ленты класса…</div>';
  try {
    const res = await fetch(`/api/questions?user_id=${currentUserId}`);
    if (!res.ok) throw new Error("questions");
    questionsCache = await res.json();
    renderQuestions(questionsCache);
  } catch (err) {
    feed.innerHTML = '<p class="empty-hint">Не удалось загрузить вопросы.</p>';
  }
}

function filterQuestions() {
  const q = document.getElementById("class-search").value.trim().toLowerCase();
  const filtered = questionsCache.filter((item) =>
    `${item.text} ${item.author_name} ${item.subject}`.toLowerCase().includes(q)
  );
  renderQuestions(filtered);
}

function renderQuestions(items) {
  const feed = document.getElementById("questions-feed");
  if (!items.length) {
    feed.innerHTML = '<p class="empty-hint">Пока нет вопросов. В чате класса в MAX напишите /ask и текст вопроса.</p>';
    return;
  }
  feed.innerHTML = items.map((item) => {
    const own = item.author_id === currentUserId;
    const isHelper = item.helper_id === currentUserId;
    const closed = item.status === "answered";
    let actions = "";
    if (!closed && !own) {
      actions = `<button class="btn btn-ghost" onclick="handleHelp(${item.id})">${isHelper ? "Вы помощник" : "Я помог"}</button>`;
    }
    if (!closed && own && item.helper_id) {
      actions += `<div class="quality-actions">
        <button class="btn btn-primary" onclick="handleThanks(${item.id}, 'clear')">Понятно</button>
        <button class="btn btn-ghost" onclick="handleThanks(${item.id}, 'short')">Коротко</button>
        <button class="btn btn-ghost" onclick="handleThanks(${item.id}, 'error')">С ошибкой</button>
      </div>`;
    } else if (!closed && own) {
      actions += `<button class="btn btn-primary" disabled>Сначала нужен ответ в чате</button>`;
    }
    if (!closed && !item.hint_blocked) {
      actions += `<button class="btn btn-ghost" onclick="handleHint(${item.id})">Подсказка ГигаЧат</button>`;
    }
    if (currentUserData?.role === "teacher" && !item.is_exemplar) {
      actions += `<button class="btn btn-ghost" onclick="handleExemplar(${item.id})">В шпаргалку</button>`;
    }
    const helperLine = item.helper_name
      ? `<div class="q-meta">Помогает ${escapeHtml(item.helper_name)}</div>`
      : "";
    const photo = item.image_url
      ? `<a class="q-photo" href="${escapeHtml(item.image_url)}" target="_blank" rel="noopener">📷 Фото задачи</a>`
      : "";
    return `
      <article class="q-card ${own ? "own" : ""} ${closed ? "answered" : ""}">
        <div class="q-meta">
          <span class="avatar">${escapeHtml(initials(item.author_name))}</span>
          <span class="q-name">${escapeHtml(item.author_name)}</span>
          <span>${escapeHtml(item.created_at)}</span>
          <span class="subject-chip">${escapeHtml(item.subject)}</span>
          ${item.is_exemplar ? '<span class="status-badge status-approved">Эталон</span>' : ""}
          ${closed ? '<span class="status-badge status-approved">Решено</span>' : ""}
        </div>
        <div class="q-text">${escapeHtml(item.text)}</div>
        ${photo}
        ${helperLine}
        <div class="q-actions">${actions}</div>
      </article>
    `;
  }).join("");
}

async function handleHint(questionId) {
  showToast("Готовлю подсказку…");
  const result = await (await fetch("/api/questions/hint", {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-User-Id": String(currentUserId) },
    body: JSON.stringify({ user_id: currentUserId, question_id: questionId }),
  })).json();
  if (!result.success) {
    showToast(result.error || "Лимит подсказок");
    return;
  }
  alert(result.hint);
}

async function handleHelp(questionId) {
  const res = await fetch("/api/questions/help", {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-User-Id": String(currentUserId) },
    body: JSON.stringify({ user_id: currentUserId, question_id: questionId }),
  });
  const result = await res.json();
  showToast(result.message || result.error);
  loadQuestions();
}

async function handleThanks(questionId, quality = "clear") {
  const res = await fetch("/api/questions/thanks", {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-User-Id": String(currentUserId) },
    body: JSON.stringify({ user_id: currentUserId, question_id: questionId, quality }),
  });
  const result = await res.json();
  showToast(result.message || result.error);
  if (result.unlocked?.length) showToast(result.unlocked[0]);
  loadProfile();
  loadQuestions();
}

async function loadLeaderboard() {
  const container = document.getElementById("leaderboard-container");
  container.innerHTML = '<div class="loading-spinner">Загрузка рейтинга…</div>';
  try {
    const leaders = await (await fetch(`/api/leaderboard?user_id=${currentUserId}`)).json();
    if (!leaders.length) {
      container.innerHTML = '<p class="empty-hint">Пока нет участников.</p>';
      return;
    }
    container.innerHTML = leaders.map((item) => {
      const medals = { 1: "🥇", 2: "🥈", 3: "🥉" };
      const isCurrent = item.max_user_id === currentUserId;
      return `
        <div class="leader-item ${isCurrent ? "current-user" : ""}">
          <div class="leader-rank">${medals[item.rank] || "#" + item.rank}</div>
          <span class="avatar">${escapeHtml(initials(item.name))}</span>
          <div class="leader-info">
            <div class="leader-name">${escapeHtml(item.name)}${isCurrent ? " · вы" : ""}</div>
            <div class="leader-sub">${escapeHtml(item.level_title)} · помог ${item.helped_count} · серия ${item.help_streak || 0} дн.</div>
          </div>
          <div class="leader-xp">${item.xp} XP</div>
        </div>
      `;
    }).join("");
  } catch (err) {
    container.innerHTML = '<p class="empty-hint">Ошибка загрузки рейтинга.</p>';
  }
}

async function loadAchievements() {
  const container = document.getElementById("achievements-container");
  container.innerHTML = '<div class="loading-spinner">Загрузка достижений…</div>';
  try {
    const achievements = await (await fetch(`/api/achievements?user_id=${currentUserId}`)).json();
    container.innerHTML = achievements.map((ach) => `
      <div class="ach-card ${ach.is_unlocked ? "unlocked" : "locked"}">
        <div class="ach-icon">${escapeHtml(ach.icon)}</div>
        <div class="ach-title">${escapeHtml(ach.title)}</div>
        <div class="ach-desc">${escapeHtml(ach.description)}</div>
        <div class="ach-reward-tag">+${ach.xp_reward} XP · +${ach.coins_reward} 🪙</div>
        <div class="ach-date">${ach.is_unlocked ? "Открыто " + escapeHtml(ach.unlocked_at) : "Ещё закрыто"}</div>
      </div>
    `).join("");
  } catch (err) {
    container.innerHTML = '<p class="empty-hint">Ошибка загрузки достижений.</p>';
  }
}

async function loadRewards() {
  const grid = document.getElementById("rewards-container");
  const requestsList = document.getElementById("my-requests-list");
  grid.innerHTML = '<div class="loading-spinner">Загрузка лавки…</div>';
  try {
    const data = await (await fetch(`/api/rewards?user_id=${currentUserId}`)).json();
    document.getElementById("shop-coins").textContent = data.user_coins;
    const lockEl = document.getElementById("shop-lock");
    if (lockEl) {
      lockEl.classList.toggle("hidden", !data.shop_locked);
      lockEl.textContent = data.shop_message || "";
    }
    if (!data.rewards.length) {
      grid.innerHTML = '<p class="empty-hint">Учитель пока не добавил награды.</p>';
    } else {
      grid.innerHTML = data.rewards.map((rew) => `
        <div class="reward-card">
          <div class="reward-icon-box">${escapeHtml(rew.icon)}</div>
          <div class="reward-details">
            <div class="reward-title">${escapeHtml(rew.title)}</div>
            <div class="reward-desc">${escapeHtml(rew.description)}</div>
          </div>
          <button class="btn btn-gold" ${!data.shop_locked && data.user_coins >= rew.price_coins ? "" : "disabled"} onclick="handleBuyReward(${rew.id})">
            ${rew.price_coins} 🪙
          </button>
        </div>
      `).join("");
    }
    if (!data.my_requests.length) {
      requestsList.innerHTML = '<p class="empty-hint">Нет запросов.</p>';
    } else {
      const labels = { pending: ["⏳ На рассмотрении", "status-pending"], approved: ["Одобрено", "status-approved"], rejected: ["Отклонено", "status-rejected"] };
      requestsList.innerHTML = data.my_requests.map((req) => {
        const [text, cls] = labels[req.status] || labels.pending;
        return `
          <div class="req-item">
            <div><strong>${escapeHtml(req.reward_icon)} ${escapeHtml(req.reward_title)}</strong><div class="muted">${escapeHtml(req.created_at)}</div></div>
            <span class="status-badge ${cls}">${text}</span>
          </div>
        `;
      }).join("");
    }
  } catch (err) {
    grid.innerHTML = '<p class="empty-hint">Ошибка загрузки наград.</p>';
  }
}

async function handleBuyReward(rewardId) {
  const result = await (await fetch("/api/rewards/buy", {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-User-Id": String(currentUserId) },
    body: JSON.stringify({ user_id: currentUserId, reward_id: rewardId }),
  })).json();
  showToast(result.message || result.error || result.detail || "Готово");
  loadProfile();
  loadRewards();
}

async function loadTeacherData() {
  const reqList = document.getElementById("teacher-requests-list");
  reqList.innerHTML = '<div class="loading-spinner">Загрузка заявок…</div>';
  try {
    const requests = await (await fetch(`/api/teacher/requests?teacher_id=${currentUserId}`)).json();
    if (!Array.isArray(requests)) {
      reqList.innerHTML = '<p class="empty-hint">Нет доступа к учительской.</p>';
      return;
    }
    if (!requests.length) {
      reqList.innerHTML = '<p class="empty-hint">Нет ожидающих заявок.</p>';
    } else {
      reqList.innerHTML = requests.map((req) => `
        <div class="teacher-req-item">
          <div class="teacher-req-header">
            <span>${escapeHtml(req.student_name)}</span>
            <span>${req.price_coins} 🪙</span>
          </div>
          <div>${escapeHtml(req.reward_icon)} ${escapeHtml(req.reward_title)}</div>
          <div class="muted">${escapeHtml(req.created_at)}</div>
          <div class="teacher-req-actions">
            <button class="btn btn-green flex-1" onclick="handleReviewRequest(${req.id}, 'approve')">Одобрить</button>
            <button class="btn btn-red flex-1" onclick="handleReviewRequest(${req.id}, 'reject')">Отклонить</button>
          </div>
        </div>
      `).join("");
    }
    const cls = await (await fetch(`/api/teacher/class?teacher_id=${currentUserId}`)).json();
    const strictEl = document.getElementById("strict-status");
    const blocked = cls.strict_subjects || [];
    if (strictEl) {
      if (blocked.includes("*") || cls.strict_mode) strictEl.textContent = "ГигаЧат выключен по всем темам";
      else if (blocked.length) strictEl.textContent = "Без ИИ: " + blocked.join(", ");
      else strictEl.textContent = "ИИ доступен по всем темам";
    }
    const toggles = document.getElementById("strict-subjects");
    if (toggles && cls.subjects) {
      const all = blocked.includes("*") || cls.strict_mode;
      toggles.innerHTML = cls.subjects.map((name) => `
        <label class="toggle">
          <input type="checkbox" value="${escapeHtml(name)}" ${all || blocked.includes(name) ? "checked" : ""}>
          ${escapeHtml(name)}
        </label>
      `).join("");
    }
    const examStatus = document.getElementById("exam-status");
    if (examStatus) examStatus.textContent = cls.exam_label || "Контрольной нет. Можно включить на сегодня: без ГигаЧат и лавки.";
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
    const preview = document.getElementById("week-export-preview");
    if (preview) {
      const week = await (await fetch(`/api/teacher/export?teacher_id=${currentUserId}`)).json();
      const topics = (week.subjects || []).map(([name, n]) => `${name}: ${n}`).join(" · ") || "нет вопросов";
      preview.innerHTML = `За 7 дней: <b>${week.questions || 0}</b> вопросов, разобрано ${week.answered || 0}. ${topics}`;
    }
    const hang = document.getElementById("teacher-unanswered-list");
    if (hang) {
      const items = await (await fetch(`/api/teacher/unanswered?teacher_id=${currentUserId}`)).json();
      if (!Array.isArray(items) || !items.length) {
        hang.innerHTML = '<p class="empty-hint">Все вопросы разобраны.</p>';
      } else {
        hang.innerHTML = items.map((q) => `
          <div class="teacher-req-item">
            <strong>${escapeHtml(q.subject)}</strong>
            <div>${escapeHtml(q.text)}</div>
            <div class="muted">${escapeHtml(q.author_name)} · ${escapeHtml(q.created_at)}</div>
            <div class="teacher-req-actions">
              <button class="btn btn-ghost" onclick="handleHint(${q.id})">Подсказка</button>
              <button class="btn btn-primary" onclick="handleExemplar(${q.id})">Эталон</button>
            </div>
          </div>
        `).join("");
      }
    }
    const students = await (await fetch("/api/students")).json();
    document.getElementById("teacher-award-student").innerHTML =
      '<option value="">Выберите ученика…</option>' +
      students.map((s) => `<option value="${s.max_user_id}">${escapeHtml(s.name)} (${s.xp} XP)</option>`).join("");
  } catch (err) {
    reqList.innerHTML = '<p class="empty-hint">Ошибка доступа к учительской.</p>';
  }
}

async function handleReviewRequest(requestId, action) {
  const result = await (await fetch("/api/teacher/request/review", {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-User-Id": String(currentUserId) },
    body: JSON.stringify({ teacher_id: currentUserId, request_id: requestId, action }),
  })).json();
  showToast(result.message || "Готово");
  loadTeacherData();
  loadProfile();
}

async function handleCreateReward(event) {
  event.preventDefault();
  const result = await (await fetch("/api/teacher/reward/create", {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-User-Id": String(currentUserId) },
    body: JSON.stringify({
      teacher_id: currentUserId,
      title: document.getElementById("rew-title").value.trim(),
      description: document.getElementById("rew-desc").value.trim(),
      price_coins: parseInt(document.getElementById("rew-price").value, 10),
      icon: document.getElementById("rew-icon").value,
    }),
  })).json();
  showToast(result.message || "Награда добавлена");
  document.getElementById("create-reward-form").reset();
}

async function handleStrict(enabled) {
  const result = await (await fetch("/api/teacher/strict", {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-User-Id": String(currentUserId) },
    body: JSON.stringify({ teacher_id: currentUserId, enabled }),
  })).json();
  showToast(result.strict_mode ? "ГигаЧат выключен везде" : "ГигаЧат снова можно");
  loadTeacherData();
}

async function handleExam(enabled) {
  const result = await (await fetch("/api/teacher/exam", {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-User-Id": String(currentUserId) },
    body: JSON.stringify({ teacher_id: currentUserId, enabled }),
  })).json();
  showToast(result.exam_label || (enabled ? "Контрольная включена" : "Контрольная снята"));
  loadTeacherData();
  loadProfile();
}

async function saveExamSubjects() {
  const checked = [...document.querySelectorAll("#exam-subjects input:checked")].map((el) => el.value);
  const result = await (await fetch("/api/teacher/exam", {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-User-Id": String(currentUserId) },
    body: JSON.stringify({ teacher_id: currentUserId, enabled: true, subjects: checked.length ? checked : ["*"], days: 1 }),
  })).json();
  showToast(result.exam_label || "Контрольная обновлена");
  loadTeacherData();
  loadProfile();
}

async function saveStrictSubjects() {
  const checked = [...document.querySelectorAll("#strict-subjects input:checked")].map((el) => el.value);
  const result = await (await fetch("/api/teacher/strict", {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-User-Id": String(currentUserId) },
    body: JSON.stringify({ teacher_id: currentUserId, subjects: checked }),
  })).json();
  if (result.strict_subjects?.length) showToast("ГигаЧат выключен: " + result.strict_subjects.join(", "));
  else showToast("ГигаЧат снова можно по всем темам");
  loadTeacherData();
}

function downloadWeekExport() {
  window.location.href = `/api/teacher/export.csv?teacher_id=${currentUserId}`;
}

function downloadCheatsheetPdf() {
  window.location.href = `/api/cheatsheet.pdf?user_id=${currentUserId}`;
}

async function loadCheatsheet() {
  const box = document.getElementById("cheatsheet-container");
  if (!box) return;
  box.innerHTML = '<div class="loading-spinner">Собираем конспект класса…</div>';
  try {
    const data = await (await fetch(`/api/cheatsheet?user_id=${currentUserId}`)).json();
    if (!data.subjects?.length) {
      box.innerHTML = '<p class="empty-hint">Пока нет эталонов. Учитель отмечает лучшие разборы кнопкой «Эталон».</p>';
      return;
    }
    box.innerHTML = data.subjects.map((group) => `
      <div class="card">
        <h4>${escapeHtml(group.subject)}</h4>
        ${group.items.map((item) => `
          <div class="cheat-item">
            <div class="cheat-q">${escapeHtml(item.text)}</div>
            ${item.exemplar_note ? `<div class="cheat-note">${escapeHtml(item.exemplar_note)}</div>` : ""}
            <div class="muted">Разбор: ${escapeHtml(item.helper_name || "класс")} · ${escapeHtml(item.author_name)}</div>
          </div>
        `).join("")}
      </div>
    `).join("");
  } catch (err) {
    box.innerHTML = '<p class="empty-hint">Не удалось загрузить шпаргалку.</p>';
  }
}

function copyParentLink() {
  const input = document.getElementById("parent-link");
  if (!input?.value) return;
  navigator.clipboard.writeText(input.value);
  showToast("Ссылка для родителя скопирована");
}

async function handleDigest() {
  const result = await (await fetch(`/api/teacher/digest?teacher_id=${currentUserId}`, { method: "POST" })).json();
  showToast(result.message || "Дайджест отправлен");
}

async function handleExemplar(questionId) {
  const note = prompt("Короткий конспект для шпаргалки (формула, план, правило):", "") || "";
  const result = await (await fetch("/api/teacher/exemplar", {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-User-Id": String(currentUserId) },
    body: JSON.stringify({ teacher_id: currentUserId, question_id: questionId, note }),
  })).json();
  showToast(result.message || "Добавлено в шпаргалку");
  loadQuestions();
  loadTeacherData();
}

async function handleAwardTeacherBadge() {
  const studentId = document.getElementById("teacher-award-student").value;
  if (!studentId) {
    showToast("Выберите ученика");
    return;
  }
  const result = await (await fetch("/api/teacher/award_badge", {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-User-Id": String(currentUserId) },
    body: JSON.stringify({ teacher_id: currentUserId, student_id: parseInt(studentId, 10) }),
  })).json();
  showToast(result.message || "Готово");
}

function showToast(message) {
  const toast = document.getElementById("toast");
  toast.textContent = message;
  toast.classList.remove("hidden");
  clearTimeout(showToast._t);
  showToast._t = setTimeout(() => toast.classList.add("hidden"), 3200);
}
