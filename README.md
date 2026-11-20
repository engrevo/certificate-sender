# مُرسِل شهادات الحضور · Certificate Sender

موقع (Flask) يخلي المنظّم يرفع **أي تصميم شهادة**، ويحدد **تلقائيًا** مكان كتابة الاسم، ثم يولّد شهادة PDF لكل شخص في القائمة ويرسلها له على **واتساب**.

> Upload any certificate design → the app auto-detects where the name goes → generates a PDF per attendee → sends each one over the WhatsApp Cloud API.

## المميزات

- **كشف تلقائي لمكان الاسم** بأربع طرق مرتبة حسب الدقة:
  1. **كلمة دالّة** يكتبها المصمم في مكان الاسم: `{الاسم}` أو `[اسم المشارك]` أو `{{name}}` أو `<<name>>` أو `NAME`. يُقرأ بالـ OCR، ويُمسح من القالب ويُكتب الاسم مكانه بنفس اللون تقريبًا.
  2. **خط فاضي** في وسط الشهادة (متصل أو منقّط أو متقطع)، ويُكتب الاسم فوقه. خطوط التوقيع الجانبية والإطارات تُتجاهل.
  3. **أوسع مساحة فاضية** في وسط النص.
  4. **منتصف الصفحة**، إذا ما نجحت أي طريقة من اللي قبل.
- **تعديل يدوي**: مربع تقدر تسحبه وتغيّر حجمه فوق الشهادة، مع معاينة مباشرة.
- **دعم كامل للعربي**: تشكيل الحروف واتجاهها صح، والخط يصغر تلقائيًا للأسماء الطويلة. فيه خطان: Amiri وTajawal.
- **القائمة** تنقرأ من Excel أو CSV أو نص ملصوق، وأسماء الأعمدة تُكتشف بالعربي أو الإنجليزي.
- **توحيد أرقام الجوال**: الصيغ `05xxxxxxxx` و`5xxxxxxxx` و`+966…` و`00966…` والأرقام الهندية (٠٥٥…) كلها تتحول لصيغة واتساب.
- **إرسال في الخلفية** مع شريط تقدّم، وحالة لكل شخص، وزر إيقاف، وتقرير CSV في النهاية.
- **تحميل كل الشهادات ZIP** بدون إرسال.
- **وضع تجربة** (`DRY_RUN=1`) يحفظ الملفات بدل ما يرسلها.
- **حماية بكلمة مرور** (`APP_PASSWORD`).

## التشغيل محليًا

```bash
git clone https://github.com/engrevo/certificate-sender.git
cd certificate-sender
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env        # وعدّل القيم
python run.py               # http://127.0.0.1:5000
```

**الـ OCR (اختياري، بس ينصح فيه):** يلزم عشان كشف كلمة `{الاسم}`. بدونه، الكشف بالخط والمساحة الفاضية يشتغل عادي.

| النظام | الأمر |
|---|---|
| Ubuntu/Debian | `sudo apt install tesseract-ocr tesseract-ocr-ara` |
| macOS | `brew install tesseract tesseract-lang` |
| Windows | ثبّت [Tesseract من UB-Mannheim](https://github.com/UB-Mannheim/tesseract/wiki) وفعّل Arabic |

أو استخدم Docker، وكل شيء فيه جاهز:

```bash
docker build -t certificate-sender .
docker run -p 8000:8000 --env-file .env certificate-sender
```

## إعداد واتساب (WhatsApp Cloud API)

1. أنشئ تطبيق في [Meta for Developers](https://developers.facebook.com/) وأضف له منتج **WhatsApp**.
2. خذ **Phone number ID** و**Access token** (للاستخدام الدائم أنشئ System User token) وحطهم في `.env`.
3. أول رسالة لأي شخص لازم تكون **قالب رسالة معتمد** (Message Template). أنشئه من WhatsApp Manager بهذا الشكل:
   - **الاسم:** `attendance_certificate`
   - **الفئة:** Utility
   - **الترويسة (Header):** Document
   - **النص (Body):** `مرحبًا {{1}}، شكرًا لحضورك. مرفق لك شهادة الحضور.`
4. بعد ما ينعتمد، اضبط `WHATSAPP_TEMPLATE_NAME` و`WHATSAPP_TEMPLATE_LANG=ar`، وخلّ `DRY_RUN=0`.

> **ملاحظات:**
> - في رقم التجربة اللي تعطيك Meta، تقدر ترسل بس للأرقام اللي أضفتها في قائمة المستلمين المسموحين.
> - الرسائل خارج نافذة الـ 24 ساعة تنحسب عليك [حسب تسعير Meta](https://developers.facebook.com/docs/whatsapp/pricing).
> - `WHATSAPP_SEND_MODE=document` يرسل الملف مباشرة بدون قالب، ويشتغل بس مع شخص راسلك خلال آخر 24 ساعة.

## نصائح للمنظّم

- **أدق طريقة:** اكتب `{الاسم}` في مكان الاسم داخل التصميم (Canva أو Figma أو غيرها) بنفس الخط واللون والحجم اللي تبيه، والموقع يمسحها ويحط الاسم مكانها.
- الشهادة ممكن تكون صورة أو PDF. إذا كانت PDF، الصفحة الأولى تتحول لصورة بدقة 200 DPI.
- **شكل ملف القائمة:**

  | الاسم | الجوال |
  |---|---|
  | عبدالله محمد العتيبي | 0551234567 |

## هيكل المشروع

```
app/
  detector.py    # كشف مكان الاسم (OCR + OpenCV)
  renderer.py    # فتح القالب، كتابة الاسم بالعربي، تصدير PDF
  recipients.py  # قراءة القائمة وتوحيد الأرقام
  whatsapp.py    # عميل WhatsApp Cloud API
  jobs.py        # الإرسال في الخلفية + التقرير
  __init__.py    # مسارات Flask
  templates/ static/ fonts/
samples/         # قوالب تجريبية + make_samples.py
tests/           # pytest
```

## الاختبارات

```bash
pip install -r requirements-dev.txt
pytest -q
```

## النشر

التطبيق يحتاج سيرفر Python دائم، لأن الإرسال يشتغل في الخلفية. يناسبه Render أو Railway أو Fly.io أو أي VPS باستخدام الـ Dockerfile. شغّله بعامل (worker) واحد، لأن حالة الإرسال محفوظة في الذاكرة، و**فعّل `APP_PASSWORD`** قبل ما تنشره.

## الرخصة

الكود تحت رخصة MIT. الخطوط Amiri وTajawal تحت رخصة SIL OFL (ملفات الرخصة داخل `app/fonts`).
