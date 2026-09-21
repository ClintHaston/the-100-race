// The $100 Race: builds your private trade log (Google Form + Sheet) in one click.
// 1. Go to script.google.com, click New project, delete what is there, paste this whole file.
// 2. Pick "setup" in the function menu at the top and click Run. Approve the permission screen.
// 3. Open View > Logs (or Execution log) and copy the links it prints.
// 4. Deploy > New deployment > type Web app > Execute as Me > Who has access Anyone > Deploy.
//    Copy the Web app URL. That is your STATUS_URL. It only shares alert ids and Done or Skipped.

function setup() {
  const form = FormApp.create("The $100 Race: my trades");
  form.setDescription("Log what you did with each alert. Your prices and dollar amounts stay private in your own Sheet.");
  form.setCollectEmail(false);
  const idItem = form.addTextItem().setTitle("Alert ID").setHelpText("Filled in for you when you tap I did it or Skip.").setRequired(true);
  const choiceItem = form.addMultipleChoiceItem().setTitle("Choice").setChoiceValues(["Done", "Skipped"]).setRequired(true);
  form.addMultipleChoiceItem().setTitle("Platform").setChoiceValues(["Robinhood", "Kraken", "Coinbase", "Other"]);
  form.addTextItem().setTitle("Asset").setHelpText("For example BTC, SOL, SPY");
  form.addTextItem().setTitle("Dollars").setHelpText("How much you put in or took out");
  form.addTextItem().setTitle("Fill price").setHelpText("The price you actually got");
  form.addParagraphTextItem().setTitle("Notes");

  const sheet = SpreadsheetApp.create("The $100 Race: my trades");
  form.setDestination(FormApp.DestinationType.SPREADSHEET, sheet.getId());

  const resp = form.createResponse()
    .withItemResponse(idItem.createResponse("ALERT_ID"))
    .withItemResponse(choiceItem.createResponse("Done"));
  const formUrl = resp.toPrefilledUrl().replace(/=Done(&|$)/, "=CHOICE$1");

  PropertiesService.getScriptProperties().setProperty("SHEET_ID", sheet.getId());
  Logger.log("Your private Sheet (save this under My trades on the dashboard): " + sheet.getUrl());
  Logger.log("FORM_URL (GitHub secret, and paste under My trades too): " + formUrl);
  Logger.log("Form editor: " + form.getEditUrl());
  Logger.log("Next: Deploy > New deployment > Web app > Execute as Me > Anyone. That link is STATUS_URL.");
}

// Called by the race every few minutes. Returns only alert ids and Done or Skipped.
function doGet() {
  const id = PropertiesService.getScriptProperties().getProperty("SHEET_ID");
  const out = [];
  if (id) {
    for (const sh of SpreadsheetApp.openById(id).getSheets()) {
      const rows = sh.getDataRange().getValues();
      if (!rows.length) continue;
      const head = rows.shift().map(String);
      const idCol = head.indexOf("Alert ID"), choiceCol = head.indexOf("Choice");
      if (idCol < 0 || choiceCol < 0) continue;
      rows.filter(r => r[idCol]).forEach(r => out.push({ id: String(r[idCol]), status: String(r[choiceCol]) }));
    }
  }
  return ContentService.createTextOutput(JSON.stringify(out)).setMimeType(ContentService.MimeType.JSON);
}
