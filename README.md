# DropLink

A small Windows desktop app for reviewing and downloading files from a peer share.

## Run

Requires Python 3.10 or newer. Tkinter is included with the standard Windows Python installer. Install the torrent engine for magnet-link support:

```powershell
python -m pip install -r requirements.txt
```

```powershell
python -m src.main
```

Use the `Load files` menu at the top of the window to enter a link, try the demo share, or resume an unfinished magnet. Drag the divider between the share and download-location sections to change their vertical sizes.

Run the local tests with:

```powershell
python -m unittest -v
```

## Link format

DropLink accepts `https://`, `http://`, `ptp://`, and BitTorrent `magnet:?` links. Magnet links are resolved through the BitTorrent DHT using `libtorrent`; metadata must be available from peers before the file list can be shown. This means a magnet link may take a little while to load and requires network access.

Only checked torrent files are assigned download priority. Unchecked files are left untouched. The torrent engine uses the configured download location and supports common magnet links such as those copied from torrent index sites.

Torrent files are sorted into binary and text sections, then grouped by extension. Use the arrow on a group to collapse it. Text groups also include a `Deselect text` action, which clears every checked file in that extension group. Each visible file has its own progress percentage and the footer shows the size-weighted percentage across all selected files.

When a magnet download starts, DropLink stores its link, destination, file selection, and completion state under `%LOCALAPPDATA%\DropLink\shares.json`. Use `Resume saved (N)` in a later session to reload an unfinished share. `Pause all` pauses or resumes the whole torrent, while each torrent file has its own `Pause`/`Resume` button. Records are removed automatically once every selected file in that share is complete.

The `ptp://` scheme is currently an alias for an HTTPS manifest endpoint, so a peer service can expose a link such as `ptp://peer.example/share/abc` while keeping transport details behind the endpoint.

The manifest can be an object:

```json
{
	"name": "Project files",
	"files": [
		{"name": "report.pdf", "url": "files/report.pdf", "size": 248832}
	]
}
```

It may also be a plain array of file objects. Each file needs a `name` and `url`; `filename`, `download`, and `type` are also accepted aliases. Relative file URLs are resolved against the manifest URL.

## Packaging for Windows

For a standalone executable, install PyInstaller and build it from this folder:

```powershell
python -m pip install pyinstaller
pyinstaller --noconsole --onefile --name DropLink src/main.py
```

The executable will be placed in `dist\DropLink.exe`. Build it in the same environment where `libtorrent` is installed so magnet support is included.