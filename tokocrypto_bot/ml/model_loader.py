"""
MODULE: tokocrypto_bot.ml.model_loader
DESCRIPTION: Cross-platform ML model resolution with integrity validation.

Model Resolution Priority:
1. NVRA_MODEL_PATH environment variable (explicit override)
2. ./models/champion_model.pkl (repository-relative)
3. Platform-specific app data directory:
   - Windows: %LOCALAPPDATA%/NVRA/Trading/models/champion_model.pkl
   - Linux: ~/.local/share/NVRA/Trading/models/champion_model.pkl
   - macOS: ~/Library/Application Support/NVRA/Trading/models/champion_model.pkl

Integrity Validation:
- Optional NVRA_MODEL_SHA256 environment variable
- Validates model file hash if provided
- Fails closed to NO_TRADE on hash mismatch
"""

import os
import sys
import hashlib
import logging
from pathlib import Path
from typing import Optional, Tuple

logger = logging.getLogger("NVRA.ModelLoader")

MODEL_FILENAME = "champion_model.pkl"


def get_platform_app_data_dir() -> Path:
    """
    Return platform-specific application data directory.
    
    Returns:
        Path: Platform-specific models directory
    """
    if sys.platform == "win32":
        # Windows: %LOCALAPPDATA%/NVRA/Trading/models
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return base / "NVRA" / "Trading" / "models"
    elif sys.platform == "darwin":
        # macOS: ~/Library/Application Support/NVRA/Trading/models
        return Path.home() / "Library" / "Application Support" / "NVRA" / "Trading" / "models"
    else:
        # Linux: ~/.local/share/NVRA/Trading/models
        return Path.home() / ".local" / "share" / "NVRA" / "Trading" / "models"


def resolve_model_path() -> Optional[Path]:
    """
    Resolve model path following priority order.
    
    Priority:
    1. NVRA_MODEL_PATH environment variable
    2. ./models/champion_model.pkl (repository-relative)
    3. Platform-specific app data directory
    
    Returns:
        Path: Resolved model path if file exists, None otherwise
    """
    # Priority 1: Explicit environment variable override
    env_model_path = os.environ.get("NVRA_MODEL_PATH")
    if env_model_path:
        model_path = Path(env_model_path)
        if model_path.is_file():
            logger.info(f"Model resolved from NVRA_MODEL_PATH: {model_path}")
            return model_path
        else:
            logger.warning(f"NVRA_MODEL_PATH specified but file not found: {model_path}")
            return None
    
    # Priority 2: Repository-relative path (for development/testing)
    repo_model_path = Path(__file__).parent.parent.parent / "models" / MODEL_FILENAME
    if repo_model_path.is_file():
        logger.info(f"Model resolved from repository-relative path: {repo_model_path}")
        return repo_model_path
    
    # Priority 3: Platform-specific app data directory
    app_model_path = get_platform_app_data_dir() / MODEL_FILENAME
    if app_model_path.is_file():
        logger.info(f"Model resolved from platform app data directory: {app_model_path}")
        return app_model_path
    
    # No model found
    logger.warning(
        f"No model found in any resolution location. Checked:\n"
        f"  1. NVRA_MODEL_PATH={env_model_path}\n"
        f"  2. Repository-relative: {repo_model_path}\n"
        f"  3. Platform app data: {app_model_path}"
    )
    return None


def validate_model_hash(model_path: Path) -> bool:
    """
    Validate model file integrity using SHA-256.
    
    Only performs validation if NVRA_MODEL_SHA256 environment variable is set.
    
    Args:
        model_path: Path to model file
    
    Returns:
        bool: True if hash valid or not configured, False if hash mismatch
    """
    expected_hash = os.environ.get("NVRA_MODEL_SHA256")
    if not expected_hash:
        # Hash validation not configured; skip
        return True
    
    if not model_path.is_file():
        logger.error(f"Model file not found for hash validation: {model_path}")
        return False
    
    try:
        sha256_hash = hashlib.sha256()
        with open(model_path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                sha256_hash.update(chunk)
        
        actual_hash = sha256_hash.hexdigest()
        if actual_hash.lower() != expected_hash.lower():
            logger.critical(
                f"Model hash mismatch for {model_path}\n"
                f"  Expected: {expected_hash}\n"
                f"  Actual:   {actual_hash}"
            )
            return False
        
        logger.info(f"Model hash validation passed: {model_path}")
        return True
    except Exception as e:
        logger.error(f"Error computing model hash: {e}")
        return False


def resolve_and_validate_model_path() -> Tuple[Optional[Path], bool]:
    """
    Resolve and validate model path with integrity checks.
    
    Returns:
        Tuple[Optional[Path], bool]: (model_path, is_valid)
            - model_path: Path to model if valid, None otherwise
            - is_valid: True if model path valid and hash (if configured) valid
    """
    model_path = resolve_model_path()
    if model_path is None:
        return None, False
    
    is_valid = validate_model_hash(model_path)
    return model_path, is_valid
