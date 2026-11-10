import unittest
import threading
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

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
from src.ui.downloads import load_progress_timed_out


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


class UiHandlerTests(unittest.TestCase):
    def test_loading_share_name_comes_from_saved_name_magnet_or_peer(self) -> None:
        self.assertEqual(DropLinkApp._loading_share_name("magnet:?xt=urn:test&dn=Film%20Name", None), "Film Name")
        self.assertEqual(DropLinkApp._loading_share_name("https://peer.example/team.json", None), "team")
        self.assertEqual(DropLinkApp._loading_share_name("magnet:?xt=urn:test", {"name": "Saved share"}), "Saved share")

    def test_load_times_out_after_one_minute_without_progress(self) -> None:
        self.assertFalse(load_progress_timed_out(100.0, 159.9))
        self.assertTrue(load_progress_timed_out(100.0, 160.0))

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


if __name__ == "__main__":
    unittest.main()
