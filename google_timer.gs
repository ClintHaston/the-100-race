// The $100 Race: 5-minute timer. Paste this BELOW the existing code in your Apps Script project.
// Before running: Project Settings (gear) > Script properties > Add property
//   GITHUB_TOKEN = your fine-grained GitHub token (repo the-100-race only, Actions: Read and write).
// Then pick startEvery5Minutes in the function menu and click Run. Approve the permission screen.

const RACE_WORKFLOW = "https://api.github.com/repos/ClintHaston/the-100-race/actions/workflows/race.yml/dispatches";

function startEvery5Minutes() {
  stopTimer();
  ScriptApp.newTrigger("kickRace").timeBased().everyMinutes(5).create();
  kickRace();
  Logger.log("Timer is on. The race will check every 5 minutes.");
}

function stopTimer() {
  ScriptApp.getProjectTriggers().filter(t => t.getHandlerFunction() === "kickRace").forEach(t => ScriptApp.deleteTrigger(t));
}

function kickRace() {
  const token = PropertiesService.getScriptProperties().getProperty("GITHUB_TOKEN");
  if (!token) throw new Error("Add GITHUB_TOKEN under Project Settings > Script properties first.");
  const r = UrlFetchApp.fetch(RACE_WORKFLOW, {
    method: "post", contentType: "application/json", muteHttpExceptions: true,
    headers: { Authorization: "Bearer " + token, Accept: "application/vnd.github+json" },
    payload: JSON.stringify({ ref: "main" })
  });
  if (r.getResponseCode() !== 204) throw new Error("GitHub said " + r.getResponseCode() + ": " + r.getContentText().slice(0, 200));
}
