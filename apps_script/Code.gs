/**
 * حفظ شهادات الحضور في Google Drive وإرجاع رابط دائم لكل شهادة.
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
 */
const SECRET = 'CHANGE-ME-to-a-long-random-secret';
const FOLDER_NAME = 'شهادات الحضور';

function doPost(e) {
  try {
    const body = JSON.parse(e.postData.contents);
    if (!SECRET || SECRET.indexOf('CHANGE-ME') === 0 || body.secret !== SECRET) {
      return json_({ error: 'unauthorized' });
    }
    const name = String(body.filename || 'certificate.pdf').slice(0, 120);
    const blob = Utilities.newBlob(Utilities.base64Decode(body.data), 'application/pdf', name);
    const file = folder_().createFile(blob);
    file.setSharing(DriveApp.Access.ANYONE_WITH_LINK, DriveApp.Permission.VIEW);
    return json_({ url: file.getUrl(), id: file.getId() });
  } catch (err) {
    return json_({ error: String(err) });
  }
}

function doGet() {
  return json_({ ok: true, service: 'certificate-sender drive store' });
}

function folder_() {
  const it = DriveApp.getFoldersByName(FOLDER_NAME);
  return it.hasNext() ? it.next() : DriveApp.createFolder(FOLDER_NAME);
}

function json_(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON);
}
