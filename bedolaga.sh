#!/bin/bash

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

BOT_DIR="/opt/bedolaga-bot"
COMPOSE_FILE="$BOT_DIR/docker-compose.yml"
ENV_FILE="$BOT_DIR/.env"
SERVICE_FILE="/etc/systemd/system/bedolaga-bot.service"

log() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

check_root() {
    if [ "$EUID" -ne 0 ]; then
        error "Этот скрипт должен быть запущен от имени root (используйте sudo)"
        exit 1
    fi
}

check_installation() {
    if [ -d "$BOT_DIR" ] && [ -f "$COMPOSE_FILE" ] && [ -f "$ENV_FILE" ]; then
        return 0
    else
        return 1
    fi
}

update_system() {
    log "Обновление системы Ubuntu..."
    apt update && apt upgrade -y
    log "Система обновлена успешно"
}

install_docker() {
    log "Установка Docker..."
    
    apt remove -y docker docker-engine docker.io containerd runc
    
    apt install -y apt-transport-https ca-certificates curl gnupg lsb-release nano
    
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /usr/share/keyrings/docker-archive-keyring.gpg
    
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/docker-archive-keyring.gpg] https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" | tee /etc/apt/sources.list.d/docker.list > /dev/null
    
    apt update
    apt install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
    
    systemctl enable docker
    systemctl start docker
    
    log "Docker установлен успешно"
}

ensure_nano() {
    if ! command -v nano &> /dev/null; then
        log "Установка nano..."
        apt update
        apt install -y nano
        log "Nano установлен успешно"
    fi
}

create_project_structure() {
    log "Создание структуры проекта..."
    
    mkdir -p "$BOT_DIR"
    mkdir -p "$BOT_DIR/logs"
    mkdir -p "$BOT_DIR/data"
    
    log "Структура проекта создана в $BOT_DIR"
}

validate_domain() {
    local domain="$1"
    if [[ $domain =~ ^[a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?(\.[a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?)*$ ]]; then
        return 0
    else
        return 1
    fi
}

create_docker_compose() {
    log "Создание docker-compose.yml..."
    
    echo "Выберите конфигурацию установки:"
    echo "1) Только бот (панель RemnaWave на другом сервере)"
    echo "2) Панель + бот на одном сервере (рекомендуется)"
    echo "3) Расширенная - с Redis и Nginx"
    echo "4) Бот с webhook через Caddy"
    echo "5) Бот с webhook через Nginx"
    
    while true; do
        read -p "Ваш выбор (1-5): " choice
        case $choice in
            1)
                create_standalone_compose
                break
                ;;
            2)
                create_panel_bot_compose
                break
                ;;
            3)
                create_full_compose
                break
                ;;
            4)
                create_webhook_caddy_compose
                break
                ;;
            5)
                create_webhook_nginx_compose
                break
                ;;
            *)
                error "Неверный выбор. Попробуйте снова."
                ;;
        esac
    done
}

create_standalone_compose() {
    cat > "$COMPOSE_FILE" << 'EOF'
services:
  # PostgreSQL Database
  postgres:
    image: postgres:15-alpine
    container_name: remnawave_bot_db
    restart: unless-stopped
    environment:
      POSTGRES_DB: remnawave_bot
      POSTGRES_USER: remnawave_user
      POSTGRES_PASSWORD: secure_password_123
      POSTGRES_INITDB_ARGS: "--encoding=UTF-8 --lc-collate=C --lc-ctype=C"
    volumes:
      - postgres_data:/var/lib/postgresql/data
    networks:
      - bot_network
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U remnawave_user -d remnawave_bot"]
      interval: 15s
      timeout: 10s
      retries: 5
      start_period: 30s

  # RemnaWave Bot
  bot:
    image: fr1ngg/remnawave-bedolaga-telegram-bot:latest
    container_name: remnawave_bot
    restart: unless-stopped
    depends_on:
      postgres:
        condition: service_healthy
    env_file:
      - .env
    environment:
      DATABASE_URL: postgresql+asyncpg://remnawave_user:secure_password_123@postgres:5432/remnawave_bot
    volumes:
      - ./logs:/app/logs
      - ./data:/app/data
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
EOF

    log "Конфигурация только для бота создана"
    export COMPOSE_TYPE="standalone"
}

create_panel_bot_compose() {
    cat > "$COMPOSE_FILE" << 'EOF'
services:
  # PostgreSQL Database
  postgres:
    image: postgres:15-alpine
    container_name: remnawave_bot_db
    restart: unless-stopped
    environment:
      POSTGRES_DB: remnawave_bot
      POSTGRES_USER: remnawave_user
      POSTGRES_PASSWORD: secure_password_123
      POSTGRES_INITDB_ARGS: "--encoding=UTF-8 --lc-collate=C --lc-ctype=C"
    volumes:
      - postgres_data:/var/lib/postgresql/data
    networks:
      - remnawave-network
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U remnawave_user -d remnawave_bot"]
      interval: 15s
      timeout: 10s
      retries: 5
      start_period: 30s

  # RemnaWave Bot
  bot:
    image: fr1ngg/remnawave-bedolaga-telegram-bot:latest
    container_name: remnawave_bot
    restart: unless-stopped
    depends_on:
      postgres:
        condition: service_healthy
    env_file:
      - .env
    environment:
      DATABASE_URL: postgresql+asyncpg://remnawave_user:secure_password_123@postgres:5432/remnawave_bot
    volumes:
      - ./logs:/app/logs
      - ./data:/app/data
    networks:
      - remnawave-network
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
  remnawave-network:
    name: remnawave-network
    external: true
EOF

    log "Конфигурация панель + бот на одном сервере создана"
    log "ВАЖНО: Убедитесь что панель RemnaWave уже установлена и создала сеть remnawave-network"
    export COMPOSE_TYPE="panel_bot"
}

create_full_compose() {
    cat > "$COMPOSE_FILE" << 'EOF'
services:
  # PostgreSQL Database
  postgres:
    image: postgres:15-alpine
    container_name: remnawave_bot_db
    restart: unless-stopped
    environment:
      POSTGRES_DB: remnawave_bot
      POSTGRES_USER: remnawave_user
      POSTGRES_PASSWORD: secure_password_123
      POSTGRES_INITDB_ARGS: "--encoding=UTF-8 --lc-collate=C --lc-ctype=C"
    volumes:
      - postgres_data:/var/lib/postgresql/data
    ports:
      - "5432:5432"
    networks:
      - bot_network
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U remnawave_user -d remnawave_bot"]
      interval: 15s
      timeout: 10s
      retries: 5
      start_period: 30s

  # RemnaWave Bot
  bot:
    image: fr1ngg/remnawave-bedolaga-telegram-bot:latest
    container_name: remnawave_bot
    restart: unless-stopped
    depends_on:
      postgres:
        condition: service_healthy
    env_file:
      - .env
    environment:
      DATABASE_URL: postgresql+asyncpg://remnawave_user:secure_password_123@postgres:5432/remnawave_bot
    volumes:
      - ./logs:/app/logs
      - ./data:/app/data
    networks:
      - bot_network
    healthcheck:
      test: ["CMD-SHELL", "python -c 'print(\"Bot is running\")'"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 30s

  # Redis (для кэширования и улучшения производительности)
  redis:
    image: redis:7-alpine
    container_name: remnawave_bot_redis
    restart: unless-stopped
    command: redis-server --appendonly yes --requirepass redis_password_123
    volumes:
      - redis_data:/data
    ports:
      - "6379:6379"
    networks:
      - bot_network
    healthcheck:
      test: ["CMD", "redis-cli", "--raw", "incr", "ping"]
      interval: 10s
      timeout: 3s
      retries: 5
    profiles:
      - with-redis

  # Nginx (для статических файлов или веб-интерфейса)
  nginx:
    image: nginx:alpine
    container_name: remnawave_bot_nginx
    restart: unless-stopped
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./nginx.conf:/etc/nginx/nginx.conf:ro
      - ./ssl:/etc/nginx/ssl:ro
      - ./static:/usr/share/nginx/html:ro
    networks:
      - bot_network
    depends_on:
      - bot
    profiles:
      - with-nginx

volumes:
  postgres_data:
    driver: local
  redis_data:
    driver: local

networks:
  bot_network:
    driver: bridge
    ipam:
      config:
        - subnet: 172.20.0.0/16
EOF

    log "Расширенная конфигурация Docker Compose создана"
    export COMPOSE_TYPE="full"
}

create_webhook_caddy_compose() {
    while true; do
        read -p "Введите домен для webhook (например: bot.example.com): " WEBHOOK_DOMAIN
        if validate_domain "$WEBHOOK_DOMAIN"; then
            break
        else
            error "Некорректный домен. Попробуйте снова."
        fi
    done
    
    read -p "У вас уже установлен Caddy? (y/n): " caddy_installed
    
    if [[ $caddy_installed =~ ^[Yy] ]]; then
        cat > "$COMPOSE_FILE" << 'EOF'
services:
  # PostgreSQL Database
  postgres:
    image: postgres:15-alpine
    container_name: remnawave_bot_db
    restart: unless-stopped
    environment:
      POSTGRES_DB: remnawave_bot
      POSTGRES_USER: remnawave_user
      POSTGRES_PASSWORD: secure_password_123
      POSTGRES_INITDB_ARGS: "--encoding=UTF-8 --lc-collate=C --lc-ctype=C"
    volumes:
      - postgres_data:/var/lib/postgresql/data
    networks:
      - bot_network
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U remnawave_user -d remnawave_bot"]
      interval: 15s
      timeout: 10s
      retries: 5
      start_period: 30s

  # RemnaWave Bot
  bot:
    image: fr1ngg/remnawave-bedolaga-telegram-bot:latest
    container_name: remnawave_bot
    restart: unless-stopped
    depends_on:
      postgres:
        condition: service_healthy
    env_file:
      - .env
    environment:
      DATABASE_URL: postgresql+asyncpg://remnawave_user:secure_password_123@postgres:5432/remnawave_bot
    volumes:
      - ./logs:/app/logs
      - ./data:/app/data
    ports:
      - "8081:8081"
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
        export WEBHOOK_CONFIG_TYPE="external_caddy"
    else
        cat > "$COMPOSE_FILE" << 'EOF'
services:
  # PostgreSQL Database
  postgres:
    image: postgres:15-alpine
    container_name: remnawave_bot_db
    restart: unless-stopped
    environment:
      POSTGRES_DB: remnawave_bot
      POSTGRES_USER: remnawave_user
      POSTGRES_PASSWORD: secure_password_123
      POSTGRES_INITDB_ARGS: "--encoding=UTF-8 --lc-collate=C --lc-ctype=C"
    volumes:
      - postgres_data:/var/lib/postgresql/data
    networks:
      - bot_network
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U remnawave_user -d remnawave_bot"]
      interval: 15s
      timeout: 10s
      retries: 5
      start_period: 30s

  # RemnaWave Bot
  bot:
    image: fr1ngg/remnawave-bedolaga-telegram-bot:latest
    container_name: remnawave_bot
    restart: unless-stopped
    depends_on:
      postgres:
        condition: service_healthy
    env_file:
      - .env
    environment:
      DATABASE_URL: postgresql+asyncpg://remnawave_user:secure_password_123@postgres:5432/remnawave_bot
    volumes:
      - ./logs:/app/logs
      - ./data:/app/data
    networks:
      - bot_network
    healthcheck:
      test: ["CMD-SHELL", "python -c 'print(\"Bot is running\")'"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 30s

  # Caddy reverse proxy
  caddy:
    image: caddy:2.9.1
    container_name: caddy-bedolaga-bot
    restart: unless-stopped
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile
      - /opt/caddy/html:/var/www/html
      - ./caddy-logs:/var/log/caddy
      - caddy_data:/data
      - caddy_config:/config
    networks:
      - bot_network
    depends_on:
      - bot
    logging:
      driver: "json-file"
      options:
        max-size: "10m"
        max-file: "3"

volumes:
  postgres_data:
    driver: local
  caddy_data:
    driver: local
  caddy_config:
    driver: local

networks:
  bot_network:
    driver: bridge
    ipam:
      config:
        - subnet: 172.20.0.0/16
EOF
        export WEBHOOK_CONFIG_TYPE="included_caddy"
        
        create_caddyfile "$WEBHOOK_DOMAIN"
    fi
    
    log "Конфигурация бота с Caddy webhook создана"
    export COMPOSE_TYPE="webhook_caddy"
    export WEBHOOK_DOMAIN="$WEBHOOK_DOMAIN"
}

create_webhook_nginx_compose() {
    while true; do
        read -p "Введите домен для webhook (например: bot.example.com): " WEBHOOK_DOMAIN
        if validate_domain "$WEBHOOK_DOMAIN"; then
            break
        else
            error "Некорректный домен. Попробуйте снова."
        fi
    done
    
    read -p "У вас уже установлен Nginx? (y/n): " nginx_installed
    
    if [[ $nginx_installed =~ ^[Yy] ]]; then
        cat > "$COMPOSE_FILE" << 'EOF'
services:
  # PostgreSQL Database
  postgres:
    image: postgres:15-alpine
    container_name: remnawave_bot_db
    restart: unless-stopped
    environment:
      POSTGRES_DB: remnawave_bot
      POSTGRES_USER: remnawave_user
      POSTGRES_PASSWORD: secure_password_123
      POSTGRES_INITDB_ARGS: "--encoding=UTF-8 --lc-collate=C --lc-ctype=C"
    volumes:
      - postgres_data:/var/lib/postgresql/data
    networks:
      - bot_network
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U remnawave_user -d remnawave_bot"]
      interval: 15s
      timeout: 10s
      retries: 5
      start_period: 30s

  # RemnaWave Bot
  bot:
    image: fr1ngg/remnawave-bedolaga-telegram-bot:latest
    container_name: remnawave_bot
    restart: unless-stopped
    depends_on:
      postgres:
        condition: service_healthy
    env_file:
      - .env
    environment:
      DATABASE_URL: postgresql+asyncpg://remnawave_user:secure_password_123@postgres:5432/remnawave_bot
    volumes:
      - ./logs:/app/logs
      - ./data:/app/data
    ports:
      - "8081:8081"
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
        export WEBHOOK_CONFIG_TYPE="external_nginx"
    else
        cat > "$COMPOSE_FILE" << 'EOF'
services:
  # PostgreSQL Database
  postgres:
    image: postgres:15-alpine
    container_name: remnawave_bot_db
    restart: unless-stopped
    environment:
      POSTGRES_DB: remnawave_bot
      POSTGRES_USER: remnawave_user
      POSTGRES_PASSWORD: secure_password_123
      POSTGRES_INITDB_ARGS: "--encoding=UTF-8 --lc-collate=C --lc-ctype=C"
    volumes:
      - postgres_data:/var/lib/postgresql/data
    networks:
      - bot_network
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U remnawave_user -d remnawave_bot"]
      interval: 15s
      timeout: 10s
      retries: 5
      start_period: 30s

  # RemnaWave Bot
  bot:
    image: fr1ngg/remnawave-bedolaga-telegram-bot:latest
    container_name: remnawave_bot
    restart: unless-stopped
    depends_on:
      postgres:
        condition: service_healthy
    env_file:
      - .env
    environment:
      DATABASE_URL: postgresql+asyncpg://remnawave_user:secure_password_123@postgres:5432/remnawave_bot
    volumes:
      - ./logs:/app/logs
      - ./data:/app/data
    networks:
      - bot_network
    healthcheck:
      test: ["CMD-SHELL", "python -c 'print(\"Bot is running\")'"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 30s

  # Nginx reverse proxy
  nginx:
    image: nginx:alpine
    container_name: nginx-bedolaga-bot
    restart: unless-stopped
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./nginx.conf:/etc/nginx/nginx.conf
      - ./ssl:/etc/nginx/ssl
      - /var/www/html:/var/www/html
      - ./nginx-logs:/var/log/nginx
    networks:
      - bot_network
    depends_on:
      - bot

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
        export WEBHOOK_CONFIG_TYPE="included_nginx"
        
        create_nginx_config "$WEBHOOK_DOMAIN"
    fi
    
    log "Конфигурация бота с Nginx webhook создана"
    export COMPOSE_TYPE="webhook_nginx"
    export WEBHOOK_DOMAIN="$WEBHOOK_DOMAIN"
}

create_caddyfile() {
    local domain="$1"
    
    log "Создание Caddyfile для домена $domain..."
    
    mkdir -p "$BOT_DIR/caddy-logs"
    
    cat > "$BOT_DIR/Caddyfile" << EOF
$domain {
    # Webhook endpoint для Telegram бота
    handle /webhook* {
        reverse_proxy bot:8081 {
            header_up Host {host}
            header_up X-Real-IP {remote_host}
            header_up X-Forwarded-For {remote_host}
            header_up X-Forwarded-Proto {scheme}
        }
    }
    
    # Health check для webhook сервиса
    handle /health {
        reverse_proxy bot:8081 {
            header_up Host {host}
            header_up X-Real-IP {remote_host}
            header_up X-Forwarded-For {remote_host}
            header_up X-Forwarded-Proto {scheme}
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

    mkdir -p /opt/caddy/html
    
    cat > /opt/caddy/html/index.html << EOF
<!DOCTYPE html>
<html>
<head>
    <title>RemnaWave Bot Webhook</title>
    <meta charset="utf-8">
</head>
<body>
    <h1>RemnaWave Bot Webhook Server</h1>
    <p>Сервер webhook для Telegram бота успешно запущен.</p>
    <p>Домен: $domain</p>
</body>
</html>
EOF

    log "Caddyfile создан для домена $domain"
}

create_nginx_config() {
    local domain="$1"
    
    log "Создание конфигурации Nginx для домена $domain..."
    
    mkdir -p "$BOT_DIR/nginx-logs"
    mkdir -p "$BOT_DIR/ssl"
    mkdir -p /var/www/html
    
    cat > "$BOT_DIR/nginx.conf" << EOF
events {
    worker_connections 1024;
}

http {
    include /etc/nginx/mime.types;
    default_type application/octet-stream;
    
    log_format main '\$remote_addr - \$remote_user [\$time_local] "\$request" '
                    '\$status \$body_bytes_sent "\$http_referer" '
                    '"\$http_user_agent" "\$http_x_forwarded_for"';

    access_log /var/log/nginx/access.log main;
    error_log /var/log/nginx/error.log warn;

    sendfile on;
    tcp_nopush on;
    tcp_nodelay on;
    keepalive_timeout 65;
    types_hash_max_size 2048;

    # Gzip compression
    gzip on;
    gzip_vary on;
    gzip_min_length 1024;
    gzip_types text/plain text/css application/json application/javascript text/xml application/xml application/xml+rss text/javascript;

    # Rate limiting
    limit_req_zone \$binary_remote_addr zone=webhook:10m rate=10r/s;

    upstream bot_backend {
        server bot:8081;
        keepalive 32;
    }

    server {
        listen 80;
        server_name $domain;
        
        # Redirect to HTTPS
        return 301 https://\$server_name\$request_uri;
    }

    server {
        listen 443 ssl http2;
        server_name $domain;

        # SSL configuration (you need to add your certificates)
        # ssl_certificate /etc/nginx/ssl/cert.pem;
        # ssl_certificate_key /etc/nginx/ssl/key.pem;
        
        # For now, use self-signed or Let's Encrypt
        # Uncomment and configure SSL certificates
        
        # SSL security settings
        ssl_protocols TLSv1.2 TLSv1.3;
        ssl_ciphers ECDHE-RSA-AES256-GCM-SHA512:DHE-RSA-AES256-GCM-SHA512:ECDHE-RSA-AES256-GCM-SHA384:DHE-RSA-AES256-GCM-SHA384;
        ssl_prefer_server_ciphers off;
        ssl_session_cache shared:SSL:10m;
        ssl_session_timeout 10m;

        # Security headers
        add_header X-Frame-Options DENY;
        add_header X-Content-Type-Options nosniff;
        add_header X-XSS-Protection "1; mode=block";
        add_header Strict-Transport-Security "max-age=63072000; includeSubDomains; preload";

        # Webhook endpoint
        location /webhook {
            limit_req zone=webhook burst=20 nodelay;
            
            proxy_pass http://bot_backend;
            proxy_set_header Host \$host;
            proxy_set_header X-Real-IP \$remote_addr;
            proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto \$scheme;
            
            proxy_connect_timeout 30s;
            proxy_send_timeout 30s;
            proxy_read_timeout 30s;
            
            proxy_buffering off;
            proxy_request_buffering off;
        }

        # Health check
        location /health {
            proxy_pass http://bot_backend;
            proxy_set_header Host \$host;
            proxy_set_header X-Real-IP \$remote_addr;
            proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto \$scheme;
        }

        # Default location
        location / {
            root /var/www/html;
            index index.html index.htm;
            try_files \$uri \$uri/ /index.html;
        }

        # Deny access to hidden files
        location ~ /\. {
            deny all;
        }
    }
}
EOF

    # Создаем простую индексную страницу
    cat > /var/www/html/index.html << EOF
<!DOCTYPE html>
<html>
<head>
    <title>RemnaWave Bot Webhook</title>
    <meta charset="utf-8">
    <style>
        body { font-family: Arial, sans-serif; margin: 40px; }
        .container { max-width: 600px; margin: 0 auto; }
        .status { color: #28a745; }
    </style>
</head>
<body>
    <div class="container">
        <h1>RemnaWave Bot Webhook Server</h1>
        <p class="status">✅ Сервер webhook для Telegram бота успешно запущен.</p>
        <p><strong>Домен:</strong> $domain</p>
        <p><strong>Webhook URL:</strong> https://$domain/webhook</p>
        <p><strong>Health Check:</strong> https://$domain/health</p>
    </div>
</body>
</html>
EOF

    log "Конфигурация Nginx создана для домена $domain"
    warn "ВАЖНО: Не забудьте настроить SSL сертификаты для домена $domain"
    warn "Рекомендуется использовать Let's Encrypt для получения бесплатных SSL сертификатов"
}

create_env_file() {
    log "Настройка .env файла..."
    
    read -p "Введите BOT_TOKEN: " BOT_TOKEN
    read -p "Введите BOT_USERNAME (без @): " BOT_USERNAME
    read -p "Введите ADMIN_IDS (через запятую): " ADMIN_IDS
    
    if [ "$COMPOSE_TYPE" = "panel_bot" ]; then
        log "Настройка для панель + бот на одном сервере"
        REMNAWAVE_URL="http://remnawave:3000"
        echo "URL панели будет: $REMNAWAVE_URL (внутренний адрес контейнера)"
    else
        read -p "Введите REMNAWAVE_URL (например: https://your-panel.com): " REMNAWAVE_URL
    fi
    
    read -p "Введите REMNAWAVE_TOKEN: " REMNAWAVE_TOKEN
    read -p "Введите SUBSCRIPTION_BASE_URL (например: https://sub.your-domain.com): " SUBSCRIPTION_BASE_URL
    
    if [[ "$COMPOSE_TYPE" == webhook_* ]]; then
        echo ""
        echo -e "${YELLOW}=== Настройка Webhook ===${NC}"
        WEBHOOK_URL="https://${WEBHOOK_DOMAIN}/webhook"
        WEBHOOK_SECRET=$(openssl rand -hex 32)
        echo -e "${GREEN}Webhook URL: $WEBHOOK_URL${NC}"
        echo -e "${GREEN}Webhook Secret сгенерирован автоматически${NC}"
    fi
    
    while true; do
        read -p "Включить триал? (y/n): " trial_enabled
        case $trial_enabled in
            [Yy]*)
                TRIAL_ENABLED="true"
                read -p "Введите TRIAL_DURATION_DAYS: " TRIAL_DURATION_DAYS
                read -p "Введите TRIAL_TRAFFIC_GB: " TRIAL_TRAFFIC_GB
                read -p "Введите TRIAL_SQUAD_UUID: " TRIAL_SQUAD_UUID
                break
                ;;
            [Nn]*)
                TRIAL_ENABLED="false"
                TRIAL_DURATION_DAYS=""
                TRIAL_TRAFFIC_GB=""
                TRIAL_SQUAD_UUID=""
                break
                ;;
            *)
                error "Пожалуйста, ответьте y или n."
                ;;
        esac
    done
    
    read -p "Введите REFERRAL_FIRST_REWARD (сумму с .0 на конце): " REFERRAL_FIRST_REWARD
    read -p "Введите REFERRAL_REFERRED_BONUS (сумму с .0 на конце): " REFERRAL_REFERRED_BONUS
    read -p "Введите REFERRAL_THRESHOLD (сумму с .0 на конце): " REFERRAL_THRESHOLD
    read -p "Введите REFERRAL_PERCENTAGE (с 0. в начале): " REFERRAL_PERCENTAGE
    
    echo ""
    echo -e "${YELLOW}=== Настройка оплаты звездами Telegram ===${NC}"
    while true; do
        read -p "Включить оплату звездами Telegram? (y/n): " stars_enabled
        case $stars_enabled in
            [Yy]*)
                STARS_ENABLED="true"
                echo ""
                echo -e "${BLUE}Настройка курсов обмена звезд на рубли:${NC}"
                echo -e "${YELLOW}Введите курс обмена для каждого пакета звезд${NC}"
                echo -e "${YELLOW}(например, если 100 звезд = 150 рублей, введите 150)${NC}"
                echo ""
                
                read -p "Курс для 100 звезд (в рублях): " STARS_100_RATE
                read -p "Курс для 150 звезд (в рублях): " STARS_150_RATE
                read -p "Курс для 250 звезд (в рублях): " STARS_250_RATE
                read -p "Курс для 350 звезд (в рублях): " STARS_350_RATE
                read -p "Курс для 500 звезд (в рублях): " STARS_500_RATE
                break
                ;;
            [Nn]*)
                STARS_ENABLED="false"
                STARS_100_RATE=""
                STARS_150_RATE=""
                STARS_250_RATE=""
                STARS_350_RATE=""
                STARS_500_RATE=""
                break
                ;;
            *)
                error "Пожалуйста, ответьте y или n."
                ;;
        esac
    done
    
    read -p "Введите DELETE_EXPIRED_TRIAL_DAYS: " DELETE_EXPIRED_TRIAL_DAYS
    read -p "Введите DELETE_EXPIRED_REGULAR_DAYS: " DELETE_EXPIRED_REGULAR_DAYS
    
    cat > "$ENV_FILE" << EOF
# Bot Configuration
BOT_TOKEN=$BOT_TOKEN
BOT_USERNAME=$BOT_USERNAME

# RemnaWave API Configuration
REMNAWAVE_URL=$REMNAWAVE_URL
REMNAWAVE_TOKEN=$REMNAWAVE_TOKEN
SUBSCRIPTION_BASE_URL=$SUBSCRIPTION_BASE_URL

# Admin Configuration
ADMIN_IDS=$ADMIN_IDS
SUPPORT_USERNAME=support

EOF

    if [[ "$COMPOSE_TYPE" == webhook_* ]]; then
        cat >> "$ENV_FILE" << EOF
# Webhook Configuration
WEBHOOK_URL=$WEBHOOK_URL
WEBHOOK_SECRET=$WEBHOOK_SECRET
WEBHOOK_ENABLED=true

EOF
    fi

    cat >> "$ENV_FILE" << EOF
# Trial Configuration
TRIAL_ENABLED=$TRIAL_ENABLED
EOF

    if [ "$TRIAL_ENABLED" = "true" ]; then
        cat >> "$ENV_FILE" << EOF
TRIAL_DURATION_DAYS=$TRIAL_DURATION_DAYS
TRIAL_TRAFFIC_GB=$TRIAL_TRAFFIC_GB
TRIAL_SQUAD_UUID=$TRIAL_SQUAD_UUID
EOF
    fi

    cat >> "$ENV_FILE" << EOF

# Referral Configuration
REFERRAL_FIRST_REWARD=$REFERRAL_FIRST_REWARD
REFERRAL_REFERRED_BONUS=$REFERRAL_REFERRED_BONUS
REFERRAL_THRESHOLD=$REFERRAL_THRESHOLD
REFERRAL_PERCENTAGE=$REFERRAL_PERCENTAGE

# Telegram Stars Configuration
STARS_ENABLED=$STARS_ENABLED
EOF

    if [ "$STARS_ENABLED" = "true" ]; then
        cat >> "$ENV_FILE" << EOF
STARS_100_RATE=$STARS_100_RATE
STARS_150_RATE=$STARS_150_RATE
STARS_250_RATE=$STARS_250_RATE
STARS_350_RATE=$STARS_350_RATE
STARS_500_RATE=$STARS_500_RATE
EOF
    fi

    cat >> "$ENV_FILE" << EOF

# Monitor Configuration
MONITOR_ENABLED=true
MONITOR_CHECK_INTERVAL=21600
MONITOR_DAILY_CHECK_HOUR=12
MONITOR_WARNING_DAYS=2
DELETE_EXPIRED_TRIAL_DAYS=$DELETE_EXPIRED_TRIAL_DAYS
DELETE_EXPIRED_REGULAR_DAYS=$DELETE_EXPIRED_REGULAR_DAYS
AUTO_DELETE_ENABLED=true

# Lucky Game Configuration
LUCKY_GAME_ENABLED=true
LUCKY_GAME_REWARD=50.0
LUCKY_GAME_NUMBERS=30
LUCKY_GAME_WINNING_COUNT=5
EOF

    log ".env файл создан успешно"
    
    if [[ "$COMPOSE_TYPE" == webhook_* ]]; then
        echo ""
        echo -e "${YELLOW}=== ВАЖНЫЕ ИНСТРУКЦИИ ДЛЯ WEBHOOK ===${NC}"
        echo -e "${GREEN}1. Webhook URL: $WEBHOOK_URL${NC}"
        echo -e "${GREEN}2. Убедитесь что домен $WEBHOOK_DOMAIN указывает на этот сервер${NC}"
        echo -e "${GREEN}3. SSL сертификат будет настроен автоматически (Let's Encrypt)${NC}"
        
        if [ "$WEBHOOK_CONFIG_TYPE" = "external_caddy" ]; then
            echo -e "${YELLOW}4. Необходимо добавить в существующий Caddyfile:${NC}"
            cat << EOF

$WEBHOOK_DOMAIN {
    handle /webhook* {
        reverse_proxy localhost:8081 {
            header_up Host {host}
            header_up X-Real-IP {remote_host}
            header_up X-Forwarded-For {remote_host}
            header_up X-Forwarded-Proto {scheme}
        }
    }
    
    handle /health {
        reverse_proxy localhost:8081 {
            header_up Host {host}
            header_up X-Real-IP {remote_host}
            header_up X-Forwarded-For {remote_host}
            header_up X-Forwarded-Proto {scheme}
        }
    }
}
EOF
        elif [ "$WEBHOOK_CONFIG_TYPE" = "external_nginx" ]; then
            echo -e "${YELLOW}4. Необходимо добавить в конфигурацию Nginx:${NC}"
            echo -e "${BLUE}Файл конфигурации создан в: $BOT_DIR/nginx-site.conf${NC}"
            
            cat > "$BOT_DIR/nginx-site.conf" << EOF
server {
    listen 80;
    server_name $WEBHOOK_DOMAIN;
    return 301 https://\$server_name\$request_uri;
}

server {
    listen 443 ssl http2;
    server_name $WEBHOOK_DOMAIN;
    
    # SSL configuration
    ssl_certificate /path/to/your/cert.pem;
    ssl_certificate_key /path/to/your/key.pem;
    
    location /webhook {
        proxy_pass http://localhost:8081;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
    
    location /health {
        proxy_pass http://localhost:8081;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
}
EOF
        fi
        echo ""
    fi
    
    if [ "$COMPOSE_TYPE" = "panel_bot" ]; then
        echo ""
        echo -e "${YELLOW}=== ВАЖНЫЕ ИНСТРУКЦИИ ДЛЯ ПАНЕЛЬ + БОТ ===${NC}"
        echo -e "${GREEN}1. Убедитесь что панель RemnaWave уже запущена${NC}"
        echo -e "${GREEN}2. URL панели установлен как: $REMNAWAVE_URL${NC}"
        echo -e "${GREEN}3. Бот будет подключаться к панели через внутреннюю Docker сеть${NC}"
        echo -e "${YELLOW}4. Если панель не запущена, сначала запустите её!${NC}"
        echo ""
    fi
}

create_service() {
    while true; do
        read -p "Создать службу для запуска бота? (y/n): " create_service_choice
        case $create_service_choice in
            [Yy]*)
                log "Создание службы systemd..."
                
                cat > "$SERVICE_FILE" << EOF
[Unit]
Description=RemnaWave Bedolaga Bot
Requires=docker.service
After=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=$BOT_DIR
ExecStart=/usr/bin/docker compose up -d
ExecStop=/usr/bin/docker compose down
TimeoutStartSec=0

[Install]
WantedBy=multi-user.target
EOF
                
                systemctl daemon-reload
                systemctl enable bedolaga-bot.service
                log "Служба bedolaga-bot создана и включена"
                break
                ;;
            [Nn]*)
                log "Служба не будет создана"
                break
                ;;
            *)
                error "Пожалуйста, ответьте y или n."
                ;;
        esac
    done
}

check_bot_status() {
    if docker compose -f "$COMPOSE_FILE" ps --services --filter "status=running" | grep -q "bot"; then
        return 0  
    else
        return 1  
    fi
}

check_remnawave_connection() {
    if [ -f "$ENV_FILE" ]; then
        source "$ENV_FILE"
        if [ ! -z "$REMNAWAVE_URL" ]; then
            if [[ "$REMNAWAVE_URL" == *"remnawave:3000"* ]]; then
                if docker compose -f "$COMPOSE_FILE" ps bot | grep -q "Up"; then
                    return 0 
                else
                    return 1
                fi
            else
                if curl -s --connect-timeout 5 "$REMNAWAVE_URL/api/system/stats" > /dev/null 2>&1; then
                    return 0
                else
                    return 1
                fi
            fi
        else
            return 1
        fi
    else
        return 1
    fi
}

start_bot() {
    log "Запуск бота..."
    cd "$BOT_DIR"
    
    if grep -q "remnawave-network" "$COMPOSE_FILE"; then
        log "Обнаружена конфигурация панель + бот"
        
        if ! docker network ls | grep -q "remnawave-network"; then
            error "Сеть remnawave-network не найдена!"
            error "Убедитесь что панель RemnaWave запущена и создала сеть"
            echo ""
            echo "Для проверки выполните:"
            echo "  docker network ls | grep remnawave"
            echo ""
            echo "Если сети нет, сначала запустите панель RemnaWave"
            return 1
        fi
        
        log "Сеть remnawave-network найдена ✓"
    fi
    
    docker compose up -d
    log "Бот запущен"
    
    sleep 5
    if check_bot_status; then
        log "✅ Бот успешно запущен и работает"
        
        if [[ "$COMPOSE_TYPE" == webhook_* ]] && [ ! -z "$WEBHOOK_DOMAIN" ]; then
            echo ""
            echo -e "${GREEN}=== ИНФОРМАЦИЯ О WEBHOOK ===${NC}"
            echo -e "${BLUE}Webhook URL: https://$WEBHOOK_DOMAIN/webhook${NC}"
            echo -e "${BLUE}Health Check: https://$WEBHOOK_DOMAIN/health${NC}"
            echo ""
            echo -e "${YELLOW}Для тестирования webhook выполните:${NC}"
            echo -e "${YELLOW}curl https://$WEBHOOK_DOMAIN/health${NC}"
        fi
    else
        warn "⚠️ Бот запущен но возможны проблемы. Проверьте логи: docker compose logs bot"
    fi
}

stop_bot() {
    log "Остановка бота..."
    cd "$BOT_DIR"
    docker compose down
    log "Бот остановлен"
}

restart_bot() {
    log "Перезапуск бота..."
    cd "$BOT_DIR"
    docker compose restart
    log "Бот перезапущен"
}

update_bot() {
    log "Обновление бота..."
    cd "$BOT_DIR"
    docker compose down
    docker compose pull bot
    docker compose up -d
    log "Бот обновлен"
}

view_logs() {
    cd "$BOT_DIR"
    docker compose logs bot
}

view_live_logs() {
    cd "$BOT_DIR"
    docker compose logs -f bot
}

backup_database() {
    log "Создание резервной копии базы данных..."
    cd "$BOT_DIR"
    
    if ! docker compose ps postgres | grep -q "Up"; then
        log "Контейнер PostgreSQL не запущен. Запускаем PostgreSQL..."
        docker compose up -d postgres
        
        log "Ожидание готовности базы данных..."
        for i in {1..30}; do
            if docker compose exec postgres pg_isready -U remnawave_user -d remnawave_bot &>/dev/null; then
                log "PostgreSQL готов к работе"
                break
            fi
            if [ $i -eq 30 ]; then
                error "PostgreSQL не запустился в течение 60 секунд"
                return 1
            fi
            sleep 2
            echo -n "."
        done
        echo ""
    fi
    
    log "Проверка подключения к базе данных..."
    if ! docker compose exec postgres pg_isready -U remnawave_user -d remnawave_bot &>/dev/null; then
        error "База данных недоступна. Проверьте логи: docker compose logs postgres"
        return 1
    fi
    
    BACKUP_FILE="$BOT_DIR/backup_$(date +%Y%m%d_%H%M%S).sql"
    
    log "Создание дампа базы данных..."
    
    if docker compose exec postgres pg_dump -U remnawave_user -d remnawave_bot --verbose --no-owner --no-privileges > "$BACKUP_FILE" 2>/dev/null; then
        if [ -s "$BACKUP_FILE" ]; then
            FILE_SIZE=$(du -h "$BACKUP_FILE" | cut -f1)
            log "Резервная копия создана успешно: $(basename "$BACKUP_FILE")"
            echo -e "${GREEN}Размер файла: $FILE_SIZE${NC}"
            echo -e "${GREEN}Путь: $BACKUP_FILE${NC}"
            
            LINES_COUNT=$(wc -l < "$BACKUP_FILE")
            echo -e "${BLUE}Количество строк в дампе: $LINES_COUNT${NC}"
        else
            error "Резервная копия создана, но файл пустой. Возможные причины:"
            echo "  - База данных пуста (бот еще не создал таблицы)"
            echo "  - Нет прав доступа к базе данных"
            echo "  - Неправильные параметры подключения"
            rm -f "$BACKUP_FILE"
            return 1
        fi
    else
        error "Ошибка при создании резервной копии"
        echo "Попытка диагностики проблемы..."
        docker compose exec postgres psql -U remnawave_user -d remnawave_bot -c "\dt" 2>&1 || {
            echo "Не удается подключиться к базе данных."
            echo "Проверьте логи контейнера: docker compose logs postgres"
        }
        rm -f "$BACKUP_FILE" 2>/dev/null
        return 1
    fi
}

restore_database() {
    log "Восстановление базы данных из резервной копии"
    
    BACKUP_FILES=($(find "$BOT_DIR" -name "backup_*.sql" -type f 2>/dev/null))
    
    if [ ${#BACKUP_FILES[@]} -eq 0 ]; then
        error "Файлы резервных копий не найдены в $BOT_DIR"
        echo "Поместите файл резервной копии (.sql) в папку $BOT_DIR"
        return 1
    fi
    
    echo -e "${YELLOW}Найденные резервные копии:${NC}"
    for i in "${!BACKUP_FILES[@]}"; do
        BACKUP_FILE="${BACKUP_FILES[$i]}"
        FILE_SIZE=$(du -h "$BACKUP_FILE" | cut -f1)
        FILE_DATE=$(basename "$BACKUP_FILE" | sed 's/backup_\([0-9]\{8\}_[0-9]\{6\}\)\.sql/\1/' | sed 's/_/ /')
        echo "$((i+1))) $(basename "$BACKUP_FILE") - Размер: $FILE_SIZE - Дата: $FILE_DATE"
    done
    echo "$((${#BACKUP_FILES[@]}+1))) Указать путь к другому файлу"
    echo "0) Отмена"
    
    while true; do
        read -p "Выберите файл для восстановления: " choice
        
        if [ "$choice" = "0" ]; then
            log "Операция отменена"
            return 0
        elif [ "$choice" = "$((${#BACKUP_FILES[@]}+1))" ]; then
            read -p "Введите полный путь к файлу резервной копии: " SELECTED_BACKUP
            if [ ! -f "$SELECTED_BACKUP" ]; then
                error "Файл не найден: $SELECTED_BACKUP"
                continue
            fi
            break
        elif [ "$choice" -ge 1 ] && [ "$choice" -le "${#BACKUP_FILES[@]}" ]; then
            SELECTED_BACKUP="${BACKUP_FILES[$((choice-1))]}"
            break
        else
            error "Неверный выбор. Попробуйте снова."
        fi
    done
    
    warn "ВНИМАНИЕ! Это действие перезапишет текущую базу данных!"
    warn "Убедитесь, что у вас есть резервная копия текущих данных!"
    read -p "Продолжить восстановление? Введите 'YES' для подтверждения: " confirm
    
    if [ "$confirm" != "YES" ]; then
        log "Операция отменена"
        return 0
    fi
    
    cd "$BOT_DIR"
    
    if ! docker compose ps postgres | grep -q "Up"; then
        log "Запуск контейнера PostgreSQL..."
        docker compose up -d postgres
        
        log "Ожидание готовности базы данных..."
        for i in {1..30}; do
            if docker compose exec postgres pg_isready -U remnawave_user -d remnawave_bot &>/dev/null; then
                break
            fi
            sleep 2
            echo -n "."
        done
        echo ""
    fi
    
    log "Очистка текущей базы данных..."
    if docker compose exec -T postgres psql -U remnawave_user -d remnawave_bot -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;" &>/dev/null; then
        log "База данных очищена"
    else
        error "Ошибка при очистке базы данных"
        return 1
    fi
    
    log "Восстановление данных из файла: $(basename "$SELECTED_BACKUP")"
    if docker compose exec -T postgres psql -U remnawave_user -d remnawave_bot < "$SELECTED_BACKUP"; then
        log "База данных успешно восстановлена!"
        log "Перезапуск бота для применения изменений..."
        docker compose restart bot
        log "Восстановление завершено успешно"
    else
        error "Ошибка при восстановлении базы данных"
        return 1
    fi
}

diagnose_database() {
    log "Диагностика состояния базы данных..."
    cd "$BOT_DIR"
    
    echo -e "${YELLOW}Состояние контейнеров:${NC}"
    docker compose ps
    echo ""
    
    if ! docker compose ps postgres | grep -q "Up"; then
        log "PostgreSQL не запущен. Запускаем..."
        docker compose up -d postgres
        
        log "Ожидание готовности базы данных..."
        for i in {1..30}; do
            if docker compose exec postgres pg_isready -U remnawave_user -d remnawave_bot &>/dev/null; then
                log "PostgreSQL готов к работе"
                break
            fi
            if [ $i -eq 30 ]; then
                error "PostgreSQL не запустился в течение 60 секунд"
                return 1
            fi
            sleep 2
            echo -n "."
        done
        echo ""
    fi
    
    echo -e "${YELLOW}Проверка доступности PostgreSQL:${NC}"
    if docker compose exec postgres pg_isready -U remnawave_user -d remnawave_bot; then
        echo -e "${GREEN}✓ PostgreSQL доступен${NC}"
    else
        echo -e "${RED}✗ PostgreSQL недоступен${NC}"
        echo "Проверьте логи: docker compose logs postgres"
        return 1
    fi
    echo ""
    
    echo -e "${YELLOW}Информация о базе данных:${NC}"
    docker compose exec postgres psql -U remnawave_user -d remnawave_bot -c "
        SELECT 
            current_database() as database_name,
            current_user as current_user,
            version() as postgresql_version;
    " 2>/dev/null || {
        echo -e "${RED}Ошибка подключения к базе данных${NC}"
        return 1
    }
    echo ""
    
    echo -e "${YELLOW}Таблицы в базе данных:${NC}"
    TABLES_OUTPUT=$(docker compose exec postgres psql -U remnawave_user -d remnawave_bot -c "\dt" 2>/dev/null)
    if echo "$TABLES_OUTPUT" | grep -q "No relations found"; then
        echo -e "${YELLOW}База данных пуста - таблицы еще не созданы${NC}"
        echo -e "${BLUE}Это нормально если бот еще ни разу не запускался${NC}"
    elif [ -z "$TABLES_OUTPUT" ]; then
        echo -e "${YELLOW}Не удается получить список таблиц${NC}"
    else
        echo "$TABLES_OUTPUT"
    fi
    echo ""
    
    echo -e "${YELLOW}Размер базы данных:${NC}"
    docker compose exec postgres psql -U remnawave_user -d remnawave_bot -c "
        SELECT 
            pg_database.datname,
            pg_size_pretty(pg_database_size(pg_database.datname)) AS size
        FROM pg_database 
        WHERE datname = 'remnawave_bot';
    " 2>/dev/null || {
        echo -e "${RED}Не удается получить размер базы данных${NC}"
    }
}

emergency_fix_database() {
    log "Экстренное исправление базы данных..."
    
    cd "$BOT_DIR"
    
    if ! docker compose ps bot | grep -q "Up"; then
        warn "Контейнер бота не запущен. Запускаем бота..."
        docker compose up -d bot
        
        log "Ожидание готовности бота..."
        for i in {1..60}; do
            if docker compose logs bot 2>/dev/null | grep -q "Bot started successfully\|Application startup complete\|Bot is running"; then
                log "Бот готов к работе"
                break
            fi
            if [ $i -eq 60 ]; then
                warn "Бот не запустился полностью, но попробуем выполнить исправление"
                break
            fi
            sleep 2
            echo -n "."
        done
        echo ""
    fi
    
    EMERGENCY_SCRIPT="$BOT_DIR/emergency_fix.py"
    
    log "Создание скрипта экстренного исправления..."
    cat > "$EMERGENCY_SCRIPT" << 'EOF'
"""
Экстренное исправление проблемы с отображением подписок
Этот патч добавляет недостающие поля в таблицу user_subscriptions
"""

import asyncio
import sys
import os
from pathlib import Path

# Добавляем корневую директорию в путь
sys.path.insert(0, str(Path(__file__).parent))

try:
    from config import load_config
    from database import Database
except ImportError:
    # Если не можем импортировать, попробуем из app
    sys.path.insert(0, '/app')
    try:
        from config import load_config
        from database import Database
    except ImportError:
        print("⌐ Не удается импортировать модули. Проверьте структуру проекта.")
        sys.exit(1)

from sqlalchemy import text
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def check_and_add_column(db, column_name, column_definition):
    """Проверяет и добавляет колонку в таблицу"""
    try:
        # Отдельная транзакция для проверки
        async with db.engine.begin() as conn:
            await conn.execute(text(f"SELECT {column_name} FROM user_subscriptions LIMIT 1"))
            logger.info(f"✅ Поле {column_name} уже существует")
            return True
    except Exception:
        # Отдельная транзакция для добавления колонки
        try:
            async with db.engine.begin() as conn:
                logger.info(f"➕ Добавляю поле {column_name}...")
                await conn.execute(text(f"""
                    ALTER TABLE user_subscriptions 
                    ADD COLUMN {column_name} {column_definition}
                """))
                logger.info(f"✅ Поле {column_name} добавлено")
                return True
        except Exception as e:
            logger.error(f"⌐ Ошибка при добавлении {column_name}: {e}")
            return False

async def emergency_fix():
    """Экстренное исправление базы данных"""
    
    try:
        # Загружаем конфигурацию
        config = load_config()
        
        # Подключаемся к базе данных  
        db = Database(config.DATABASE_URL)
        
        logger.info("🔧 Выполняю экстренное исправление базы данных...")
        
        # Проверяем существование таблицы user_subscriptions
        try:
            async with db.engine.begin() as conn:
                result = await conn.execute(text("SELECT COUNT(*) FROM user_subscriptions"))
                count = result.scalar()
                logger.info(f"📊 Найдено {count} подписок в таблице user_subscriptions")
        except Exception as e:
            logger.error(f"⌐ Таблица user_subscriptions не найдена: {e}")
            await db.close()
            return

        # Добавляем поля по одному в отдельных транзакциях
        success1 = await check_and_add_column(db, "auto_pay_enabled", "BOOLEAN DEFAULT FALSE")
        success2 = await check_and_add_column(db, "auto_pay_days_before", "INTEGER DEFAULT 3")
        
        # Финальная проверка в отдельной транзакции
        if success1 and success2:
            try:
                async with db.engine.begin() as conn:
                    result = await conn.execute(text("""
                        SELECT id, auto_pay_enabled, auto_pay_days_before 
                        FROM user_subscriptions LIMIT 1
                    """))
                    row = result.fetchone()
                    if row:
                        logger.info("✅ Все поля доступны для чтения")
                        logger.info(f"🔍 Пример записи: id={row[0]}, auto_pay_enabled={row[1]}, auto_pay_days_before={row[2]}")
                    else:
                        logger.info("✅ Все поля доступны, но таблица пуста")
                        
            except Exception as e:
                logger.error(f"⌐ Поля все еще недоступны: {e}")
        else:
            logger.error("⌐ Не удалось добавить все необходимые поля")
                
        await db.close()
        logger.info("🎉 Экстренное исправление завершено!")
        
    except Exception as e:
        logger.error(f"💥 Критическая ошибка: {e}")
        raise

if __name__ == "__main__":
    asyncio.run(emergency_fix())
EOF

    log "Копирование скрипта в контейнер бота..."
    if docker compose exec bot test -d /app; then
        docker compose cp "$EMERGENCY_SCRIPT" bot:/app/emergency_fix.py
        
        log "Запуск экстренного исправления в контейнере бота..."
        if docker compose exec bot python emergency_fix.py; then
            log "✅ Экстренное исправление выполнено успешно!"
            
            log "Перезапуск бота для применения изменений..."
            docker compose restart bot
            log "✅ Бот перезапущен"
        else
            error "⌐ Ошибка при выполнении экстренного исправления"
            echo "Проверьте логи бота: docker compose logs bot"
        fi
        
        docker compose exec bot rm -f /app/emergency_fix.py 2>/dev/null || true
    else
        error "⌐ Не удается найти директорию /app в контейнере бота"
        echo "Проверьте, что контейнер бота запущен правильно"
    fi
    
    rm -f "$EMERGENCY_SCRIPT"
}

emergency_fix_database_sql() {
    log "Экстренное исправление базы данных через SQL..."
    
    cd "$BOT_DIR"
    
    if ! docker compose ps postgres | grep -q "Up"; then
        log "Контейнер PostgreSQL не запущен. Запускаем PostgreSQL..."
        docker compose up -d postgres
        
        log "Ожидание готовности базы данных..."
        for i in {1..30}; do
            if docker compose exec postgres pg_isready -U remnawave_user -d remnawave_bot &>/dev/null; then
                log "PostgreSQL готов к работе"
                break
            fi
            if [ $i -eq 30 ]; then
                error "PostgreSQL не запустился в течение 60 секунд"
                return 1
            fi
            sleep 2
            echo -n "."
        done
        echo ""
    fi
    
    log "Проверка существования полей в таблице user_subscriptions..."
    
    if docker compose exec postgres psql -U remnawave_user -d remnawave_bot -c "SELECT auto_pay_enabled FROM user_subscriptions LIMIT 1" &>/dev/null; then
        log "✅ Поле auto_pay_enabled уже существует"
    else
        log "➕ Добавление поля auto_pay_enabled..."
        if docker compose exec postgres psql -U remnawave_user -d remnawave_bot -c "ALTER TABLE user_subscriptions ADD COLUMN auto_pay_enabled BOOLEAN DEFAULT FALSE" &>/dev/null; then
            log "✅ Поле auto_pay_enabled добавлено"
        else
            error "⌐ Ошибка при добавлении поля auto_pay_enabled"
            return 1
        fi
    fi
    
    if docker compose exec postgres psql -U remnawave_user -d remnawave_bot -c "SELECT auto_pay_days_before FROM user_subscriptions LIMIT 1" &>/dev/null; then
        log "✅ Поле auto_pay_days_before уже существует"
    else
        log "➕ Добавление поля auto_pay_days_before..."
        if docker compose exec postgres psql -U remnawave_user -d remnawave_bot -c "ALTER TABLE user_subscriptions ADD COLUMN auto_pay_days_before INTEGER DEFAULT 3" &>/dev/null; then
            log "✅ Поле auto_pay_days_before добавлено"
        else
            error "⌐ Ошибка при добавлении поля auto_pay_days_before"
            return 1
        fi
    fi
    
    log "✅ Экстренное исправление через SQL завершено!"
    
    if docker compose ps bot | grep -q "Up"; then
        log "Перезапуск бота для применения изменений..."
        docker compose restart bot
        log "✅ Бот перезапущен"
    fi
}

edit_env_file() {
    ensure_nano
    
    if [ ! -f "$ENV_FILE" ]; then
        error "Файл .env не найден: $ENV_FILE"
        return 1
    fi
    
    log "Открытие .env файла для редактирования..."
    log "После изменений перезапустите бота для применения настроек"
    
    cp "$ENV_FILE" "$ENV_FILE.backup.$(date +%Y%m%d_%H%M%S)"
    
    nano "$ENV_FILE"
    
    log "Редактирование завершено"
    echo -e "${YELLOW}Не забудьте перезапустить бота для применения изменений!${NC}"
}

remove_database() {
    warn "ВНИМАНИЕ! Это действие удалит всю базу данных!"
    warn "Все данные бота (пользователи, подписки, настройки) будут потеряны!"
    read -p "Вы уверены? Введите 'YES' для подтверждения: " confirm
    if [ "$confirm" = "YES" ]; then
        log "Удаление базы данных..."
        cd "$BOT_DIR"
        
        if docker compose ps --services --filter "status=running" | grep -q "."; then
            log "Остановка всех контейнеров..."
            docker compose down
        fi
        
        log "Удаление volume с данными PostgreSQL..."
        VOLUME_NAME=$(docker compose config --volumes 2>/dev/null | grep postgres || echo "bedolaga-bot_postgres_data")
        
        if docker volume ls | grep -q "$VOLUME_NAME"; then
            if docker volume rm "$VOLUME_NAME" 2>/dev/null; then
                log "Volume $VOLUME_NAME успешно удален"
            else
                log "Принудительное удаление volume..."
                docker volume rm "$VOLUME_NAME" --force 2>/dev/null || true
            fi
        fi
        
        docker volume rm "$(basename $BOT_DIR)_postgres_data" 2>/dev/null || true
        
        log "Очистка неиспользуемых volumes..."
        docker volume prune -f 2>/dev/null || true
        
        log "База данных удалена"
        log "При следующем запуске бота будет создана новая пустая база данных"
    else
        log "Операция отменена"
    fi
}

remove_bot() {
    warn "ВНИМАНИЕ! Это действие полностью удалит бота и все данные!"
    read -p "Вы уверены? Введите 'YES' для подтверждения: " confirm
    if [ "$confirm" = "YES" ]; then
        log "Удаление бота..."
        cd "$BOT_DIR"
        docker compose down -v
        systemctl disable bedolaga-bot.service 2>/dev/null || true
        rm -f "$SERVICE_FILE"
        rm -rf "$BOT_DIR"
        log "Бот полностью удален"
        exit 0
    else
        log "Операция отменена"
    fi
}

check_remnawave_network() {
    if docker network ls | grep -q "remnawave-network"; then
        return 0  
    else
        return 1 
    fi
}

view_webhook_config() {
    if [[ "$COMPOSE_TYPE" == webhook_* ]]; then
        echo -e "${YELLOW}=== КОНФИГУРАЦИЯ WEBHOOK ===${NC}"
        
        if [ -f "$ENV_FILE" ]; then
            source "$ENV_FILE"
            echo -e "${GREEN}Webhook URL: $WEBHOOK_URL${NC}"
            echo -e "${GREEN}Webhook Secret: ${WEBHOOK_SECRET:0:8}...${NC}"
            echo -e "${GREEN}Домен: $WEBHOOK_DOMAIN${NC}"
        fi
        
        if [ "$COMPOSE_TYPE" = "webhook_caddy" ] && [ -f "$BOT_DIR/Caddyfile" ]; then
            echo -e "${BLUE}Caddyfile:${NC}"
            cat "$BOT_DIR/Caddyfile"
        elif [ "$COMPOSE_TYPE" = "webhook_nginx" ] && [ -f "$BOT_DIR/nginx.conf" ]; then
            echo -e "${BLUE}Nginx конфигурация:${NC}"
            echo "Файл: $BOT_DIR/nginx.conf"
        fi
        
        if [ ! -z "$WEBHOOK_DOMAIN" ]; then
            echo ""
            echo -e "${YELLOW}Проверка доступности webhook:${NC}"
            if curl -s --connect-timeout 5 "https://$WEBHOOK_DOMAIN/health" > /dev/null 2>&1; then
                echo -e "${GREEN}✅ Webhook доступен${NC}"
            else
                echo -e "${RED}❌ Webhook недоступен${NC}"
                echo "Проверьте:"
                echo "1. DNS настройки для домена $WEBHOOK_DOMAIN"
                echo "2. SSL сертификат"
                echo "3. Статус контейнеров: docker compose ps"
            fi
        fi
    else
        warn "Webhook не настроен для данной конфигурации"
    fi
}

show_menu() {
    clear
    echo -e "${BLUE}=== RemnaWave Bedolaga Bot Management ===${NC}"
    echo ""
    
    INSTALLATION_TYPE="Неизвестно"
    if [ -f "$COMPOSE_FILE" ]; then
        if grep -q "remnawave-network" "$COMPOSE_FILE" && grep -q "external: true" "$COMPOSE_FILE"; then
            INSTALLATION_TYPE="Панель + Бот на одном сервере"
        elif grep -q "caddy:" "$COMPOSE_FILE"; then
            INSTALLATION_TYPE="Бот с Caddy webhook"
        elif grep -q "nginx:" "$COMPOSE_FILE"; then
            INSTALLATION_TYPE="Бот с Nginx webhook"
        elif grep -q "bot_network" "$COMPOSE_FILE"; then
            INSTALLATION_TYPE="Только бот (внешняя панель)"
        fi
    fi
    
    echo -e "${YELLOW}Тип установки: ${NC}$INSTALLATION_TYPE"
    
    echo -e "${YELLOW}Статус бота:${NC}"
    if check_bot_status; then
        echo -e "🟢 Бот: ${GREEN}ЗАПУЩЕН${NC}"
    else
        echo -e "🔴 Бот: ${RED}ОСТАНОВЛЕН${NC}"
    fi
    
    if [[ "$INSTALLATION_TYPE" == *"Панель + Бот"* ]]; then
        echo -e "${YELLOW}Статус сети RemnaWave:${NC}"
        if check_remnawave_network; then
            echo -e "🟢 Сеть: ${GREEN}СОЗДАНА${NC}"
        else
            echo -e "🔴 Сеть: ${RED}НЕ НАЙДЕНА${NC} (запустите панель RemnaWave)"
        fi
    fi
    
    echo -e "${YELLOW}Подключение к RemnaWave API:${NC}"
    if check_remnawave_connection; then
        echo -e "🟢 API: ${GREEN}ПОДКЛЮЧЕН${NC}"
    else
        echo -e "🔴 API: ${RED}НЕ ПОДКЛЮЧЕН${NC}"
    fi
    
    if [[ "$INSTALLATION_TYPE" == *"webhook"* ]]; then
        echo -e "${YELLOW}Webhook статус:${NC}"
        if [ -f "$ENV_FILE" ]; then
            source "$ENV_FILE"
            if [ ! -z "$WEBHOOK_DOMAIN" ]; then
                echo -e "🌐 Домен: ${BLUE}$WEBHOOK_DOMAIN${NC}"
                echo -e "📡 URL: ${BLUE}https://$WEBHOOK_DOMAIN/webhook${NC}"
            fi
        fi
    fi
    
    echo ""
    echo -e "${YELLOW}Доступные действия:${NC}"
    
    if check_bot_status; then
        echo "1) Выключить бота"
        echo "2) Перезапустить бота"
        echo "3) Посмотреть логи в реальном времени"
        echo "4) Обновить бота"
        echo "5) Посмотреть логи"
        echo "6) Создать резервную копию БД"
        echo "7) Восстановить базу данных"
        echo "8) Редактировать .env файл"
        echo "9) Диагностика базы данных"
        echo "10) Экстренное исправление БД (Python)"
        echo "11) Экстренное исправление БД (SQL)"
        if [[ "$INSTALLATION_TYPE" == *"webhook"* ]]; then
            echo "12) Просмотр конфигурации webhook"
            echo "13) Удалить базу данных"
            echo "14) Полностью удалить бота"
        else
            echo "12) Удалить базу данных"
            echo "13) Полностью удалить бота"
        fi
        echo "0) Выход"
        
        read -p "Выберите действие: " choice
        case $choice in
            1) stop_bot; read -p "Нажмите Enter для продолжения..."; ;;
            2) restart_bot; read -p "Нажмите Enter для продолжения..."; ;;
            3) view_live_logs; ;;
            4) update_bot; read -p "Нажмите Enter для продолжения..."; ;;
            5) view_logs; read -p "Нажмите Enter для продолжения..."; ;;
            6) backup_database; read -p "Нажмите Enter для продолжения..."; ;;
            7) restore_database; read -p "Нажмите Enter для продолжения..."; ;;
            8) edit_env_file; read -p "Нажмите Enter для продолжения..."; ;;
            9) diagnose_database; read -p "Нажмите Enter для продолжения..."; ;;
            10) emergency_fix_database; read -p "Нажмите Enter для продолжения..."; ;;
            11) emergency_fix_database_sql; read -p "Нажмите Enter для продолжения..."; ;;
            12) 
                if [[ "$INSTALLATION_TYPE" == *"webhook"* ]]; then
                    view_webhook_config; read -p "Нажмите Enter для продолжения...";
                else
                    remove_database; read -p "Нажмите Enter для продолжения...";
                fi
                ;;
            13) 
                if [[ "$INSTALLATION_TYPE" == *"webhook"* ]]; then
                    remove_database; read -p "Нажмите Enter для продолжения...";
                else
                    remove_bot;
                fi
                ;;
            14) 
                if [[ "$INSTALLATION_TYPE" == *"webhook"* ]]; then
                    remove_bot;
                fi
                ;;
            0) exit 0; ;;
            *) error "Неверный выбор"; read -p "Нажмите Enter для продолжения..."; ;;
        esac
    else
        echo "1) Запустить бота"
        echo "2) Обновить бота"
        echo "3) Посмотреть логи"
        echo "4) Создать резервную копию БД"
        echo "5) Восстановить базу данных"
        echo "6) Редактировать .env файл"
        echo "7) Диагностика базы данных"
        echo "8) Экстренное исправление БД (Python)"
        echo "9) Экстренное исправление БД (SQL)"
        if [[ "$INSTALLATION_TYPE" == *"webhook"* ]]; then
            echo "10) Просмотр конфигурации webhook"
            echo "11) Удалить базу данных"
            echo "12) Полностью удалить бота"
        else
            echo "10) Удалить базу данных"
            echo "11) Полностью удалить бота"
        fi
        echo "0) Выход"
        
        read -p "Выберите действие: " choice
        case $choice in
            1) start_bot; read -p "Нажмите Enter для продолжения..."; ;;
            2) update_bot; read -p "Нажмите Enter для продолжения..."; ;;
            3) view_logs; read -p "Нажмите Enter для продолжения..."; ;;
            4) backup_database; read -p "Нажмите Enter для продолжения..."; ;;
            5) restore_database; read -p "Нажмите Enter для продолжения..."; ;;
            6) edit_env_file; read -p "Нажмите Enter для продолжения..."; ;;
            7) diagnose_database; read -p "Нажмите Enter для продолжения..."; ;;
            8) emergency_fix_database; read -p "Нажмите Enter для продолжения..."; ;;
            9) emergency_fix_database_sql; read -p "Нажмите Enter для продолжения..."; ;;
            10) 
                if [[ "$INSTALLATION_TYPE" == *"webhook"* ]]; then
                    view_webhook_config; read -p "Нажмите Enter для продолжения...";
                else
                    remove_database; read -p "Нажмите Enter для продолжения...";
                fi
                ;;
            11) 
                if [[ "$INSTALLATION_TYPE" == *"webhook"* ]]; then
                    remove_database; read -p "Нажмите Enter для продолжения...";
                else
                    remove_bot;
                fi
                ;;
            12) 
                if [[ "$INSTALLATION_TYPE" == *"webhook"* ]]; then
                    remove_bot;
                fi
                ;;
            0) exit 0; ;;
            *) error "Неверный выбор"; read -p "Нажмите Enter для продолжения..."; ;;
        esac
    fi
}

install_bot() {
    log "Начало установки RemnaWave Bedolaga Bot"
    
    update_system
    install_docker
    create_project_structure
    create_docker_compose
    create_env_file
    create_service
    
    log "Установка завершена!"
    log "Бот установлен в: $BOT_DIR"
    
    if [ "$COMPOSE_TYPE" = "panel_bot" ]; then
        echo ""
        echo -e "${YELLOW}=== ВАЖНО ДЛЯ ПАНЕЛЬ + БОТ УСТАНОВКИ ===${NC}"
        echo -e "${GREEN}1. Перед запуском бота убедитесь что панель RemnaWave запущена${NC}"
        echo -e "${GREEN}2. Панель должна создать сеть 'remnawave-network'${NC}"
        echo -e "${GREEN}3. Проверить сеть: docker network ls | grep remnawave${NC}"
        echo -e "${YELLOW}4. Если сети нет - сначала запустите панель RemnaWave!${NC}"
        echo ""
    elif [[ "$COMPOSE_TYPE" == webhook_* ]]; then
        echo ""
        echo -e "${YELLOW}=== ВАЖНО ДЛЯ WEBHOOK УСТАНОВКИ ===${NC}"
        echo -e "${GREEN}1. Webhook URL: https://$WEBHOOK_DOMAIN/webhook${NC}"
        echo -e "${GREEN}2. Убедитесь что домен указывает на этот сервер${NC}"
        echo -e "${GREEN}3. SSL будет настроен автоматически${NC}"
        
        if [ "$WEBHOOK_CONFIG_TYPE" = "external_caddy" ]; then
            echo -e "${YELLOW}4. Добавьте конфигурацию в существующий Caddyfile${NC}"
        elif [ "$WEBHOOK_CONFIG_TYPE" = "external_nginx" ]; then
            echo -e "${YELLOW}4. Добавьте конфигурацию в существующий Nginx${NC}"
            echo -e "${BLUE}   Файл конфигурации: $BOT_DIR/nginx-site.conf${NC}"
        fi
        
        echo -e "${GREEN}5. После запуска протестируйте: curl https://$WEBHOOK_DOMAIN/health${NC}"
        echo ""
    fi
    
    log "Для управления ботом используйте это меню или systemctl"
    
    read -p "Нажмите Enter для перехода в меню управления..."
}

main() {
    check_root
    
    if check_installation; then
        log "Бот уже установлен. Переход в меню управления..."
        ensure_nano  
        
        if [ -f "$COMPOSE_FILE" ]; then
            if grep -q "remnawave-network" "$COMPOSE_FILE" && grep -q "external: true" "$COMPOSE_FILE"; then
                export COMPOSE_TYPE="panel_bot"
            elif grep -q "caddy:" "$COMPOSE_FILE"; then
                export COMPOSE_TYPE="webhook_caddy"
                if [ -f "$BOT_DIR/Caddyfile" ]; then
                    export WEBHOOK_DOMAIN=$(grep -E "^[a-zA-Z0-9.-]+\s*{" "$BOT_DIR/Caddyfile" | head -1 | awk '{print $1}')
                fi
            elif grep -q "nginx:" "$COMPOSE_FILE"; then
                export COMPOSE_TYPE="webhook_nginx"
                if [ -f "$BOT_DIR/nginx.conf" ]; then
                    export WEBHOOK_DOMAIN=$(grep "server_name" "$BOT_DIR/nginx.conf" | grep -v "_" | head -1 | awk '{print $2}' | sed 's/;//')
                fi
            elif grep -q "bot_network" "$COMPOSE_FILE"; then
                export COMPOSE_TYPE="standalone"
            else
                export COMPOSE_TYPE="full"
            fi
        fi
    else
        install_bot
    fi
    
    while true; do
        show_menu
    done
}

main "$@"
