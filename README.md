# DropLink

A small Windows desktop app for reviewing and downloading files from a peer share.

## Run

Requires Python 3.10 or newer. Tkinter is included with the standard Windows Python installer. The launcher installs the torrent engine for magnet-link support:

```powershell
.\run-app.ps1
```

The script creates and activates a `.venv` environment if needed, installs the requirements, and starts the app. To run it from a PowerShell session that blocks local scripts, use `powershell -ExecutionPolicy Bypass -File .\run-app.ps1`.

Use **File > Download New Link...** to enter a link, or choose **Resume saved (N)** to reopen an unfinished magnet. Each link opens an independent window for metadata loading and file selection, so you can prepare several magnet links at once. Starting a download closes that window and adds the transfer to the main window's active-download list.

Run the local tests with:

```powershell
python -m unittest -v
```

## Link format

DropLink accepts `https://`, `http://`, `ptp://`, and BitTorrent `magnet:?` links. Magnet links are resolved through the BitTorrent DHT using `libtorrent`; metadata must be available from peers before the file list can be shown. This means a magnet link may take a little while to load and requires network access.

Only checked torrent files are assigned download priority. Unchecked files are left untouched. The selection window lets you choose a destination for each link. The torrent engine supports common magnet links such as those copied from torrent index sites.

While magnet metadata loads, the popup shows the current number of connected peers. This live count can decrease when peers disconnect or network connections are dropped; it is not a cumulative count.

Files are sorted with non-text files first and then by extension in the selection window. The main window aggregates each active download's file count, progress, and status; the footer shows the average progress across active transfers.

When a magnet download starts, DropLink stores its link, destination, file selection, and completion state under `%LOCALAPPDATA%\DropLink\shares.json`. Each transfer continues independently, and `Pause all` pauses or resumes all active magnet downloads. Use `Resume saved (N)` in a later session to reload an unfinished share. Records are removed automatically once every selected file in that share is complete.

After all selected files finish downloading successfully, DropLink removes any leftover temporary download files and displays the total selected size and share name.

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