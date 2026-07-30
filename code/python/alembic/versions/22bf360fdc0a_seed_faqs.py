"""seed_faqs

把產品內 help center 的靜態 FAQ（static/js/help.js 的 FAQ_DATA）seed 進 faqs 表，
供公開 /api/faq endpoint 讀取。空表才插（idempotent）：對已有資料的環境 = no-op，
不重複塞。sort_order 依 help.js 陣列順序（0-based），category 直接沿用 help.js 值
（general/search/account/privacy/other，faqs.category 無 CHECK 約束）。

is_published：SQLite 用 1、PG 用 TRUE（沿 auth_db.py 既有型別；本 migration 統一
用 Python bool True 當 param 綁定，psycopg 轉 PG boolean、SQLite 存為 1）。
created_at/updated_at：unix epoch float，同 feedbacks/faqs 既有風格。

Revision ID: 22bf360fdc0a
Revises: 9ebc45a96911
Create Date: 2026-07-24 22:35:54.316700
"""
import time
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '22bf360fdc0a'
down_revision: Union[str, Sequence[str], None] = '9ebc45a96911'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# ── FAQ 內容（鏡射 static/js/help.js FAQ_DATA；executor 執行時逐條對照校正）──
# (question, answer, category)。sort_order = 陣列 index。
_FAQS = [
    ('讀豹是什麼？', '讀豹是一個繁體中文新聞搜尋與分析平台，使用 AI 技術提供自然語言搜尋、深度研究報告等功能，專為知識工作者設計。', 'general'),
    ('讀豹的資料來源有哪些？', '目前收錄以下媒體來源：自由時報、聯合新聞網、中央通訊社、中國時報、環境資訊中心、今周刊 ESG、經濟部能源署，共 7 個可信來源。所有來源經過篩選，確保資訊品質與可信度。', 'general'),
    ('新聞資料多久更新一次？', '更新頻率從每小時到每半天不等，根據內部技術流程及各資料來源的更新頻率決定。', 'general'),
    ('如何搜尋新聞？', '讀豹的一般搜尋採用「混合搜尋」技術，同時結合語意理解與關鍵字匹配。在搜尋框輸入自然語言問句，例如「最近台積電有什麼新聞？」或「AI 對台灣就業有什麼影響？」，按 Enter 即可搜尋。不需要精確關鍵字。', 'search'),
    ('什麼是深度研究（Deep Research）？', '深度研究是類似多條件或進階搜尋的功能。AI 會分析多篇相關報導，產生結構化研究報告，包含論點分析、事實查核、知識圖譜等。適合需要深入了解某議題時使用。', 'search'),
    ('搜尋結果的排序依據是什麼？', '搜尋結果依相關度排序，綜合考量語意相似度、關鍵字匹配、來源可信度、時效性等因素，透過多層排序模型確保結果品質與多元性。', 'search'),
    ('可以搜尋特定時間範圍的新聞嗎？', '可以。你可以在問句中自然地描述時間範圍，例如「上週的半導體新聞」或「2025年12月的環保政策」，系統會自動解析時間條件。', 'search'),
    ('什麼是自由對話模式？', '自由對話讓你針對搜尋結果或深度研究報告進行追問，類似 ChatGPT 的對話體驗，但所有回答都基於已檢索的新聞資料，確保資訊有據可查。', 'search'),
    ('如何註冊帳號？', '讀豹採用邀請制（B2B 模式）。請聯絡我們取得註冊連結，使用該連結即可設定帳號。', 'account'),
    ('忘記密碼怎麼辦？', '在登入頁面點擊「忘記密碼」，輸入註冊時使用的電子郵件，系統會寄送密碼重設連結。已登入的用戶可在左下角設定中變更密碼。', 'account'),
    ('可以在多台裝置上使用嗎？', '可以。登入後對話紀錄會同步，你可以在不同裝置間切換使用。如需登出所有裝置，可在設定中選擇「登出全部裝置」。', 'account'),
    ('什麼是組織功能？', '組織功能讓企業用戶統一管理團隊帳號。組織管理員可以邀請成員、管理權限、查看團隊使用狀況。', 'account'),
    ('如何釘選重要的搜尋結果？', '在搜尋結果卡片上點擊釘選圖示，即可將該結果標記為重要。釘選的內容會保留在當前對話中，方便後續參考。', 'general'),
    ('對話紀錄會保存多久？', '登入後的對話紀錄會永久保存在你的帳號中。你可以隨時在左側邊欄查看歷史對話，點擊即可繼續追問。', 'general'),
    ('可以分享搜尋結果嗎？', '可以。點擊分享按鈕，可以將搜尋結果或深度研究報告匯出，支援複製文字、分享到其他平台等方式。', 'general'),
    ('讀豹如何確保資訊準確性？', '讀豹使用多重機制確保準確性：(1) 只收錄可信媒體來源 (2) AI 回答必須引用原始報導 (3) 深度研究模式有事實查核機制（Critic Agent）(4) 所有引用可追溯至原始新聞。', 'privacy'),
    ('我的搜尋紀錄會被用於其他用途嗎？', '不會。你的搜尋紀錄僅供你個人使用，不會被用於廣告投放、轉售給第三方或其他商業用途。', 'privacy'),
    ('資料儲存在哪裡？', '所有資料儲存在位於歐洲的安全伺服器上，採用加密傳輸（HTTPS）與安全的資料庫存取機制。', 'privacy'),
    ('如何刪除我的帳號和資料？', '請聯絡客服（support@twdubao.com）申請刪除帳號。我們會在確認身分後刪除所有相關資料。', 'privacy'),
    ('讀豹支援哪些瀏覽器？', '建議使用最新版本的 Chrome、Firefox、Safari 或 Edge。需要啟用 JavaScript 和 Cookie。', 'other'),
    ('遇到問題如何回報？', '請使用說明中心的「聯絡客服」頁面送出意見回饋，或直接寄信至 support@twdubao.com。我們會在 2 個工作天內回覆。', 'other'),
]


def upgrade() -> None:
    bind = op.get_bind()

    # 空表 guard：只在 faqs 完全沒資料時 seed（idempotent，重跑 no-op）。
    count = bind.execute(sa.text("SELECT COUNT(*) FROM faqs")).scalar()
    if count and count > 0:
        return

    now = time.time()
    # is_published 用 Python bool True → psycopg 轉 PG boolean、SQLite 存 1（雙 dialect 安全）。
    for sort_order, (question, answer, category) in enumerate(_FAQS):
        bind.execute(
            sa.text(
                "INSERT INTO faqs "
                "(question, answer, category, sort_order, is_published, created_at, updated_at) "
                "VALUES (:q, :a, :c, :so, :pub, :ca, :ua)"
            ),
            {
                'q': question, 'a': answer, 'c': category,
                'so': sort_order, 'pub': True, 'ca': now, 'ua': now,
            },
        )


def downgrade() -> None:
    # 🔧 R1（B-1）：no-op，刻意不刪資料。
    # 原因（三家 AR 同位置抓）：upgrade 對「已有手動資料」的環境走空表 guard = no-op，
    # 但 alembic 仍標本 revision applied。若 downgrade 做 DELETE FROM faqs，會把該環境
    # 「非本 seed 塞入的手動資料」一併刪光（本 plan 明寫未來維護 = admin 手動 SQL 寫入
    # faqs，此情境是設計內預期狀態、非假想）。seed 資料無害留存，不對稱刪除的風險 >
    # 回滾潔癖 → downgrade 不動資料。
    # （Gemini 提的 seed_id 標記欄方案經裁決不採：為可回滾而擴 schema 屬過度工程。）
    pass
