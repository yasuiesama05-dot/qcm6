# نشر البوت على Render (خطوة بخطوة)

هذا الدليل يشرح كيف تشغّل البوت على **Render** (خطة مجانية) بنظام **Webhook**.
ملفات النشر جاهزة في المشروع: `render.yaml`، `requirements.txt`، `runtime.txt`،
`render_app.py`.

---

## 0. قبل البدء (مهم)

- **جدّد التوكن** من @BotFather إن كان قد تسرّب في أي مكان:
  `/mybots` → بوتك → **API Token** → **Revoke current token**.
- **لا تكتب التوكن في أي ملف داخل المشروع.** يوضع فقط في
  Render → Environment.
- البوت يجب أن يكون **مشرفًا (Admin)** في القناة `@qcMAX_batna`، وإلا لن
  يستطيع التحقق من اشتراك المستخدمين.

---

## 1. رفع الكود إلى GitHub

المشروع ليس مستودع Git بعد. من مجلد المشروع:

```bash
git init
git add .
git commit -m "Prepare bot for Render deployment"
```

أنشئ مستودعًا **فارغًا** (بدون README) على GitHub باسم مثل `qcm-bot`، ثم:

```bash
git branch -M main
git remote add origin https://github.com/<USERNAME>/qcm-bot.git
git push -u origin main
```

> تحقّق قبل الرفع أن `bot_users.json` و`reported_questions.json` غير موجودين
> في الـ commit (موجودان في `.gitignore`).

---

## 2. إنشاء الخدمة على Render

### الطريقة الأسهل: Blueprint (يقرأ `render.yaml` تلقائيًا)

1. سجّل في https://render.com (يمكن الدخول بحساب GitHub).
2. **New +** → **Blueprint**.
3. اختر المستودع `qcm-bot` → **Connect**.
4. سيعرض Render الخدمة `qcm-bot` وكل المتغيرات. اضغط **Apply**.

### أو يدويًا: New + → Web Service

| الإعداد | القيمة |
|---|---|
| Repository | `qcm-bot` |
| Runtime | Python 3 |
| Build Command | `pip install -r requirements.txt` |
| Start Command | `python render_app.py` |
| Plan | Free |
| Health Check Path | اتركه فارغًا |

---

## 3. المتغيرات (Environment)

من صفحة الخدمة → **Environment** → أضف:

| المتغير | القيمة |
|---|---|
| `BOT_TOKEN` | التوكن الجديد من @BotFather (**إلزامي**) |
| `REQUIRED_CHANNEL` | `@qcMAX_batna` (أو اتركه فارغًا لتعطيل شرط الاشتراك) |
| `ADMIN_USER_IDS` | `5696844039` (عدة أرقام مفصولة بفواصل) |
| `WEBHOOK_SECRET` | يُولّده Render تلقائيًا (لا تلمسه) |

`PORT` و`RENDER_EXTERNAL_URL` يضيفهما Render تلقائيًا.

اضغط **Save** → سيُعاد النشر تلقائيًا.

---

## 4. التحقق

1. افتح رابط الخدمة `https://qcm-bot-xxxx.onrender.com` — يجب أن ترى:
   `✅ Le bot QCM est en ligne (mode webhook).`
2. اذهب إلى البوت في تلجرام وأرسل `/start`.
3. في سجلّات Render (**Logs**) يجب أن ترى:
   ```
   Webhook Telegram configuré : https://.../webhook/...
   ```
4. للتأكد من ربط الويب هوك، افتح في المتصفح:
   `https://api.telegram.org/bot<TOKEN>/getWebhookInfo`
   يجب أن يظهر `"url"` الخاص بخدمتك و`"pending_update_count": 0`.

> **مهم:** لا تشغّل البوت بنظام polling (محليًا أو على استضافة أخرى) بنفس
> التوكن في نفس الوقت — سيسبب تعارضًا مع الويب هوك.

---

## 5. منع النوم (Keep-alive)

الخدمة المجانية على Render تنام بعد **15 دقيقة** بلا زيارات، وتستغرق أول
طلبة ~30–60 ثانية للاستيقاظ (قد تتأخر رسائل تلجرام). الحل المجاني:

1. اذهب إلى https://cron-job.org (أو UptimeRobot) وأنشئ حسابًا.
2. أضف مهمة URL = `https://qcm-bot-xxxx.onrender.com/healthz` كل **10 دقائق**.

الخطة المجانية تمنح **750 ساعة/شهر** لكل حساب، وهي كافية لخدمة واحدة تعمل
24/7 (الشهر ≈ 744 ساعة).

---

## 6. ملاحظة عن حفظ البيانات

نظام ملفات Render **مؤقت**: الملفات `bot_users.json` (قائمة المستخدمين)
و`reported_questions.json` (الإبلاغات) تُفقد عند كل إعادة نشر/إعادة تشغيل.

- قائمة الأسئلة نفسها في `data/*.json` محفوظة في Git، فلا مشكلة.
- إن أردت حفظ الإبلاغات والمستخدمين بشكل دائم: أضف **Disk** من صفحة الخدمة
  (غير مجاني)، واضبط متغير البيئة `DATA_DIR` على مسار القرص — أو استعمل
  خدمة قاعدة بيانات خارجية لاحقًا.

---

## 7. التحديثات المستقبلية

كل `git push` على فرع `main` يُعيد النشر تلقائيًا (`autoDeploy: true`).
لتعديل الأسئلة: عدّل `data/*.json` ثم:

```bash
git add data
git commit -m "Update questions"
git push
```

---

## 8. استكشاف الأخطاء

| المشكلة | الحل |
|---|---|
| `BOT_TOKEN manquant` | أضف المتغير في Environment وأعد النشر |
| البوت لا يردّ | افحص **Logs**؛ تحقق من `getWebhookInfo` |
| `409 Conflict` | البوت يعمل بمكان آخر (polling) بنفس التوكن — أوقفه |
| لا يتحقق من الاشتراك | البوت ليس مشرفًا في القناة، أو `REQUIRED_CHANNEL` خاطئ |
| `Unauthorized` | التوكن قديم/تم إبطاله — جدّده من @BotFather |
