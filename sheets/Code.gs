// CPEEN 2026 — Google Apps Script backend
// Deploy: Extensions → Apps Script → Deploy → New deployment
//   Type: Web app | Execute as: Me | Who has access: Anyone
// Copy the deployment URL into js/config.js

var COLS = {
  participants: ['id','name','school','county','teacher','level','stage','email','grade','accessCode','registeredAt','status'],
  sessions:     ['id','participantId','examId','level','stage','variantId','answers','startedAt','submittedAt','result','status','locked','writingGrade','writingFeedback'],
  exams:        ['id','level','stage','title','duration','variants','createdAt']
};

// ── Sheet helpers ─────────────────────────────────────────────────────────────

function sheetToObjects(sheet) {
  if (!sheet) return [];
  var data = sheet.getDataRange().getValues();
  if (data.length < 2) return [];
  var headers = data[0];
  return data.slice(1).map(function(row) {
    var obj = {};
    headers.forEach(function(h, i) { obj[h] = row[i] === '' ? null : row[i]; });
    return obj;
  });
}

function objectsToSheet(sheet, objects, cols) {
  sheet.clearContents();
  var rows = [cols];
  (objects || []).forEach(function(obj) {
    rows.push(cols.map(function(c) {
      var v = obj[c];
      if (v === null || v === undefined) return '';
      if (typeof v === 'object') return JSON.stringify(v);
      return v;
    }));
  });
  sheet.getRange(1, 1, rows.length, cols.length).setValues(rows);
}

function configToObject(sheet) {
  if (!sheet) return {};
  var data = sheet.getDataRange().getValues();
  var obj = {};
  data.forEach(function(row) {
    if (!row[0]) return;
    try { obj[row[0]] = JSON.parse(row[1]); }
    catch(e) { obj[row[0]] = row[1]; }
  });
  return obj;
}

function saveConfigSheet(sheet, configObj) {
  sheet.clearContents();
  var rows = Object.keys(configObj).map(function(k) {
    var v = configObj[k];
    return [k, typeof v === 'object' ? JSON.stringify(v) : v];
  });
  if (rows.length) sheet.getRange(1, 1, rows.length, 2).setValues(rows);
}

// ── Parsing (Sheets stores numbers as numbers but JSON fields as strings) ─────

function parseNum(v) { return (v === '' || v === null) ? null : Number(v); }
function parseBool(v) { return v === true || v === 'true'; }
function parseJSON(v) {
  if (!v || v === '') return null;
  if (typeof v === 'object') return v;
  try { return JSON.parse(v); } catch(e) { return null; }
}

function parseParticipants(rows) {
  return rows.map(function(p) {
    return Object.assign({}, p, { registeredAt: parseNum(p.registeredAt) });
  });
}

function parseSessions(rows) {
  return rows.map(function(s) {
    return Object.assign({}, s, {
      answers:     parseJSON(s.answers) || {},
      result:      parseJSON(s.result),
      startedAt:   parseNum(s.startedAt),
      submittedAt: parseNum(s.submittedAt),
      locked:      parseBool(s.locked),
      writingGrade: s.writingGrade === '' || s.writingGrade === null ? null : Number(s.writingGrade)
    });
  });
}

function parseExams(rows) {
  return rows.map(function(e) {
    return Object.assign({}, e, {
      duration:  Number(e.duration) || 45,
      createdAt: parseNum(e.createdAt),
      variants:  parseJSON(e.variants) || []
    });
  });
}

// ── doGet ─────────────────────────────────────────────────────────────────────

function doGet(e) {
  var ss     = SpreadsheetApp.getActiveSpreadsheet();
  var action = e.parameter.action;

  if (action === 'getAll') {
    var result = {
      participants: parseParticipants(sheetToObjects(ss.getSheetByName('Participants'))),
      sessions:     parseSessions(sheetToObjects(ss.getSheetByName('Sessions'))),
      exams:        parseExams(sheetToObjects(ss.getSheetByName('Exams'))),
      config:       configToObject(ss.getSheetByName('Config'))
    };
    return ContentService
      .createTextOutput(JSON.stringify(result))
      .setMimeType(ContentService.MimeType.JSON);
  }

  return ContentService.createTextOutput('{"error":"unknown action"}')
    .setMimeType(ContentService.MimeType.JSON);
}

// ── doPost ────────────────────────────────────────────────────────────────────

function doPost(e) {
  var ss     = SpreadsheetApp.getActiveSpreadsheet();
  var action = e.parameter.action;
  var data;
  try { data = JSON.parse(e.parameter.data || 'null'); } catch(ex) { data = null; }

  if (action === 'saveParticipants') {
    objectsToSheet(ss.getSheetByName('Participants'), data, COLS.participants);
  } else if (action === 'saveSessions') {
    objectsToSheet(ss.getSheetByName('Sessions'), data, COLS.sessions);
  } else if (action === 'saveExams') {
    objectsToSheet(ss.getSheetByName('Exams'), data, COLS.exams);
  } else if (action === 'saveConfig') {
    saveConfigSheet(ss.getSheetByName('Config'), data || {});
  }

  return ContentService.createTextOutput('ok').setMimeType(ContentService.MimeType.TEXT);
}
