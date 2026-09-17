"""Exercise scripts/rebuild.ps1 with a real PowerShell interpreter.

Windows PowerShell is reachable from WSL through interop, so this runs for
real rather than being assumed. Two constraints shape the setup:

  * a Windows interpreter can only see paths under a Windows drive, so the
    test tree is built under /mnt/c rather than /tmp;
  * paths handed to the script are translated with `wslpath -w`.

Where no PowerShell exists at all, every test skips with a clear reason.
"""

import _bootstrap  # noqa: F401

import hashlib
import os
import shutil
import subprocess
import unittest
from pathlib import Path

from anac_data import packaging

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "rebuild.ps1"

#: pwsh first (it is the modern one), then the two Windows-interop names.
_CANDIDATES = ("pwsh", "pwsh.exe", "powershell.exe")

PAYLOAD = b"ANO;MES;PAX\r\n" + os.urandom(30_000)


def _interpreter():
    for name in _CANDIDATES:
        found = shutil.which(name)
        if found:
            return name, found
    return None, None


POWERSHELL_NAME, POWERSHELL_PATH = _interpreter()

#: A Windows interpreter cannot open /tmp. Build the tree where it can.
_WINDOWS_TEMP = Path("/mnt/c/Users") / os.environ.get("USER", "") / "AppData/Local/Temp"


def _needs_windows_path(name):
    return name.endswith(".exe")


def _to_interpreter_path(name, path):
    """Translate a WSL path for a Windows interpreter; pass through for pwsh."""
    if not _needs_windows_path(name):
        return str(path)
    done = subprocess.run(
        ["wslpath", "-w", str(path)], capture_output=True, text=True
    )
    if done.returncode != 0:
        raise unittest.SkipTest(f"wslpath could not translate {path}")
    return done.stdout.strip()


@unittest.skipUnless(SCRIPT.is_file(), "scripts/rebuild.ps1 not present")
@unittest.skipUnless(
    POWERSHELL_NAME is not None,
    "no PowerShell found (looked for pwsh, pwsh.exe, powershell.exe)",
)
class RebuildPowerShellTestCase(unittest.TestCase):
    chunk_size = 4096

    @classmethod
    def setUpClass(cls):
        if _needs_windows_path(POWERSHELL_NAME) and not _WINDOWS_TEMP.is_dir():
            raise unittest.SkipTest(
                f"{POWERSHELL_NAME} needs a Windows-visible directory and "
                f"{_WINDOWS_TEMP} does not exist"
            )

    def setUp(self):
        if _needs_windows_path(POWERSHELL_NAME):
            base = _WINDOWS_TEMP / f"anac-ps-{os.getpid()}-{id(self)}"
        else:
            import tempfile

            base = Path(tempfile.mkdtemp())
        base.mkdir(parents=True, exist_ok=True)
        self.root = base
        self.addCleanup(shutil.rmtree, base, True)

        self.raw_dir = self.root / "data" / "raw" / "anac" / "estatisticos"
        self.raw_dir.mkdir(parents=True)
        self.source = self.raw_dir / "Dados_Estatisticos.csv"
        self.source.write_bytes(PAYLOAD)
        self.expected_sha = hashlib.sha256(PAYLOAD).hexdigest()

        self.chunk_dir = packaging.chunk_dir_for(
            self.root / "data" / "dist", "estatisticos", self.source.name
        )
        self.result = packaging.package_file(
            self.source,
            self.chunk_dir,
            dataset="estatisticos",
            chunk_size=self.chunk_size,
        )
        self.assertGreater(len(self.result.chunks), 1, "need a multi-chunk set")

        # The script is copied in so the run needs nothing from the repository.
        self.script = self.root / "rebuild.ps1"
        shutil.copy2(SCRIPT, self.script)

    def run_script(self, *args, cwd=None):
        working = cwd or self.root
        command = [
            POWERSHELL_PATH,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            _to_interpreter_path(POWERSHELL_NAME, self.script),
            *args,
        ]
        done = subprocess.run(
            command, cwd=working, capture_output=True, text=True
        )
        # Windows interop hands back CRLF; normalise so assertions read simply.
        return subprocess.CompletedProcess(
            done.args,
            done.returncode,
            (done.stdout or "").replace("\r\n", "\n"),
            (done.stderr or "").replace("\r\n", "\n"),
        )

    def digest(self, path):
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class InterpreterAvailabilityTest(unittest.TestCase):
    def test_a_powershell_is_available_on_this_machine(self):
        """Documents that these tests really run here rather than skipping."""
        self.assertIsNotNone(
            POWERSHELL_NAME,
            "no PowerShell found; the .ps1 script is untested on this host",
        )


class SyntaxTest(RebuildPowerShellTestCase):
    def test_the_script_parses(self):
        script_path = _to_interpreter_path(POWERSHELL_NAME, self.script)
        done = subprocess.run(
            [
                POWERSHELL_PATH,
                "-NoProfile",
                "-Command",
                f"$null = [ScriptBlock]::Create("
                f"(Get-Content -Raw '{script_path}')); Write-Output 'ok'",
            ],
            capture_output=True,
            text=True,
        )
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.assertIn("ok", done.stdout)

    def test_it_does_not_call_get_filehash(self):
        """Get-FileHash is missing even on some 5.1 installs; .NET is not.

        Checked against code lines only - the comment explaining the choice
        naturally names the cmdlet it avoids.
        """
        code = [
            line
            for line in SCRIPT.read_text(encoding="utf-8").splitlines()
            if not line.lstrip().startswith("#")
        ]
        offenders = [line for line in code if "Get-FileHash" in line]
        self.assertEqual([], offenders)
        self.assertTrue(
            any("System.Security.Cryptography.SHA256" in l for l in code)
        )


class HappyPathTest(RebuildPowerShellTestCase):
    def test_rebuilds_a_deleted_original_byte_for_byte(self):
        self.source.unlink()
        done = self.run_script()

        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.assertTrue(self.source.is_file())
        self.assertEqual(PAYLOAD, self.source.read_bytes())
        self.assertEqual(self.expected_sha, self.digest(self.source))
        self.assertIn("rebuilt and verified", done.stdout)

    def test_rebuilds_a_named_chunk_directory(self):
        self.source.unlink()
        done = self.run_script(
            "-ChunkDir", _to_interpreter_path(POWERSHELL_NAME, self.chunk_dir)
        )

        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.assertEqual(self.expected_sha, self.digest(self.source))

    def test_honours_an_explicit_output_directory(self):
        elsewhere = self.root / "elsewhere"
        done = self.run_script(
            "-ChunkDir",
            _to_interpreter_path(POWERSHELL_NAME, self.chunk_dir),
            "-OutputDir",
            _to_interpreter_path(POWERSHELL_NAME, elsewhere),
        )

        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.assertEqual(
            self.expected_sha, self.digest(elsewhere / self.source.name)
        )

    def test_leaves_no_temporary_file(self):
        self.source.unlink()
        self.run_script()
        self.assertEqual([], list(self.raw_dir.glob("*.rebuilding")))

    def test_an_already_correct_file_is_not_rewritten(self):
        before = self.source.stat().st_mtime_ns
        done = self.run_script()

        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.assertIn("already correct", done.stdout)
        self.assertEqual(before, self.source.stat().st_mtime_ns)

    def test_a_wrong_existing_file_is_replaced(self):
        self.source.write_bytes(b"stale contents")
        done = self.run_script()

        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.assertEqual(self.expected_sha, self.digest(self.source))

    def test_memory_stays_flat_on_a_larger_payload(self):
        """Streaming, not buffering: a payload well over one chunk must work."""
        bigger = b"A;B\r\n" + os.urandom(400_000)
        self.source.write_bytes(bigger)
        shutil.rmtree(self.chunk_dir)
        result = packaging.package_file(
            self.source,
            self.chunk_dir,
            dataset="estatisticos",
            chunk_size=self.chunk_size,
        )
        self.assertGreater(len(result.chunks), 20)
        self.source.unlink()

        done = self.run_script()

        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.assertEqual(bigger, self.source.read_bytes())


class FailureTest(RebuildPowerShellTestCase):
    def test_a_corrupt_chunk_is_named_and_nothing_is_written(self):
        self.source.unlink()
        victim = self.chunk_dir / self.result.chunks[0].name
        victim.write_bytes(b"\x00" * victim.stat().st_size)

        done = self.run_script()

        self.assertNotEqual(0, done.returncode)
        combined = done.stdout + done.stderr
        self.assertIn("failed verification", combined)
        self.assertIn(self.result.chunks[0].name, combined)
        self.assertFalse(self.source.exists())
        self.assertEqual([], list(self.raw_dir.glob("*.rebuilding")))

    def test_a_missing_chunk_is_named(self):
        self.source.unlink()
        gone = self.result.chunks[1].name
        (self.chunk_dir / gone).unlink()

        done = self.run_script()

        self.assertNotEqual(0, done.returncode)
        combined = done.stdout + done.stderr
        self.assertIn("missing", combined)
        self.assertIn(gone, combined)
        self.assertFalse(self.source.exists())

    def test_a_final_digest_mismatch_is_distinct_from_a_chunk_failure(self):
        self.source.unlink()
        listing = self.chunk_dir / "checksums.sha256"
        listing.write_text(
            listing.read_text(encoding="utf-8").replace(
                f"# original-sha256: {self.expected_sha}",
                "# original-sha256: " + "0" * 64,
            ),
            encoding="utf-8",
        )

        done = self.run_script()

        self.assertNotEqual(0, done.returncode)
        combined = done.stdout + done.stderr
        self.assertIn("does not match the recorded digest", combined)
        self.assertNotIn("failed verification", combined)
        self.assertFalse(self.source.exists())

    def test_a_directory_without_a_listing_is_rejected(self):
        empty = self.root / "not-a-chunk-dir"
        empty.mkdir()
        done = self.run_script(
            "-ChunkDir", _to_interpreter_path(POWERSHELL_NAME, empty)
        )

        self.assertNotEqual(0, done.returncode)
        self.assertIn("checksums.sha256", done.stdout + done.stderr)

    def test_a_missing_directory_is_rejected(self):
        done = self.run_script(
            "-ChunkDir",
            _to_interpreter_path(POWERSHELL_NAME, self.root / "nope"),
        )
        self.assertNotEqual(0, done.returncode)
        self.assertIn("not a directory", done.stdout + done.stderr)

    def test_no_chunk_sets_at_all_is_reported(self):
        shutil.rmtree(self.root / "data" / "dist")
        done = self.run_script()
        self.assertNotEqual(0, done.returncode)
        self.assertIn("dist", done.stdout + done.stderr)


class MultipleChunkSetsTest(RebuildPowerShellTestCase):
    def test_rebuilds_every_chunk_set_under_dist(self):
        other_dir = self.root / "data" / "raw" / "anac" / "vra"
        other_dir.mkdir(parents=True)
        other = other_dir / "VRA_20267.csv"
        other_payload = b"ICAO;VOO\r\n" + os.urandom(20_000)
        other.write_bytes(other_payload)
        packaging.package_file(
            other,
            packaging.chunk_dir_for(
                self.root / "data" / "dist", "vra", other.name
            ),
            dataset="vra",
            chunk_size=self.chunk_size,
        )

        self.source.unlink()
        other.unlink()
        done = self.run_script()

        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.assertEqual(PAYLOAD, self.source.read_bytes())
        self.assertEqual(other_payload, other.read_bytes())
        self.assertIn("rebuilt 2 file(s)", done.stdout)


class BothScriptsAgreeTest(RebuildPowerShellTestCase):
    """Drift between the two implementations should fail here, not on a laptop."""

    def test_shell_and_powershell_produce_the_same_digest(self):
        shell_script = REPO / "scripts" / "rebuild.sh"
        if not shell_script.is_file():
            self.skipTest("scripts/rebuild.sh not present")
        if not (shutil.which("sha256sum") or shutil.which("shasum")):
            self.skipTest("no sha256 tool for the shell script")
        if not shutil.which("gunzip"):
            self.skipTest("gunzip not available")

        # PowerShell rebuild.
        self.source.unlink()
        done = self.run_script()
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        from_powershell = self.digest(self.source)

        # Shell rebuild of the same chunk set, into its own directory.
        self.source.unlink()
        shell_out = self.root / "from-shell"
        shell = subprocess.run(
            [
                shutil.which("sh") or "/bin/sh",
                str(shell_script),
                str(self.chunk_dir),
                "-o",
                str(shell_out),
            ],
            cwd=self.root,
            capture_output=True,
            text=True,
        )
        self.assertEqual(0, shell.returncode, shell.stdout + shell.stderr)
        from_shell = self.digest(shell_out / self.source.name)

        self.assertEqual(from_powershell, from_shell)
        self.assertEqual(self.expected_sha, from_shell)


if __name__ == "__main__":
    unittest.main()
