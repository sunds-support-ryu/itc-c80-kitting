// Set Script Property SPREADSHEET_ID, then deploy as a Web App.
// Python follows ContentService redirects and validates ok + record_id.
function doPost(e) {
  let recordId = '';
  let lock = null;
  const reply = value => ContentService.createTextOutput(JSON.stringify(value))
    .setMimeType(ContentService.MimeType.JSON);
  try {
    const data = JSON.parse(e.postData.contents);
    recordId = String(data.record_id || '');
    if (!recordId) throw new Error('record_id is required');
    if (data.type === 'test') return reply({ok: true, record_id: recordId, test: true});
    if (data.type !== 'camera_result') throw new Error('Unsupported type');
    const id = PropertiesService.getScriptProperties().getProperty('SPREADSHEET_ID');
    if (!id) throw new Error('Set Script Property SPREADSHEET_ID');
    lock = LockService.getScriptLock();
    lock.waitLock(20000);
    const book = SpreadsheetApp.openById(id);
    const sheet = book.getSheetByName('CameraResults') || book.insertSheet('CameraResults');
    const fields = ['timestamp','record_id','carton','camera_slot','mac','sn','old_ip','new_ip',
      'result','appearance','network','rtsp','ir_cut','settings','tool_version','ir_on','ir_off'];
    if (sheet.getLastRow() === 0) sheet.appendRow(fields);
    else sheet.getRange(1, 1, 1, fields.length).setValues([fields]);
    let existingRow = 0;
    if (sheet.getLastRow() > 1) {
      const ids = sheet.getRange(2, 2, sheet.getLastRow() - 1, 1).getValues();
      const index = ids.findIndex(row => String(row[0]) === recordId);
      if (index >= 0) existingRow = index + 2;
    }
    const row = fields.map(field => {
      const value = data[field] == null ? '' : String(data[field]);
      return /^[=+@-]/.test(value) ? "'" + value : value;
    });
    if (existingRow) sheet.getRange(existingRow, 1, 1, fields.length).setValues([row]);
    else sheet.appendRow(row);
    SpreadsheetApp.flush();
    return reply({ok: true, record_id: recordId, duplicate: Boolean(existingRow)});
  } catch (error) {
    return reply({ok: false, record_id: recordId, error: String(error.message || error)});
  } finally {
    if (lock && lock.hasLock()) lock.releaseLock();
  }
}
