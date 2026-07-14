"""Centralized path utilities for temporary files and outputs.

This module provides a unified interface for temporary file and directory
management, ensuring consistent usage across the DSA-110 pipeline.

Policy:
- /tmp/dsa110-contimg/ → PID/PGID files ONLY
- /stage/dsa110-contimg/ → All other temp files (logs, plots, reports, test outputs)

Usage:
    from dsa110_continuum.utils.paths.temporary import TempPaths

    # Get paths for different types of temporary files
    log_path = TempPaths.log('myfile.log', 'calibration')
    plot_path = TempPaths.plot('debug.png')
    report_path = TempPaths.report('analysis.txt')
    test_dir = TempPaths.test_output('test_name')
    pid_file = TempPaths.pid_file('worker')
"""

from pathlib import Path

# Lazy imports: data_config is imported inside each method to avoid circular
# import (data_config -> paths -> temporary -> data_config). See plan:
# conversion_log_issues_diagnosis.


class TempPaths:
    """Centralized temporary path management.

    This class provides static methods for obtaining temporary file paths
    according to the DSA-110 path policy.
    """

    @staticmethod
    def log(filename: str, category: str = "misc") -> Path:
        """Get path for a log file.

        Parameters
        ----------
        filename : str
            Name of the log file
        category : str, optional
            Log category (e.g., 'calibration', 'imaging', 'misc'), by default "misc"

        Returns
        -------
        Path
            Full path to the log file in /stage/.../logs/category/

        Examples
        --------
        >>> log_path = TempPaths.log('debug.log', 'calibration')
        >>> log_path = TempPaths.log('processing.log')  # Uses 'misc' category
        """
        from dsa110_continuum.database.data_config import get_logs_dir

        log_dir = get_logs_dir(category)
        log_dir.mkdir(parents=True, exist_ok=True)
        return log_dir / filename

    @staticmethod
    def plot(filename: str) -> Path:
        """Get path for a debug plot file.

        Parameters
        ----------
        filename : str
            Name of the plot file

        Returns
        -------
        Path
            Full path to the plot file in /stage/.../debug/

        Examples
        --------
        >>> plot_path = TempPaths.plot('debug_antenna_positions.png')
        >>> plot_path = TempPaths.plot('flux_distribution.pdf')
        """
        from dsa110_continuum.database.data_config import get_debug_plots_dir

        plot_dir = get_debug_plots_dir()
        plot_dir.mkdir(parents=True, exist_ok=True)
        return plot_dir / filename

    @staticmethod
    def report(filename: str) -> Path:
        """Get path for a report file.

        Parameters
        ----------
        filename : str
            Name of the report file

        Returns
        -------
        Path
            Full path to the report file in /stage/.../reports/

        Examples
        --------
        >>> report_path = TempPaths.report('analysis_results.txt')
        >>> report_path = TempPaths.report('ground_truth.json')
        """
        from dsa110_continuum.database.data_config import get_reports_dir

        report_dir = get_reports_dir()
        report_dir.mkdir(parents=True, exist_ok=True)
        return report_dir / filename

    @staticmethod
    def test_output(test_name: str) -> Path:
        """Get directory for test output files.

        Parameters
        ----------
        test_name : str
            Name of the test (used as subdirectory name)

        Returns
        -------
        Path
            Full path to the test output directory in /stage/.../test/

        Examples
        --------
        >>> test_dir = TempPaths.test_output('simulation_quick_test')
        >>> test_dir = TempPaths.test_output('time_domain_example')
        """
        from dsa110_continuum.database.data_config import get_test_dir

        test_dir = get_test_dir() / test_name
        test_dir.mkdir(parents=True, exist_ok=True)
        return test_dir

    @staticmethod
    def pid_file(process_name: str) -> Path:
        """Get path for a PID file.

        NOTE: This is the ONLY method that should write to /tmp/.
        PID/PGID files are ephemeral process state and belong in /tmp/.

        Parameters
        ----------
        process_name : str
            Name of the process

        Returns
        -------
        Path
            Full path to the PID file in /tmp/dsa110-contimg/

        Examples
        --------
        >>> pid_path = TempPaths.pid_file('worker')
        >>> pid_path = TempPaths.pid_file('dagster-daemon')
        """
        from dsa110_continuum.database.data_config import get_pid_dir

        pid_dir = get_pid_dir()
        pid_dir.mkdir(parents=True, exist_ok=True)
        return pid_dir / f"{process_name}.pid"

    @staticmethod
    def pgid_file(process_name: str) -> Path:
        """Get path for a PGID (process group ID) file.

        NOTE: This is the ONLY method (along with pid_file) that should write to /tmp/.
        PID/PGID files are ephemeral process state and belong in /tmp/.

        Parameters
        ----------
        process_name : str
            Name of the process

        Returns
        -------
        Path
            Full path to the PGID file in /tmp/dsa110-contimg/

        Examples
        --------
        >>> pgid_path = TempPaths.pgid_file('worker')
        >>> pgid_path = TempPaths.pgid_file('dagster-daemon')
        """
        from dsa110_continuum.database.data_config import get_pid_dir

        pid_dir = get_pid_dir()
        pid_dir.mkdir(parents=True, exist_ok=True)
        return pid_dir / f"{process_name}.pgid"

    @staticmethod
    def get_temp_dir(subdirectory: str | None = None) -> Path:
        """Get a general-purpose temporary directory.

        For most uses, prefer the specific methods (log, plot, report, test_output).
        This method is provided for cases where a temporary directory is needed
        but doesn't fit the other categories.

        Parameters
        ----------
        subdirectory : str, optional
            Optional subdirectory name within the test directory

        Returns
        -------
        Path
            Full path to the test output directory in /stage/.../test/

        Examples
        --------
        >>> temp_dir = TempPaths.get_temp_dir()
        >>> temp_dir = TempPaths.get_temp_dir('custom_temp')
        """
        if subdirectory:
            return TempPaths.test_output(subdirectory)
        from dsa110_continuum.database.data_config import get_test_dir

        return get_test_dir()
