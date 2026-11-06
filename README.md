# 🎓 Лига Помощников | Чат-бот и WebApp для мессенджера MAX

Проект разработан для Всероссийского хакатона **«Идея фикс»** (номинация *«Продвинутая разработка»*).  
**Команда:** Команда11 (ГАУДО РК «РЦДО», центр цифрового образования детей «IT-куб», г. Сыктывкар).

---

## 📌 О проекте

**«Лига Помощников»** — геймифицированный сервис для учебных чатов классов в мессенджере MAX:
* Школьники задают вопросы по учёбе командой `/ask`.
* Одноклассники объясняют решение и получают опыт (XP) и валюту («спасибки» 🪙).
* Растёт звание ученика: от *Новичка* до *Легенды школы*.
* Учитель модерирует класс и выставляет в «Школьную лавку» реальные награды (освобождение от одного ДЗ, выбор соседа по парте и др.), которые ученики покупают за спасибки.

---

## 🔗 Ссылки

* **Чат-бот в MAX:** [https://max.ru/ideya_fiks_59_bot](https://max.ru/ideya_fiks_59_bot) (`@ideya_fiks_59_bot`)
* **WebApp (Мини-приложение):** [https://itcubefor.ru/app](https://itcubefor.ru/app)

---

## 🛠 Стек технологий

* **Чат-бот:** Python 3.12, асинхронная библиотека `maxapi`
* **Бэкенд:** `FastAPI`, `Uvicorn`
* **База данных:** `SQLite` через `SQLModel`
* **Фронтенд (WebApp):** Чистый `HTML5`, `CSS3`, `Vanilla JavaScript` (без тяжёлых фреймворков и сборщиков)
* **SDK:** Официальный `max-web-app.js`
* **Контейнеризация:** `Docker`, `docker-compose`

---

## 🚀 Быстрый запуск

### Вариант 1: Запуск через Python

1. Установите зависимости:
```bash
pip install -r requirements.txt
```

2. Запустите единый сервис:
```bash
python main.py
```
Сервер поднимет базу данных, раздаст WebApp по адресу `http://localhost:8000/app` и запустит фонового бота в MAX.

---

### Вариант 2: Запуск через Docker

1. Сборка и запуск контейнера:
```bash
docker compose up -d --build
```

2. Просмотр логов:
```bash
docker compose logs -f
```

3. Остановка:
```bash
docker compose down
```

---

## 🌐 Развёртывание на сервере (Nginx + SSL)

Конфигурация Nginx для проксирования на домене `itcubefor.ru`:

```nginx
server {
    server_name itcubefor.ru;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    listen 443 ssl;
    ssl_certificate /etc/letsencrypt/live/itcubefor.ru/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/itcubefor.ru/privkey.pem;
}
```

---

## 🎮 Команды бота в MAX

* `/start` — приветствие и запуск мини-приложения
* `/ask [текст]` — задать вопрос по учёбе классу
* `/me` — профиль, уровень, прогресс-бар XP и баланс спасибок
* `/top` — доска лидеров класса
* `/help` — правила Лиги и подсказки
* `/teacher учитель11` — активация роли учителя

