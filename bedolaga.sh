#!/bin/bash

# Bedolaga Bot Installer & Manager
# Автоматический установщик и менеджер для Telegram бота
# Версия: 1.1

set -euo pipefail

# Цвета для вывода
readonly RED='\033[0;31m'
readonly GREEN='\033[0;32m'
readonly YELLOW='\033[0;33m'
readonly BLUE='\033[0;34m'
readonly PURPLE='\033[0;35m'
readonly CYAN='\033[0;36m'
readonly WHITE='\033[1;37m'
readonly NC='\033[0m' # No Color

# Константы
readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly PROJECT_DIR="/opt/bedolaga-bot"
readonly SERVICE_NAME="bedolaga-bot"
readonly DOCKER_COMPOSE_FILE="$PROJECT_DIR/docker-compose.yml"
readonly ENV_FILE="$PROJECT_DIR/.env"
readonly CONFIG_FILE="$PROJECT_DIR/.installer_config"

# Проверка прав root
check_root() {
    if [[ $EUID -ne 0 ]]; then
        echo -e "${RED}❌ Этот скрипт должен запускаться от имени root${NC}"
        echo "Используйте: sudo $0"
        exit 1
    fi
}

# Вывод заголовка
print_header() {
    clear
    echo -e "${PURPLE}╔══════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${PURPLE}║${NC}${WHITE}                    BEDOLAGA BOT INSTALLER                    ${NC}${PURPLE}║${NC}"
    echo -e "${PURPLE}║${NC}${CYAN}              Автоматический установщик и менеджер           ${NC}${PURPLE}║${NC}"
    echo -e "${PURPLE}╚══════════════════════════════════════════════════════════════╝${NC}"
    echo
}

# Логирование
log() {
    echo -e "${GREEN}[$(date '+%Y-%m-%d %H:%M:%S')]${NC} $1"
}

error() {
    echo -e "${RED}[ERROR]${NC} $1" >&2
}

warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

# Проверка системы
check_system() {
    log "🔍 Проверка системы..."
    
    if ! command -v lsb_release &> /dev/null; then
        error "lsb_release не найден. Установите пакет lsb-release"
        exit 1
    fi
    
    local distro=$(lsb_release -si)
    local version=$(lsb_release -sr)
    
    if [[ "$distro" != "Ubuntu" ]]; then
        error "Поддерживается только Ubuntu. Найдено: $distro"
        exit 1
    fi
    
    log "✅ Система: $distro $version"
}

# Обновление системы
update_system() {
    log "🔄 Обновление системы..."
    
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -y
    apt-get upgrade -y
    apt-get install -y curl wget git jq htop nano vim systemctl
    
    log "✅ Система обновлена"
}

# Установка Docker
install_docker() {
    if command -v docker &> /dev/null; then
        log "✅ Docker уже установлен"
        return 0
    fi
    
    log "🐳 Установка Docker..."
    
    # Установка зависимостей
    apt-get install -y \
        apt-transport-https \
        ca-certificates \
        curl \
        gnupg \
        lsb-release
    
    # Добавление GPG ключа Docker
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /usr/share/keyrings/docker-archive-keyring.gpg
    
    # Добавление репозитория
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/docker-archive-keyring.gpg] https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" | tee /etc/apt/sources.list.d/docker.list > /dev/null
    
    # Установка Docker
    apt-get update -y
    apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
    
    # Запуск и автозапуск
    systemctl start docker
    systemctl enable docker
    
    log "✅ Docker установлен"
}

# Поиск веб-серверов
detect_webservers() {
    log "🔍 Поиск веб-серверов..."
    
    local webservers=()
    
    # Поиск Nginx
    if systemctl is-active --quiet nginx 2>/dev/null; then
        webservers+=("nginx-system")
        log "✅ Найден: Nginx (системный)"
    fi
    
    # Поиск Caddy
    if systemctl is-active --quiet caddy 2>/dev/null; then
        webservers+=("caddy-system")
        log "✅ Найден: Caddy (системный)"
    fi
    
    # Поиск Docker контейнеров с веб-серверами
    if command -v docker &> /dev/null; then
        local containers=$(docker ps --format "table {{.Names}}\t{{.Image}}" | grep -E "(nginx|caddy)" || true)
        if [[ -n "$containers" ]]; then
            while IFS= read -r line; do
                if [[ "$line" =~ nginx ]]; then
                    webservers+=("nginx-docker:$(echo "$line" | awk '{print $1}')")
                    log "✅ Найден: Nginx в Docker ($(echo "$line" | awk '{print $1}'))"
                elif [[ "$line" =~ caddy ]]; then
                    webservers+=("caddy-docker:$(echo "$line" | awk '{print $1}')")
                    log "✅ Найден: Caddy в Docker ($(echo "$line" | awk '{print $1}'))"
                fi
            done <<< "$containers"
        fi
    fi
    
    if [[ ${#webservers[@]} -eq 0 ]]; then
        warning "Веб-серверы не найдены"
        return 1
    fi
    
    # Сохраняем найденные веб-серверы
    printf '%s\n' "${webservers[@]}" > /tmp/detected_webservers
    return 0
}

# Настройка веб-сервера
configure_webserver() {
    local domain="$1"
    
    if [[ ! -f /tmp/detected_webservers ]]; then
        warning "Веб-серверы не найдены. Пропускаем настройку."
        return 0
    fi
    
    local webservers=($(cat /tmp/detected_webservers))
    
    if [[ ${#webservers[@]} -eq 1 ]]; then
        local selected_server="${webservers[0]}"
        log "🔧 Автоматически выбран: $selected_server"
    else
        echo -e "${CYAN}Найдено несколько веб-серверов:${NC}"
        for i in "${!webservers[@]}"; do
            echo "  $((i+1))) ${webservers[i]}"
        done
        
        while true; do
            read -p "Выберите веб-сервер для настройки (1-${#webservers[@]}): " choice
            if [[ "$choice" =~ ^[0-9]+$ ]] && [[ "$choice" -ge 1 ]] && [[ "$choice" -le ${#webservers[@]} ]]; then
                selected_server="${webservers[$((choice-1))]}"
                break
            fi
            echo "Неверный выбор. Попробуйте еще раз."
        done
    fi
    
    configure_selected_webserver "$selected_server" "$domain"
}

# Настройка выбранного веб-сервера
configure_selected_webserver() {
    local server="$1"
    local domain="$2"
    
    local server_type=$(echo "$server" | cut -d':' -f1)
    local container_name=$(echo "$server" | cut -d':' -f2 -s)
    
    case "$server_type" in
        "nginx-system")
            configure_nginx_system "$domain"
            ;;
        "nginx-docker")
            configure_nginx_docker "$container_name" "$domain"
            ;;
        "caddy-system")
            configure_caddy_system "$domain"
            ;;
        "caddy-docker")
            configure_caddy_docker "$container_name" "$domain"
            ;;
        *)
            error "Неизвестный тип сервера: $server_type"
            ;;
    esac
}

# Настройка Nginx (системный)
configure_nginx_system() {
    local domain="$1"
    local config_file="/etc/nginx/sites-available/$domain"
    
    log "🔧 Настройка Nginx для домена: $domain"
    
    # Создаем или обновляем конфигурацию
    if [[ -f "$config_file" ]]; then
        # Добавляем location для webhook
        if ! grep -q "/tribute-webhook" "$config_file"; then
            sed -i '/server_name/a\\n    # Tribute webhook\n    location /tribute-webhook {\n        proxy_pass http://127.0.0.1:8081;\n        proxy_set_header Host $host;\n        proxy_set_header X-Real-IP $remote_addr;\n        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;\n    }\n\n    location /webhook-health {\n        proxy_pass http://127.0.0.1:8081/health;\n        proxy_set_header Host $host;\n        proxy_set_header X-Real-IP $remote_addr;\n    }' "$config_file"
        fi
    else
        # Создаем новую конфигурацию
        cat > "$config_file" << EOF
server {
    listen 80;
    server_name $domain;

    # Tribute webhook
    location /tribute-webhook {
        proxy_pass http://127.0.0.1:8081;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
    }

    location /webhook-health {
        proxy_pass http://127.0.0.1:8081/health;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
    }

    location / {
        root /var/www/html;
        index index.html index.htm;
        try_files \$uri \$uri/ =404;
    }
}
EOF
        
        # Активируем сайт
        ln -sf "$config_file" "/etc/nginx/sites-enabled/"
    fi
    
    # Проверяем конфигурацию и перезагружаем
    if nginx -t; then
        systemctl reload nginx
        log "✅ Nginx настроен и перезагружен"
    else
        error "Ошибка в конфигурации Nginx"
        return 1
    fi
}

# Настройка Caddy (Docker)
configure_caddy_docker() {
    local container_name="$1"
    local domain="$2"
    
    log "🔧 Настройка Caddy Docker для домена: $domain"
    
    # Находим директорию с Caddyfile
    local caddy_dir=$(docker inspect "$container_name" | jq -r '.[0].Mounts[] | select(.Destination == "/etc/caddy") | .Source' | head -1)
    
    if [[ -z "$caddy_dir" || "$caddy_dir" == "null" ]]; then
        error "Не удалось найти директорию с Caddyfile для контейнера $container_name"
        return 1
    fi
    
    local caddyfile="$caddy_dir/Caddyfile"
    
    if [[ ! -f "$caddyfile" ]]; then
        error "Caddyfile не найден: $caddyfile"
        return 1
    fi
    
    # Создаем резервную копию
    cp "$caddyfile" "$caddyfile.backup.$(date +%s)"
    
    # Проверяем, есть ли уже настройка для домена
    if grep -q "^https://$domain" "$caddyfile" || grep -q "^$domain" "$caddyfile"; then
        log "⚠️  Конфигурация для домена $domain уже существует"
        
        # Проверяем наличие webhook настроек
        if ! grep -q "/tribute-webhook" "$caddyfile"; then
            # Добавляем webhook настройки в существующий блок
            sed -i "/^https:\/\/$domain\|^$domain/,/^}/ {
                /handle.*{/a\\
    # Tribute webhook endpoint\\
    handle /tribute-webhook* {\\
        reverse_proxy localhost:8081 {\\
            header_up Host {host}\\
            header_up X-Real-IP {remote_host}\\
        }\\
    }\\
    \\
    # Health check для webhook сервиса\\
    handle /webhook-health {\\
        reverse_proxy localhost:8081/health {\\
            header_up Host {host}\\
            header_up X-Real-IP {remote_host}\\
        }\\
    }
            }" "$caddyfile"
            
            log "✅ Добавлены webhook настройки в существующую конфигурацию"
        else
            log "✅ Webhook настройки уже присутствуют"
        fi
    else
        # Создаем новую конфигурацию для домена
        cat >> "$caddyfile" << EOF

https://$domain {
    # Tribute webhook endpoint
    handle /tribute-webhook* {
        reverse_proxy localhost:8081 {
            header_up Host {host}
            header_up X-Real-IP {remote_host}
        }
    }
    
    # Health check для webhook сервиса
    handle /webhook-health {
        reverse_proxy localhost:8081/health {
            header_up Host {host}
            header_up X-Real-IP {remote_host}
        }
    }
    
    # Остальные запросы обрабатываются как обычно
    handle {
        root * /var/www/html
        try_files {path} /index.html
        file_server
    }
    
    log {
        output file /var/log/caddy/access.log {
            roll_size 10MB
            roll_keep 5
            roll_keep_for 720h
            roll_compression gzip
        }
        level ERROR
    }
}
EOF
        log "✅ Добавлена новая конфигурация для домена $domain"
    fi
    
    # Перезагружаем Caddy
    if docker exec "$container_name" caddy reload --config /etc/caddy/Caddyfile; then
        log "✅ Caddy перезагружен"
    else
        error "Ошибка при перезагрузке Caddy"
        # Восстанавливаем из резервной копии
        cp "$caddyfile.backup.$(date +%s)" "$caddyfile"
        return 1
    fi
}

# Интерактивная настройка .env
configure_env() {
    log "⚙️  Интерактивная настройка параметров бота..."
    
    # Создаем временный файл для .env
    local temp_env="/tmp/bedolaga.env"
    
    echo "# Bedolaga Bot Configuration" > "$temp_env"
    echo "# Generated by installer on $(date)" >> "$temp_env"
    echo "" >> "$temp_env"
    
    # Bot Configuration
    echo -e "${CYAN}=== ОСНОВНЫЕ НАСТРОЙКИ БОТА ===${NC}"
    
    read -p "🤖 Введите токен бота (BOT_TOKEN): " bot_token
    echo "BOT_TOKEN=$bot_token" >> "$temp_env"
    
    read -p "📝 Введите username бота без @ (BOT_USERNAME): " bot_username
    echo "BOT_USERNAME=$bot_username" >> "$temp_env"
    
    echo "" >> "$temp_env"
    echo "# Referral System" >> "$temp_env"
    
    read -p "💰 Награда пригласившему (REFERRAL_FIRST_REWARD): " ref_first
    echo "REFERRAL_FIRST_REWARD=$ref_first" >> "$temp_env"
    
    read -p "🎁 Награда рефералу (REFERRAL_REFERRED_BONUS): " ref_bonus
    echo "REFERRAL_REFERRED_BONUS=$ref_bonus" >> "$temp_env"
    
    read -p "💵 Сумма пополнения для выплаты (REFERRAL_THRESHOLD): " ref_threshold
    echo "REFERRAL_THRESHOLD=$ref_threshold" >> "$temp_env"
    
    read -p "📊 Процент за пополнение (0.20 = 20%) (REFERRAL_PERCENTAGE): " ref_percentage
    echo "REFERRAL_PERCENTAGE=$ref_percentage" >> "$temp_env"
    
    # RemnaWave API
    echo "" >> "$temp_env"
    echo "# RemnaWave API Configuration" >> "$temp_env"
    
    read -p "🌐 URL панели RemnaWave (https://panel.example.com): " remnawave_url
    echo "REMNAWAVE_URL=$remnawave_url" >> "$temp_env"
    
    read -p "🔑 Токен RemnaWave API: " remnawave_token
    echo "REMNAWAVE_TOKEN=$remnawave_token" >> "$temp_env"
    
    # Admin Configuration
    echo "" >> "$temp_env"
    echo "# Admin Configuration" >> "$temp_env"
    
    read -p "👑 ID администраторов (через запятую): " admin_ids
    echo "ADMIN_IDS=$admin_ids" >> "$temp_env"
    
    # Support Configuration
    echo "" >> "$temp_env"
    echo "# Support Configuration" >> "$temp_env"
    
    read -p "🆘 Username для поддержки без @: " support_username
    echo "SUPPORT_USERNAME=$support_username" >> "$temp_env"
    
    # Trial Configuration
    echo -e "${CYAN}=== НАСТРОЙКИ ТРИАЛА ===${NC}"
    
    read -p "🆓 Включить триал? (true/false): " trial_enabled
    echo "TRIAL_ENABLED=$trial_enabled" >> "$temp_env"
    
    if [[ "$trial_enabled" == "true" ]]; then
        read -p "📅 Дней триала: " trial_days
        echo "TRIAL_DURATION_DAYS=$trial_days" >> "$temp_env"
        
        read -p "📊 Лимит трафика ГБ: " trial_traffic
        echo "TRIAL_TRAFFIC_GB=$trial_traffic" >> "$temp_env"
        
        read -p "🏷️  UUID сквада из панели: " trial_squad
        echo "TRIAL_SQUAD_UUID=$trial_squad" >> "$temp_env"
        
        echo "TRIAL_PRICE=0.0" >> "$temp_env"
        
        read -p "🔔 Уведомления об истечении триала? (true/false): " trial_notif
        echo "TRIAL_NOTIFICATION_ENABLED=$trial_notif" >> "$temp_env"
        
        if [[ "$trial_notif" == "true" ]]; then
            read -p "⏰ Через сколько часов уведомлять: " notif_hours
            echo "TRIAL_NOTIFICATION_HOURS_AFTER=$notif_hours" >> "$temp_env"
            
            read -p "🔄 Через сколько часов повторить: " notif_window
            echo "TRIAL_NOTIFICATION_HOURS_WINDOW=$notif_window" >> "$temp_env"
        fi
    fi
    
    # Monitor Service
    echo "" >> "$temp_env"
    echo "# Monitor Service Settings" >> "$temp_env"
    
    read -p "⏱️  Интервал проверки в секундах (3600 = час): " monitor_interval
    echo "MONITOR_CHECK_INTERVAL=$monitor_interval" >> "$temp_env"
    
    read -p "🌅 Час ежедневной проверки (0-23): " daily_hour
    echo "MONITOR_DAILY_CHECK_HOUR=$daily_hour" >> "$temp_env"
    
    read -p "⚠️  За сколько дней предупреждать: " warning_days
    echo "MONITOR_WARNING_DAYS=$warning_days" >> "$temp_env"
    
    read -p "🗑️  Удалять триал через дней: " delete_trial
    echo "DELETE_EXPIRED_TRIAL_DAYS=$delete_trial" >> "$temp_env"
    
    read -p "🗑️  Удалять обычные через дней: " delete_regular
    echo "DELETE_EXPIRED_REGULAR_DAYS=$delete_regular" >> "$temp_env"
    
    read -p "🤖 Автоудаление? (true/false): " auto_delete
    echo "AUTO_DELETE_ENABLED=$auto_delete" >> "$temp_env"
    
    # Lucky Game
    echo "" >> "$temp_env"
    echo "# Lucky Game Settings" >> "$temp_env"
    
    read -p "🎲 Включить игру удачи? (true/false): " lucky_enabled
    echo "LUCKY_GAME_ENABLED=$lucky_enabled" >> "$temp_env"
    
    if [[ "$lucky_enabled" == "true" ]]; then
        read -p "💰 Размер награды в рублях: " lucky_reward
        echo "LUCKY_GAME_REWARD=$lucky_reward" >> "$temp_env"
        
        read -p "🔢 Всего чисел для выбора: " lucky_numbers
        echo "LUCKY_GAME_NUMBERS=$lucky_numbers" >> "$temp_env"
        
        read -p "🎯 Количество выигрышных: " lucky_winning
        echo "LUCKY_GAME_WINNING_COUNT=$lucky_winning" >> "$temp_env"
    fi
    
    # Telegram Stars
    echo -e "${CYAN}=== TELEGRAM STARS ===${NC}"
    echo "" >> "$temp_env"
    echo "# Telegram Stars Configuration" >> "$temp_env"
    
    read -p "⭐ Включить Telegram Stars? (true/false): " stars_enabled
    echo "STARS_ENABLED=$stars_enabled" >> "$temp_env"
    
    if [[ "$stars_enabled" == "true" ]]; then
        declare -a star_amounts=("100" "150" "250" "350" "500" "750" "1000")
        
        for amount in "${star_amounts[@]}"; do
            read -p "💫 $amount звёзд = сколько рублей: " rate
            echo "STARS_${amount}_RATE=$rate" >> "$temp_env"
        done
    fi
    
    # Tribute
    echo -e "${CYAN}=== TRIBUTE ДОНАТЫ ===${NC}"
    echo "" >> "$temp_env"
    echo "# Tribute Configuration" >> "$temp_env"
    
    read -p "🎁 Включить Tribute? (true/false): " tribute_enabled
    echo "TRIBUTE_ENABLED=$tribute_enabled" >> "$temp_env"
    
    if [[ "$tribute_enabled" == "true" ]]; then
        read -p "🔑 API ключ Tribute: " tribute_api
        echo "TRIBUTE_API_KEY=$tribute_api" >> "$temp_env"
        
        read -p "🚪 Порт webhook (8081): " webhook_port
        webhook_port=${webhook_port:-8081}
        echo "TRIBUTE_WEBHOOK_PORT=$webhook_port" >> "$temp_env"
        
        echo "TRIBUTE_WEBHOOK_PATH=/tribute-webhook" >> "$temp_env"
        
        read -p "🔗 Ссылка на донат в Tribute: " tribute_link
        echo "TRIBUTE_DONATE_LINK=$tribute_link" >> "$temp_env"
        
        # Спрашиваем домен для webhook
        echo -e "${CYAN}=== НАСТРОЙКА ВЕБХУКА ===${NC}"
        read -p "🌐 Домен для webhook (example.com): " webhook_domain
        
        # Настраиваем веб-сервер
        configure_webserver "$webhook_domain"
    fi
    
    # Копируем в финальное место
    cp "$temp_env" "$ENV_FILE"
    log "✅ Конфигурация сохранена"
}

# Создание структуры проекта
create_project_structure() {
    log "📁 Создание структуры проекта..."
    
    mkdir -p "$PROJECT_DIR"/{logs,data,backups}
    chown -R root:docker "$PROJECT_DIR" 2>/dev/null || chown -R root:root "$PROJECT_DIR"
    chmod -R 755 "$PROJECT_DIR"
    
    log "✅ Структура проекта создана"
}

# Создание docker-compose.yml
create_docker_compose() {
    log "🐳 Создание docker-compose.yml..."
    
    # Генерируем случайный пароль для БД
    local db_password=$(openssl rand -base64 32 | tr -d "=+/" | cut -c1-25)
    
    cat > "$DOCKER_COMPOSE_FILE" << 'EOF'
services:
  # PostgreSQL Database
  postgres:
    image: postgres:15-alpine
    container_name: bedolaga_bot_db
    restart: unless-stopped
    environment:
      POSTGRES_DB: bedolaga_bot
      POSTGRES_USER: bedolaga_user
      POSTGRES_PASSWORD: ${DB_PASSWORD}
      POSTGRES_INITDB_ARGS: "--encoding=UTF-8 --lc-collate=C --lc-ctype=C"
    volumes:
      - postgres_data:/var/lib/postgresql/data
    networks:
      - bot_network
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U bedolaga_user -d bedolaga_bot"]
      interval: 15s
      timeout: 10s
      retries: 5
      start_period: 30s
  
  # Bedolaga Bot
  bot:
    image: fr1ngg/remnawave-bedolaga-telegram-bot:latest
    container_name: bedolaga_bot
    restart: unless-stopped
    depends_on:
      postgres:
        condition: service_healthy
    env_file:
      - .env
    environment:
      DATABASE_URL: postgresql+asyncpg://bedolaga_user:${DB_PASSWORD}@postgres:5432/bedolaga_bot
    volumes:
      - ./logs:/app/logs
      - ./data:/app/data
    ports:
      - "${TRIBUTE_WEBHOOK_PORT:-8081}:${TRIBUTE_WEBHOOK_PORT:-8081}"
    networks:
      - bot_network
    healthcheck:
      test: ["CMD-SHELL", "python -c 'print(\"Bot is running\")'"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 30s

volumes:
  postgres_data:
    driver: local

networks:
  bot_network:
    driver: bridge
    ipam:
      config:
        - subnet: 172.20.0.0/16
EOF
    
    # Добавляем пароль БД в .env
    echo "" >> "$ENV_FILE"
    echo "# Database Configuration" >> "$ENV_FILE"
    echo "DB_PASSWORD=$db_password" >> "$ENV_FILE"
    
    log "✅ Docker Compose файл создан"
}

# Создание systemd службы
create_systemd_service() {
    local create_service=""
    
    echo -e "${CYAN}Создать systemd службу для автозапуска? (y/n):${NC}"
    read -r create_service
    
    if [[ "$create_service" =~ ^[Yy]$ ]]; then
        log "⚙️  Создание systemd службы..."
        
        cat > "/etc/systemd/system/${SERVICE_NAME}.service" << EOF
[Unit]
Description=Bedolaga Telegram Bot
Requires=docker.service
After=docker.service

[Service]
Type=oneshot
RemainAfterExit=true
WorkingDirectory=$PROJECT_DIR
ExecStart=/usr/bin/docker compose up -d
ExecStop=/usr/bin/docker compose down
TimeoutStartSec=0

[Install]
WantedBy=multi-user.target
EOF
        
        systemctl daemon-reload
        systemctl enable "$SERVICE_NAME"
        
        log "✅ Systemd служба создана и включена"
        echo "DB_PASSWORD" >> "$CONFIG_FILE"
    fi
}

# Запуск бота
start_bot() {
    log "🚀 Запуск бота..."
    
    cd "$PROJECT_DIR"
    
    if docker compose up -d; then
        log "✅ Бот запущен успешно"
        
        # Ждем запуска и показываем статус
        sleep 5
        show_status
    else
        error "Ошибка запуска бота"
        return 1
    fi
}

# Показать статус
show_status() {
    echo -e "${CYAN}=== СТАТУС СЕРВИСОВ ===${NC}"
    
    cd "$PROJECT_DIR"
    docker compose ps
    
    echo -e "\n${CYAN}=== ЛОГИ БОТА (последние 20 строк) ===${NC}"
    docker compose logs --tail=20 bot
}

# Функции управления ботом
start_bot_service() {
    log "🚀 Запуск бота..."
    cd "$PROJECT_DIR"
    docker compose up -d
    log "✅ Бот запущен"
}

stop_bot_service() {
    log "⏹️  Остановка бота..."
    cd "$PROJECT_DIR"
    docker compose down
    log "✅ Бот остановлен"
}

restart_bot_service() {
    log "🔄 Перезапуск бота..."
    cd "$PROJECT_DIR"
    docker compose restart
    log "✅ Бот перезапущен"
}

# Просмотр логов
view_logs() {
    echo -e "${CYAN}Выберите режим просмотра логов:${NC}"
    echo "1) Последние 50 строк"
    echo "2) Последние 100 строк"
    echo "3) В реальном времени (Ctrl+C для выхода)"
    echo "4) Ошибки только"
    
    read -p "Ваш выбор (1-4): " log_choice
    
    cd "$PROJECT_DIR"
    
    case $log_choice in
        1)
            docker compose logs --tail=50 bot
            ;;
        2)
            docker compose logs --tail=100 bot
            ;;
        3)
            echo -e "${YELLOW}Нажмите Ctrl+C для выхода${NC}"
            docker compose logs -f bot
            ;;
        4)
            docker compose logs bot | grep -i error
            ;;
        *)
            echo "Неверный выбор"
            ;;
    esac
}

# Создание резервной копии
create_backup() {
    log "💾 Создание резервной копии БД..."
    
    local backup_name="bedolaga_backup_$(date +%Y%m%d_%H%M%S).sql"
    local backup_path="$PROJECT_DIR/backups/$backup_name"
    
    cd "$PROJECT_DIR"
    
    # Получаем пароль БД из .env
    local db_password=$(grep "^DB_PASSWORD=" .env | cut -d'=' -f2)
    
    if docker compose exec -T postgres pg_dump -U bedolaga_user -d bedolaga_bot > "$backup_path"; then
        log "✅ Резервная копия создана: $backup_name"
        
        # Сжимаем резервную копию
        gzip "$backup_path"
        log "✅ Резервная копия сжата: ${backup_name}.gz"
        
        # Удаляем старые резервные копии (оставляем последние 10)
        find "$PROJECT_DIR/backups" -name "*.sql.gz" -type f | sort -r | tail -n +11 | xargs rm -f
        
        echo -e "${GREEN}Резервная копия сохранена в: $PROJECT_DIR/backups/${backup_name}.gz${NC}"
    else
        error "Ошибка создания резервной копии"
        return 1
    fi
}

# Восстановление из резервной копии
restore_backup() {
    echo -e "${CYAN}=== ВОССТАНОВЛЕНИЕ ИЗ РЕЗЕРВНОЙ КОПИИ ===${NC}"
    
    local backups_dir="$PROJECT_DIR/backups"
    
    if [[ ! -d "$backups_dir" ]] || [[ -z "$(ls -A "$backups_dir"/*.sql.gz 2>/dev/null)" ]]; then
        error "Резервные копии не найдены в $backups_dir"
        return 1
    fi
    
    echo "Доступные резервные копии:"
    local backups=($(ls -1t "$backups_dir"/*.sql.gz))
    
    for i in "${!backups[@]}"; do
        local backup_file=$(basename "${backups[i]}")
        local backup_date=$(echo "$backup_file" | grep -o '[0-9]\{8\}_[0-9]\{6\}')
        local formatted_date=$(echo "$backup_date" | sed 's/\([0-9]\{4\}\)\([0-9]\{2\}\)\([0-9]\{2\}\)_\([0-9]\{2\}\)\([0-9]\{2\}\)\([0-9]\{2\}\)/\1-\2-\3 \4:\5:\6/')
        echo "  $((i+1))) $backup_file ($formatted_date)"
    done
    
    read -p "Выберите резервную копию для восстановления (1-${#backups[@]}): " backup_choice
    
    if [[ ! "$backup_choice" =~ ^[0-9]+$ ]] || [[ "$backup_choice" -lt 1 ]] || [[ "$backup_choice" -gt ${#backups[@]} ]]; then
        error "Неверный выбор"
        return 1
    fi
    
    local selected_backup="${backups[$((backup_choice-1))]}"
    
    echo -e "${RED}⚠️  ВНИМАНИЕ: Это действие полностью заменит текущую базу данных!${NC}"
    read -p "Вы уверены? Введите 'yes' для подтверждения: " confirm
    
    if [[ "$confirm" != "yes" ]]; then
        echo "Восстановление отменено"
        return 0
    fi
    
    log "🔄 Восстановление из $selected_backup..."
    
    cd "$PROJECT_DIR"
    
    # Останавливаем бота
    docker compose stop bot
    
    # Восстанавливаем БД
    if zcat "$selected_backup" | docker compose exec -T postgres psql -U bedolaga_user -d bedolaga_bot; then
        log "✅ База данных восстановлена"
        
        # Запускаем бота
        docker compose start bot
        log "✅ Бот запущен"
    else
        error "Ошибка восстановления базы данных"
        docker compose start bot
        return 1
    fi
}

# Редактирование конфигурации
edit_config() {
    echo -e "${CYAN}=== РЕДАКТИРОВАНИЕ КОНФИГУРАЦИИ ===${NC}"
    echo "1) Редактировать .env файл"
    echo "2) Редактировать docker-compose.yml"
    echo "3) Пересоздать .env интерактивно"
    
    read -p "Ваш выбор (1-3): " edit_choice
    
    case $edit_choice in
        1)
            if command -v nano &> /dev/null; then
                nano "$ENV_FILE"
            elif command -v vim &> /dev/null; then
                vim "$ENV_FILE"
            else
                error "Редактор не найден. Установите nano или vim"
                return 1
            fi
            
            read -p "Перезапустить бота для применения изменений? (y/n): " restart_choice
            if [[ "$restart_choice" =~ ^[Yy]$ ]]; then
                restart_bot_service
            fi
            ;;
        2)
            if command -v nano &> /dev/null; then
                nano "$DOCKER_COMPOSE_FILE"
            elif command -v vim &> /dev/null; then
                vim "$DOCKER_COMPOSE_FILE"
            else
                error "Редактор не найден. Установите nano или vim"
                return 1
            fi
            
            read -p "Пересоздать контейнеры для применения изменений? (y/n): " recreate_choice
            if [[ "$recreate_choice" =~ ^[Yy]$ ]]; then
                cd "$PROJECT_DIR"
                docker compose up -d --force-recreate
            fi
            ;;
        3)
            # Создаем резервную копию текущего .env
            cp "$ENV_FILE" "$ENV_FILE.backup.$(date +%s)"
            configure_env
            restart_bot_service
            ;;
        *)
            echo "Неверный выбор"
            ;;
    esac
}

# Диагностика
run_diagnostics() {
    echo -e "${CYAN}=== ДИАГНОСТИКА СИСТЕМЫ ===${NC}"
    
    log "🔍 Проверка Docker..."
    if systemctl is-active --quiet docker; then
        echo -e "  ✅ Docker: ${GREEN}Запущен${NC}"
    else
        echo -e "  ❌ Docker: ${RED}Не запущен${NC}"
    fi
    
    log "🔍 Проверка контейнеров..."
    cd "$PROJECT_DIR"
    
    local db_status=$(docker compose ps postgres --format "{{.State}}")
    local bot_status=$(docker compose ps bot --format "{{.State}}")
    
    echo -e "  PostgreSQL: ${db_status}"
    echo -e "  Bot: ${bot_status}"
    
    log "🔍 Проверка портов..."
    local webhook_port=$(grep "^TRIBUTE_WEBHOOK_PORT=" "$ENV_FILE" | cut -d'=' -f2)
    webhook_port=${webhook_port:-8081}
    
    if netstat -tuln | grep -q ":$webhook_port "; then
        echo -e "  ✅ Порт $webhook_port: ${GREEN}Открыт${NC}"
    else
        echo -e "  ❌ Порт $webhook_port: ${RED}Закрыт${NC}"
    fi
    
    log "🔍 Проверка места на диске..."
    df -h "$PROJECT_DIR"
    
    log "🔍 Проверка логов на ошибки..."
    local error_count=$(docker compose logs bot 2>/dev/null | grep -ci error || echo "0")
    echo -e "  Количество ошибок в логах: $error_count"
    
    if [[ "$error_count" -gt 0 ]]; then
        echo -e "${YELLOW}Последние ошибки:${NC}"
        docker compose logs bot | grep -i error | tail -5
    fi
    
    log "🔍 Проверка конфигурации..."
    if [[ -f "$ENV_FILE" ]]; then
        echo -e "  ✅ .env файл: ${GREEN}Существует${NC}"
        
        # Проверяем основные переменные
        local required_vars=("BOT_TOKEN" "REMNAWAVE_URL" "REMNAWAVE_TOKEN")
        for var in "${required_vars[@]}"; do
            if grep -q "^$var=" "$ENV_FILE" && [[ -n "$(grep "^$var=" "$ENV_FILE" | cut -d'=' -f2)" ]]; then
                echo -e "    ✅ $var: ${GREEN}Настроено${NC}"
            else
                echo -e "    ❌ $var: ${RED}Не настроено${NC}"
            fi
        done
    else
        echo -e "  ❌ .env файл: ${RED}Не найден${NC}"
    fi
}

# Автообновление
auto_update() {
    log "🔄 Автообновление бота..."
    
    cd "$PROJECT_DIR"
    
    # Создаем резервную копию перед обновлением
    create_backup
    
    # Скачиваем новый образ
    if docker compose pull bot; then
        log "✅ Новый образ загружен"
        
        # Перезапускаем с новым образом
        docker compose up -d bot
        
        log "✅ Бот обновлен и перезапущен"
        
        # Показываем статус
        sleep 3
        show_status
    else
        error "Ошибка загрузки нового образа"
        return 1
    fi
}

# Главное меню управления
management_menu() {
    while true; do
        clear
        print_header
        
        echo -e "${CYAN}=== МЕНЮ УПРАВЛЕНИЯ БОТОМ ===${NC}"
        echo
        echo "🚀 1) Запустить бота"
        echo "⏹️  2) Остановить бота"
        echo "🔄 3) Перезапустить бота"
        echo "📊 4) Показать статус"
        echo "📺 5) Просмотр логов"
        echo "💾 6) Создать резервную копию"
        echo "🔙 7) Восстановить из резервной копии"
        echo "✏️  8) Редактировать конфигурацию"
        echo "🩺 9) Диагностика"
        echo "🔄 10) Автообновление"
        echo "❌ 0) Выход"
        echo
        
        read -p "Выберите действие (0-10): " choice
        
        case $choice in
            1)
                start_bot_service
                read -p "Нажмите Enter для продолжения..."
                ;;
            2)
                stop_bot_service
                read -p "Нажмите Enter для продолжения..."
                ;;
            3)
                restart_bot_service
                read -p "Нажмите Enter для продолжения..."
                ;;
            4)
                show_status
                read -p "Нажмите Enter для продолжения..."
                ;;
            5)
                view_logs
                read -p "Нажмите Enter для продолжения..."
                ;;
            6)
                create_backup
                read -p "Нажмите Enter для продолжения..."
                ;;
            7)
                restore_backup
                read -p "Нажмите Enter для продолжения..."
                ;;
            8)
                edit_config
                ;;
            9)
                run_diagnostics
                read -p "Нажмите Enter для продолжения..."
                ;;
            10)
                auto_update
                read -p "Нажмите Enter для продолжения..."
                ;;
            0)
                echo -e "${GREEN}До свидания!${NC}"
                exit 0
                ;;
            *)
                echo -e "${RED}Неверный выбор. Попробуйте еще раз.${NC}"
                sleep 2
                ;;
        esac
    done
}

# Функция для настройки Nginx (системный)
configure_nginx_system() {
    local domain="$1"
    local config_file="/etc/nginx/sites-available/$domain"
    
    log "🔧 Настройка Nginx для домена: $domain"
    
    # Создаем или обновляем конфигурацию
    if [[ -f "$config_file" ]]; then
        # Добавляем location для webhook
        if ! grep -q "/tribute-webhook" "$config_file"; then
            sed -i '/server_name/a\\n    # Tribute webhook\n    location /tribute-webhook {\n        proxy_pass http://127.0.0.1:8081;\n        proxy_set_header Host $host;\n        proxy_set_header X-Real-IP $remote_addr;\n        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;\n    }\n\n    location /webhook-health {\n        proxy_pass http://127.0.0.1:8081/health;\n        proxy_set_header Host $host;\n        proxy_set_header X-Real-IP $remote_addr;\n    }' "$config_file"
            log "✅ Добавлены webhook настройки в существующую конфигурацию"
        else
            log "✅ Webhook настройки уже присутствуют"
        fi
    else
        # Создаем новую конфигурацию
        cat > "$config_file" << EOF
server {
    listen 80;
    server_name $domain;

    # Tribute webhook
    location /tribute-webhook {
        proxy_pass http://127.0.0.1:8081;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
    }

    location /webhook-health {
        proxy_pass http://127.0.0.1:8081/health;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
    }

    location / {
        root /var/www/html;
        index index.html index.htm;
        try_files \$uri \$uri/ =404;
    }
}
EOF
        
        # Активируем сайт
        ln -sf "$config_file" "/etc/nginx/sites-enabled/"
        log "✅ Создана новая конфигурация для $domain"
    fi
    
    # Проверяем конфигурацию и перезагружаем
    if nginx -t; then
        systemctl reload nginx
        log "✅ Nginx настроен и перезагружен"
    else
        error "Ошибка в конфигурации Nginx"
        return 1
    fi
}

# Функция для настройки Caddy (системный)
configure_caddy_system() {
    local domain="$1"
    local caddyfile="/etc/caddy/Caddyfile"
    
    log "🔧 Настройка системного Caddy для домена: $domain"
    
    if [[ ! -f "$caddyfile" ]]; then
        error "Caddyfile не найден: $caddyfile"
        return 1
    fi
    
    # Создаем резервную копию
    cp "$caddyfile" "$caddyfile.backup.$(date +%s)"
    
    # Проверяем, есть ли уже настройка для домена
    if grep -q "^https://$domain\|^$domain" "$caddyfile"; then
        log "⚠️  Конфигурация для домена $domain уже существует"
        
        # Проверяем наличие webhook настроек
        if ! grep -q "/tribute-webhook" "$caddyfile"; then
            # Добавляем webhook настройки в существующий блок
            sed -i "/^https:\/\/$domain\|^$domain/,/^}/ {
                /handle.*{/a\\
    # Tribute webhook endpoint\\
    handle /tribute-webhook* {\\
        reverse_proxy localhost:8081 {\\
            header_up Host {host}\\
            header_up X-Real-IP {remote_host}\\
        }\\
    }\\
    \\
    # Health check для webhook сервиса\\
    handle /webhook-health {\\
        reverse_proxy localhost:8081/health {\\
            header_up Host {host}\\
            header_up X-Real-IP {remote_host}\\
        }\\
    }
            }" "$caddyfile"
            
            log "✅ Добавлены webhook настройки в существующую конфигурацию"
        else
            log "✅ Webhook настройки уже присутствуют"
        fi
    else
        # Создаем новую конфигурацию для домена
        cat >> "$caddyfile" << EOF

https://$domain {
    # Tribute webhook endpoint
    handle /tribute-webhook* {
        reverse_proxy localhost:8081 {
            header_up Host {host}
            header_up X-Real-IP {remote_host}
        }
    }
    
    # Health check для webhook сервиса
    handle /webhook-health {
        reverse_proxy localhost:8081/health {
            header_up Host {host}
            header_up X-Real-IP {remote_host}
        }
    }
    
    # Остальные запросы обрабатываются как обычно
    handle {
        root * /var/www/html
        try_files {path} /index.html
        file_server
    }
    
    log {
        output file /var/log/caddy/access.log {
            roll_size 10MB
            roll_keep 5
            roll_keep_for 720h
            roll_compression gzip
        }
        level ERROR
    }
}
EOF
        log "✅ Добавлена новая конфигурация для домена $domain"
    fi
    
    # Перезагружаем Caddy
    if systemctl reload caddy; then
        log "✅ Caddy перезагружен"
    else
        error "Ошибка при перезагрузке Caddy"
        # Восстанавливаем из резервной копии
        cp "$caddyfile.backup.$(date +%s)" "$caddyfile"
        return 1
    fi
}

# Функция для настройки Nginx (Docker)
configure_nginx_docker() {
    local container_name="$1"
    local domain="$2"
    
    log "🔧 Настройка Nginx Docker для домена: $domain"
    
    # Находим директорию с конфигурацией nginx
    local nginx_dir=$(docker inspect "$container_name" | jq -r '.[0].Mounts[] | select(.Destination | contains("nginx")) | .Source' | head -1)
    
    if [[ -z "$nginx_dir" || "$nginx_dir" == "null" ]]; then
        error "Не удалось найти директорию с конфигурацией nginx для контейнера $container_name"
        return 1
    fi
    
    local config_file="$nginx_dir/conf.d/$domain.conf"
    
    # Создаем конфигурацию для домена
    cat > "$config_file" << EOF
server {
    listen 80;
    server_name $domain;

    # Tribute webhook
    location /tribute-webhook {
        proxy_pass http://host.docker.internal:8081;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
    }

    location /webhook-health {
        proxy_pass http://host.docker.internal:8081/health;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
    }

    location / {
        root /var/www/html;
        index index.html index.htm;
        try_files \$uri \$uri/ =404;
    }
}
EOF
    
    # Перезагружаем nginx в контейнере
    if docker exec "$container_name" nginx -t && docker exec "$container_name" nginx -s reload; then
        log "✅ Nginx в Docker перезагружен"
    else
        error "Ошибка при перезагрузке Nginx в Docker"
        rm -f "$config_file"
        return 1
    fi
}

# Основная функция установки
main_install() {
    print_header
    
    log "🚀 Начало установки Bedolaga Bot..."
    
    # Проверки
    check_root
    check_system
    
    # Обновление и установка
    update_system
    install_docker
    
    # Создание структуры
    create_project_structure
    
    # Обнаружение веб-серверов
    detect_webservers
    
    # Интерактивная настройка
    configure_env
    
    # Создание файлов
    create_docker_compose
    
    # Создание службы
    create_systemd_service
    
    # Запуск
    start_bot
    
    log "🎉 Установка завершена успешно!"
    
    echo -e "${GREEN}"
    echo "╔════════════════════════════════════════════════════════════════╗"
    echo "║                    УСТАНОВКА ЗАВЕРШЕНА!                       ║"
    echo "╠════════════════════════════════════════════════════════════════╣"
    echo "║ Бот установлен в: $PROJECT_DIR"
    echo "║ Для управления ботом запустите: $0 --manage"
    echo "║ Или перейдите в директорию и используйте docker compose"
    echo "╚════════════════════════════════════════════════════════════════╝"
    echo -e "${NC}"
    
    read -p "Запустить меню управления сейчас? (y/n): " start_menu
    if [[ "$start_menu" =~ ^[Yy]$ ]]; then
        management_menu
    fi
}

# Основная логика скрипта
main() {
    case "${1:-}" in
        --manage|manage|-m)
            if [[ ! -d "$PROJECT_DIR" ]]; then
                error "Проект не найден в $PROJECT_DIR. Сначала выполните установку."
                exit 1
            fi
            management_menu
            ;;
        --install|install|-i|"")
            main_install
            ;;
        --help|help|-h)
            print_header
            echo -e "${CYAN}Использование:${NC}"
            echo "  $0                 # Установка бота"
            echo "  $0 --install       # Установка бота"
            echo "  $0 --manage        # Меню управления"
            echo "  $0 --help          # Показать справку"
            ;;
        *)
            error "Неизвестный параметр: $1"
            echo "Используйте $0 --help для справки"
            exit 1
            ;;
    esac
}

# Обработка сигналов
trap 'echo -e "\n${YELLOW}Установка прервана пользователем${NC}"; exit 130' INT
trap 'echo -e "\n${RED}Произошла ошибка${NC}"; exit 1' ERR

# Запуск основной функции
main "$@"
