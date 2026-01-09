# Telegram Stories Downloader API

A powerful FastAPI-based service for downloading Telegram stories. Fetch active, pinned, and archived stories with ease through a beautiful web interface or REST API.

[![Deploy with Vercel](https://vercel.com/button)](https://vercel.com/new/clone?repository-url=https://github.com/SmartStoryDownloader)

## Features

- ðŸš€ **Beautiful Web Interface** - Interactive web UI for easy story downloads
- ðŸ“¥ **Download Stories** - Active, pinned, and archived Telegram stories
- ðŸ”— **Direct URL Support** - Download stories directly from Telegram URLs
- ðŸ“Š **Story Metadata** - Fetch captions, timestamps, and media types
- â˜ï¸ **Auto File Hosting** - Automatic temporary file hosting via tmpfiles.org
- âš¡ **High Performance** - Built with FastAPI, Pyrogram, and uvloop
- ðŸŽ¨ **Modern Design** - Premium dark theme with responsive layout
- ðŸ”„ **Real-time Updates** - Live status indicators and notifications

## Live Demo

Visit the web interface at your deployment URL to access:
- Interactive story downloader with multiple methods
- Browse current and all stories by username
- Download stories with direct Telegram URLs
- Complete API documentation

## API Endpoints

### Base URL
```
https://your-deployment.vercel.app
```

### 1. Web Interface
Access the beautiful web interface for easy story downloads.

```http
GET /
```

**Features:**
- Direct URL download
- Username + Story ID download
- Browse and download current/all stories
- Interactive API documentation

### 2. Get Current Stories
Fetch all currently active stories from a user.

```http
GET /api/current?username={username}
```

**Example:**
```bash
curl "https://your-deployment.vercel.app/api/current?username=ISmartCoder"
```

**Response:**
```json
{
  "success": true,
  "username": "ISmartCoder",
  "count": 2,
  "stories": [
    {
      "story_id": 9,
      "type": "Active",
      "date": "2026-01-08 14:30:00",
      "timestamp": 1736348400,
      "caption": "Example caption",
      "has_media": true
    }
  ],
  "api_dev": "@ISmartCoder",
  "api_channel": "@abirxdhackz"
}
```

### 3. Get All Stories
Fetch all stories including active, pinned, and archived.

```http
GET /api/all?username={username}
```

**Example:**
```bash
curl "https://your-deployment.vercel.app/api/all?username=ISmartCoder"
```

**Response:**
```json
{
  "success": true,
  "username": "ISmartCoder",
  "total_count": 15,
  "stories": [
    {
      "story_id": 9,
      "type": "Active",
      "date": "2026-01-08 14:30:00",
      "timestamp": 1736348400,
      "caption": "Example caption",
      "has_media": true
    },
    {
      "story_id": 7,
      "type": "Pinned",
      "date": "2026-01-07 10:15:00",
      "timestamp": 1736245500,
      "caption": "",
      "has_media": true
    }
  ],
  "api_dev": "@ISmartCoder",
  "api_channel": "@abirxdhackz"
}
```

### 4. Download Specific Story
Download a specific story by username and story ID.

```http
GET /api/special?username={username}&storyid={story_id}
```

**Example:**
```bash
curl "https://your-deployment.vercel.app/api/special?username=ISmartCoder&storyid=9"
```

**Response:**
```json
{
  "success": true,
  "username": "ISmartCoder",
  "story_id": 9,
  "type": "Active",
  "media_type": "video",
  "date": "2026-01-08 14:30:00",
  "timestamp": 1736348400,
  "caption": "Example caption",
  "download_url": "https://tmpfiles.org/dl/12345/video.mp4",
  "expires_in": "60 minutes",
  "api_dev": "@ISmartCoder",
  "api_channel": "@abirxdhackz"
}
```

### 5. Download from Direct URL (NEW!)
Download a story directly from a Telegram story URL.

```http
GET /api/direct?url={telegram_story_url}
```

**Example:**
```bash
curl "https://your-deployment.vercel.app/api/direct?url=https://t.me/ISmartCoder/s/9"
```

**Supported URL formats:**
- `https://t.me/username/s/storyid`
- `https://telegram.me/username/s/storyid`
- `https://t.me/c/channelid/storyid`
- `https://telegram.me/c/channelid/storyid`

**Response:**
```json
{
  "success": true,
  "username": "ISmartCoder",
  "story_id": 9,
  "type": "Active",
  "media_type": "video",
  "date": "2026-01-08 14:30:00",
  "timestamp": 1736348400,
  "caption": "Example caption",
  "download_url": "https://tmpfiles.org/dl/12345/video.mp4",
  "expires_in": "60 minutes",
  "source_url": "https://t.me/ISmartCoder/s/9",
  "api_dev": "@ISmartCoder",
  "api_channel": "@abirxdhackz"
}
```

## Deployment

### Deploy to Vercel

1. Fork this repository
2. Create a new project on [Vercel](https://vercel.com)
3. Import your forked repository
4. Add the required environment variable:
   - `SESSION_STRING`: Your Telegram session string
5. Deploy

### Get Telegram Session String

You need a Telegram session string to authenticate with the API. You can generate one using:

1. Install Pyrogram: `pip install pyrofork`
2. Run this script:

```python
from pyrogram import Client

api_id = "YOUR_API_ID"
api_hash = "YOUR_API_HASH"

with Client("my_account", api_id=api_id, api_hash=api_hash) as app:
    print(app.export_session_string())
```

3. Get your API credentials from [my.telegram.org](https://my.telegram.org)

### Environment Variables

Create a `config.py` file or set environment variables:

```python
SESSION_STRING = "your_session_string_here"
```

## Local Development

### Prerequisites

- Python 3.10+
- Telegram API credentials
- Telegram session string

### Installation

1. Clone the repository:
```bash
git clone https://github.com/SmartStoryDownloader
cd SmartStoryDownloader
```

2. Install dependencies:
```bash
pip install -r requirements.txt
```

3. Create `templates` directory and add `index.html`:
```bash
mkdir templates
```

4. Create `config.py`:
```python
SESSION_STRING = "your_session_string_here"
```

5. Run the server:
```bash
python api.py
```

The API will be available at:
- Web Interface: `http://localhost:4747`
- API Endpoints: `http://localhost:4747/api/*`

## Project Structure

```
.
api.py              
config.py           
templates/index.html      
requirements.txt    
pyproject.toml     
README.md           
```

## Dependencies

- **fastapi**: Modern web framework for building APIs
- **uvicorn**: ASGI server implementation
- **pyrofork**: Telegram MTProto API framework
- **tgcrypto**: Cryptography for Telegram
- **aiohttp**: Async HTTP client/server
- **uvloop**: Fast event loop implementation
- **python-dateutil**: Date utilities
- **jinja2**: Template engine for web interface

## Web Interface Features

### Download Methods

1. **Direct URL** - Paste any Telegram story URL
2. **Username & ID** - Enter username and story ID manually
3. **Browse Stories** - View and download all stories from a user

### Interactive Elements

- Real-time loading indicators
- Success/error notifications
- Story grid with metadata display
- One-click downloads
- Copy link functionality
- Responsive design for all devices

## Error Handling

The API returns appropriate HTTP status codes:

- `200`: Success
- `400`: Bad request (invalid parameters)
- `404`: Story not found
- `500`: Server error

All error responses include:
```json
{
  "success": false,
  "error": "Error description",
  "api_dev": "@ISmartCoder",
  "api_channel": "@abirxdhackz"
}
```

## Rate Limiting

Be mindful of Telegram's rate limits. Excessive requests may result in temporary restrictions.

## Notes

- Downloaded files are temporarily hosted on tmpfiles.org and expire after 60 minutes
- The API supports photos, videos, and documents
- Stories are automatically searched in active, pinned, and archived collections
- Web interface auto-detects the API base URL for seamless operation
- All downloads include metadata (caption, date, type, etc.)

## Security

- Session strings are securely stored in environment variables
- No sensitive data is exposed through the API
- HTTPS recommended for production deployments
- Rate limiting recommended for public deployments

## Browser Support

The web interface supports all modern browsers:
- Chrome/Edge (latest)
- Firefox (latest)
- Safari (latest)
- Mobile browsers

## Credits

- **Developer**: Abir Arafat Chawdhury - [@ISmartCoder](https://t.me/ISmartCoder)
- **Channel**: [@abirxdhackz](https://t.me/abirxdhackz)
- **Community**: [@TheSmartDev](https://t.me/TheSmartDev)
- **GitHub**: [TheSmartDevs](https://github.com/TheSmartDevs)

## License

This project is open source and available under the MIT License.

## Support

For issues, questions, or contributions, please visit:
- **GitHub**: [github.com/SmartStoryDownloader](https://github.com/SmartStoryDownloader)
- **Telegram**: [@ISmartCoder](https://t.me/ISmartCoder)
- **Developer Community**: [@TheSmartDev](https://t.me/TheSmartDev)

## Changelog

### Version 28.0.0
- âœ¨ Added beautiful web interface
- âœ¨ Direct URL download support (`/api/direct`)
- âœ¨ Interactive story browser
- âœ¨ Real-time notifications
- âœ¨ Responsive design
- âœ¨ Auto-detect API base URL
- ðŸ”§ Improved error handling
- ðŸ”§ Enhanced metadata display
- ðŸ“š Complete documentation

## Roadmap

- [ ] Bulk download support
- [ ] Story analytics
- [ ] Video preview
- [ ] Download history
- [ ] Custom expiry times
- [ ] Authentication system

## Disclaimer

This tool is for educational purposes only. Make sure you comply with Telegram's Terms of Service and respect users' privacy. The developers are not responsible for any misuse of this tool.

---

**Made with â¤ï¸ by Abir Arafat Chawdhury ðŸ‡§ðŸ‡©**