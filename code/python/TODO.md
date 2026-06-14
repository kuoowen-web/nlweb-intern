# TODO Items - NLWeb Project

This file tracks incomplete features and technical debt identified during code review.

**Last Updated:** 2025-11-15

---

## High Priority

None currently.

## Medium Priority

### 1. Health Check System - Version Management
**File:** `webserver/routes/health.py:29`
```python
'version': '2.0.0',  # TODO: Get from config or package
```
**Description:** Version number is hardcoded. Should be retrieved from package metadata or config file.
**Impact:** Manual updates required for version changes, risk of stale version info.

### 2. Health Check System - Additional Checks
**File:** `webserver/routes/health.py:55`
```python
# TODO: Add more checks as needed
```
**Description:** Health check system could be expanded with additional service checks.
**Suggestions:**
- Database connectivity check
- Vector database (Qdrant) status
- LLM provider availability
- Memory/disk usage monitoring

## Low Priority

### 3. Chat System - Unread Message Tracking
**Files:**
- `webserver/routes/chat.py:306`
- `webserver/routes/chat.py:442`

```python
'unread_count': 0  # TODO: Implement unread tracking
```
**Description:** Unread message counting is stubbed out, always returns 0.
**Impact:** Users cannot see which conversations have new messages.
**Implementation Notes:** Requires tracking last-read timestamp per conversation per user.

### 4. Chat System - Pagination Total Count
**File:** `webserver/routes/chat.py:311`
```python
'total': len(formatted_conversations),  # TODO: Get actual total from storage
```
**Description:** Pagination total uses filtered results length instead of actual database total.
**Impact:** Pagination may not work correctly if filtering is applied.

---

## Completed Items

None yet.

---

## Notes

- All TODO items should be reviewed quarterly
- High priority items should be addressed within 2-4 weeks
- Medium priority items within 2-3 months
- Low priority items as time permits
