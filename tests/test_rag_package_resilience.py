import tempfile
import time
import unittest
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from app.services.rag_package_builder import (
    RagPackageCancelled,
    _checkpoint_path,
    _append_checkpoint_result,
    _extract_file_with_hard_timeout,
    _load_checkpoint,
    _save_checkpoint,
    build_and_save_rag_package,
)
from app.services.analysis_result import AnalysisResult, AnalyzedFile


def _slow_extract(_path: str) -> str:
    time.sleep(2)
    return "too late"


def _paced_extract(path: str) -> str:
    time.sleep(0.8)
    return path


class RagPackageResilienceTests(unittest.TestCase):
    def test_hard_timeout_abandons_slow_process(self):
        started = time.perf_counter()
        with self.assertRaises(TimeoutError):
            _extract_file_with_hard_timeout(
                Path("damaged.slow"), 0.2, extractor=_slow_extract
            )
        self.assertLess(time.perf_counter() - started, 1.5)

    def test_checkpoint_round_trip_resumes_completed_results(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "checkpoint.json"
            expected = {
                "signature": "same-input",
                "results": {"file-1": [[{"chunk_text": "saved"}], []]},
                "timed_out_files": ["damaged.hwp"],
            }
            _save_checkpoint(path, expected)

            self.assertEqual(_load_checkpoint(path, "same-input"), expected)
            self.assertEqual(_load_checkpoint(path, "changed-input")["results"], {})

    def test_checkpoint_appends_one_jsonl_record_per_completed_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "checkpoint.jsonl"
            _save_checkpoint(path, {
                "signature": "same-input", "results": {}, "timed_out_files": []
            })
            initial_size = path.stat().st_size
            _append_checkpoint_result(
                path,
                "file-1",
                [{"chunk_text": "saved"}],
                [],
            )
            first_size = path.stat().st_size
            _append_checkpoint_result(
                path,
                "file-2",
                [{"chunk_text": "saved again"}],
                [],
            )

            loaded = _load_checkpoint(path, "same-input")
            lines = path.read_text(encoding="utf-8").splitlines()

        self.assertEqual(len(lines), 3)
        self.assertEqual(set(loaded["results"]), {"file-1", "file-2"})
        self.assertGreater(first_size, initial_size)
        self.assertLess(path_increment := len(lines[-1].encode("utf-8")), first_size)
        self.assertGreater(path_increment, 0)

    def test_process_extractions_run_concurrently(self):
        paths = [Path(f"file-{index}.txt") for index in range(4)]
        started = time.perf_counter()
        for path in paths:
            _extract_file_with_hard_timeout(path, 5, extractor=_paced_extract)
        sequential_seconds = time.perf_counter() - started

        started = time.perf_counter()
        with ThreadPoolExecutor(max_workers=4) as executor:
            results = list(executor.map(
                lambda path: _extract_file_with_hard_timeout(
                    path, 5, extractor=_paced_extract
                ),
                paths,
            ))
        parallel_seconds = time.perf_counter() - started

        self.assertEqual(results, [str(path) for path in paths])
        print(
            f"extraction benchmark: sequential={sequential_seconds:.3f}s "
            f"parallel={parallel_seconds:.3f}s"
        )
        self.assertLess(parallel_seconds, sequential_seconds * 0.75)

    def test_cancel_then_restart_reuses_completed_file_checkpoint(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for name in ("one.txt", "two.txt"):
                (root / name).write_text(name, encoding="utf-8")
            files = [
                AnalyzedFile(
                    file_name=name,
                    relative_path=f"{root.name}/{name}",
                    modified_at="2026-07-14T00:00:00",
                    modified_timestamp=1.0,
                    size_bytes=(root / name).stat().st_size,
                )
                for name in ("one.txt", "two.txt")
            ]
            analysis = AnalysisResult(
                root_folder_path=str(root), total_folder_count=0,
                total_file_count=2, total_size_bytes=10,
                modified_within_7_days_count=0, modified_within_30_days_count=0,
                modified_within_90_days_count=0, error_count=0,
                child_folder_summaries=[], all_files=files,
            )
            first_calls = []
            canceled = {"value": False}

            def build_record(file, _path, _include_content=True):
                first_calls.append(file.file_name)
                return ([{"chunk_text": file.file_name, "source_file": file.relative_path,
                          "chunk_index": 0, "metadata": {}}], [], False)

            def progress(stage, current, _total):
                if stage == "files" and current == 1:
                    canceled["value"] = True

            with patch("app.services.rag_package_builder.FILE_EXTRACTION_MAX_WORKERS", 1), patch(
                "app.services.rag_package_builder._build_file_chunk_records_with_timeout",
                side_effect=build_record,
            ):
                with self.assertRaises(RagPackageCancelled):
                    build_and_save_rag_package(
                        analysis, [str(root)], "unused", str(root / "package"),
                        progress_callback=progress,
                        cancel_check=lambda: canceled["value"],
                        selected_extensions=set(),
                    )

            checkpoint = _checkpoint_path([str(root)])
            self.assertTrue(checkpoint.exists())
            saved_count = len(_load_checkpoint(checkpoint, json.loads(
                checkpoint.read_text(encoding="utf-8").splitlines()[0]
            )["signature"])["results"])
            resumed_calls = []

            def resumed_record(file, _path, _include_content=True):
                resumed_calls.append(file.file_name)
                return ([{"chunk_text": file.file_name, "source_file": file.relative_path,
                          "chunk_index": 0, "metadata": {}}], [], False)

            canceled["value"] = False
            with patch("app.services.rag_package_builder.FILE_EXTRACTION_MAX_WORKERS", 1), patch(
                "app.services.rag_package_builder._build_file_chunk_records_with_timeout",
                side_effect=resumed_record,
            ), patch(
                "app.services.rag_package_builder._embed_chunk_records",
                return_value=([], [], 0),
            ), patch(
                "app.services.rag_package_builder.save_rag_package",
                return_value=str(root / "package.zip"),
            ):
                build_and_save_rag_package(
                    analysis, [str(root)], "unused", str(root / "package"),
                    selected_extensions=set(),
                )

            self.assertEqual(saved_count, 1)
            self.assertEqual(len(resumed_calls), 1)
            self.assertFalse(checkpoint.exists())

    def test_progress_dialog_fetches_optional_server_banners(self):
        source = (Path(__file__).parents[1] / "app/ui/main_window.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("PackageBannerWorker", source)
        self.assertIn("_start_package_banner_worker", source)
        self.assertIn("_apply_package_banners", source)
        self.assertIn("layout.addSpacing(18)", source)
        self.assertIn("font-size:12px", source)
        self.assertIn("color:#2563EB", source)
        self.assertIn("banner_label.setOpenExternalLinks(False)", source)
        self.assertIn("Qt.CursorShape.PointingHandCursor", source)


if __name__ == "__main__":
    unittest.main()
