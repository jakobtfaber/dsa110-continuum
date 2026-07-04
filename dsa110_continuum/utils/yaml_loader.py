# Vendored from dsa110-contimg @ /data/dsa110-contimg/backend/src (H17), 2026-07-03,
# as part of the contimg-import-retirement migration (docs/rse/specs/plan-contimg-import-retirement.md).
"""Advanced YAML loader with environment variable expansion.

This module provides a sophisticated YAML loader that expands environment variables
in YAML files, supporting multiple syntaxes and handling nested structures.

This prevents bugs where literal strings like '${CONTIMG_STATE_DIR}' are used as
directory names instead of being expanded to actual paths.

Supported syntaxes:
- ${VAR} - Simple substitution (raises error if VAR not set)
- ${VAR:-default} - Substitution with default value
- ${VAR:+value} - Substitution with alternative value if VAR is set
- ${VAR:?error} - Substitution with error message if VAR not set

Examples:
    >>> from dsa110_continuum.utils.yaml_loader import load_yaml_with_env
    >>> config = load_yaml_with_env('config.yaml')
    
    # In YAML file:
    # paths:
    #   state_dir: ${CONTIMG_STATE_DIR:-/data/dsa110-contimg/state}
    #   output_dir: ${CONTIMG_OUTPUT_DIR}
    
    # Environment variables are automatically expanded before Path objects are created

Usage:
    This loader is used by:
    - UnifiedPipelineConfig.from_yaml()
    - PipelineSpec.from_yaml()
    - HealthConfig.from_yaml()
    - Dagster recipe loaders
    
    All critical YAML loaders use this to prevent literal ${VAR} strings from
    being used as directory or file names.
"""

import logging
import os
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


def expand_env_vars(value: str, context: dict[str, Any] | None = None) -> str:
    """Expand environment variables in a string value.
    
    Supports multiple syntaxes:
    - ${VAR} - Simple substitution
    - ${VAR:-default} - Use default if VAR not set
    - ${VAR:+value} - Use value if VAR is set, otherwise empty
    - ${VAR:?error} - Raise error if VAR not set
    
    Handles nested variables like ${VAR:-${DEFAULT_VAR}}.
    
    Parameters
    ----------
    value : str
        String potentially containing environment variable references
    context : dict, optional
        Additional context dictionary to check for variables (checked before os.environ)
        
    Returns
    -------
    str
        String with environment variables expanded
        
    Raises
    ------
    ValueError
        If a required variable (${VAR} or ${VAR:?error}) is not set
    """
    if not isinstance(value, str):
        return value

    cursor = 0
    result = []
    
    while cursor < len(value):
        # Find next ${
        start = value.find("${", cursor)
        if start == -1:
            result.append(value[cursor:])
            break
        
        # Append text before ${
        result.append(value[cursor:start])
        
        # Find matching }
        nesting = 0
        end = -1
        
        # Scan forward from after ${
        for i in range(start + 2, len(value)):
            if value[i] == "}" and nesting == 0:
                end = i
                break
            elif value[i] == "}" and nesting > 0:
                nesting -= 1
            elif value[i] == "{" and value[i-1] == "$": # Nested ${
                nesting += 1
        
        if end == -1:
            # No matching }, treat as literal text (or could raise error)
            # For robustness, we treat as literal
            logger.warning("Unbalanced braces in environment variable expansion: %s", value[start:])
            result.append(value[start:])
            break
            
        # Extract full expression inside braces: VAR:-DEFAULT
        expression = value[start+2:end]
        
        # Parse expression to find operator
        # We need to find the first ':', but ignore any inside nested braces in the default value part?
        # Actually, the variable name part cannot contain braces.
        # So we just scan for the first ':' or end of string.
        
        operator_idx = -1
        for i, char in enumerate(expression):
            if char == ":":
                operator_idx = i
                break
        
        if operator_idx != -1:
            var_name = expression[:operator_idx]
            remainder = expression[operator_idx:]
            
            operator = None
            operator_value = None
            
            if remainder.startswith(":-"):
                operator = "-"
                operator_value = remainder[2:]
            elif remainder.startswith(":+"):
                operator = "+"
                operator_value = remainder[2:]
            elif remainder.startswith(":?"):
                operator = "?"
                operator_value = remainder[2:]
            else:
                # Fallback for unknown operators or malformed syntax
                # Treat as part of variable name or literal? 
                # Standard shell doesn't allow ':' in names usually.
                # We'll treat it as unknown operator -> ignore or treat as literal
                logger.warning("Unknown operator in environment variable: %s", remainder)
                var_name = expression # Treat whole thing as var name? Unlikely to work.
                
        else:
            var_name = expression
            operator = None
            operator_value = None
            
        # Resolve variable
        var_value = None
        if context and var_name in context:
            var_value = context[var_name]
        elif var_name in os.environ:
            var_value = os.environ[var_name]
            
        # Apply operator logic
        final_value = ""
        if operator == '-':
            # If var is unset or empty, use default
            if not var_value:
                # Recursively expand the default value
                final_value = expand_env_vars(operator_value, context) if operator_value else ""
            else:
                final_value = var_value
        elif operator == '+':
            if var_value:
                final_value = expand_env_vars(operator_value, context) if operator_value else ""
            else:
                final_value = ""
        elif operator == '?':
            if not var_value:
                error_msg = expand_env_vars(operator_value, context) if operator_value else f"Environment variable {var_name} is required"
                raise ValueError(error_msg)
            final_value = var_value
        else:
            # Simple substitution
            if var_value is None:
                raise ValueError(
                    f"Environment variable {var_name} is not set and no default provided. "
                    f"Use ${{{var_name}:-default}} to provide a default value."
                )
            final_value = var_value
            
        result.append(str(final_value))
        cursor = end + 1
        
    return "".join(result)


def expand_env_recursive(data: Any, context: dict[str, Any] | None = None) -> Any:
    """Recursively expand environment variables in nested data structures.
    
    Handles:
    - Strings: Expands ${VAR} patterns
    - Dictionaries: Recursively processes values
    - Lists: Recursively processes items
    - Other types: Returns as-is
    
    Parameters
    ----------
    data : Any
        Data structure to process (dict, list, str, or other)
    context : dict, optional
        Additional context dictionary for variable lookup
        
    Returns
    -------
    Any
        Data structure with environment variables expanded
    """
    if isinstance(data, dict):
        return {key: expand_env_recursive(value, context) for key, value in data.items()}
    elif isinstance(data, list):
        return [expand_env_recursive(item, context) for item in data]
    elif isinstance(data, str):
        # Only expand if the string contains ${...} pattern
        if '${' in data and '}' in data:
            try:
                return expand_env_vars(data, context)
            except ValueError as e:
                logger.error(
                    "Failed to expand environment variable in value '%s': %s",
                    data[:100],  # Truncate long values
                    e
                )
                raise
        return data
    else:
        # For non-string, non-container types, return as-is
        return data


def load_yaml_with_env(
    path: str | Path,
    *,
    context: dict[str, Any] | None = None,
    expand_vars: bool = True,
    loader: type[yaml.SafeLoader] | None = None,
) -> dict[str, Any]:
    """Load YAML file with environment variable expansion.
    
    This is a drop-in replacement for yaml.safe_load() that automatically
    expands environment variables in the loaded data.
    
    Parameters
    ----------
    path : str or Path
        Path to YAML file
    context : dict, optional
        Additional context dictionary for variable lookup (checked before os.environ)
    expand_vars : bool, default=True
        Whether to expand environment variables. Set to False to disable expansion.
    loader : type, optional
        Custom YAML loader class. Defaults to yaml.SafeLoader.
        
    Returns
    -------
    dict
        Loaded YAML data with environment variables expanded
        
    Raises
    ------
    FileNotFoundError
        If the YAML file doesn't exist
    ValueError
        If a required environment variable is missing
    yaml.YAMLError
        If the YAML file is malformed
        
    Examples
    --------
    >>> # Simple usage
    >>> config = load_yaml_with_env('config.yaml')
    
    >>> # With additional context
    >>> context = {'CUSTOM_VAR': 'custom_value'}
    >>> config = load_yaml_with_env('config.yaml', context=context)
    
    >>> # Disable expansion (equivalent to yaml.safe_load)
    >>> config = load_yaml_with_env('config.yaml', expand_vars=False)
    """
    yaml_path = Path(path)
    if not yaml_path.exists():
        raise FileNotFoundError(f"YAML file not found: {path}")

    # Use provided loader or default to SafeLoader
    loader_class = loader or yaml.SafeLoader

    try:
        with open(yaml_path, encoding="utf-8") as f:
            data = yaml.load(f, Loader=loader_class)
    except yaml.YAMLError as e:
        logger.error("Failed to parse YAML file %s: %s", path, e)
        raise

    if data is None:
        data = {}

    # Expand environment variables if requested
    if expand_vars:
        try:
            data = expand_env_recursive(data, context)
        except ValueError as e:
            logger.error(
                "Failed to expand environment variables in YAML file %s: %s",
                path,
                e
            )
            raise

    return data


def safe_load_yaml_with_env(path: str | Path) -> dict[str, Any]:
    """Convenience function: safe YAML load with environment expansion.
    
    Equivalent to load_yaml_with_env(path, loader=yaml.SafeLoader).
    
    Parameters
    ----------
    path : str or Path
        Path to YAML file
        
    Returns
    -------
    dict
        Loaded YAML data with environment variables expanded
    """
    return load_yaml_with_env(path, loader=yaml.SafeLoader)
