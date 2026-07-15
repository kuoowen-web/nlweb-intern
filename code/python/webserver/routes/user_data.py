# Copyright (c) 2025 Microsoft Corporation.
# Licensed under the MIT License

"""
API routes for user-uploaded files and private knowledge base.
"""

import json
import asyncio
import traceback
from typing import Dict, Any
from aiohttp import web
from core.user_data_manager import get_user_data_manager
from core.user_data_processor import get_user_data_processor
from retrieval_providers.user_postgres_provider import get_user_postgres_provider
from misc.logger.logging_config_helper import get_configured_logger

logger = get_configured_logger("user_data_routes")

# Store active processing tasks
active_tasks = {}


# -- Quota helpers ---------------------------------------------------------

async def _check_storage_quota(org_id: str, new_file_bytes: int) -> Dict[str, Any]:
    """Check whether an org has enough storage quota for a new upload.

    Returns dict with keys: allowed (bool), reason (str), warn (bool).
    Logs a warning when usage exceeds 80% of the limit.
    Falls back to allowed=True on any DB error (fail-open for quota).
    """
    try:
        from auth.auth_db import AuthDB
        db = AuthDB.get_instance()

        org = await db.fetchone(
            "SELECT storage_quota_gb FROM organizations WHERE id = ?",
            (org_id,)
        )
        if not org or org['storage_quota_gb'] is None:
            return {'allowed': True, 'warn': False, 'reason': ''}

        quota_bytes = org['storage_quota_gb'] * 1024 ** 3

        # Sum current storage for this org
        rows = await db.fetchall(
            "SELECT COALESCE(SUM(s.size_bytes), 0) AS used "
            "FROM user_sources s "
            "WHERE s.org_id = ? AND s.status != 'failed'",
            (org_id,)
        )
        used_bytes = rows[0]['used'] if rows else 0

        projected = used_bytes + new_file_bytes
        if projected > quota_bytes:
            return {
                'allowed': False,
                'warn': False,
                'reason': f'組織儲存空間已達上限（{org["storage_quota_gb"]} GB），無法繼續上傳。',
            }

        usage_pct = projected / quota_bytes * 100
        if usage_pct >= 80:
            logger.warning(f"Org {org_id} storage at {usage_pct:.1f}% of quota")
            return {'allowed': True, 'warn': True, 'reason': ''}

        return {'allowed': True, 'warn': False, 'reason': ''}

    except Exception as e:
        logger.warning(f"Storage quota check failed (fail-open): {e}")
        return {'allowed': True, 'warn': False, 'reason': ''}


async def upload_file_handler(request: web.Request) -> web.Response:
    """
    Handle file upload requests.

    POST /api/user/upload
    Content-Type: multipart/form-data

    Form fields:
        - file: The file to upload

    Returns:
        JSON response with source_id and status
    """
    try:
        # Get user_id from authenticated session
        user_info = request.get('user', {})
        user_id = user_info.get('id')
        if not user_id:
            return web.json_response(
                {'error': 'Authentication required'},
                status=401
            )

        reader = await request.multipart()

        file_data = None
        filename = None

        # Read multipart fields - must read data immediately as fields are consumed during iteration
        async for field in reader:
            if field.name == 'file':
                filename = field.filename
                file_data = await field.read()  # Read immediately, field is consumed after iteration

        if not file_data or not filename:
            return web.json_response(
                {'error': 'No file uploaded'},
                status=400
            )

        file_size = len(file_data)
        org_id = user_info.get('org_id')

        logger.info(f"Received file upload: {filename} ({file_size} bytes) from user: {user_id}, org: {org_id}")

        # Get manager instance
        manager = get_user_data_manager()

        # Validate file
        validation = await manager.validate_file(filename, file_size, user_id)
        if not validation['valid']:
            return web.json_response(
                {'error': validation['error']},
                status=400
            )

        # Quota check: org storage limit
        if org_id:
            quota_result = await _check_storage_quota(org_id, file_size)
            if not quota_result['allowed']:
                return web.json_response(
                    {'error': quota_result['reason'], 'type': 'quota_exceeded'},
                    status=413
                )

        # Create source record
        source_id = await manager.create_source(user_id, filename, file_size, org_id=org_id)

        # Save file to storage
        import io
        file_stream = io.BytesIO(file_data)
        try:
            file_path = manager.save_file(user_id, source_id, file_stream, filename)

            logger.info(f"File uploaded successfully: source_id={source_id}, waiting for SSE connection to start processing")

            # Don't start processing here - let SSE handler start it
            # This prevents duplicate processing when frontend connects to SSE

            return web.json_response({
                'success': True,
                'source_id': source_id,
                'filename': filename,
                'size_bytes': file_size,
                'status': 'uploaded',
                'message': '檔案上傳成功，正在準備處理。'
            })

        except Exception as e:
            logger.exception(f"Failed to save file: {str(e)}")
            await manager.update_source_status(source_id, 'failed', str(e))
            return web.json_response(
                {'error': f'Failed to save file: {str(e)}'},
                status=500
            )

    except Exception as e:
        logger.exception(f"Upload handler error: {str(e)}")
        return web.json_response(
            {'error': f'Internal server error: {str(e)}'},
            status=500
        )


async def upload_progress_sse_handler(request: web.Request) -> web.StreamResponse:
    """
    SSE endpoint for tracking upload processing progress.

    GET /api/user/upload/{source_id}/progress

    Returns:
        Server-Sent Events stream with progress updates
    """
    try:
        source_id = request.match_info.get('source_id')
        user_info = request.get('user', {})
        user_id = user_info.get('id')
        org_id = user_info.get('org_id')

        if not user_id or not source_id:
            return web.json_response(
                {'error': 'Authentication and source_id are required'},
                status=400
            )

        logger.info(f"SSE progress stream requested: source_id={source_id}, user_id={user_id}")

        # Setup SSE response
        response = web.StreamResponse()
        response.headers['Content-Type'] = 'text/event-stream'
        response.headers['Cache-Control'] = 'no-cache'
        response.headers['Connection'] = 'keep-alive'
        await response.prepare(request)

        # Progress callback
        async def send_progress(progress: int, status: str, message: str):
            """Send progress event to client."""
            data = {
                'progress': progress,
                'status': status,
                'message': message
            }
            event_data = f"data: {json.dumps(data)}\n\n"
            await response.write(event_data.encode('utf-8'))

        # Check if already processing to prevent duplicate processing
        if source_id in active_tasks and not active_tasks[source_id].done():
            logger.warning(f"Processing already in progress for source_id={source_id}")
            await send_progress(0, 'failed', '文件正在處理中，請勿重複請求')
            await response.write_eof()
            return response

        # Check if already processed (status is ready or failed)
        manager = get_user_data_manager()
        org_id = user_info.get('org_id')
        sources = await manager.list_user_sources(user_id, org_id=org_id)
        source = next((s for s in sources if s['source_id'] == source_id), None)
        if source and source['status'] in ['ready', 'failed']:
            logger.warning(f"Source already processed: source_id={source_id}, status={source['status']}")
            if source['status'] == 'ready':
                await send_progress(100, 'completed', '文件已處理完成')
            else:
                await send_progress(0, 'failed', f"文件處理失敗: {source.get('error_message', '未知錯誤')}")
            await response.write_eof()
            return response

        # Start processing with progress callback
        processor = get_user_data_processor()

        # Create a wrapper to handle async callback
        progress_queue = asyncio.Queue()

        def progress_callback(progress, status, message):
            asyncio.create_task(progress_queue.put((progress, status, message)))

        # Start processing
        processing_task = asyncio.create_task(
            processor.process_file(user_id, source_id, progress_callback, org_id=org_id)
        )
        active_tasks[source_id] = processing_task

        # Stream progress updates
        try:
            while not processing_task.done():
                try:
                    progress, status, message = await asyncio.wait_for(
                        progress_queue.get(),
                        timeout=1.0
                    )
                    await send_progress(progress, status, message)

                    # If completed or failed, break
                    if status in ['completed', 'failed']:
                        break

                except asyncio.TimeoutError:
                    # Send keepalive
                    await response.write(b": keepalive\n\n")

            # Wait for final result
            result = await processing_task

            # Send final status
            if result['success']:
                await send_progress(100, 'completed', '處理完成！')
            else:
                await send_progress(0, 'failed', f"處理失敗: {result.get('error', '未知錯誤')}")

        finally:
            # Clean up active_tasks to prevent memory leak
            if source_id in active_tasks:
                del active_tasks[source_id]
            await response.write_eof()

        return response

    except Exception as e:
        logger.exception(f"SSE progress error: {str(e)}")
        return web.json_response(
            {'error': f'Internal server error: {str(e)}'},
            status=500
        )


async def list_sources_handler(request: web.Request) -> web.Response:
    """
    List all sources for a user.

    GET /api/user/sources

    Returns:
        JSON array of source objects
    """
    try:
        user_info = request.get('user', {})
        user_id = user_info.get('id')

        if not user_id:
            return web.json_response(
                {'error': 'Authentication required'},
                status=401
            )

        # Get manager instance
        manager = get_user_data_manager()

        # List user sources (org-isolated if org_id present)
        org_id = user_info.get('org_id')
        sources = await manager.list_user_sources(user_id, org_id=org_id)

        return web.json_response({
            'success': True,
            'sources': sources
        })

    except Exception as e:
        logger.exception(f"List sources error: {str(e)}")
        return web.json_response(
            {'error': f'Internal server error: {str(e)}'},
            status=500
        )


async def delete_source_handler(request: web.Request) -> web.Response:
    """
    Delete a source and all its associated data.

    DELETE /api/user/sources/{source_id}

    Returns:
        JSON response with success status
    """
    try:
        # Get source_id from path
        source_id = request.match_info.get('source_id')

        user_info = request.get('user', {})
        user_id = user_info.get('id')

        if not user_id:
            return web.json_response(
                {'error': 'Authentication required'},
                status=401
            )

        if not source_id:
            return web.json_response(
                {'error': 'source_id is required'},
                status=400
            )

        # Get manager instance
        manager = get_user_data_manager()

        # Delete source (org-isolated if org_id present)
        org_id = user_info.get('org_id')
        success = await manager.delete_source(user_id, source_id, org_id=org_id)

        if success:
            # Cancel active task if exists
            if source_id in active_tasks:
                active_tasks[source_id].cancel()
                del active_tasks[source_id]

            # Clean up PostgreSQL chunks (best-effort, don't block deletion)
            try:
                provider = get_user_postgres_provider()
                deleted_count = await provider.delete_source_vectors(source_id)
                logger.info(f"Cleaned up {deleted_count} PG chunks for source_id={source_id}")
            except Exception as e:
                logger.warning(f"Failed to clean up PG chunks for source_id={source_id}: {str(e)}")

            return web.json_response({
                'success': True,
                'message': '資料來源已成功刪除。'
            })
        else:
            return web.json_response(
                {'error': '找不到此資料來源，或您沒有刪除權限。'},
                status=404
            )

    except Exception as e:
        logger.exception(f"Delete source error: {str(e)}")
        return web.json_response(
            {'error': f'Internal server error: {str(e)}'},
            status=500
        )


async def get_source_status_handler(request: web.Request) -> web.Response:
    """
    Get the processing status of a source.

    GET /api/user/sources/{source_id}/status

    Returns:
        JSON response with source status
    """
    try:
        # Get source_id from path
        source_id = request.match_info.get('source_id')

        user_info = request.get('user', {})
        user_id = user_info.get('id')

        if not user_id:
            return web.json_response(
                {'error': 'Authentication required'},
                status=401
            )

        if not source_id:
            return web.json_response(
                {'error': 'source_id is required'},
                status=400
            )

        # Get manager instance
        manager = get_user_data_manager()

        # Get source info (org-isolated)
        org_id = user_info.get('org_id')
        sources = await manager.list_user_sources(user_id, org_id=org_id)
        source = next((s for s in sources if s['source_id'] == source_id), None)

        if not source:
            return web.json_response(
                {'error': 'Source not found'},
                status=404
            )

        return web.json_response({
            'success': True,
            'source': source
        })

    except Exception as e:
        logger.exception(f"Get source status error: {str(e)}")
        return web.json_response(
            {'error': f'Internal server error: {str(e)}'},
            status=500
        )


def setup_user_data_routes(app: web.Application):
    """Register user data routes."""
    app.router.add_post('/api/user/upload', upload_file_handler)
    app.router.add_get('/api/user/upload/{source_id}/progress', upload_progress_sse_handler)
    app.router.add_get('/api/user/sources', list_sources_handler)
    app.router.add_delete('/api/user/sources/{source_id}', delete_source_handler)
    app.router.add_get('/api/user/sources/{source_id}/status', get_source_status_handler)

    logger.info("User data routes registered")
