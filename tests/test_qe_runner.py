import io
import queue
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

import qe_runner as runner


class FakeProcess:
    def __init__(self, lines: list[str], returncode: int = 0) -> None:
        self.stdout = io.StringIO("".join(lines))
        self._returncode = returncode

    def wait(self) -> int:
        return self._returncode


class QERunnerTests(unittest.TestCase):
    def tearDown(self) -> None:
        runner.RUN_REGISTRY.clear()

    def test_start_qe_run_streams_lines_to_file_and_queue(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            run_dir = Path(tmp_dir)
            with mock.patch(
                "qe_runner.subprocess.Popen",
                return_value=FakeProcess(["first line\n", "second line\n"], returncode=0),
            ) as mocked_popen:
                run_id, output_path = runner.start_qe_run(
                    run_dir=run_dir,
                    command=["pw.x", "-in", "pw.in"],
                )

                context = runner.get_run_context(run_id)
                self.assertIsNotNone(context)
                assert context is not None
                context.thread.join(timeout=5)
                self.assertFalse(context.thread.is_alive())

            mocked_popen.assert_called_once_with(
                ["pw.x", "-in", "pw.in"],
                cwd=str(run_dir),
                stdout=runner.subprocess.PIPE,
                stderr=runner.subprocess.STDOUT,
                stdin=runner.subprocess.DEVNULL,
                text=True,
                bufsize=1,
            )
            self.assertEqual(output_path.read_text(encoding="utf-8"), "first line\nsecond line\n")

            events = runner.drain_run_events(run_id)
            self.assertEqual(
                events,
                [
                    runner.RunEvent(type=runner.EVENT_TYPE_LINE, line="first line\n"),
                    runner.RunEvent(type=runner.EVENT_TYPE_LINE, line="second line\n"),
                    runner.RunEvent(type=runner.EVENT_TYPE_COMPLETE, returncode=0),
                ],
            )

    def test_apply_run_events_updates_tail_and_returncode(self) -> None:
        state: dict[str, object] = {}
        runner.initialize_run_state(state)

        completed = runner.apply_run_events(
            state,
            [
                runner.RunEvent(type=runner.EVENT_TYPE_LINE, line="line 1\n"),
                runner.RunEvent(type=runner.EVENT_TYPE_LINE, line="line 2\n"),
                runner.RunEvent(type=runner.EVENT_TYPE_COMPLETE, returncode=7),
            ],
        )

        self.assertTrue(completed)
        self.assertFalse(state[runner.RUN_ACTIVE_STATE_KEY])
        self.assertTrue(state[runner.RUN_FINISHED_STATE_KEY])
        self.assertEqual(state[runner.RUN_RETURNCODE_STATE_KEY], 7)
        self.assertEqual(
            state[runner.RUN_TAIL_LINES_STATE_KEY],
            ["line 1\n", "line 2\n"],
        )

    def test_apply_run_events_keeps_only_latest_tail_lines(self) -> None:
        state: dict[str, object] = {}
        runner.initialize_run_state(state)

        runner.apply_run_events(
            state,
            [
                runner.RunEvent(type=runner.EVENT_TYPE_LINE, line="line 1\n"),
                runner.RunEvent(type=runner.EVENT_TYPE_LINE, line="line 2\n"),
                runner.RunEvent(type=runner.EVENT_TYPE_LINE, line="line 3\n"),
                runner.RunEvent(type=runner.EVENT_TYPE_LINE, line="line 4\n"),
            ],
            max_tail_lines=2,
        )

        self.assertEqual(
            state[runner.RUN_TAIL_LINES_STATE_KEY],
            ["line 3\n", "line 4\n"],
        )

    def test_reset_run_state_clears_finished_registry_entry(self) -> None:
        finished_thread = threading.Thread(target=lambda: None)
        finished_thread.start()
        finished_thread.join(timeout=5)

        run_id = "finished-run"
        runner.RUN_REGISTRY[run_id] = runner.RunContext(
            process=None,
            thread=finished_thread,
            queue=queue.Queue(),
            started_at=datetime.now(timezone.utc),
        )

        state: dict[str, object] = {
            runner.RUN_ACTIVE_STATE_KEY: False,
            runner.RUN_FINISHED_STATE_KEY: True,
            runner.RUN_RETURNCODE_STATE_KEY: 0,
            runner.RUN_TAIL_LINES_STATE_KEY: ["done\n"],
            runner.RUN_OUTPUT_PATH_STATE_KEY: "qe_runs/pw.out",
            runner.ACTIVE_RUN_ID_STATE_KEY: run_id,
        }

        runner.reset_run_state(state)

        self.assertFalse(state[runner.RUN_ACTIVE_STATE_KEY])
        self.assertFalse(state[runner.RUN_FINISHED_STATE_KEY])
        self.assertIsNone(state[runner.RUN_RETURNCODE_STATE_KEY])
        self.assertEqual(state[runner.RUN_TAIL_LINES_STATE_KEY], [])
        self.assertIsNone(state[runner.RUN_OUTPUT_PATH_STATE_KEY])
        self.assertIsNone(state[runner.ACTIVE_RUN_ID_STATE_KEY])
        self.assertNotIn(run_id, runner.RUN_REGISTRY)

    def test_mark_runtime_lost_marks_run_failed(self) -> None:
        state: dict[str, object] = {}
        runner.initialize_run_state(state)
        runner.set_run_started(
            state,
            run_id="lost-run",
            output_path=Path("qe_runs/pw.out"),
        )

        runner.mark_runtime_lost(state)

        self.assertFalse(state[runner.RUN_ACTIVE_STATE_KEY])
        self.assertTrue(state[runner.RUN_FINISHED_STATE_KEY])
        self.assertEqual(state[runner.RUN_RETURNCODE_STATE_KEY], runner.DEFAULT_FAILURE_RETURNCODE)
        self.assertIn(
            "runtime state was lost",
            "".join(state[runner.RUN_TAIL_LINES_STATE_KEY]).lower(),
        )


if __name__ == "__main__":
    unittest.main()
