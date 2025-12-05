# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Database analyzer classes for source database analysis."""

import os
from awslabs.dynamodb_mcp_server.db_analyzer import DatabasePlugin
from awslabs.dynamodb_mcp_server.markdown_formatter import MarkdownFormatter
from datetime import datetime
from loguru import logger
from typing import Any, Dict, List, Tuple


DEFAULT_ANALYSIS_DAYS = 30
DEFAULT_MAX_QUERY_RESULTS = 500
SECONDS_PER_DAY = 86400
DDL_PREFIXES = ('CREATE ', 'DROP ', 'ALTER ', 'TRUNCATE ')


class DatabaseAnalyzer:
    """Base class for database analyzers."""

    @staticmethod
    def build_connection_params(source_db_type: str, **kwargs) -> Dict[str, Any]:
        """Build connection parameters for database analysis.

        Args:
            source_db_type: Type of source database (e.g., 'mysql')
            **kwargs: Connection parameters (aws_cluster_arn, aws_secret_arn, etc.)

        Returns:
            Dictionary of connection parameters

        Raises:
            ValueError: If database type is not supported
        """
        if source_db_type == 'mysql':
            user_provided_dir = kwargs.get('output_dir')

            # Validate user-provided directory
            if not os.path.isabs(user_provided_dir):
                raise ValueError(f'Output directory must be an absolute path: {user_provided_dir}')
            if not os.path.isdir(user_provided_dir) or not os.access(user_provided_dir, os.W_OK):
                raise ValueError(
                    f'Output directory does not exist or is not writable: {user_provided_dir}'
                )
            output_dir = user_provided_dir

            return {
                'cluster_arn': kwargs.get('aws_cluster_arn') or os.getenv('MYSQL_CLUSTER_ARN'),
                'secret_arn': kwargs.get('aws_secret_arn') or os.getenv('MYSQL_SECRET_ARN'),
                'database': kwargs.get('database_name') or os.getenv('MYSQL_DATABASE'),
                'region': kwargs.get('aws_region') or os.getenv('AWS_REGION'),
                'max_results': kwargs.get('max_query_results')
                or int(os.getenv('MYSQL_MAX_QUERY_RESULTS', str(DEFAULT_MAX_QUERY_RESULTS))),
                'pattern_analysis_days': kwargs.get(
                    'pattern_analysis_days', DEFAULT_ANALYSIS_DAYS
                ),
                'output_dir': output_dir,
            }
        raise ValueError(f'Unsupported database type: {source_db_type}')

    @staticmethod
    def validate_connection_params(
        source_db_type: str, connection_params: Dict[str, Any]
    ) -> Tuple[List[str], Dict[str, str]]:
        """Validate connection parameters for database type.

        Args:
            source_db_type: Type of source database
            connection_params: Dictionary of connection parameters

        Returns:
            Tuple of (missing_params, param_descriptions)
        """
        if source_db_type == 'mysql':
            required_params = ['cluster_arn', 'secret_arn', 'database', 'region']
            missing_params = [
                param
                for param in required_params
                if not connection_params.get(param)
                or (
                    isinstance(connection_params[param], str)
                    and connection_params[param].strip() == ''
                )
            ]

            param_descriptions = {
                'cluster_arn': 'AWS cluster ARN',
                'secret_arn': 'AWS secret ARN',
                'database': 'Database name',
                'region': 'AWS region',
            }
            return missing_params, param_descriptions
        return [], {}

    @staticmethod
    def save_analysis_files(
        results: Dict[str, Any],
        source_db_type: str,
        database: str,
        pattern_analysis_days: int,
        max_results: int,
        output_dir: str,
        plugin: DatabasePlugin,
        performance_enabled: bool = True,
        skipped_queries: List[str] = None,
    ) -> Tuple[List[str], List[str]]:
        """Save analysis results to Markdown files using MarkdownFormatter.

        Args:
            results: Dictionary of query results
            source_db_type: Type of source database
            database: Database name
            pattern_analysis_days: Number of days to analyze the logs for pattern analysis query
            max_results: Maximum results per query
            output_dir: Absolute directory path where the timestamped output analysis folder will be created
            plugin: DatabasePlugin instance for getting query definitions (REQUIRED)
            performance_enabled: Whether performance schema is enabled
            skipped_queries: List of query names that were skipped during analysis

        Returns:
            Tuple of (saved_files, save_errors)
        """
        if plugin is None:
            raise ValueError('plugin parameter is required and cannot be None')

        saved_files = []
        save_errors = []

        logger.info(f'save_analysis_files called with {len(results) if results else 0} results')

        if not results:
            logger.warning('No results to save - returning empty lists')
            return saved_files, save_errors

        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        analysis_folder = os.path.join(output_dir, f'database_analysis_{timestamp}')
        logger.info(f'Creating analysis folder: {analysis_folder}')

        try:
            os.makedirs(analysis_folder, exist_ok=True)
            logger.info(f'Created folder at: {analysis_folder}')
        except OSError as e:
            logger.error(f'Failed to create analysis folder: {str(e)}')
            save_errors.append(f'Failed to create folder {analysis_folder}: {str(e)}')
            return saved_files, save_errors

        # Prepare metadata for MarkdownFormatter
        metadata = {
            'database': database,
            'source_db_type': source_db_type,
            'analysis_period': f'{pattern_analysis_days} days',
            'max_query_results': max_results,
            'performance_enabled': performance_enabled,
            'skipped_queries': skipped_queries or [],
        }

        # Use MarkdownFormatter to generate files
        try:
            formatter = MarkdownFormatter(results, metadata, analysis_folder, plugin=plugin)
            generated_files, generation_errors = formatter.generate_all_files()
            saved_files = generated_files

            # Convert error tuples to error strings
            if generation_errors:
                for query_name, error_msg in generation_errors:
                    save_errors.append(f'{query_name}: {error_msg}')

            logger.info(
                f'Successfully generated {len(saved_files)} Markdown files with {len(save_errors)} errors'
            )
        except Exception as e:
            logger.error(f'Failed to generate Markdown files: {str(e)}')
            save_errors.append(f'Failed to generate Markdown files: {str(e)}')

        return saved_files, save_errors

    @staticmethod
    def filter_pattern_data(
        data: List[Dict[str, Any]], pattern_analysis_days: int
    ) -> List[Dict[str, Any]]:
        """Filter pattern analysis data to exclude DDL statements and add RPS calculations.

        Args:
            data: List of query pattern dictionaries
            pattern_analysis_days: Number of days in analysis period

        Returns:
            Filtered list with calculated RPS added to each pattern
        """
        if not data:
            return data

        total_seconds = (pattern_analysis_days or DEFAULT_ANALYSIS_DAYS) * SECONDS_PER_DAY
        filtered_patterns = []

        for pattern in data:
            digest = pattern.get('DIGEST_TEXT', '')
            # Skip DDL statements
            if not any(digest.upper().startswith(prefix) for prefix in DDL_PREFIXES):
                pattern_with_rps = pattern.copy()
                count = pattern.get('COUNT_STAR', 0)
                pattern_with_rps['calculated_rps'] = (
                    round(count / total_seconds, 6) if total_seconds > 0 else 0
                )
                filtered_patterns.append(pattern_with_rps)

        return filtered_patterns
