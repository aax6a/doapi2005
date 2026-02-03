import asyncio
import logging
import os
import socket
import base64
import aiohttp
import uvloop
import threading
import re
import tempfile
from datetime import datetime
from typing import Optional, Dict, Any, Tuple
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import (
    JSONResponse, 
    HTMLResponse, 
    Response, 
    StreamingResponse,
    FileResponse
)
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from fastapi import Request, Form
from pyrogram import Client
from pyrogram.raw.functions.stories import (
    GetPeerStories, 
    GetStoriesArchive, 
    GetPinnedStories, 
    GetStoriesByID
)
from pyrogram.raw.types import (
    InputPeerUser, 
    InputPeerChannel,
    InputPeerChat
)
from pyrogram.file_id import FileId, FileType
from config import SESSION_STRING, API_ID, API_HASH
import uvicorn

# ==================== Configuration ====================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('telegram_stories.log', encoding='utf-8')
    ]
)

logger = logging.getLogger(__name__)

# ==================== Global Variables ====================
user_client = None
client_lock = threading.Lock()
DOWNLOAD_TIMEOUT = 300  # 5 minutes timeout
MAX_FILE_SIZE = 100 * 1024 * 1024  # 100MB max file size

# ==================== Templates ====================
templates = Jinja2Templates(directory="templates")

# ==================== Client Management ====================
async def ensure_client() -> bool:
    """Ensure Pyrogram client is started and connected"""
    global user_client
    
    with client_lock:
        if user_client is None:
            try:
                user_client = Client(
                    "telegram_stories_bot",
                    api_id=API_ID,
                    api_hash=API_HASH,
                    session_string=SESSION_STRING,
                    workdir="./sessions",
                    in_memory=False,
                    workers=20
                )
                await user_client.start()
                me = await user_client.get_me()
                logger.info(f"Pyrogram client started successfully as @{me.username}")
                return True
            except Exception as e:
                logger.error(f"Failed to start Pyrogram client: {str(e)}")
                user_client = None
                return False
        
        try:
            # Check if client is connected
            if not user_client.is_connected:
                await user_client.start()
                logger.info("Pyrogram client reconnected successfully")
        except Exception as e:
            logger.error(f"Failed to check/restart client: {str(e)}")
            try:
                await user_client.stop()
                user_client = None
                # Try to recreate client
                user_client = Client(
                    "telegram_stories_bot",
                    api_id=API_ID,
                    api_hash=API_HASH,
                    session_string=SESSION_STRING,
                    workdir="./sessions",
                    in_memory=False,
                    workers=20
                )
                await user_client.start()
                logger.info("Pyrogram client recreated successfully")
                return True
            except Exception as e2:
                logger.error(f"Failed to recreate client: {str(e2)}")
                user_client = None
                return False
        
        return True

def get_local_ip() -> str:
    """Get local IP address"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except:
        return '127.0.0.1'

# ==================== URL Parsing ====================
def parse_story_url(url: str) -> Tuple[Optional[str], Optional[int]]:
    """
    Parse Telegram story URL and extract username/chat_id and story_id
    
    Supports:
    - https://t.me/username/s/123456
    - https://t.me/c/1234567890/123456
    - https://t.me/username/123456 (alternative format)
    """
    
    url_patterns = [
        # Standard format with /s/
        r't\.me/([^/]+)/s/(\d+)',
        r'telegram\.me/([^/]+)/s/(\d+)',
        r'telegram\.dog/([^/]+)/s/(\d+)',
        
        # Channel format
        r't\.me/c/(\d+)/(\d+)',
        r'telegram\.me/c/(\d+)/(\d+)',
        r'telegram\.dog/c/(\d+)/(\d+)',
        
        # Alternative format (without /s/)
        r't\.me/([^/]+)/(\d+)$',
        r'telegram\.me/([^/]+)/(\d+)$',
        r'telegram\.dog/([^/]+)/(\d+)$',
    ]
    
    for pattern in url_patterns:
        match = re.search(pattern, url, re.IGNORECASE)
        if match:
            if 'c/' in pattern:
                # Channel format
                chat_id = match.group(1)
                story_id = int(match.group(2))
                return f"-100{chat_id}", story_id
            else:
                # User or public channel format
                username = match.group(1).lstrip('@')
                story_id = int(match.group(2))
                return username, story_id
    
    return None, None

# ==================== Peer Resolution ====================
async def resolve_peer(identifier: str):
    """Resolve username/channel_id to peer object"""
    try:
        if identifier.startswith('-100'):
            # It's a channel ID
            channel_id = int(identifier[4:])
            # We need to get the channel access hash
            # This is a workaround since we can't directly create InputPeerChannel
            try:
                peer = await user_client.resolve_peer(identifier)
                return peer
            except:
                # Try with @ format
                peer = await user_client.resolve_peer(f"@{identifier}")
                return peer
        else:
            # It's a username
            peer = await user_client.resolve_peer(identifier)
            return peer
            
    except Exception as e:
        logger.error(f"Failed to resolve peer {identifier}: {str(e)}")
        raise HTTPException(
            status_code=400, 
            detail=f"Failed to resolve user/channel: {str(e)}"
        )

# ==================== Story Retrieval ====================
async def get_story_by_id(peer, story_id: int):
    """Get a specific story by ID"""
    try:
        input_peer = await resolve_peer(peer)
        
        # Try to get story directly
        result = await user_client.invoke(
            GetStoriesByID(
                peer=input_peer,
                id=[story_id]
            )
        )
        
        if result and hasattr(result, 'stories') and result.stories:
            return result.stories[0]
        
        # If not found, search in all stories
        return await search_story_in_all_locations(input_peer, story_id)
        
    except Exception as e:
        logger.error(f"Error getting story {story_id}: {str(e)}")
        return None

async def search_story_in_all_locations(peer, story_id: int):
    """Search for story in active, pinned, and archived stories"""
    
    # Check active stories
    try:
        active_result = await user_client.invoke(GetPeerStories(peer=peer))
        if (active_result and hasattr(active_result, 'stories') 
            and active_result.stories.stories):
            for story in active_result.stories.stories:
                if story.id == story_id:
                    return story
    except:
        pass
    
    # Check pinned stories
    try:
        pinned_result = await user_client.invoke(
            GetPinnedStories(peer=peer, offset_id=0, limit=100)
        )
        if pinned_result and hasattr(pinned_result, 'stories'):
            for story in pinned_result.stories:
                if story.id == story_id:
                    return story
    except:
        pass
    
    # Check archived stories
    try:
        offset_id = 0
        while True:
            archive_result = await user_client.invoke(
                GetStoriesArchive(peer=peer, offset_id=offset_id, limit=100)
            )
            
            if not archive_result or not hasattr(archive_result, 'stories'):
                break
            
            for story in archive_result.stories:
                if story.id == story_id:
                    return story
            
            if len(archive_result.stories) < 100:
                break
            
            offset_id = archive_result.stories[-1].id
    except:
        pass
    
    return None

# ==================== Media Download ====================
async def download_story_media(story) -> Tuple[Optional[bytes], Optional[str], Optional[str]]:
    """Download story media and return (bytes, media_type, mime_type)"""
    try:
        if not hasattr(story, 'media'):
            return None, None, None
        
        media = story.media
        media_type = None
        mime_type = None
        
        # Determine media type and prepare download
        if hasattr(media, 'photo'):
            media_type = "photo"
            mime_type = "image/jpeg"
            
            # Download photo
            photo = media.photo
            file_bytes = await user_client.download_media(
                photo,
                in_memory=True,
                file_name="story_photo.jpg"
            )
            
        elif hasattr(media, 'document'):
            doc = media.document
            mime_type = getattr(doc, 'mime_type', 'application/octet-stream')
            
            if mime_type.startswith('video'):
                media_type = "video"
                file_name = "story_video.mp4"
            elif mime_type.startswith('image'):
                media_type = "image"
                file_name = "story_image.jpg"
            else:
                media_type = "document"
                # Try to determine extension from attributes
                for attr in doc.attributes:
                    if hasattr(attr, 'file_name'):
                        file_name = attr.file_name
                        break
                else:
                    file_name = "story_document.bin"
            
            # Download document
            file_bytes = await user_client.download_media(
                doc,
                in_memory=True,
                file_name=file_name
            )
        else:
            return None, None, None
        
        if not file_bytes:
            return None, None, None
        
        # Check file size limit
        if len(file_bytes) > MAX_FILE_SIZE:
            logger.warning(f"File too large: {len(file_bytes)} bytes")
            return None, None, None
        
        return file_bytes, media_type, mime_type
        
    except asyncio.TimeoutError:
        logger.error("Download timeout")
        return None, None, None
    except Exception as e:
        logger.error(f"Error downloading media: {str(e)}")
        return None, None, None

# ==================== Format Helpers ====================
def format_story_info(story, story_type: str = "Unknown") -> Dict[str, Any]:
    """Format story information for JSON response"""
    info = {
        "story_id": story.id,
        "type": story_type,
        "date": datetime.fromtimestamp(story.date).strftime("%Y-%m-%d %H:%M:%S"),
        "timestamp": story.date,
        "has_media": hasattr(story, 'media'),
    }
    
    if hasattr(story, 'caption'):
        info["caption"] = story.caption
    
    # Get media info
    if info["has_media"]:
        media = story.media
        if hasattr(media, 'photo'):
            info["media_type"] = "photo"
        elif hasattr(media, 'document'):
            doc = media.document
            mime_type = getattr(doc, 'mime_type', '')
            if mime_type.startswith('video'):
                info["media_type"] = "video"
            elif mime_type.startswith('image'):
                info["media_type"] = "image"
            else:
                info["media_type"] = "document"
            info["mime_type"] = mime_type
    
    return info

def get_file_extension(media_type: str, mime_type: str = "") -> str:
    """Get appropriate file extension"""
    if media_type == "photo" or media_type == "image":
        return ".jpg"
    elif media_type == "video":
        if "webm" in mime_type.lower():
            return ".webm"
        elif "gif" in mime_type.lower():
            return ".gif"
        else:
            return ".mp4"
    else:
        # Try to extract from mime type
        mime_to_ext = {
            "image/png": ".png",
            "image/gif": ".gif",
            "image/webp": ".webp",
            "image/jpeg": ".jpg",
            "video/mp4": ".mp4",
            "video/quicktime": ".mov",
            "video/x-msvideo": ".avi",
            "application/pdf": ".pdf",
        }
        return mime_to_ext.get(mime_type.lower(), ".bin")

# ==================== API Response Helpers ====================
def success_response(data: Dict[str, Any]) -> JSONResponse:
    """Create successful JSON response"""
    base_data = {
        "success": True,
        "timestamp": datetime.now().isoformat(),
        "api_info": {
            "version": "2.0.0",
            "developer": "@ISmartCoder",
            "channel": "@abirxdhackz"
        }
    }
    base_data.update(data)
    return JSONResponse(content=base_data)

def error_response(message: str, status_code: int = 400) -> JSONResponse:
    """Create error JSON response"""
    return JSONResponse(
        status_code=status_code,
        content={
            "success": False,
            "error": message,
            "timestamp": datetime.now().isoformat(),
            "api_info": {
                "version": "2.0.0",
                "developer": "@ISmartCoder",
                "channel": "@abirxdhackz"
            }
        }
    )

# ==================== FastAPI App ====================
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan manager for FastAPI app"""
    # Startup
    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
    
    logger.info("Starting Telegram Stories API...")
    
    if not await ensure_client():
        logger.error("Failed to initialize Telegram client on startup!")
    else:
        me = await user_client.get_me()
        logger.info(f"Logged in as: @{me.username} (ID: {me.id})")
    
    local_ip = get_local_ip()
    logger.info(f"Server running on: http://{local_ip}:4747")
    logger.info(f"Local access: http://127.0.0.1:4747")
    logger.info(f"Public access: http://0.0.0.0:4747")
    
    yield
    
    # Shutdown
    if user_client:
        logger.info("Stopping Telegram client...")
        await user_client.stop()
        logger.info("Telegram client stopped")

# Create FastAPI app
app = FastAPI(
    title="Telegram Stories Downloader API",
    description="Download Telegram stories (photos and videos) directly",
    version="2.0.0",
    lifespan=lifespan
)

# Mount static files
app.mount("/static", StaticFiles(directory="static"), name="static")

# ==================== API Endpoints ====================
@app.get("/", response_class=HTMLResponse)
async def home_page(request: Request):
    """Home page with web interface"""
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "title": "Telegram Stories Downloader",
            "endpoints": [
                {"name": "Check Story", "path": "/api/check"},
                {"name": "Download Story", "path": "/api/download"},
                {"name": "Get Story Info", "path": "/api/info"},
                {"name": "Base64 Data", "path": "/api/base64"},
                {"name": "Direct URL", "path": "/api/direct"}
            ]
        }
    )

@app.post("/", response_class=HTMLResponse)
async def process_url(request: Request, story_url: str = Form(...)):
    """Process URL from web form"""
    username, story_id = parse_story_url(story_url)
    
    if not username or not story_id:
        return templates.TemplateResponse(
            "index.html",
            {
                "request": request,
                "error": "Invalid Telegram story URL format!",
                "title": "Telegram Stories Downloader"
            }
        )
    
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "success": True,
            "username": username,
            "story_id": story_id,
            "original_url": story_url,
            "title": "Telegram Stories Downloader",
            "download_links": {
                "info": f"/api/info?username={username}&storyid={story_id}",
                "download": f"/api/download?username={username}&storyid={story_id}",
                "base64": f"/api/base64?username={username}&storyid={story_id}",
                "stream": f"/api/stream?username={username}&storyid={story_id}"
            }
        }
    )

@app.get("/api/check")
async def check_story_exists(
    username: str = Query(..., description="Username or channel ID"),
    storyid: int = Query(..., description="Story ID")
):
    """Check if a story exists and is accessible"""
    try:
        if not await ensure_client():
            return error_response("Telegram client not available", 503)
        
        logger.info(f"Checking story {storyid} for {username}")
        
        story = await get_story_by_id(username, storyid)
        
        if not story:
            return success_response({
                "exists": False,
                "username": username,
                "story_id": storyid,
                "message": "Story not found or not accessible"
            })
        
        story_info = format_story_info(story, "Found")
        
        return success_response({
            "exists": True,
            "username": username,
            "story_id": storyid,
            "story_info": story_info,
            "message": "Story exists and is accessible"
        })
        
    except Exception as e:
        logger.error(f"Error checking story: {str(e)}")
        return error_response(f"Check failed: {str(e)}", 500)

@app.get("/api/info")
async def get_story_info(
    username: str = Query(..., description="Username or channel ID"),
    storyid: int = Query(..., description="Story ID")
):
    """Get detailed information about a story"""
    try:
        if not await ensure_client():
            return error_response("Telegram client not available", 503)
        
        logger.info(f"Getting info for story {storyid} from {username}")
        
        story = await get_story_by_id(username, storyid)
        
        if not story:
            return error_response("Story not found", 404)
        
        story_info = format_story_info(story, "Detailed")
        
        # Try to get file size if possible
        if story_info["has_media"]:
            try:
                # Download a small portion to get size
                media = story.media
                if hasattr(media, 'document'):
                    doc = media.document
                    story_info["file_size"] = doc.size
                elif hasattr(media, 'photo'):
                    # Estimate photo size
                    story_info["file_size"] = "Unknown (photo)"
            except:
                story_info["file_size"] = "Unknown"
        
        return success_response({
            "username": username,
            "story_id": storyid,
            "story": story_info
        })
        
    except Exception as e:
        logger.error(f"Error getting story info: {str(e)}")
        return error_response(f"Failed to get info: {str(e)}", 500)

@app.get("/api/download")
async def download_story_file(
    username: str = Query(..., description="Username or channel ID"),
    storyid: int = Query(..., description="Story ID"),
    direct: bool = Query(False, description="Direct download (attachment)")
):
    """Download story as a file"""
    try:
        if not await ensure_client():
            return error_response("Telegram client not available", 503)
        
        logger.info(f"Downloading story {storyid} from {username}")
        
        story = await get_story_by_id(username, storyid)
        
        if not story:
            return error_response("Story not found", 404)
        
        # Download media
        file_bytes, media_type, mime_type = await download_story_media(story)
        
        if not file_bytes:
            return error_response("Failed to download story media", 500)
        
        # Get file extension
        extension = get_file_extension(media_type, mime_type)
        filename = f"story_{username}_{storyid}{extension}"
        
        # Create response
        if direct:
            # Force download as attachment
            headers = {
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Content-Length": str(len(file_bytes))
            }
        else:
            # Display in browser if possible
            headers = {
                "Content-Disposition": f'inline; filename="{filename}"',
                "Content-Length": str(len(file_bytes))
            }
        
        return Response(
            content=file_bytes,
            media_type=mime_type or "application/octet-stream",
            headers=headers
        )
        
    except asyncio.TimeoutError:
        return error_response("Download timeout - story might be too large", 408)
    except Exception as e:
        logger.error(f"Error downloading story: {str(e)}")
        return error_response(f"Download failed: {str(e)}", 500)

@app.get("/api/base64")
async def get_story_base64(
    username: str = Query(..., description="Username or channel ID"),
    storyid: int = Query(..., description="Story ID")
):
    """Get story as base64 encoded data"""
    try:
        if not await ensure_client():
            return error_response("Telegram client not available", 503)
        
        logger.info(f"Getting base64 for story {storyid} from {username}")
        
        story = await get_story_by_id(username, storyid)
        
        if not story:
            return error_response("Story not found", 404)
        
        # Download media
        file_bytes, media_type, mime_type = await download_story_media(story)
        
        if not file_bytes:
            return error_response("Failed to download story media", 500)
        
        # Encode to base64
        base64_data = base64.b64encode(file_bytes).decode('utf-8')
        
        return success_response({
            "username": username,
            "story_id": storyid,
            "media_type": media_type,
            "mime_type": mime_type,
            "size_bytes": len(file_bytes),
            "size_base64": len(base64_data),
            "data": base64_data,
            "message": "Use data field for base64 content"
        })
        
    except Exception as e:
        logger.error(f"Error getting base64: {str(e)}")
        return error_response(f"Failed to get base64: {str(e)}", 500)

@app.get("/api/stream")
async def stream_story(
    username: str = Query(..., description="Username or channel ID"),
    storyid: int = Query(..., description="Story ID")
):
    """Stream story media (useful for large files)"""
    async def file_generator(file_bytes):
        """Generator to stream file in chunks"""
        chunk_size = 1024 * 64  # 64KB chunks
        for i in range(0, len(file_bytes), chunk_size):
            yield file_bytes[i:i + chunk_size]
            await asyncio.sleep(0.001)  # Small delay to prevent blocking
    
    try:
        if not await ensure_client():
            return error_response("Telegram client not available", 503)
        
        logger.info(f"Streaming story {storyid} from {username}")
        
        story = await get_story_by_id(username, storyid)
        
        if not story:
            return error_response("Story not found", 404)
        
        # Download media
        file_bytes, media_type, mime_type = await download_story_media(story)
        
        if not file_bytes:
            return error_response("Failed to download story media", 500)
        
        # Get file extension
        extension = get_file_extension(media_type, mime_type)
        filename = f"story_{username}_{storyid}{extension}"
        
        return StreamingResponse(
            file_generator(file_bytes),
            media_type=mime_type or "application/octet-stream",
            headers={
                "Content-Disposition": f'inline; filename="{filename}"',
                "Content-Length": str(len(file_bytes))
            }
        )
        
    except Exception as e:
        logger.error(f"Error streaming story: {str(e)}")
        return error_response(f"Streaming failed: {str(e)}", 500)

@app.get("/api/direct")
async def download_from_url(
    url: str = Query(..., description="Full Telegram story URL")
):
    """Download story from direct URL"""
    try:
        if not await ensure_client():
            return error_response("Telegram client not available", 503)
        
        logger.info(f"Processing direct URL: {url}")
        
        # Parse URL
        username, story_id = parse_story_url(url)
        
        if not username or not story_id:
            return error_response(
                "Invalid URL format. Supported formats:\n"
                "- https://t.me/username/s/123456\n"
                "- https://t.me/c/1234567890/123456\n"
                "- https://t.me/username/123456",
                400
            )
        
        # Redirect to download endpoint
        return await download_story_file(username, story_id, direct=True)
        
    except Exception as e:
        logger.error(f"Error processing direct URL: {str(e)}")
        return error_response(f"URL processing failed: {str(e)}", 500)

@app.get("/api/stories/active")
async def get_active_stories(
    username: str = Query(..., description="Username or channel ID")
):
    """Get all active stories for a user/channel"""
    try:
        if not await ensure_client():
            return error_response("Telegram client not available", 503)
        
        logger.info(f"Getting active stories for {username}")
        
        peer = await resolve_peer(username)
        
        result = await user_client.invoke(GetPeerStories(peer=peer))
        
        if not result or not hasattr(result, 'stories') or not result.stories.stories:
            return success_response({
                "username": username,
                "count": 0,
                "stories": [],
                "message": "No active stories found"
            })
        
        stories = [
            format_story_info(story, "Active") 
            for story in result.stories.stories
        ]
        
        return success_response({
            "username": username,
            "count": len(stories),
            "stories": stories
        })
        
    except Exception as e:
        logger.error(f"Error getting active stories: {str(e)}")
        return error_response(f"Failed to get stories: {str(e)}", 500)

# ==================== Health Check ====================
@app.get("/api/health")
async def health_check():
    """Health check endpoint"""
    try:
        if not await ensure_client():
            return JSONResponse(
                status_code=503,
                content={
                    "status": "unhealthy",
                    "telegram_client": "disconnected",
                    "timestamp": datetime.now().isoformat()
                }
            )
        
        # Test client connectivity
        me = await user_client.get_me()
        
        return {
            "status": "healthy",
            "telegram_client": "connected",
            "user": f"@{me.username}",
            "user_id": me.id,
            "timestamp": datetime.now().isoformat(),
            "server_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        
    except Exception as e:
        return JSONResponse(
            status_code=503,
            content={
                "status": "unhealthy",
                "error": str(e),
                "timestamp": datetime.now().isoformat()
            }
        )

# ==================== Error Handlers ====================
@app.exception_handler(404)
async def not_found_handler(request: Request, exc: HTTPException):
    """Handle 404 errors"""
    return error_response("Endpoint not found", 404)

@app.exception_handler(500)
async def server_error_handler(request: Request, exc: HTTPException):
    """Handle 500 errors"""
    logger.error(f"Server error: {str(exc)}")
    return error_response("Internal server error", 500)

# ==================== Main Entry Point ====================
if __name__ == "__main__":
    # Create necessary directories
    os.makedirs("templates", exist_ok=True)
    os.makedirs("static", exist_ok=True)
    os.makedirs("sessions", exist_ok=True)
    
    # Create simple HTML template if not exists
    template_path = "templates/index.html"
    if not os.path.exists(template_path):
        with open(template_path, "w", encoding="utf-8") as f:
            f.write("""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{{ title }}</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: Arial, sans-serif; background: #0f0f23; color: #fff; padding: 20px; }
        .container { max-width: 800px; margin: 0 auto; }
        .header { text-align: center; margin-bottom: 30px; }
        .header h1 { color: #00ff9d; margin-bottom: 10px; }
        .card { background: #1a1a2e; padding: 25px; border-radius: 10px; margin-bottom: 20px; }
        .form-group { margin-bottom: 20px; }
        input[type="text"] { 
            width: 100%; 
            padding: 12px; 
            background: #2d2d44; 
            border: 1px solid #00ff9d; 
            border-radius: 5px; 
            color: #fff; 
            font-size: 16px; 
        }
        button { 
            background: #00ff9d; 
            color: #000; 
            border: none; 
            padding: 12px 30px; 
            border-radius: 5px; 
            cursor: pointer; 
            font-size: 16px; 
            font-weight: bold; 
            width: 100%; 
        }
        button:hover { background: #00cc7a; }
        .result { margin-top: 20px; }
        .success { color: #00ff9d; padding: 15px; background: rgba(0, 255, 157, 0.1); border-radius: 5px; }
        .error { color: #ff4444; padding: 15px; background: rgba(255, 68, 68, 0.1); border-radius: 5px; }
        .links { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 10px; margin-top: 20px; }
        .link { background: #2d2d44; padding: 15px; border-radius: 5px; text-align: center; }
        .link a { color: #00ff9d; text-decoration: none; display: block; }
        .link a:hover { text-decoration: underline; }
        .endpoints { margin-top: 30px; }
        .endpoint { background: #2d2d44; padding: 15px; margin-bottom: 10px; border-radius: 5px; }
        .endpoint h3 { color: #00ff9d; margin-bottom: 5px; }
        .endpoint code { background: #1a1a2e; padding: 2px 5px; border-radius: 3px; }
        .info { color: #888; font-size: 14px; margin-top: 5px; }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>{{ title }}</h1>
            <p>Download Telegram stories (photos & videos) directly</p>
        </div>
        
        <div class="card">
            <h2>Download Story</h2>
            <form method="POST" action="/">
                <div class="form-group">
                    <p>Enter Telegram Story URL:</p>
                    <input type="text" name="story_url" placeholder="https://t.me/username/s/123456" required>
                    <div class="info">Supports: t.me/username/s/123456 or t.me/c/1234567890/123456</div>
                </div>
                <button type="submit">Download Story</button>
            </form>
            
            {% if error %}
            <div class="result">
                <div class="error">{{ error }}</div>
            </div>
            {% endif %}
            
            {% if success %}
            <div class="result">
                <div class="success">
                    Story found: {{ username }} - ID: {{ story_id }}
                </div>
                <div class="links">
                    <div class="link">
                        <a href="{{ download_links.info }}" target="_blank">Get Story Info</a>
                    </div>
                    <div class="link">
                        <a href="{{ download_links.download }}" target="_blank">Download File</a>
                    </div>
                    <div class="link">
                        <a href="{{ download_links.base64 }}" target="_blank">Base64 Data</a>
                    </div>
                    <div class="link">
                        <a href="{{ download_links.stream }}" target="_blank">Stream Media</a>
                    </div>
                </div>
                <div class="info">
                    Original URL: <a href="{{ original_url }}" target="_blank">{{ original_url }}</a>
                </div>
            </div>
            {% endif %}
        </div>
        
        <div class="endpoints">
            <h2>API Endpoints</h2>
            {% for endpoint in endpoints %}
            <div class="endpoint">
                <h3>{{ endpoint.name }}</h3>
                <code>{{ endpoint.path }}</code>
            </div>
            {% endfor %}
        </div>
        
        <div class="info" style="text-align: center; margin-top: 30px;">
            <p>API Version: 2.0.0 | Developer: @ISmartCoder | Channel: @abirxdhackz</p>
        </div>
    </div>
</body>
</html>""")
    
    # Create config.py if not exists
    config_path = "config.py"
    if not os.path.exists(config_path):
        with open(config_path, "w", encoding="utf-8") as f:
            f.write("""# Telegram API Configuration
# Get these from https://my.telegram.org

API_ID = 123456  # Your API ID here
API_HASH = "your_api_hash_here"  # Your API Hash here

# Session string - run get_session.py first to generate this
SESSION_STRING = "your_session_string_here"

# Optional: Proxy settings (if needed)
# PROXY = {
#     "scheme": "socks5",
#     "hostname": "127.0.0.1",
#     "port": 1080,
#     "username": "",
#     "password": ""
# }
""")
        print("⚠️  Please edit config.py with your Telegram API credentials!")
        print("   Get API_ID and API_HASH from: https://my.telegram.org")
        exit(1)
    
    # Create session generator script
    session_script = "get_session.py"
    if not os.path.exists(session_script):
        with open(session_script, "w", encoding="utf-
