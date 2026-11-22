/**
 * مِرسال: حفظ شهادات الحضور في Google Drive، والتحقق منها لصفحة الشهادة.
 *
 * طريقة التركيب:
 * 1. افتح https://script.new بحساب Gmail الشخصي.
 * 2. امسح الكود الموجود والصق هذا الملف كامل، وغيّر SECRET لكلمة سر طويلة من عندك.
 * 3. Deploy > New deployment > Web app
 *      Execute as: Me
 *      Who has access: Anyone
 *    ووافق على الصلاحيات.
 * 4. انسخ رابط الـ Web app (ينتهي بـ /exec) وحطه في Render:
 *      DRIVE_SCRIPT_URL    = الرابط
 *      DRIVE_SCRIPT_SECRET = نفس قيمة SECRET تحت
 *    وحطه كمان في docs/config.js عشان صفحة الشهادة تتحقق منه.
 *
 * لو عدّلت هذا الملف بعد النشر: Deploy > Manage deployments > ✏️ > Version: New version > Deploy
 * (بهذي الطريقة الرابط ما يتغيّر).
 */
const SECRET = 'CHANGE-ME-to-a-long-random-secret';
const FOLDER_NAME = 'شهادات الحضور';

// يرفع الشهادة (يحتاج كلمة السر).
function doPost(e) {
  try {
    const body = JSON.parse(e.postData.contents);
    if (!SECRET || SECRET.indexOf('CHANGE-ME') === 0 || body.secret !== SECRET) {
      return json_({ error: 'unauthorized' });
    }
    const filename = String(body.filename || 'certificate.pdf').slice(0, 120);
    const blob = Utilities.newBlob(Utilities.base64Decode(body.data), 'application/pdf', filename);
    const file = folder_().createFile(blob);
    file.setDescription(JSON.stringify({
      name: String(body.name || '').slice(0, 120),
      issued: Utilities.formatDate(new Date(), 'Etc/UTC', 'yyyy-MM-dd'),
    }));
    file.setSharing(DriveApp.Access.ANYONE_WITH_LINK, DriveApp.Permission.VIEW);
    return json_({ url: file.getUrl(), id: file.getId() });
  } catch (err) {
    return json_({ error: String(err) });
  }
}

// ?id=<file id>: هل الشهادة صادرة من مِرسال؟ يرجّع الاسم وتاريخ الإصدار فقط.
// يجاوب عن الملفات اللي داخل مجلد الشهادات بس، وما يكشف أي ملف ثاني في الدرايف.
function doGet(e) {
  const id = e && e.parameter && e.parameter.id;
  if (!id) {
    return json_({ ok: true, service: 'mirsal drive store' });
  }
  try {
    if (!/^[A-Za-z0-9_-]{10,128}$/.test(id)) return json_({ ok: false });
    const file = DriveApp.getFileById(id);
    if (file.isTrashed() || !inFolder_(file)) return json_({ ok: false });
    let meta = {};
    try { meta = JSON.parse(file.getDescription() || '{}'); } catch (ignored) {}
    const fallbackName = file.getName().replace(/\.pdf$/i, '').replace(/^شهادة_/, '').replace(/_/g, ' ');
    return json_({
      ok: true,
      name: meta.name || fallbackName,
      issued: meta.issued || Utilities.formatDate(file.getDateCreated(), 'Etc/UTC', 'yyyy-MM-dd'),
    });
  } catch (err) {
    return json_({ ok: false });
  }
}

function inFolder_(file) {
  const target = folder_().getId();
  const parents = file.getParents();
  while (parents.hasNext()) {
    if (parents.next().getId() === target) return true;
  }
  return false;
}

function folder_() {
  const it = DriveApp.getFoldersByName(FOLDER_NAME);
  return it.hasNext() ? it.next() : DriveApp.createFolder(FOLDER_NAME);
}

function json_(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON);
}
