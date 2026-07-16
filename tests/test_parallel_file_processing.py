import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from app.services.analysis_result import AnalysisResult, AnalyzedFile
from app.services.email_file_handler import process_email_files
from app.services.kakao_file_handler import process_kakao_files
from app.services.parallel_file_runner import run_process_items_with_timeout
from app.services.rag_package_builder import estimate_rag_package_cost


def _sleep_worker(delay: float) -> float:
    time.sleep(delay)
    return delay


def _timeline_worker(delay: float) -> tuple[int, float, float]:
    started = time.perf_counter()
    time.sleep(delay)
    return os.getpid(), started, time.perf_counter()


def _analysis(root: Path, names: list[str]) -> AnalysisResult:
    files = [
        AnalyzedFile(
            file_name=name,
            relative_path=f"{root.name}/{name}",
            modified_at="2026-07-14 00:00:00",
            modified_timestamp=float(index + 1),
            size_bytes=(root / name).stat().st_size,
        )
        for index, name in enumerate(names)
    ]
    return AnalysisResult(
        root_folder_path=str(root),
        total_folder_count=0,
        total_file_count=len(files),
        total_size_bytes=sum(file.size_bytes for file in files),
        modified_within_7_days_count=0,
        modified_within_30_days_count=0,
        modified_within_90_days_count=0,
        error_count=0,
        child_folder_summaries=[],
        all_files=files,
    )


class ParallelFileProcessingTests(unittest.TestCase):
    def test_process_runner_reuses_workers_and_times_out(self):
        started = time.perf_counter()
        outcomes = run_process_items_with_timeout(
            [0.2, 0.2, 0.2, 0.2],
            _timeline_worker,
            timeout_seconds=2,
            max_workers=2,
        )
        elapsed = time.perf_counter() - started
        self.assertEqual([status for status, _value in outcomes], ["ok"] * 4)
        timelines = [value for _status, value in outcomes if value is not None]
        self.assertEqual(len({timeline[0] for timeline in timelines}), 2)
        self.assertTrue(any(
            left[0] != right[0]
            and left[1] < right[2]
            and right[1] < left[2]
            for left_index, left in enumerate(timelines)
            for right in timelines[left_index + 1:]
        ))
        # Process startup varies substantially across supported Windows PCs;
        # overlap and PID reuse above validate parallelism without a brittle
        # sub-second threshold.
        self.assertLess(elapsed, 1.2)

        started = time.perf_counter()
        outcomes = run_process_items_with_timeout(
            [0.5],
            _sleep_worker,
            timeout_seconds=0.1,
            max_workers=1,
        )
        self.assertEqual(outcomes[0][0], "timeout")
        self.assertLess(time.perf_counter() - started, 1.2)

    def test_cost_estimation_processes_files_in_parallel_batches(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            names = [f"file-{index}.txt" for index in range(6)]
            for index, name in enumerate(names):
                (root / name).write_text(f"sample-{index}", encoding="utf-8")
            analysis = _analysis(root, names)

            def paced_estimate(*_args):
                time.sleep(0.2)
                return 100

            started = time.perf_counter()
            with patch(
                "app.services.rag_package_builder.FILE_EXTRACTION_MAX_WORKERS", 3
            ), patch(
                "app.services.rag_package_builder._estimate_file_chars",
                side_effect=paced_estimate,
            ):
                result = estimate_rag_package_cost(
                    analysis,
                    [str(root)],
                    selected_extensions={".txt"},
                    embedding_unit_cost_per_1k=0.05,
                )
            elapsed = time.perf_counter() - started

        self.assertEqual(result["file_count"], 6)
        self.assertLess(elapsed, 0.8)

    def test_estimated_credit_tracks_server_embedding_unit_cost(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            path = root / "sample.txt"
            path.write_text("sample", encoding="utf-8")
            analysis = _analysis(root, [path.name])
            with patch(
                "app.services.rag_package_builder._estimate_file_chars",
                return_value=100_000,
            ):
                at_five = estimate_rag_package_cost(
                    analysis,
                    [str(root)],
                    selected_extensions={".txt"},
                    embedding_unit_cost_per_1k=0.05,
                )
                at_eight = estimate_rag_package_cost(
                    analysis,
                    [str(root)],
                    selected_extensions={".txt"},
                    embedding_unit_cost_per_1k=0.08,
                )
        self.assertEqual(at_five["estimated_cost_krw"], 5)
        self.assertEqual(at_eight["estimated_cost_krw"], 8)

    def test_email_and_kakao_files_are_processed_in_parallel_workers(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            email_paths = []
            kakao_paths = []
            for index in range(4):
                email_path = root / f"mail-{index}.eml"
                email_path.write_text(
                    "From: sender@example.com\n"
                    "To: receiver@example.com\n"
                    f"Subject: sample {index}\n\nbody",
                    encoding="utf-8",
                )
                email_paths.append(str(email_path))

                kakao_path = root / f"kakao-{index}.txt"
                kakao_path.write_text(
                    "--------------- 2026년 7월 14일 화요일 ---------------\n"
                    f"[사용자] [오전 9:0{index}] 메시지 {index}\n",
                    encoding="utf-8",
                )
                kakao_paths.append(str(kakao_path))

            emails, email_failures = process_email_files(
                email_paths, timeout_seconds=5, max_workers=3
            )
            messages, kakao_failures = process_kakao_files(
                kakao_paths, timeout_seconds=5, max_workers=3
            )

        self.assertEqual(len(emails), 4)
        self.assertEqual(email_failures, 0)
        self.assertEqual(len(messages), 4)
        self.assertEqual(kakao_failures, 0)


if __name__ == "__main__":
    unittest.main()
