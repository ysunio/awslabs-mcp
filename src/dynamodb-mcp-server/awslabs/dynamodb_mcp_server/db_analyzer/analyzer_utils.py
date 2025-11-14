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

"""Utility functions for source database analyzer."""

import os
from awslabs.dynamodb_mcp_server.common import validate_path_within_directory
from awslabs.dynamodb_mcp_server.database_analyzers import DatabaseAnalyzer
from loguru import logger


def resolve_and_validate_path(file_path: str, base_dir: str, path_type: str) -> str:
    """Resolve and validate file path within base directory."""
    if not os.path.isabs(file_path):
        resolved = os.path.join(base_dir, file_path.lstrip('./'))
    else:
        resolved = file_path
    return validate_path_within_directory(resolved, base_dir, path_type)


def generate_query_file(
    plugin,
    database_name: str,
    max_results: int,
    query_output_file: str,
    output_dir: str,
    source_db_type: str,
) -> str:
    """Generate SQL query file for self-service mode."""
    if not database_name:
        return 'database_name is required for self-service mode to generate queries.'

    resolved_query_file = resolve_and_validate_path(
        query_output_file, output_dir, 'query output file'
    )

    query_dir = os.path.dirname(resolved_query_file)
    if query_dir and not os.path.exists(query_dir):
        os.makedirs(query_dir, exist_ok=True)

    output_file = plugin.write_queries_to_file(database_name, max_results, resolved_query_file)

    return f"""SQL queries have been written to: {output_file}

Next Steps:
1. Run these queries against your {source_db_type} database
2. Save the results to a text file (pipe-separated format)
3. Call this tool again with:
   - execution_mode='self_service'
   - result_input_file='<path_to_your_results_file>'
   - Same database_name and output_dir

Example commands:
- MySQL: mysql -u user -p -D {database_name} --table < {output_file} > results.txt
- PostgreSQL: psql -d {database_name} -f {output_file} > results.txt
- SQL Server: sqlcmd -d {database_name} -i {output_file} -o results.txt

IMPORTANT for MySQL: The --table flag is required to produce pipe-separated output that can be parsed correctly.

After running queries, provide the results file path to continue analysis."""


def parse_results_and_generate_analysis(
    plugin,
    result_input_file: str,
    output_dir: str,
    database_name: str,
    pattern_analysis_days: int,
    max_results: int,
    source_db_type: str,
) -> str:
    """Parse query results and generate analysis files."""
    resolved_result_file = validate_path_within_directory(
        result_input_file, output_dir, 'result input file'
    )
    if not os.path.exists(resolved_result_file):
        raise FileNotFoundError(f'Result file not found: {resolved_result_file}')

    logger.info(f'Parsing query results from: {resolved_result_file}')
    results = plugin.parse_results_from_file(resolved_result_file)

    if not results:
        return f'No query results found in file: {resolved_result_file}. Please check the file format.'

    saved_files, save_errors = DatabaseAnalyzer.save_analysis_files(
        results,
        source_db_type,
        database_name or 'unknown',
        pattern_analysis_days or 30,
        max_results,
        output_dir,
        plugin,
        performance_enabled=True,
        skipped_queries=[],
    )

    return build_analysis_report(
        saved_files, save_errors, database_name, result_input_file, is_self_service=True
    )


async def execute_managed_analysis(plugin, connection_params: dict, source_db_type: str) -> str:
    """Execute managed mode analysis via AWS RDS Data API."""
    analysis_result = await plugin.execute_managed_mode(connection_params)

    saved_files, save_errors = DatabaseAnalyzer.save_analysis_files(
        analysis_result['results'],
        source_db_type,
        connection_params.get('database'),
        connection_params.get('pattern_analysis_days'),
        connection_params.get('max_results'),
        connection_params.get('output_dir'),
        plugin,
        analysis_result.get('performance_enabled', True),
        analysis_result.get('skipped_queries', []),
    )

    if analysis_result['results']:
        return build_analysis_report(
            saved_files,
            save_errors,
            connection_params.get('database'),
            None,
            is_self_service=False,
            analysis_period=connection_params.get('pattern_analysis_days'),
        )
    else:
        return build_failure_report(analysis_result['errors'])


def build_analysis_report(
    saved_files: list,
    save_errors: list,
    database_name: str,
    source_file: str = None,
    is_self_service: bool = False,
    analysis_period: int = None,
) -> str:
    """Build analysis completion report."""
    mode = 'Self-Service Mode' if is_self_service else 'Managed Mode'
    report = [f'Database Analysis Complete ({mode})', '']

    summary = ['Summary:', f'- Database: {database_name or "unknown"}']
    if source_file:
        summary.append(f'- Source: {source_file}')
    if analysis_period:
        summary.append(f'- Analysis Period: {analysis_period} days')
    summary.extend(
        ['**CRITICAL: Read ALL Analysis Files**', '', 'Follow these steps IN ORDER:', '']
    )
    report.extend(summary)

    workflow = [
        '1. Read manifest.md from the timestamped analysis directory',
        '   - Lists all generated analysis files by category',
        '',
        '2. Read EVERY file listed in the manifest',
        '   - Each file contains critical information for data modeling',
        '',
        '3. After reading all files, use dynamodb_data_modeling tool',
        '   - Extract entities and relationships from schema files',
        '   - Identify access patterns from performance files',
        '   - Document findings in dynamodb_requirement.md',
    ]
    report.extend(workflow)

    if saved_files:
        report.extend(['', 'Generated Analysis Files (Read All):'])
        report.extend(f'- {f}' for f in saved_files)

    if save_errors:
        report.extend(['', 'File Save Errors:'])
        report.extend(f'- {e}' for e in save_errors)

    return '\n'.join(report)


def build_failure_report(errors: list) -> str:
    """Build failure report when all queries fail."""
    return f'Database Analysis Failed\n\nAll {len(errors)} queries failed:\n' + '\n'.join(
        f'{i}. {error}' for i, error in enumerate(errors, 1)
    )
