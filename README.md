# TuneVault

Premium Windows-first media library app.

TuneVault is a local-first media manager: your files stay on your PC, while the app gives you a focused interface for organizing folders and staging downloads.

## Current features

- Explicit library access: only folders the user grants are visible.
- Duplicate / nested root protection.
- Real folder browsing with a media-focused interface rather than a generic file explorer clone.
- Create folders, rename, move and delete.
- **Staged changes:** yellow actions appear immediately in the UI but the Windows filesystem is changed only by **Save Changes**.
- Per-operation results: successful operations become history entries; failures stay available for retry.
- **Undo last staged action** before saving.
- **Retry failed** operations.
- Global search across every accessible library.
- Automatic light refresh to notice external Explorer changes.
- Add Media dialog with local `yt-dlp` URL detection.
- Thumbnail preview, title, creator and duration metadata when provided by the source.
- Single downloads in MP3 or MP4.
- Playlist / batch detection with MP3/MP4 controls per item and quick **All MP3 / All MP4** actions.
- Background processing so the UI remains responsive.
- FFmpeg supplied through `imageio-ffmpeg` for audio conversion and video merging.
- No TuneVault cloud storage is required for the local workflow.

## Run locally

Requires Python 3.11+ on Windows.

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python main.py
```

## Build the Windows application

GitHub Actions builds a Windows executable automatically from `.github/workflows/build.yml` and uploads a `TuneVault-Windows` artifact containing the packaged app.

## Design principles

1. Local files remain local.
2. User actions are staged first.
3. Saving is the explicit commit point to the real filesystem.
4. The app should feel like a premium media library, not Windows File Explorer with a new skin.

## Important

Only download media you are authorized to download and use. Availability and format support depend on the source service.
