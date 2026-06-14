# API 端點文件

## Chat 系統

### GET /health/chat
取得 Chat 系統健康狀態與設定。

**回應**：
```json
{
  "status": "healthy",
  "connections": 5,
  "conversations": 2,
  "participants_by_conversation": {
    "conv_abc": {"humans": 3, "ai_agents": 1}
  },
  "queue_depths": {"conv_abc": 10},
  "storage": "connected"
}
```

---

## 對話管理

### GET /chat/my-conversations
取得使用者的對話列表。

**Query 參數**：
- `limit`（可選）：回傳數量（預設：20）
- `offset`（可選）：分頁偏移量（預設：0）

**回應**：
```json
{
  "conversations": [
    {
      "id": "conv_abc123",
      "title": "專案討論",
      "site": "all",
      "participantCount": 3,
      "lastMessage": {
        "content": "看起來不錯！",
        "timestamp": "2026-01-15T10:30:00Z"
      },
      "createdAt": "2026-01-15T09:00:00Z",
      "updatedAt": "2026-01-15T10:30:00Z"
    }
  ],
  "total": 42,
  "limit": 20,
  "offset": 0
}
```

### POST /chat/create
建立新對話。

**Request Body**：
```json
{
  "title": "新對話",
  "participants": [
    {
      "user_id": "user_123",
      "name": "Alice"
    }
  ],
  "enable_ai": true
}
```

---

## 網站

### GET /sites?streaming=false
取得可用於搜尋/聊天的網站列表。

**回應**：
```json
{
  "sites": [
    {
      "id": "hackernews",
      "name": "Hacker News",
      "domain": "news.ycombinator.com"
    }
  ]
}
```

---

## WebSocket 連線

### WebSocket /chat/ws/{conv_id}
即時雙向通訊。

**連線 URL**：
```
wss://example.com/chat/ws/conv_abc123
```

### 發送訊息類型

**一般訊息**：
```json
{
  "type": "message",
  "content": "大家好！"
}
```

**正在輸入指示**：
```json
{
  "type": "typing",
  "isTyping": true
}
```

**同步請求**（重連後）：
```json
{
  "type": "sync",
  "lastSequenceId": 42
}
```

### 接收訊息類型

**聊天訊息**：
```json
{
  "type": "message",
  "data": {
    "id": "msg_123",
    "content": "你好！",
    "senderId": "user_123",
    "senderName": "Alice",
    "timestamp": "2026-01-15T10:00:00Z",
    "sequenceId": 43
  }
}
```

**AI 回應**：
```json
{
  "type": "ai_response",
  "data": {
    "id": "ai_msg_456",
    "content": "根據我的搜尋...",
    "responseType": "search_results",
    "metadata": {
      "sources": ["hackernews", "reddit"]
    },
    "timestamp": "2026-01-15T10:01:00Z"
  }
}
```

**參與者更新**：
```json
{
  "type": "participant_update",
  "data": {
    "action": "joined",
    "participant": {
      "id": "user_789",
      "displayName": "Charlie",
      "joinedAt": "2026-01-15T10:05:00Z"
    }
  }
}
```

**錯誤**：
```json
{
  "type": "error",
  "data": {
    "code": "QUEUE_FULL",
    "message": "訊息佇列已滿，請稍候。"
  }
}
```

---

## 認證

所有端點需透過以下方式認證：
- Authorization header 中的 Bearer token
- WebSocket 連線的 sessionStorage token
- localStorage 中的 OAuth 使用者資訊（僅非敏感資料）

---

*更新：2026-01-19*
