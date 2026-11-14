import queue
import sys
import unittest
import threading
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Callable
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
from src.ui.downloads import DownloadJob, DownloadsMixin, metadata_load_timed_out
from src.ui.file_list import FileListMixin, build_directory_tree
from src.ui.selection import ShareSelectionWindow, magnet_link_name


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

    def test_fetch_magnet_extends_metadata_timeout_for_additional_peers(self) -> None:
        class FakeClock:
            current_time = 0.0

            def monotonic(self) -> float:
                return self.current_time

            def sleep(self, seconds: float) -> None:
                self.current_time += seconds

        class FakeStatus:
            def __init__(self, clock: FakeClock) -> None:
                if clock.current_time >= 150:
                    self.num_peers = 0
                elif clock.current_time >= 120:
                    self.num_peers = 1
                elif clock.current_time >= 90:
                    self.num_peers = 2
                elif clock.current_time >= 60:
                    self.num_peers = 1
                else:
                    self.num_peers = 0

        class FakeHandle:
            def __init__(self, clock: FakeClock) -> None:
                self.clock = clock

            def has_metadata(self) -> bool:
                return self.clock.current_time >= 195

            def pause(self) -> None:
                pass

            def status(self) -> FakeStatus:
                return FakeStatus(self.clock)

            def torrent_file(self) -> "FakeTorrent":
                return FakeTorrent()

            def file_priority(self, _index: int, _priority: int) -> None:
                pass

        class FakeFiles:
            def num_files(self) -> int:
                return 1

            def file_path(self, _index: int) -> str:
                return "example.txt"

            def file_size(self, _index: int) -> int:
                return 8

        class FakeTorrent:
            def files(self) -> FakeFiles:
                return FakeFiles()

            def name(self) -> str:
                return "Example"

        class FakeParams:
            def __init__(self) -> None:
                self.save_path = ""

        class FakeSession:
            def __init__(self, clock: FakeClock) -> None:
                self.clock = clock
                self.handles: list[FakeHandle] = []
                self.removed: list[FakeHandle] = []
                self.add_times: list[float] = []
                self.added_params: list[FakeParams] = []

            def add_torrent(self, params: FakeParams) -> FakeHandle:
                handle = FakeHandle(self.clock)
                self.handles.append(handle)
                self.add_times.append(self.clock.current_time)
                self.added_params.append(params)
                return handle

            def remove_torrent(self, handle: FakeHandle) -> None:
                self.removed.append(handle)

        clock = FakeClock()
        session = FakeSession(clock)
        parsed_links: list[str] = []

        class FakeLibtorrent:
            @staticmethod
            def parse_magnet_uri(link: str) -> FakeParams:
                parsed_links.append(link)
                return FakeParams()

            @staticmethod
            def session() -> FakeSession:
                return session

        progress: list[tuple[str, int, float]] = []

        with (
            patch.dict(sys.modules, {"libtorrent": FakeLibtorrent}),
            patch("src.transfer.time.monotonic", side_effect=clock.monotonic),
            patch("src.transfer.time.sleep", side_effect=clock.sleep),
        ):
            name, files, share = LinkClient.fetch_magnet(
                "magnet:?xt=urn:btih:test",
                Path("downloads"),
                progress=lambda status, percentage: progress.append((status, percentage, clock.current_time)),
            )

        self.assertEqual(name, "Example")
        self.assertEqual(files[0].name, "example.txt")
        self.assertIs(share.handle, session.handles[0])
        self.assertEqual(len(session.handles), 1)
        self.assertEqual(session.removed, [])
        self.assertGreaterEqual(clock.current_time, 195)
        metadata_progress = [item for item in progress if "Waiting for peer metadata" in item[0]]
        self.assertTrue(any(timestamp >= 60 for _, _, timestamp in metadata_progress))
        self.assertTrue(any("(1 peers connected)" in status for status, _, _ in metadata_progress))
        self.assertTrue(any("(2 peers connected)" in status for status, _, _ in metadata_progress))
        self.assertTrue(any("down from 2" in status for status, _, _ in metadata_progress))
        self.assertTrue(any("(0 peers connected); down from 2" in status for status, _, _ in metadata_progress))
        self.assertLessEqual(
            max(current[2] - previous[2] for previous, current in zip(metadata_progress, metadata_progress[1:])),
            15.25,
        )
        self.assertEqual(session.add_times, [0.0])
        self.assertEqual(parsed_links, ["magnet:?xt=urn:btih:test"])
        self.assertTrue(all(params.save_path == "downloads" for params in session.added_params))

    def test_fetch_magnet_times_out_after_two_minutes_without_peers(self) -> None:
        class FakeClock:
            current_time = 0.0

            def monotonic(self) -> float:
                return self.current_time

            def sleep(self, seconds: float) -> None:
                self.current_time += seconds

        class FakeStatus:
            num_peers = 0

        class FakeHandle:
            def has_metadata(self) -> bool:
                return False

            def pause(self) -> None:
                pass

            def status(self) -> FakeStatus:
                return FakeStatus()

        class FakeSession:
            def __init__(self) -> None:
                self.handle = FakeHandle()
                self.removed: list[FakeHandle] = []

            def add_torrent(self, _params: object) -> FakeHandle:
                return self.handle

            def remove_torrent(self, handle: FakeHandle) -> None:
                self.removed.append(handle)

        class FakeParams:
            save_path = ""

        clock = FakeClock()
        session = FakeSession()

        class FakeLibtorrent:
            @staticmethod
            def parse_magnet_uri(_link: str) -> FakeParams:
                return FakeParams()

            @staticmethod
            def session() -> FakeSession:
                return session

        with (
            patch.dict(sys.modules, {"libtorrent": FakeLibtorrent}),
            patch("src.transfer.time.monotonic", side_effect=clock.monotonic),
            patch("src.transfer.time.sleep", side_effect=clock.sleep),
        ):
            with self.assertRaisesRegex(TimeoutError, "within 120 seconds"):
                LinkClient.fetch_magnet("magnet:?xt=urn:btih:test", Path("downloads"))

        self.assertGreaterEqual(clock.current_time, 120)
        self.assertLess(clock.current_time, 120.25)
        self.assertEqual(session.removed, [session.handle])

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
            download_running = False
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
    def test_each_link_opens_its_own_selection_window(self) -> None:
        class SelectionWindow:
            def lift(self) -> None:
                pass

        class LoadHarness:
            def __init__(self) -> None:
                self.link_var = Mock()
                self.link_var.get.side_effect = [
                    "magnet:?xt=urn:btih:first",
                    "magnet:?xt=urn:btih:second",
                ]
                self.destination_var = Mock()
                self.destination_var.get.return_value = "downloads"
                self.store = Mock()
                self.store.get.return_value = None
                self.selection_windows = {}
                self.download_jobs = {}
                self._start_download = Mock()
                self._selection_window_closed = Mock()

            def load_link(self) -> None:
                DownloadsMixin.load_link(self)

        app = LoadHarness()
        with patch("src.ui.downloads.ShareSelectionWindow", return_value=SelectionWindow()) as window:
            app.load_link()
            app.load_link()

        self.assertEqual(window.call_count, 2)
        self.assertEqual(
            set(app.selection_windows),
            {"magnet:?xt=urn:btih:first", "magnet:?xt=urn:btih:second"},
        )

    def test_metadata_load_times_out_after_two_minutes_only_until_loaded(self) -> None:
        self.assertFalse(metadata_load_timed_out(100.0, 219.9))
        self.assertTrue(metadata_load_timed_out(100.0, 220.0))
        self.assertFalse(metadata_load_timed_out(100.0, 250.0, metadata_loaded=True))

    def test_extracts_name_from_magnet_link_for_popup_title(self) -> None:
        self.assertEqual(
            magnet_link_name("magnet:?xt=urn:btih:test&dn=Example+Share%20Name"),
            "Example Share Name",
        )
        self.assertIsNone(magnet_link_name("magnet:?xt=urn:btih:test"))
        self.assertIsNone(magnet_link_name("https://peer.example/share.json"))

    def test_selection_window_passes_only_checked_files_and_closes(self) -> None:
        selected_file = FileEntry("selected.bin", "", torrent_index=0)
        unchecked_file = FileEntry("unchecked.bin", "", torrent_index=1)

        class SelectionHarness:
            def __init__(self) -> None:
                self.link = "magnet:?xt=urn:btih:share"
                self.name = "Example share"
                self.files = [selected_file, unchecked_file]
                self.file_vars = {
                    id(selected_file): Mock(get=Mock(return_value=True)),
                    id(unchecked_file): Mock(get=Mock(return_value=False)),
                }
                self.destination = Mock()
                self.destination.get.return_value = "downloads"
                self.torrent_share = TorrentShare(
                    Mock(), Mock(), self.link, self.name, set(), threading.Lock()
                )
                self.store = Mock()
                self.on_download = Mock()
                self.close = Mock()

        popup = SelectionHarness()
        ShareSelectionWindow._start_download(popup)

        popup.on_download.assert_called_once_with(
            popup.link,
            popup.name,
            [selected_file],
            popup.torrent_share,
            Path("downloads"),
        )
        popup.close.assert_called_once_with(cancel=False)
        saved = popup.store.save.call_args.args[0]
        self.assertEqual(
            [file["selected"] for file in saved["files"]],
            [True, False],
        )

    def test_torrent_progress_updates_the_aggregate_download_row(self) -> None:
        class ProgressHarness:
            def __init__(self) -> None:
                self.events = queue.Queue()
                self.events.put(
                    (
                        "torrent_progress",
                        (1, {0: (25, 100), 1: (50, 100)}, 421842126, 1124912336),
                    )
                )
                self.current_download_id = 1
                self.download_jobs = {
                    1: DownloadJob(
                        [FileEntry("first.bin", "", torrent_index=0)],
                        Path("downloads"),
                        None,
                        "magnet:?xt=urn:btih:first&dn=First+Magnet+Share",
                        "First share",
                        "100 B",
                    )
                }
                self.downloads_tree = Mock()
                self.downloads_tree.exists.return_value = True
                self.progress_var = Mock()
                self.total_progress_label_var = Mock()
                self.after = Mock()

            def _process_events(self) -> None:
                DownloadsMixin._process_events(self)

            def _refresh_download_row(self, request_id: int, job: DownloadJob) -> None:
                DownloadsMixin._refresh_download_row(self, request_id, job)

            def _refresh_download_summary(self) -> None:
                DownloadsMixin._refresh_download_summary(self)

        app = ProgressHarness()
        app._process_events()

        self.assertEqual(app.download_jobs[1].progress, 37.5)
        self.assertEqual(app.download_jobs[1].downloaded_bytes, 421842126)
        app.downloads_tree.item.assert_called_once_with(
            "download-1",
            text="First Magnet Share",
            values=(1, "100 B", "402.3MB / 38%"),
        )
        app.total_progress_label_var.set.assert_called_once_with("Downloads: 1 active | 38% average")

    def test_regular_download_progress_reports_cumulative_downloaded_bytes(self) -> None:
        class WorkerHarness:
            def __init__(self) -> None:
                self.events = queue.Queue()

        app = WorkerHarness()
        job = DownloadJob(
            [FileEntry("first.bin", "", size_bytes=100), FileEntry("second.bin", "", size_bytes=200)],
            Path("downloads"),
            None,
            "https://peer.example/share.json",
            "Example share",
            "300 B",
        )

        def download_file(
            file: FileEntry,
            destination: Path,
            progress: Callable[[int, int], None],
        ) -> None:
            if file.size_bytes is not None:
                progress(file.size_bytes, file.size_bytes)

        with (
            patch("src.ui.downloads.LinkClient.download", side_effect=download_file),
            patch("src.ui.downloads.LinkClient.cleanup_temporary_downloads"),
        ):
            DownloadsMixin._download_worker(app, 1, job)

        progress_events = [
            payload for event, payload in list(app.events.queue) if event == "progress"
        ]
        self.assertEqual([payload[-1] for payload in progress_events], [100, 300])

    def test_successful_download_completion_updates_main_status(self) -> None:
        class CompletionHarness:
            def __init__(self) -> None:
                self.events = queue.Queue()
                self.events.put(("finished", (1, 1, 1, [])))
                self.download_jobs = {
                    1: DownloadJob(
                        [FileEntry("example.bin", "")],
                        Path("downloads"),
                        None,
                        "",
                        "Example share",
                        "2.0 MB",
                    )
                }
                self.current_download_id = 1
                self.download_running = True
                self.share_control_var = Mock()
                self.status_var = Mock()
                self._update_download_controls = Mock()
                self.after = Mock()

            def _process_events(self) -> None:
                DownloadsMixin._process_events(self)

            def _refresh_download_summary(self) -> None:
                DownloadsMixin._refresh_download_summary(self)

        app = CompletionHarness()
        with patch("src.ui.downloads.messagebox.showinfo") as showinfo:
            app._process_events()

        self.assertFalse(app.download_running)
        app.status_var.set.assert_called_once_with("Download finished for Example share.")
        showinfo.assert_called_once_with("DropLink", "Download 2.0 MB finished for Example share")

    def test_multiple_magnet_downloads_start_without_replacing_active_jobs(self) -> None:
        class DownloadHarness:
            def __init__(self) -> None:
                self.download_running = False
                self.download_request_id = 0
                self.current_download_id = None
                self.download_jobs = {}
                self.downloads_tree = Mock()
                self.downloads_tree.exists.return_value = False
                self.destination_var = Mock()
                self.status_var = Mock()
                self.progress_var = Mock()
                self.total_progress_label_var = Mock()
                self.share_control_var = Mock()
                self.pause_button = Mock()
                self.file_menu = Mock()
                self.stop_all_menu_index = 0
                self.resume_all_menu_index = 1
                self._update_download_controls = Mock()
                self._refresh_resume_button = Mock()
                self._download_worker = Mock()
                self.first_share = TorrentShare(
                    Mock(), Mock(), "magnet:?xt=first", "First", set(), threading.Lock()
                )

            def _refresh_download_row(self, request_id: int, job: DownloadJob) -> None:
                DownloadsMixin._refresh_download_row(self, request_id, job)

            def _refresh_download_summary(self) -> None:
                DownloadsMixin._refresh_download_summary(self)

        app = DownloadHarness()
        first_share = app.first_share
        first_file = FileEntry("first.bin", "", torrent_index=0, size_bytes=1)
        second_file = FileEntry("second.bin", "", torrent_index=0, size_bytes=2)
        third_file = FileEntry("third.bin", "", torrent_index=1, size_bytes=3)
        second_share = TorrentShare(
            Mock(), Mock(), "magnet:?xt=second&dn=Second+Magnet", "Second", set(), threading.Lock()
        )
        with patch("src.ui.downloads.threading.Thread") as worker:
            DownloadsMixin._start_download(
                app,
                first_share.link,
                first_share.name,
                [first_file],
                first_share,
                Path("downloads"),
            )
            DownloadsMixin._start_download(
                app,
                second_share.link,
                second_share.name,
                [second_file, third_file],
                second_share,
                Path("downloads"),
            )

        self.assertEqual(len(worker.call_args_list), 2)
        self.assertEqual(len(app.download_jobs), 2)
        self.assertIs(app.download_jobs[1].torrent_share, first_share)
        self.assertIs(app.download_jobs[2].torrent_share, second_share)
        self.assertEqual(app.download_jobs[1].files, [first_file])
        self.assertEqual(app.download_jobs[2].files, [second_file, third_file])
        self.assertEqual(app.downloads_tree.insert.call_args_list[1].kwargs["text"], "Second Magnet")
        self.assertEqual(
            app.downloads_tree.insert.call_args_list[1].kwargs["values"],
            (2, "5 B", "Preparing download"),
        )

    def test_finishing_older_download_does_not_clear_current_share(self) -> None:
        class CompletionHarness:
            def __init__(self) -> None:
                self.events = queue.Queue()
                self.events.put(("finished", (1, 1, 1, [])))
                self.download_jobs = {
                    1: DownloadJob(
                        [FileEntry("older.bin", "")],
                        Path("downloads"),
                        None,
                        "",
                        "Older share",
                        "1 B",
                    ),
                    2: DownloadJob(
                        [FileEntry("current.bin", "")],
                        Path("downloads"),
                        None,
                        "",
                        "Current share",
                        "1 B",
                    ),
                }
                self.current_download_id = 2
                self.download_running = True
                self.load_in_progress = False
                self.share_control_var = Mock()
                self.status_var = Mock()
                self._update_download_controls = Mock()
                self.after = Mock()

            def _process_events(self) -> None:
                DownloadsMixin._process_events(self)

            def _refresh_download_summary(self) -> None:
                DownloadsMixin._refresh_download_summary(self)

        app = CompletionHarness()
        with patch("src.ui.downloads.messagebox.showinfo"):
            app._process_events()

        self.assertTrue(app.download_running)
        self.assertEqual(app.current_download_id, 2)
        self.assertEqual(set(app.download_jobs), {2})

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
