import asyncio
import logging
import os
import socket
import aiohttp
import uvloop
import threading
import re
from datetime import datetime
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi import Request
from pyrogram import Client
from pyrogram.raw.functions.stories import GetPeerStories, GetStoriesArchive, GetPinnedStories
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

async def upload_to_tmpfiles(file_path):
    try:
        logger.info(f"Uploading {file_path} to tmpfiles.org")
        
        async with aiohttp.ClientSession() as session:
            with open(file_path, 'rb') as f:
                form = aiohttp.FormData()
                form.add_field('file', f, filename=os.path.basename(file_path))
                
                async with session.post('https://tmpfiles.org/api/v1/upload', data=form) as resp:
                    if resp.status == 200:
                        result = await resp.json()
                        if result.get('status') == 'success':
                            original_url = result['data']['url']
                            download_url = original_url.replace('tmpfiles.org/', 'tmpfiles.org/dl/')
                            logger.info(f"Upload successful: {download_url}")
                            return download_url
                        else:
                            logger.error(f"Upload failed: {result}")
                            return None
                    else:
                        logger.error(f"Upload failed with status: {resp.status}")
                        return None
    except Exception as e:
        logger.error(f"Error uploading to tmpfiles: {str(e)}")
        return None

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

async def download_photo(media_photo):
    """Download photo from story using direct Pyrogram method"""
    try:
        # Create a temporary file path
        import tempfile
        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".jpg")
        temp_path = temp_file.name
        temp_file.close()
        
        # Download using Pyrogram's download_media with file_id
        photo = media_photo
        
        # Get file_id for the photo
        file_id = FileId(
            file_type=FileType.PHOTO,
            dc_id=photo.dc_id,
            media_id=photo.id,
            access_hash=photo.access_hash,
            file_reference=photo.file_reference,
            thumbnail_source=ThumbnailSource.THUMBNAIL,
            thumbnail_file_type=FileType.PHOTO,
            thumbnail_size=""
        )
        
        # Download using user.download_media
        file_path = await user.download_media(
            message=file_id.encode(),
            file_name=temp_path
        )
        
        if file_path and os.path.exists(file_path):
            return file_path
        else:
            return None
            
    except Exception as e:
        logger.error(f"Error downloading photo: {str(e)}")
        return None

async def download_document(media_document):
    """Download document from story"""
    try:
        import tempfile
        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".mp4")
        temp_path = temp_file.name
        temp_file.close()
        
        doc = media_document
        mime_type = getattr(doc, 'mime_type', '')
        
        if mime_type.startswith('video'):
            file_type = FileType.VIDEO
        else:
            file_type = FileType.DOCUMENT
        
        file_id = FileId(
            file_type=file_type,
            dc_id=doc.dc_id,
            media_id=doc.id,
            access_hash=doc.access_hash,
            file_reference=doc.file_reference,
            thumbnail_source=ThumbnailSource.THUMBNAIL,
            thumbnail_file_type=FileType.PHOTO,
            thumbnail_size=""
        )
        
        file_path = await user.download_media(
            message=file_id.encode(),
            file_name=temp_path
        )
        
        if file_path and os.path.exists(file_path):
            return file_path
        else:
            return None
            
    except Exception as e:
        logger.error(f"Error downloading document: {str(e)}")
        return None

async def find_and_download_story(username_or_id, storyid):
    try:
        input_peer = await resolve_peer_helper(username_or_id)
    except HTTPException:
        return None
    
    target_story = None
    story_type = None
    
    # Search in active stories
    try:
        active_result = await user.invoke(
            GetPeerStories(peer=input_peer)
        )
        if active_result and hasattr(active_result, 'stories') and active_result.stories.stories:
            for story in active_result.stories.stories:
                if story.id == storyid:
                    target_story = story
                    story_type = "Active"
                    break
    except Exception as e:
        logger.warning(f"No active stories or error: {str(e)}")
    
    # Search in pinned stories
    if not target_story:
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
                    if story.id == storyid:
                        target_story = story
                        story_type = "Pinned"
                        break
        except Exception as e:
            logger.warning(f"No pinned stories or error: {str(e)}")
    
    # Search in archived stories
    if not target_story:
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
                    if story.id == storyid:
                        target_story = story
                        story_type = "Archived"
                        break
                
                if target_story:
                    break
                
                if len(archive_result.stories) < 100:
                    break
                
                offset_id = archive_result.stories[-1].id
        except Exception as e:
            logger.warning(f"No archived stories or error: {str(e)}")
    
    if not target_story:
        logger.error(f"Story {storyid} not found for {username_or_id}")
        return None
    
    # Download the story media
    story_date = datetime.fromtimestamp(target_story.date).strftime("%Y-%m-%d %H:%M:%S")
    caption = getattr(target_story, 'caption', '') if hasattr(target_story, 'caption') else ''
    
    media = target_story.media
    file_path = None
    media_type = None
    
    if hasattr(media, 'photo'):
        media_type = "photo"
        logger.info(f"Downloading photo story {storyid}")
        file_path = await download_photo(media.photo)
        
    elif hasattr(media, 'document'):
        doc = media.document
        mime_type = getattr(doc, 'mime_type', '')
        
        if mime_type.startswith('video'):
            media_type = "video"
        elif mime_type.startswith('image'):
            media_type = "image"
        else:
            media_type = "document"
        
        logger.info(f"Downloading {media_type} story {storyid}")
        file_path = await download_document(doc)
    else:
        logger.warning(f"Unsupported media type: {type(media)}")
        return None
    
    if not file_path or not os.path.exists(file_path):
        logger.error(f"Failed to download story media to {file_path}")
        return None
    
    logger.info(f"Story downloaded to: {file_path}, size: {os.path.getsize(file_path) if os.path.exists(file_path) else 0} bytes")
    
    # Upload to temporary file host
    upload_url = await upload_to_tmpfiles(file_path)
    
    # Clean up local file
    if os.path.exists(file_path):
        os.remove(file_path)
    
    if not upload_url:
        logger.error("Failed to upload to tmpfiles.org")
        return None
    
    return {
        "success": True,
        "username": username_or_id,
        "story_id": storyid,
        "type": story_type,
        "media_type": media_type,
        "date": story_date,
        "timestamp": target_story.date,
        "caption": caption,
        "download_url": upload_url,
        "expires_in": "60 minutes"
    }

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

app = FastAPI(title="Telegram Stories API", version="1.0.0", lifespan=lifespan)

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
    try:
        if not await ensure_client():
            return JSONResponse(content={
                "success": False,
                "error": "Client initialization failed",
                "api_dev": "@ISmartCoder",
                "api_channel": "@abirxdhackz"
            }, status_code=500)
        
        logger.info(f"Downloading story {storyid} from {username}")
        
        result = await find_and_download_story(username, storyid)
        
        if not result:
            return JSONResponse(content={
                "success": False,
                "error": "Story not found or download failed",
                "api_dev": "@ISmartCoder",
                "api_channel": "@abirxdhackz"
            }, status_code=404)
        
        result["api_dev"] = "@ISmartCoder"
        result["api_channel"] = "@abirxdhackz"
        
        return JSONResponse(content=result)
        
    except Exception as e:
        logger.error(f"Error downloading story: {str(e)}")
        return JSONResponse(content={
            "success": False,
            "error": str(e),
            "api_dev": "@ISmartCoder",
            "api_channel": "@abirxdhackz"
        }, status_code=500)

@app.get("/api/direct")
async def download_story_direct(url: str):
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
        
        result = await find_and_download_story(username_or_id, story_id)
        
        if not result:
            return JSONResponse(content={
                "success": False,
                "error": "Story not found or download failed",
                "api_dev": "@ISmartCoder",
                "api_channel": "@abirxdhackz"
            }, status_code=404)
        
        result["api_dev"] = "@ISmartCoder"
        result["api_channel"] = "@abirxdhackz"
        result["source_url"] = url
        
        return JSONResponse(content=result)
        
    except Exception as e:
        logger.error(f"Error processing direct URL: {str(e)}")
        return JSONResponse(content={
            "success": False,
            "error": str(e),
            "api_dev": "@ISmartCoder",
            "api_channel": "@abirxdhackz"
        }, status_code=500)

if __name__ == "__main__":
    local_ip = get_local_ip()
    print(f"\n{'='*60}")
    print(f"Telegram Stories API Server (uvloop enabled)")
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
    print(f"  - /api/special?username=<username>&storyid=<id>")
    print(f"  - /api/direct?url=<telegram_story_url>")
    print(f"{'='*60}\n")
    
    uvicorn.run(app, host="0.0.0.0", port=4747, loop="uvloop")
