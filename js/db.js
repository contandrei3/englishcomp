/* CPEEN Platform — shared data layer (pure ES5/ES6, no framework) */
var CPEEN = (function () {
  'use strict';

  var KEYS = {
    CONFIG:       'cpeen_config',
    PARTICIPANTS: 'cpeen_participants',
    EXAMS:        'cpeen_exams',
    SESSIONS:     'cpeen_sessions'
  };

  var SHEETS_URL = '';

  function syncToSheets(action, data) {
    if (!SHEETS_URL) return;
    fetch(SHEETS_URL + '?action=' + encodeURIComponent(action), {
      method: 'POST',
      body: JSON.stringify(data),
      mode: 'no-cors'
    }).catch(function() {});
  }

  function init() {
    var url = (typeof CPEEN_SHEETS_URL !== 'undefined') ? CPEEN_SHEETS_URL : '';
    if (!url || url.indexOf('YOUR_APPS') !== -1) return Promise.resolve();
    SHEETS_URL = url;
    var fetchData = fetch(url + '?action=getAll')
      .then(function(r) { return r.json(); })
      .then(function(d) {
        if (d.participants) ss(KEYS.PARTICIPANTS, d.participants);
        if (d.sessions)     ss(KEYS.SESSIONS,     d.sessions);
        if (d.exams)        ss(KEYS.EXAMS,         d.exams);
        if (d.config)       ss(KEYS.CONFIG,         d.config);
      });
    var timeout = new Promise(function(resolve) { setTimeout(resolve, 4000); });
    return Promise.race([fetchData, timeout]).catch(function() {});
  }

  var DEFAULT_ADMIN_HASH = '214d1f1c62239db83286301ef9ce31e93144e98570370de2f035560e13b2a7d9'; // cpeen2026

  // ── Storage ───────────────────────────────────────────────────────────────
  function sg(key, fb) {
    try { var v = localStorage.getItem(key); return v !== null ? JSON.parse(v) : (fb !== undefined ? fb : null); }
    catch(e) { return fb !== undefined ? fb : null; }
  }
  function ss(key, val) {
    try { localStorage.setItem(key, JSON.stringify(val)); return true; } catch(e) { return false; }
  }

  // ── Utilities ─────────────────────────────────────────────────────────────
  function genId(pfx) {
    return (pfx || 'id') + '_' + Date.now() + '_' + Math.random().toString(36).substr(2, 6);
  }

  function genCode() {
    var chars = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789';
    var code = '';
    for (var i = 0; i < 8; i++) code += chars[Math.floor(Math.random() * chars.length)];
    return code;
  }

  function uniqueCode(existing) {
    var code, tries = 0;
    do { code = genCode(); tries++; }
    while (tries < 500 && existing.some(function (p) { return p.accessCode === code; }));
    return code;
  }

  function hashPass(s) {
    return crypto.subtle.digest('SHA-256', new TextEncoder().encode(s)).then(function (buf) {
      return Array.from(new Uint8Array(buf)).map(function (b) { return b.toString(16).padStart(2, '0'); }).join('');
    });
  }

  function norm(s) {
    return (s || '').trim().toLowerCase().replace(/\s+/g, ' ').replace(/[.,;:!?]+$/, '');
  }

  // ── Config ────────────────────────────────────────────────────────────────
  function getConfig() {
    return Object.assign({ maxPerSchoolPerLevel: 0, showResultsImmediately: true, adminHash: DEFAULT_ADMIN_HASH, activeExams: {} }, sg(KEYS.CONFIG, {}));
  }
  function saveConfig(cfg) { ss(KEYS.CONFIG, cfg); syncToSheets('saveConfig', cfg); }

  // ── Participants ──────────────────────────────────────────────────────────
  function getParticipants() { return sg(KEYS.PARTICIPANTS, []); }
  function saveParticipants(ps) { ss(KEYS.PARTICIPANTS, ps); syncToSheets('saveParticipants', ps); }

  function addParticipant(data) {
    var ps = getParticipants();
    var p = Object.assign({}, data, { id: genId('p'), accessCode: uniqueCode(ps), registeredAt: Date.now(), status: 'registered' });
    ps.push(p);
    saveParticipants(ps);
    return p;
  }

  function findParticipantByCode(code) {
    var c = (code || '').toUpperCase().trim();
    return getParticipants().find(function (p) { return p.accessCode === c; }) || null;
  }

  function updateParticipant(id, updates) {
    var ps = getParticipants();
    var idx = ps.findIndex(function (p) { return p.id === id; });
    if (idx === -1) return null;
    ps[idx] = Object.assign({}, ps[idx], updates);
    saveParticipants(ps);
    return ps[idx];
  }

  function deleteParticipant(id) {
    saveParticipants(getParticipants().filter(function (p) { return p.id !== id; }));
    saveSessions(getSessions().filter(function (s) { return s.participantId !== id; }));
  }

  // ── Sessions ──────────────────────────────────────────────────────────────
  function getSessions() { return sg(KEYS.SESSIONS, []); }
  function saveSessions(ss_) { ss(KEYS.SESSIONS, ss_); syncToSheets('saveSessions', ss_); }

  function getSessionByParticipant(pid) {
    return getSessions().find(function (s) { return s.participantId === pid; }) || null;
  }

  function createSession(pid, exam, variantId) {
    var v = exam.variants.find(function (v) { return v.id === variantId; });
    if (!v) return null;
    var session = {
      id: genId('s'), participantId: pid, examId: exam.id,
      level: exam.level, stage: exam.stage, variantId: variantId,
      answers: {
        s1: new Array(v.s1.items.length).fill(''),
        s2: new Array(v.s2.key.length).fill(''),
        s3: new Array(v.s3.key.length).fill(''),
        s4: new Array(v.s4.items.length).fill(''),
        writing: ''
      },
      startedAt: null, submittedAt: null, result: null,
      status: 'pending', locked: false,
      writingGrade: null, writingFeedback: ''
    };
    var arr = getSessions();
    arr.push(session);
    saveSessions(arr);
    return session;
  }

  function updateSession(id, updates) {
    var arr = getSessions();
    var idx = arr.findIndex(function (s) { return s.id === id; });
    if (idx === -1) return null;
    arr[idx] = Object.assign({}, arr[idx], updates);
    saveSessions(arr);
    return arr[idx];
  }

  // ── Exams ─────────────────────────────────────────────────────────────────
  function getExams() { return sg(KEYS.EXAMS, []); }
  function saveExams(exams) { ss(KEYS.EXAMS, exams); syncToSheets('saveExams', exams); }

  function getExamForParticipant(level, stage) {
    return getExams().find(function (e) { return e.level === level && e.stage === stage && e.variants && e.variants.length; }) || null;
  }

  function addExam(data) {
    var exams = getExams();
    var exam = Object.assign({ id: genId('exam'), createdAt: Date.now() }, data);
    exams.push(exam);
    saveExams(exams);
    return exam;
  }

  function updateExam(id, updates) {
    var exams = getExams();
    var idx = exams.findIndex(function (e) { return e.id === id; });
    if (idx === -1) return null;
    exams[idx] = Object.assign({}, exams[idx], updates);
    saveExams(exams);
    return exams[idx];
  }

  function deleteExam(id) { saveExams(getExams().filter(function (e) { return e.id !== id; })); }

  // ── Grading ───────────────────────────────────────────────────────────────
  function gradeSession(session, exam) {
    var v = exam.variants.find(function (x) { return x.id === session.variantId; });
    if (!v) return null;
    var a = session.answers, total = 10, det = { s1: [], s2: [], s3: [], s4: [] };
    v.s1.key.forEach(function (acc, i) { var ok = acc.some(function (x) { return norm(x) === norm(a.s1[i]); }); det.s1.push(ok); if (ok) total += v.s1.pts; });
    v.s2.key.forEach(function (x, i)   { var ok = norm(x) === norm(a.s2[i]); det.s2.push(ok); if (ok) total += v.s2.pts; });
    v.s3.key.forEach(function (acc, i) { var ok = acc.some(function (x) { return norm(x) === norm(a.s3[i]); }); det.s3.push(ok); if (ok) total += v.s3.pts; });
    v.s4.key.forEach(function (acc, i) { var ok = acc.some(function (x) { return norm(x) === norm(a.s4[i]); }); det.s4.push(ok); if (ok) total += v.s4.pts; });
    return { total: total, det: det };
  }

  // ── Seed ──────────────────────────────────────────────────────────────────
  function seedInitialData() {
    if (getExams().some(function (e) { return e.id === 'seed_c1_finala'; })) return;
    addExam({
      id: 'seed_c1_finala', level: 'C1', stage: 'finala',
      title: 'C1 Finală 2026', duration: 60,
      variants: [{
        id: 'V1',
        s1: {
          items: [
            {kw:"DAWNED",   s1:"She had no idea how costly the renovation would turn out to be until she received the builder's quote.", before:"It only", after:"costly the renovation would be when she received the builder's quote."},
            {kw:"PROSPECT", s1:"The authorities are very unlikely to lift the travel ban before the end of the year.", before:"There is", after:"of the travel ban being lifted before the end of the year."},
            {kw:"EXCEEDED", s1:"Margaret found the new software system far more intuitive than she had anticipated.", before:"The new software system's ease of use", after:"Margaret."},
            {kw:"OWN",      s1:"She consistently refused to admit that her initial assessment had been wrong.", before:"She consistently refused", after:"that her initial assessment had been wrong."},
            {kw:"HAVING",   s1:"People rarely achieve lasting success in creative fields without experiencing at least one major setback.", before:"Lasting success in creative fields rarely comes", after:"at least one major setback."},
            {kw:"SPITE",    s1:"Even though the negotiations had broken down completely, the diplomats refused to abandon hope.", before:"The diplomats refused to abandon hope", after:"complete breakdown of the negotiations."},
            {kw:"POINT",    s1:"He never once stopped to consider that his business model might be fundamentally flawed.", before:"", after:"did he stop to consider that his business model might be fundamentally flawed."},
            {kw:"LIGHT",    s1:"The investigation revealed that the company had been falsifying its accounts for years.", before:"The fact that the company had been falsifying its accounts for years was", after:"the investigation."},
            {kw:"ACCOUNT",  s1:"The government faced severe criticism for failing to address the housing crisis in time.", before:"The government", after:"its failure to address the housing crisis in time."},
            {kw:"TERMS",    s1:"She found it extremely difficult to accept the new working conditions.", before:"She", after:"the new working conditions."}
          ],
          key:[
            ["dawned on her how","only dawned on her how"],
            ["very little prospect","little prospect whatsoever","slim prospect whatsoever","very slim prospect"],
            ["far exceeded the expectations of"],
            ["to own up to the fact","to own up to the fact that"],
            ["without having gone through","without having experienced","without having had"],
            ["in spite of the"],["at no point"],["brought to light by"],
            ["was called to account for"],
            ["struggled to come to terms with","found it hard to come to terms with"]
          ], pts:3
        },
        s2:{
          intro:"(0) OK | (00) DELETE 'to'", text:"Social Media and Political Discourse",
          lines:[
            "Whereas once news was controlled by established broadcasters, today anyone who has internet",
            "connection is capable to broadcasting their views to a global audience. Supporters of this",
            "shift argue that social media have democratised political participation, giving marginalised",
            "voices a chance to be heard. Critics, however, note that false stories spread considerately",
            "faster than accurate ones on these platforms and are far most likely to go viral. Perhaps",
            "the most troubling development is the rise of \"echo chambers\" — digital spaces in that",
            "users are exposed solely to views that reinforce their own beliefs. This makes increasingly",
            "difficult to engage in the kind of reasoned dialogue that healthy democracies depend of.",
            "Unless social media companies take greater responsible for the content shared on their",
            "platforms, these troubling trends are unlike to reverse, threatening democratic discourse."
          ],
          key:["an","of","has","considerably","more","which","it","on","responsibility","unlikely"], pts:3
        },
        s3:{
          title:"THE ART OF SLOW TRAVEL",
          segments:["The concept of slow travel has gained considerable momentum ",{g:1}," recent years, emerging as a deliberate counterpoint to the relentless pace of modern tourism. ",{g:2}," of rushing from one landmark to another, slow travellers choose to remain in a single destination for an extended period, immersing themselves fully in ",{g:3}," local culture and daily rhythms. The philosophy is rooted in the belief that genuine cultural understanding cannot ",{g:4}," achieved by merely ticking items off a list of must-see attractions. ",{g:5},", it demands patience, curiosity, and a willingness to embrace the unplanned. Advocates argue that this approach benefits not only the individual traveller, ",{g:6}," also the communities they visit. When tourists stay longer, they tend to spend their money at local businesses ",{g:7}," than at international chains. Furthermore, the reduced need for frequent flights significantly lowers one's carbon footprint, making slow travel an environmentally conscious ",{g:8}," as well. Critics, ",{g:9},", maintain that slow travel is a luxury that few can afford. This is a valid point, and one ",{g:10}," which advocates have yet to offer a fully satisfactory answer."],
          key:[["in"],["instead","rather"],["the"],["be"],["rather","instead"],["but"],["rather"],["choice","option"],["however"],["to"]], pts:2
        },
        s4:{
          items:[
            {before:"The",after:"of the ancient manuscript confirmed that it dated back to the 12th century.",kw:"AUTHENTIC"},
            {before:"His",after:"refusal to compromise ultimately led to the breakdown of the negotiations.",kw:"YIELD"},
            {before:"The charity's work has brought about a remarkable",after:"in the lives of thousands of displaced families.",kw:"TRANSFORM"},
            {before:"She was widely praised for her",after:"in dealing with the unexpected crisis that arose mid-project.",kw:"RESOURCE"},
            {before:"The",after:"of the new policy has sparked widespread debate among legal experts.",kw:"IMPLEMENT"},
            {before:"Many historians consider the treaty to have been fundamentally",after:"due to its ambiguous wording.",kw:"EFFECT"},
            {before:"The professor's",after:"approach to teaching encouraged students to question received wisdom.",kw:"CONVENTIONAL"},
            {before:"The report highlighted a fundamental",after:"between the two countries' approaches to fiscal policy.",kw:"COMPATIBLE"},
            {before:"Her speech was delivered with such",after:"that the entire audience fell silent.",kw:"ELOQUENT"},
            {before:"The excavation shed new light on the",after:"of the ancient civilisation and its early trading routes.",kw:"ORIGIN"}
          ],
          key:[["authentication"],["unyielding"],["transformation"],["resourcefulness"],["implementation"],["ineffective","ineffectual"],["unconventional"],["incompatibility"],["eloquence"],["origins"]], pts:2
        }
      }]
    });
  }

  return {
    init,
    genId, genCode, hashPass, norm,
    getConfig, saveConfig,
    getParticipants, saveParticipants, addParticipant, findParticipantByCode, updateParticipant, deleteParticipant,
    getSessions, saveSessions, getSessionByParticipant, createSession, updateSession,
    getExams, saveExams, getExamForParticipant, addExam, updateExam, deleteExam,
    gradeSession, seedInitialData
  };
})();
