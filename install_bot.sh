#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
STATE_FILE="$SCRIPT_DIR/.bot_install_state"
BACKUP_DIR="$SCRIPT_DIR/backups"

# Цвета для красивого вывода
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
PURPLE='\033[0;35m'
CYAN='\033[0;36m'
WHITE='\033[1;37m'
NC='\033[0m' # No Color
BOLD='\033[1m'

# Символы для UI
CHECK="✓"
CROSS="✗"
ARROW="➜"
STAR="★"
GEAR="⚙"

# Утилиты для вывода
print_header() {
  echo -e "\n${CYAN}${BOLD}╔════════════════════════════════════════════════════════════╗${NC}"
  echo -e "${CYAN}${BOLD}║${NC}  ${WHITE}${BOLD}$1${NC}${CYAN}${BOLD}║${NC}"
  echo -e "${CYAN}${BOLD}╚════════════════════════════════════════════════════════════╝${NC}\n"
}

print_section() {
  echo -e "\n${BLUE}${BOLD}${ARROW} $1${NC}"
  echo -e "${BLUE}─────────────────────────────────────────────────────${NC}"
}

print_success() {
  echo -e "${GREEN}${CHECK} $1${NC}"
}

print_error() {
  echo -e "${RED}${CROSS} $1${NC}" >&2
}

print_warning() {
  echo -e "${YELLOW}⚠ $1${NC}"
}

print_info() {
  echo -e "${CYAN}ℹ $1${NC}"
}

print_status() {
  local status=$1
  local text=$2
  if [[ "$status" == "running" ]]; then
    echo -e "${GREEN}● ${text}${NC}"
  elif [[ "$status" == "stopped" ]]; then
    echo -e "${RED}● ${text}${NC}"
  else
    echo -e "${YELLOW}● ${text}${NC}"
  fi
}

# Загрузка состояния
load_state() {
  if [[ -f "$STATE_FILE" ]]; then
    # shellcheck disable=SC1090
    source "$STATE_FILE"
    return 0
  else
    print_error "Установка бота не найдена. Запустите ./install.sh сначала."
    exit 1
  fi
}

# Определение команды docker compose
resolve_compose_command() {
  if docker compose version >/dev/null 2>&1; then
    COMPOSE_BIN=(docker compose)
  elif docker-compose version >/dev/null 2>&1; then
    COMPOSE_BIN=(docker-compose)
  else
    print_error "Docker Compose не найден."
    exit 1
  fi
}

run_compose() {
  (cd "$INSTALL_PATH" && "${COMPOSE_BIN[@]}" "$@")
}

# Получение статуса сервисов
get_service_status() {
  local service=$1
  local status
  status=$(run_compose ps -q "$service" 2>/dev/null | xargs -r docker inspect -f '{{.State.Status}}' 2>/dev/null || echo "not_found")
  echo "$status"
}

# Проверка установки веб-сервера
check_webserver() {
  local caddy_installed=false
  local nginx_installed=false
  local caddy_path=""
  local nginx_path=""
  
  # Проверка Caddy
  if docker ps -a --format '{{.Names}}' | grep -q "caddy"; then
    caddy_installed=true
    # Попытка найти путь к Caddyfile через docker inspect
    local caddy_container
    caddy_container=$(docker ps -a --format '{{.Names}}' | grep "caddy" | head -n1)
    
    # Извлекаем путь из Source и убираем имя файла
    caddy_path=$(docker inspect "$caddy_container" 2>/dev/null | \
      grep -A 1 'Caddyfile' | \
      grep 'Source' | \
      sed 's/.*"Source": "\(.*\)".*/\1/' | \
      sed 's/\/Caddyfile$//')
    
    # Если не нашли через inspect, проверяем стандартные пути
    if [[ -z "$caddy_path" ]] || [[ ! -d "$caddy_path" ]]; then
      if [[ -f "/opt/caddy/Caddyfile" ]]; then
        caddy_path="/opt/caddy"
      elif [[ -f "$INSTALL_PATH/caddy/Caddyfile" ]]; then
        caddy_path="$INSTALL_PATH/caddy"
      fi
    fi
  fi
  
  # Проверка Nginx
  if docker ps -a --format '{{.Names}}' | grep -q "nginx"; then
    nginx_installed=true
    local nginx_container
    nginx_container=$(docker ps -a --format '{{.Names}}' | grep "nginx" | head -n1)
    
    # Извлекаем путь из Source и убираем имя файла
    nginx_path=$(docker inspect "$nginx_container" 2>/dev/null | \
      grep -A 1 'nginx.conf' | \
      grep 'Source' | \
      sed 's/.*"Source": "\(.*\)".*/\1/' | \
      sed 's/\/nginx.conf$//')
    
    # Если не нашли через inspect, проверяем стандартные пути
    if [[ -z "$nginx_path" ]] || [[ ! -d "$nginx_path" ]]; then
      if [[ -f "/etc/nginx/nginx.conf" ]]; then
        nginx_path="/etc/nginx"
      elif [[ -f "$INSTALL_PATH/nginx/nginx.conf" ]]; then
        nginx_path="$INSTALL_PATH/nginx"
      fi
    fi
  fi
  
  echo "$caddy_installed|$nginx_installed|$caddy_path|$nginx_path"
}

# Создание docker network
create_bot_network() {
  if ! docker network ls | grep -q "bot_network"; then
    print_info "Создаем Docker сеть bot_network..."
    docker network create bot_network
    print_success "Сеть bot_network создана"
  else
    print_info "Сеть bot_network уже существует"
  fi

  # Проверяем и обновляем docker-compose.yml
  fix_bot_compose_network
}

# Очистка конфликтующих docker сетей
cleanup_conflicting_networks() {
  print_section "Очистка конфликтующих сетей Docker"

  if ! command -v docker &>/dev/null; then
    print_error "Docker не установлен"
    return 1
  fi

  local target_subnet="172.20.0.0/16"
  local networks=()

  while IFS= read -r network; do
    [[ -z "$network" ]] && continue

    local subnet
    subnet=$(docker network inspect "$network" -f '{{range .IPAM.Config}}{{.Subnet}}{{end}}' 2>/dev/null | tr -d '\n')

    if [[ "$subnet" == "$target_subnet" ]]; then
      networks+=("$network")
    fi
  done < <(docker network ls --format '{{.Name}}')

  if [[ ${#networks[@]} -eq 0 ]]; then
    print_success "Конфликтующих сетей не обнаружено"
    return 0
  fi

  print_info "Найдены сети с подсетью $target_subnet:"
  for network in "${networks[@]}"; do
    if [[ "$network" == "bot_network" ]]; then
      echo -e "   ${GREEN}→${NC} $network ${CYAN}(основная сеть)${NC}"
    else
      echo -e "   ${YELLOW}→${NC} $network"
    fi
  done

  local removable_networks=()
  for network in "${networks[@]}"; do
    local attached
    attached=$(docker network inspect "$network" -f '{{range .Containers}}{{.Name}} {{end}}' 2>/dev/null | xargs)

    if [[ -n "$attached" ]]; then
      print_warning "Сеть $network используется контейнерами: $attached"
      continue
    fi

    removable_networks+=("$network")
  done

  if [[ ${#removable_networks[@]} -eq 0 ]]; then
    print_warning "Нет сетей, которые можно удалить автоматически"
    return 0
  fi

  echo ""
  print_warning "Будут удалены следующие сети:"
  for network in "${removable_networks[@]}"; do
    echo -e "   ${RED}→${NC} $network"
  done

  read -rp "Подтвердите удаление [y/N]: " confirm
  if [[ "${confirm,,}" != "y" ]]; then
    print_info "Удаление отменено"
    return 0
  fi

  for network in "${removable_networks[@]}"; do
    if docker network rm "$network" >/dev/null 2>&1; then
      print_success "Сеть $network удалена"
    else
      print_error "Не удалось удалить сеть $network"
    fi
  done

  if docker network ls | grep -q "bot_network"; then
    print_success "Очистка завершена"
  else
    read -rp "Сеть bot_network отсутствует. Создать заново? [Y/n]: " recreate
    if [[ "${recreate,,}" != "n" ]]; then
      create_bot_network
    fi
  fi
}

# Исправление docker-compose.yml для использования внешней сети
fix_bot_compose_network() {
  local compose_file="$INSTALL_PATH/docker-compose.yml"
  
  if [[ ! -f "$compose_file" ]]; then
    print_warning "docker-compose.yml не найден в $INSTALL_PATH"
    return 1
  fi
  
  print_info "Проверяем конфигурацию сети в docker-compose.yml..."
  
  # Проверяем, нужно ли обновление
  if grep -q "external: true" "$compose_file" && grep -q "name: bot_network" "$compose_file"; then
    print_success "docker-compose.yml уже настроен правильно"
    return 0
  fi
  
  # Создаем резервную копию
  cp "$compose_file" "$compose_file.backup.$(date +%Y%m%d_%H%M%S)"
  print_info "Резервная копия создана"
  
  # Проверяем, есть ли секция networks в конце файла
  if grep -q "^networks:" "$compose_file"; then
    print_info "Обновляем существующую секцию networks..."
    
    # Удаляем старую секцию networks и добавляем новую
    sed -i '/^networks:/,$d' "$compose_file"
    cat >> "$compose_file" <<'EOF'
networks:
  default:
    name: bot_network
    external: true
EOF
  else
    print_info "Добавляем секцию networks..."
    cat >> "$compose_file" <<'EOF'

networks:
  default:
    name: bot_network
    external: true
EOF
  fi
  
  # Также нужно убедиться, что сервисы не определяют свои networks явно
  # Удаляем строки с networks внутри сервисов, если они есть
  if grep -q "    networks:" "$compose_file"; then
    print_info "Удаляем явные определения networks из сервисов..."
    sed -i '/^  [a-z_]*:/,/^  [a-z_]*:/ { /    networks:/d; /      - bot_network/d; /      - .*_bot_network/d }' "$compose_file"
  fi
  
  print_success "docker-compose.yml обновлен для использования внешней сети bot_network"
  print_warning "Необходимо пересоздать контейнеры командой:"
  echo -e "${YELLOW}cd $INSTALL_PATH && docker compose down && docker compose up -d${NC}"
  
  read -rp "$(echo -e ${YELLOW}Пересоздать контейнеры сейчас? [Y/n]: ${NC})" recreate
  if [[ "${recreate,,}" != "n" ]]; then
    print_info "Останавливаем контейнеры..."
    run_compose down
    
    print_info "Запускаем контейнеры с новой конфигурацией..."
    run_compose up -d
    
    print_success "Контейнеры пересозданы"
  fi
}

# Подключение бота к сети
connect_bot_to_network() {
  local bot_container
  bot_container=$(docker ps --filter "name=bot" --format "{{.Names}}" | head -n1)
  
  if [[ -n "$bot_container" ]]; then
    if ! docker inspect "$bot_container" 2>/dev/null | grep -q '"bot_network"'; then
      print_info "Подключаем бот к сети bot_network..."
      docker network connect bot_network "$bot_container" 2>/dev/null || true
      print_success "Бот подключен к сети"
    else
      print_info "Бот уже подключен к сети bot_network"
    fi
  fi
}

# Установка и настройка Caddy
install_caddy() {
  print_section "Установка Caddy"
  
  local caddy_dir="$INSTALL_PATH/caddy"
  mkdir -p "$caddy_dir/logs"
  mkdir -p "/opt/caddy/html"
  
  # Создаем начальный Caddyfile
  cat > "$caddy_dir/Caddyfile" <<'EOF'
# Caddy configuration
# Webhook и miniapp будут добавлены через меню настройки
EOF
  
  # Создаем docker-compose для Caddy
  cat > "$caddy_dir/docker-compose.yml" <<EOF
services:
  caddy:
    image: caddy:2.9.1
    container_name: caddy-bot-proxy
    restart: unless-stopped
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile
      - /opt/caddy/html:/var/www/html
      - ./logs:/var/log/caddy
      - caddy_data:/data
      - caddy_config:/config
      - $INSTALL_PATH/miniapp:/var/www/remnawave-miniapp:ro
    network_mode: "host"
    logging:
      driver: "json-file"
      options:
        max-size: "10m"
        max-file: "3"

volumes:
  caddy_data:
  caddy_config:

networks:
  default:
    name: bot_network
    external: true
EOF
  
  # Создаем сеть
  create_bot_network
  
  # Запускаем Caddy
  print_info "Запускаем Caddy..."
  (cd "$caddy_dir" && docker compose up -d)
  
  sleep 2
  
  if docker ps | grep -q "caddy-bot-proxy"; then
    print_success "Caddy успешно установлен и запущен"
    print_info "Путь к конфигурации: $caddy_dir"
    return 0
  else
    print_error "Не удалось запустить Caddy"
    return 1
  fi
}

# Настройка webhook прокси
configure_webhook_proxy() {
  echo -e "\n${BLUE}${BOLD}${ARROW} Настройка прокси для webhook${NC}" >&2
  echo -e "${BLUE}─────────────────────────────────────────────────────${NC}" >&2
  
  local webhook_domain
  read -rp "Введите домен для webhook (например, webhook.example.com): " webhook_domain
  
  # Очищаем от невидимых символов и пробелов
  webhook_domain=$(echo "$webhook_domain" | tr -d '\r\n\t' | xargs | LC_ALL=C sed 's/[^a-zA-Z0-9.-]//g')
  
  if [[ -z "$webhook_domain" ]]; then
    echo -e "${RED}${CROSS} Домен не указан${NC}" >&2
    return 1
  fi
  
  # Проверяем валидность домена
  if ! [[ "$webhook_domain" =~ ^[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?(\.[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)*$ ]]; then
    echo -e "${RED}${CROSS} Невалидный домен: $webhook_domain${NC}" >&2
    return 1
  fi
  
  echo -e "${CYAN}ℹ Используем домен: ${YELLOW}$webhook_domain${NC}" >&2
  
  # Возвращаем чистый текст БЕЗ echo -e
  cat <<EOF
$webhook_domain {
    handle /tribute-webhook* {
        reverse_proxy localhost:8081
    }
    
    handle /cryptobot-webhook* {
        reverse_proxy localhost:8081
    }
    
    handle /mulenpay-webhook* {
        reverse_proxy localhost:8081
    }
    
    handle /pal24-webhook* {
        reverse_proxy localhost:8084
    }
    
    handle /yookassa-webhook* {
        reverse_proxy localhost:8082
    }
    
    handle /health {
        reverse_proxy localhost:8081/health
    }
}
EOF
}

# Настройка miniapp прокси
configure_miniapp_proxy() {
  echo -e "\n${BLUE}${BOLD}${ARROW} Настройка прокси для miniapp${NC}" >&2
  echo -e "${BLUE}─────────────────────────────────────────────────────${NC}" >&2
  
  local miniapp_domain
  read -rp "Введите домен для miniapp (например, miniapp.example.com): " miniapp_domain
  
  # Очищаем от невидимых символов и пробелов
  miniapp_domain=$(echo "$miniapp_domain" | tr -d '\r\n\t' | xargs | LC_ALL=C sed 's/[^a-zA-Z0-9.-]//g')
  
  if [[ -z "$miniapp_domain" ]]; then
    echo -e "${RED}${CROSS} Домен не указан${NC}" >&2
    return 1
  fi
  
  # Проверяем валидность домена
  if ! [[ "$miniapp_domain" =~ ^[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?(\.[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)*$ ]]; then
    echo -e "${RED}${CROSS} Невалидный домен: $miniapp_domain${NC}" >&2
    return 1
  fi
  
  echo -e "${CYAN}ℹ Используем домен: ${YELLOW}$miniapp_domain${NC}" >&2
  
  # Возвращаем чистый текст БЕЗ echo -e
  cat <<EOF
$miniapp_domain {
    encode gzip zstd
    root * /var/www/remnawave-miniapp
    file_server
    
    @config path /app-config.json
    header @config Access-Control-Allow-Origin "*"
    
    # Redirect for /miniapp/redirect/index.html
    @redirect path /miniapp/redirect/index.html
    redir @redirect /miniapp/redirect/index.html permanent
    
    reverse_proxy /miniapp/* 127.0.0.1:8080 {
        header_up Host {host}
        header_up X-Real-IP {remote_host}
    }
}
EOF
}

upsert_caddy_block() {
  local caddy_file=$1
  local config=$2
  local label=$3

  # Убираем лишние пробелы для проверки пустоты
  local stripped
  stripped=$(echo "$config" | tr -d ' \t\n\r')
  if [[ -z "$stripped" ]]; then
    return 0
  fi

  local first_line
  first_line=$(echo "$config" | sed -n '1p')
  local domain=${first_line%% *}

  if [[ -z "$domain" ]]; then
    print_warning "Не удалось определить домен для секции $label"
    return 1
  fi

  local domain_marker="$domain {"

  if [[ -f "$caddy_file" ]] && grep -Fq "$domain_marker" "$caddy_file"; then
    if ! command -v python3 >/dev/null 2>&1; then
      print_error "Python3 не найден, не могу обновить существующую конфигурацию домена $domain"
      return 1
    fi
    print_info "Обновляем конфигурацию домена $domain"
    python3 - "$caddy_file" "$domain" <<'PY'
import os
import sys

path, domain = sys.argv[1:]
if not os.path.exists(path):
    sys.exit(0)

with open(path, encoding="utf-8") as fh:
    lines = fh.read().splitlines()

result = []
skip = False
brace_level = 0

for line in lines:
    stripped = line.lstrip()
    if not skip:
        if stripped.startswith(domain) and stripped[len(domain):].lstrip().startswith('{'):
            skip = True
            brace_level = line.count('{') - line.count('}')
            continue
        result.append(line)
        continue

    brace_level += line.count('{') - line.count('}')
    if brace_level <= 0:
        skip = False
    # Не добавляем строки из удаляемого блока

text = "\n".join(result)
if text and not text.endswith("\n"):
    text += "\n"

with open(path, "w", encoding="utf-8") as fh:
    fh.write(text)
PY
  else
    print_info "Добавляем новый домен $domain"
  fi

  # Обеспечиваем перевод строки в конце файла
  if [[ -s "$caddy_file" ]]; then
    if [[ $(tail -c1 "$caddy_file" 2>/dev/null || echo '') != $'\n' ]]; then
      echo >> "$caddy_file"
    fi
    # Добавляем пустую строку для отделения блоков, если файл не пуст
    local last_line
    last_line=$(tail -n1 "$caddy_file" 2>/dev/null || echo '')
    if [[ -n "$last_line" ]]; then
      echo >> "$caddy_file"
    fi
  fi

  printf '%s\n' "$config" >> "$caddy_file"
  print_success "Конфигурация для домена $domain обновлена"
}

# Применение конфигурации Caddy
apply_caddy_config() {
  local caddy_dir=$1
  local webhook_config=$2
  local miniapp_config=$3
  local caddy_file="$caddy_dir/Caddyfile"

  mkdir -p "$caddy_dir"

  # Создаем резервную копию
  if [[ -f "$caddy_file" ]]; then
    cp "$caddy_file" "$caddy_dir/Caddyfile.backup.$(date +%Y%m%d_%H%M%S)"
    print_info "Резервная копия создана"
  else
    print_info "Создаем новый Caddyfile"
  fi

  # Инициализируем файл, если он пустой
  if [[ ! -s "$caddy_file" ]]; then
    cat > "$caddy_file" <<EOF
# Caddy configuration for Remnawave Bot
# Managed by install_bot.sh

EOF
  fi

  upsert_caddy_block "$caddy_file" "$webhook_config" "webhook"
  upsert_caddy_block "$caddy_file" "$miniapp_config" "miniapp"

  print_success "Конфигурация записана в $caddy_file"

  # Перезагружаем Caddy
  print_info "Перезагружаем Caddy..."
  local caddy_container
  caddy_container=$(docker ps --filter "name=caddy" --format "{{.Names}}" | head -n1)

  if [[ -n "$caddy_container" ]]; then
    # Сначала проверяем конфигурацию
    if docker exec "$caddy_container" caddy validate --config /etc/caddy/Caddyfile 2>/dev/null; then
      print_success "Конфигурация валидна"

      # Перезагружаем
      if docker exec "$caddy_container" caddy reload --config /etc/caddy/Caddyfile 2>/dev/null; then
        print_success "Caddy перезагружен успешно"
      else
        print_warning "Перезагрузка через reload не удалась, перезапускаем контейнер..."
        docker restart "$caddy_container"
        sleep 3
        print_success "Контейнер перезапущен"
      fi
    else
      print_error "Ошибка валидации конфигурации Caddy"
      print_warning "Восстанавливаем предыдущую конфигурацию..."

      # Находим последний бэкап
      local last_backup
      last_backup=$(ls -t "$caddy_dir"/Caddyfile.backup.* 2>/dev/null | head -n1)
      if [[ -n "$last_backup" ]]; then
        cp "$last_backup" "$caddy_dir/Caddyfile"
        print_info "Предыдущая конфигурация восстановлена"
      fi
      return 1
    fi
  else
    print_error "Caddy контейнер не найден или не запущен"
    print_info "Попробуйте запустить: docker start caddy"
    return 1
  fi
}

# Применение конфигурации Nginx
apply_nginx_config() {
  local nginx_dir=$1
  local webhook_domain=$2
  local miniapp_domain=$3
  
  # Создаем резервную копию
  cp "$nginx_dir/nginx.conf" "$nginx_dir/nginx.conf.backup.$(date +%Y%m%d_%H%M%S)" 2>/dev/null || true
  
  local nginx_config="
# Webhook proxy
server {
    listen 80;
    server_name $webhook_domain;

    location /tribute-webhook {
        proxy_pass http://localhost:8081;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
    }
    
    location /cryptobot-webhook {
        proxy_pass http://localhost:8081;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
    }
    
    location /mulenpay-webhook {
        proxy_pass http://localhost:8081;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
    }
    
    location /pal24-webhook {
        proxy_pass http://localhost:8084;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
    }
    
    location /yookassa-webhook {
        proxy_pass http://localhost:8082;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
    }
    
    location /health {
        proxy_pass http://localhost:8081/health;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
    }
}

# Miniapp proxy
server {
    listen 80;
    server_name $miniapp_domain;

    root /var/www/remnawave-miniapp;
    index index.html;

    gzip on;
    gzip_types text/plain application/json text/css application/javascript;

    location /app-config.json {
        add_header Access-Control-Allow-Origin *;
    }

    location /miniapp/ {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
    }

    location / {
        try_files \$uri \$uri/ =404;
    }
}
"
  
  echo "$nginx_config" > "$nginx_dir/nginx.conf"
  
  # Перезагружаем Nginx
  print_info "Перезагружаем Nginx..."
  local nginx_container
  nginx_container=$(docker ps --filter "name=nginx" --format "{{.Names}}" | head -n1)
  
  if [[ -n "$nginx_container" ]]; then
    docker exec "$nginx_container" nginx -t && docker exec "$nginx_container" nginx -s reload
    print_success "Конфигурация Nginx применена"
  else
    print_error "Nginx контейнер не найден"
    return 1
  fi
}

# Показать текущую конфигурацию прокси
show_proxy_status() {
  print_header "СТАТУС ОБРАТНОГО ПРОКСИ"
  
  local webserver_info
  webserver_info=$(check_webserver)
  IFS='|' read -r caddy_installed nginx_installed caddy_path nginx_path <<< "$webserver_info"
  
  print_section "Установленные веб-серверы"
  
  if [[ "$caddy_installed" == "true" ]]; then
    local caddy_container
    caddy_container=$(docker ps --filter "name=caddy" --format "{{.Names}}" | head -n1)
    local caddy_status
    caddy_status=$(docker inspect -f '{{.State.Status}}' "$caddy_container" 2>/dev/null || echo "not_found")
    
    print_status "$caddy_status" "Caddy: $caddy_status"
    if [[ -n "$caddy_path" ]]; then
      echo -e "   ${CYAN}Путь к конфигурации: ${YELLOW}$caddy_path${NC}"
    fi
    
    # Показываем домены из Caddyfile
    if [[ -f "$caddy_path/Caddyfile" ]]; then
      print_info "Настроенные домены в Caddy:"
      grep -E "^[a-zA-Z0-9\.-]+ \{" "$caddy_path/Caddyfile" | sed 's/ {//' | while read -r domain; do
        echo -e "   ${GREEN}→${NC} $domain"
      done
    fi
  else
    print_warning "Caddy не установлен"
  fi
  
  echo ""
  
  if [[ "$nginx_installed" == "true" ]]; then
    local nginx_container
    nginx_container=$(docker ps --filter "name=nginx" --format "{{.Names}}" | head -n1)
    local nginx_status
    nginx_status=$(docker inspect -f '{{.State.Status}}' "$nginx_container" 2>/dev/null || echo "not_found")
    
    print_status "$nginx_status" "Nginx: $nginx_status"
    if [[ -n "$nginx_path" ]]; then
      echo -e "   ${CYAN}Путь к конфигурации: ${YELLOW}$nginx_path${NC}"
    fi
  else
    print_warning "Nginx не установлен"
  fi
  
  # Проверка сети bot_network
  print_section "Docker сеть"
  if docker network ls | grep -q "bot_network"; then
    print_success "Сеть bot_network существует"
    local connected_containers
    connected_containers=$(docker network inspect bot_network -f '{{range .Containers}}{{.Name}} {{end}}' 2>/dev/null || echo "")
    if [[ -n "$connected_containers" ]]; then
      echo -e "   ${CYAN}Подключенные контейнеры:${NC}"
      for container in $connected_containers; do
        echo -e "   ${GREEN}→${NC} $container"
      done
    fi
  else
    print_warning "Сеть bot_network не создана"
  fi
}

# Главное меню настройки прокси
configure_reverse_proxy() {
  while true; do
    print_header "НАСТРОЙКА ОБРАТНОГО ПРОКСИ"
    
    local webserver_info
    webserver_info=$(check_webserver)
    IFS='|' read -r caddy_installed nginx_installed caddy_path nginx_path <<< "$webserver_info"
    
    echo -e "${CYAN}[1]${NC} 📊 Показать статус прокси"
    echo -e "${CYAN}[2]${NC} ⚙️  Настроить Caddy (webhook + miniapp)"
    
    if [[ "$nginx_installed" == "true" ]]; then
      echo -e "${CYAN}[3]${NC} ⚙️  Настроить Nginx (webhook + miniapp)"
    fi
    
    if [[ "$caddy_installed" == "false" ]]; then
      echo -e "${CYAN}[4]${NC} 📦 Установить Caddy"
    else
      echo -e "${CYAN}[4]${NC} 📝 Редактировать Caddyfile вручную"
    fi
    
    echo -e "${CYAN}[5]${NC} 🔗 Создать/проверить Docker сеть"
    echo -e "${CYAN}[6]${NC} 🔌 Подключить бот к сети"
    echo -e "${CYAN}[7]${NC} 🔄 Перезагрузить Caddy/Nginx"
    echo -e "${CYAN}[8]${NC} 🧹 Очистить конфликтующие сети Docker"
    echo -e "${CYAN}[0]${NC} 🔙 Вернуться в главное меню"
    
    echo ""
    read -rp "$(echo -e ${WHITE}${BOLD}Выберите опцию: ${NC})" choice
    
    case $choice in
      1)
        show_proxy_status
        ;;
      2)
        if [[ "$caddy_installed" == "false" ]]; then
          print_warning "Caddy не установлен"
          read -rp "Установить Caddy сейчас? [y/N]: " install_confirm
          if [[ "${install_confirm,,}" == "y" ]]; then
            install_caddy
            caddy_path="$INSTALL_PATH/caddy"
          else
            continue
          fi
        fi
        
        # Проверяем и запрашиваем путь к Caddyfile
        if [[ -z "$caddy_path" ]] || [[ ! -d "$caddy_path" ]]; then
          print_warning "Автоматически определить путь не удалось"
          echo -e "${CYAN}Обнаруженные пути с Caddyfile:${NC}"
          
          # Ищем все возможные Caddyfile
          local found_paths=()
          while IFS= read -r caddyfile; do
            local dir_path
            dir_path=$(dirname "$caddyfile")
            echo -e "  ${GREEN}→${NC} $dir_path"
            found_paths+=("$dir_path")
          done < <(find /opt /root "$INSTALL_PATH" -name "Caddyfile" 2>/dev/null | head -n 5)
          
          if [[ ${#found_paths[@]} -eq 1 ]]; then
            caddy_path="${found_paths[0]}"
            print_info "Используем найденный путь: $caddy_path"
          else
            read -rp "Введите путь к директории с Caddyfile: " caddy_path
          fi
        fi
        
        if [[ ! -d "$caddy_path" ]]; then
          print_error "Директория не найдена: $caddy_path"
          continue
        fi
        
        if [[ ! -f "$caddy_path/Caddyfile" ]]; then
          print_error "Файл Caddyfile не найден в $caddy_path"
          read -rp "Создать новый Caddyfile? [y/N]: " create_new
          if [[ "${create_new,,}" != "y" ]]; then
            continue
          fi
          touch "$caddy_path/Caddyfile"
        fi
        
        local webhook_config
        local miniapp_config
        webhook_config=$(configure_webhook_proxy)
        miniapp_config=$(configure_miniapp_proxy)
        
        echo ""
        print_info "Предпросмотр конфигурации:"
        echo -e "${YELLOW}$webhook_config${NC}"
        echo -e "${YELLOW}$miniapp_config${NC}"
        
        read -rp "Применить эту конфигурацию? [y/N]: " confirm
        if [[ "${confirm,,}" == "y" ]]; then
          apply_caddy_config "$caddy_path" "$webhook_config" "$miniapp_config"
          connect_bot_to_network
        fi
        ;;
      3)
        if [[ "$nginx_installed" == "true" ]]; then
          if [[ -z "$nginx_path" ]]; then
            read -rp "Введите путь к директории с nginx.conf: " nginx_path
          fi
          
          if [[ ! -d "$nginx_path" ]]; then
            print_error "Директория не найдена: $nginx_path"
            continue
          fi
          
          read -rp "Введите домен для webhook: " webhook_domain
          read -rp "Введите домен для miniapp: " miniapp_domain
          
          if [[ -n "$webhook_domain" ]] && [[ -n "$miniapp_domain" ]]; then
            apply_nginx_config "$nginx_path" "$webhook_domain" "$miniapp_domain"
            connect_bot_to_network
          fi
        fi
        ;;
      4)
        if [[ "$caddy_installed" == "false" ]]; then
          install_caddy
        else
          # Редактирование существующего Caddyfile
          if [[ -z "$caddy_path" ]]; then
            read -rp "Введите путь к директории с Caddyfile: " caddy_path
          fi
          
          if [[ ! -f "$caddy_path/Caddyfile" ]]; then
            print_error "Caddyfile не найден в $caddy_path"
            continue
          fi
          
          print_info "Открываем Caddyfile для редактирования..."
          print_warning "Будет создана резервная копия"
          
          # Создаем резервную копию
          cp "$caddy_path/Caddyfile" "$caddy_path/Caddyfile.backup.$(date +%Y%m%d_%H%M%S)"
          
          # Открываем в редакторе
          ${EDITOR:-nano} "$caddy_path/Caddyfile"
          
          print_info "Проверяем конфигурацию..."
          local caddy_container
          caddy_container=$(docker ps --filter "name=caddy" --format "{{.Names}}" | head -n1)
          
          if [[ -n "$caddy_container" ]]; then
            if docker exec "$caddy_container" caddy validate --config /etc/caddy/Caddyfile 2>&1; then
              print_success "Конфигурация валидна"
              read -rp "Перезагрузить Caddy? [Y/n]: " reload_confirm
              if [[ "${reload_confirm,,}" != "n" ]]; then
                docker exec "$caddy_container" caddy reload --config /etc/caddy/Caddyfile
                print_success "Caddy перезагружен"
              fi
            else
              print_error "Конфигурация содержит ошибки!"
              read -rp "Восстановить из резервной копии? [Y/n]: " restore_confirm
              if [[ "${restore_confirm,,}" != "n" ]]; then
                local last_backup
                last_backup=$(ls -t "$caddy_path"/Caddyfile.backup.* 2>/dev/null | head -n1)
                if [[ -n "$last_backup" ]]; then
                  cp "$last_backup" "$caddy_path/Caddyfile"
                  print_success "Конфигурация восстановлена"
                fi
              fi
            fi
          fi
        fi
        ;;
      5)
        create_bot_network
        print_success "Сеть проверена/создана"
        ;;
      6)
        connect_bot_to_network
        ;;
      7)
        # Перезагрузка веб-серверов
        print_section "Перезагрузка веб-серверов"
        
        if [[ "$caddy_installed" == "true" ]]; then
          local caddy_container
          caddy_container=$(docker ps --filter "name=caddy" --format "{{.Names}}" | head -n1)
          if [[ -n "$caddy_container" ]]; then
            print_info "Перезагружаем Caddy..."
            docker restart "$caddy_container"
            sleep 2
            if docker ps --filter "name=caddy" --filter "status=running" | grep -q caddy; then
              print_success "Caddy перезапущен успешно"
            else
              print_error "Ошибка при перезапуске Caddy"
            fi
          fi
        fi
        
        if [[ "$nginx_installed" == "true" ]]; then
          local nginx_container
          nginx_container=$(docker ps --filter "name=nginx" --format "{{.Names}}" | head -n1)
          if [[ -n "$nginx_container" ]]; then
            print_info "Перезагружаем Nginx..."
            docker exec "$nginx_container" nginx -s reload 2>/dev/null || docker restart "$nginx_container"
            print_success "Nginx перезагружен успешно"
          fi
        fi
        ;;
      8)
        cleanup_conflicting_networks
        ;;
      0)
        return 0
        ;;
      *)
        print_error "Неверный выбор"
        ;;
    esac
    
    echo ""
    read -rp "$(echo -e ${CYAN}Нажмите Enter для продолжения...${NC})"
  done
}

# Мониторинг сервисов
show_monitoring() {
  print_header "МОНИТОРИНГ СЕРВИСОВ БОТА"
  
  print_section "Статус контейнеров"
  
  local services=("bot" "postgres" "redis")
  local all_running=true
  
  for service in "${services[@]}"; do
    local status
    status=$(get_service_status "$service")
    local uptime=""
    
    if [[ "$status" == "running" ]]; then
      uptime=$(run_compose ps "$service" 2>/dev/null | tail -n1 | awk '{for(i=1;i<=NF;i++){if($i~/Up/){print $(i+1), $(i+2); break}}}')
      print_status "running" "$service: работает (uptime: $uptime)"
    elif [[ "$status" == "exited" ]] || [[ "$status" == "stopped" ]]; then
      print_status "stopped" "$service: остановлен"
      all_running=false
    else
      print_status "unknown" "$service: не найден"
      all_running=false
    fi
  done
  
  # Статистика ресурсов
  print_section "Использование ресурсов"
  
  local stats
  stats=$(docker stats --no-stream --format "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}" 2>/dev/null | grep -E "bot|postgres|redis" || echo "")
  
  if [[ -n "$stats" ]]; then
    echo -e "${WHITE}${BOLD}КОНТЕЙНЕР          CPU       ПАМЯТЬ${NC}"
    echo "$stats" | tail -n+2 | while IFS=$'\t' read -r name cpu mem; do
      echo -e "${CYAN}${name}${NC}  ${YELLOW}${cpu}${NC}  ${PURPLE}${mem}${NC}"
    done
  else
    print_warning "Статистика недоступна"
  fi
  
  # Размер логов
  print_section "Размер логов"
  if [[ -d "$INSTALL_PATH/logs" ]]; then
    local log_size
    log_size=$(du -sh "$INSTALL_PATH/logs" 2>/dev/null | cut -f1)
    echo -e "${CYAN}Логи: ${YELLOW}${log_size}${NC}"
  fi
  
  # Последние ошибки
  print_section "Последние ошибки (если есть)"
  local errors
  errors=$(run_compose logs --tail=100 bot 2>/dev/null | grep -i "error\|exception\|critical" | tail -n 5 || echo "")
  
  if [[ -n "$errors" ]]; then
    echo "$errors" | while read -r line; do
      print_error "$line"
    done
  else
    print_success "Ошибок не обнаружено"
  fi
  
  echo ""
  if $all_running; then
    print_success "Все сервисы работают нормально!"
  else
    print_warning "Некоторые сервисы не запущены"
  fi
}

# Обновление из Git
update_from_git() {
  print_header "ОБНОВЛЕНИЕ ИЗ GIT РЕПОЗИТОРИЯ"
  
  if [[ ! -d "$INSTALL_PATH/.git" ]]; then
    print_error "Git репозиторий не найден в $INSTALL_PATH"
    print_info "Инициализируем репозиторий..."
    
    local repo_url
    read -rp "Введите URL Git репозитория: " repo_url
    
    if [[ -z "$repo_url" ]]; then
      print_error "URL не указан"
      return 1
    fi
    
    (cd "$INSTALL_PATH" && git init && git remote add origin "$repo_url")
  fi
  
  print_section "Проверка обновлений"
  
  (cd "$INSTALL_PATH" && git fetch origin 2>&1)
  
  local current_commit
  local remote_commit
  current_commit=$(cd "$INSTALL_PATH" && git rev-parse HEAD 2>/dev/null || echo "unknown")
  remote_commit=$(cd "$INSTALL_PATH" && git rev-parse origin/main 2>/dev/null || git rev-parse origin/master 2>/dev/null || echo "unknown")
  
  if [[ "$current_commit" == "$remote_commit" ]]; then
    print_success "Бот уже имеет последнюю версию"
    return 0
  fi
  
  print_info "Найдены обновления"
  echo -e "${CYAN}Текущий коммит: ${YELLOW}${current_commit:0:8}${NC}"
  echo -e "${CYAN}Новый коммит:   ${YELLOW}${remote_commit:0:8}${NC}"
  
  # Показываем изменения
  print_section "Список изменений"
  (cd "$INSTALL_PATH" && git log --oneline HEAD..origin/main 2>/dev/null || git log --oneline HEAD..origin/master 2>/dev/null || true)
  
  echo ""
  read -rp "$(echo -e ${YELLOW}Применить обновления? [y/N]: ${NC})" confirm
  
  if [[ "${confirm,,}" != "y" ]]; then
    print_warning "Обновление отменено"
    return 1
  fi
  
  # Создаем резервную копию перед обновлением
  print_info "Создаем резервную копию перед обновлением..."
  create_backup "pre-update"
  
  print_section "Применение обновлений"
  
  # Останавливаем бота
  print_info "Останавливаем сервисы..."
  run_compose down
  
  # Обновляем код
  print_info "Обновляем код..."
  (cd "$INSTALL_PATH" && git pull origin main 2>/dev/null || git pull origin master 2>/dev/null)
  
  # Перезапускаем
  print_info "Пересобираем и запускаем сервисы..."
  run_compose up -d --build
  
  print_success "Обновление завершено!"
  
  # Показываем логи
  echo ""
  read -rp "$(echo -e ${YELLOW}Показать логи запуска? [y/N]: ${NC})" show_logs
  if [[ "${show_logs,,}" == "y" ]]; then
    run_compose logs --tail=50 -f bot
  fi
}

# Создание резервной копии
create_backup() {
  local backup_type=${1:-manual}
  local timestamp
  timestamp=$(date +%Y%m%d_%H%M%S)
  local backup_name="backup_${backup_type}_${timestamp}"
  local backup_path="$BACKUP_DIR/$backup_name"
  
  print_header "СОЗДАНИЕ РЕЗЕРВНОЙ КОПИИ"
  
  mkdir -p "$BACKUP_DIR"
  mkdir -p "$backup_path"
  
  print_section "Архивирование данных"
  
  # Копируем конфигурацию
  print_info "Сохраняем конфигурацию..."
  cp "$INSTALL_PATH/.env" "$backup_path/" 2>/dev/null || true
  cp "$INSTALL_PATH/docker-compose.yml" "$backup_path/" 2>/dev/null || true
  
  # Экспортируем базу данных
  if [[ $(get_service_status "postgres") == "running" ]]; then
    print_info "Экспортируем базу данных PostgreSQL..."
    run_compose exec -T postgres pg_dump -U postgres remnawave_bot > "$backup_path/database.sql" 2>/dev/null || {
      print_warning "Не удалось экспортировать БД"
    }
  fi
  
  # Копируем данные
  if [[ -d "$INSTALL_PATH/data" ]]; then
    print_info "Копируем пользовательские данные..."
    cp -r "$INSTALL_PATH/data" "$backup_path/" 2>/dev/null || true
  fi
  
  # Создаем архив
  print_info "Создаем архив..."
  (cd "$BACKUP_DIR" && tar -czf "${backup_name}.tar.gz" "$backup_name" && rm -rf "$backup_name")
  
  local backup_size
  backup_size=$(du -h "$BACKUP_DIR/${backup_name}.tar.gz" | cut -f1)
  
  print_success "Резервная копия создана: $BACKUP_DIR/${backup_name}.tar.gz"
  echo -e "${CYAN}Размер: ${YELLOW}${backup_size}${NC}"
  
  # Очистка старых бэкапов (оставляем последние 10)
  print_info "Очистка старых бэкапов..."
  (cd "$BACKUP_DIR" && ls -t backup_*.tar.gz 2>/dev/null | tail -n +11 | xargs -r rm -f)
  
  local backup_count
  backup_count=$(ls -1 "$BACKUP_DIR"/backup_*.tar.gz 2>/dev/null | wc -l)
  print_info "Всего резервных копий: $backup_count"
}

# Восстановление из резервной копии
restore_backup() {
  print_header "ВОССТАНОВЛЕНИЕ ИЗ РЕЗЕРВНОЙ КОПИИ"
  
  if [[ ! -d "$BACKUP_DIR" ]] || [[ -z "$(ls -A "$BACKUP_DIR"/*.tar.gz 2>/dev/null)" ]]; then
    print_error "Резервные копии не найдены"
    return 1
  fi
  
  print_section "Доступные резервные копии"
  
  local backups=()
  local i=1
  while IFS= read -r backup; do
    local backup_name
    local backup_size
    local backup_date
    backup_name=$(basename "$backup")
    backup_size=$(du -h "$backup" | cut -f1)
    backup_date=$(stat -c %y "$backup" 2>/dev/null | cut -d' ' -f1,2 | cut -d'.' -f1 || stat -f "%Sm" "$backup")
    
    echo -e "${CYAN}[$i]${NC} ${WHITE}$backup_name${NC}"
    echo -e "    Размер: ${YELLOW}$backup_size${NC}, Дата: ${PURPLE}$backup_date${NC}"
    backups+=("$backup")
    ((i++))
  done < <(ls -t "$BACKUP_DIR"/*.tar.gz 2>/dev/null)
  
  echo ""
  read -rp "Выберите номер резервной копии для восстановления [1-$((i-1))]: " selection
  
  if [[ ! "$selection" =~ ^[0-9]+$ ]] || [[ "$selection" -lt 1 ]] || [[ "$selection" -ge "$i" ]]; then
    print_error "Неверный выбор"
    return 1
  fi
  
  local selected_backup="${backups[$((selection-1))]}"
  
  print_warning "ВНИМАНИЕ: Текущие данные будут перезаписаны!"
  read -rp "$(echo -e ${RED}${BOLD}Продолжить восстановление? [y/N]: ${NC})" confirm
  
  if [[ "${confirm,,}" != "y" ]]; then
    print_warning "Восстановление отменено"
    return 1
  fi
  
  # Создаем резервную копию перед восстановлением
  print_info "Создаем резервную копию текущего состояния..."
  create_backup "pre-restore"
  
  print_section "Восстановление данных"
  
  # Останавливаем сервисы
  print_info "Останавливаем сервисы..."
  run_compose down
  
  # Распаковываем бэкап
  print_info "Распаковываем резервную копию..."
  local temp_dir
  temp_dir=$(mktemp -d)
  tar -xzf "$selected_backup" -C "$temp_dir"
  
  local backup_folder
  backup_folder=$(ls "$temp_dir")
  
  # Восстанавливаем конфигурацию
  if [[ -f "$temp_dir/$backup_folder/.env" ]]; then
    print_info "Восстанавливаем конфигурацию..."
    cp "$temp_dir/$backup_folder/.env" "$INSTALL_PATH/"
  fi
  
  # Восстанавливаем данные
  if [[ -d "$temp_dir/$backup_folder/data" ]]; then
    print_info "Восстанавливаем пользовательские данные..."
    rm -rf "$INSTALL_PATH/data"
    cp -r "$temp_dir/$backup_folder/data" "$INSTALL_PATH/"
  fi
  
  # Запускаем сервисы
  print_info "Запускаем сервисы..."
  run_compose up -d
  
  # Восстанавливаем БД
  if [[ -f "$temp_dir/$backup_folder/database.sql" ]]; then
    print_info "Ожидаем запуска PostgreSQL..."
    sleep 5
    print_info "Восстанавливаем базу данных..."
    run_compose exec -T postgres psql -U postgres remnawave_bot < "$temp_dir/$backup_folder/database.sql" 2>/dev/null || {
      print_warning "Не удалось восстановить БД (возможно, структура уже актуальна)"
    }
  fi
  
  # Очистка
  rm -rf "$temp_dir"
  
  print_success "Восстановление завершено!"
  
  echo ""
  show_monitoring
}

# Просмотр логов
view_logs() {
  print_header "ПРОСМОТР ЛОГОВ"
  
  echo -e "${CYAN}[1]${NC} Логи бота (последние 100 строк)"
  echo -e "${CYAN}[2]${NC} Логи PostgreSQL (последние 100 строк)"
  echo -e "${CYAN}[3]${NC} Логи Redis (последние 100 строк)"
  echo -e "${CYAN}[4]${NC} Все логи (последние 100 строк)"
  echo -e "${CYAN}[5]${NC} Следить за логами в реальном времени"
  echo -e "${CYAN}[6]${NC} Поиск по логам"
  
  echo ""
  read -rp "Выберите опцию [1-6]: " choice
  
  case $choice in
    1)
      run_compose logs --tail=100 bot
      ;;
    2)
      run_compose logs --tail=100 postgres
      ;;
    3)
      run_compose logs --tail=100 redis
      ;;
    4)
      run_compose logs --tail=100
      ;;
    5)
      print_info "Нажмите Ctrl+C для выхода"
      run_compose logs -f
      ;;
    6)
      read -rp "Введите текст для поиска: " search_term
      run_compose logs | grep -i "$search_term" --color=always | tail -n 50
      ;;
    *)
      print_error "Неверный выбор"
      ;;
  esac
}

# Управление сервисами
manage_services() {
  print_header "УПРАВЛЕНИЕ СЕРВИСАМИ"
  
  echo -e "${CYAN}[1]${NC} Запустить все сервисы"
  echo -e "${CYAN}[2]${NC} Остановить все сервисы"
  echo -e "${CYAN}[3]${NC} Перезапустить все сервисы"
  echo -e "${CYAN}[4]${NC} Пересобрать и запустить"
  echo -e "${CYAN}[5]${NC} Остановить и удалить контейнеры"
  
  echo ""
  read -rp "Выберите опцию [1-5]: " choice
  
  case $choice in
    1)
      print_info "Запускаем сервисы..."
      run_compose up -d
      print_success "Сервисы запущены"
      show_monitoring
      ;;
    2)
      print_info "Останавливаем сервисы..."
      run_compose stop
      print_success "Сервисы остановлены"
      ;;
    3)
      print_info "Перезапускаем сервисы..."
      run_compose restart
      print_success "Сервисы перезапущены"
      show_monitoring
      ;;
    4)
      print_info "Пересобираем и запускаем..."
      run_compose up -d --build
      print_success "Сервисы пересобраны и запущены"
      show_monitoring
      ;;
    5)
      print_warning "Контейнеры будут удалены (данные сохранятся в volumes)"
      read -rp "$(echo -e ${YELLOW}Продолжить? [y/N]: ${NC})" confirm
      if [[ "${confirm,,}" == "y" ]]; then
        run_compose down
        print_success "Контейнеры остановлены и удалены"
      fi
      ;;
    *)
      print_error "Неверный выбор"
      ;;
  esac
}

# Очистка системы
cleanup_system() {
  print_header "ОЧИСТКА СИСТЕМЫ"
  
  echo -e "${CYAN}[1]${NC} Очистить старые логи (старше 7 дней)"
  echo -e "${CYAN}[2]${NC} Очистить старые резервные копии (оставить 5 последних)"
  echo -e "${CYAN}[3]${NC} Очистить неиспользуемые Docker образы"
  echo -e "${CYAN}[4]${NC} Полная очистка (всё вышеперечисленное)"
  
  echo ""
  read -rp "Выберите опцию [1-4]: " choice
  
  case $choice in
    1)
      print_info "Очищаем старые логи..."
      find "$INSTALL_PATH/logs" -type f -mtime +7 -delete 2>/dev/null || true
      print_success "Старые логи удалены"
      ;;
    2)
      print_info "Очищаем старые бэкапы..."
      (cd "$BACKUP_DIR" && ls -t backup_*.tar.gz 2>/dev/null | tail -n +6 | xargs -r rm -f)
      print_success "Старые бэкапы удалены"
      ;;
    3)
      print_info "Очищаем неиспользуемые Docker образы..."
      docker image prune -f
      print_success "Неиспользуемые образы удалены"
      ;;
    4)
      print_info "Выполняем полную очистку..."
      find "$INSTALL_PATH/logs" -type f -mtime +7 -delete 2>/dev/null || true
      (cd "$BACKUP_DIR" && ls -t backup_*.tar.gz 2>/dev/null | tail -n +6 | xargs -r rm -f)
      docker image prune -f
      docker volume prune -f
      print_success "Полная очистка завершена"
      ;;
    *)
      print_error "Неверный выбор"
      ;;
  esac
}

# Главное меню
show_menu() {
  clear
  echo -e "${PURPLE}${BOLD}"
  cat << "EOF"
╔════════════════════════════════════════════════════════════╗
║                                                            ║
║     ██████╗  ██████╗ ████████╗    ███╗   ███╗ ██████╗ ██████╗  
║     ██╔══██╗██╔═══██╗╚══██╔══╝    ████╗ ████║██╔════╝ ██╔══██╗
║     ██████╔╝██║   ██║   ██║       ██╔████╔██║██║  ███╗██████╔╝
║     ██╔══██╗██║   ██║   ██║       ██║╚██╔╝██║██║   ██║██╔══██╗
║     ██████╔╝╚██████╔╝   ██║       ██║ ╚═╝ ██║╚██████╔╝██║  ██║
║     ╚═════╝  ╚═════╝    ╚═╝       ╚═╝     ╚═╝ ╚═════╝ ╚═╝  ╚═╝
║                                                            ║
║              Система управления Telegram ботом            ║
╚════════════════════════════════════════════════════════════╝
EOF
  echo -e "${NC}"
  
  echo -e "${WHITE}${BOLD}Путь установки:${NC} ${CYAN}$INSTALL_PATH${NC}"
  echo ""
  
  echo -e "${GREEN}${BOLD}[1]${NC} ${STAR} Мониторинг и статус сервисов"
  echo -e "${BLUE}${BOLD}[2]${NC} ${GEAR} Управление сервисами"
  echo -e "${YELLOW}${BOLD}[3]${NC} 📋 Просмотр логов"
  echo -e "${PURPLE}${BOLD}[4]${NC} 🔄 Обновление из Git"
  echo -e "${CYAN}${BOLD}[5]${NC} 💾 Создать резервную копию"
  echo -e "${YELLOW}${BOLD}[6]${NC} 📦 Восстановить из резервной копии"
  echo -e "${RED}${BOLD}[7]${NC} 🧹 Очистка системы"
  echo -e "${PURPLE}${BOLD}[8]${NC} 🌐 Настройка обратного прокси (Caddy/Nginx)"
  echo -e "${WHITE}${BOLD}[0]${NC} 🚪 Выход"
  
  echo ""
}

main() {
  load_state
  resolve_compose_command
  
  while true; do
    show_menu
    read -rp "$(echo -e ${WHITE}${BOLD}Выберите опцию: ${NC})" choice
    
    case $choice in
      1)
        show_monitoring
        ;;
      2)
        manage_services
        ;;
      3)
        view_logs
        ;;
      4)
        update_from_git
        ;;
      5)
        create_backup "manual"
        ;;
      6)
        restore_backup
        ;;
      7)
        cleanup_system
        ;;
      8)
        configure_reverse_proxy
        ;;
      0)
        print_success "До свидания!"
        exit 0
        ;;
      *)
        print_error "Неверный выбор. Попробуйте снова."
        ;;
    esac
    
    echo ""
    read -rp "$(echo -e ${CYAN}Нажмите Enter для продолжения...${NC})"
  done
}

main "$@"
