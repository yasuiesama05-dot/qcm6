# نشر البوت على PythonAnywhere (مجاني، بلا بطاقة بنكية)

## 0. قبل البدء
- جدّد **BOT_TOKEN** عبر @BotFather (`/mybots` → بوتك → API Token → Revoke current token).
- جدّد **GEMINI_API_KEY** عبر https://aistudio.google.com/apikey (احذف القديم، أنشئ جديدًا).
- لا تكتب أي منهما داخل أي ملف من ملفات المشروع — سنضعهما فقط في PythonAnywhere مباشرة.

## 1. إنشاء الحساب
1. اذهب إلى https://www.pythonanywhere.com/registration/register/beginner/ وأنشئ حساب Beginner (مجاني، بدون بطاقة).
2. أكّد بريدك الإلكتروني.

## 2. رفع المشروع
1. من تبويب **Files**، ارفع ملف `qcm_project_2.zip` (النسخة المحدّثة التي سترسلها لك بعد هذا الشرح) إلى المجلد الرئيسي لحسابك.
2. افتح **Bash console** جديدة (من تبويب Consoles → Bash) ونفّذ:
   ```bash
   unzip qcm_project_2.zip -d qcm_project
   cd qcm_project
   ls
   ```
   تأكد أنك ترى: `bot.py`، `qcm_storage.py`، `set_webhook.py`، `wsgi_pythonanywhere.py`، ومجلد `data/`.

## 3. تثبيت المكتبات
في نفس الـ Bash console:
```bash
pip3 install --user "python-telegram-bot[job-queue]" httpx flask
```

## 4. إنشاء تطبيق الويب (Web app)
1. من تبويب **Web** → **Add a new web app**.
2. اختر النطاق المجاني (`ton_nom.pythonanywhere.com`).
3. اختر **Manual configuration** (وليس Flask المُعد مسبقًا) → اختر نفس نسخة Python المثبّتة.
4. بعد الإنشاء، ستظهر لك صفحة الإعدادات وفيها رابط لملف **WSGI configuration file** — افتحه للتعديل.

## 5. تعديل ملف WSGI
احذف محتوى الملف بالكامل واستبدله بهذا (بدّل `ton_nom` و`XXXX...` بقيمك الحقيقية):

```python
import sys
import os

# --- الأسرار: توضع هنا فقط، ولا تُرفع أبدًا إلى أي ملف آخر ---
os.environ["BOT_TOKEN"] = "ضع_هنا_التوكن_الجديد"
os.environ["GEMINI_API_KEY"] = "ضع_هنا_مفتاح_Gemini_الجديد"

# --- مسار المشروع ---
project_path = "/home/ton_nom/qcm_project"
if project_path not in sys.path:
    sys.path.insert(0, project_path)

from wsgi_pythonanywhere import application
```

احفظ الملف.

## 6. ربط مجلد المشروع بالإعدادات
في صفحة **Web** نفسها، تحت "Code":
- **Source code**: `/home/ton_nom/qcm_project`
- **Working directory**: `/home/ton_nom/qcm_project`

اضغط الزر الأخضر **Reload** أعلى الصفحة.

## 7. تفعيل الـ Webhook (مرة واحدة فقط)
في الـ Bash console:
```bash
cd qcm_project
export BOT_TOKEN="نفس_التوكن_الذي_وضعته_في_WSGI"
export PA_USERNAME="ton_nom"
python3 set_webhook.py
```
يجب أن تشاهد رسالة نجاح من تلجرام (`"ok":true`).

## 8. الاختبار
افتح بوتك في تلجرام وأرسل `/start`. إن لم يردّ:
- افحص تبويب **Web** → **Log files** → **Error log** لمعرفة الخطأ بالضبط.
- تأكد أن رابط WSGI مطابق تمامًا للمسار (`/home/ton_nom/qcm_project`).

## 9. تجديد التطبيق شهريًا (إلزامي على الحساب المجاني)
تطبيقات الويب المجانية على PythonAnywhere تنتهي صلاحيتها بعد شهر إن لم تُجدَّد.
كل شهر تقريبًا، سجّل الدخول واذهب لتبويب **Web** واضغط زر **Run until 3 months from today**
(أو الزر المكافئ الظاهر). بدون بطاقة، بدون دفع — فقط ضغطة زر.
