"""Pins for the cache service (Valkey) definition in the compose files.

The bot talks to the cache strictly through redis-py / REDIS_URL, so the only
places where the Redis→Valkey switch can silently break are the compose files
themselves: an image/binary mismatch (the valkey image ships redis-* only as compat symlinks, which upstream may drop - we pin the native valkey-* binaries) or the two compose files drifting
apart. These tests parse both files and pin the invariants.
"""

from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parent.parent
COMPOSE_FILES = ('docker-compose.yml', 'docker-compose.local.yml')


def _redis_service(compose_name: str) -> dict:
    compose = yaml.safe_load((REPO_ROOT / compose_name).read_text(encoding='utf-8'))
    return compose['services']['redis']


def test_both_compose_files_use_the_same_valkey_image() -> None:
    images = {name: _redis_service(name)['image'] for name in COMPOSE_FILES}
    assert len(set(images.values())) == 1, f'compose files disagree on the cache image: {images}'
    image = next(iter(images.values()))
    assert image.startswith('valkey/valkey:9'), image


def test_command_and_healthcheck_match_the_valkey_image() -> None:
    for name in COMPOSE_FILES:
        service = _redis_service(name)
        command = service['command']
        assert command.startswith('valkey-server '), (
            f'{name}: the valkey image ships valkey-server, not redis-server: {command}'
        )
        healthcheck = service['healthcheck']['test']
        assert 'valkey-cli' in healthcheck, (
            f'{name}: the healthcheck must call valkey-cli from the valkey image: {healthcheck}'
        )


def test_cache_behaviour_flags_preserved() -> None:
    """The Redis-era runtime flags must survive the image switch."""
    for name in COMPOSE_FILES:
        command = _redis_service(name)['command']
        for flag in ('--appendonly yes', '--maxmemory 256mb', '--maxmemory-policy allkeys-lru'):
            assert flag in command, f'{name}: lost cache flag {flag!r}'


def test_bot_still_connects_via_redis_url_scheme() -> None:
    """redis-py keeps the redis:// scheme with Valkey; service/volume names stay."""
    for name in COMPOSE_FILES:
        compose = yaml.safe_load((REPO_ROOT / name).read_text(encoding='utf-8'))
        bot_env = compose['services']['bot']['environment']
        assert bot_env['REDIS_URL'].startswith('redis://redis:'), f'{name}: {bot_env["REDIS_URL"]}'
        assert 'redis_data' in compose['volumes'], f'{name}: the data volume must keep its name'
        assert any(v.startswith('redis_data:/data') for v in compose['services']['redis']['volumes']), name
