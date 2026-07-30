// static/js/features/lr-nav-confirm.js
//
// Pure-function module — NO imports, NO DOM, NO side effects（沿 lr-resume-classify.js
// 慣例，供 node --test 直接載入）。plan: lr-ux-u1u2u3（U3 退階段 confirm modal）。
//
// lrBackNavClearedItems(currentStage)：back_one（退到 currentStage-1）會清除、且該
// checkpoint 時已實際存在的產出（user-facing 字串）。真值以後端 stage_state.py
// reset_to_stage 真值表為準（:528-588；該函式 docstring 有反向指標）：
//   - Stage 5 輸出一律清，但只在 currentStage>=5 才存在 → 只在 5 列。
//   - book_outline_json：target<=4 清 → currentStage 4/5 列（stage 4 起才存在）。
//   - style_features_json + executed_searches：target<=2 清 → currentStage 2/3 列
//     （style 於 stage 3 才存在；executed_searches 於 stage 2 起存在）。
//   - evidence_pool_json / context_map_json 永遠保留 → 不在此清單（固定句由 modal 呈現）。
// ⚠ 改後端 reset_to_stage 清除範圍時，本表 + 測試必同步。

// 與 news-search-prototype.html .lr-stage-labels 逐字一致（modal 文案用）。
export const LR_NAV_STAGE_LABELS = {
    1: '建立結構', 2: '資料蒐集', 3: '寫作準備', 4: '格式確認', 5: '分段輸出', 6: '匯出',
};

export function lrBackNavClearedItems(currentStage) {
    switch (currentStage) {
        case 5: return ['已寫好的章節內容', '章節查證紀錄與知識圖譜', '章節大綱'];
        case 4: return ['章節大綱'];
        case 3: return ['文筆風格設定', '已執行的搜尋紀錄'];
        case 2: return ['已執行的搜尋紀錄'];
        default: return [];   // navAllowed gate（stage 2-5）外：防禦回空
    }
}
