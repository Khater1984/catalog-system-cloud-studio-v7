// أضف هذا الملف إلى Apps Script (Extensions > Apps Script) في جدول "Catalog DB".
function doGet() {
  return HtmlService.createHtmlOutputFromFile('Index').setTitle('الكتالوج');
}

function getCatalog() {
  var ss = SpreadsheetApp.getActive();
  var m = ss.getSheetByName('Models').getDataRange().getValues();
  var s = ss.getSheetByName('Specs').getDataRange().getValues();
  var specs = {};
  for (var i = 1; i < s.length; i++) {
    if (String(s[i][6]).toUpperCase() === 'TRUE') continue;       // خاصية مخفية
    if (!s[i][5]) continue;
    (specs[s[i][0]] = specs[s[i][0]] || []).push([s[i][2], s[i][5]]);
  }
  var out = [];
  for (var j = 1; j < m.length; j++) {
    if (String(m[j][6]).toUpperCase() !== 'TRUE') continue;       // مخفي من الكتالوج
    out.push({ id: m[j][0], code: m[j][1], series: m[j][2], family: m[j][3], arName: m[j][5],
               image: m[j][7], product: m[j][8], specs: specs[m[j][0]] || [] });
  }
  return out;
}

function getImage(fileId, full) {
  var f = DriveApp.getFileById(fileId);
  var b = full ? f.getBlob() : f.getThumbnail();
  return 'data:' + b.getContentType() + ';base64,' + Utilities.base64Encode(b.getBytes());
}
