// static/js/features/search-generation.js
//
// SSE 遲到訊息世代閘門（Late-Message Generation Gate）純邏輯。
// 抽獨立檔以便 node:test 單元測試（不拉進 search.js 的 DOM/window 依賴鏈）。
//
// 語意：判斷「某個 inflight SSE stream 捕捉到的 generationToken」是否仍是
//   當前世代。token === current → 當前（放行）；token < current → 已被
//   後續搜尋/切換 supersede（stale 遲到訊息，攔）。
//
// null / undefined token = caller 明確 opt-out（維持修改前的無 gate 放行行為），
//   供保守 caller（如選擇不接線的路徑）使用。0 是合法世代值，必須用嚴格
//   null/undefined 檢查，不可用 falsy。
export function isCurrentGeneration(generationToken, currentGeneration) {
    if (generationToken === null || generationToken === undefined) return true;
    return generationToken === currentGeneration;
}
