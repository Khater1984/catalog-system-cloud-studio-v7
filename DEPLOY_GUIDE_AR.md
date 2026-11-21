# دليل النشر السحابي — خطوات عملية

هذا الدليل يشرح كيف تنشر نظام الكتالوجات أونلاين ليعمل عليه زملاؤك، مع تخزين الملفات في Google Drive ومزامنة البيانات إلى Google Sheets.

## المتطلبات المسبقة
- حساب Google Cloud مفعّل وفيه فوترة مرتبطة (أو حساب تجريبي)
- حساب Google عادي سيمتلك الملفات على Drive
- المشروع مستنسخ محليًا من GitHub

## الخطوة 1: تجهيز Google Cloud

1. ادخل إلى [Google Cloud Console](https://console.cloud.google.com/).
2. أنشئ مشروعًا جديدًا (مثلًا `catalog-system`).
3. فعّل الخدمات التالية من قائمة APIs & Services:
   - Google Drive API
   - Google Sheets API
   - Cloud Run API
   - Cloud SQL Admin API
   - Secret Manager API
   - Cloud Build API

## الخطوة 2: إعداد OAuth

1. اذهب إلى **APIs & Services → OAuth consent screen**.
2. اختر External (أو Internal إذا كنت على Workspace).
3. املأ البيانات المطلوبة واجعل الحالة **In production** (مهم جدًا).
4. اذهب إلى **Credentials → Create Credentials → OAuth client ID**.
5. اختر نوع **Desktop app**.
6. نزّل ملف `credentials.json` وضعه في مجلد المشروع بجانب `app.py`.

## الخطوة 3: الحصول على Token

على جهازك المحلي (بعد تثبيت المتطلبات):

```bash
pip install -r requirements.txt
python scripts/get_token.py > token.json
```

- سيفتح المتصفح لتسجيل الدخول.
- سجّل الدخول بحساب Google **الذي سيمتلك الملفات** على Drive.
- سيُنشأ ملف `token.json`.

**لا ترفع** ملف `token.json` أو `credentials.json` إلى GitHub.

## الخطوة 4: النشر على Cloud Run

1. ثبّت [Google Cloud SDK](https://cloud.google.com/sdk/docs/install) على جهازك.
2. سجّل الدخول:
   ```bash
   gcloud auth login
   gcloud config set project YOUR_PROJECT_ID
   ```
3. عدّل ملف `deploy.sh` وضع اسم مشروعك والمنطقة.
4. نفّذ الأوامر داخل `deploy.sh` واحدة واحدة (لا تشغّله كله مرة واحدة إلا إذا كنت متأكدًا).
5. بعد النشر سيعطيك السكريبت رابط التطبيق.

## الخطوة 5: بعد النشر

- افتح الرابط وسجّل الدخول بالمالك (`OWNER_USERNAME` / `OWNER_PASSWORD`).
- أضف زملاءك من صفحة «المستخدمين».
- ارفع كتالوج تجريبي وتأكد أن الصور ظهرت في Google Drive.
- لتشغيل المزامنة إلى Google Sheets: استخدم الزر المخصص لذلك داخل التطبيق (أو استدع دالة `gsync.sync()` يدويًا).

## ملاحظات مهمة
- حد الرفع على Cloud Run حوالي 32 ميجابايت. الكتالوجات الأكبر تحتاج ضغط.
- المزامنة إلى Sheets أحادية الاتجاه (من قاعدة البيانات → الشيت).
- احتفظ بنسخة احتياطية من بياناتك المحلية قبل أي ترحيل.
- لا ترفع ملفات الأسرار إلى GitHub أبدًا.

## استكشاف الأخطاء
- إذا فشل النشر: راجع سجلات Cloud Run.
- إذا لم تظهر الصور: تأكد أن `STORAGE_BACKEND=drive` وأن الـ token صحيح.
- إذا انتهى صلاحية الـ token: أعد تشغيل `scripts/get_token.py` وحدّث السر في Secret Manager.
