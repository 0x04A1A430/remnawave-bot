#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
STATE_FILE="$SCRIPT_DIR/.bot_install_state"
DEFAULT_INSTALL_PATH="$SCRIPT_DIR"
DEFAULT_ENV_FILE=".env"

# Util functions
log() {
  printf '\n%s\n' "$1"
}

error() {
  printf 'Error: %s\n' "$1" >&2
}

ask_yes_no() {
  local prompt="$1"
  local default="$2"
  local reply
  while true; do
    read -rp "$prompt" reply || reply=""
    if [[ -z "$reply" ]]; then
      reply="$default"
    fi
    case "${reply,,}" in
      y|yes)
        return 0
        ;;
      n|no)
        return 1
        ;;
      *)
        echo "Пожалуйста, ответьте 'y' или 'n'."
        ;;
    esac
  done
}

prompt_value() {
  local var_name="$1"
  local prompt="$2"
  local default_value="$3"
  local reply
  if [[ -n "$default_value" ]]; then
    read -rp "$prompt [$default_value]: " reply || reply=""
  else
    read -rp "$prompt: " reply || reply=""
  fi
  if [[ -z "$reply" ]]; then
    printf '%s' "$default_value"
  else
    printf '%s' "$reply"
  fi
}

escape_env_value() {
  printf '%s' "$1" | sed 's/\\\\/\\\\\\\\/g; s/"/\\"/g'
}

write_env_file() {
  local env_file="$1"
  shift
  local entries=("$@")
  {
    for entry in "${entries[@]}"; do
      local key=${entry%%=*}
      local value=${entry#*=}
      printf '%s="%s"\n' "$key" "$(escape_env_value "$value")"
    done
  } >"$env_file"
}

get_env_value() {
  local key="$1"
  local env_file="$2"
  if [[ -f "$env_file" ]]; then
    local line
    line=$(grep -E "^${key}=" "$env_file" | tail -n1 || true)
    if [[ -n "$line" ]]; then
      printf '%s' "${line#*=}" | sed 's/^"//; s/"$//'
      return
    fi
  fi
  printf ''
}

ensure_directory_structure() {
  local path="$1"
  mkdir -p "$path/logs" "$path/data" "$path/data/backups" "$path/data/referral_qr"
  chmod -R 755 "$path/logs" "$path/data"
  if command -v sudo >/dev/null 2>&1; then
    sudo chown -R 1000:1000 "$path/logs" "$path/data" || true
  else
    chown -R 1000:1000 "$path/logs" "$path/data" 2>/dev/null || true
  fi
}

sync_project_files() {
  local destination="$1"
  if [[ "$SCRIPT_DIR" == "$destination" ]]; then
    return
  fi

  log "Синхронизируем файлы проекта в $destination"
  local excludes=(--exclude '.git' --exclude '__pycache__' --exclude 'logs' --exclude 'data' --exclude '.bot_install_state' --exclude '.env')
  if command -v rsync >/dev/null 2>&1; then
    rsync -a --delete "${excludes[@]}" "$SCRIPT_DIR/" "$destination/"
  else
    (cd "$SCRIPT_DIR" && tar cf - --exclude='.git' --exclude='__pycache__' --exclude='logs' --exclude='data' --exclude='.bot_install_state' --exclude='.env' .) \
      | (cd "$destination" && tar xf -)
  fi
}

load_state() {
  if [[ -f "$STATE_FILE" ]]; then
    # shellcheck disable=SC1090
    source "$STATE_FILE"
  else
    INSTALL_PATH="$DEFAULT_INSTALL_PATH"
    ENV_FILE="$DEFAULT_ENV_FILE"
  fi
}

save_state() {
  cat >"$STATE_FILE" <<EOF_STATE
INSTALL_PATH="$INSTALL_PATH"
ENV_FILE="$ENV_FILE"
INSTALLED_AT="$(date --iso-8601=seconds)"
EOF_STATE
}

resolve_compose_command() {
  if docker compose version >/dev/null 2>&1; then
    COMPOSE_BIN=(docker compose)
  elif docker-compose version >/dev/null 2>&1; then
    COMPOSE_BIN=(docker-compose)
  else
    error "Docker Compose не найден. Установите docker compose или docker-compose."
    exit 1
  fi
}

run_compose() {
  (cd "$INSTALL_PATH" && "${COMPOSE_BIN[@]}" "$@")
}

show_services_state() {
  log "Текущее состояние сервисов:" 
  run_compose ps
}

follow_logs() {
  if ask_yes_no "Хотите посмотреть последние логи бота? [y/N]: " "n"; then
    run_compose logs --tail=50 bot || true
  fi
}

start_services() {
  log "Запускаем сервисы бота..."
  run_compose up -d
  show_services_state
  follow_logs
}

perform_installation() {
  INSTALL_PATH=$(prompt_value "INSTALL_PATH" "Укажите путь установки бота" "$DEFAULT_INSTALL_PATH")
  INSTALL_PATH=${INSTALL_PATH%/}
  if [[ ! -d "$INSTALL_PATH" ]]; then
    mkdir -p "$INSTALL_PATH"
    log "Создана директория установки $INSTALL_PATH"
  fi

  sync_project_files "$INSTALL_PATH"

  ENV_FILE="$INSTALL_PATH/$DEFAULT_ENV_FILE"
  ensure_directory_structure "$INSTALL_PATH"

  local existing_env
  if [[ -f "$ENV_FILE" ]]; then
    existing_env="true"
  else
    existing_env="false"
  fi

  log "Заполняем параметры окружения (.env)."
  local bot_token admin_ids api_url api_key auth_type username password secret_key web_api_token
  bot_token=$(prompt_value "BOT_TOKEN" "Введите BOT_TOKEN" "$(get_env_value BOT_TOKEN "$ENV_FILE")")
  admin_ids=$(prompt_value "ADMIN_IDS" "Введите ADMIN_IDS (через запятую)" "$(get_env_value ADMIN_IDS "$ENV_FILE")")

  printf '\nREMNAWAVE_API_URL должен быть в формате https://panel.example.com или http://remnawave:3000 при локальном запуске.\n'
  api_url=$(prompt_value "REMNAWAVE_API_URL" "Введите REMNAWAVE_API_URL" "$(get_env_value REMNAWAVE_API_URL "$ENV_FILE")")
  api_key=$(prompt_value "REMNAWAVE_API_KEY" "Введите REMNAWAVE_API_KEY" "$(get_env_value REMNAWAVE_API_KEY "$ENV_FILE")")

  local default_auth
  default_auth=$(get_env_value REMNAWAVE_AUTH_TYPE "$ENV_FILE")
  auth_type=$(prompt_value "REMNAWAVE_AUTH_TYPE" "Введите тип авторизации REMNAWAVE (api_key/basic_auth)" "${default_auth:-api_key}")

  username=""
  password=""
  if [[ "${auth_type}" == "basic_auth" ]]; then
    username=$(prompt_value "REMNAWAVE_USERNAME" "Введите REMNAWAVE_USERNAME" "$(get_env_value REMNAWAVE_USERNAME "$ENV_FILE")")
    password=$(prompt_value "REMNAWAVE_PASSWORD" "Введите REMNAWAVE_PASSWORD" "$(get_env_value REMNAWAVE_PASSWORD "$ENV_FILE")")
  else
    if ask_yes_no "Указать REMNAWAVE_USERNAME/REMNAWAVE_PASSWORD? [y/N]: " "n"; then
      username=$(prompt_value "REMNAWAVE_USERNAME" "Введите REMNAWAVE_USERNAME" "$(get_env_value REMNAWAVE_USERNAME "$ENV_FILE")")
      password=$(prompt_value "REMNAWAVE_PASSWORD" "Введите REMNAWAVE_PASSWORD" "$(get_env_value REMNAWAVE_PASSWORD "$ENV_FILE")")
    fi
  fi

  secret_key=$(prompt_value "REMNAWAVE_SECRET_KEY" "Введите REMNAWAVE_SECRET_KEY (для установок eGames, формат XXXXXXX:DDDDDDDD, можно оставить пустым)" "$(get_env_value REMNAWAVE_SECRET_KEY "$ENV_FILE")")
  web_api_token=$(prompt_value "WEB_API_DEFAULT_TOKEN" "Введите WEB_API_DEFAULT_TOKEN" "$(get_env_value WEB_API_DEFAULT_TOKEN "$ENV_FILE")")

  local entries=()
  entries+=("BOT_TOKEN=$bot_token")
  entries+=("ADMIN_IDS=$admin_ids")
  entries+=("REMNAWAVE_API_URL=$api_url")
  entries+=("REMNAWAVE_API_KEY=$api_key")
  entries+=("REMNAWAVE_AUTH_TYPE=$auth_type")
  entries+=("REMNAWAVE_USERNAME=$username")
  entries+=("REMNAWAVE_PASSWORD=$password")
  entries+=("REMNAWAVE_SECRET_KEY=$secret_key")
  entries+=("WEB_API_DEFAULT_TOKEN=$web_api_token")

  write_env_file "$ENV_FILE" "${entries[@]}"
  log "Файл окружения сохранен: $ENV_FILE"

  save_state
  log "Информация об установке сохранена ($STATE_FILE)."

  resolve_compose_command
  start_services
}

show_existing_installation() {
  log "Найдена предыдущая установка бота."
  log "Путь установки: $INSTALL_PATH"
  log "Файл окружения: $ENV_FILE"
  resolve_compose_command
  show_services_state
  if ask_yes_no "Перезапустить сервисы бота? [y/N]: " "n"; then
    run_compose restart
    show_services_state
  fi
  follow_logs
}

main() {
  load_state
  if [[ -f "$STATE_FILE" ]]; then
    if ask_yes_no "Обнаружена существующая установка. Переустановить? [y/N]: " "n"; then
      perform_installation
    else
      show_existing_installation
    fi
  else
    perform_installation
  fi
}

main "$@"
