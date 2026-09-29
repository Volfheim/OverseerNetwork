import subprocess
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from app import fleet
from app import system_probe


class GpuProbeTests(unittest.TestCase):
    def run_probe(self, output, returncode=0, platform="win32"):
        with patch.object(system_probe.shutil, "which", return_value="nvidia-smi"), \
             patch.object(subprocess, "CREATE_NO_WINDOW", 0x08000000, create=True), \
             patch.object(system_probe.sys, "platform", platform), \
             patch.object(system_probe.subprocess, "run", return_value=Mock(
                 returncode=returncode, stdout=output)) as runner:
            result = system_probe.collect_gpu_metrics()
        return result, runner.call_args.kwargs

    def test_windows_gpu_query_is_windowless_bounded_and_preserves_metrics(self):
        result, options = self.run_probe('"NVIDIA, Fixture", 21.5, 1024, 8192\nOther GPU, 0, 0, 4096\n')
        self.assertEqual(result, {"name": "NVIDIA, Fixture", "load_percent": 21.5,
                                 "vram_used_mb": 1024, "vram_total_mb": 8192})
        self.assertTrue(options["creationflags"] & 0x08000000)
        self.assertEqual(options["stdin"], subprocess.DEVNULL)
        self.assertTrue(options["capture_output"])
        self.assertLessEqual(options["timeout"], 3)

    def test_unix_does_not_receive_windows_creation_flags(self):
        result, options = self.run_probe("GPU, 0, 0, 4096", platform="linux")
        self.assertIsNotNone(result)
        self.assertEqual(options["creationflags"], 0)

    @unittest.skipUnless(sys.platform == "win32", "Windows console regression")
    def test_real_probe_child_has_no_console(self):
        run = subprocess.run
        fixture = (
            "import ctypes; "
            "name = 'windowless' if not ctypes.windll.kernel32.GetConsoleWindow() else 'console'; "
            "print(name + ', 0, 0, 8192')"
        )

        def launch_fixture(command, **options):
            return run([sys.executable, "-c", fixture], **options)

        with patch.object(system_probe.shutil, "which", return_value="nvidia-smi"), \
             patch.object(system_probe.subprocess, "run", side_effect=launch_fixture):
            self.assertEqual(system_probe.collect_gpu_metrics()["name"], "windowless")

    def test_unavailable_or_invalid_metrics_do_not_break_cpu_monitoring(self):
        for output in ("", "GPU, N/A, 0, 8192", "GPU, nan, 0, 8192",
                       "GPU, 5, inf, 8192", "GPU, 200, 0, 8192", "truncated",
                       "GPU, 0, 9000, 8192"):
            with self.subTest(output=output):
                self.assertIsNone(self.run_probe(output)[0])
        self.assertIsNone(self.run_probe("GPU, 0, 0, 8192", returncode=1)[0])

    def test_missing_driver_or_timeout_is_optional(self):
        for error in (FileNotFoundError(), subprocess.TimeoutExpired("nvidia-smi", 3)):
            with self.subTest(error=type(error).__name__), \
                 patch.object(system_probe.shutil, "which", return_value="nvidia-smi"), \
                 patch.object(system_probe.subprocess, "run", side_effect=error):
                self.assertIsNone(system_probe.collect_local_metrics()["gpu"])
        with patch.object(system_probe.shutil, "which", return_value=None), \
             patch.object(system_probe.sys, "platform", "linux"), \
             patch.object(system_probe.subprocess, "run") as runner:
            self.assertIsNone(system_probe.collect_gpu_metrics())
            runner.assert_not_called()


class LocalDiagnosticTests(unittest.IsolatedAsyncioTestCase):
    async def test_diagnostic_worker_runs_without_a_windows_console(self):
        process = Mock(returncode=0)
        process.communicate = AsyncMock(return_value=(b'{"checks":[]}', b''))
        with patch.object(fleet.sys, "platform", "win32"), \
             patch.object(subprocess, "CREATE_NO_WINDOW", 0x08000000, create=True), \
             patch.object(fleet.asyncio, "create_subprocess_exec", new_callable=AsyncMock,
                          return_value=process) as spawn:
            result = await fleet.probe(SimpleNamespace(type="local"), {"mode": "diagnose"})
        self.assertEqual(result, {"checks": []})
        self.assertTrue(spawn.call_args.kwargs["creationflags"] & 0x08000000)
        process.kill.assert_not_called()
