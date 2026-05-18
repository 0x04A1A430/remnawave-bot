"""Regression tests for the "Главное меню" button in cabinet-mode notifications.

Production incident (2026-05-18): in ``MAIN_MENU_MODE=cabinet``, the
"💸 Пополнение успешно" notification's last button (labelled
"🏠 Главное меню") opened the cabinet WebApp instead of returning the
user to the bot's main menu. Root cause:
``build_miniapp_or_callback_button(callback_data='back_to_menu')`` saw
``back_to_menu`` mapped to ``/`` in ``CALLBACK_TO_CABINET_PATH`` and
silently swapped the callback button for a WebApp launcher.

UX impact: user in cabinet mode taps "Главное меню" → cabinet root
loads again → user is stuck in the cabinet with no obvious escape to
the bot.

Two-layer defence:

  1. ``back_to_menu`` removed from ``CALLBACK_TO_CABINET_PATH``.
     Even if a caller wrongly passes it through
     ``build_miniapp_or_callback_button``, the helper falls through
     to a normal callback button.
  2. New dedicated helper ``build_main_menu_button(text)`` in
     ``app/utils/miniapp_buttons.py`` always returns a callback button,
     making the intent explicit at every call site.

These tests pin both layers.
"""

from __future__ import annotations

import pytest
from aiogram.types import InlineKeyboardButton

from app.config import settings
from app.utils.miniapp_buttons import (
    CALLBACK_TO_CABINET_PATH,
    CALLBACK_TO_CABINET_STYLE,
    build_main_menu_button,
    build_miniapp_or_callback_button,
)


# ---------------------------------------------------------------------------
# Layer 1: mapping defence.
# ---------------------------------------------------------------------------


def test_back_to_menu_is_not_in_cabinet_path_mapping() -> None:
    """REGRESSION: ``back_to_menu`` must NOT be a key in
    ``CALLBACK_TO_CABINET_PATH``. Its presence was the root cause of
    the bug: ``build_miniapp_or_callback_button`` consulted the
    mapping and silently swapped the callback for a WebApp launcher
    pointing at the cabinet root.

    Other callbacks like ``menu_balance`` legitimately ARE in the
    mapping because they semantically open a cabinet section. But
    ``back_to_menu`` semantically means "return to bot menu" and must
    never be cabinet-routed.
    """
    assert 'back_to_menu' not in CALLBACK_TO_CABINET_PATH, (
        'back_to_menu must NOT be in CALLBACK_TO_CABINET_PATH — the callback '
        "semantically means 'return to bot main menu', not 'open cabinet root'. "
        'Adding it back here will re-introduce the cabinet-mode UX trap where '
        'the user is stuck in an infinite "хочу в бот → попадаю в кабинет" loop.'
    )


def test_back_to_menu_is_not_in_cabinet_style_mapping() -> None:
    """Dead config caught: if ``back_to_menu`` were styled per-section
    here, the styling would be applied only when the WebApp path was
    used — which we've now disabled. Removing it from style mapping
    keeps the two configs in sync."""
    assert 'back_to_menu' not in CALLBACK_TO_CABINET_STYLE


def test_build_miniapp_or_callback_button_falls_through_for_back_to_menu(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Belt-and-suspenders: even in cabinet mode, calling
    ``build_miniapp_or_callback_button(callback_data='back_to_menu')``
    must produce a callback button — never a WebApp launcher.

    A future contributor who doesn't know about ``build_main_menu_button``
    might use the generic helper. The mapping omission guarantees they
    can't accidentally re-introduce the bug.
    """
    monkeypatch.setattr(settings, 'MAIN_MENU_MODE', 'cabinet', raising=False)
    # Set a cabinet URL — without it the helper falls through anyway,
    # so we'd be testing the wrong defence layer. The point of this
    # test is that EVEN WITH cabinet mode fully configured, back_to_menu
    # produces a callback button.
    monkeypatch.setattr(settings, 'MINIAPP_CUSTOM_URL', 'https://cabinet.example.com', raising=False)

    button = build_miniapp_or_callback_button(
        text='🏠 Главное меню',
        callback_data='back_to_menu',
    )

    assert isinstance(button, InlineKeyboardButton)
    assert button.callback_data == 'back_to_menu', (
        'In cabinet mode with cabinet URL configured, back_to_menu must STILL '
        'produce a callback button. WebApp launcher would re-introduce the '
        'incident where the user gets stuck in cabinet root.'
    )
    assert button.web_app is None, (
        'back_to_menu button must NOT have a WebAppInfo attached — that would '
        'open the cabinet root instead of firing the bot callback'
    )


# ---------------------------------------------------------------------------
# Layer 2: dedicated helper.
# ---------------------------------------------------------------------------


def test_build_main_menu_button_returns_callback_button() -> None:
    """The dedicated helper always returns a callback button. No mode
    detection, no URL check — pure intent expression."""
    button = build_main_menu_button('🏠 Главное меню')

    assert isinstance(button, InlineKeyboardButton)
    assert button.text == '🏠 Главное меню'
    assert button.callback_data == 'back_to_menu'
    assert button.web_app is None
    assert button.url is None


def test_build_main_menu_button_immune_to_cabinet_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the design contract: ``build_main_menu_button`` ignores
    ``MAIN_MENU_MODE`` entirely. This is the WHOLE POINT — it exists
    precisely so cabinet mode can't accidentally swap it."""
    monkeypatch.setattr(settings, 'MAIN_MENU_MODE', 'cabinet', raising=False)
    monkeypatch.setattr(settings, 'MINIAPP_CUSTOM_URL', 'https://cabinet.example.com', raising=False)

    button = build_main_menu_button('🏠 Main menu')

    assert button.callback_data == 'back_to_menu'
    assert button.web_app is None


# ---------------------------------------------------------------------------
# Producer: top-up success keyboard uses the dedicated helper.
# ---------------------------------------------------------------------------


def test_topup_success_keyboard_main_menu_button_is_callback() -> None:
    """Source-level pin: ``app/services/payment/common.py`` must use
    ``build_main_menu_button(texts.MAIN_MENU_BUTTON)`` for the Main
    Menu row, NOT ``build_miniapp_or_callback_button``.

    Whitespace-robust positive assertion only — the previous version
    had a literal-string negative match keyed to a specific 20-space
    indent, which would silently pass after any reformat that changed
    the indentation. We rely on the AST-based scan in
    ``test_no_other_callsite_wraps_back_to_menu_in_miniapp_helper``
    to catch the buggy pattern (it's resilient to formatting).
    """
    from pathlib import Path

    common_path = Path(__file__).resolve().parents[2] / 'app' / 'services' / 'payment' / 'common.py'
    source = common_path.read_text(encoding='utf-8')

    # The corrected form must be present.
    assert 'build_main_menu_button(texts.MAIN_MENU_BUTTON)' in source, (
        'build_topup_success_keyboard must call build_main_menu_button() for '
        'the Главное меню row to guarantee bot-callback semantics regardless '
        'of MAIN_MENU_MODE. AST scan below catches the buggy pattern.'
    )


# ---------------------------------------------------------------------------
# Convention pin: no other call site secretly wraps back_to_menu through
# build_miniapp_or_callback_button.
# ---------------------------------------------------------------------------


def test_no_other_callsite_wraps_back_to_menu_in_miniapp_helper() -> None:
    """AST-based scan: no callsite anywhere in ``app/`` may invoke
    ``build_miniapp_or_callback_button(..., callback_data='back_to_menu')``.

    The previous regex-based scan had a nested-paren blind spot — a
    contributor writing ``build_miniapp_or_callback_button(text=f'x {fn()} y',
    callback_data='back_to_menu')`` would slip past because the
    ``[^)]*?`` lookahead stopped at the first ``)``. AST walk handles
    nested calls naturally.
    """
    import ast
    from pathlib import Path

    app_root = Path(__file__).resolve().parents[2] / 'app'
    offenders: list[tuple[str, int]] = []

    # Skip the helper module itself — its docstring legitimately
    # references the anti-pattern as an example of what NOT to write.
    skip_files = {'miniapp_buttons.py'}

    class _BackToMenuMisuseFinder(ast.NodeVisitor):
        def __init__(self, file_path: Path) -> None:
            self.file_path = file_path

        def visit_Call(self, node: ast.Call) -> None:
            func_name: str | None = None
            if isinstance(node.func, ast.Name):
                func_name = node.func.id
            elif isinstance(node.func, ast.Attribute):
                func_name = node.func.attr

            if func_name == 'build_miniapp_or_callback_button':
                for kw in node.keywords:
                    if (
                        kw.arg == 'callback_data'
                        and isinstance(kw.value, ast.Constant)
                        and kw.value.value == 'back_to_menu'
                    ):
                        offenders.append((str(self.file_path), node.lineno))
                        break
            # Always recurse so nested calls are inspected.
            self.generic_visit(node)

    for py_file in app_root.rglob('*.py'):
        if py_file.name in skip_files:
            continue
        try:
            tree = ast.parse(py_file.read_text(encoding='utf-8'))
        except SyntaxError:
            # If a file has bad syntax it's a separate failure mode;
            # don't mask it with a vague test error here.
            continue
        _BackToMenuMisuseFinder(py_file).visit(tree)

    assert not offenders, (
        'AST scan found build_miniapp_or_callback_button(callback_data="back_to_menu") '
        f'at {offenders}. This wrapper turns the "Главное меню" button into a '
        'WebApp launcher in cabinet mode, trapping the user in the cabinet. '
        'Use build_main_menu_button(text) instead.'
    )


def test_home_button_key_is_not_in_cabinet_miniapp_button_keys() -> None:
    """Foot-gun pin: ``BUTTON_KEY_TO_CABINET_PATH['home'] = '/'`` exists
    for the admin-broadcast button vocabulary. It's currently inert
    because ``CABINET_MINIAPP_BUTTON_KEYS`` does NOT include ``'home'``
    — admin custom-button rendering at ``app/handlers/admin/messages.py``
    falls through to raw ``InlineKeyboardButton(callback_data='back_to_menu')``.

    If a future hand adds ``'home'`` to ``CABINET_MINIAPP_BUTTON_KEYS``,
    the admin broadcast's "Home" button would silently flip to WebApp
    in cabinet mode — same UX trap as the original incident. This pin
    fails loudly if that change ever happens, forcing the contributor
    to confirm intent.
    """
    from app.handlers.admin import messages as admin_messages

    cabinet_keys = getattr(admin_messages, 'CABINET_MINIAPP_BUTTON_KEYS', None)
    assert cabinet_keys is not None, 'CABINET_MINIAPP_BUTTON_KEYS expected in admin.messages'
    assert 'home' not in cabinet_keys, (
        "'home' key MUST NOT be added to CABINET_MINIAPP_BUTTON_KEYS — it routes "
        "through BUTTON_KEY_TO_CABINET_PATH['home']='/' which would re-introduce "
        'the cabinet-mode "Главное меню" trap for admin broadcast buttons. '
        "If 'home' truly must open the cabinet root, name it explicitly "
        "('cabinet_home' or similar) so reviewers see the intent."
    )
