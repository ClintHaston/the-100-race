// Paste this into your private Google Sheet: Extensions > Apps Script.
// It shares ONLY the alert id and your choice (Done or Skipped). Prices and dollar amounts stay private.
function doGet() {
  const sheet = SpreadsheetApp.getActiveSpreadsheet().getSheets()[0];
  const rows = sheet.getDataRange().getValues();
  const head = rows.shift().map(String);
  const idCol = head.indexOf("Alert ID");
  const choiceCol = head.indexOf("Choice");
  const out = rows
    .filter(r => idCol >= 0 && r[idCol])
    .map(r => ({ id: String(r[idCol]), status: String(r[choiceCol]) }));
  return ContentService.createTextOutput(JSON.stringify(out)).setMimeType(ContentService.MimeType.JSON);
}
