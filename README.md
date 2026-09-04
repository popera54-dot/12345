# TuneVault

Premium Windows media library prototype.

## What is implemented
- Local-first library: only folders explicitly granted by the user are visible.
- Add Folder with duplicate / nested-root protection.
- Folder browsing, create, rename and delete as pending changes.
- Yellow pending state; changes are applied to the real Windows filesystem only after **Save changes**.
- Add Media dialog with URL detection via `yt-dlp`.
- Automatic title/duration/creator detection.
- MP3 and MP4 download options.
- Downloads are performed locally on the user's PC; the app does not use cloud storage.
- FFmpeg is supplied through `imageio-ffmpeg` for conversion/merging.

## Run locally

Requires Python 3.11+ on Windows.

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python main.py
```

## Build the Windows application

GitHub Actions builds a Windows executable automatically from `.github/workflows/build.yml`.
The workflow uploads a `TuneVault-Windows` artifact containing the packaged app.

## Important

Only download media you are authorized to download and use. Availability and format support depend on the source service.
