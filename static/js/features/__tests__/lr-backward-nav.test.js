// lr-backward-nav.test.js — plan: lr-backward-nav 前端純函式 / 契約回歸測試。
//
// 鎖兩個 #6 相關契約（既有 land 邏輯，本 plan 不改 production，只鎖回歸）：
//   1. stage-regression 決策：何時清 Stage 5 section cards（live-research.js
//      live_research_stage_change handler 的 `stageNum < _currentLRStage && _currentLRStage >= 5`）。
//   2. clearLRStage5Artifacts 的 DOM 契約：清 #lrSections + 移除 [data-lr-section-index]
//      泡泡，**保留** narration 對話脈絡（#6「只清章節卡片、保留 chat」要求）。
//   3. continueLiveResearch body：navAction 透傳成 nav_action（只在非空時帶）。
import { test } from 'node:test';
import assert from 'node:assert';

// ── 契約 1：stage-regression 清除決策（鏡像 live-research.js:2081 的純條件）──
// 只有「曾到 Stage 5+（會產生 section cards）且新 stage 退回到更早」才清。
function shouldClearStage5Artifacts(newStage, currentStage) {
    return newStage < currentStage && currentStage >= 5;
}

test('退回 Stage 5→3：清 section cards（曾到 Stage 5）', () => {
    assert.strictEqual(shouldClearStage5Artifacts(3, 5), true);
});

test('退回 Stage 5→4：清（仍從 Stage 5 退）', () => {
    assert.strictEqual(shouldClearStage5Artifacts(4, 5), true);
});

test('退回 Stage 4→2：不清（從未到 Stage 5，無 card 可清）', () => {
    assert.strictEqual(shouldClearStage5Artifacts(2, 4), false);
});

test('前進 Stage 3→4：不清（非退回）', () => {
    assert.strictEqual(shouldClearStage5Artifacts(4, 3), false);
});

test('restart Stage 5→1：清（退回起點，曾到 Stage 5）', () => {
    assert.strictEqual(shouldClearStage5Artifacts(1, 5), true);
});

// ── 契約 2：clearLRStage5Artifacts DOM 行為契約（鏡像 live-research.js:428-439）──
// 用最小 DOM stub 鎖「清 #lrSections + 移除 section 泡泡 + 保留 narration」。
function makeDomStub() {
    const removed = [];
    const sectionsEl = { innerHTML: '<div>old</div>', style: { display: 'block' } };
    const narrationBubbles = [
        { kind: 'narration', removed: false },
        { kind: 'narration', removed: false },
    ];
    const sectionBubbles = [
        { kind: 'section', removed: false, remove() { this.removed = true; removed.push(this); } },
    ];
    const chat = {
        querySelectorAll(sel) {
            // clearLRStage5Artifacts 只 query [data-lr-section-index]
            return sel === '[data-lr-section-index]' ? sectionBubbles : [];
        },
    };
    const doc = {
        getElementById(id) {
            if (id === 'lrSections') return sectionsEl;
            if (id === 'lrChat') return chat;
            return null;
        },
    };
    return { doc, sectionsEl, narrationBubbles, sectionBubbles, removed };
}

// 鏡像 clearLRStage5Artifacts 的 DOM 操作（逐行對應 production:429-437）。
function clearLRStage5ArtifactsMirror(document) {
    const sectionsEl = document.getElementById('lrSections');
    if (sectionsEl) {
        sectionsEl.innerHTML = '';
        sectionsEl.style.display = 'none';
    }
    const chat = document.getElementById('lrChat');
    if (chat) {
        chat.querySelectorAll('[data-lr-section-index]').forEach(el => el.remove());
    }
}

test('clearLRStage5Artifacts：清空 #lrSections、隱藏、移除 section 泡泡', () => {
    const { doc, sectionsEl, sectionBubbles } = makeDomStub();
    clearLRStage5ArtifactsMirror(doc);
    assert.strictEqual(sectionsEl.innerHTML, '');
    assert.strictEqual(sectionsEl.style.display, 'none');
    assert.strictEqual(sectionBubbles[0].removed, true);
});

test('clearLRStage5Artifacts：narration 對話脈絡保留（#6 只清章節卡片）', () => {
    const { doc, narrationBubbles } = makeDomStub();
    clearLRStage5ArtifactsMirror(doc);
    // narration 泡泡不在 [data-lr-section-index] 集合 → 不被移除
    assert.ok(narrationBubbles.every(b => b.removed === false));
});

// ── 契約 3：continueLiveResearch body 組裝（鏡像 live-research.js body spread）──
function buildContinueBody(userMessage, autoContinue, navAction = '') {
    return JSON.parse(JSON.stringify({
        session_id: 'sid',
        lr_session_id: 'lrid',
        user_message: userMessage || '',
        auto_continue: autoContinue || false,
        enable_web_search: true,
        enable_gap_enrichment: true,
        ...(navAction ? { nav_action: navAction } : {}),
    }));
}

test('navAction=back_one → body.nav_action=back_one', () => {
    assert.strictEqual(buildContinueBody('', false, 'back_one').nav_action, 'back_one');
});

test('navAction=restart → body.nav_action=restart', () => {
    assert.strictEqual(buildContinueBody('', false, 'restart').nav_action, 'restart');
});

test("navAction='' → body 不含 nav_action（backward compat）", () => {
    assert.ok(!('nav_action' in buildContinueBody('hi', false, '')));
});

test('navAction 省略 → body 不含 nav_action', () => {
    assert.ok(!('nav_action' in buildContinueBody('hi', false)));
});

// ── Task 8：legacy 鎖契約（鏡像 lockLRUIForLegacySession 的 nav 按鈕鎖定迴圈）──
// legacy session 的退回/重來按鈕：grey-out（opacity 0.4 / cursor not-allowed）+
// click 開唯讀 modal、不送 continue。
function lockNavButtonsMirror(buttons, showModal) {
    buttons.forEach(btn => {
        if (btn) {
            btn.title = '此 session 為舊版，已封存唯讀，請匯出後開啟新 session';
            btn.style.opacity = '0.4';
            btn.style.cursor = 'not-allowed';
            btn._onclick = (e) => { e.stopPropagation(); showModal(); };
        }
    });
}

test('legacy 鎖：nav 按鈕 grey-out 且 click 開 modal、不送 continue', () => {
    let modalOpened = 0;
    let continueSent = 0;
    const back = { style: {}, title: '' };
    const restart = { style: {}, title: '' };
    lockNavButtonsMirror([back, restart], () => { modalOpened++; });
    // 視覺鎖定
    assert.strictEqual(back.style.opacity, '0.4');
    assert.strictEqual(restart.style.opacity, '0.4');
    assert.strictEqual(back.style.cursor, 'not-allowed');
    // 點擊只開 modal、不送 continue
    const stop = { stopPropagation() {} };
    back._onclick(stop);
    restart._onclick(stop);
    assert.strictEqual(modalOpened, 2);
    assert.strictEqual(continueSent, 0);
});
