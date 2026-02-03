import asyncio
import logging
import os
import re
import base64
from datetime import datetime
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse, HTMLResponse, Response, RedirectResponse
from fastapi.templating import Jinja2Templates
from pyrogram import Client
from pyrogram.raw.functions.stories import GetStoriesByID
import uvicorn

# ============ Configuration ============
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Telegram credentials from environment variables (for Vercel)
API_ID = int(os.getenv("API_ID", 28426910))
API_HASH = os.getenv("API_HASH", "14824e6e01b1b6e6bef683c3e1797821")
SESSION_STRING = os.getenv("SESSION_STRING", "AQF722MAdGPFXOVUJ3QWo-FXtQSkKFMMhCfF8rUNsK4fSao6j4x1nmwntswCcoJ7HuvimpdCN_uwj7OtABxPy5f6ICPfYbEqeAYZFhvQDi8EuTm8zX7bJqlY2P_lKVmhMLtPlKWJhYRpnQ23bV2uApUO3rljn0Z885d0igTqe-d5nDvrSR2XzRSSPj4OP77RVjd_cVSETxZ3HpeqAX8lVEZBigDd59_sQ0BJdRS3DMTBtkqzGPPK2C75jEWymebSeN_UWb9aV-gEWrbPmF_plQusjrXeqKlBUu3eAwtxYIYQ5RGfylq9vQuOq_Sc4gLWEGRC-NzY4Il_FO0QaqNEzx9VkQXGOgAAAAFw2qiqAA")

# Initialize client
user_client = None
templates = Jinja2Templates(directory=".")

# ============ Client Management ============
async def get_client():
    """Get or create Telegram client"""
    global user_client
    if user_client is None or not user_client.is_connected:
        try:
            user_client = Client(
                "telegram_stories",
                api_id=API_ID,
                api_hash=API_HASH,
                session_string=SESSION_STRING,
                in_memory=True,
                workers=10
            )
            await user_client.start()
            me = await user_client.get_me()
            logger.info(f"Client started as @{me.username}")
        except Exception as e:
            logger.error(f"Failed to start client: {e}")
            user_client = None
            raise
    return user_client

# ============ URL Parsing ============
def parse_story_url(url: str):
    """Parse Telegram story URL"""
    patterns = [
        r't\.me/([^/]+)/s/(\d+)',
        r'telegram\.me/([^/]+)/s/(\d+)',
        r't\.me/c/(\d+)/(\d+)',
        r't\.me/([^/]+)/(\d+)$'
    ]
    
    for pattern in patterns:
        match = re.search(pattern, url, re.IGNORECASE)
        if match:
            if 'c/' in pattern:
                chat_id = match.group(1)
                story_id = int(match.group(2))
                return f"-100{chat_id}", story_id
            else:
                username = match.group(1).lstrip('@')
                story_id = int(match.group(2))
                return username, story_id
    return None, None

# ============ Story Download ============
async def download_story(username: str, story_id: int, return_type: str = "json"):
    """Download story and return based on type"""
    try:
        client = await get_client()
        
        # Resolve peer
        try:
            peer = await client.resolve_peer(username)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"User/channel not found: {e}")
        
        # Get story
        try:
            result = await client.invoke(GetStoriesByID(peer=peer, id=[story_id]))
            if not result or not hasattr(result, 'stories') or not result.stories:
                raise HTTPException(status_code=404, detail="Story not found")
            
            story = result.stories[0]
            
            # Download media
            if not hasattr(story, 'media'):
                raise HTTPException(status_code=404, detail="Story has no media")
            
            # Download to bytes
            file_bytes = await client.download_media(
                story.media,
                in_memory=True
            )
            
            if not file_bytes:
                raise HTTPException(status_code=500, detail="Failed to download media")
            
            # Get media info
            media_type = "unknown"
            mime_type = "application/octet-stream"
            
            if hasattr(story.media, 'photo'):
                media_type = "photo"
                mime_type = "image/jpeg"
            elif hasattr(story.media, 'document'):
                doc = story.media.document
                mime_type = getattr(doc, 'mime_type', 'application/octet-stream')
                if 'video' in mime_type:
                    media_type = "video"
                elif 'image' in mime_type:
                    media_type = "image"
                else:
                    media_type = "document"
            
            # Prepare response based on type
            if return_type == "file":
                extension = ".jpg" if media_type == "photo" else ".mp4" if media_type == "video" else ".bin"
                filename = f"story_{username}_{story_id}{extension}"
                
                return Response(
                    content=file_bytes,
                    media_type=mime_type,
                    headers={
                        "Content-Disposition": f'attachment; filename="{filename}"',
                        "Content-Length": str(len(file_bytes))
                    }
                )
            
            elif return_type == "base64":
                base64_data = base64.b64encode(file_bytes).decode('utf-8')
                
                return JSONResponse({
                    "success": True,
                    "username": username,
                    "story_id": story_id,
                    "media_type": media_type,
                    "mime_type": mime_type,
                    "size": len(file_bytes),
                    "data": base64_data,
                    "timestamp": datetime.now().isoformat(),
                    "api_dev": "@ISmartCoder",
                    "api_channel": "@abirxdhackz"
                })
            
            else:  # json - return URL
                # For Vercel, we return a direct download link
                download_url = f"/api/download?username={username}&storyid={story_id}"
                
                return JSONResponse({
                    "success": True,
                    "username": username,
                    "story_id": story_id,
                    "media_type": media_type,
                    "mime_type": mime_type,
                    "size": len(file_bytes),
                    "download_url": download_url,
                    "direct_download": f"https://{os.getenv('VERCEL_URL', '')}{download_url}",
                    "date": datetime.fromtimestamp(story.date).strftime("%Y-%m-%d %H:%M:%S"),
                    "timestamp": datetime.now().isoformat(),
                    "api_dev": "@ISmartCoder",
                    "api_channel": "@abirxdhackz"
                })
                
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Story download error: {str(e)}")
            
    except Exception as e:
        logger.error(f"Error in download_story: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

# ============ FastAPI App ============
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events"""
    # Startup
    logger.info("Starting Telegram Stories API...")
    try:
        await get_client()
    except:
        logger.warning("Client initialization failed on startup")
    
    yield
    
    # Shutdown
    if user_client and user_client.is_connected:
        await user_client.stop()
        logger.info("Telegram client stopped")

app = FastAPI(
    title="Telegram Stories API",
    description="Download Telegram stories (photos & videos)",
    version="1.0.0",
    lifespan=lifespan
)

# ============ Routes ============
@app.get("/")
async def home():
    """Home page redirect to API docs"""
    html_content = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Telegram Stories API</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            body { font-family: Arial, sans-serif; max-width: 800px; margin: 0 auto; padding: 20px; }
            h1 { color: #0088cc; }
            .endpoint { background: #f5f5f5; padding: 15px; margin: 10px 0; border-radius: 5px; }
            code { background: #e0e0e0; padding: 2px 5px; border-radius: 3px; }
            .example { color: #666; font-size: 0.9em; }
        </style>
    </head>
    <body>
        <h1>Telegram Stories API</h1>
        <p>API for downloading Telegram stories (photos & videos)</p>
        
        <div class="endpoint">
            <h3>📱 Download from URL</h3>
            <p><code>GET /api/direct?url=STORY_URL</code></p>
            <p class="example">Example: <code>/api/direct?url=https://t.me/username/s/123456</code></p>
        </div>
        
        <div class="endpoint">
            <h3>📥 Direct Download</h3>
            <p><code>GET /api/download?username=USERNAME&storyid=ID</code></p>
            <p class="example">Example: <code>/api/download?username=username&storyid=123456</code></p>
        </div>
        
        <div class="endpoint">
            <h3>🔍 Check Story</h3>
            <p><code>GET /api/check?username=USERNAME&storyid=ID</code></p>
            <p class="example">Example: <code>/api/check?username=username&storyid=123456</code></p>
        </div>
        
        <div class="endpoint">
            <h3>📊 Base64 Data</h3>
            <p><code>GET /api/base64?username=USERNAME&storyid=ID</code></p>
            <p class="example">Example: <code>/api/base64?username=username&storyid=123456</code></p>
        </div>
        
        <hr>
        <p><strong>Developer:</strong> @ISmartCoder</p>
        <p><strong>Channel:</strong> @abirxdhackz</p>
        <p><strong>Version:</strong> 1.0.0</p>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)

@app.get("/api/direct")
async def direct_download(url: str = Query(..., description="Telegram story URL")):
    """Download story from direct URL"""
    username, story_id = parse_story_url(url)
    
    if not username or not story_id:
        return JSONResponse(
            status_code=400,
            content={
                "success": False,
                "error": "Invalid URL format. Use: https://t.me/username/s/123456",
                "api_dev": "@ISmartCoder",
                "api_channel": "@abirxdhackz"
            }
        )
    
    return await download_story(username, story_id, "json")

@app.get("/api/download")
async def download_file(
    username: str = Query(..., description="Username or channel ID"),
    storyid: int = Query(..., description="Story ID")
):
    """Download story as file"""
    return await download_story(username, storyid, "file")

@app.get("/api/base64")
async def get_base64(
    username: str = Query(..., description="Username or channel ID"),
    storyid: int = Query(..., description="Story ID")
):
    """Get story as base64 encoded data"""
    return await download_story(username, storyid, "base64")

@app.get("/api/check")
async def check_story(
    username: str = Query(..., description="Username or channel ID"),
    storyid: int = Query(..., description="Story ID")
):
    """Check if story exists"""
    try:
        client = await get_client()
        
        # Resolve peer
        peer = await client.resolve_peer(username)
        
        # Get story
        result = await client.invoke(GetStoriesByID(peer=peer, id=[storyid]))
        
        if not result or not hasattr(result, 'stories') or not result.stories:
            return JSONResponse({
                "success": False,
                "exists": False,
                "error": "Story not found",
                "api_dev": "@ISmartCoder",
                "api_channel": "@abirxdhackz"
            })
        
        story = result.stories[0]
        
        # Get info
        media_type = "unknown"
        if hasattr(story, 'media'):
            if hasattr(story.media, 'photo'):
                media_type = "photo"
            elif hasattr(story.media, 'document'):
                doc = story.media.document
                mime = getattr(doc, 'mime_type', '')
                if 'video' in mime:
                    media_type = "video"
                elif 'image' in mime:
                    media_type = "image"
        
        return JSONResponse({
            "success": True,
            "exists": True,
            "username": username,
            "story_id": storyid,
            "media_type": media_type,
            "date": datetime.fromtimestamp(story.date).strftime("%Y-%m-%d %H:%M:%S"),
            "has_media": hasattr(story, 'media'),
            "api_dev": "@ISmartCoder",
            "api_channel": "@abirxdhackz"
        })
        
    except Exception as e:
        return JSONResponse({
            "success": False,
            "exists": False,
            "error": str(e),
            "api_dev": "@ISmartCoder",
            "api_channel": "@abirxdhackz"
        }, status_code=500)

@app.get("/api/health")
async def health_check():
    """Health check endpoint"""
    try:
        client = await get_client()
        if client.is_connected:
            me = await client.get_me()
            return {
                "status": "healthy",
                "telegram_user": f"@{me.username}",
                "timestamp": datetime.now().isoformat()
            }
    except:
        pass
    
    return JSONResponse(
        status_code=503,
        content={"status": "unhealthy", "error": "Telegram client not connected"}
    )

# ============ Error Handlers ============
@app.exception_handler(HTTPException)
async def http_exception_handler(request, exc):
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "success": False,
            "error": exc.detail,
            "api_dev": "@ISmartCoder",
            "api_channel": "@abirxdhackz"
        }
    )

# ============ Vercel Handler ============
# This is required for Vercel deployment
app = app

# For local development
if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)
