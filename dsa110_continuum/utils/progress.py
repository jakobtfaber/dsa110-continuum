# Vendored from dsa110-contimg @ /data/dsa110-contimg/backend/src (H17), 2026-07-03,
# as part of the contimg-import-retirement migration (docs/rse/specs/plan-contimg-import-retirement.md).
"""
Progress indicators for CLI operations using tqdm.

This module provides progress bar utilities that integrate with the CLI helpers
and respect the --disable-progress and --quiet flags.

Following expert recommendations: Use tqdm library (industry standard) instead
of custom solutions.

Also provides stage-level progress monitoring for long-running operations
(CASA calibration, WSClean imaging) that don't have built-in progress callbacks.
"""

import logging
import sys
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

try:
    from tqdm import tqdm

    TQDM_AVAILABLE = True
except ImportError:
    TQDM_AVAILABLE = False

    # Fallback: create a no-op tqdm-like object
    class tqdm:  # type: ignore
        def __init__(self, *args, **kwargs):
            self.total = kwargs.get("total", 0)
            self.n = 0

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def update(self, n=1):
            self.n += n

        def __iter__(self):
            return iter([])


def get_progress_bar(
    iterable: Any | None = None,
    total: int | None = None,
    desc: str = "Processing",
    disable: bool = False,
    mininterval: float = 0.1,
) -> Iterator:
    """Get a progress bar using tqdm, with automatic disable if stdout is not a TTY.

    Parameters
    ----------
    iterable : Optional[Any]
        Iterable to wrap (optional)
        (Default value = None)
    total : Optional[int]
        Total number of items (if iterable doesn't have __len__)
        (Default value = None)
    desc : str
        Description for the progress bar
        (Default value = "Processing")
    disable : bool
        Force disable progress bar
        (Default value = False)
    mininterval : float
        Minimum time (seconds) between updates
        (Default value = 0.1)

    """
    if not TQDM_AVAILABLE:
        # Fallback: return iterable as-is
        if iterable is not None:
            return iter(iterable)
        return iter(range(total or 0))

    # Auto-disable if not TTY (useful for scripts/automation)
    if not sys.stdout.isatty():
        disable = True

    return tqdm(
        iterable=iterable,
        total=total,
        desc=desc,
        disable=disable,
        mininterval=mininterval,
        file=sys.stderr,  # Use stderr so it doesn't interfere with stdout
    )


def progress_context(
    total: int | None = None,
    desc: str = "Processing",
    disable: bool = False,
    mininterval: float = 0.1,
):
    """Context manager for progress bars.

    Parameters
    ----------
    total : Optional[int]
        Total number of items to process (default: None)
    desc : str
        Description for the progress bar (default: "Processing")
    disable : bool
        Force disable progress bar (default: False)
    mininterval : float
        Minimum time (seconds) between updates (default: 0.1)
    """
    if not TQDM_AVAILABLE:
        # Fallback: create dummy context manager
        class DummyProgress:
            def update(self, n=1):
                pass

        @contextmanager
        def dummy_context():
            yield DummyProgress()

        return dummy_context()

    # Auto-disable if not TTY
    if not sys.stdout.isatty():
        disable = True

    return tqdm(
        total=total,
        desc=desc,
        disable=disable,
        mininterval=mininterval,
        file=sys.stderr,
    )


def should_disable_progress(args=None, env_var: str | None = None) -> bool:
    """Determine if progress should be disabled based on args or environment.

    Parameters
    ----------
    args : optional
        Parsed arguments object (checks disable_progress/quiet flags) (default: None)
    env_var : Optional[str]
        Environment variable name to check (default: None)
    """
    import os

    # Check if progress is explicitly forced on (for background processes)
    if os.getenv("DSA110_FORCE_PROGRESS", "").lower() in ("1", "true", "yes"):
        return False  # Don't disable - force progress on

    # Check environment variable
    if env_var:
        if os.getenv(env_var, "").lower() in ("1", "true", "yes"):
            return True

    # Check args
    if args:
        if getattr(args, "disable_progress", False) or getattr(args, "quiet", False):
            return True

    # Check if stdout is not a TTY (but allow override via DSA110_FORCE_PROGRESS)
    if not sys.stdout.isatty():
        return True

    return False


# =============================================================================
# Stage-level Progress Monitoring
# =============================================================================
# For long-running operations (CASA calibration, WSClean imaging) that don't
# have built-in progress callbacks.


class StageProgressMonitor:
    """Monitor and report progress for long-running pipeline stages.

        This class provides a consistent progress reporting mechanism for operations
        that don't have built-in progress callbacks (e.g., CASA tasks, subprocesses).

        Example
    -------
        >>> with StageProgressMonitor("Bandpass solve", output_path="/path/to/caltable") as monitor:
        ...     monitor.set_context(rows=1000000, spws=16, antennas=110)
        ...     casa_bandpass(**kwargs)  # Long-running operation
    """

    def __init__(
        self,
        stage_name: str,
        *,
        output_path: str | None = None,
        poll_interval: float = 5.0,
        estimated_seconds: float | None = None,
        quiet: bool = False,
    ):
        """Initialize progress monitor.

        Parameters
        ----------
        stage_name : str
            Human-readable name of the stage (e.g., "Bandpass solve")
        output_path : Optional[str]
            Path to output file/directory to monitor for size changes
        poll_interval : float
            How often to report progress (seconds)
        estimated_seconds : Optional[float]
            Estimated total runtime (for ETA calculation)
        quiet : bool
            If True, suppress progress output (for batch/non-interactive)
        """
        self.stage_name = stage_name
        self.output_path = Path(output_path) if output_path else None
        self.poll_interval = poll_interval
        self.estimated_seconds = estimated_seconds
        self.quiet = quiet or should_disable_progress()

        self._stop_event = threading.Event()
        self._monitor_thread: threading.Thread | None = None
        self._start_time: float | None = None
        self._context: dict[str, Any] = {}
        self._exception: Exception | None = None

    def set_context(self, **kwargs: Any) -> None:
        """Set context information to display in progress messages.

        Parameters
        ----------
        **kwargs :
            Key-value pairs to display (e.g., rows=1000000, spws=16)
        **kwargs : Any :

        **kwargs : Any :

        **kwargs: Any :


        """
        self._context.update(kwargs)

    def _format_elapsed(self) -> str:
        """Format elapsed time as human-readable string."""
        if self._start_time is None:
            return "0m00s"
        elapsed = time.time() - self._start_time
        return f"{int(elapsed // 60)}m{int(elapsed % 60):02d}s"

    def _format_context(self) -> str:
        """Format context as compact string."""
        if not self._context:
            return ""

        parts = []
        for key, value in self._context.items():
            if isinstance(value, int) and value > 1000:
                parts.append(f"{value:,} {key}")
            else:
                parts.append(f"{value} {key}")
        return " × ".join(parts)

    def _get_output_size_mb(self) -> float | None:
        """Get current size of output path in MB."""
        if not self.output_path or not self.output_path.exists():
            return None

        try:
            if self.output_path.is_dir():
                total_size = sum(
                    f.stat().st_size for f in self.output_path.rglob("*") if f.is_file()
                )
            else:
                total_size = self.output_path.stat().st_size
            return total_size / (1024 * 1024)
        except Exception:
            return None

    def _monitor_loop(self) -> None:
        """Background thread loop for progress monitoring."""
        last_size = 0.0

        while not self._stop_event.is_set():
            self._stop_event.wait(self.poll_interval)
            if self._stop_event.is_set():
                break

            elapsed_str = self._format_elapsed()
            size_mb = self._get_output_size_mb()

            if size_mb is not None:
                if size_mb > last_size:
                    print(
                        f"  ... {elapsed_str} elapsed, output: {size_mb:.1f} MB",
                        flush=True,
                    )
                    last_size = size_mb
                else:
                    print(f"  ... {elapsed_str} elapsed (processing...)", flush=True)
            elif self.output_path:
                print(f"  ... {elapsed_str} elapsed (initializing...)", flush=True)
            else:
                print(f"  ... {elapsed_str} elapsed", flush=True)

    def _print_header(self) -> None:
        """Print stage header with context information."""
        eta_part = ""
        if self.estimated_seconds:
            eta_min = int(self.estimated_seconds // 60)
            eta_max = int((self.estimated_seconds * 1.5) // 60)
            if eta_min == eta_max:
                eta_part = f" (estimated ~{eta_min} min)"
            else:
                eta_part = f" (estimated {eta_min}–{eta_max} min)"

        print(f"\n→ {self.stage_name} starting{eta_part}...", flush=True)

        context_str = self._format_context()
        if context_str:
            print(f"  Data: {context_str}", flush=True)

        if self.output_path:
            print(f"  Output: {self.output_path}", flush=True)

        sys.stdout.flush()

    def _print_completion(self, success: bool) -> None:
        """Print completion message.

        Parameters
        ----------
        success : bool
            Whether the operation completed successfully
        """
        elapsed_str = self._format_elapsed()
        size_mb = self._get_output_size_mb()

        if success:
            size_part = f", {size_mb:.1f} MB" if size_mb else ""
            print(f"  ✓ {self.stage_name} completed in {elapsed_str}{size_part}", flush=True)
        else:
            print(f"  ✗ {self.stage_name} FAILED after {elapsed_str}", flush=True)

    def start(self) -> None:
        """Start progress monitoring."""
        if self.quiet:
            return

        self._start_time = time.time()
        self._print_header()

        self._stop_event.clear()
        self._monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._monitor_thread.start()

    def stop(self, success: bool = True) -> None:
        """Stop progress monitoring and print completion.

        Parameters
        ----------
        success : bool :
            (Default value = True)
        success : bool :
            (Default value = True)
        """
        if self._monitor_thread is None:
            if not self.quiet and self._start_time is not None:
                self._print_completion(success)
            return

        self._stop_event.set()
        self._monitor_thread.join(timeout=2.0)
        self._monitor_thread = None

        if not self.quiet:
            self._print_completion(success)

    def __enter__(self) -> "StageProgressMonitor":
        """Context manager entry - starts monitoring."""
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        """Context manager exit - stops monitoring."""
        success = exc_type is None
        self.stop(success=success)
        return False  # Don't suppress exceptions


@contextmanager
def stage_progress(
    stage_name: str,
    *,
    output_path: str | None = None,
    poll_interval: float = 5.0,
    estimated_seconds: float | None = None,
    quiet: bool = False,
    **context_kwargs,
):
    """Context manager for monitoring a pipeline stage with progress.

        This is a convenience wrapper around StageProgressMonitor for simple use cases.

    Parameters
    ----------
    stage_name : str
        Human-readable name of the stage
    output_path : Optional[str]
        Path to monitor for size changes (default: None)
    poll_interval : float
        Progress report interval in seconds (default: 5.0)
    estimated_seconds : Optional[float]
        Estimated runtime for ETA display (default: None)
    quiet : bool
        Suppress output if True (default: False)
        **context_kwargs
        Context to display (e.g., rows=1000, spws=16)

    Yields
    ------
        StageProgressMonitor
        StageProgressMonitor instance

        Example
    -------
        >>> with stage_progress("Bandpass solve", output_path=caltable, rows=n_rows):
        ...     casa_bandpass(**kwargs)
    """
    monitor = StageProgressMonitor(
        stage_name,
        output_path=output_path,
        poll_interval=poll_interval,
        estimated_seconds=estimated_seconds,
        quiet=quiet,
    )
    if context_kwargs:
        monitor.set_context(**context_kwargs)

    with monitor:
        yield monitor


def run_with_stage_progress(
    func: Callable[..., Any],
    *args,
    stage_name: str,
    output_path: str | None = None,
    poll_interval: float = 5.0,
    estimated_seconds: float | None = None,
    quiet: bool = False,
    **kwargs,
) -> Any:
    """Run a function with stage-level progress monitoring.

        This is useful for wrapping blocking calls that don't provide progress callbacks.

    Parameters
    ----------
    func : Callable[..., Any]
        Function to run
        *args
        Positional arguments to pass to func
    stage_name : str
        Human-readable name for progress display
    output_path : Optional[str]
        Path to monitor for size changes (default: None)
    poll_interval : float
        Progress report interval in seconds (default: 5.0)
    estimated_seconds : Optional[float]
        Estimated runtime for ETA display (default: None)
    quiet : bool
        Suppress output if True (default: False)
        **kwargs
        Keyword arguments to pass to func

        Example
    -------
        >>> result = run_with_stage_progress(
        ...     casa_bandpass,
        ...     vis=ms, caltable=caltable,
        ...     stage_name="Bandpass solve",
        ...     output_path=caltable,
        ... )
    """
    with stage_progress(
        stage_name,
        output_path=output_path,
        poll_interval=poll_interval,
        estimated_seconds=estimated_seconds,
        quiet=quiet,
    ):
        return func(*args, **kwargs)


def estimate_calibration_time(n_rows: int, n_spws: int, n_antennas: int) -> float:
    """Estimate calibration solve time in seconds.

        Based on empirical measurements with DSA-110 data (~1.8M rows, 16 SPWs, 110 antennas).

    Parameters
    ----------
    n_rows : int
        Number of rows in MS
    n_spws : int
        Number of spectral windows
    n_antennas : int
        Number of antennas
    """
    # Empirical: ~2 seconds per SPW for ~1.8M rows baseline
    # Scale roughly linearly with row count and number of SPWs
    base_time = 10  # Startup overhead
    per_spw_time = 2.0 * (n_rows / 1_800_000)  # ~2s per SPW at 1.8M rows
    antenna_factor = (n_antennas / 110) ** 0.5  # Weak scaling with antenna count

    return base_time + (per_spw_time * n_spws * antenna_factor)


def estimate_imaging_time(n_rows: int, imsize: int, niter: int) -> float:
    """Estimate imaging time in seconds.

        Based on empirical measurements with WSClean.

    Parameters
    ----------
    n_rows : int
        Number of rows in MS
    imsize : int
        Image size in pixels
    niter : int
        Number of clean iterations
    """
    # Empirical: For imsize=2048, ~60-180s depending on iterations
    base_time = 30  # Startup/reorder overhead
    gridding_time = (n_rows / 1_000_000) * (imsize / 2048) ** 2 * 30
    clean_time = (niter / 1000) * 10  # ~10s per 1000 iterations

    return base_time + gridding_time + clean_time


# =============================================================================
# Bandpass Live Channel Progress Monitor
# =============================================================================


class BandpassChannelMonitor:
    """Monitor CASA bandpass solve with live per-channel progress output.

        Shows status for ALL channels (not just flagged ones) as CASA solves them.
        Parses CASA log output in real-time to track solution progress.

        Example
    -------
        >>> from dsa110_continuum.utils import TempPaths
        >>> log_path = TempPaths.log("casa_bandpass.log", "calibration")
        >>> monitor = BandpassChannelMonitor(n_spws=16, n_chans=48, casa_log_path=str(log_path))
        >>> with monitor:
        ...     casa_bandpass(**kwargs)
    """

    def __init__(
        self,
        n_spws: int,
        n_chans: int,
        casa_log_path: str | None = None,
        poll_interval: float = 0.5,
        quiet: bool = False,
    ):
        """Initialize bandpass channel monitor.

        Parameters
        ----------
        n_spws : int
            Number of spectral windows
        n_chans : int
            Number of channels per SPW
        casa_log_path : Optional[str]
            Path to CASA log file to tail (auto-detected if None)
        poll_interval : float
            How often to check log file (seconds)
        quiet : bool
            If True, suppress output
        """
        self.n_spws = n_spws
        self.n_chans = n_chans
        self.poll_interval = poll_interval
        self.quiet = quiet or should_disable_progress()

        # Channel state: 0=pending, 1=solved OK, 2=solved with flags
        self._channel_state: dict[tuple[int, int], int] = {}
        self._channel_flags: dict[tuple[int, int], int] = {}  # (spw, chan) -> n_flagged
        self._channel_total: dict[tuple[int, int], int] = {}  # (spw, chan) -> n_total

        # Initialize all channels as pending
        for spw in range(n_spws):
            for chan in range(n_chans):
                self._channel_state[(spw, chan)] = 0
                self._channel_flags[(spw, chan)] = 0
                self._channel_total[(spw, chan)] = 0

        # CASA log file to tail
        if casa_log_path:
            self._log_path = Path(casa_log_path)
        else:
            # Auto-detect CASA log path
            import os

            casa_log = os.environ.get("CASALOGFILE", "")
            if casa_log and Path(casa_log).exists():
                self._log_path = Path(casa_log)
            else:
                # Default location
                self._log_path = Path.home() / ".casa" / "logs" / "casa.log"

        self._stop_event = threading.Event()
        self._monitor_thread: threading.Thread | None = None
        self._start_time: float | None = None
        self._last_log_pos = 0
        self._solved_count = 0
        self._total_channels = n_spws * n_chans

    def _parse_casa_log_line(self, line: str) -> None:
        """Parse a CASA log line for bandpass solution info.

            CASA outputs lines like:
            '1 of 174 solutions flagged due to SNR < 3 in spw=3 (chan=14) at 2025/10/02/15:42:35.2'

        Parameters
        ----------
        line : str
            A single line from the CASA log
        """
        import re

        # Pattern for flagged solutions
        pattern = r"(\d+) of (\d+) solutions flagged due to SNR.*spw=(\d+).*chan=(\d+)"
        match = re.search(pattern, line)
        if match:
            n_flagged = int(match.group(1))
            n_total = int(match.group(2))
            spw = int(match.group(3))
            chan = int(match.group(4))

            key = (spw, chan)
            if key in self._channel_state:
                self._channel_state[key] = 2  # Solved with flags
                self._channel_flags[key] = n_flagged
                self._channel_total[key] = n_total
                if self._channel_state[key] == 0:
                    self._solved_count += 1

    def _tail_log_file(self) -> list[str]:
        """Read new lines from CASA log file."""
        new_lines = []
        try:
            if not self._log_path.exists():
                return new_lines

            with open(self._log_path) as f:
                f.seek(self._last_log_pos)
                new_content = f.read()
                self._last_log_pos = f.tell()

                if new_content:
                    new_lines = new_content.splitlines()
        except Exception:
            pass

        return new_lines

    def _print_channel_row(self, spw: int) -> str:
        """Generate a single SPW row showing all channels.

        Parameters
        ----------
        spw : int
            Spectral window index
        """
        chars = []
        for chan in range(self.n_chans):
            state = self._channel_state[(spw, chan)]
            n_flagged = self._channel_flags[(spw, chan)]
            n_total = self._channel_total[(spw, chan)]

            if state == 0:
                # Pending - not yet solved
                chars.append("·")
            elif state == 1:
                # Solved OK - no flags
                chars.append("✓")
            else:
                # Solved with flags
                if n_total > 0:
                    frac = n_flagged / n_total
                    if frac >= 0.999:
                        chars.append("✗")  # Dead channel
                    elif frac > 0.5:
                        chars.append("▓")  # High flagging
                    elif frac > 0.2:
                        chars.append("▒")  # Medium flagging
                    else:
                        chars.append("░")  # Low flagging
                else:
                    chars.append("?")

        return "".join(chars)

    def _print_live_grid(self) -> None:
        """Print the current state of all channels."""
        # Clear previous output and reprint
        print("\033[2J\033[H", end="")  # Clear screen and move cursor to top

        elapsed = time.time() - self._start_time if self._start_time else 0
        elapsed_str = f"{int(elapsed // 60)}m{int(elapsed % 60):02d}s"

        print("=" * 80)
        print(f"BANDPASS SOLVE - Live Channel Progress ({elapsed_str} elapsed)")
        print("=" * 80)
        print("Legend: · pending  ✓ OK  ░ <20% flagged  ▒ 20-50%  ▓ >50%  ✗ dead")
        print("-" * 80)
        print(f"{'SPW':>4}  {'Channels 0-' + str(self.n_chans - 1):^{self.n_chans}}")
        print("-" * 80)

        total_flagged_chans = 0
        total_solved = 0

        for spw in range(self.n_spws):
            row = self._print_channel_row(spw)
            # Count solved and flagged for this SPW
            spw_solved = sum(1 for c in range(self.n_chans) if self._channel_state[(spw, c)] > 0)
            spw_flagged = sum(1 for c in range(self.n_chans) if self._channel_state[(spw, c)] == 2)
            total_solved += spw_solved
            total_flagged_chans += spw_flagged

            flag_info = f" ({spw_flagged} flagged)" if spw_flagged > 0 else ""
            print(f"{spw:>4}  {row}{flag_info}")

        print("-" * 80)
        pct = (total_solved / self._total_channels * 100) if self._total_channels > 0 else 0
        print(
            f"Progress: {total_solved}/{self._total_channels} channels solved ({pct:.1f}%), "
            f"{total_flagged_chans} with flags"
        )
        print("=" * 80)
        sys.stdout.flush()

    def _monitor_loop(self) -> None:
        """Background thread loop for monitoring CASA log."""
        last_print_time = 0

        while not self._stop_event.is_set():
            # Read new log lines
            new_lines = self._tail_log_file()
            for line in new_lines:
                self._parse_casa_log_line(line)

            # Update display periodically
            now = time.time()
            if now - last_print_time >= 1.0:  # Update display every second
                if not self.quiet:
                    self._print_live_grid()
                last_print_time = now

            self._stop_event.wait(self.poll_interval)

    def _print_header(self) -> None:
        """Print initial header."""
        print("\n" + "=" * 80)
        print("BANDPASS SOLVE - Live Channel Progress")
        print("=" * 80)
        print(
            f"Monitoring {self.n_spws} SPWs × {self.n_chans} channels = {self._total_channels} total"
        )
        print(f"CASA log: {self._log_path}")
        print("=" * 80 + "\n")
        sys.stdout.flush()

    def _print_final_summary(self) -> None:
        """Print final summary after solve completes.

        Note: This only shows a brief summary. The detailed per-channel breakdown
        is printed by _print_bandpass_solution_summary() in calibration.py which
        reads the actual caltable data (not just CASA log output).

        """
        elapsed = time.time() - self._start_time if self._start_time else 0
        elapsed_str = f"{int(elapsed // 60)}m{int(elapsed % 60):02d}s"

        # Count channels with flags (from CASA log output)
        channels_with_flags = sum(
            1 for (spw, chan), state in self._channel_state.items() if state == 2
        )

        print("\n" + "=" * 80)
        print(f"BANDPASS SOLVE COMPLETE ({elapsed_str})")
        print("=" * 80)
        print(f"  CASA reported {channels_with_flags} channels with flagged solutions")
        print("  (Channels with 0 flags are not reported by CASA during solve)")
        print("  → Full per-channel summary follows from caltable analysis...")
        print("=" * 80)
        sys.stdout.flush()

    def start(self) -> None:
        """Start monitoring."""
        self._start_time = time.time()

        # Record current log position (to only read new lines)
        if self._log_path.exists():
            self._last_log_pos = self._log_path.stat().st_size

        if not self.quiet:
            self._print_header()

        self._stop_event.clear()
        self._monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._monitor_thread.start()

    def stop(self, success: bool = True) -> None:
        """Stop monitoring and print final summary.

        Parameters
        ----------
        success : bool :
            (Default value = True)
        success : bool :
            (Default value = True)
        """
        if self._monitor_thread:
            self._stop_event.set()
            self._monitor_thread.join(timeout=2.0)
            self._monitor_thread = None

        if not self.quiet:
            self._print_final_summary()

    def __enter__(self) -> "BandpassChannelMonitor":
        """Context manager entry."""
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        """Context manager exit."""
        success = exc_type is None
        self.stop(success=success)
        return False
