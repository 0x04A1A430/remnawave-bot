"""Admin handler for managing required channel subscriptions."""

import structlog
from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.database.crud.required_channel import (
    add_channel,
    delete_channel,
    get_all_channels,
    get_channel_by_id,
    resolve_channel_id,
    toggle_channel,
    validate_channel_id,
)
from app.database.database import AsyncSessionLocal
from app.services.channel_subscription_service import channel_subscription_service
from app.utils.decorators import admin_required


logger = structlog.get_logger(__name__)

router = Router(name='admin_required_channels')


class AddChannelStates(StatesGroup):
    waiting_channel_id = State()
    waiting_channel_link = State()
    waiting_channel_title = State()


# -- List channels ----------------------------------------------------------------


def _channels_keyboard(channels: list) -> InlineKeyboardMarkup:
    buttons = []
    for ch in channels:
        status = 'ON' if ch.is_active else 'OFF'
        title = ch.title or ch.channel_id
        buttons.append(
            [
                InlineKeyboardButton(
                    text=f'{status} {title}',
                    callback_data=f'reqch:view:{ch.id}',
                )
            ]
        )
    buttons.append([InlineKeyboardButton(text='+ Add channel', callback_data='reqch:add')])
    buttons.append([InlineKeyboardButton(text='< Back', callback_data='admin:back')])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def _channel_detail_keyboard(channel_id: int, is_active: bool) -> InlineKeyboardMarkup:
    toggle_text = 'Disable' if is_active else 'Enable'
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=toggle_text, callback_data=f'reqch:toggle:{channel_id}')],
            [InlineKeyboardButton(text='Delete', callback_data=f'reqch:delete:{channel_id}')],
            [InlineKeyboardButton(text='< Back to list', callback_data='reqch:list')],
        ]
    )


@router.callback_query(F.data == 'reqch:list')
@admin_required
async def show_channels_list(callback: CallbackQuery, **kwargs) -> None:
    async with AsyncSessionLocal() as db:
        channels = await get_all_channels(db)

    if not channels:
        text = '<b>Required Channels</b>\n\nNo channels configured. Click "Add" to create one.'
    else:
        lines = ['<b>Required Channels</b>\n']
        for ch in channels:
            status = 'ON' if ch.is_active else 'OFF'
            title = ch.title or ch.channel_id
            lines.append(f'{status} <code>{ch.channel_id}</code> -- {title}')
        text = '\n'.join(lines)

    await callback.message.edit_text(text, reply_markup=_channels_keyboard(channels))
    await callback.answer()


@router.callback_query(F.data.startswith('reqch:view:'))
@admin_required
async def view_channel(callback: CallbackQuery, **kwargs) -> None:
    try:
        channel_db_id = int(callback.data.split(':')[2])
    except (ValueError, IndexError):
        await callback.answer('Invalid channel ID', show_alert=True)
        return
    async with AsyncSessionLocal() as db:
        ch = await get_channel_by_id(db, channel_db_id)

    if not ch:
        await callback.answer('Channel not found', show_alert=True)
        return

    status = 'Active' if ch.is_active else 'Disabled'
    text = (
        f'<b>{ch.title or "Untitled"}</b>\n\n'
        f'<b>ID:</b> <code>{ch.channel_id}</code>\n'
        f'<b>Link:</b> {ch.channel_link or "--"}\n'
        f'<b>Status:</b> {status}\n'
        f'<b>Sort order:</b> {ch.sort_order}'
    )

    await callback.message.edit_text(text, reply_markup=_channel_detail_keyboard(ch.id, ch.is_active))
    await callback.answer()


# -- Toggle / Delete ---------------------------------------------------------------


@router.callback_query(F.data.startswith('reqch:toggle:'))
@admin_required
async def toggle_channel_handler(callback: CallbackQuery, **kwargs) -> None:
    try:
        channel_db_id = int(callback.data.split(':')[2])
    except (ValueError, IndexError):
        await callback.answer('Invalid channel ID', show_alert=True)
        return
    async with AsyncSessionLocal() as db:
        ch = await toggle_channel(db, channel_db_id)

    if ch:
        await channel_subscription_service.invalidate_channels_cache()
        status = 'enabled' if ch.is_active else 'disabled'
        await callback.answer(f'Channel {status}', show_alert=True)

    # Refresh list
    async with AsyncSessionLocal() as db:
        channels = await get_all_channels(db)
    await callback.message.edit_text(
        '<b>Required Channels</b>',
        reply_markup=_channels_keyboard(channels),
    )


@router.callback_query(F.data.startswith('reqch:delete:'))
@admin_required
async def delete_channel_handler(callback: CallbackQuery, **kwargs) -> None:
    try:
        channel_db_id = int(callback.data.split(':')[2])
    except (ValueError, IndexError):
        await callback.answer('Invalid channel ID', show_alert=True)
        return
    async with AsyncSessionLocal() as db:
        ok = await delete_channel(db, channel_db_id)

    if ok:
        await channel_subscription_service.invalidate_channels_cache()
        await callback.answer('Channel deleted', show_alert=True)
    else:
        await callback.answer('Delete failed', show_alert=True)

    async with AsyncSessionLocal() as db:
        channels = await get_all_channels(db)
    await callback.message.edit_text(
        '<b>Required Channels</b>',
        reply_markup=_channels_keyboard(channels),
    )


# -- Add channel flow --------------------------------------------------------------


@router.callback_query(F.data == 'reqch:add')
@admin_required
async def start_add_channel(callback: CallbackQuery, state: FSMContext, **kwargs) -> None:
    await state.set_state(AddChannelStates.waiting_channel_id)
    await callback.message.edit_text(
        '<b>Add Channel</b>\n\nSend channel ID (e.g. <code>@mychannel</code> or <code>-1001234567890</code>):'
    )
    await callback.answer()


@router.message(AddChannelStates.waiting_channel_id)
@admin_required
async def process_channel_id(message: Message, state: FSMContext, **kwargs) -> None:
    if not message.text:
        await message.answer('Please send a text message.')
        return
    channel_id = message.text.strip()

    # Validate channel_id format
    try:
        channel_id = validate_channel_id(channel_id)
    except ValueError as e:
        await message.answer(f'Invalid format. {e}\n\nTry again:')
        return

    # Resolve @username to numeric ID (ChatMemberUpdated events use numeric IDs)
    original_channel_id = channel_id
    bot: Bot = message.bot
    try:
        channel_id = await resolve_channel_id(bot, channel_id)
    except ValueError as e:
        await message.answer(f'Cannot verify channel: {e}\n\nMake sure the bot is admin in this channel. Try again:')
        return

    await state.update_data(channel_id=channel_id, original_channel_id=original_channel_id)
    await state.set_state(AddChannelStates.waiting_channel_link)
    await message.answer(
        f'Channel: <code>{channel_id}</code>\n\n'
        'Now send the channel link (e.g. <code>https://t.me/mychannel</code>)\n'
        'Or send <code>-</code> to skip:'
    )


@router.message(AddChannelStates.waiting_channel_link)
@admin_required
async def process_channel_link(message: Message, state: FSMContext, **kwargs) -> None:
    if not message.text:
        await message.answer('Please send a text message.')
        return
    link = message.text.strip()
    if link == '-':
        link = None

    if link is not None:
        # Validate and normalize channel link
        if not link.startswith(('https://t.me/', 'http://t.me/', '@')):
            await message.answer('Link must be a t.me URL or @username. Try again:')
            return
        if link.startswith('@'):
            link = f'https://t.me/{link[1:]}'
        if link.startswith('http://'):
            link = link.replace('http://', 'https://', 1)

    await state.update_data(channel_link=link)
    await state.set_state(AddChannelStates.waiting_channel_title)
    await message.answer(
        'Send display name for the channel (e.g. <code>Project News</code>)\nOr send <code>-</code> to skip:'
    )


@router.message(AddChannelStates.waiting_channel_title)
@admin_required
async def process_channel_title(message: Message, state: FSMContext, **kwargs) -> None:
    if not message.text:
        await message.answer('Please send a text message.')
        return
    title = message.text.strip()
    if title == '-':
        title = None

    data = await state.get_data()
    await state.clear()

    # Use original @username as title fallback when channel was resolved to numeric
    original_id = data.get('original_channel_id')
    if not title and original_id and original_id != data['channel_id']:
        title = original_id

    async with AsyncSessionLocal() as db:
        try:
            ch = await add_channel(
                db,
                channel_id=data['channel_id'],
                channel_link=data.get('channel_link'),
                title=title,
            )
            await channel_subscription_service.invalidate_channels_cache()

            text = (
                'Channel added!\n\n'
                f'<b>ID:</b> <code>{ch.channel_id}</code>\n'
                f'<b>Link:</b> {ch.channel_link or "--"}\n'
                f'<b>Title:</b> {ch.title or "--"}'
            )
        except Exception as e:
            text = 'Error adding channel. Please try again.'
            logger.error('Error adding channel', error=e)

    async with AsyncSessionLocal() as db:
        channels = await get_all_channels(db)

    await message.answer(text, reply_markup=_channels_keyboard(channels))


def register_handlers(dp_router: Router) -> None:
    dp_router.include_router(router)
