#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TorBox Stremio Bot - النسخة المحدّثة
بوت تيليجرام لأتمتة إنشاء حساب TorBox مجاني وربطه بإضافات Stremio المتعددة
"""

import logging
import asyncio
import requests
import random
import string
import time
import re
import json

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)
from telegram.request import HTTPXRequest
from telegram.constants import ParseMode

# ─── إعدادات ───────────────────────────────────────────────────────────────
BOT_TOKEN = "8839817636:AAHH967XRo6SfEVEor4Yofcjp1_FQ_8jj_M"

SUPABASE_URL = "https://db.torbox.app"
SUPABASE_ANON_KEY = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImJlanhmeXRrbm5rb2VndHRldXpzIiwicm9sZSI6ImFub24iLCJpYXQiOjE3MjkxMjgzMzAsImV4cCI6MjA0NDcwNDMzMH0."
    "vIQWcZuN6Nx3DnkmsWLK25J8BM3TTA_8Tb4GoK99MqM"
)

# ─── Logging ────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# الخطوة 1: إنشاء إيميل مؤقت عبر mail.tm
# ══════════════════════════════════════════════════════════════════════════════
def create_temp_email() -> tuple:
    """ينشئ حساب إيميل مؤقت ويعيد (email, password, token)."""
    r = requests.get("https://api.mail.tm/domains", timeout=15)
    r.raise_for_status()
    domain = r.json()["hydra:member"][0]["domain"]

    username = "".join(random.choices(string.ascii_lowercase + string.digits, k=12))
    email = f"{username}@{domain}"
    password = "".join(random.choices(string.ascii_letters + string.digits, k=18))

    r2 = requests.post(
        "https://api.mail.tm/accounts",
        json={"address": email, "password": password},
        timeout=15,
    )
    if r2.status_code not in (200, 201):
        raise RuntimeError(f"فشل إنشاء الإيميل المؤقت: {r2.text[:200]}")

    r3 = requests.post(
        "https://api.mail.tm/token",
        json={"address": email, "password": password},
        timeout=15,
    )
    r3.raise_for_status()
    token = r3.json()["token"]

    logger.info(f"✅ إيميل مؤقت: {email}")
    return email, password, token


# ══════════════════════════════════════════════════════════════════════════════
# الخطوة 2: التسجيل في TorBox عبر Supabase
# ══════════════════════════════════════════════════════════════════════════════
def register_torbox(email: str, password: str) -> dict:
    """يسجّل حساباً جديداً في TorBox."""
    headers = {
        "apikey": SUPABASE_ANON_KEY,
        "Content-Type": "application/json",
    }
    r = requests.post(
        f"{SUPABASE_URL}/auth/v1/signup",
        headers=headers,
        json={"email": email, "password": password},
        timeout=15,
    )
    if r.status_code != 200:
        raise RuntimeError(f"فشل التسجيل في TorBox: {r.text[:200]}")

    data = r.json()
    if not data.get("id"):
        raise RuntimeError(f"استجابة تسجيل غير متوقعة: {data}")

    logger.info(f"✅ تم التسجيل في TorBox: {email}")
    return data


# ══════════════════════════════════════════════════════════════════════════════
# الخطوة 3: انتظار رسالة التأكيد
# ══════════════════════════════════════════════════════════════════════════════
def wait_for_confirmation(mail_token: str, max_wait: int = 90) -> str:
    """ينتظر رسالة التأكيد ويعيد رمز التحقق."""
    mail_headers = {"Authorization": f"Bearer {mail_token}"}

    for attempt in range(max_wait // 5):
        time.sleep(5)
        r = requests.get(
            "https://api.mail.tm/messages",
            headers=mail_headers,
            timeout=15,
        )
        messages = r.json().get("hydra:member", [])

        if messages:
            msg_id = messages[0]["id"]
            r2 = requests.get(
                f"https://api.mail.tm/messages/{msg_id}",
                headers=mail_headers,
                timeout=15,
            )
            full_msg = r2.json()
            text_content = full_msg.get("text", "")

            pattern = r"https://[^\s\]>\"']+verify%3Ftoken[^\s\]>\"']+"
            matches = re.findall(pattern, text_content)

            if matches:
                token_match = re.search(r"token=([a-f0-9]+)", matches[0])
                if token_match:
                    logger.info("✅ تم استخراج رمز التأكيد")
                    return token_match.group(1)

            direct_pattern = r"https://db\.torbox\.app/auth/v1/verify\?token=([a-f0-9]+)"
            direct_matches = re.findall(direct_pattern, text_content)
            if direct_matches:
                logger.info("✅ رمز التأكيد المباشر")
                return direct_matches[0]

        logger.info(f"⏳ انتظار رسالة التأكيد... ({(attempt + 1) * 5}s)")

    raise RuntimeError("انتهت مهلة انتظار رسالة التأكيد (90 ثانية)")


# ══════════════════════════════════════════════════════════════════════════════
# الخطوة 4: تأكيد الحساب والحصول على API Key
# ══════════════════════════════════════════════════════════════════════════════
def confirm_account(verify_token: str) -> str:
    """يؤكد الحساب ويعيد access_token (= API Key)."""
    verify_url = (
        f"{SUPABASE_URL}/auth/v1/verify"
        f"?token={verify_token}&type=signup&redirect_to=https://torbox.app"
    )
    r = requests.get(verify_url, allow_redirects=False, timeout=15)

    if r.status_code not in (302, 303, 307):
        raise RuntimeError(f"فشل تأكيد الحساب: HTTP {r.status_code}")

    redirect_url = r.headers.get("Location", "")
    if "#" not in redirect_url:
        raise RuntimeError("لم يتم العثور على access_token في الـ redirect")

    fragment = redirect_url.split("#")[1]
    params = {}
    for part in fragment.split("&"):
        if "=" in part:
            k, v = part.split("=", 1)
            params[k] = v

    access_token = params.get("access_token")
    if not access_token:
        raise RuntimeError("لم يتم العثور على access_token")

    logger.info("✅ تم تأكيد الحساب وجلب API Key")
    return access_token


# ══════════════════════════════════════════════════════════════════════════════
# بناء رابط Torrentio
# ══════════════════════════════════════════════════════════════════════════════
def build_torrentio_url(api_key: str) -> str:
    """يبني رابط Torrentio مع TorBox API Key."""
    options = (
        f"torbox={api_key}"
        "|qualityfilter=cam,scr,screener,r5,dvdscr"
        "|sort=qualitysize"
        "|limit=1"
    )
    return f"https://torrentio.strem.fun/{options}/manifest.json"


# ══════════════════════════════════════════════════════════════════════════════
# التحقق من صحة الرابط
# ══════════════════════════════════════════════════════════════════════════════
def verify_addon_link(manifest_url: str) -> bool:
    """يتحقق من أن رابط الإضافة يعمل."""
    try:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Linux; Android 10; K) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/114.0.0.0 Mobile Safari/537.36"
            ),
        }
        r = requests.get(manifest_url, headers=headers, timeout=20)
        if r.status_code == 200:
            data = r.json()
            return "id" in data and "name" in data
    except Exception as e:
        logger.warning(f"فشل التحقق من الرابط: {e}")
    return False


# ══════════════════════════════════════════════════════════════════════════════
# الدالة الرئيسية للأتمتة الكاملة
# ══════════════════════════════════════════════════════════════════════════════
async def run_full_automation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تنفّذ العملية الكاملة وترسل النتيجة."""
    chat_id = update.effective_chat.id

    status_msg = await context.bot.send_message(
        chat_id=chat_id,
        text=(
            "⚙️ *جاري تنفيذ العملية تلقائياً...*\n\n"
            "🔄 الخطوة 1/5: إنشاء بريد إلكتروني مؤقت..."
        ),
        parse_mode=ParseMode.MARKDOWN,
    )

    try:
        # ── الخطوة 1: إيميل مؤقت ──────────────────────────────────────────
        email, mail_pass, mail_token = create_temp_email()

        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=status_msg.message_id,
            text=(
                "⚙️ *جاري تنفيذ العملية تلقائياً...*\n\n"
                f"✅ الخطوة 1/5: تم إنشاء البريد المؤقت\n"
                f"📧 `{email}`\n\n"
                "🔄 الخطوة 2/5: إنشاء حساب TorBox..."
            ),
            parse_mode=ParseMode.MARKDOWN,
        )

        # ── الخطوة 2: التسجيل في TorBox ───────────────────────────────────
        torbox_pass = (
            "".join(random.choices(string.ascii_letters + string.digits, k=16))
            + "!A1"
        )
        register_torbox(email, torbox_pass)

        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=status_msg.message_id,
            text=(
                "⚙️ *جاري تنفيذ العملية تلقائياً...*\n\n"
                f"✅ الخطوة 1/5: تم إنشاء البريد المؤقت\n"
                f"✅ الخطوة 2/5: تم إنشاء حساب TorBox\n\n"
                "🔄 الخطوة 3/5: انتظار رسالة التأكيد... ⏳"
            ),
            parse_mode=ParseMode.MARKDOWN,
        )

        # ── الخطوة 3: انتظار التأكيد ──────────────────────────────────────
        verify_token = wait_for_confirmation(mail_token, max_wait=90)

        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=status_msg.message_id,
            text=(
                "⚙️ *جاري تنفيذ العملية تلقائياً...*\n\n"
                f"✅ الخطوة 1/5: تم إنشاء البريد المؤقت\n"
                f"✅ الخطوة 2/5: تم إنشاء حساب TorBox\n"
                f"✅ الخطوة 3/5: تم استلام رسالة التأكيد\n\n"
                "🔄 الخطوة 4/5: تفعيل الحساب وجلب API Key..."
            ),
            parse_mode=ParseMode.MARKDOWN,
        )

        # ── الخطوة 4: تأكيد الحساب وجلب API Key ──────────────────────────
        api_key = confirm_account(verify_token)

        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=status_msg.message_id,
            text=(
                "⚙️ *جاري تنفيذ العملية تلقائياً...*\n\n"
                f"✅ الخطوة 1/5: تم إنشاء البريد المؤقت\n"
                f"✅ الخطوة 2/5: تم إنشاء حساب TorBox\n"
                f"✅ الخطوة 3/5: تم استلام رسالة التأكيد\n"
                f"✅ الخطوة 4/5: تم جلب API Key\n\n"
                "🔄 الخطوة 5/5: بناء رابط Stremio والتحقق منه..."
            ),
            parse_mode=ParseMode.MARKDOWN,
        )

        # ── الخطوة 5: بناء رابط Torrentio والتحقق ─────────────────────────
        torrentio_url = build_torrentio_url(api_key)
        is_valid = verify_addon_link(torrentio_url)

        # ── تحديث رسالة الحالة النهائية ────────────────────────────────────
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=status_msg.message_id,
            text=(
                "✅ *اكتملت العملية بنجاح!*\n\n"
                "✅ الخطوة 1/5: تم إنشاء البريد المؤقت\n"
                "✅ الخطوة 2/5: تم إنشاء حساب TorBox\n"
                "✅ الخطوة 3/5: تم استلام رسالة التأكيد\n"
                "✅ الخطوة 4/5: تم جلب API Key\n"
                f"✅ الخطوة 5/5: تم بناء رابط Stremio"
                + (" ✔️" if is_valid else " ⚠️")
            ),
            parse_mode=ParseMode.MARKDOWN,
        )

        # ══════════════════════════════════════════════════════════════════
        # رسالة 1: رابط Torrentio الجاهز
        # ══════════════════════════════════════════════════════════════════
        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                "🎬 *رابط Torrentio جاهز للاستخدام:*\n\n"
                "انسخ الرابط أدناه وأضفه في Stremio ← Addons ← Search Addons"
            ),
            parse_mode=ParseMode.MARKDOWN,
        )

        await context.bot.send_message(
            chat_id=chat_id,
            text=f"`{torrentio_url}`",
            parse_mode=ParseMode.MARKDOWN,
        )

        # ══════════════════════════════════════════════════════════════════
        # رسالة 2: API Key وحده في رسالة منفصلة
        # ══════════════════════════════════════════════════════════════════
        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                "🔑 *مفتاح TorBox API Key:*\n\n"
                "انسخ هذا المفتاح إذا أردت استخدامه في إضافة أخرى"
            ),
            parse_mode=ParseMode.MARKDOWN,
        )

        await context.bot.send_message(
            chat_id=chat_id,
            text=f"`{api_key}`",
            parse_mode=ParseMode.MARKDOWN,
        )

        # ══════════════════════════════════════════════════════════════════
        # رسالة 3: التنبيه + أزرار الإضافات الأخرى
        # ══════════════════════════════════════════════════════════════════
        keyboard = [
            # صف 1: الإضافات العادية
            [
                InlineKeyboardButton("🎬 Torrentio (مُفعَّل)", callback_data="info_torrentio"),
                InlineKeyboardButton("🌊 MediaFusion", callback_data="addon_mediafusion"),
            ],
            # صف 2: إضافات +18
            [
                InlineKeyboardButton("🔞 إضافات +18", callback_data="menu_adult"),
            ],
            # صف 3: تجديد
            [
                InlineKeyboardButton("🔄 تجديد رابط جديد", callback_data="renew"),
            ],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                "━━━━━━━━━━━━━━━━━━━━━━\n"
                "⚠️ *تنبيه مهم:*\n"
                "الرابط والمفتاح صالحان لمدة *24 ساعة فقط* من الآن.\n"
                "بعد انتهاء المدة، اضغط /start للحصول على رابط جديد تلقائياً.\n\n"
                "━━━━━━━━━━━━━━━━━━━━━━\n"
                "📌 *هل تريد استخدام المفتاح في إضافة أخرى؟*\n"
                "اختر الإضافة المطلوبة:"
            ),
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=reply_markup,
        )

        # حفظ الـ API Key في context لاستخدامه لاحقاً عند الضغط على الأزرار
        context.user_data["api_key"] = api_key
        context.user_data["torrentio_url"] = torrentio_url

        logger.info(f"✅ تم إرسال الرابط للمستخدم {chat_id}")

    except Exception as e:
        error_msg = str(e)
        logger.error(f"❌ خطأ: {error_msg}")

        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=status_msg.message_id,
            text=(
                "❌ *حدث خطأ أثناء العملية*\n\n"
                f"التفاصيل: `{error_msg[:200]}`\n\n"
                "🔄 اضغط /start للمحاولة مجدداً."
            ),
            parse_mode=ParseMode.MARKDOWN,
        )


# ══════════════════════════════════════════════════════════════════════════════
# معالج الأزرار
# ══════════════════════════════════════════════════════════════════════════════
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالج جميع أزرار الـ inline keyboard."""
    query = update.callback_query
    await query.answer()
    chat_id = update.effective_chat.id
    api_key = context.user_data.get("api_key", "")

    # ── تجديد رابط جديد ───────────────────────────────────────────────────
    if query.data == "renew":
        await run_full_automation(update, context)

    # ── معلومات Torrentio ──────────────────────────────────────────────────
    elif query.data == "info_torrentio":
        torrentio_url = context.user_data.get("torrentio_url", "")
        if torrentio_url:
            await context.bot.send_message(
                chat_id=chat_id,
                text=(
                    "🎬 *Torrentio - رابطك الحالي:*\n\n"
                    "الرابط مُفعَّل بالفعل وجاهز للاستخدام في Stremio."
                ),
                parse_mode=ParseMode.MARKDOWN,
            )
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"`{torrentio_url}`",
                parse_mode=ParseMode.MARKDOWN,
            )
        else:
            await context.bot.send_message(
                chat_id=chat_id,
                text="⚠️ لا يوجد رابط محفوظ. اضغط /start لإنشاء رابط جديد.",
                parse_mode=ParseMode.MARKDOWN,
            )

    # ── MediaFusion ────────────────────────────────────────────────────────
    elif query.data == "addon_mediafusion":
        if not api_key:
            await context.bot.send_message(
                chat_id=chat_id,
                text="⚠️ لا يوجد API Key محفوظ. اضغط /start أولاً.",
            )
            return

        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                "🌊 *MediaFusion | ElfHosted*\n\n"
                "لإضافة TorBox إلى MediaFusion:\n\n"
                "1️⃣ افتح رابط الإعداد:\n"
                "https://mediafusion.elfhosted.com/configure\n\n"
                "2️⃣ ابحث عن قسم *Debrid Providers*\n\n"
                "3️⃣ في خانة *TorBox API Key* الصق المفتاح التالي:"
            ),
            parse_mode=ParseMode.MARKDOWN,
            disable_web_page_preview=True,
        )
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"`{api_key}`",
            parse_mode=ParseMode.MARKDOWN,
        )
        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                "4️⃣ اضغط *Install* وانسخ الرابط الناتج إلى Stremio.\n\n"
                "⚠️ المفتاح صالح *24 ساعة فقط*."
            ),
            parse_mode=ParseMode.MARKDOWN,
        )

    # ── قائمة إضافات +18 ──────────────────────────────────────────────────
    elif query.data == "menu_adult":
        adult_keyboard = [
            [
                InlineKeyboardButton("🔥 TPB 4K Porn", callback_data="addon_tpb"),
                InlineKeyboardButton("🎭 Porn Tube", callback_data="addon_porntube"),
            ],
            [
                InlineKeyboardButton("🔙 رجوع", callback_data="back_main"),
            ],
        ]
        adult_markup = InlineKeyboardMarkup(adult_keyboard)

        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                "🔞 *إضافات المحتوى للبالغين (+18)*\n\n"
                "اختر الإضافة التي تريد ربطها بمفتاح TorBox:"
            ),
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=adult_markup,
        )

    # ── TPB 4K Porn ────────────────────────────────────────────────────────
    elif query.data == "addon_tpb":
        if not api_key:
            await context.bot.send_message(
                chat_id=chat_id,
                text="⚠️ لا يوجد API Key محفوظ. اضغط /start أولاً.",
            )
            return

        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                "🔥 *TPB 4K Porn*\n\n"
                "لإضافة TorBox إلى هذه الإضافة:\n\n"
                "1️⃣ افتح رابط الإعداد:\n"
                "https://stremio-tpb-porn.sliplane.app/configure\n\n"
                "2️⃣ ابحث عن قسم *Debrid Providers*\n\n"
                "3️⃣ في خانة *TorBox API key* الصق المفتاح التالي:"
            ),
            parse_mode=ParseMode.MARKDOWN,
            disable_web_page_preview=True,
        )
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"`{api_key}`",
            parse_mode=ParseMode.MARKDOWN,
        )
        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                "4️⃣ اضغط *Install* في أسفل الصفحة\n"
                "5️⃣ انسخ الرابط الناتج وأضفه في Stremio\n\n"
                "⚠️ المفتاح صالح *24 ساعة فقط*."
            ),
            parse_mode=ParseMode.MARKDOWN,
        )

    # ── Porn Tube ──────────────────────────────────────────────────────────
    elif query.data == "addon_porntube":
        if not api_key:
            await context.bot.send_message(
                chat_id=chat_id,
                text="⚠️ لا يوجد API Key محفوظ. اضغط /start أولاً.",
            )
            return

        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                "🎭 *Porn Tube*\n\n"
                "لإضافة TorBox إلى هذه الإضافة:\n\n"
                "1️⃣ افتح رابط الإعداد:\n"
                "https://ptube.ers.pw/\n\n"
                "2️⃣ في خانة كلمة المرور الأولى الصق المفتاح التالي\n"
                "_(هذه الخانة هي TorBox API Key)_:"
            ),
            parse_mode=ParseMode.MARKDOWN,
            disable_web_page_preview=True,
        )
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"`{api_key}`",
            parse_mode=ParseMode.MARKDOWN,
        )
        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                "3️⃣ اضغط *Install Porn Tube Addon*\n"
                "4️⃣ انسخ الرابط الناتج وأضفه في Stremio\n\n"
                "⚠️ المفتاح صالح *24 ساعة فقط*."
            ),
            parse_mode=ParseMode.MARKDOWN,
        )

    # ── رجوع للقائمة الرئيسية ──────────────────────────────────────────────
    elif query.data == "back_main":
        keyboard = [
            [
                InlineKeyboardButton("🎬 Torrentio (مُفعَّل)", callback_data="info_torrentio"),
                InlineKeyboardButton("🌊 MediaFusion", callback_data="addon_mediafusion"),
            ],
            [
                InlineKeyboardButton("🔞 إضافات +18", callback_data="menu_adult"),
            ],
            [
                InlineKeyboardButton("🔄 تجديد رابط جديد", callback_data="renew"),
            ],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                "📌 *اختر الإضافة المطلوبة:*"
            ),
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=reply_markup,
        )


# ══════════════════════════════════════════════════════════════════════════════
# معالجات أوامر البوت
# ══════════════════════════════════════════════════════════════════════════════
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالج أمر /start - يبدأ العملية الكاملة فوراً."""
    await run_full_automation(update, context)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالج أمر /help."""
    help_text = (
        "🤖 *بوت TorBox Stremio*\n\n"
        "هذا البوت يقوم تلقائياً بـ:\n"
        "• إنشاء بريد إلكتروني مؤقت\n"
        "• إنشاء حساب TorBox مجاني جديد\n"
        "• تفعيل الحساب وجلب API Key\n"
        "• بناء رابط Torrentio جاهز للاستخدام\n"
        "• إرسال API Key لاستخدامه في إضافات أخرى\n\n"
        "⚠️ *ملاحظة:* الرابط والمفتاح صالحان لمدة 24 ساعة فقط.\n\n"
        "📌 *الأوامر المتاحة:*\n"
        "/start - بدء العملية وإنشاء رابط جديد\n"
        "/help - عرض هذه المساعدة\n\n"
        "📌 *الإضافات المدعومة:*\n"
        "🎬 Torrentio - رابط جاهز تلقائياً\n"
        "🌊 MediaFusion | ElfHosted\n"
        "🔥 TPB 4K Porn (+18)\n"
        "🎭 Porn Tube (+18)"
    )
    await update.message.reply_text(help_text, parse_mode=ParseMode.MARKDOWN)


# ══════════════════════════════════════════════════════════════════════════════
# نقطة الدخول الرئيسية
# ══════════════════════════════════════════════════════════════════════════════
def main():
    """تشغيل البوت."""
    logger.info("🚀 بدء تشغيل TorBox Stremio Bot (v2.0)...")

    request = HTTPXRequest(
        connection_pool_size=8,
        connect_timeout=30.0,
        read_timeout=60.0,
        write_timeout=60.0,
        pool_timeout=30.0,
    )
    app = Application.builder().token(BOT_TOKEN).request(request).build()

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CallbackQueryHandler(button_callback))

    logger.info("✅ البوت يعمل الآن. اضغط Ctrl+C للإيقاف.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
