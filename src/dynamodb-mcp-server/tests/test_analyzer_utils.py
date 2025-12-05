"""Tests for analyzer_utils module.

These tests cover utility functions for database analysis workflows.
"""

import os
import pytest
import tempfile
from awslabs.dynamodb_mcp_server.db_analyzer import analyzer_utils
from awslabs.dynamodb_mcp_server.db_analyzer.mysql import MySQLPlugin


class TestResolveAndValidatePath:
    """Test path resolution and validation."""

    def test_path_resolution_scenarios(self):
        """Test various path resolution and validation scenarios."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Test 1: Relative path
            output_dir = os.path.join(tmpdir, 'output')
            os.makedirs(output_dir, exist_ok=True)
            result = analyzer_utils.resolve_and_validate_path(
                'output/queries.sql', tmpdir, 'test file'
            )
            assert result.startswith(os.path.realpath(tmpdir))
            assert 'output' in result and 'queries.sql' in result

            # Test 2: Absolute path within base
            file_path = os.path.join(tmpdir, 'queries.sql')
            result = analyzer_utils.resolve_and_validate_path(file_path, tmpdir, 'test file')
            assert result == os.path.normpath(os.path.realpath(file_path))

            # Test 3: Path with ./
            result = analyzer_utils.resolve_and_validate_path(
                './output/queries.sql', tmpdir, 'test file'
            )
            assert result.startswith(os.path.realpath(tmpdir))
            assert 'output' in result

            # Test 4: Path traversal rejected
            with pytest.raises(ValueError, match='Path traversal detected'):
                analyzer_utils.resolve_and_validate_path('/etc/passwd', tmpdir, 'test file')


class TestGenerateQueryFile:
    """Test SQL query file generation."""

    def test_query_file_generation_scenarios(self):
        """Test various query file generation scenarios."""
        plugin = MySQLPlugin()

        with tempfile.TemporaryDirectory() as tmpdir:
            # Test 1: Successful generation
            result = analyzer_utils.generate_query_file(
                plugin, 'test_db', 500, 'queries.sql', tmpdir, 'mysql'
            )
            assert 'SQL queries have been written to:' in result
            assert 'Next Steps:' in result
            assert 'mysql -u user -p' in result
            assert os.path.exists(os.path.join(tmpdir, 'queries.sql'))

            # Test 2: Creates subdirectories
            result = analyzer_utils.generate_query_file(
                plugin, 'test_db', 500, 'output/subdir/queries.sql', tmpdir, 'mysql'
            )
            assert os.path.exists(os.path.join(tmpdir, 'output', 'subdir', 'queries.sql'))

            # Test 3: Missing database name
            result = analyzer_utils.generate_query_file(
                plugin, None, 500, 'queries.sql', tmpdir, 'mysql'
            )
            assert 'database_name is required' in result

            # Test 4: Empty database name
            result = analyzer_utils.generate_query_file(
                plugin, '', 500, 'queries.sql', tmpdir, 'mysql'
            )
            assert 'database_name is required' in result

            # Test 5: Path traversal rejected
            with pytest.raises(ValueError, match='Path traversal detected'):
                analyzer_utils.generate_query_file(
                    plugin, 'test_db', 500, '/etc/passwd', tmpdir, 'mysql'
                )

            # Test 6: Includes proper instructions
            result = analyzer_utils.generate_query_file(
                plugin, 'airline_db', 1000, 'queries.sql', tmpdir, 'mysql'
            )
            assert 'Example commands:' in result
            assert '--table' in result
            assert 'IMPORTANT for MySQL' in result


class TestParseResultsAndGenerateAnalysis:
    """Test result parsing and analysis generation."""

    def test_result_parsing_scenarios(self):
        """Test various result parsing scenarios."""
        plugin = MySQLPlugin()

        with tempfile.TemporaryDirectory() as tmpdir:
            # Test 1: Path traversal - absolute path outside base directory
            with pytest.raises(ValueError, match='Path traversal detected'):
                analyzer_utils.parse_results_and_generate_analysis(
                    plugin, '/nonexistent/file.txt', tmpdir, 'test_db', 30, 500, 'mysql'
                )

            # Test 2: Empty file
            result_file = os.path.join(tmpdir, 'empty.txt')
            with open(result_file, 'w', encoding='utf-8') as f:
                f.write('')
            result = analyzer_utils.parse_results_and_generate_analysis(
                plugin, result_file, tmpdir, 'test_db', 30, 500, 'mysql'
            )
            assert 'No query results found' in result

            # Test 3: Successful parsing
            result_file = os.path.join(tmpdir, 'results.txt')
            with open(result_file, 'w', encoding='utf-8') as f:
                f.write("""| marker |
| -- QUERY_NAME_START: comprehensive_table_analysis |
+------------+-----------+
| table_name | row_count |
+------------+-----------+
| users      |      1000 |
+------------+-----------+
| marker |
| -- QUERY_NAME_END: comprehensive_table_analysis |
""")
            result = analyzer_utils.parse_results_and_generate_analysis(
                plugin, result_file, tmpdir, 'test_db', 30, 500, 'mysql'
            )
            assert 'Database Analysis Complete' in result
            assert 'Self-Service Mode' in result
            assert 'test_db' in result


class TestExecuteManagedAnalysis:
    """Test managed mode analysis execution."""

    @pytest.mark.asyncio
    async def test_execute_managed_analysis_success(self, monkeypatch):
        """Test successful managed analysis execution."""
        # Arrange
        plugin = MySQLPlugin()
        connection_params = {
            'database': 'test_db',
            'pattern_analysis_days': 30,
            'max_results': 500,
            'output_dir': '/tmp',
        }
        source_db_type = 'mysql'

        # Mock execute_managed_mode
        async def mock_execute_managed_mode(params):
            return {
                'results': {
                    'comprehensive_table_analysis': {
                        'description': 'Test',
                        'data': [{'table': 'users'}],
                    }
                },
                'performance_enabled': True,
                'skipped_queries': [],
                'errors': [],
            }

        monkeypatch.setattr(plugin, 'execute_managed_mode', mock_execute_managed_mode)

        # Mock save_analysis_files
        def mock_save_files(*args, **kwargs):
            return ['/tmp/file1.md'], []

        from awslabs.dynamodb_mcp_server import database_analyzers

        monkeypatch.setattr(
            database_analyzers.DatabaseAnalyzer, 'save_analysis_files', mock_save_files
        )

        # Act
        result = await analyzer_utils.execute_managed_analysis(
            plugin, connection_params, source_db_type
        )

        # Assert
        assert 'Database Analysis Complete' in result
        assert 'Managed Mode' in result
        assert 'test_db' in result

    @pytest.mark.asyncio
    async def test_execute_managed_analysis_all_queries_failed(self, monkeypatch):
        """Test managed analysis when all queries fail."""
        # Arrange
        plugin = MySQLPlugin()
        connection_params = {
            'database': 'test_db',
            'pattern_analysis_days': 30,
            'max_results': 500,
            'output_dir': '/tmp',
        }
        source_db_type = 'mysql'

        # Mock execute_managed_mode with empty results
        async def mock_execute_managed_mode(params):
            return {
                'results': {},
                'performance_enabled': True,
                'skipped_queries': [],
                'errors': ['Query 1 failed', 'Query 2 failed'],
            }

        monkeypatch.setattr(plugin, 'execute_managed_mode', mock_execute_managed_mode)

        # Act
        result = await analyzer_utils.execute_managed_analysis(
            plugin, connection_params, source_db_type
        )

        # Assert
        assert 'Database Analysis Failed' in result
        assert 'All 2 queries failed' in result
        assert '1. Query 1 failed' in result
        assert '2. Query 2 failed' in result


class TestReportBuilding:
    """Test analysis and failure report building."""

    def test_analysis_report_scenarios(self):
        """Test various analysis report building scenarios."""
        # Test 1: Self-service mode report
        result = analyzer_utils.build_analysis_report(
            ['/tmp/file1.md', '/tmp/file2.md'],
            [],
            'test_db',
            '/tmp/results.txt',
            is_self_service=True,
        )
        assert 'Self-Service Mode' in result
        assert 'test_db' in result
        assert '/tmp/results.txt' in result
        assert '/tmp/file1.md' in result

        # Test 2: Managed mode report
        result = analyzer_utils.build_analysis_report(
            ['/tmp/file1.md'], [], 'prod_db', None, is_self_service=False, analysis_period=30
        )
        assert 'Managed Mode' in result
        assert 'prod_db' in result
        assert '30 days' in result

        # Test 3: Report with errors
        result = analyzer_utils.build_analysis_report(
            ['/tmp/file1.md'], ['Error 1', 'Error 2'], 'test_db', None, is_self_service=False
        )
        assert 'File Save Errors:' in result
        assert 'Error 1' in result

        # Test 4: Report with no files
        result = analyzer_utils.build_analysis_report(
            [], [], 'test_db', None, is_self_service=False
        )
        assert 'Database Analysis Complete' in result
        assert 'Generated Analysis Files (Read All):' not in result

    def test_failure_report_scenarios(self):
        """Test failure report building with various error counts."""
        # Test 1: Single error
        result = analyzer_utils.build_failure_report(['Connection timeout'])
        assert 'Database Analysis Failed' in result
        assert 'All 1 queries failed' in result
        assert '1. Connection timeout' in result

        # Test 2: Multiple errors
        result = analyzer_utils.build_failure_report(['Error 1', 'Error 2', 'Error 3'])
        assert 'All 3 queries failed' in result
        assert '1. Error 1' in result
        assert '2. Error 2' in result
        assert '3. Error 3' in result
