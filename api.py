#Copyright @ISmartCoder
#Updates Channel @abirxdhackz
import asyncio
import logging
import os
import socket
import aiohttp
import uvloop
import threading
from datetime import datetime
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
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

async def resolve_peer_helper(username):
    peer = await user.resolve_peer(username)
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
    else:
        raise HTTPException(status_code=400, detail="Unsupported peer type")

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

@app.get("/")
async def root():
    return {
        "status": "online",
        "api": "Telegram Stories API",
        "version": "1.0.0",
        "endpoints": {
            "/api/current?username={}": "Get current active stories",
            "/api/all?username={}": "Get all stories (active + pinned + archived)",
            "/api/special?username={}&storyid={}": "Download specific story by ID"
        },
        "api_dev": "@ISmartCoder",
        "api_channel": "@abirxdhackz"
    }

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
        input_peer = await resolve_peer_helper(username)
        
        target_story = None
        story_type = None
        
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
        except:
            pass
        
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
            except:
                pass
        
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
            except:
                pass
        
        if not target_story:
            return JSONResponse(content={
                "success": False,
                "error": "Story not found",
                "api_dev": "@ISmartCoder",
                "api_channel": "@abirxdhackz"
            }, status_code=404)
        
        story_date = datetime.fromtimestamp(target_story.date).strftime("%Y-%m-%d %H:%M:%S")
        caption = getattr(target_story, 'caption', '') if hasattr(target_story, 'caption') else ''
        
        media = target_story.media
        file_path = None
        media_type = None
        
        if hasattr(media, 'photo'):
            media_type = "photo"
            file_id_obj = FileId(
                file_type=FileType.PHOTO,
                dc_id=media.photo.dc_id,
                media_id=media.photo.id,
                access_hash=media.photo.access_hash,
                file_reference=media.photo.file_reference,
                thumbnail_source=ThumbnailSource.THUMBNAIL,
                thumbnail_file_type=FileType.PHOTO,
                thumbnail_size=""
            )
            file_path = await user.download_media(file_id_obj.encode(), file_name="/tmp/")
            
        elif hasattr(media, 'document'):
            doc = media.document
            mime_type = getattr(doc, 'mime_type', '')
            
            if mime_type.startswith('video'):
                media_type = "video"
                file_type = FileType.VIDEO
            else:
                media_type = "document"
                file_type = FileType.DOCUMENT
            
            file_id_obj = FileId(
                file_type=file_type,
                dc_id=doc.dc_id,
                media_id=doc.id,
                access_hash=doc.access_hash,
                file_reference=doc.file_reference,
                thumbnail_source=ThumbnailSource.THUMBNAIL,
                thumbnail_file_type=FileType.PHOTO,
                thumbnail_size=""
            )
            file_path = await user.download_media(file_id_obj.encode(), file_name="/tmp/")
        else:
            return JSONResponse(content={
                "success": False,
                "error": "Unsupported media type",
                "api_dev": "@ISmartCoder",
                "api_channel": "@abirxdhackz"
            }, status_code=400)
        
        upload_url = await upload_to_tmpfiles(file_path)
        
        if os.path.exists(file_path):
            os.remove(file_path)
        
        if not upload_url:
            return JSONResponse(content={
                "success": False,
                "error": "Failed to upload file",
                "api_dev": "@ISmartCoder",
                "api_channel": "@abirxdhackz"
            }, status_code=500)
        
        return JSONResponse(content={
            "success": True,
            "username": username,
            "story_id": storyid,
            "type": story_type,
            "media_type": media_type,
            "date": story_date,
            "timestamp": target_story.date,
            "caption": caption,
            "download_url": upload_url,
            "expires_in": "60 minutes",
            "api_dev": "@ISmartCoder",
            "api_channel": "@abirxdhackz"
        })
        
    except Exception as e:
        logger.error(f"Error downloading story: {str(e)}")
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
    print(f"{'='*60}\n")
    
    uvicorn.run(app, host="0.0.0.0", port=4747, loop="uvloop")
