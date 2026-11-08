let currentUserId = "";
let currentUserDisplayName = "";
let currentUserData = null;
let availableChats = [];
let selectedChatId = "all";
let selectedChatTitle = "Учебный класс";
let currentLeaderboardPeriod = "all_time";
let currentLeaderboardScope = "class";
let teacherSelectedChatId = "all";

document.addEventListener("DOMContentLoaded", () => {
  initTheme();
  initUserFromMAX();
  loadProfile();
});

function initTheme() {
  localStorage.removeItem("sferum_theme");
  const savedTheme = localStorage.getItem("sferum_theme_v3");
  if (savedTheme === "dark") {
    applyTheme("dark");
  } else {
    applyTheme("light");
  }
}

function toggleTheme() {
  const current = document.documentElement.getAttribute("data-theme") || "light";
  const next = current === "light" ? "dark" : "light";
  applyTheme(next);
  localStorage.setItem("sferum_theme_v3", next);
}

function applyTheme(theme) {
  document.documentElement.setAttribute("data-theme", theme);
  const sunIcon = document.getElementById("theme-icon-sun");
  const moonIcon = document.getElementById("theme-icon-moon");
  if (sunIcon && moonIcon) {
    if (theme === "dark") {
      sunIcon.classList.remove("hidden");
      moonIcon.classList.add("hidden");
    } else {
      sunIcon.classList.add("hidden");
      moonIcon.classList.remove("hidden");
    }
  }
}

async function initUserFromMAX() {
  const urlParams = new URLSearchParams(window.location.search);
  const urlUserId = urlParams.get("user_id");

  if (window.WebApp && window.WebApp.initDataUnsafe && window.WebApp.initDataUnsafe.user) {
    const maxUser = window.WebApp.initDataUnsafe.user;
    currentUserId = String(maxUser.id);

    const fName = maxUser.first_name || "";
    const lName = maxUser.last_name || "";
    const builtName = `${fName} ${lName}`.trim() || maxUser.username || "";
    if (builtName) {
      currentUserDisplayName = builtName;
      syncUserWithBackend(maxUser.id, fName, lName, maxUser.username);
    }

    const demoBar = document.getElementById("demo-bar");
    if (demoBar) demoBar.style.display = "none";
  } else if (urlUserId) {
    currentUserId = String(urlUserId);
    const demoSelect = document.getElementById("demo-user-select");
    if (demoSelect) demoSelect.value = currentUserId;
  }
}

async function syncUserWithBackend(userId, firstName, lastName, username) {
  try {
    await fetch("/api/sync-user", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        user_id: String(userId),
        first_name: firstName,
        last_name: lastName,
        username: username,
      }),
    });
  } catch (err) {}
}

function switchDemoUser(newUserId) {
  currentUserId = String(newUserId);
  currentUserDisplayName = "";
  showToast("Выбран профиль: " + currentUserId);
  loadProfile();
  refreshActiveTab();
}

function refreshActiveTab() {
  const activeTab = document.querySelector(".tab-pane.active");
  if (!activeTab) return;
  const tabId = activeTab.id.replace("tab-", "");
  if (tabId === "leaderboard") loadLeaderboard();
  if (tabId === "achievements") loadAchievements();
  if (tabId === "rewards") loadRewards();
  if (tabId === "teacher") loadTeacherData();
}

function getInitials(name) {
  if (!name) return "УЧ";
  const parts = name.trim().split(/\s+/);
  if (parts.length >= 2) {
    return (parts[0][0] + parts[1][0]).toUpperCase();
  }
  return name.slice(0, 2).toUpperCase();
}

async function loadProfile() {
  try {
    let url = `/api/profile?user_id=${encodeURIComponent(currentUserId)}`;
    if (selectedChatId && selectedChatId !== "all") {
      url += `&chat_id=${encodeURIComponent(selectedChatId)}`;
    }
    if (currentUserDisplayName) {
      url += `&display_name=${encodeURIComponent(currentUserDisplayName)}`;
    }

    const res = await fetch(url);
    if (!res.ok) throw new Error("Profile error");
    const data = await res.json();
    currentUserData = data;
    if (data.max_user_id) {
      currentUserId = String(data.max_user_id);
    }

    document.getElementById("header-name").textContent = data.display_name;
    const roleElem = document.getElementById("header-role");
    if (data.role === "teacher") {
      roleElem.textContent = "Учитель";
      roleElem.classList.add("teacher");
    } else {
      roleElem.textContent = "Ученик";
      roleElem.classList.remove("teacher");
    }

    document.getElementById("header-coins").textContent = data.balance_spasibki;
    document.getElementById("header-avatar").textContent = getInitials(data.display_name);

    document.getElementById("profile-level-title").textContent = data.rank;
    document.getElementById("profile-level-num").textContent = `${data.level} уровень`;

    const xpInLevel = data.xp % 100;
    document.getElementById("profile-xp-text").textContent = `${xpInLevel} / 100 XP (${data.xp} всего)`;
    document.getElementById("profile-xp-fill").style.width = `${Math.min(100, xpInLevel)}%`;

    document.getElementById("profile-helped").textContent = data.helped_count || 0;
    document.getElementById("profile-questions").textContent = data.questions_count || 0;
    document.getElementById("profile-rank").textContent = `#${data.rank_position || 1}`;

    const shopCoins = document.getElementById("shop-coins");
    if (shopCoins) shopCoins.textContent = data.balance_spasibki;

    if (data.chats && data.chats.length > 0) {
      availableChats = data.chats.filter(c => c.title && c.title.toLowerCase() !== "диалог");
      if (selectedChatId === "all" || !availableChats.some(c => c.id === selectedChatId)) {
        selectedChatId = availableChats[0].id;
        selectedChatTitle = availableChats[0].title;
      }
    } else {
      availableChats = [{ id: "my_class", title: "Мой учебный класс", role: data.role }];
      selectedChatId = "my_class";
      selectedChatTitle = "Мой учебный класс";
    }

    const teacherNavBtn = document.getElementById("nav-teacher-btn");
    const hasTeacherRole = (data.role === "teacher" || availableChats.some(c => c.role === "teacher"));
    if (teacherNavBtn) {
      teacherNavBtn.style.display = hasTeacherRole ? "flex" : "none";
    }

    updateClassSelectorUI();
    populateTeacherClassDropdowns();

  } catch (err) {
    showToast("Не удалось обновить профиль");
  }
}

function updateClassSelectorUI() {
  const label = document.getElementById("current-class-label");
  if (label) {
    label.textContent = selectedChatTitle;
  }
}

function openClassModal() {
  const modal = document.getElementById("class-modal");
  const list = document.getElementById("sheet-classes-list");
  if (!modal || !list) return;

  const validChats = availableChats.filter(c => c.title && c.title.toLowerCase() !== "диалог");

  list.innerHTML = validChats.map(c => `
    <div class="class-option-card ${c.id === selectedChatId ? 'selected' : ''}" onclick="selectClass('${c.id}', '${escapeHtml(c.title)}')">
      <div>
        <div class="class-option-name">${escapeHtml(c.title)}</div>
        <div class="class-option-role">${c.role === 'teacher' ? 'Преподаватель' : 'Ученик'}</div>
      </div>
      <div>${c.id === selectedChatId ? '✓' : ''}</div>
    </div>
  `).join("");

  modal.classList.remove("hidden");
}

function closeClassModal(e) {
  if (e && e.target && e.target.closest && e.target.closest(".modal-sheet") && !e.target.classList.contains("sheet-close")) {
    return;
  }
  const modal = document.getElementById("class-modal");
  if (modal) modal.classList.add("hidden");
}

function selectClass(chatId, chatTitle) {
  selectedChatId = chatId;
  selectedChatTitle = chatTitle;

  const chatObj = availableChats.find(c => c.id === chatId);
  if (chatObj) {
    const roleElem = document.getElementById("header-role");
    if (roleElem) {
      if (chatObj.role === "teacher") {
        roleElem.textContent = "Учитель";
        roleElem.classList.add("teacher");
      } else {
        roleElem.textContent = "Ученик";
        roleElem.classList.remove("teacher");
      }
    }
  }

  updateClassSelectorUI();
  closeClassModal();
  showToast("Выбран класс: " + chatTitle);
  loadProfile();
  refreshActiveTab();
}

function populateTeacherClassDropdowns() {
  const selectElem = document.getElementById("teacher-class-select");
  const rewSelectElem = document.getElementById("rew-chat-select");

  let options = '<option value="all">Все классы</option>';
  options += availableChats.map(c => `<option value="${c.id}">${escapeHtml(c.title)}</option>`).join("");

  if (selectElem) {
    selectElem.innerHTML = options;
    selectElem.value = teacherSelectedChatId;
  }
  if (rewSelectElem) {
    rewSelectElem.innerHTML = options;
  }
}

function onTeacherClassChange(chatId) {
  teacherSelectedChatId = chatId;
  loadTeacherData();
}

function switchTab(tabName, btnElement) {
  document.querySelectorAll(".tab-pane").forEach(pane => pane.classList.remove("active"));
  const target = document.getElementById(`tab-${tabName}`);
  if (target) target.classList.add("active");

  document.querySelectorAll(".tab-item").forEach(item => item.classList.remove("active"));
  if (btnElement) btnElement.classList.add("active");

  if (tabName === "profile") loadProfile();
  if (tabName === "leaderboard") loadLeaderboard();
  if (tabName === "achievements") loadAchievements();
  if (tabName === "rewards") loadRewards();
  if (tabName === "teacher") loadTeacherData();
}

function setLeaderboardScope(scope) {
  currentLeaderboardScope = scope;
  document.getElementById("scope-class-btn").classList.toggle("active", scope === "class");
  document.getElementById("scope-school-btn").classList.toggle("active", scope === "school");
  loadLeaderboard();
}

function changeLeaderboardPeriod(period) {
  currentLeaderboardPeriod = period;
  document.getElementById("btn-period-all").classList.toggle("active", period === "all_time");
  document.getElementById("btn-period-week").classList.toggle("active", period === "weekly");
  loadLeaderboard();
}

async function loadLeaderboard() {
  const container = document.getElementById("leaderboard-container");
  container.innerHTML = '<div class="state-loading">Загрузка рейтинга...</div>';

  try {
    let url = `/api/leaderboard?period=${currentLeaderboardPeriod}`;
    if (currentLeaderboardScope === "class" && selectedChatId && selectedChatId !== "all") {
      url += `&chat_id=${selectedChatId}`;
    } else {
      url += `&chat_id=all`;
    }

    const res = await fetch(url);
    const data = await res.json();
    const leaders = data.items || [];

    if (!leaders.length) {
      container.innerHTML = '<p class="empty-state-text">В этом рейтинге пока нет участников.</p>';
      return;
    }

    container.innerHTML = leaders.map(item => {
      let rankText = `#${item.rank_position}`;
      let topClass = "";
      if (item.rank_position === 1) topClass = "top-1";
      if (item.rank_position === 2) topClass = "top-2";
      if (item.rank_position === 3) topClass = "top-3";

      const isCurrent = String(item.max_user_id) === String(currentUserId);
      const initials = getInitials(item.display_name);

      return `
        <div class="leader-card ${isCurrent ? 'current-user' : ''}">
          <div class="leader-rank-col ${topClass}">${rankText}</div>
          <div class="leader-avatar">${initials}</div>
          <div class="leader-details">
            <div class="leader-name">${escapeHtml(item.display_name)}</div>
            <div class="leader-meta">${escapeHtml(item.rank)}</div>
          </div>
          <div class="leader-score">
            <div class="leader-xp-val">${item.xp} XP</div>
            <div class="leader-coins-val">${item.balance_spasibki} спасибок</div>
          </div>
        </div>
      `;
    }).join("");

  } catch (err) {
    container.innerHTML = '<p class="empty-state-text">Не удалось загрузить рейтинг</p>';
  }
}

async function loadAchievements() {
  const container = document.getElementById("achievements-container");
  container.innerHTML = '<div class="state-loading">Загрузка достижений...</div>';

  try {
    const res = await fetch(`/api/achievements?user_id=${currentUserId}`);
    const achievements = await res.json();

    if (!achievements.length) {
      container.innerHTML = '<p class="empty-state-text">Достижения не найдены.</p>';
      return;
    }

    container.innerHTML = achievements.map(ach => {
      const isUnlocked = ach.unlocked;
      let statusTag = "";
      if (isUnlocked && ach.awarded_at) {
        const d = new Date(ach.awarded_at);
        statusTag = `<span class="ach-tag tag-unlocked">Получено ${d.toLocaleDateString('ru-RU')}</span>`;
      } else if (isUnlocked) {
        statusTag = `<span class="ach-tag tag-unlocked">Получено</span>`;
      } else {
        statusTag = `<span class="ach-tag tag-locked">Не открыто</span>`;
      }

      return `
        <div class="ach-item-card ${isUnlocked ? 'unlocked' : 'locked'}">
          <div class="ach-icon-circle">
            <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <circle cx="12" cy="8" r="7"/>
              <polyline points="8.21 13.89 7 23 12 20 17 23 15.79 13.88"/>
            </svg>
          </div>
          <div class="ach-title">${escapeHtml(ach.name_ru)}</div>
          <div class="ach-desc">${escapeHtml(ach.description_ru)}</div>
          ${statusTag}
        </div>
      `;
    }).join("");

  } catch (err) {
    container.innerHTML = '<p class="empty-state-text">Не удалось загрузить список наград</p>';
  }
}

async function loadRewards() {
  const container = document.getElementById("rewards-container");
  container.innerHTML = '<div class="state-loading">Загрузка лавки...</div>';

  try {
    let url = "/api/rewards";
    if (selectedChatId && selectedChatId !== "all") {
      url += `?chat_id=${selectedChatId}`;
    }

    const res = await fetch(url);
    const rewards = await res.json();

    if (!rewards.length) {
      container.innerHTML = '<p class="empty-state-text">В лавке пока нет доступных наград.</p>';
    } else {
      container.innerHTML = rewards.map(r => `
        <div class="reward-card-item">
          <div class="reward-main-col">
            <div class="reward-item-title">${escapeHtml(r.title)}</div>
            <div class="reward-item-desc">${escapeHtml(r.description)}</div>
            <div class="reward-cost-tag">${r.cost_spasibki} спасибок</div>
          </div>
          <div>
            <button class="sferum-btn btn-primary" onclick="handleClaimReward(${r.id}, ${r.cost_spasibki})">Обменять</button>
          </div>
        </div>
      `).join("");
    }

    loadMyClaims();

  } catch (err) {
    container.innerHTML = '<p class="empty-state-text">Не удалось загрузить лавку</p>';
  }
}

async function loadMyClaims() {
  const container = document.getElementById("my-requests-list");
  try {
    const res = await fetch(`/api/my-claims?user_id=${currentUserId}`);
    const claims = await res.json();

    if (!claims.length) {
      container.innerHTML = '<p class="empty-state-text">У вас пока нет активных запросов.</p>';
      return;
    }

    container.innerHTML = claims.map(c => {
      let statusText = "Ожидает учителя";
      let statusClass = "status-requested";
      if (c.status === "approved") {
        statusText = "Одобрено учителем";
        statusClass = "status-approved";
      } else if (c.status === "rejected") {
        statusText = "Отклонено";
        statusClass = "status-rejected";
      }

      return `
        <div class="claim-history-row">
          <div>
            <strong>${escapeHtml(c.title)}</strong>
            <div class="stat-caption">${c.cost_spasibki} спасибок</div>
          </div>
          <span class="claim-status-pill ${statusClass}">${statusText}</span>
        </div>
      `;
    }).join("");

  } catch (err) {
    container.innerHTML = '<p class="empty-state-text">Не удалось загрузить историю наград</p>';
  }
}

async function handleClaimReward(rewardId, cost) {
  if (currentUserData && currentUserData.balance_spasibki < cost) {
    showToast(`Нужно ${cost} спасибок. У вас ${currentUserData.balance_spasibki}`);
    return;
  }

  try {
    const res = await fetch("/api/rewards/claim", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-User-Id": currentUserId,
      },
      body: JSON.stringify({
        reward_id: rewardId,
        user_id: currentUserId,
        chat_id: selectedChatId !== "all" ? selectedChatId : null,
      })
    });

    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || "Ошибка запроса");
    }

    showToast("Заявка успешно отправлена учителю!");
    loadProfile();
    loadRewards();

  } catch (err) {
    showToast(err.message || "Ошибка обмена");
  }
}

async function loadTeacherData() {
  const listContainer = document.getElementById("teacher-requests-list");
  const badgeCount = document.getElementById("pending-claims-count");
  listContainer.innerHTML = '<div class="state-loading">Загрузка заявок...</div>';

  try {
    let url = `/api/teacher/overview?user_id=${currentUserId}`;
    if (teacherSelectedChatId && teacherSelectedChatId !== "all") {
      url += `&chat_id=${teacherSelectedChatId}`;
    }

    const res = await fetch(url);
    if (!res.ok) throw new Error("Только для учителей");
    const overview = await res.json();

    const claims = overview.pending_claims || [];
    if (badgeCount) badgeCount.textContent = claims.length;

    if (!claims.length) {
      listContainer.innerHTML = '<p class="empty-state-text">Новых заявок от учеников нет.</p>';
    } else {
      listContainer.innerHTML = claims.map(c => `
        <div class="teacher-claim-card">
          <div class="claim-user-row">
            <span class="claim-student-name">${escapeHtml(c.student_name)}</span>
            <span class="claim-price-pill">${c.cost_spasibki} спасибок</span>
          </div>
          <div class="claim-reward-text">Запрос: <b>${escapeHtml(c.reward_title)}</b></div>
          <div class="claim-btn-row">
            <button class="sferum-btn btn-primary flex-1" onclick="handleReviewClaim(${c.claim_id}, 'approved')">Одобрить</button>
            <button class="sferum-btn btn-outline flex-1" onclick="handleReviewClaim(${c.claim_id}, 'rejected')">Отклонить</button>
          </div>
        </div>
      `).join("");
    }

    loadStudentsDropdown();

  } catch (err) {
    listContainer.innerHTML = '<p class="empty-state-text">Кабинет доступен учителям.</p>';
  }
}

async function loadStudentsDropdown() {
  const selectElem = document.getElementById("teacher-award-student");
  try {
    let url = "/api/students";
    if (teacherSelectedChatId && teacherSelectedChatId !== "all") {
      url += `?chat_id=${teacherSelectedChatId}`;
    }
    const res = await fetch(url);
    const students = await res.json();
    selectElem.innerHTML = '<option value="">Выберите ученика...</option>' +
      students.map(s => `<option value="${s.max_user_id}">${escapeHtml(s.display_name)} (${s.xp} XP, ${s.balance_spasibki} спасибок)</option>`).join("");
  } catch (err) {
    selectElem.innerHTML = '<option value="">Ошибка загрузки учеников</option>';
  }
}

async function handleReviewClaim(claimId, decision) {
  try {
    const res = await fetch("/api/teacher/review-claim", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-User-Id": currentUserId,
      },
      body: JSON.stringify({ claim_id: claimId, decision: decision })
    });

    if (!res.ok) throw new Error("Ошибка обновления");
    showToast(decision === "approved" ? "Заявка одобрена!" : "Заявка отклонена");
    loadTeacherData();

  } catch (err) {
    showToast("Не удалось обработать заявку");
  }
}

async function handleCreateReward(e) {
  e.preventDefault();
  const title = document.getElementById("rew-title").value.trim();
  const desc = document.getElementById("rew-desc").value.trim();
  const price = parseInt(document.getElementById("rew-price").value);
  const chatId = document.getElementById("rew-chat-select").value;

  if (!title || !desc || !price) return;

  try {
    const res = await fetch("/api/teacher/rewards", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-User-Id": currentUserId,
      },
      body: JSON.stringify({
        title: title,
        description: desc,
        cost_spasibki: price,
        chat_id: chatId !== "all" ? chatId : null,
      })
    });

    if (!res.ok) throw new Error("Ошибка публикации");
    showToast("Награда добавлена в лавку!");
    document.getElementById("create-reward-form").reset();
    document.getElementById("rew-price").value = 30;

  } catch (err) {
    showToast("Не удалось добавить награду");
  }
}

async function handleAwardTeacherBadge() {
  const studentId = document.getElementById("teacher-award-student").value;
  if (!studentId) {
    showToast("Выберите ученика из списка");
    return;
  }

  try {
    const res = await fetch("/api/teacher/award-badge", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-User-Id": currentUserId,
      },
      body: JSON.stringify({ student_id: studentId, badge_code: "teacher_choice" })
    });

    if (!res.ok) throw new Error("Ошибка награждения");
    showToast("Бейдж «Выбор учителя» успешно вручён!");
    loadTeacherData();

  } catch (err) {
    showToast("Не удалось вручить награду");
  }
}

function showToast(text) {
  const toast = document.getElementById("toast");
  toast.textContent = text;
  toast.classList.remove("hidden");
  clearTimeout(window.__toastTimeout);
  window.__toastTimeout = setTimeout(() => {
    toast.classList.add("hidden");
  }, 2600);
}

function escapeHtml(str) {
  if (!str) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}
