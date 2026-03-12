# Luminary — Local Media Gallery

A lightweight, local-first photo gallery. Index photos from your filesystem, browse them with a beautiful dark UI, organise into albums, and manage visibility — all with no cloud dependency.

---

## Requirements

- Python 3.8+
- pip

---

## Installation

```bash
# 1. Clone / download the project folder
cd luminary

# 2. Install dependencies manually (or let run.sh do it)
pip install flask flask-cors Pillow
```

---

## Configuration

### `media.json` — Add your photo directories

```json
[
  {
    "name": "Travel Photos",
    "path": "/Users/you/Pictures/Travel/",
    "visibility": true
  },
  {
    "name": "Family",
    "path": "/Users/you/Pictures/Family/",
    "visibility": true
  }
]
```

- **name** — label shown in logs
- **path** — absolute path to the directory
- **visibility** — `false` skips scanning this directory

### `configuration.json` — Tune behaviour

| Key | Default | Description |
|-----|---------|-------------|
| `supported_formats` | jpg, jpeg, png, heic… | File extensions to index |
| `thumbnail_size` | 300 | Thumbnail dimension (px) |
| `lazy_load_batch` | 50 | Images loaded per scroll batch |
| `show_hidden_default` | false | Show hidden media on load |
| `api_port` | 5000 | Backend port |

---

## Running the Application

```bash
chmod +x run.sh
./run.sh
```

Then open **http://localhost:5000** in your browser.

To run an initial sync before starting the server:

```bash
./run.sh --sync
```

Or start the backend directly:

```bash
python3 app.py
python3 app.py --port 8080
python3 app.py --sync-only   # scan + exit, no server
```

---

## Sync Process

1. Open the gallery in your browser
2. Click the **↻ Sync** button in the top-right
3. The backend reads `media.json`, scans all visible directories, extracts EXIF metadata, and updates `db.json`
4. New photos appear immediately — no page refresh needed

Or trigger via the API directly:

```bash
curl -X POST http://localhost:5000/api/sync
```

---

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/media` | All indexed media |
| GET | `/api/albums` | All albums |
| POST | `/api/sync` | Scan and index new files |
| GET | `/api/db` | Full database |
| POST | `/api/db` | Save updated database |
| POST | `/api/album/create` | Create album `{"name": "…"}` |
| POST | `/api/album/add` | Add to album `{"albumId": "…", "uniqueName": "…"}` |
| POST | `/api/media/hide` | Toggle hide `{"uniqueName": "…", "hidden": true}` |

---

## Features

- **Masonry grid** with lazy loading (batches of 50)
- **Album management** — create, add/remove, delete
- **Per-image context menu** — hide, album, metadata
- **Full EXIF metadata viewer** — camera, GPS, software
- **Search** by filename, camera model, date
- **Filter** by format and camera
- **Sort** by date (newest/oldest) or name
- **Lightbox** with keyboard navigation (← →, Esc)
- **Hidden media** toggle

---

## File Structure

```
luminary/
├── index.html          # Frontend SPA
├── app.py              # Backend + API
├── media.json          # Directory sources
├── configuration.json  # App settings
├── db.json             # Media + album database
├── run.sh              # Startup script
└── README.md           # This file
```

---

## Troubleshooting

**No photos showing after sync**
- Check that paths in `media.json` are absolute and exist
- Confirm `visibility: true` on the desired sources
- Check terminal output for scan errors

**EXIF data not extracted**
- Install Pillow: `pip install Pillow`
- Some formats (HEIC on Linux) may require additional system libraries

**Backend not reachable (Sync fails)**
- Make sure `app.py` is running on the configured port
- Check for port conflicts: `lsof -i :5000`

**Images not loading in gallery**
- The frontend loads images directly by filesystem path
- Ensure the browser has access to the file paths (same machine)
- For network access, serve the photo directories via the backend or nginx

---

## Possible Enhancements

- Thumbnail generation (WebP cache)
- Timeline view (year → month → day)
- Map view using GPS coordinates
- Face / object AI tagging
- Video support (mp4, mov)
- Multi-device network access
