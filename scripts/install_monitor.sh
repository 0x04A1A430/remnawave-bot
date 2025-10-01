#!/usr/bin/env bash
set -euo pipefail

REPO_URL_DEFAULT="https://github.com/remnawave/remnawave-bedolaga-telegram-bot.git"
INSTALL_DIR_DEFAULT="/opt/remnawave-bot"
CADDY_DIR="/opt/caddy"
PROXY_NETWORK="remnawave_bot_proxy"
BOT_CONTAINER_NAME="remnawave_bot"
CADDY_CONTAINER_NAME="remnawave_caddy"

log() {
    local level="$1"; shift
    printf '[%s] %s\n' "$level" "$*"
}

command_exists() {
    command -v "$1" >/dev/null 2>&1
}

require_command() {
    if ! command_exists "$1"; then
        log ERROR "Команда '$1' не найдена. Установите её и запустите скрипт снова."
        exit 1
    fi
}

read_non_empty() {
    local prompt="$1"
    local default_value="${2:-}"
    local value
    while true; do
        if [[ -n "$default_value" ]]; then
            read -r -p "$prompt [$default_value]: " value || value=""
            value="${value:-$default_value}"
        else
            read -r -p "$prompt: " value || value=""
        fi
        if [[ -n "$value" ]]; then
            printf '%s' "$value"
            return 0
        fi
        log WARN "Значение не может быть пустым."
    done
}

read_optional() {
    local prompt="$1"
    local default_value="${2:-}"
    local value
    read -r -p "$prompt${default_value:+ [$default_value]}: " value || value=""
    if [[ -z "$value" && -n "$default_value" ]]; then
        value="$default_value"
    fi
    printf '%s' "$value"
}

confirm() {
    local prompt="$1"
    local default_answer="${2:-y}"
    local answer
    while true; do
        read -r -p "$prompt [$( [[ "$default_answer" == "y" ]] && printf 'Y/n' || printf 'y/N' )]: " answer || answer=""
        answer="${answer:-$default_answer}"
        case "$answer" in
            [Yy]*) return 0 ;;
            [Nn]*) return 1 ;;
            *) log WARN "Пожалуйста, введите y или n." ;;
        esac
    done
}

ensure_repo() {
    local script_dir
    script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

    if [[ -f "$script_dir/../docker-compose.yml" ]]; then
        REPO_DIR="$(cd "$script_dir/.." && pwd)"
        log INFO "Скрипт запущен из репозитория: $REPO_DIR"
        return
    fi

    local install_dir
    install_dir=$(read_optional "Укажите путь установки репозитория" "$INSTALL_DIR_DEFAULT")
    if [[ -z "$install_dir" ]]; then
        install_dir="$INSTALL_DIR_DEFAULT"
    fi
    mkdir -p "$install_dir"
    if [[ -f "$install_dir/docker-compose.yml" && -d "$install_dir/.git" ]]; then
        REPO_DIR="$install_dir"
        log INFO "Найден существующий репозиторий в $REPO_DIR"
        return
    fi

    if [[ -d "$install_dir/.git" ]]; then
        REPO_DIR="$install_dir"
        log INFO "Используется существующий клон репозитория: $REPO_DIR"
        return
    fi

    local repo_url
    repo_url=$(read_optional "Введите URL репозитория" "$REPO_URL_DEFAULT")
    if [[ -z "$repo_url" ]]; then
        repo_url="$REPO_URL_DEFAULT"
    fi

    log INFO "Клонирование репозитория в $install_dir"
    if [[ -n "$(ls -A "$install_dir" 2>/dev/null)" ]]; then
        log ERROR "Каталог $install_dir не пуст. Удалите содержимое или выберите другой путь."
        exit 1
    fi
    git clone "$repo_url" "$install_dir"
    REPO_DIR="$install_dir"
}

load_env() {
    declare -gA CURRENT_ENV=()
    local env_file="$REPO_DIR/.env"
    if [[ -f "$env_file" ]]; then
        while IFS='=' read -r key value; do
            [[ -z "$key" || "$key" == \#* ]] && continue
            value="${value%%$'\r'}"
            CURRENT_ENV["$key"]="${value}"
        done < "$env_file"
    fi
}

save_env() {
    local env_file="$REPO_DIR/.env"
    log INFO "Сохранение настроек в $env_file"
    {
        echo "# Автоматически создано install_monitor.sh"
        echo "# $(date)"
        for key in "${!NEW_ENV[@]}"; do
            printf '%s=%s\n' "$key" "${NEW_ENV[$key]}"
        done | sort
    } > "$env_file"
}

ensure_directories() {
    log INFO "Создание необходимых каталогов"
    mkdir -p "$REPO_DIR/logs" "$REPO_DIR/data" "$REPO_DIR/data/backups" "$REPO_DIR/data/referral_qr"
    chmod -R 755 "$REPO_DIR/logs" "$REPO_DIR/data"

    local sudo_cmd=""
    if [[ "$(id -u)" -ne 0 ]]; then
        if command_exists sudo; then
            sudo_cmd="sudo"
        else
            log WARN "Нет привилегий root и недоступен sudo. Пропуск смены владельца каталогов."
        fi
    fi

    if [[ -n "$sudo_cmd" ]]; then
        $sudo_cmd chown -R 1000:1000 "$REPO_DIR/logs" "$REPO_DIR/data"
    elif [[ "$(id -u)" -eq 0 ]]; then
        chown -R 1000:1000 "$REPO_DIR/logs" "$REPO_DIR/data"
    fi
}

compose_cmd() {
    if docker compose version >/dev/null 2>&1; then
        echo "docker compose"
    elif command_exists docker-compose; then
        echo "docker-compose"
    else
        log ERROR "Не найден docker compose."
        exit 1
    fi
}

ensure_docker_network() {
    if ! docker network inspect "$PROXY_NETWORK" >/dev/null 2>&1; then
        log INFO "Создание сети $PROXY_NETWORK"
        docker network create "$PROXY_NETWORK"
    fi
}

connect_to_proxy_network() {
    local container_name="$1"
    if docker ps --format '{{.Names}}' | grep -Fxq "$container_name"; then
        if ! docker network inspect "$PROXY_NETWORK" | grep -q "$container_name"; then
            log INFO "Подключение контейнера $container_name к сети $PROXY_NETWORK"
            docker network connect "$PROXY_NETWORK" "$container_name" || true
        fi
    fi
}

append_unique() {
    local file="$1"
    local marker="$2"
    local content="$3"

    if [[ ! -f "$file" ]]; then
        log INFO "Создание файла $file"
        mkdir -p "$(dirname "$file")"
        printf '%s\n' "$content" > "$file"
        return
    fi

    if grep -Fq "$marker" "$file"; then
        log INFO "Конфигурация с маркером $marker уже присутствует в $file"
        return
    fi

    printf '\n%s\n%s\n' "$marker" "$content" >> "$file"
    log INFO "Добавлен блок конфигурации в $file"
}

configure_caddy_snippet() {
    local file_path="$1"
    local webhook_domain="$2"
    local miniapp_domain="$3"
    local redirect_domain="$4"
    local marker="# --- remnawave-bot (auto) ---"

    local parts=""
    if [[ -n "$webhook_domain" ]]; then
        parts+="$webhook_domain {\n"
        parts+="    handle /tribute-webhook* {\n        reverse_proxy $BOT_CONTAINER_NAME:8081\n    }\n"
        parts+="    handle /cryptobot-webhook* {\n        reverse_proxy $BOT_CONTAINER_NAME:8081\n    }\n"
        parts+="    handle /mulenpay-webhook* {\n        reverse_proxy $BOT_CONTAINER_NAME:8081\n    }\n"
        parts+="    handle /pal24-webhook* {\n        reverse_proxy $BOT_CONTAINER_NAME:8084\n    }\n"
        parts+="    handle /yookassa-webhook* {\n        reverse_proxy $BOT_CONTAINER_NAME:8082\n    }\n"
        parts+="    handle /health {\n        reverse_proxy $BOT_CONTAINER_NAME:8081/health\n    }\n}\n"
    fi

    if [[ -n "$miniapp_domain" ]]; then
        parts+="\n$miniapp_domain {\n    root * /srv/miniapp\n    try_files {path} /index.html\n    file_server\n}\n"
    fi

    if [[ -n "$redirect_domain" ]]; then
        parts+="\n$redirect_domain {\n    root * /srv/miniapp/redirect\n    file_server\n}\n"
    fi

    if [[ -z "$parts" ]]; then
        log WARN "Данные доменов не указаны, конфигурация Caddy не изменена"
        return
    fi

    local snippet="# --- remnawave-bot (auto) ---\n$parts"
    append_unique "$file_path" "$marker" "$snippet"
}

configure_nginx_snippet() {
    local file_path="$1"
    local webhook_domain="$2"
    local miniapp_domain="$3"
    local redirect_domain="$4"
    local marker="# --- remnawave-bot (auto) ---"

    local parts=""
    if [[ -n "$webhook_domain" ]]; then
        parts+="server {\n    listen 80;\n    server_name $webhook_domain;\n\n    location /tribute-webhook {\n        proxy_pass http://$BOT_CONTAINER_NAME:8081;\n        proxy_set_header Host $host;\n        proxy_set_header X-Real-IP $remote_addr;\n        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;\n        proxy_set_header X-Forwarded-Proto $scheme;\n    }\n\n    location /cryptobot-webhook {\n        proxy_pass http://$BOT_CONTAINER_NAME:8081;\n        proxy_set_header Host $host;\n        proxy_set_header X-Real-IP $remote_addr;\n        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;\n        proxy_set_header X-Forwarded-Proto $scheme;\n    }\n\n    location /mulenpay-webhook {\n        proxy_pass http://$BOT_CONTAINER_NAME:8081;\n        proxy_set_header Host $host;\n        proxy_set_header X-Real-IP $remote_addr;\n        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;\n        proxy_set_header X-Forwarded-Proto $scheme;\n    }\n\n    location /pal24-webhook {\n        proxy_pass http://$BOT_CONTAINER_NAME:8084;\n        proxy_set_header Host $host;\n        proxy_set_header X-Real-IP $remote_addr;\n        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;\n        proxy_set_header X-Forwarded-Proto $scheme;\n    }\n\n    location /yookassa-webhook {\n        proxy_pass http://$BOT_CONTAINER_NAME:8082;\n        proxy_set_header Host $host;\n        proxy_set_header X-Real-IP $remote_addr;\n        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;\n        proxy_set_header X-Forwarded-Proto $scheme;\n    }\n\n    location /health {\n        proxy_pass http://$BOT_CONTAINER_NAME:8081/health;\n    }\n}\n"
    fi

    if [[ -n "$miniapp_domain" ]]; then
        parts+="\nserver {\n    listen 80;\n    server_name $miniapp_domain;\n\n    location / {\n        root /srv/miniapp;\n        try_files $uri $uri/ /index.html;\n    }\n}\n"
    fi

    if [[ -n "$redirect_domain" ]]; then
        parts+="\nserver {\n    listen 80;\n    server_name $redirect_domain;\n\n    location / {\n        root /srv/miniapp/redirect;\n        try_files $uri $uri/ /index.html;\n    }\n}\n"
    fi

    if [[ -z "$parts" ]]; then
        log WARN "Данные доменов не указаны, конфигурация Nginx не изменена"
        return
    fi

    local snippet="# --- remnawave-bot (auto) ---\n$parts"
    append_unique "$file_path" "$marker" "$snippet"
}

configure_existing_caddy() {
    local container="$1"
    local webhook_domain="$2"
    local miniapp_domain="$3"
    local redirect_domain="$4"

    local mount_path
    mount_path=$(docker inspect -f '{{range .Mounts}}{{if or (eq .Destination "/etc/caddy/Caddyfile") (eq .Destination "/etc/caddy")}}{{.Source}}{{" "}}{{end}}{{end}}' "$container" | awk 'NF {print $1}' | head -n1)

    if [[ -z "$mount_path" ]]; then
        log WARN "Не удалось определить путь конфигурации Caddy для контейнера $container. Добавьте конфигурацию вручную."
        return
    fi

    local config_file
    if [[ -d "$mount_path" ]]; then
        config_file="$mount_path/Caddyfile"
    else
        config_file="$mount_path"
    fi

    configure_caddy_snippet "$config_file" "$webhook_domain" "$miniapp_domain" "$redirect_domain"
}

configure_existing_nginx() {
    local container="$1"
    local webhook_domain="$2"
    local miniapp_domain="$3"
    local redirect_domain="$4"

    local mount_path
    mount_path=$(docker inspect -f '{{range .Mounts}}{{if or (eq .Destination "/etc/nginx/nginx.conf") (eq .Destination "/etc/nginx/conf.d") (eq .Destination "/etc/nginx")}}{{.Source}}{{" "}}{{end}}{{end}}' "$container" | awk 'NF {print $1}' | head -n1)

    if [[ -z "$mount_path" ]]; then
        log WARN "Не удалось определить путь конфигурации Nginx для контейнера $container. Добавьте конфигурацию вручную."
        return
    fi

    local config_file
    if [[ -d "$mount_path" ]]; then
        config_file="$mount_path/remnawave-bot.conf"
    else
        config_file="$mount_path"
    fi

    configure_nginx_snippet "$config_file" "$webhook_domain" "$miniapp_domain" "$redirect_domain"
}

setup_new_caddy() {
    local webhook_domain="$1"
    local miniapp_domain="$2"
    local redirect_domain="$3"

    mkdir -p "$CADDY_DIR"
    local compose_file="$CADDY_DIR/docker-compose.yml"
    local caddyfile="$CADDY_DIR/Caddyfile"

    cat > "$compose_file" <<EOF
version: "3.9"
services:
  caddy:
    image: caddy:2
    container_name: $CADDY_CONTAINER_NAME
    restart: unless-stopped
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile:ro
      - $REPO_DIR/miniapp:/srv/miniapp:ro
    networks:
      - proxy

networks:
  proxy:
    external: true
    name: $PROXY_NETWORK
EOF

    configure_caddy_snippet "$caddyfile" "$webhook_domain" "$miniapp_domain" "$redirect_domain"

    (cd "$CADDY_DIR" && $(compose_cmd) up -d)
}

configure_reverse_proxy() {
    local webhook_domain="$1"
    local miniapp_domain="$2"
    local redirect_domain="$3"

    local caddy_container
    caddy_container=$(docker ps -a --format '{{.Names}} {{.Image}}' | awk '/caddy/ {print $1}' | head -n1)
    local nginx_container
    nginx_container=$(docker ps -a --format '{{.Names}} {{.Image}}' | awk '/nginx/ {print $1}' | head -n1)

    if [[ -n "$caddy_container" ]]; then
        log INFO "Обнаружен контейнер Caddy: $caddy_container"
        configure_existing_caddy "$caddy_container" "$webhook_domain" "$miniapp_domain" "$redirect_domain"
        connect_to_proxy_network "$caddy_container"
        docker restart "$caddy_container" >/dev/null 2>&1 || true
        return
    fi

    if [[ -n "$nginx_container" ]]; then
        log INFO "Обнаружен контейнер Nginx: $nginx_container"
        configure_existing_nginx "$nginx_container" "$webhook_domain" "$miniapp_domain" "$redirect_domain"
        connect_to_proxy_network "$nginx_container"
        docker exec "$nginx_container" nginx -s reload >/dev/null 2>&1 || true
        return
    fi

    log INFO "Контейнеры Caddy/Nginx не найдены. Будет создана новая конфигурация Caddy в $CADDY_DIR"
    setup_new_caddy "$webhook_domain" "$miniapp_domain" "$redirect_domain"
}

run_compose_stack() {
    local cmd
    cmd=$(compose_cmd)
    (cd "$REPO_DIR" && $cmd pull bot >/dev/null 2>&1 || true)
    (cd "$REPO_DIR" && $cmd up -d)
}

collect_status() {
    local container="$1"
    if ! docker ps -a --format '{{.Names}}' | grep -Fxq "$container"; then
        printf 'не установлен'
        return
    fi
    docker inspect -f '{{.State.Status}}{{if .State.Health}}{{printf " (health: %s)" .State.Health.Status}}{{end}}' "$container"
}

show_monitor() {
    local compose
    compose=$(compose_cmd)
    while true; do
        clear
        echo "================ Remnawave Bot Monitor ================"
        echo "Дата: $(date)"
        echo
        printf '%-25s %s\n' "Bot" "$(collect_status "$BOT_CONTAINER_NAME")"
        printf '%-25s %s\n' "Postgres" "$(collect_status remnawave_bot_db)"
        printf '%-25s %s\n' "Redis" "$(collect_status remnawave_bot_redis)"
        printf '%-25s %s\n' "Caddy" "$(collect_status "$CADDY_CONTAINER_NAME")"
        echo
        echo "Доступные действия:"
        echo "  [1] Перезапустить бота"
        echo "  [2] Перезапустить все сервисы"
        echo "  [3] Просмотреть логи бота"
        echo "  [4] Выйти"
        echo
        read -r -p "Выберите действие: " choice || choice=""
        case "$choice" in
            1)
                (cd "$REPO_DIR" && $compose restart bot)
                ;;
            2)
                (cd "$REPO_DIR" && $compose restart)
                ;;
            3)
                (cd "$REPO_DIR" && $compose logs -f bot)
                ;;
            4)
                break
                ;;
            *)
                log WARN "Неизвестный выбор"
                ;;
        esac
        read -r -p "Нажмите Enter для продолжения..." _ || true
    done
}

prompt_domains() {
    local webhook_default="${CURRENT_ENV[WEBHOOK_DOMAIN]:-}"
    local miniapp_default="${CURRENT_ENV[MINIAPP_DOMAIN]:-}"
    local redirect_default="${CURRENT_ENV[MINIAPP_REDIRECT_DOMAIN]:-}"

    read -r -p "Домен для webhook'ов${webhook_default:+ [$webhook_default]}: " WEBHOOK_DOMAIN || WEBHOOK_DOMAIN=""
    if [[ -z "$WEBHOOK_DOMAIN" && -n "$webhook_default" ]]; then
        WEBHOOK_DOMAIN="$webhook_default"
    fi

    read -r -p "Домен мини-аппы (miniapp/index.html)${miniapp_default:+ [$miniapp_default]}: " MINIAPP_DOMAIN || MINIAPP_DOMAIN=""
    if [[ -z "$MINIAPP_DOMAIN" && -n "$miniapp_default" ]]; then
        MINIAPP_DOMAIN="$miniapp_default"
    fi

    read -r -p "Домен страницы редиректа (miniapp/redirect/index.html)${redirect_default:+ [$redirect_default]}: " MINIAPP_REDIRECT_DOMAIN || MINIAPP_REDIRECT_DOMAIN=""
    if [[ -z "$MINIAPP_REDIRECT_DOMAIN" && -n "$redirect_default" ]]; then
        MINIAPP_REDIRECT_DOMAIN="$redirect_default"
    fi

    NEW_ENV["WEBHOOK_DOMAIN"]="$WEBHOOK_DOMAIN"
    NEW_ENV["MINIAPP_DOMAIN"]="$MINIAPP_DOMAIN"
    NEW_ENV["MINIAPP_REDIRECT_DOMAIN"]="$MINIAPP_REDIRECT_DOMAIN"
}

prompt_auth_settings() {
    if confirm "Используется авторизация при подключении к Remnawave API?" "${CURRENT_ENV[REMNAWAVE_AUTH_TYPE]:+y}"; then
        local auth_type
        while true; do
            auth_type=$(read_optional "Выберите тип авторизации (api_key/basic_auth)" "${CURRENT_ENV[REMNAWAVE_AUTH_TYPE]:-api_key}")
            case "$auth_type" in
                api_key|basic_auth)
                    NEW_ENV["REMNAWAVE_AUTH_TYPE"]="$auth_type"
                    break
                    ;;
                *)
                    log WARN "Допустимые значения: api_key, basic_auth"
                    ;;
            esac
        done
    else
        NEW_ENV["REMNAWAVE_AUTH_TYPE"]=""
    fi

    if [[ "${NEW_ENV[REMNAWAVE_AUTH_TYPE]}" == "basic_auth" ]]; then
        NEW_ENV["REMNAWAVE_USERNAME"]="$(read_non_empty "REMNAWAVE_USERNAME" "${CURRENT_ENV[REMNAWAVE_USERNAME]:-}")"
        NEW_ENV["REMNAWAVE_PASSWORD"]="$(read_non_empty "REMNAWAVE_PASSWORD" "${CURRENT_ENV[REMNAWAVE_PASSWORD]:-}")"
    else
        NEW_ENV["REMNAWAVE_USERNAME"]="${CURRENT_ENV[REMNAWAVE_USERNAME]:-}"
        NEW_ENV["REMNAWAVE_PASSWORD"]="${CURRENT_ENV[REMNAWAVE_PASSWORD]:-}"
    fi

    if confirm "Используете панель, установленную скриптом eGames (требуется REMNAWAVE_SECRET_KEY)?" "${CURRENT_ENV[REMNAWAVE_SECRET_KEY]:+y}"; then
        echo "Укажите ключ в формате XXXXXXX:DDDDDDDD"
        NEW_ENV["REMNAWAVE_SECRET_KEY"]="$(read_non_empty "REMNAWAVE_SECRET_KEY" "${CURRENT_ENV[REMNAWAVE_SECRET_KEY]:-}")"
    else
        NEW_ENV["REMNAWAVE_SECRET_KEY"]="${CURRENT_ENV[REMNAWAVE_SECRET_KEY]:-}"
    fi
}

prompt_core_settings() {
    NEW_ENV["BOT_TOKEN"]="$(read_non_empty "Введите BOT_TOKEN" "${CURRENT_ENV[BOT_TOKEN]:-}")"
    NEW_ENV["ADMIN_IDS"]="$(read_non_empty "Введите ADMIN_IDS (через запятую)" "${CURRENT_ENV[ADMIN_IDS]:-}")"
    echo "Укажите ссылку на Remnawave API (например, https://panel.example.com или http://remnawave:3000)"
    NEW_ENV["REMNAWAVE_API_URL"]="$(read_non_empty "REMNAWAVE_API_URL" "${CURRENT_ENV[REMNAWAVE_API_URL]:-}")"
    NEW_ENV["REMNAWAVE_API_KEY"]="$(read_non_empty "REMNAWAVE_API_KEY" "${CURRENT_ENV[REMNAWAVE_API_KEY]:-}")"
}

run_monitoring() {
    log INFO "Перезапуск контейнера бота"
    docker restart "$BOT_CONTAINER_NAME" >/dev/null 2>&1 || true
    show_monitor
}

main() {
    require_command git
    require_command docker

    ensure_repo
    load_env

    declare -gA NEW_ENV=()

    log INFO "Настройка параметров окружения"
    prompt_core_settings
    prompt_auth_settings
    prompt_domains

    save_env
    ensure_directories
    ensure_docker_network

    run_compose_stack

    connect_to_proxy_network "$BOT_CONTAINER_NAME"
    configure_reverse_proxy "${NEW_ENV[WEBHOOK_DOMAIN]}" "${NEW_ENV[MINIAPP_DOMAIN]}" "${NEW_ENV[MINIAPP_REDIRECT_DOMAIN]}"

    run_monitoring
}

main "$@"
