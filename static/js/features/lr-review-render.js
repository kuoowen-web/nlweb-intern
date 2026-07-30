/**
 * lr-review-render.js — LR 回顧 fallback renderer 的 pure HTML builder。
 *
 * 抽成獨立 pure 模組（比照 lr-snapshot.js 先例）：無 DOM globals、無 HTTP、無
 * module-load 副作用 → node --test 可直測 schema 欄位映射。fallback renderer 的
 * 高風險區是「shape 猜錯 = 顯示漏欄位 = silent data loss 觀感」，故欄位映射
 * 全部以後端 schema 親讀為準（非樣本推測）：
 *   - ContextMap:          code/python/reasoning/schemas_live.py:266
 *     （Topic :203 / Relation :218 / SearchSeed :233）
 *   - StyleAnalysisOutput: code/python/reasoning/schemas_live.py:1060（Feature :1052）
 *   - UserVoice:           code/python/reasoning/live_research/stage_state.py:23（to_dict :63）
 * 未知欄位（未來 schema 新增）一律落「其他欄位」<pre> 殘塊 — 不 silent drop。
 * 消費端：live-research.js renderLRContextMap / renderLRStyleFeatures /
 * loadLRStageReview / renderLRStageDialog（banner 共用）。
 */
import { escapeHtmlAttr } from './text-fragment.js';

// text 內容跳脫：escapeHtmlAttr 是嚴格超集（多跳脫引號，對 text node 無害），
// 且為既有 pure 模組 export（plain node 可載，已驗）— 不重複造 escape。
const esc = escapeHtmlAttr;

// snapshot 路（renderLRStageDialog）既有 banner 文字，逐字保留。
export const LR_REVIEW_BANNER_TEXT =
    '階段回顧（唯讀）— 以下為該階段已存檔的對話紀錄。'
    + '點選階段圓點僅供回顧，不會退回階段、也不會變更研究進度或消耗運算。';

// fallback 路（loadLRStageReview，無對話快照）— 同唯讀語意 + 明說降級來源。
export const LR_REVIEW_FALLBACK_BANNER_TEXT =
    '階段回顧（唯讀）— 此 session 沒有可重播的對話快照，'
    + '以下改以該階段已存檔的結構化研究資料回顧。'
    + '點選階段圓點僅供回顧，不會退回階段、也不會變更研究進度或消耗運算。';

/**
 * 唯讀 banner HTML（snapshot 路與 fallback 路共用；body 過 escape）。
 * @param {string} [bodyText]  預設 = snapshot 路文字
 */
export function lrReviewBannerHTML(bodyText = LR_REVIEW_BANNER_TEXT) {
    return `<div class="lr-review-banner">${esc(String(bodyText))}</div>`;
}

// ── enum → 中文標籤（schemas_live.py Literal 全值盤點；未知值原樣顯示不丟）──
const RELEVANCE_LABELS = { core: '核心', supporting: '輔助', peripheral: '周邊' };
const LEVEL_LABELS = { high: '高', medium: '中', low: '低' };
const RELATION_TYPE_LABELS = {
    causes: '導致', enables: '促成', prevents: '阻止', contradicts: '矛盾',
    supports: '支持', part_of: '屬於', precedes: '先於', analogous_to: '類比',
};
const SEED_STATUS_LABELS = { pending: '待執行', executed: '已執行', exhausted: '已窮盡' };
const SEED_SOURCE_LABELS = { internal: '站內', web: '網路', both: '站內+網路' };
const CITATION_FORMAT_LABELS = {
    author_year: '作者-年份（APA 風格）', numeric: '數字編號 [N]',
    footnote: '腳註編號', none: '不使用引用標記',
};

function labelOf(map, v) {
    return map[v] || String(v == null ? '' : v);
}

// 已知欄位以外的 residual → 「其他欄位」<pre> 殘塊（不 silent drop）。
function unknownFieldsBlockHTML(obj, knownKeys) {
    const residual = {};
    for (const k of Object.keys(obj)) {
        if (!knownKeys.has(k)) residual[k] = obj[k];
    }
    if (Object.keys(residual).length === 0) return '';
    return `<div class="lr-review-block"><h4>其他欄位</h4>`
        + `<pre class="lr-review-json">${esc(JSON.stringify(residual, null, 2))}</pre></div>`;
}

// ── ContextMap ──────────────────────────────────────────────────────────────
// 已知欄位集合：集合內由人類可讀區塊渲染（revision_history / created_at /
// last_refined_at 刻意彙總為 meta 行）；集合外落「其他欄位」殘塊。
const CONTEXT_MAP_KNOWN_KEYS = new Set([
    'research_question', 'working_hypothesis', 'topics', 'relations',
    'followup_questions', 'search_seeds', 'version', 'revision_history',
    'created_at', 'last_refined_at',
]);

/**
 * context_map（已 parse 物件）→ 人類可讀 HTML。
 * cm 為 null / 非 plain object → 回 ''（呼叫端沿用既有空狀態處理）。
 */
export function lrContextMapHTML(cm) {
    if (!cm || typeof cm !== 'object' || Array.isArray(cm)) return '';
    const parts = [];
    if (cm.research_question) {
        parts.push(`<div class="lr-review-block"><h4>研究問題</h4><p>${esc(String(cm.research_question))}</p></div>`);
    }
    if (cm.working_hypothesis) {
        parts.push(`<div class="lr-review-block"><h4>工作假設</h4><p>${esc(String(cm.working_hypothesis))}</p></div>`);
    }
    const topics = Array.isArray(cm.topics) ? cm.topics : [];
    const topicNameById = new Map();
    topics.forEach(t => { if (t && t.topic_id) topicNameById.set(t.topic_id, String(t.name || t.topic_id)); });
    if (topics.length) {
        const items = topics.map(t => {
            const name = esc(String((t && t.name) || '(未命名議題)'));
            const meta = [
                (t && t.domain) ? `領域：${String(t.domain)}` : '',
                (t && t.relevance) ? `定位：${labelOf(RELEVANCE_LABELS, t.relevance)}` : '',
                (t && t.confidence) ? `信心：${labelOf(LEVEL_LABELS, t.confidence)}` : '',
                (t && Array.isArray(t.evidence_ids) && t.evidence_ids.length) ? `證據 ${t.evidence_ids.length} 筆` : '',
            ].filter(Boolean).join('・');
            const desc = (t && t.description) ? `<div class="lr-outline-desc">${esc(String(t.description))}</div>` : '';
            return `<li><strong>${name}</strong>${meta ? `<div class="lr-outline-desc">${esc(meta)}</div>` : ''}${desc}</li>`;
        }).join('');
        parts.push(`<div class="lr-review-block"><h4>研究議題（${topics.length}）</h4><ul>${items}</ul></div>`);
    }
    const relations = Array.isArray(cm.relations) ? cm.relations : [];
    if (relations.length) {
        const items = relations.map(r => {
            const src = esc(topicNameById.get(r && r.source_topic_id) || String((r && r.source_topic_id) || '?'));
            const tgt = esc(topicNameById.get(r && r.target_topic_id) || String((r && r.target_topic_id) || '?'));
            const type = esc(labelOf(RELATION_TYPE_LABELS, r && r.relation_type));
            const desc = (r && r.description) ? `<div class="lr-outline-desc">${esc(String(r.description))}</div>` : '';
            return `<li><strong>${src}</strong> —${type}→ <strong>${tgt}</strong>${desc}</li>`;
        }).join('');
        parts.push(`<div class="lr-review-block"><h4>議題關聯（${relations.length}）</h4><ul>${items}</ul></div>`);
    }
    const followups = Array.isArray(cm.followup_questions) ? cm.followup_questions : [];
    if (followups.length) {
        const items = followups.map(q => `<li>${esc(String(q))}</li>`).join('');
        parts.push(`<div class="lr-review-block"><h4>後續問題（${followups.length}）</h4><ul>${items}</ul></div>`);
    }
    const seeds = Array.isArray(cm.search_seeds) ? cm.search_seeds : [];
    if (seeds.length) {
        const items = seeds.map(sd => {
            const q = esc(String((sd && sd.query) || ''));
            const meta = [
                (sd && sd.status) ? labelOf(SEED_STATUS_LABELS, sd.status) : '',
                (sd && sd.source_strategy) ? `來源：${labelOf(SEED_SOURCE_LABELS, sd.source_strategy)}` : '',
                (sd && sd.priority) ? `優先度：${labelOf(LEVEL_LABELS, sd.priority)}` : '',
                (sd && sd.target_topic_id && topicNameById.get(sd.target_topic_id)) ? `議題：${topicNameById.get(sd.target_topic_id)}` : '',
            ].filter(Boolean).join('・');
            const rationale = (sd && sd.rationale) ? `<div class="lr-outline-desc">${esc(String(sd.rationale))}</div>` : '';
            return `<li><strong>${q}</strong>${meta ? `<div class="lr-outline-desc">${esc(meta)}</div>` : ''}${rationale}</li>`;
        }).join('');
        parts.push(`<div class="lr-review-block"><h4>搜尋計畫（${seeds.length}）</h4><ul>${items}</ul></div>`);
    }
    const metaBits = [
        Number.isInteger(cm.version) ? `結構版本 ${cm.version}` : '',
        (Array.isArray(cm.revision_history) && cm.revision_history.length) ? `精煉紀錄 ${cm.revision_history.length} 筆` : '',
    ].filter(Boolean).join('・');
    if (metaBits) {
        parts.push(`<div class="lr-review-block lr-outline-desc">${esc(metaBits)}</div>`);
    }
    parts.push(unknownFieldsBlockHTML(cm, CONTEXT_MAP_KNOWN_KEYS));
    return parts.join('');
}

// ── StyleAnalysisOutput + UserVoice ─────────────────────────────────────────
// input_is_writing_sample 為內部 LLM 判定訊號，刻意不渲染也不入殘塊
// （lessons：internal param 不應出現在 user-facing copy）。
const STYLE_FEATURES_KNOWN_KEYS = new Set([
    'features', 'overall_tone', 'sample_quality_note', 'citation_format',
    'input_is_writing_sample',
]);
const USER_VOICE_KNOWN_KEYS = new Set([
    'citation_style', 'target_word_count', 'stage2_feedback', 'revise_instructions',
]);

/**
 * style_features（已 parse StyleAnalysisOutput）+ user_voice（UserVoice.to_dict）
 * → 人類可讀 HTML。兩者皆空 → ''（呼叫端 fallback empty notice）。
 */
export function lrStyleFeaturesHTML(sf, voice) {
    const parts = [];
    if (sf && typeof sf === 'object' && !Array.isArray(sf)) {
        const features = Array.isArray(sf.features) ? sf.features : [];
        if (features.length) {
            const items = features.map(f => {
                const dim = esc(String((f && f.dimension) || '(未命名面向)'));
                const obs = (f && f.observation) ? `<div class="lr-outline-desc">觀察：${esc(String(f.observation))}</div>` : '';
                const ins = (f && f.instruction) ? `<div class="lr-outline-desc">寫作指令：${esc(String(f.instruction))}</div>` : '';
                return `<li><strong>${dim}</strong>${obs}${ins}</li>`;
            }).join('');
            parts.push(`<div class="lr-review-block"><h4>文筆特徵（${features.length}）</h4><ul>${items}</ul></div>`);
        }
        const kv = [
            sf.overall_tone ? `<li><strong>整體語氣</strong>：${esc(String(sf.overall_tone))}</li>` : '',
            sf.citation_format ? `<li><strong>引用格式</strong>：${esc(labelOf(CITATION_FORMAT_LABELS, sf.citation_format))}</li>` : '',
            sf.sample_quality_note ? `<li><strong>範本品質備註</strong>：${esc(String(sf.sample_quality_note))}</li>` : '',
        ].filter(Boolean).join('');
        if (kv) parts.push(`<div class="lr-review-block"><h4>風格設定</h4><ul>${kv}</ul></div>`);
        parts.push(unknownFieldsBlockHTML(sf, STYLE_FEATURES_KNOWN_KEYS));
    }
    if (voice && typeof voice === 'object' && !Array.isArray(voice)) {
        const rows = [];
        if (voice.citation_style) {
            rows.push(`<li><strong>使用者拍板引用格式</strong>：${esc(labelOf(CITATION_FORMAT_LABELS, voice.citation_style))}</li>`);
        }
        if (voice.target_word_count != null) {
            rows.push(`<li><strong>目標字數</strong>：${esc(String(voice.target_word_count))}</li>`);
        }
        const fb = Array.isArray(voice.stage2_feedback) ? voice.stage2_feedback : [];
        if (fb.length) {
            const items = fb.map(e => `<li>${esc(String((e && e.text) || ''))}</li>`).join('');
            rows.push(`<li><strong>資料蒐集階段回饋（${fb.length}）</strong><ul>${items}</ul></li>`);
        }
        const ri = (voice.revise_instructions && typeof voice.revise_instructions === 'object' && !Array.isArray(voice.revise_instructions))
            ? voice.revise_instructions : {};
        const riKeys = Object.keys(ri);
        if (riKeys.length) {
            const items = riKeys.map(k => {
                const idx = Number(k);
                const label = Number.isInteger(idx) ? `第 ${idx + 1} 段` : `段落 ${k}`;  // 0-based → 1-based
                const list = Array.isArray(ri[k]) ? ri[k] : [ri[k]];
                const inner = list.map(x => `<li>${esc(String(x))}</li>`).join('');
                return `<li><strong>${esc(label)}</strong><ul>${inner}</ul></li>`;
            }).join('');
            rows.push(`<li><strong>段落修訂指示</strong><ul>${items}</ul></li>`);
        }
        if (rows.length) {
            parts.push(`<div class="lr-review-block"><h4>使用者語氣與格式指示</h4><ul>${rows.join('')}</ul></div>`);
        }
        parts.push(unknownFieldsBlockHTML(voice, USER_VOICE_KNOWN_KEYS));
    }
    return parts.join('');
}
