#!/bin/bash

# === RemnaWave Bot Setup & Management Script ===

SERVICE_NAME="remnawave-bot"
BOT_FILE="main.py"
ENV_FILE=".env"
VENV_DIR="venv"

# Select language
echo "🌐 Select language / Выберите язык:"
echo "1) English"
echo "2) Русский"
read -p ">>> " LANG_CHOICE

case $LANG_CHOICE in
  2) LANG_CODE="ru" ;;
  *) LANG_CODE="en" ;;
esac

# === Internationalized messages ===
msg() {
  case $1 in
    check_python) [[ $LANG_CODE == "ru" ]] && echo "🔍 Проверка Python..." || echo "🔍 Checking Python..." ;;
    python_missing) [[ $LANG_CODE == "ru" ]] && echo "❌ Python 3 не установлен. Установите Python 3.8+." || echo "❌ Python 3 is not installed. Please install Python 3.8+." ;;
    version) echo "📋 Python version: $PYTHON_VERSION" ;;
    creating_venv) [[ $LANG_CODE == "ru" ]] && echo "🔧 Создание виртуального окружения..." || echo "🔧 Creating virtual environment..." ;;
    activating_venv) [[ $LANG_CODE == "ru" ]] && echo "🔧 Активация виртуального окружения..." || echo "🔧 Activating virtual environment..." ;;
    upgrading_pip) [[ $LANG_CODE == "ru" ]] && echo "📦 Обновление pip..." || echo "📦 Upgrading pip..." ;;
    installing_requirements) [[ $LANG_CODE == "ru" ]] && echo "📦 Установка зависимостей..." || echo "📦 Installing requirements..." ;;
    env_missing) [[ $LANG_CODE == "ru" ]] && echo "⚠️  Файл .env не найден!" || echo "⚠️  .env file not found!" ;;
    env_created) [[ $LANG_CODE == "ru" ]] && echo "📋 Создан .env файл. Пожалуйста, отредактируйте его." || echo "📋 .env created. Please edit it." ;;
    validating) [[ $LANG_CODE == "ru" ]] && echo "🔍 Проверка конфигурации..." || echo "🔍 Validating configuration..." ;;
    config_ok) [[ $LANG_CODE == "ru" ]] && echo "✅ Конфигурация прошла проверку." || echo "✅ Configuration validated successfully." ;;
    creating_service) [[ $LANG_CODE == "ru" ]] && echo "🔧 Создание systemd-сервиса..." || echo "🔧 Creating systemd service..." ;;
    service_created) [[ $LANG_CODE == "ru" ]] && echo "✅ Сервис создан и включён." || echo "✅ Service created and enabled." ;;
    start_prompt) [[ $LANG_CODE == "ru" ]] && echo "🚀 Запуск бота..." || echo "🚀 Starting the bot..." ;;
    already_running) [[ $LANG_CODE == "ru" ]] && echo "🟢 Бот уже запущен!" || echo "🟢 Bot is already running!" ;;
    last_logs) [[ $LANG_CODE == "ru" ]] && echo "📄 Последние 20 строк лога:" || echo "📄 Last 20 log lines:" ;;
    action_menu)
      [[ $LANG_CODE == "ru" ]] && {
        echo -e "\nЧто вы хотите сделать?"
        echo "1) Остановить бота"
        echo "2) Перезапустить бота"
        echo "3) Выйти"
      } || {
        echo -e "\nWhat would you like to do?"
        echo "1) Stop the bot"
        echo "2) Restart the bot"
        echo "3) Exit"
      }
      ;;
    restarting) [[ $LANG_CODE == "ru" ]] && echo "🔄 Перезапуск бота..." || echo "🔄 Restarting bot..." ;;
    completed)
      echo ""
      [[ $LANG_CODE == "ru" ]] && {
        echo "🎉 Установка завершена!"
        echo "📋 Возможности бота:"
        echo "   ✅ Мультиязычный интерфейс"
        echo "   ✅ Управление балансом и подписками"
        echo "   ✅ Промокоды и поддержка"
        echo ""
      } || {
        echo "🎉 Installation complete!"
        echo "📋 Bot Features:"
        echo "   ✅ Multi-language support"
        echo "   ✅ Subscription and balance management"
        echo "   ✅ Promocode system and support"
        echo ""
      }
      ;;
  esac
}

# === Check if bot is running ===
if systemctl is-active --quiet "$SERVICE_NAME"; then
  msg already_running
  msg last_logs
  sudo journalctl -u "$SERVICE_NAME" -n 20 --no-pager
  msg action_menu
  read -p ">>> " choice
  case $choice in
    1)
      [[ $LANG_CODE == "ru" ]] && echo "🛑 Остановка бота..." || echo "🛑 Stopping the bot..."
      sudo systemctl stop "$SERVICE_NAME"
      [[ $LANG_CODE == "ru" ]] && echo "✅ Бот остановлен." || echo "✅ Bot stopped."
      exit 0
      ;;
    2)
      msg restarting
      sudo systemctl restart "$SERVICE_NAME"
      [[ $LANG_CODE == "ru" ]] && echo "✅ Бот перезапущен." || echo "✅ Bot restarted."
      exit 0
      ;;
    3)
      [[ $LANG_CODE == "ru" ]] && echo "👋 Выход." || echo "👋 Exiting."
      exit 0
      ;;
    *)
      [[ $LANG_CODE == "ru" ]] && echo "⚠️ Неверный выбор." || echo "⚠️ Invalid choice."
      exit 1
      ;;
  esac
fi

# === Install/Setup ===
msg check_python
if ! command -v python3 &> /dev/null; then
  msg python_missing
  exit 1
fi

PYTHON_VERSION=$(python3 -c 'import sys; print(".".join(map(str, sys.version_info[:2])))')
msg version

if [ ! -d "$VENV_DIR" ]; then
  msg creating_venv
  python3 -m venv "$VENV_DIR"
fi

msg activating_venv
source "$VENV_DIR/bin/activate"

msg upgrading_pip
pip install --upgrade pip

msg installing_requirements
pip install -r requirements.txt

# Create .env if needed
if [ ! -f "$ENV_FILE" ]; then
  msg env_missing
  cat > "$ENV_FILE" <<EOF
# Bot Configuration
BOT_TOKEN=

# RemnaWave API Configuration
REMNAWAVE_URL=
REMNAWAVE_MODE=
REMNAWAVE_TOKEN=

# Admin Configuration (comma-separated list of Telegram user IDs)
ADMIN_IDS=

# Support Configuration
SUPPORT_USERNAME=support

# Database Configuration (optional, defaults to SQLite)
# DATABASE_URL=sqlite+aiosqlite:///bot.db

# Trial Configuration
TRIAL_ENABLED=true
TRIAL_DURATION_DAYS=3
TRIAL_TRAFFIC_GB=2
TRIAL_SQUAD_UUID=
TRIAL_PRICE=0.0
EOF
  msg env_created
  echo ""
  read -p "🔧 Press Enter after configuring .env / Нажмите Enter после настройки .env..."
fi

# Load env
export $(grep -v '^#' "$ENV_FILE" | xargs)

msg validating

if [[ -z "$BOT_TOKEN" || "$BOT_TOKEN" == "your_telegram_bot_token_here" ]]; then
  echo "❌ BOT_TOKEN not set properly."
  exit 1
fi

if [[ -z "$REMNAWAVE_URL" || "$REMNAWAVE_URL" == "https://your-panel.com" ]]; then
  echo "❌ REMNAWAVE_URL not set properly."
  exit 1
fi

if [[ -z "$REMNAWAVE_TOKEN" || "$REMNAWAVE_TOKEN" == "your_jwt_token_here" ]]; then
  echo "❌ REMNAWAVE_TOKEN not set properly."
  exit 1
fi

msg config_ok

# Create systemd service
read -p "🔧 Create systemd service? (y/n): " CREATE_SERVICE
if [[ "$CREATE_SERVICE" == "y" || "$CREATE_SERVICE" == "Y" ]]; then
  msg creating_service
  SERVICE_FILE="/etc/systemd/system/$SERVICE_NAME.service"
  CURRENT_DIR=$(pwd)
  CURRENT_USER=$(whoami)
  sudo tee "$SERVICE_FILE" > /dev/null <<EOF
[Unit]
Description=RemnaWave Bot
After=network.target

[Service]
Type=simple
User=$CURRENT_USER
WorkingDirectory=$CURRENT_DIR
Environment=PATH=$CURRENT_DIR/$VENV_DIR/bin
EnvironmentFile=$CURRENT_DIR/.env
ExecStart=$CURRENT_DIR/$VENV_DIR/bin/python3 $BOT_FILE
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

  sudo systemctl daemon-reload
  sudo systemctl enable "$SERVICE_NAME"
  msg service_created
fi

msg completed
msg start_prompt
python3 "$BOT_FILE"
