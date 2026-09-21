import datetime
import logging
from typing import Optional
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.enums import ParseMode
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import func, select
from src.config import settings
from src.database import get_db_session
from src.models import Product, Subscription
from src.services.notifier import TelegramNotifier, AlertPayload

logger = logging.getLogger(__name__)

# Instancia global del despachador
dp = Dispatcher()
notifier = TelegramNotifier()


@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    """Mensaje de bienvenida y estado del bot."""
    welcome_text = (
        "🤖 <b>Bot Rastreador de Precios y Ofertas</b>\n\n"
        "Este bot monitorea automáticamente caídas de precio en tiendas online "
        "y publica ofertas con enlaces de afiliado en el canal oficial.\n\n"
        "<b>Comandos disponibles:</b>\n"
        "/misuscripcion - Consulta el estado de tu membresía VIP\n"
        "/status - Consulta el estado del sistema y productos en seguimiento\n"
        "/help - Instrucciones y ayuda"
    )
    await message.answer(welcome_text, parse_mode=ParseMode.HTML)


@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    """Instrucciones de uso."""
    help_text = (
        "📖 <b>Guía de Administración y Comandos</b>\n\n"
        "<b>Comandos de Usuario:</b>\n"
        "/misuscripcion - Revisa los días restantes de tu membresía VIP\n\n"
        "<b>Comandos de Administrador:</b>\n"
        "/suscriptores - Resumen de miembros activos, ingresos y vencimientos\n"
        "/status - Estado del catálogo y escaneo continuo\n\n"
        f"Frecuencia de escaneo: Cada {settings.CHECK_INTERVAL_MINUTES} minutos.\n"
        f"Membresía VIP: ${settings.SUBSCRIPTION_PRICE_CLP:,.0f} CLP / mes."
    )
    await message.answer(help_text, parse_mode=ParseMode.HTML)


@dp.message(Command("status"))
async def cmd_status(message: types.Message):
    """Muestra el total de productos activos e historial en base de datos."""
    async with get_db_session() as session:
        count_stmt = select(func.count(Product.id)).where(Product.activo.is_(True))
        total_active = (await session.execute(count_stmt)).scalar() or 0

        total_stmt = select(func.count(Product.id))
        total_products = (await session.execute(total_stmt)).scalar() or 0

        now = datetime.datetime.now(datetime.timezone.utc)
        sub_stmt = select(func.count(Subscription.id)).where(Subscription.activo.is_(True), Subscription.fecha_fin > now)
        active_subs = (await session.execute(sub_stmt)).scalar() or 0

    status_msg = (
        "📊 <b>Estado del Sistema de Rastreo</b>\n\n"
        f"🟢 <b>Productos Activos:</b> {total_active}\n"
        f"📦 <b>Total en Catálogo:</b> {total_products}\n"
        f"💎 <b>Suscriptores VIP Activos:</b> {active_subs}\n"
        f"⏱️ <b>Intervalo:</b> {settings.CHECK_INTERVAL_MINUTES} min\n"
        f"🛡️ <b>Anti-Spam:</b> {settings.ALERT_COOLDOWN_HOURS}h\n"
        f"📢 <b>Canal Destino:</b> <code>{settings.TELEGRAM_CHAT_ID or 'No configurado'}</code>"
    )
    await message.answer(status_msg, parse_mode=ParseMode.HTML)


# =====================================================================
# 1. SOLICITUDES DE UNIÓN AL CANAL (CHAT JOIN REQUESTS)
# =====================================================================

@dp.chat_join_request()
async def handle_join_request(event: types.ChatJoinRequest):
    """
    Se activa cuando un usuario hace clic en el enlace de invitación con aprobación requerida.
    Envía un mensaje privado al usuario con el valor de la membresía y los datos bancarios.
    """
    user = event.from_user
    chat_id = event.chat.id
    logger.info(f"Solicitud de acceso al canal {chat_id} recibida de {user.full_name} (@{user.username} - ID: {user.id})")

    welcome_text = (
        f"👋 ¡Hola <b>{user.first_name}</b>!\n\n"
        f"Recibimos tu solicitud para unirte al <b>Canal VIP de Ofertas y Errores de Precio</b> 🚀\n\n"
        f"💎 <b>Membresía Mensual:</b> ${settings.SUBSCRIPTION_PRICE_CLP:,.0f} CLP\n\n"
        f"{settings.BANK_TRANSFER_DETAILS}\n\n"
        f"<i>Apenas verifiquemos tu transferencia, el bot aprobará tu acceso al canal de inmediato.</i>"
    )
    try:
        await event.bot.send_message(
            chat_id=user.id,
            text=welcome_text,
            parse_mode=ParseMode.HTML,
        )
    except Exception as ex:
        logger.warning(f"No se pudo enviar DM a usuario {user.id} (puede requerir iniciar chat con el bot): {ex}")


# =====================================================================
# 2. RECEPCIÓN DE COMPROBANTES DE TRANSFERENCIA (FOTO O DOCUMENTO)
# =====================================================================

@dp.message(F.photo | F.document)
async def handle_payment_receipt(message: types.Message):
    """
    Recibe el comprobante enviado por el usuario por privado y lo reenvía al Administrador con botones de aprobación.
    """
    if message.chat.type != "private":
        return

    user = message.from_user
    logger.info(f"Comprobante recibido de {user.full_name} (@{user.username} - ID: {user.id})")

    if not settings.TELEGRAM_ADMIN_ID:
        await message.answer(
            "⚠️ Tu comprobante ha sido recibido, pero actualmente no hay un ID de Administrador configurado en el sistema.\n"
            "Por favor contacta al administrador del canal directamente."
        )
        return

    # Teclado inline de aprobación para el Administrador
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Aprobar Acceso (30 días)", callback_data=f"sub_app_{user.id}"),
                InlineKeyboardButton(text="❌ Rechazar", callback_data=f"sub_rej_{user.id}"),
            ]
        ]
    )

    caption = (
        f"📩 <b>NUEVO COMPROBANTE DE PAGO</b>\n\n"
        f"👤 <b>Usuario:</b> {user.full_name}\n"
        f"🏷️ <b>Username:</b> @{user.username or 'Sin alias'}\n"
        f"🆔 <b>Telegram ID:</b> <code>{user.id}</code>\n"
        f"💰 <b>Monto Membresía:</b> ${settings.SUBSCRIPTION_PRICE_CLP:,.0f} CLP\n"
        f"📅 <b>Fecha:</b> {datetime.datetime.now().strftime('%d/%m/%Y %H:%M')}"
    )

    try:
        file_id = message.photo[-1].file_id if message.photo else message.document.file_id
        if message.photo:
            await message.bot.send_photo(
                chat_id=settings.TELEGRAM_ADMIN_ID,
                photo=file_id,
                caption=caption,
                reply_markup=keyboard,
                parse_mode=ParseMode.HTML,
            )
        else:
            await message.bot.send_document(
                chat_id=settings.TELEGRAM_ADMIN_ID,
                document=file_id,
                caption=caption,
                reply_markup=keyboard,
                parse_mode=ParseMode.HTML,
            )

        await message.answer(
            "✅ <b>¡Comprobante recibido exitosamente!</b>\n\n"
            "El administrador validará la transferencia en breve.\n"
            "En cuanto sea aprobado, recibirás una notificación y tu acceso al canal VIP se activará automáticamente.",
            parse_mode=ParseMode.HTML,
        )
    except Exception as ex:
        logger.error(f"Error reenviando comprobante al admin {settings.TELEGRAM_ADMIN_ID}: {ex}")
        await message.answer("Ocurrió un error al enviar tu comprobante al administrador. Por favor intenta más tarde.")


# =====================================================================
# 3. ACCIONES DEL ADMINISTRADOR (APROBAR / RECHAZAR ACCESO)
# =====================================================================

@dp.callback_query(F.data.startswith("sub_app_") | F.data.startswith("sub_rej_"))
async def handle_admin_action(callback: types.CallbackQuery):
    """
    Procesa la decisión del administrador al pulsar Aprobar o Rechazar en el comprobante.
    """
    admin_id = callback.from_user.id
    if settings.TELEGRAM_ADMIN_ID and admin_id != settings.TELEGRAM_ADMIN_ID:
        await callback.answer("⛔ No tienes permisos de administrador.", show_alert=True)
        return

    is_approval = callback.data.startswith("sub_app_")
    user_id_str = callback.data.replace("sub_app_", "").replace("sub_rej_", "")
    user_id = int(user_id_str)

    bot = callback.bot
    channel_id = settings.TELEGRAM_CHAT_ID

    if is_approval:
        try:
            # 1. Aprobar solicitud de ingreso en Telegram
            if channel_id:
                try:
                    await bot.approve_chat_join_request(chat_id=channel_id, user_id=user_id)
                except Exception as e_tg:
                    logger.warning(f"No se pudo llamar approve_chat_join_request ({e_tg}). Puede estar ya dentro o haber expirado el enlace.")

            # 2. Registrar o extender en base de datos (30 días)
            now = datetime.datetime.now(datetime.timezone.utc)
            expiration = now + datetime.timedelta(days=30)

            async with get_db_session() as session:
                stmt = select(Subscription).where(Subscription.telegram_user_id == user_id)
                res = await session.execute(stmt)
                sub = res.scalar_one_or_none()

                if sub:
                    base_date = max(now, sub.fecha_fin) if (sub.activo and sub.fecha_fin) else now
                    sub.fecha_fin = base_date + datetime.timedelta(days=30)
                    sub.activo = True
                    sub.notificado_vencimiento = False
                    sub.actualizado_en = now
                else:
                    sub = Subscription(
                        telegram_user_id=user_id,
                        telegram_username=None,
                        telegram_full_name=None,
                        activo=True,
                        fecha_inicio=now,
                        fecha_fin=expiration,
                        monto_pagado=float(settings.SUBSCRIPTION_PRICE_CLP),
                        notificado_vencimiento=False,
                    )
                    session.add(sub)
                await session.commit()

            # 3. Notificar al usuario de su activación
            try:
                await bot.send_message(
                    chat_id=user_id,
                    text=(
                        "🎉 <b>¡Tu pago ha sido APROBADO!</b>\n\n"
                        "Bienvenido al <b>Canal VIP de Ofertas y Errores de Precio</b> 🚀\n"
                        f"📅 <b>Vencimiento de tu membresía:</b> {expiration.strftime('%d/%m/%Y')}\n\n"
                        "¡Disfruta de las mejores alertas y descuentos exclusivos!"
                    ),
                    parse_mode=ParseMode.HTML,
                )
            except Exception as ex_user:
                logger.warning(f"No se pudo notificar al usuario {user_id}: {ex_user}")

            # 4. Actualizar mensaje del admin
            await callback.message.edit_caption(
                caption=f"{callback.message.caption or ''}\n\n<b>✅ APROBADO por {callback.from_user.first_name}</b> el {datetime.datetime.now().strftime('%d/%m/%Y %H:%M')}",
                reply_markup=None,
                parse_mode=ParseMode.HTML,
            )
            await callback.answer("✅ Usuario aprobado y 30 días activados.", show_alert=True)

        except Exception as ex:
            logger.error(f"Error procesando aprobación de usuario {user_id}: {ex}")
            await callback.answer(f"Error: {ex}", show_alert=True)

    else:
        # Rechazo
        try:
            if channel_id:
                try:
                    await bot.decline_chat_join_request(chat_id=channel_id, user_id=user_id)
                except Exception:
                    pass

            try:
                await bot.send_message(
                    chat_id=user_id,
                    text=(
                        "❌ <b>Comprobante no verificado</b>\n\n"
                        "Tu comprobante de pago no pudo ser validado por el administrador.\n"
                        "Por favor revisa los datos de transferencia y vuelve a enviar el comprobante correcto si corresponde."
                    ),
                    parse_mode=ParseMode.HTML,
                )
            except Exception:
                pass

            await callback.message.edit_caption(
                caption=f"{callback.message.caption or ''}\n\n<b>❌ RECHAZADO por {callback.from_user.first_name}</b> el {datetime.datetime.now().strftime('%d/%m/%Y %H:%M')}",
                reply_markup=None,
                parse_mode=ParseMode.HTML,
            )
            await callback.answer("❌ Comprobante rechazado.", show_alert=True)
        except Exception as ex:
            logger.error(f"Error procesando rechazo: {ex}")
            await callback.answer(f"Error: {ex}", show_alert=True)


# =====================================================================
# 4. COMANDOS DE CONSULTA (/misuscripcion y /suscriptores)
# =====================================================================

@dp.message(Command("misuscripcion"))
async def cmd_my_subscription(message: types.Message):
    """Permite al usuario verificar el estado y vigencia de su suscripción VIP."""
    user_id = message.from_user.id
    async with get_db_session() as session:
        stmt = select(Subscription).where(Subscription.telegram_user_id == user_id)
        res = await session.execute(stmt)
        sub = res.scalar_one_or_none()

    now = datetime.datetime.now(datetime.timezone.utc)
    if not sub or not sub.activo or sub.fecha_fin <= now:
        await message.answer(
            "ℹ️ No tienes una suscripción VIP activa actualmente.\n"
            f"El valor mensual es de <b>${settings.SUBSCRIPTION_PRICE_CLP:,.0f} CLP</b>.\n\n"
            f"{settings.BANK_TRANSFER_DETAILS}",
            parse_mode=ParseMode.HTML,
        )
        return

    diff = sub.fecha_fin - now
    days_left = max(0, diff.days)

    await message.answer(
        "💎 <b>Tu Membresía VIP</b>\n\n"
        f"🟢 <b>Estado:</b> Activa\n"
        f"📅 <b>Fecha de Vencimiento:</b> {sub.fecha_fin.strftime('%d/%m/%Y')}\n"
        f"⏳ <b>Días Restantes:</b> {days_left} día(s)\n\n"
        "¡Gracias por ser parte de nuestra comunidad exclusiva!",
        parse_mode=ParseMode.HTML,
    )


@dp.message(Command("suscriptores"))
async def cmd_subscribers(message: types.Message):
    """Muestra estadísticas y lista de suscriptores (Solo Administrador)."""
    user_id = message.from_user.id
    if settings.TELEGRAM_ADMIN_ID and user_id != settings.TELEGRAM_ADMIN_ID:
        await message.answer("⛔ Comando exclusivo para el administrador del sistema.")
        return

    async with get_db_session() as session:
        now = datetime.datetime.now(datetime.timezone.utc)
        stmt_active = select(Subscription).where(Subscription.activo.is_(True), Subscription.fecha_fin > now)
        res_active = await session.execute(stmt_active)
        active_subs = res_active.scalars().all()

        total_stmt = select(func.count(Subscription.id))
        total_subs = (await session.execute(total_stmt)).scalar() or 0

    income = len(active_subs) * settings.SUBSCRIPTION_PRICE_CLP
    text = (
        "📊 <b>Panel de Suscriptores VIP</b>\n\n"
        f"🟢 <b>Miembros Activos Vigentes:</b> {len(active_subs)}\n"
        f"📦 <b>Total Histórico de Suscripciones:</b> {total_subs}\n"
        f"💵 <b>Ingreso Mensual Estimado:</b> ${income:,.0f} CLP\n\n"
    )
    if active_subs:
        text += "<b>Próximos vencimientos:</b>\n"
        sorted_subs = sorted(active_subs, key=lambda s: s.fecha_fin)[:5]
        for s in sorted_subs:
            days = max(0, (s.fecha_fin - now).days)
            identifier = f"@{s.telegram_username}" if s.telegram_username else str(s.telegram_user_id)
            text += f"• <code>{identifier}</code>: {s.fecha_fin.strftime('%d/%m/%Y')} ({days}d restantes)\n"

    await message.answer(text, parse_mode=ParseMode.HTML)


async def start_bot_polling(bot: Bot) -> None:
    """Inicia el consumo de comandos y eventos de Telegram en segundo plano."""
    try:
        logger.info("Iniciando polling de comandos y solicitudes de Telegram...")
        await dp.start_polling(bot, allowed_updates=["message", "callback_query", "chat_join_request"])
    except Exception as ex:
        logger.error(f"Error en polling de Telegram: {ex}")

