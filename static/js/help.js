// ── Tab navigation ────────────────────────────────────────────

const TABS = { help: 'panel-help', faq: 'panel-faq', contact: 'panel-contact' };

function activateTab(tabKey) {
  document.querySelectorAll('.hc-tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.hc-panel').forEach(p => p.classList.remove('active'));
  const btn = document.querySelector(`.hc-tab[data-tab="${tabKey}"]`);
  const panel = document.getElementById(TABS[tabKey]);
  if (btn) btn.classList.add('active');
  if (panel) panel.classList.add('active');
  history.replaceState(null, '', `#${tabKey}`);
}

document.querySelectorAll('.hc-tab').forEach(btn => {
  btn.addEventListener('click', () => activateTab(btn.dataset.tab));
});

(function () {
  const hash = location.hash.replace('#', '');
  if (TABS[hash]) activateTab(hash);
})();

// ── Auth detect ───────────────────────────────────────────────

let _isAdmin = false;
let _authToken = null;

// getJwtEmail() is provided by feedback-utils.js
const _userEmail = getJwtEmail();

(function detectAuth() {
  const tokenCookie = document.cookie.split(';').map(c => c.trim()).find(c => c.startsWith('access_token='));
  if (!tokenCookie) return;
  const token = tokenCookie.split('=')[1];
  if (!token) return;
  _authToken = token;
  try {
    const payload = JSON.parse(atob(token.split('.')[1]));
    _isAdmin = payload.role === 'admin';
    if (_userEmail) document.getElementById('fbEmail').value = _userEmail;
  } catch (e) {}
})();

function getAuthHeaders() {
  return _authToken ? { 'Authorization': `Bearer ${_authToken}` } : {};
}

// ── FAQ static data ───────────────────────────────────────────

const FAQ_DATA = [
  { question: '讀豹是什麼？', answer: '讀豹是一個繁體中文新聞搜尋與分析平台，使用 AI 技術提供自然語言搜尋、深度研究報告等功能，專為知識工作者設計。', category: 'general' },
  { question: '讀豹的資料來源有哪些？', answer: '目前收錄以下媒體來源：自由時報、聯合新聞網、中央通訊社、中國時報、環境資訊中心、今周刊 ESG、經濟部能源署，共 7 個可信來源。所有來源經過篩選，確保資訊品質與可信度。', category: 'general' },
  { question: '新聞資料多久更新一次？', answer: '更新頻率從每小時到每半天不等，根據內部技術流程及各資料來源的更新頻率決定。', category: 'general' },
  { question: '如何搜尋新聞？', answer: '讀豹的一般搜尋採用「混合搜尋」技術，同時結合語意理解與關鍵字匹配。在搜尋框輸入自然語言問句，例如「最近台積電有什麼新聞？」或「AI 對台灣就業有什麼影響？」，按 Enter 即可搜尋。不需要精確關鍵字。', category: 'search' },
  { question: '什麼是深度研究（Deep Research）？', answer: '深度研究是類似多條件或進階搜尋的功能。AI 會分析多篇相關報導，產生結構化研究報告，包含論點分析、事實查核、知識圖譜等。適合需要深入了解某議題時使用。', category: 'search' },
  { question: '搜尋結果的排序依據是什麼？', answer: '搜尋結果依相關度排序，綜合考量語意相似度、關鍵字匹配、來源可信度、時效性等因素，透過多層排序模型確保結果品質與多元性。', category: 'search' },
  { question: '可以搜尋特定時間範圍的新聞嗎？', answer: '可以。你可以在問句中自然地描述時間範圍，例如「上週的半導體新聞」或「2025年12月的環保政策」，系統會自動解析時間條件。', category: 'search' },
  { question: '什麼是自由對話模式？', answer: '自由對話讓你針對搜尋結果或深度研究報告進行追問，類似 ChatGPT 的對話體驗，但所有回答都基於已檢索的新聞資料，確保資訊有據可查。', category: 'search' },
  { question: '如何註冊帳號？', answer: '讀豹採用邀請制（B2B 模式）。請聯絡我們取得註冊連結，使用該連結即可設定帳號。', category: 'account' },
  { question: '忘記密碼怎麼辦？', answer: '在登入頁面點擊「忘記密碼」，輸入註冊時使用的電子郵件，系統會寄送密碼重設連結。已登入的用戶可在左下角設定中變更密碼。', category: 'account' },
  { question: '可以在多台裝置上使用嗎？', answer: '可以。登入後對話紀錄會同步，你可以在不同裝置間切換使用。如需登出所有裝置，可在設定中選擇「登出全部裝置」。', category: 'account' },
  { question: '什麼是組織功能？', answer: '組織功能讓企業用戶統一管理團隊帳號。組織管理員可以邀請成員、管理權限、查看團隊使用狀況。', category: 'account' },
  { question: '如何釘選重要的搜尋結果？', answer: '在搜尋結果卡片上點擊釘選圖示，即可將該結果標記為重要。釘選的內容會保留在當前對話中，方便後續參考。', category: 'general' },
  { question: '對話紀錄會保存多久？', answer: '登入後的對話紀錄會永久保存在你的帳號中。你可以隨時在左側邊欄查看歷史對話，點擊即可繼續追問。', category: 'general' },
  { question: '可以分享搜尋結果嗎？', answer: '可以。點擊分享按鈕，可以將搜尋結果或深度研究報告匯出，支援複製文字、分享到其他平台等方式。', category: 'general' },
  { question: '讀豹如何確保資訊準確性？', answer: '讀豹使用多重機制確保準確性：(1) 只收錄可信媒體來源 (2) AI 回答必須引用原始報導 (3) 深度研究模式有事實查核機制（Critic Agent）(4) 所有引用可追溯至原始新聞。', category: 'privacy' },
  { question: '我的搜尋紀錄會被用於其他用途嗎？', answer: '不會。你的搜尋紀錄僅供你個人使用，不會被用於廣告投放、轉售給第三方或其他商業用途。', category: 'privacy' },
  { question: '資料儲存在哪裡？', answer: '所有資料儲存在位於歐洲的安全伺服器上，採用加密傳輸（HTTPS）與安全的資料庫存取機制。', category: 'privacy' },
  { question: '如何刪除我的帳號和資料？', answer: '請聯絡客服（support@twdubao.com）申請刪除帳號。我們會在確認身分後刪除所有相關資料。', category: 'privacy' },
  { question: '讀豹支援哪些瀏覽器？', answer: '建議使用最新版本的 Chrome、Firefox、Safari 或 Edge。需要啟用 JavaScript 和 Cookie。', category: 'other' },
  { question: '遇到問題如何回報？', answer: '請使用說明中心的「聯絡客服」頁面送出意見回饋，或直接寄信至 support@twdubao.com。我們會在 2 個工作天內回覆。', category: 'other' },
];

let _currentCat = 'all';

function escHtml(s) {
  return String(s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function renderFaqs() {
  const list = document.getElementById('faqList');
  const filtered = _currentCat === 'all' ? FAQ_DATA : FAQ_DATA.filter(f => f.category === _currentCat);

  if (filtered.length === 0) {
    list.innerHTML = '<p style="color:#999;text-align:center;padding:32px">目前沒有相關問題。</p>';
    return;
  }

  list.innerHTML = filtered.map(faq => `
    <div class="faq-item">
      <button class="faq-question">
        <span>${escHtml(faq.question)}</span>
        <span class="faq-chevron">▼</span>
      </button>
      <div class="faq-answer">${escHtml(faq.answer)}</div>
    </div>
  `).join('');

  list.querySelectorAll('.faq-question').forEach(btn => {
    btn.addEventListener('click', () => {
      btn.closest('.faq-item').classList.toggle('open');
    });
  });
}

document.querySelectorAll('.faq-filter-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.faq-filter-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    _currentCat = btn.dataset.cat;
    renderFaqs();
  });
});

renderFaqs();

// ── Feedback Modal ────────────────────────────────────────────

let _fbCategory = null;
let _fbRating = 0;

const feedbackBody = document.getElementById('feedbackBody');
const feedbackFooter = document.getElementById('feedbackFooter');
// Save original body HTML before any modifications so we can restore it on close.
const origFbBodyHtml = feedbackBody.innerHTML;

function setupChipListeners() {
  document.querySelectorAll('.fb-radio-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.fb-radio-btn').forEach(b => b.classList.remove('selected'));
      btn.classList.add('selected');
      _fbCategory = btn.dataset.val;
    });
  });
}

function setupStarListeners() {
  document.querySelectorAll('.fb-star').forEach(star => {
    star.addEventListener('click', () => {
      _fbRating = parseInt(star.dataset.val);
      document.querySelectorAll('.fb-star').forEach(s => {
        s.classList.toggle('lit', parseInt(s.dataset.val) <= _fbRating);
      });
    });
  });
}

document.getElementById('btnOpenFeedback').addEventListener('click', () => {
  document.getElementById('feedbackOverlay').classList.add('visible');
});

document.getElementById('btnCloseFeedback').addEventListener('click', closeFeedback);
document.getElementById('btnCancelFeedback').addEventListener('click', closeFeedback);

function closeFeedback() {
  document.getElementById('feedbackOverlay').classList.remove('visible');
  // Restore original form body so next open shows the form, not thank-you message.
  feedbackBody.innerHTML = origFbBodyHtml;
  feedbackFooter.style.display = '';
  _fbCategory = null;
  _fbRating = 0;
  // Re-attach event listeners since innerHTML was replaced.
  setupChipListeners();
  setupStarListeners();
  // Re-fill email if user is logged in.
  if (_userEmail) {
    const emailEl = document.getElementById('fbEmail');
    if (emailEl) emailEl.value = _userEmail;
  }
}

setupChipListeners();
setupStarListeners();

document.getElementById('btnSubmitFeedback').addEventListener('click', async () => {
  const content = document.getElementById('fbContent').value.trim();
  const email = document.getElementById('fbEmail').value.trim();
  const errEl = document.getElementById('fbError');
  errEl.style.display = 'none';

  if (!_fbCategory) { errEl.textContent = '請選擇問題類別'; errEl.style.display = 'block'; return; }
  if (!_fbRating) { errEl.textContent = '請給予評分'; errEl.style.display = 'block'; return; }
  if (content.length < 10) { errEl.textContent = '說明至少需要 10 個字元'; errEl.style.display = 'block'; return; }

  let screenshot = '';
  const fileInput = document.getElementById('fbScreenshot');
  if (fileInput.files[0]) {
    try { screenshot = await compressAndEncode(fileInput.files[0]); }
    catch (e) { errEl.textContent = '截圖處理失敗'; errEl.style.display = 'block'; return; }
  }

  const btn = document.getElementById('btnSubmitFeedback');
  btn.disabled = true; btn.textContent = '送出中...';

  try {
    const resp = await fetch('/api/help/feedback', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ category: _fbCategory, rating: _fbRating, content, email, screenshot }),
    });
    if (resp.ok) {
      document.getElementById('feedbackBody').innerHTML = '<div class="fb-success">感謝您的回饋！</div>';
      document.getElementById('feedbackFooter').style.display = 'none';
      setTimeout(closeFeedback, 2000);
    } else {
      const err = await resp.json().catch(() => ({}));
      errEl.textContent = err.error || '送出失敗，請稍後再試';
      errEl.style.display = 'block';
    }
  } catch (e) {
    errEl.textContent = '網路錯誤，請稍後再試';
    errEl.style.display = 'block';
  } finally {
    btn.disabled = false; btn.textContent = '送出意見回饋';
  }
});

// compressAndEncode() is provided by feedback-utils.js
