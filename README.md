# Anime Video Downloader

## About

A command-line tool for batch-downloading anime episodes from Xifan Video (`next.xifanacg.com`). It reads the episodes and available sources from an anime page, lets you choose a source, and downloads episodes in parallel. Its primary purpose is to download episodes in bulk for local viewing.

Features:

- Automatically detects episodes and available sources. You can choose a preferred source, and the downloader will try other sources if it fails.
- Supports resuming direct MP4 downloads.
- Supports downloading HLS (m3u8) segments; with ffmpeg installed, segments can be remuxed into MP4.
- Skips episode files that already exist. Press `Ctrl+C` during a download to stop.

## Installation

Requires Python 3.10 or later; Python 3.12.* is recommended.

Run this from the project root:

```powershell
.\activate.ps1 # Create and activate a virtual environment, then install dependencies
```

If PowerShell does not allow the activation script to run, you can leave the virtual environment inactive and use its Python executable directly to install dependencies and run the downloader.

Remuxing HLS to MP4 requires ffmpeg. Make sure `ffmpeg` is on `PATH`, or set the `FFMPEG` environment variable to its executable path. If ffmpeg is not found, HLS downloads are kept as TS files. You can search for ffmpeg in your browser or visit `https://www.gyan.dev/ffmpeg/builds/` to choose a suitable release build and add it to your `PATH`.

## Usage

Run from the project root:

```powershell
.\venv\Scripts\activate # Activate the virtual environment
python src\main.py <anime_id> "<folder_name>" [options] # Run the downloader
```

For example:

```powershell
python src\main.py 12345 "<folder_name>"
python src\main.py 12345 "<folder_name>" --source CS --workers 4
```

The anime ID and folder name are optional; if omitted, the program will prompt for them. The anime ID comes from the Xifan database; search for the video on the official Xifan site to find it.

### Options

| Option | Description |
| --- | --- |
| `-s`, `--source` | Specify a download source by its number or code, such as `1`, `xfxf1`, `AL`, or `CS`. If omitted, you can choose a source while the program is running. |
| `-j`, `--workers` | Number of parallel downloads. Defaults to `3`. |
| `--headful` | Show the browser window for debugging. |
| `-h`, `--help` | Show help. |

Downloaded files are saved in a directory named after the folder name under the current working directory. The progress bar is shown only in interactive terminals by default; set `XIFAN_PROGRESS=0` or `XIFAN_PROGRESS=1` to force it off or on.
