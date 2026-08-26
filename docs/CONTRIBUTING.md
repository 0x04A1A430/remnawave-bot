# Contributing to @xilarobot

Thanks for your interest in improving the project! This document contains rules and guidelines for contributors.

## Table of Contents

- [Code of Conduct](#code-of-conduct)
- [How to Help](#how-to-help)
- [Development Setup](#development-setup)
- [Code Standards](#code-standards)
- [Git Workflow](#git-workflow)
- [Testing](#testing)
- [Review Process](#review-process)
- [Documentation](#documentation)

## Code of Conduct

### Core Principles

- **Respect** - treat project participants with respect
- **Constructiveness** - propose solutions, not just criticism
- **Openness** - be open to feedback and new ideas
- **Professionalism** - maintain a high level of discussion

### Unacceptable Behavior

- Insults and personal attacks
- Spam and off-topic messages
- Publishing private information
- Any form of discrimination

## How to Help

### Bug Reports

Before creating an issue, check:
- [ ] A similar problem has not been reported before
- [ ] You are using the current version of the bot
- [ ] The problem is reproducible consistently

**Bug report template:**

```markdown
## Bug Description
A brief description of the problem

## Steps to Reproduce
1. Go to '...'
2. Click on '...'
3. See the error

## Expected Behavior
What should have happened

## Actual Behavior
What actually happened

## Environment
- Bot version: [e.g. 1.0.0]
- Python version: [e.g. 3.13.1]
- OS: [e.g. Ubuntu 22.04]
- Docker version: [if applicable]

## Logs
```
Paste relevant logs here
```

## Screenshots
If applicable, add screenshots
```

### Feature Requests

Use the `enhancement` label and describe:
- **The problem** the feature solves
- **The proposed solution**
- **Alternatives** you have considered
- **Additional context**

### Documentation Improvements

- Fix typos
- Add examples
- Translate to other languages
- Improve structure

## Development Setup

### Requirements

- Python 3.13+
- Docker and Docker Compose
- Git
- PostgreSQL 15+ (optional for local development)
- Redis (optional for local development)

### Installation

1. **Fork and clone the repository:**
```bash
git clone https://github.com/0x04A1A430/remnawave-bot.git
cd remnawave-bot
```

2. **Install dependencies with uv:**
```bash
uv sync --group dev
```

3. **Configure the environment:**
```bash
# Create a .env file and fill in your values
# Refer to the configuration documentation for required variables
```

4. **Run via Docker (recommended):**
```bash
docker compose up -d postgres redis
python -m app.main
```

### Project Structure

```
remnawave-bot/
├── app/                     # Application source code
│   ├── handlers/            # Telegram message handlers
│   ├── services/            # Business logic
│   ├── database/            # Models and CRUD operations
│   ├── utils/               # Utilities
│   ├── middlewares/         # Middleware
│   └── external/            # External API clients
├── migrations/              # Database migrations (Alembic)
├── tests/                   # Tests
├── docs/                    # Documentation
├── scripts/                 # Helper scripts
└── pyproject.toml           # Dependencies and tool config
```

## Code Standards

### Python Style

We follow **PEP 8** with some exceptions:

```python
# Good
async def get_user_subscription(user_id: int) -> Subscription | None:
    """Return the user's active subscription."""
    async with get_session() as session:
        result = await session.execute(
            select(Subscription)
            .where(Subscription.user_id == user_id)
            .where(Subscription.is_active == True)  # noqa: E712
        )
        return result.scalar_one_or_none()

# Bad
async def getUserSub(uid):
    session = get_session()
    sub = session.query(Subscription).filter(Subscription.user_id==uid,Subscription.is_active==True).first()
    return sub
```

### Naming Conventions

- **Functions and variables**: `snake_case`
- **Classes**: `PascalCase`
- **Constants**: `UPPER_CASE`
- **Private methods**: `_leading_underscore`

### Type Hints

Always use type hints:

```python
async def create_subscription(
    user_id: int,
    duration_days: int,
    traffic_limit_gb: int | None = None,
) -> Subscription:
    """Create a new subscription."""
    # implementation
```

### Error Handling

```python
# Good
try:
    subscription = await subscription_service.create_subscription(user_id, data)
    await message.answer("Subscription created successfully!")
except RemnaWaveAPIError as e:
    logger.error("RemnaWave API error: %s", e)
    await message.answer("Failed to create subscription. Try again later.")
except ValidationError as e:
    logger.warning("Validation error: %s", e)
    await message.answer("Invalid data for subscription creation.")

# Bad
try:
    subscription = await subscription_service.create_subscription(user_id, data)
    await message.answer("Subscription created successfully!")
except:
    await message.answer("Error")
```

### Logging

```python
import structlog

logger = structlog.get_logger(__name__)

# Log levels
logger.debug("Detailed information for debugging")
logger.info("General operational information")
logger.warning("Warning about a potential issue")
logger.error("Non-fatal error")
logger.critical("Critical error")
```

## Git Workflow

### Branches

- `main` - stable release version
- `dev` - active development

### Commits

Use [Conventional Commits](https://www.conventionalcommits.org/):

```bash
# Commit types
feat: add CryptoBot payment support
fix: fix subscription price calculation
docs: update setup documentation
style: fix code formatting
refactor: refactor payment service
test: add tests for subscription_service
chore: update dependencies

# Examples
git commit -m "feat(payments): add YooKassa webhook support"
git commit -m "fix(subscription): fix device cost calculation"
git commit -m "docs(readme): update installation instructions"
```

### Pull Request Process

1. **Create a branch** from `dev`:
```bash
git checkout dev
git pull origin dev
git checkout -b feature/new-payment-method
```

2. **Develop the feature** following the standards above

3. **Test** your changes locally

4. **Create a Pull Request** with a description:

```markdown
## Description
A brief description of the changes

## Motivation
Why are these changes needed?

## Type of Changes
- [ ] Bug fix
- [ ] New feature
- [ ] Breaking change
- [ ] Documentation update

## Checklist
- [ ] Code follows project standards
- [ ] Tests added/updated
- [ ] Documentation updated
- [ ] Verified in Docker
- [ ] Verified compatibility with existing API

## Testing
How the changes were tested:
- [ ] Local testing
- [ ] Testing with real Remnawave API
- [ ] Testing payment systems
```

## Testing

### Local Testing

```bash
# Run the full test suite
make test

# Or directly
uv run pytest -v
```

### Component Testing

```python
# tests/services/test_pricing_engine.py
import pytest
from app.services.pricing_engine import PricingEngine

def test_calculate_renewal_price():
    pricing = PricingEngine.calculate_renewal_price(
        subscription=mock_subscription,
        period_days=30,
        user=mock_user,
    )
    assert pricing.final_total > 0
    assert isinstance(pricing.final_total, int)
```

### Integration Tests

Test integration with:
- Remnawave API (with test data)
- Database
- Payment systems (sandbox mode)

## Review Process

### Reviewer Responsibilities

- Check compliance with code standards
- Verify functionality
- Check security (especially for payment-related features)
- Assess performance
- Verify compatibility with existing API

### Author Responsibilities

- Respond to all comments
- Address requested changes
- Update documentation when needed
- Ensure all checks pass

## Documentation

### Updating Documentation

When adding new features, update:

- `docs/README.md` - if API or configuration changed
- Code comments and docstrings
- Usage examples
- Changelog (auto-generated by release-please)

### Documentation Style

- Use clear and accessible language
- Include code examples
- Document possible errors and their solutions

## Issue and PR Labels

### Priority
- `priority:high` - high priority
- `priority:medium` - medium priority
- `priority:low` - low priority

### Type
- `bug` - defect
- `enhancement` - improvement
- `feature` - new feature
- `documentation` - docs
- `question` - question

### Area
- `payments` - payment systems
- `api` - Remnawave API
- `database` - database
- `ui/ux` - user interface
- `admin` - admin panel

## Security

### Reporting Vulnerabilities

To report critical security vulnerabilities:
- Contact the maintainer directly via Telegram
- Do not create public issues for vulnerabilities
- Allow time to fix the issue before public disclosure

### Security Guidelines

- Never commit API keys or passwords
- Use environment variables for sensitive data
- Validate all user input
- Use HTTPS for all external requests

## Getting Help

### Communication Channels

- **Telegram:** [@xilarobot](https://t.me/xilarobot) - general questions
- **GitHub Issues:** technical questions and bugs

### FAQ

**Q: How to set up local development without Docker?**
A: Install PostgreSQL and Redis locally, update `DATABASE_URL` and `REDIS_URL` in `.env`

**Q: Can I use SQLite for development?**
A: Yes, set `DATABASE_MODE=sqlite` in `.env`

**Q: How to test payment systems?**
A: Use test/sandbox modes provided by payment systems

**Q: What to do if tests fail?**
A: Check your `.env` configuration and make sure all services are running

## Contributor Checklist

Before submitting a PR, make sure:

- [ ] Code follows project standards
- [ ] Comments and docstrings added
- [ ] Functionality tested
- [ ] Documentation updated
- [ ] No sensitive data in the code
- [ ] Changes work in Docker
- [ ] PR includes a detailed description
- [ ] Appropriate labels applied

## Acknowledgements

Thank you to everyone who contributes to the project! Your help makes @xilarobot better for the whole community.
