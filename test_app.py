import queue
import unittest
import threading
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from src.manifest import parse_manifest
from src.models import (
    FileEntry,
    TorrentShare,
    format_bytes,
    group_share_files,
    is_magnet_link,
    normalize_link,
    safe_filename,
    sort_share_files,
)
from src.storage import DownloadStore
from src.transfer import LinkClient
from src.ui import DropLinkApp
from src.ui.clipboard import ClipboardMixin
from src.ui.downloads import DownloadsMixin, load_progress_timed_out
from src.ui.file_list import FileListMixin, build_directory_tree


class ManifestTests(unittest.TestCase):
    def test_parses_manifest_and_resolves_relative_urls(self) -> None:
        name, files = parse_manifest(
            {
                "name": "Team share",
                "files": [{"name": "notes.txt", "url": "files/notes.txt", "size": 2048}],
            },
            "https://peer.example/share.json",
        )
        self.assertEqual(name, "Team share")
        self.assertEqual(files[0].url, "https://peer.example/files/notes.txt")
        self.assertEqual(files[0].size, "2.0 KB")

    def test_rejects_manifest_without_downloadable_files(self) -> None:
        with self.assertRaises(ValueError):
            parse_manifest({"files": [{"name": "missing-url"}]})

    def test_sanitizes_download_filename(self) -> None:
        self.assertEqual(safe_filename("..\\secret<>.txt"), "secret__.txt")

    def test_format_bytes(self) -> None:
        self.assertEqual(format_bytes(0), "0 B")
        self.assertEqual(format_bytes(1024 * 1024), "1.0 MB")

    def test_accepts_magnet_links(self) -> None:
        self.assertTrue(is_magnet_link("MAGNET:?xt=urn:btih:abc"))
        self.assertEqual(normalize_link("ptp://peer.example/share"), "https://peer.example/share")

    def test_fetch_manifest_reports_loading_progress(self) -> None:
        manifest = b'{"name":"Team share","files":[{"name":"notes.txt","url":"notes.txt"}]}'
        response = BytesIO(manifest)
        response.headers = {"Content-Length": str(len(manifest))}
        progress: list[tuple[str, int]] = []

        with patch("src.transfer.urllib.request.urlopen", return_value=response):
            name, files = LinkClient.fetch_manifest(
                "https://peer.example/share.json",
                progress=lambda status, percentage: progress.append((status, percentage)),
            )

        self.assertEqual(name, "Team share")
        self.assertEqual(files[0].name, "notes.txt")
        self.assertEqual(progress[0], ("Connecting to peer", 5))
        self.assertEqual(progress[-1], ("Share ready", 100))
        self.assertTrue(any("Reading share manifest" in status for status, _ in progress))

    def test_sorts_binary_before_text_then_by_extension(self) -> None:
        files = [
            FileEntry("readme.txt", ""),
            FileEntry("cover.jpg", ""),
            FileEntry("archive.zip", ""),
            FileEntry("data.csv", ""),
        ]
        self.assertEqual([file.name for file in sort_share_files(files)], ["cover.jpg", "archive.zip", "data.csv", "readme.txt"])

    def test_groups_files_by_text_category_and_extension(self) -> None:
        files = [FileEntry("a.txt", ""), FileEntry("b.txt", ""), FileEntry("image.png", "")]
        groups = group_share_files(files)
        self.assertEqual([(is_text, extension, len(group)) for is_text, extension, group in groups], [(False, ".png", 1), (True, ".txt", 2)])

    def test_share_directory_tree_supports_parent_and_nested_selection(self) -> None:
        album_one = FileEntry("Artist/Studio Albums/Album One/track-1.flac", "")
        album_two = FileEntry("Artist/Studio Albums/Album Two/track-2.flac", "")
        live_album = FileEntry("Artist/Live Albums/Show/track-3.flac", "")
        root, directories = build_directory_tree([album_one, album_two, live_album])

        self.assertEqual(set(root.directories), {"Artist"})
        self.assertEqual(
            [file.name for file in directories[("Artist", "Studio Albums")]],
            [album_one.name, album_two.name],
        )
        self.assertEqual(directories[("Artist", "Studio Albums", "Album One")], [album_one])
        self.assertEqual(
            {file.name for file in directories[("Artist",)]},
            {album_one.name, album_two.name, live_album.name},
        )

    def test_selecting_directory_and_nested_album_are_independent(self) -> None:
        class SelectionVariable:
            def __init__(self) -> None:
                self.selected = True

            def get(self) -> bool:
                return self.selected

            def set(self, selected: bool) -> None:
                self.selected = selected

        album_one = FileEntry("Artist/Studio Albums/Album One/track-1.flac", "")
        album_two = FileEntry("Artist/Studio Albums/Album Two/track-2.flac", "")
        live_album = FileEntry("Artist/Live Albums/Show/track-3.flac", "")
        _, directory_files = build_directory_tree([album_one, album_two, live_album])

        class SelectionHarness:
            group_files = directory_files
            files = [album_one, album_two, live_album]
            busy = False
            file_vars = {
                id(file): SelectionVariable()
                for file in (album_one, album_two, live_album)
            }
            file_group_keys = {
                id(album_one): (
                    ("Artist",),
                    ("Artist", "Studio Albums"),
                    ("Artist", "Studio Albums", "Album One"),
                ),
                id(album_two): (
                    ("Artist",),
                    ("Artist", "Studio Albums"),
                    ("Artist", "Studio Albums", "Album Two"),
                ),
                id(live_album): (
                    ("Artist",),
                    ("Artist", "Live Albums"),
                    ("Artist", "Live Albums", "Show"),
                ),
            }
            selected_file_count = 3
            group_selected_counts = {
                key: len(files)
                for key, files in directory_files.items()
            }
            group_selected_sizes = {key: 0 for key in directory_files}
            selection_var = Mock()
            download_button = Mock()
            _update_selection = Mock()

            def _refresh_selection_display(
                self,
                files: list[FileEntry],
                groups: set[tuple[str, ...]],
            ) -> None:
                FileListMixin._refresh_selection_display(self, files, groups)

        app = SelectionHarness()
        FileListMixin._set_group_selection(app, ("Artist", "Studio Albums"), False)
        FileListMixin._set_group_selection(app, ("Artist", "Studio Albums", "Album Two"), True)

        self.assertFalse(app.file_vars[id(album_one)].get())
        self.assertTrue(app.file_vars[id(album_two)].get())
        self.assertTrue(app.file_vars[id(live_album)].get())
        self.assertEqual(app.selected_file_count, 2)
        self.assertEqual(app.group_selected_counts[("Artist", "Studio Albums")], 1)
        app._update_selection.assert_not_called()

    def test_directory_subtrees_stay_within_their_extension_section(self) -> None:
        flac_track = FileEntry("Artist/Studio Albums/Album One/track.flac", "")
        jpg_cover = FileEntry("Artist/Studio Albums/Album One/cover.jpg", "")
        files = [flac_track, jpg_cover]
        sections = group_share_files(files)

        self.assertEqual(
            [(is_text, extension, [file.name for file in grouped]) for is_text, extension, grouped in sections],
            [
                (False, ".flac", [flac_track.name]),
                (False, ".jpg", [jpg_cover.name]),
            ],
        )
        for _, extension, grouped_files in sections:
            _, directories = build_directory_tree(grouped_files)
            self.assertEqual(
                [file.name for file in directories[("Artist", "Studio Albums", "Album One")]],
                [f"Artist/Studio Albums/Album One/{'cover.jpg' if extension == '.jpg' else 'track.flac'}"],
            )

    def test_download_store_saves_reads_and_removes_records(self) -> None:
        with TemporaryDirectory() as directory:
            store = DownloadStore(Path(directory) / "shares.json")
            record = {"link": "magnet:?xt=urn:btih:test", "name": "Test", "files": []}
            store.save(record)
            self.assertEqual(store.get(record["link"])["name"], "Test")
            self.assertEqual(len(store.records()), 1)
            store.remove(record["link"])
            self.assertEqual(store.records(), [])

    def test_download_store_purges_completed_records(self) -> None:
        with TemporaryDirectory() as directory:
            store = DownloadStore(Path(directory) / "shares.json")
            store.save({
                "link": "magnet:?xt=urn:btih:complete",
                "name": "Complete",
                "files": [{"index": 0, "selected": True, "completed": True}],
            })
            self.assertEqual(store.records(), [])

    def test_cleanup_completed_torrent_removes_unselected_files_and_handle(self) -> None:
        class TorrentFiles:
            def num_files(self) -> int:
                return 2

            def file_path(self, index: int) -> str:
                return ["keep.txt", "remove.bin"][index]

        class Torrent:
            def files(self) -> TorrentFiles:
                return TorrentFiles()

        class Handle:
            def torrent_file(self) -> Torrent:
                return Torrent()

        class Session:
            def __init__(self) -> None:
                self.removed = None

            def remove_torrent(self, handle: Handle) -> None:
                self.removed = handle

        with TemporaryDirectory() as directory:
            destination = Path(directory)
            kept = destination / "keep.txt"
            removed = destination / "remove.bin"
            kept.write_text("keep", encoding="utf-8")
            removed.write_text("remove", encoding="utf-8")
            handle = Handle()
            session = Session()
            share = TorrentShare(handle, session, "magnet:?xt=test", "Test", set(), threading.Lock())
            LinkClient.cleanup_completed_torrent([FileEntry("keep.txt", "", torrent_index=0)], share, destination)
            self.assertTrue(kept.exists())
            self.assertFalse(removed.exists())
            self.assertIs(session.removed, handle)

    def test_cleanup_temporary_downloads_removes_partial_files(self) -> None:
        with TemporaryDirectory() as directory:
            destination = Path(directory)
            file = FileEntry("report.pdf", "")
            target = destination / "report.pdf"
            temporary = destination / "report.pdf.part"
            target.write_text("complete", encoding="utf-8")
            temporary.write_text("partial", encoding="utf-8")

            LinkClient.cleanup_temporary_downloads([file], destination)

            self.assertTrue(target.exists())
            self.assertFalse(temporary.exists())


class UiHandlerTests(unittest.TestCase):
    def test_loading_share_name_comes_from_saved_name_magnet_or_peer(self) -> None:
        self.assertEqual(DropLinkApp._loading_share_name("magnet:?xt=urn:test&dn=Film%20Name", None), "Film Name")
        self.assertEqual(DropLinkApp._loading_share_name("https://peer.example/team.json", None), "team")
        self.assertEqual(DropLinkApp._loading_share_name("magnet:?xt=urn:test", {"name": "Saved share"}), "Saved share")

    def test_load_times_out_after_one_minute_without_progress(self) -> None:
        self.assertFalse(load_progress_timed_out(100.0, 159.9))
        self.assertTrue(load_progress_timed_out(100.0, 160.0))

    def test_successful_download_completion_clears_share_and_shows_summary(self) -> None:
        class CompletionHarness:
            def __init__(self) -> None:
                self.events = queue.Queue()
                self.events.put(("finished", (1, 1, [], Path("downloads"), "Example share", "2.0 MB")))
                self.download_running = True
                self.load_in_progress = False
                self.torrent_share = None
                self.share_control_var = Mock()
                self.status_var = Mock()
                self._set_busy = Mock()
                self._clear_loaded_share = Mock()
                self.after = Mock()

            def _process_events(self) -> None:
                DownloadsMixin._process_events(self)

        app = CompletionHarness()
        with patch("src.ui.downloads.messagebox.showinfo") as showinfo:
            app._process_events()

        app._clear_loaded_share.assert_called_once_with()
        self.assertFalse(app.download_running)
        app.status_var.set.assert_called_once_with("Use Load files to choose a peer link")
        showinfo.assert_called_once_with("DropLink", "Download 2.0 MB finished for Example share")

    def test_menu_command_handlers_exist(self) -> None:
        for handler in (
            "show_load_dialog",
            "show_saved_shares",
            "stop_all_downloads",
            "resume_all_downloads",
            "show_settings_dialog",
        ):
            with self.subTest(handler=handler):
                self.assertTrue(callable(getattr(DropLinkApp, handler, None)))

    def test_accepting_detected_magnet_starts_loading(self) -> None:
        class ClipboardApp:
            def __init__(self) -> None:
                self.last_clipboard_text = ""
                self.auto_detect_magnets_var = Mock()
                self.auto_detect_magnets_var.get.return_value = True
                self.busy = False
                self.clipboard_prompt_active = False
                self.link_var = Mock()
                self.load_link = Mock()

            def clipboard_get(self) -> str:
                return "magnet:?xt=urn:btih:test&dn=Example"

            def _get_magnet_title(self, _clipboard_text: str) -> str:
                return "Example"

            def _poll_clipboard(self) -> None:
                ClipboardMixin._poll_clipboard(self)

            def after(self, _delay: int, _callback: object) -> None:
                pass

        app = ClipboardApp()
        with patch("src.ui.clipboard.messagebox.askyesno", return_value=True):
            ClipboardMixin._poll_clipboard(app)

        app.link_var.set.assert_called_once_with("magnet:?xt=urn:btih:test&dn=Example")
        app.load_link.assert_called_once_with()
        self.assertFalse(app.clipboard_prompt_active)


if __name__ == "__main__":
    unittest.main()
