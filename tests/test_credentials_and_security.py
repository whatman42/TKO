"""
MODULE: tests.test_credentials_and_security
DESCRIPTION: Security Test Suite verifying DPAPI Encryption and Gemini Non-Blocking Isolation.
"""

import sys
import pytest
import tempfile

from tokocrypto_bot.security.credential_manager import SecureCredentialStore
from tokocrypto_bot.quant.gemini_supervisor import GeminiGodAdministrator
from tokocrypto_bot.quant.performance_evaluator import PerformanceReport


def test_dpapi_encryption_and_decryption_flow():
    if sys.platform != "win32":
        pytest.skip("Windows DPAPI test skipped on non-Windows OS")

    store = SecureCredentialStore(app_name="NVRA_TEST")
    success = store.save_credentials("MOCK_T_KEY_123", "MOCK_T_SECRET_456", "MOCK_G_KEY_789")
    assert success is True

    creds = store.load_credentials()
    assert creds.tokocrypto_api_key == "MOCK_T_KEY_123"
    assert creds.tokocrypto_api_secret == "MOCK_T_SECRET_456"
    assert creds.gemini_api_key == "MOCK_G_KEY_789"


def test_gemini_failure_does_not_halt_trading():
    # Pass invalid/disabled Gemini Administrator
    admin = GeminiGodAdministrator(enabled=False)
    dummy_report = PerformanceReport(10, 0.6, 1.5, 2.0, 1.8, 1.5, 0.02, 1.8, 2.1, 1.0, 0.5, 1.0)
    
    # Must return None without throwing exception
    proposal = admin.evaluate_periodically_async(dummy_report)
    assert proposal is None
