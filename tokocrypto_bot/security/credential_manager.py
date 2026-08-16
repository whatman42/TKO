"""MODULE: tokocrypto_bot.security.credential_manager
DESCRIPTION: Windows DPAPI Encrypted Credential Manager for Tokocrypto and Gemini API Keys.
"""

import os
import sys
import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger("NVRA.CredentialManager")


@dataclass(frozen=True)
class DecryptedCredentials:
    tokocrypto_api_key: Optional[str]
    tokocrypto_api_secret: Optional[str]
    gemini_api_key: Optional[str]


class SecureCredentialStore:
    def __init__(self, app_name: str = "NVRA_Trading"):
        self.app_name = app_name
        self.cred_dir = os.path.expandvars(r"%LOCALAPPDATA%\\NVRA\\Trading\\credentials")
        os.makedirs(self.cred_dir, exist_ok=True)

    def save_credentials(self, tokocrypto_key: str, tokocrypto_secret: str, gemini_key: Optional[str] = None) -> bool:
        if sys.platform != "win32":
            logger.warning("Non-Windows platform detected. Windows DPAPI is unavailable.")
            return False
        try:
            import win32crypt
            enc_t_key = win32crypt.CryptProtectData(tokocrypto_key.encode('utf-8'), f"{self.app_name}_TKEY", None, None, None, 0)
            enc_t_secret = win32crypt.CryptProtectData(tokocrypto_secret.encode('utf-8'), f"{self.app_name}_TSECRET", None, None, None, 0)
            with open(os.path.join(self.cred_dir, "t_key.dat"), "wb") as f:
                f.write(enc_t_key)
            with open(os.path.join(self.cred_dir, "t_secret.dat"), "wb") as f:
                f.write(enc_t_secret)
            if gemini_key:
                enc_g_key = win32crypt.CryptProtectData(gemini_key.encode('utf-8'), f"{self.app_name}_GKEY", None, None, None, 0)
                with open(os.path.join(self.cred_dir, "g_key.dat"), "wb") as f:
                    f.write(enc_g_key)
            return True
        except Exception as e:
            logger.error(f"Failed to encrypt credentials via DPAPI: {e}")
            return False

    def load_credentials(self) -> DecryptedCredentials:
        t_key_file = os.path.join(self.cred_dir, "t_key.dat")
        t_secret_file = os.path.join(self.cred_dir, "t_secret.dat")
        g_key_file = os.path.join(self.cred_dir, "g_key.dat")
        if not (os.path.exists(t_key_file) and os.path.exists(t_secret_file)):
            return DecryptedCredentials(
                tokocrypto_api_key=os.environ.get("TOKOCRYPTO_API_KEY"),
                tokocrypto_api_secret=os.environ.get("TOKOCRYPTO_API_SECRET"),
                gemini_api_key=os.environ.get("GEMINI_API_KEY")
            )
        try:
            import win32crypt
            with open(t_key_file, "rb") as f:
                _, t_key = win32crypt.CryptUnprotectData(f.read(), None, None, None, 0)
            with open(t_secret_file, "rb") as f:
                _, t_secret = win32crypt.CryptUnprotectData(f.read(), None, None, None, 0)
            g_key_str = None
            if os.path.exists(g_key_file):
                with open(g_key_file, "rb") as f:
                    _, g_key = win32crypt.CryptUnprotectData(f.read(), None, None, None, 0)
                    g_key_str = g_key.decode('utf-8')
            return DecryptedCredentials(
                tokocrypto_api_key=t_key.decode('utf-8'),
                tokocrypto_api_secret=t_secret.decode('utf-8'),
                gemini_api_key=g_key_str
            )
        except Exception as e:
            logger.error(f"Error decrypting DPAPI credentials: {e}")
            return DecryptedCredentials(None, None, None)

    def load_api_credentials(self):
        creds = self.load_credentials()
        return (creds.tokocrypto_api_key, creds.tokocrypto_api_secret)
