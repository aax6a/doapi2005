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
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, HTMLResponse, Response, StreamingResponse
from fastapi.templating import Jinja2Templates
from fastapi import Request
from pyrogram import Client
from pyrogram.raw.functions.stories import GetPeerStories, GetStoriesArchive, GetPinnedStories, GetStoriesByID
from pyrogram.raw.types import InputPeerUser, InputPeerChannel
from pyrogram.file_id import FileId, FileType, ThumbnailSource
from config import SESSION_STRING
import uvicorn

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler()
    ]
)

logger = logging.getLogger(__name__)

user = None
client_lock = threading.Lock()

templates = Jinja2Templates(directory="templates")

async def ensure_client():
    global user
    with client_lock:
        if user is None:
            try:
                user = Client(
                    "SmartUserBot",
                    session_string=SESSION_STRING,
                    workdir="/tmp",
                    in_memory=True,
                    workers=10
                )
                await user.start()
                logger.info("Pyrogram user client started successfully")
                return True
            except Exception as e:
                logger.error(f"Failed to start Pyrogram client: {str(e)}")
                user = None
                return False
        
        try:
            is_connected = getattr(user, 'is_connected', False)
            if callable(is_connected):
                connected = is_connected()
            else:
                connected = is_connected
            
            if not connected:
                await user.start()
                logger.info("Pyrogram user client reconnected successfully")
        except Exception as e:
            logger.error(f"Failed to check/restart client: {str(e)}")
            try:
                user = Client(
                    "SmartUserBot",
                    session_string=SESSION_STRING,
                    workdir="/tmp",
                    in_memory=True,
                    workers=10
                )
                await user.start()
                logger.info("Pyrogram user client recreated successfully")
            except Exception as e2:
                logger.error(f"Failed to recreate client: {str(e2)}")
                user = None
                return False
        
        return True

def get_local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
    except Exception:
        ip = '127.0.0.1'
    finally:
        s.close()
    return ip

def parse_story_url(url):
    """
    Parse Telegram story URL and extract username/chat_id and story_id
    """
    patterns = [
        r't\.me/([^/]+)/s/(\d+)',
        r'telegram\.me/([^/]+)/s/(\d+)',
        r't\.me/c/(\d+)/(\d+)',
        r'telegram\.me/c/(\d+)/(\d+)'
    ]
    
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            if pattern.startswith(r't\.me/c/') or pattern.startswith(r'telegram\.me/c/'):
                chat_id = match.group(1)
                story_id = int(match.group(2))
                try:
                    chat_id_int = int(chat_id)
                    return f"-100{chat_id_int}", story_id
                except ValueError:
                    return None, None
            else:
                username = match.group(1)
                story_id = int(match.group(2))
                return username, story_id
    
    return None, None

async def resolve_peer_helper(username_or_id):
    """
    Resolve peer from username or chat_id
    """
    try:
        peer = await user.resolve_peer(username_or_id)
        if hasattr(peer, 'user_id'):
            return InputPeerUser(
                user_id=peer.user_id,
                access_hash=peer.access_hash
            )
        elif hasattr(peer, 'channel_id'):
            return InputPeerChannel(
                channel_id=peer.channel_id,
                access_hash=peer.access_hash
            )
        elif hasattr(peer, 'chat_id'):
            raise HTTPException(status_code=400, detail="Groups are not supported for stories")
        else:
            raise HTTPException(status_code=400, detail="Unsupported peer type")
            
    except Exception as e:
        logger.error(f"Failed to resolve peer {username_or_id}: {str(e)}")
        raise HTTPException(status_code=400, detail=f"Failed to resolve peer: {str(e)}")

def format_story_info(story, story_type):
    story_date = datetime.fromtimestamp(story.date).strftime("%Y-%m-%d %H:%M:%S")
    caption = getattr(story, 'caption', '') if hasattr(story, 'caption') else ''
    
    return {
        "story_id": story.id,
        "type": story_type,
        "date": story_date,
        "timestamp": story.date,
        "caption": caption,
        "has_media": hasattr(story, 'media')
    }

async def get_story_file_bytes(story):
    """Get story media as bytes without saving to file"""
    try:
        # Create a temporary file in memory
        import io
        
        # Get media from story
        media = story.media
        
        if hasattr(media, 'photo'):
            media_type = "photo"
            # For photos
            photo = media.photo
            
            # Create file_id for photo
            file_id = FileId(
                file_type=FileType.PHOTO,
                dc_id=photo.dc_id,
                media_id=photo.id,
                access_hash=photo.access_hash,
                file_reference=photo.file_reference,
                thumbnail_source=ThumbnailSource.THUMBNAIL,
                thumbnail_file_type=FileType.PHOTO,
                thumbnail_size=""
            ).encode()
            
        elif hasattr(media, 'document'):
            # For documents/videos
            doc = media.document
            
            # Determine file type
            mime_type = getattr(doc, 'mime_type', '')
            if mime_type.startswith('video'):
                media_type = "video"
                file_type = FileType.VIDEO
            elif mime_type.startswith('image'):
                media_type = "image"
                file_type = FileType.PHOTO
            else:
                media_type = "document"
                file_type = FileType.DOCUMENT
            
            # Create file_id for document
            file_id = FileId(
                file_type=file_type,
                dc_id=doc.dc_id,
                media_id=doc.id,
                access_hash=doc.access_hash,
                file_reference=doc.file_reference,
                thumbnail_source=ThumbnailSource.THUMBNAIL,
                thumbnail_file_type=FileType.PHOTO,
                thumbnail_size=""
            ).encode()
        else:
            return None, None, None
        
        # Download to bytes
        file_bytes = await user.download_media(
            message=file_id,
            in_memory=True
        )
        
        return file_bytes, media_type, getattr(doc, 'mime_type', 'image/jpeg') if hasattr(media, 'document') else 'image/jpeg'
        
    except Exception as e:
        logger.error(f"Error getting story bytes: {str(e)}")
        return None, None, None

async def download_story_direct_response(username_or_id, storyid):
    """Download story and return as direct response"""
    try:
        # First, resolve the peer
        input_peer = await resolve_peer_helper(username_or_id)
        logger.info(f"Resolved peer for {username_or_id}")
        
        # Get the story directly by ID
        logger.info(f"Getting story {storyid} directly...")
        result = await user.invoke(GetStoriesByID(peer=input_peer, id=[storyid]))
        
        if not result or not hasattr(result, 'stories') or not result.stories:
            logger.error(f"No stories returned for ID {storyid}")
            return None, None, None
        
        story = result.stories[0]
        logger.info(f"Successfully retrieved story {storyid}")
        
        # Get story info
        story_date = datetime.fromtimestamp(story.date).strftime("%Y-%m-%d %H:%M:%S")
        caption = getattr(story, 'caption', '') if hasattr(story, 'caption') else ''
        
        # Get file bytes
        file_bytes, media_type, mime_type = await get_story_file_bytes(story)
        
        if not file_bytes:
            logger.error("Failed to get story bytes")
            return None, None, None
        
        logger.info(f"Got {len(file_bytes)} bytes of {media_type}")
        
        return file_bytes, media_type, {
            "username": username_or_id,
            "story_id": storyid,
            "date": story_date,
            "caption": caption,
            "timestamp": story.date,
            "mime_type": mime_type
        }
        
    except Exception as e:
        logger.error(f"Error in download_story_direct_response: {str(e)}", exc_info=True)
        return None, None, None

@asynccontextmanager
async def lifespan(app: FastAPI):
    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
    
    if not await ensure_client():
        logger.error("Failed to initialize client on startup")
    
    local_ip = get_local_ip()
    logger.info(f"API running on local IP: {local_ip}:4747")
    logger.info(f"API accessible at: http://{local_ip}:4747")
    logger.info(f"API accessible at: http://0.0.0.0:4747")
    
    yield
    
    global user
    if user:
        logger.info("Stopping Pyrogram user client...")
        await user.stop()
        logger.info("Pyrogram user client stopped")

app = FastAPI(title="Telegram Stories API", version="4.0.0", lifespan=lifespan)

@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/api/current")
async def get_current_stories(username: str):
    try:
        if not await ensure_client():
            return JSONResponse(content={
                "success": False,
                "error": "Client initialization failed",
                "api_dev": "@ISmartCoder",
                "api_channel": "@abirxdhackz"
            }, status_code=500)
        
        logger.info(f"Fetching current stories for {username}")
        input_peer = await resolve_peer_helper(username)
        
        result = await user.invoke(
            GetPeerStories(peer=input_peer)
        )
        
        if not result or not hasattr(result, 'stories') or not result.stories.stories:
            return JSONResponse(content={
                "success": True,
                "username": username,
                "count": 0,
                "stories": [],
                "api_dev": "@ISmartCoder",
                "api_channel": "@abirxdhackz"
            })
        
        stories_data = [format_story_info(story, "Active") for story in result.stories.stories]
        
        return JSONResponse(content={
            "success": True,
            "username": username,
            "count": len(stories_data),
            "stories": stories_data,
            "api_dev": "@ISmartCoder",
            "api_channel": "@abirxdhackz"
        })
        
    except Exception as e:
        logger.error(f"Error fetching current stories: {str(e)}")
        return JSONResponse(content={
            "success": False,
            "error": str(e),
            "api_dev": "@ISmartCoder",
            "api_channel": "@abirxdhackz"
        }, status_code=500)

@app.get("/api/all")
async def get_all_stories(username: str):
    try:
        if not await ensure_client():
            return JSONResponse(content={
                "success": False,
                "error": "Client initialization failed",
                "api_dev": "@ISmartCoder",
                "api_channel": "@abirxdhackz"
            }, status_code=500)
        
        logger.info(f"Fetching all stories for {username}")
        input_peer = await resolve_peer_helper(username)
        
        all_stories = []
        
        try:
            active_result = await user.invoke(
                GetPeerStories(peer=input_peer)
            )
            if active_result and hasattr(active_result, 'stories') and active_result.stories.stories:
                for story in active_result.stories.stories:
                    all_stories.append(format_story_info(story, "Active"))
        except Exception as e:
            logger.warning(f"No active stories: {str(e)}")
        
        try:
            pinned_result = await user.invoke(
                GetPinnedStories(
                    peer=input_peer,
                    offset_id=0,
                    limit=100
                )
            )
            if pinned_result and hasattr(pinned_result, 'stories'):
                for story in pinned_result.stories:
                    all_stories.append(format_story_info(story, "Pinned"))
        except Exception as e:
            logger.warning(f"No pinned stories: {str(e)}")
        
        try:
            offset_id = 0
            while True:
                archive_result = await user.invoke(
                    GetStoriesArchive(
                        peer=input_peer,
                        offset_id=offset_id,
                        limit=100
                    )
                )
                
                if not archive_result or not hasattr(archive_result, 'stories') or not archive_result.stories:
                    break
                
                for story in archive_result.stories:
                    all_stories.append(format_story_info(story, "Archived"))
                
                if len(archive_result.stories) < 100:
                    break
                
                offset_id = archive_result.stories[-1].id
        except Exception as e:
            logger.warning(f"No archived stories: {str(e)}")
        
        return JSONResponse(content={
            "success": True,
            "username": username,
            "total_count": len(all_stories),
            "stories": all_stories,
            "api_dev": "@ISmartCoder",
            "api_channel": "@abirxdhackz"
        })
        
    except Exception as e:
        logger.error(f"Error fetching all stories: {str(e)}")
        return JSONResponse(content={
            "success": False,
            "error": str(e),
            "api_dev": "@ISmartCoder",
            "api_channel": "@abirxdhackz"
        }, status_code=500)

@app.get("/api/special")
async def download_story(username: str, storyid: int):
    """Download story and return as base64 encoded data"""
    try:
        if not await ensure_client():
            return JSONResponse(content={
                "success": False,
                "error": "Client initialization failed",
                "api_dev": "@ISmartCoder",
                "api_channel": "@abirxdhackz"
            }, status_code=500)
        
        logger.info(f"Downloading story {storyid} from {username}")
        
        file_bytes, media_type, story_info = await download_story_direct_response(username, storyid)
        
        if not file_bytes:
            return JSONResponse(content={
                "success": False,
                "error": "Failed to download story media",
                "api_dev": "@ISmartCoder",
                "api_channel": "@abirxdhackz"
            }, status_code=404)
        
        # Encode to base64
        base64_data = base64.b64encode(file_bytes).decode('utf-8')
        
        return JSONResponse(content={
            "success": True,
            "username": story_info["username"],
            "story_id": story_info["story_id"],
            "type": "Direct",
            "media_type": media_type,
            "date": story_info["date"],
            "timestamp": story_info["timestamp"],
            "caption": story_info["caption"],
            "mime_type": story_info["mime_type"],
            "data": base64_data,
            "size": len(file_bytes),
            "api_dev": "@ISmartCoder",
            "api_channel": "@abirxdhackz"
        })
        
    except Exception as e:
        logger.error(f"Error downloading story: {str(e)}", exc_info=True)
        return JSONResponse(content={
            "success": False,
            "error": f"Server error: {str(e)}",
            "api_dev": "@ISmartCoder",
            "api_channel": "@abirxdhackz"
        }, status_code=500)

@app.get("/api/direct")
async def download_story_direct(url: str):
    """Download story from direct URL and return as base64"""
    try:
        if not await ensure_client():
            return JSONResponse(content={
                "success": False,
                "error": "Client initialization failed",
                "api_dev": "@ISmartCoder",
                "api_channel": "@abirxdhackz"
            }, status_code=500)
        
        logger.info(f"Processing direct URL: {url}")
        
        username_or_id, story_id = parse_story_url(url)
        
        if not username_or_id or not story_id:
            return JSONResponse(content={
                "success": False,
                "error": "Invalid Telegram story URL format. Expected formats:\n"
                        "1. https://t.me/username/s/story_id\n"
                        "2. https://t.me/c/1234567890/story_id",
                "api_dev": "@ISmartCoder",
                "api_channel": "@abirxdhackz"
            }, status_code=400)
        
        logger.info(f"Extracted: {username_or_id}, story_id: {story_id}")
        
        file_bytes, media_type, story_info = await download_story_direct_response(username_or_id, story_id)
        
        if not file_bytes:
            return JSONResponse(content={
                "success": False,
                "error": "Failed to download story media",
                "api_dev": "@ISmartCoder",
                "api_channel": "@abirxdhackz"
            }, status_code=404)
        
        # Encode to base64
        base64_data = base64.b64encode(file_bytes).decode('utf-8')
        
        return JSONResponse(content={
            "success": True,
            "username": story_info["username"],
            "story_id": story_info["story_id"],
            "type": "Direct",
            "media_type": media_type,
            "date": story_info["date"],
            "timestamp": story_info["timestamp"],
            "caption": story_info["caption"],
            "mime_type": story_info["mime_type"],
            "data": base64_data,
            "size": len(file_bytes),
            "source_url": url,
            "api_dev": "@ISmartCoder",
            "api_channel": "@abirxdhackz"
        })
        
    except Exception as e:
        logger.error(f"Error processing direct URL: {str(e)}", exc_info=True)
        return JSONResponse(content={
            "success": False,
            "error": f"Processing error: {str(e)}",
            "api_dev": "@ISmartCoder",
            "api_channel": "@abirxdhackz"
        }, status_code=500)

# New endpoint: Direct file download
@app.get("/api/download")
async def download_story_file(username: str, storyid: int):
    """Download story as direct file download"""
    try:
        if not await ensure_client():
            return JSONResponse(content={
                "success": False,
                "error": "Client initialization failed"
            }, status_code=500)
        
        logger.info(f"Downloading story {storyid} from {username} as file")
        
        file_bytes, media_type, story_info = await download_story_direct_response(username, storyid)
        
        if not file_bytes:
            return JSONResponse(content={
                "success": False,
                "error": "Failed to download story media"
            }, status_code=404)
        
        # Determine file extension
        if media_type == "photo" or media_type == "image":
            extension = ".jpg"
            content_type = "image/jpeg"
        elif media_type == "video":
            extension = ".mp4"
            content_type = "video/mp4"
        else:
            extension = ".bin"
            content_type = "application/octet-stream"
        
        filename = f"story_{username}_{storyid}{extension}"
        
        return Response(
            content=file_bytes,
            media_type=content_type,
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Content-Length": str(len(file_bytes))
            }
        )
        
    except Exception as e:
        logger.error(f"Error downloading story file: {str(e)}")
        return JSONResponse(content={
            "success": False,
            "error": str(e)
        }, status_code=500)

# New endpoint: Streaming response
@app.get("/api/stream")
async def stream_story(username: str, storyid: int):
    """Stream story media"""
    try:
        if not await ensure_client():
            return JSONResponse(content={
                "success": False,
                "error": "Client initialization failed"
            }, status_code=500)
        
        logger.info(f"Streaming story {storyid} from {username}")
        
        file_bytes, media_type, story_info = await download_story_direct_response(username, storyid)
        
        if not file_bytes:
            return JSONResponse(content={
                "success": False,
                "error": "Failed to download story media"
            }, status_code=404)
        
        # Create a generator for streaming
        def iterfile():
            yield file_bytes
        
        # Determine content type
        if media_type == "photo" or media_type == "image":
            content_type = "image/jpeg"
        elif media_type == "video":
            content_type = "video/mp4"
        else:
            content_type = "application/octet-stream"
        
        return StreamingResponse(
            iterfile(),
            media_type=content_type,
            headers={
                "Content-Disposition": f'inline; filename="story_{storyid}"',
                "Content-Length": str(len(file_bytes))
            }
        )
        
    except Exception as e:
        logger.error(f"Error streaming story: {str(e)}")
        return JSONResponse(content={
            "success": False,
            "error": str(e)
        }, status_code=500)

@app.get("/api/check")
async def check_story(username: str, storyid: int):
    try:
        if not await ensure_client():
            return JSONResponse(content={
                "success": False,
                "error": "Client initialization failed"
            }, status_code=500)
        
        logger.info(f"Checking story {storyid} for {username}")
        
        try:
            input_peer = await resolve_peer_helper(username)
        except Exception as e:
            return JSONResponse(content={
                "success": False,
                "error": f"Cannot resolve user/channel: {str(e)}"
            })
        
        # Try to get story info
        try:
            result = await user.invoke(GetStoriesByID(peer=input_peer, id=[storyid]))
            
            if result and hasattr(result, 'stories') and result.stories:
                story = result.stories[0]
                
                # Check media type
                media_type = "unknown"
                mime_type = "unknown"
                if hasattr(story, 'media'):
                    media = story.media
                    if hasattr(media, 'photo'):
                        media_type = "photo"
                        mime_type = "image/jpeg"
                    elif hasattr(media, 'document'):
                        doc = media.document
                        mime_type = getattr(doc, 'mime_type', 'application/octet-stream')
                        if mime_type.startswith('video'):
                            media_type = "video"
                        elif mime_type.startswith('image'):
                            media_type = "image"
                        else:
                            media_type = "document"
                
                return JSONResponse(content={
                    "success": True,
                    "exists": True,
                    "story_id": storyid,
                    "has_media": hasattr(story, 'media'),
                    "media_type": media_type,
                    "mime_type": mime_type,
                    "date": datetime.fromtimestamp(story.date).strftime("%Y-%m-%d %H:%M:%S"),
                    "caption": getattr(story, 'caption', '')[:100] if hasattr(story, 'caption') else '',
                    "message": "Story exists and is accessible"
                })
            else:
                return JSONResponse(content={
                    "success": True,
                    "exists": False,
                    "story_id": storyid,
                    "message": "Story not found or not accessible"
                })
                
        except Exception as e:
            return JSONResponse(content={
                "success": True,
                "exists": False,
                "story_id": storyid,
                "error": str(e),
                "message": "Cannot access story"
            })
        
    except Exception as e:
        logger.error(f"Error checking story: {str(e)}")
        return JSONResponse(content={
            "success": False,
            "error": str(e)
        }, status_code=500)

if __name__ == "__main__":
    local_ip = get_local_ip()
    print(f"\n{'='*60}")
    print(f"Telegram Stories API Server (Direct Data Version)")
    print(f"{'='*60}")
    print(f"Local IP: {local_ip}")
    print(f"Port: 4747")
    print(f"{'='*60}")
    print(f"Access URLs:")
    print(f"  - http://{local_ip}:4747")
    print(f"  - http://0.0.0.0:4747")
    print(f"  - http://127.0.0.1:4747")
    print(f"{'='*60}")
    print(f"API Endpoints:")
    print(f"  - /api/current?username=<username>")
    print(f"  - /api/all?username=<username>")
    print(f"  - /api/special?username=<username>&storyid=<id> (Base64)")
    print(f"  - /api/direct?url=<telegram_story_url> (Base64)")
    print(f"  - /api/download?username=<username>&storyid=<id> (File)")
    print(f"  - /api/stream?username=<username>&storyid=<id> (Stream)")
    print(f"  - /api/check?username=<username>&storyid=<id> (Check)")
    print(f"{'='*60}")
    print(f"Note: No external uploads - data returned directly")
    print(f"{'='*60}\n")
    
    uvicorn.run(app, host="0.0.0.0", port=4747, loop="uvloop")
