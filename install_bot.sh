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
