"""Папочный workflow для subscription-templates Remnawave.

Все шаблоны выгружаются в каталог, правятся там, затем одной командой
создаются/обновляются в панели. Grace-шаблоны — такие же файлы, но с
``uuid: null`` до первого применения.

Примеры:
    python -m app.tools.panel_templates export --dir /root/remnawave-templates
    python -m app.tools.panel_templates make-grace --dir /root/remnawave-templates
    python -m app.tools.panel_templates apply --dir /root/remnawave-templates --only grace
    python -m app.tools.panel_templates assign-squad --dir /root/remnawave-templates \
        --squad-uuid <uuid Grace external squad> --from grace
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from app.services.remnawave_service import RemnaWaveService


DEFAULT_DIR = Path('/root/remnawave-templates')
TEMPLATE_TYPES = ('XRAY_JSON', 'MIHOMO', 'STASH', 'CLASH', 'SINGBOX')
PLACEHOLDER_UUID = ''
GRACE_TAG = 'GRACE'


def _safe(value: str) -> str:
    cleaned = re.sub(r'[^\w.-]+', '_', value, flags=re.UNICODE).strip('_')
    return cleaned or 'template'


def _template_file(root: Path, template: dict[str, Any]) -> Path:
    template_type = str(template.get('templateType') or 'UNKNOWN')
    name = _safe(str(template.get('name') or 'template'))
    uuid = str(template.get('uuid') or 'new')
    return root / 'templates' / template_type / f'{name}__{uuid}.json'


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def _read_files(directory: Path) -> Iterable[tuple[Path, dict[str, Any]]]:
    if not directory.exists():
        return []
    result: list[tuple[Path, dict[str, Any]]] = []
    for path in sorted(directory.rglob('*.json')):
        if path.name == 'manifest.json':
            continue
        try:
            payload = json.loads(path.read_text(encoding='utf-8'))
        except Exception as error:
            print(f'! пропуск {path}: {error}')
            continue
        if isinstance(payload, dict):
            result.append((path, payload))
    return result


def _manifest(root: Path, templates: list[dict[str, Any]]) -> None:
    manifest = {
        'templates': [
            {
                'uuid': t.get('uuid'),
                'name': t.get('name'),
                'templateType': t.get('templateType'),
                'tags': t.get('tags') or [],
                'file': str(_template_file(root, t).relative_to(root)),
            }
            for t in templates
        ]
    }
    _write_json(root / 'manifest.json', manifest)


async def export_templates(root: Path) -> None:
    service = RemnaWaveService()
    async with service.get_api_client() as api:
        listed = await api.get_subscription_templates()
        full: list[dict[str, Any]] = []
        for item in listed:
            uuid = str(item.get('uuid') or '')
            if not uuid:
                continue
            template = await api.get_subscription_template(uuid)
            full.append(template)
            _write_json(_template_file(root, template), template)
            print(f'✓ {template.get("templateType")}: {template.get("name")} → {_template_file(root, template)}')
    _manifest(root, full)
    print(f'Готово: {len(full)} шаблонов в {root}')


def make_grace(root: Path, source_name: str = 'Default') -> None:
    files = _read_files(root / 'templates')
    grouped: dict[str, list[dict[str, Any]]] = {}
    for _, payload in files:
        template_type = str(payload.get('templateType') or '')
        if template_type:
            grouped.setdefault(template_type, []).append(payload)

    for template_type in TEMPLATE_TYPES:
        candidates = grouped.get(template_type) or []
        if not candidates:
            print(f'! {template_type}: исходных шаблонов нет')
            continue
        source = next((t for t in candidates if str(t.get('name') or '') == source_name), candidates[0])
        path = root / 'grace' / f'{template_type}__Grace.json'
        existing_uuid = None
        if path.exists():
            try:
                existing_uuid = json.loads(path.read_text(encoding='utf-8')).get('uuid')
            except Exception:
                existing_uuid = None
        grace = {
            'uuid': existing_uuid,
            'name': f'Grace {template_type}',
            'templateType': template_type,
            'tags': [GRACE_TAG],
            'templateJson': source.get('templateJson'),
            'encodedTemplateYaml': source.get('encodedTemplateYaml'),
        }
        _write_json(path, grace)
        print(f'✓ Grace {template_type} из {source.get("name")} → {path}')


async def apply_templates(root: Path, only: str, dry_run: bool = False) -> None:
    if only == 'grace':
        files = _read_files(root / 'grace')
    elif only == 'templates':
        files = _read_files(root / 'templates')
    else:
        files = [*_read_files(root / 'templates'), *_read_files(root / 'grace')]

    if not files:
        print(f'Нет файлов для применения в {root}/{only}')
        return

    service = RemnaWaveService()
    async with service.get_api_client() as api:
        existing = {str(t.get('uuid')): t for t in await api.get_subscription_templates()}
        applied: list[dict[str, Any]] = []
        for path, payload in files:
            name = str(payload.get('name') or '')
            template_type = str(payload.get('templateType') or '')
            template_json = payload.get('templateJson')
            encoded_yaml = payload.get('encodedTemplateYaml')
            tags = payload.get('tags') or []
            uuid = str(payload.get('uuid') or '')
            if not name or not template_type:
                print(f'! пропуск {path}: нет name/templateType')
                continue
            if not isinstance(template_json, dict) and not encoded_yaml:
                print(f'! пропуск {path}: нет templateJson/encodedTemplateYaml')
                continue

            if dry_run:
                action = 'update' if uuid and uuid in existing else 'create'
                print(f'· {action}: {template_type} / {name} ({path.name})')
                continue

            if uuid and uuid in existing:
                updated = await api.update_subscription_template(
                    uuid,
                    name=name,
                    template_type=template_type,
                    template_json=template_json if isinstance(template_json, dict) else None,
                    encoded_template_yaml=encoded_yaml,
                    tags=tags,
                )
                print(f'↻ обновлён: {template_type} / {name} ({uuid})')
            else:
                created = await api.create_subscription_template(
                    name,
                    template_type,
                    template_json if isinstance(template_json, dict) else None,
                    encoded_template_yaml=encoded_yaml,
                    tags=tags,
                )
                updated = await api.update_subscription_template(
                    str(created['uuid']),
                    name=name,
                    template_type=template_type,
                    template_json=template_json if isinstance(template_json, dict) else None,
                    encoded_template_yaml=encoded_yaml,
                    tags=tags,
                )
                payload['uuid'] = created['uuid']
                _write_json(path, payload)
                print(f'+ создан: {template_type} / {name} ({created["uuid"]})')

            applied.append(updated)

    if not dry_run:
        print(f'Применено шаблонов: {len(applied)}')


async def assign_squad(root: Path, squad_uuid: str, source: str = 'grace') -> None:
    files = _read_files(root / source)
    entries: dict[str, dict[str, str]] = {}
    for _, payload in files:
        uuid = str(payload.get('uuid') or '')
        template_type = str(payload.get('templateType') or '')
        if not uuid or not template_type:
            continue
        # Панель хранит один шаблон на тип ядра.
        entries.setdefault(template_type, {'templateUuid': uuid, 'templateType': template_type})

    if not entries:
        print(f'Нет шаблонов с uuid в {root}/{source}')
        return

    service = RemnaWaveService()
    async with service.get_api_client() as api:
        squad = await api.get_external_squad_by_uuid(squad_uuid)
        if squad is None:
            print(f'! External squad {squad_uuid} не найден')
            return
        await api.update_external_squad(squad_uuid, templates=list(entries.values()))
        print(f'✓ {squad.name}: назначено {len(entries)} шаблонов ({", ".join(sorted(entries))})')


def main() -> None:
    parser = argparse.ArgumentParser(description='Папочный workflow subscription-templates Remnawave')
    parser.add_argument('command', choices=['export', 'make-grace', 'apply', 'assign-squad'])
    parser.add_argument('--dir', type=Path, default=DEFAULT_DIR)
    parser.add_argument('--only', choices=['grace', 'templates', 'all'], default='all')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--source-name', default='Default')
    parser.add_argument('--squad-uuid', default='')
    parser.add_argument('--from', dest='source', choices=['grace', 'templates'], default='grace')
    args = parser.parse_args()

    if args.command == 'export':
        asyncio.run(export_templates(args.dir))
    elif args.command == 'make-grace':
        make_grace(args.dir, source_name=args.source_name)
    elif args.command == 'apply':
        asyncio.run(apply_templates(args.dir, args.only, dry_run=args.dry_run))
    elif args.command == 'assign-squad':
        if not args.squad_uuid:
            parser.error('--squad-uuid обязателен для assign-squad')
        asyncio.run(assign_squad(args.dir, args.squad_uuid, source=args.source))


if __name__ == '__main__':
    main()
