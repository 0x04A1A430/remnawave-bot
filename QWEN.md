# Qwen Code Context - Remnawave Bedolaga Telegram Bot

## Project Overview

The Remnawave Bedolaga Telegram Bot is a modern VPN subscription management system built with Python, aiogram, and FastAPI. It provides a comprehensive solution for managing VPN subscriptions through the Remnawave API with extensive features including multi-channel payments, user management, admin functionality, and automated systems.

## Architecture and Technology Stack

### Core Technologies
- **Python 3.13+** with AsyncIO for maximum performance
- **aiogram 3** for Telegram Bot API interactions
- **FastAPI** for REST API services
- **PostgreSQL 15+** for reliable data storage (with SQLite fallback)
- **Redis** for caching and session management
- **SQLAlchemy ORM** for safe database operations
- **Pydantic v2** for data validation

### Key Components
- **Payment Service**: Supports multiple payment methods (Telegram Stars, YooKassa, Tribute, CryptoBot, Heleket, MulenPay, Pal24, WATA)
- **Subscription Service**: Manages VPN subscriptions and syncs with Remnawave panel
- **Monitoring Service**: Regular API checks and automatic maintenance mode
- **Backup Service**: Smart automatic backups with scheduling
- **Web API**: Full-featured admin API with endpoints for all aspects

## Project Structure

```
remnawave-bedolaga-telegram-bot/
├── app/                          # Main application code
│   ├── bot.py                   # Bot initialization and setup
│   ├── config.py                # Configuration and settings
│   ├── database/                # Database models and CRUD operations
│   ├── external/                # External webhook servers
│   ├── handlers/                # Bot command and callback handlers
│   ├── keyboards/               # Inline and reply keyboards
│   ├── localization/            # Translation and locale management
│   ├── middlewares/             # Bot middlewares
│   ├── services/                # Business logic services
│   ├── utils/                   # Utility functions
│   ├── webapi/                  # Web API endpoints
│   └── states.py                # FSM states
├── assets/                      # Project assets
├── data/                        # Runtime data storage
├── docs/                        # Documentation
├── logs/                        # Log files
├── .env.example                 # Environment configuration template
├── docker-compose.yml           # Docker services configuration
├── Dockerfile                   # Docker image definition
├── install_bot.sh               # Installation and management script
├── main.py                      # Main application entry point
├── README.md                    # Project documentation
└── requirements.txt             # Python dependencies
```

## Key Features

### For Users
- **Multi-channel payments**: Telegram Stars, Tribute, CryptoBot, YooKassa, MulenPay, PayPalych, WATA
- **Smart subscription purchase**: Flexible periods with discounts, traffic selection, server selection
- **Trial subscriptions**: Configurable trial with welcome sequence
- **Auto-pay**: Configurable auto-renewal with day selection
- **Referral system**: Commission-based referral program
- **MiniApp dashboard**: Full personal account inside Telegram
- **Multiple languages**: RU/EN with dynamic localization

### For Administrators
- **Analytics**: Detailed dashboards for users, subscriptions, and payments
- **User management**: Search, filters, detailed user cards
- **Promo system**: Codes, groups, personal offers
- **Ticket system**: Support with priorities and SLA
- **Backup and restore**: Smart automatic backups with scheduling
- **Web API**: Full integration capabilities
- **Monitoring**: Server monitoring and health checks

## Configuration

### Environment Variables
The bot is configured through `.env` file with comprehensive settings including:
- Bot token and admin IDs
- Remnawave panel integration (URL, API key, secret key)
- Database configuration (PostgreSQL or SQLite)
- Payment system configurations
- Pricing and subscription settings
- Feature flags and UI options

### Key Configuration Options
- `BOT_TOKEN`: Telegram bot token from BotFather
- `REMNAWAVE_API_URL`: Your Remnawave panel URL
- `REMNAWAVE_API_KEY`: API key for your panel
- `REMNAWAVE_SECRET_KEY`: Panel protection key (for eGames panels)
- `ADMIN_IDS`: Telegram IDs of administrators
- Payment system specific configurations
- Database and Redis settings

## Building and Running

### Docker Setup (Recommended)
The project uses Docker Compose for easy deployment:

```bash
# Clone repository
git clone https://github.com/Fr1ngg/remnawave-bedolaga-telegram-bot.git
cd remnawave-bedolaga-telegram-bot

# Configure environment
cp .env.example .env
# Edit .env with your settings

# Create necessary directories
mkdir -p ./logs ./data ./data/backups ./data/referral_qr
chmod -R 755 ./logs ./data
sudo chown -R 1000:1000 ./logs ./data

# Start all services
docker compose up -d

# Check status
docker compose logs
```

### Management Script
The `install_bot.sh` script provides an interactive management menu:
- Container status monitoring and resource monitoring
- Service management (start/stop/rebuild)
- Log viewing and search
- Git update with automatic backup
- Backup and restore functionality
- Environment configuration

## Key Services and Components

### Payment Service
Handles all payment system integrations with support for:
- Telegram Stars (built-in)
- YooKassa (SBP and cards)
- Tribute (cryptocurrency)
- CryptoBot (USDT, TON, BTC, ETH and others)
- Heleket (cryptocurrency with markup)
- MulenPay (SBP)
- PayPalych/Pal24 (SBP and cards)
- WATA (bank card payments)

### Subscription Service
Manages VPN subscriptions and integrates with Remnawave API:
- Creates and updates users in the Remnawave panel
- Handles subscription expiration and traffic limits
- Supports different traffic reset strategies
- Syncs subscription usage and status

### Web API and MiniApp
- FastAPI Web API with endpoints for all aspects
- MiniApp personal account inside Telegram
- Integrated payments in MiniApp
- App Config for centralized link distribution

### Database and Storage
- PostgreSQL: Primary database for user data and subscriptions
- Redis: Fast caching and session storage for cart functionality
- Migration system: Automatic schema updates
- Backup system: Automatic database backups

## Development Conventions

### Code Structure
- **Modular architecture**: Subscription and payment modules are separate
- **AsyncIO**: All operations are asynchronous for maximum performance
- **Type hints**: Full type annotation coverage
- **Dependency injection**: Services are properly injected and managed
- **Pydantic models**: Data validation and configuration management

### Error Handling
- **Graceful shutdown**: Proper cleanup on termination signals
- **Service restart**: Automatic restart of failed services
- **Comprehensive logging**: Detailed logs for debugging and monitoring
- **Middleware protection**: Global error handling for callback queries

### Testing and Quality
- The codebase includes various test files and debugging tools
- Uses pytest for testing (evidenced by .pytest_cache directory)
- Type checking supported through development practices

## Security Features

- **API key validation**: All external API calls validated
- **Rate limiting**: Protection against spam
- **Data encryption**: Sensitive data encrypted
- **Session management**: Automatic session management
- **Suspicious activity monitoring**: Activity monitoring and alerts
- **Username blocking**: Automatic blocking of suspicious names

## Health Checks and Monitoring

- Main: `http://localhost:8081/health`
- YooKassa: `http://localhost:8082/health`
- Pal24: `http://localhost:8084/health`

## Troubleshooting Commands

```bash
# View logs in real-time
docker compose logs -f bot

# Status of all containers
docker compose ps

# Restart only the bot
docker compose restart bot

# Check database
docker compose exec postgres pg_isready -U remnawave_user

# Connect to database
docker compose exec postgres psql -U remnawave_user -d remnawave_bot

# Check Redis
docker compose exec redis redis-cli ping
```

## Main Entry Point

The application starts in `main.py` which:
- Initializes the database and runs migrations
- Sets up the bot with aiogram
- Starts webhook servers for various payment systems
- Launches background services (monitoring, maintenance, version checking)
- Begins polling for Telegram updates
- Handles graceful shutdown

## Localization

The bot supports multiple languages (RU/EN) with:
- Dynamic language selection
- Comprehensive translation files
- Localized user interface elements
- Proper formatting for prices and dates

## Payment Integration

The bot supports multiple payment systems with:
- Webhook handling for payment confirmations
- Automatic balance updates
- Transaction history tracking
- Refund and cancellation support
- Payment method-specific configurations

## Administrative Features

Administrative functionality includes:
- User management (search, edit, ban)
- Subscription management (create, modify, extend)
- Promotional tools (codes, groups, offers)
- Payment configuration
- System monitoring and health checks
- Backup and restore capabilities
- Analytics and reporting