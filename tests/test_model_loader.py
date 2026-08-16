"""TEST SUITE: tokocrypto_bot.ml.model_loader"""
import os, sys, pytest, tempfile, hashlib
from pathlib import Path
from unittest.mock import patch, MagicMock
from tokocrypto_bot.ml.model_loader import resolve_model_path, validate_model_hash, resolve_and_validate_model_path, get_platform_app_data_dir
class TestGetPlatformAppDataDir:
    def test_windows_app_data_dir(self):
        with patch.object(sys,'platform','win32'), patch.dict(os.environ,{'LOCALAPPDATA':'C:\\Users\\test\\AppData\\Local'}):
            r=get_platform_app_data_dir(); assert 'NVRA' in str(r) and 'models' in str(r)
    def test_macos_app_data_dir(self):
        with patch.object(sys,'platform','darwin'):
            r=get_platform_app_data_dir(); assert 'Library' in str(r) and 'NVRA' in str(r)
    def test_linux_app_data_dir(self):
        with patch.object(sys,'platform','linux'):
            r=get_platform_app_data_dir(); assert '.local' in str(r) and 'NVRA' in str(r)
class TestResolveModelPath:
    def test_priority_1_env_variable_override(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mf=Path(tmpdir)/"test_model.pkl"; mf.write_bytes(b"fake")
            with patch.dict(os.environ,{'NVRA_MODEL_PATH':str(mf)}):
                assert resolve_model_path()==mf
    def test_priority_1_env_variable_not_found(self):
        with patch.dict(os.environ,{'NVRA_MODEL_PATH':'/nonexistent/model.pkl'}):
            assert resolve_model_path() is None
    def test_priority_3_platform_app_data(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mf=Path(tmpdir)/"champion_model.pkl"; mf.write_bytes(b"fake")
            with patch.dict(os.environ,{},clear=True), patch('tokocrypto_bot.ml.model_loader.get_platform_app_data_dir', return_value=Path(tmpdir)):
                assert resolve_model_path()==mf
    def test_no_model_found(self):
        with patch.dict(os.environ,{},clear=True), patch('tokocrypto_bot.ml.model_loader.Path.is_file', return_value=False):
            assert resolve_model_path() is None
class TestValidateModelHash:
    def test_hash_validation_enabled_valid(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mf=Path(tmpdir)/"model.pkl"; data=b"fake model data"; mf.write_bytes(data)
            h=hashlib.sha256(data).hexdigest()
            with patch.dict(os.environ,{'NVRA_MODEL_SHA256':h}): assert validate_model_hash(mf) is True
    def test_hash_validation_enabled_invalid(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mf=Path(tmpdir)/"model.pkl"; mf.write_bytes(b"fake")
            with patch.dict(os.environ,{'NVRA_MODEL_SHA256':'0'*64}): assert validate_model_hash(mf) is False
    def test_hash_validation_disabled(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mf=Path(tmpdir)/"model.pkl"; mf.write_bytes(b"fake")
            with patch.dict(os.environ,{},clear=True): assert validate_model_hash(mf) is True
    def test_hash_validation_file_not_found(self):
        with patch.dict(os.environ,{'NVRA_MODEL_SHA256':'0'*64}): assert validate_model_hash(Path("/nonexistent.pkl")) is False
class TestResolveAndValidateModelPath:
    def test_valid_model_found_and_valid(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mf=Path(tmpdir)/"champion_model.pkl"; data=b"fake"; mf.write_bytes(data)
            h=hashlib.sha256(data).hexdigest()
            with patch.dict(os.environ,{'NVRA_MODEL_PATH':str(mf),'NVRA_MODEL_SHA256':h}):
                path,ok=resolve_and_validate_model_path(); assert path==mf and ok is True
    def test_model_found_hash_invalid(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mf=Path(tmpdir)/"champion_model.pkl"; mf.write_bytes(b"fake")
            with patch.dict(os.environ,{'NVRA_MODEL_PATH':str(mf),'NVRA_MODEL_SHA256':'0'*64}):
                path,ok=resolve_and_validate_model_path(); assert path==mf and ok is False
    def test_no_model_found(self):
        with patch.dict(os.environ,{},clear=True), patch('tokocrypto_bot.ml.model_loader.resolve_model_path', return_value=None):
            path,ok=resolve_and_validate_model_path(); assert path is None and ok is False
