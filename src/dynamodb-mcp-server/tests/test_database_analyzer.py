"""Tests for DatabaseAnalyzer static methods and utility functions.

These tests cover the DatabaseAnalyzer class methods that are used
across all database plugins for connection management, validation,
and file operations.
"""

import os
import pytest
from awslabs.dynamodb_mcp_server.database_analyzers import DatabaseAnalyzer
from awslabs.dynamodb_mcp_server.db_analyzer.mysql import MySQLPlugin


class TestConnectionParams:
    """Test connection parameter building and validation."""

    def test_build_connection_params_mysql(self, tmp_path):
        """Test building MySQL connection parameters."""
        params = DatabaseAnalyzer.build_connection_params(
            'mysql',
            aws_cluster_arn='test-cluster',
            aws_secret_arn='test-secret',
            database_name='test_db',
            aws_region='us-east-1',
            max_query_results=1000,
            pattern_analysis_days=30,
            output_dir=str(tmp_path),
        )

        assert params['cluster_arn'] == 'test-cluster'
        assert params['secret_arn'] == 'test-secret'
        assert params['database'] == 'test_db'
        assert params['region'] == 'us-east-1'
        assert params['max_results'] == 1000
        assert params['pattern_analysis_days'] == 30
        assert params['output_dir'] == str(tmp_path)

    def test_build_connection_params_invalid_directory(self):
        """Test build_connection_params with invalid output directory."""
        # Test non-absolute path
        with pytest.raises(ValueError, match='Output directory must be an absolute path'):
            DatabaseAnalyzer.build_connection_params('mysql', output_dir='relative/path')

        # Test non-existent directory
        with pytest.raises(ValueError, match='Output directory does not exist or is not writable'):
            DatabaseAnalyzer.build_connection_params('mysql', output_dir='/nonexistent/path')

    def test_build_connection_params_unsupported_database(self, tmp_path):
        """Test build_connection_params with unsupported database type."""
        with pytest.raises(ValueError, match='Unsupported database type: postgresql'):
            DatabaseAnalyzer.build_connection_params(
                'postgresql',
                database_name='test_db',
                output_dir=str(tmp_path),
            )

    def test_validate_connection_params_mysql_missing(self):
        """Test MySQL connection parameter validation with missing params."""
        params = {'cluster_arn': 'test'}
        missing, descriptions = DatabaseAnalyzer.validate_connection_params('mysql', params)

        assert 'secret_arn' in missing
        assert 'database' in missing
        assert 'region' in missing
        assert descriptions['cluster_arn'] == 'AWS cluster ARN'

    def test_validate_connection_params_all_valid(self):
        """Test validate_connection_params when all params are valid."""
        connection_params = {
            'cluster_arn': 'test-cluster',
            'secret_arn': 'test-secret',
            'database': 'test-db',
            'region': 'us-east-1',
            'output_dir': '/tmp',
        }

        missing_params, param_descriptions = DatabaseAnalyzer.validate_connection_params(
            'mysql', connection_params
        )

        assert missing_params == []
        assert isinstance(param_descriptions, dict)
        assert len(param_descriptions) > 0

    def test_validate_connection_params_unsupported_type(self):
        """Test validate_connection_params with unsupported database type."""
        connection_params = {'some_param': 'value'}

        missing_params, param_descriptions = DatabaseAnalyzer.validate_connection_params(
            'postgresql', connection_params
        )

        assert missing_params == []
        assert param_descriptions == {}


class TestSaveAnalysisFiles:
    """Test analysis file saving functionality."""

    def test_save_analysis_files_empty_results(self):
        """Test save_analysis_files with empty results."""
        plugin = MySQLPlugin()
        saved_files, save_errors = DatabaseAnalyzer.save_analysis_files(
            {}, 'mysql', 'test_db', 30, 500, '/tmp', plugin
        )

        assert saved_files == []
        assert save_errors == []

    def test_save_analysis_files_with_data(self, tmp_path, monkeypatch):
        """Test save_analysis_files with actual data."""

        # Mock datetime to control timestamp
        class MockDateTime:
            @staticmethod
            def now():
                class MockNow:
                    def strftime(self, fmt):
                        return '20231009_120000'

                return MockNow()

        monkeypatch.setattr(
            'awslabs.dynamodb_mcp_server.database_analyzers.datetime', MockDateTime
        )

        results = {
            'comprehensive_table_analysis': {
                'data': [{'table': 'users', 'rows': 100}],
                'description': 'Table analysis',
            },
            'query_performance_stats': {
                'data': [{'pattern': 'SELECT * FROM users', 'frequency': 10}],
                'description': 'Query patterns',
            },
        }

        plugin = MySQLPlugin()
        saved_files, save_errors = DatabaseAnalyzer.save_analysis_files(
            results, 'mysql', 'test_db', 30, 500, str(tmp_path), plugin
        )

        # Should generate markdown files for all expected queries (6 total: 4 schema + 2 performance)
        assert len(saved_files) == 6
        assert len(save_errors) == 0

        # Verify markdown files were created
        for filename in saved_files:
            assert os.path.exists(filename)
            assert filename.endswith('.md')

    def test_save_analysis_files_creation_error(self, tmp_path, monkeypatch):
        """Test save_analysis_files when folder creation fails."""

        def mock_makedirs_fail(*args, **kwargs):
            raise OSError('Permission denied')

        monkeypatch.setattr('os.makedirs', mock_makedirs_fail)

        plugin = MySQLPlugin()
        results = {'table_analysis': {'data': [], 'description': 'Test'}}

        saved_files, save_errors = DatabaseAnalyzer.save_analysis_files(
            results, 'mysql', 'test_db', 30, 500, str(tmp_path), plugin
        )

        assert len(saved_files) == 0
        assert len(save_errors) == 1
        assert 'Failed to create folder' in save_errors[0]

    def test_save_analysis_files_with_generation_errors(self, tmp_path, monkeypatch):
        """Test save_analysis_files when there are generation errors."""

        class MockDateTime:
            @staticmethod
            def now():
                class MockNow:
                    def strftime(self, fmt):
                        return '20231009_120000'

                return MockNow()

        monkeypatch.setattr(
            'awslabs.dynamodb_mcp_server.database_analyzers.datetime', MockDateTime
        )

        # Mock MarkdownFormatter to return errors
        class MockFormatter:
            def __init__(self, *args, **kwargs):
                pass

            def generate_all_files(self):
                return ['/tmp/file1.md'], [
                    ('query1', 'Error message 1'),
                    ('query2', 'Error message 2'),
                ]

        monkeypatch.setattr(
            'awslabs.dynamodb_mcp_server.database_analyzers.MarkdownFormatter', MockFormatter
        )

        results = {
            'comprehensive_table_analysis': {
                'data': [{'table': 'users', 'rows': 100}],
                'description': 'Table analysis',
            },
        }

        plugin = MySQLPlugin()
        saved_files, save_errors = DatabaseAnalyzer.save_analysis_files(
            results, 'mysql', 'test_db', 30, 500, str(tmp_path), plugin, True, []
        )

        assert len(saved_files) == 1
        assert len(save_errors) == 2
        assert 'query1: Error message 1' in save_errors
        assert 'query2: Error message 2' in save_errors

    def test_save_analysis_files_markdown_error(self, tmp_path, monkeypatch):
        """Test save_analysis_files with Markdown generation error."""
        results = {'test': {'description': 'Test', 'data': []}}

        def mock_markdown_formatter_init(*args, **kwargs):
            raise Exception('Markdown generation failed')

        monkeypatch.setattr(
            'awslabs.dynamodb_mcp_server.database_analyzers.MarkdownFormatter',
            mock_markdown_formatter_init,
        )

        plugin = MySQLPlugin()
        saved, errors = DatabaseAnalyzer.save_analysis_files(
            results, 'mysql', 'db', 30, 500, str(tmp_path), plugin
        )

        assert len(errors) == 1
        assert 'Markdown generation failed' in errors[0]


class TestFilterPatternData:
    """Test pattern data filtering functionality."""

    def test_filter_pattern_data_calculation(self):
        """Test filter_pattern_data RPS calculation."""
        pattern_data = [
            {'DIGEST_TEXT': 'SELECT * FROM users', 'COUNT_STAR': 10},
            {'DIGEST_TEXT': 'INSERT INTO users', 'COUNT_STAR': 5},
        ]

        filtered = DatabaseAnalyzer.filter_pattern_data(pattern_data, 30)
        expected = [
            {'DIGEST_TEXT': 'SELECT * FROM users', 'COUNT_STAR': 10, 'calculated_rps': 0.000004},
            {'DIGEST_TEXT': 'INSERT INTO users', 'COUNT_STAR': 5, 'calculated_rps': 0.000002},
        ]

        assert len(filtered) == 2
        assert filtered == expected

    def test_filter_pattern_data_excludes_ddl(self):
        """Test that DDL statements are filtered out."""
        pattern_data = [
            {'DIGEST_TEXT': 'SELECT * FROM users', 'COUNT_STAR': 10},
            {'DIGEST_TEXT': 'CREATE TABLE test', 'COUNT_STAR': 1},
            {'DIGEST_TEXT': 'DROP TABLE old', 'COUNT_STAR': 1},
            {'DIGEST_TEXT': 'ALTER TABLE users', 'COUNT_STAR': 1},
            {'DIGEST_TEXT': 'TRUNCATE TABLE logs', 'COUNT_STAR': 1},
            {'DIGEST_TEXT': 'INSERT INTO users', 'COUNT_STAR': 5},
        ]

        filtered = DatabaseAnalyzer.filter_pattern_data(pattern_data, 30)

        # Should only have SELECT and INSERT (DDL filtered out)
        assert len(filtered) == 2
        assert all('CREATE' not in item['DIGEST_TEXT'] for item in filtered)
        assert all('DROP' not in item['DIGEST_TEXT'] for item in filtered)
        assert all('ALTER' not in item['DIGEST_TEXT'] for item in filtered)
        assert all('TRUNCATE' not in item['DIGEST_TEXT'] for item in filtered)

    def test_filter_pattern_data_empty_input(self):
        """Test filter_pattern_data with empty input."""
        filtered = DatabaseAnalyzer.filter_pattern_data([], 30)
        assert filtered == []

    def test_filter_pattern_data_none_days(self):
        """Test filter_pattern_data uses default when days is None."""
        pattern_data = [
            {'DIGEST_TEXT': 'SELECT * FROM users', 'COUNT_STAR': 10},
        ]

        # When None is passed, it should use DEFAULT_ANALYSIS_DAYS (30)
        filtered = DatabaseAnalyzer.filter_pattern_data(pattern_data, None)

        assert len(filtered) == 1
        # With 30 days default: 10 / (30 * 86400) = 0.000004
        assert filtered[0]['calculated_rps'] == 0.000004


class TestBuildConnectionParamsEdgeCases:
    """Test build_connection_params edge cases."""

    def test_build_connection_params_with_env_vars(self, tmp_path, monkeypatch):
        """Test that environment variables are used as fallback."""
        # Set environment variables
        monkeypatch.setenv('MYSQL_CLUSTER_ARN', 'env-cluster')
        monkeypatch.setenv('MYSQL_SECRET_ARN', 'env-secret')
        monkeypatch.setenv('MYSQL_DATABASE', 'env_db')
        monkeypatch.setenv('AWS_REGION', 'env-region')
        monkeypatch.setenv('MYSQL_MAX_QUERY_RESULTS', '999')

        params = DatabaseAnalyzer.build_connection_params(
            'mysql',
            output_dir=str(tmp_path),
        )

        # Should use env vars when parameters not provided
        assert params['cluster_arn'] == 'env-cluster'
        assert params['secret_arn'] == 'env-secret'
        assert params['database'] == 'env_db'
        assert params['region'] == 'env-region'
        assert params['max_results'] == 999

    def test_build_connection_params_explicit_overrides_env(self, tmp_path, monkeypatch):
        """Test that explicit parameters override environment variables."""
        # Set environment variables
        monkeypatch.setenv('MYSQL_CLUSTER_ARN', 'env-cluster')
        monkeypatch.setenv('MYSQL_SECRET_ARN', 'env-secret')

        params = DatabaseAnalyzer.build_connection_params(
            'mysql',
            aws_cluster_arn='explicit-cluster',
            aws_secret_arn='explicit-secret',
            database_name='explicit_db',
            aws_region='explicit-region',
            output_dir=str(tmp_path),
        )

        # Explicit parameters should override env vars
        assert params['cluster_arn'] == 'explicit-cluster'
        assert params['secret_arn'] == 'explicit-secret'
        assert params['database'] == 'explicit_db'
        assert params['region'] == 'explicit-region'
