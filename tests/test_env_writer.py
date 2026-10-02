"""Tests for env_writer module."""

import pytest

from utils.env_schema import parse_env_example
from utils.env_writer import (
    create_backup,
    get_backup_files,
    get_extra_keys,
    read_current_env,
    restore_from_backup,
    write_env_from_template,
)


@pytest.fixture
def sample_env_example(tmp_path):
    """Create a sample .env.example file."""
    content = """# ================================
# Test Configuration
# ================================

# ================================
# Core Settings
# ================================

# Active user
REX_ACTIVE_USER=default

# Log level
REX_LOG_LEVEL=INFO

# Debug mode
REX_DEBUG=false

# API key
OPENAI_API_KEY=
"""
    env_example = tmp_path / ".env.example"
    env_example.write_text(content)
    return env_example


@pytest.fixture
def sample_env(tmp_path):
    """Create a sample .env file."""
    content = """REX_ACTIVE_USER=john
REX_LOG_LEVEL=DEBUG
REX_DEBUG=true
OPENAI_API_KEY=sk-test123
CUSTOM_KEY=custom_value
"""
    env_file = tmp_path / ".env"
    env_file.write_text(content)
    return env_file


def test_read_current_env(sample_env):
    """Test reading current .env file."""
    env_vars = read_current_env(sample_env)

    assert env_vars["REX_ACTIVE_USER"] == "john"
    assert env_vars["REX_LOG_LEVEL"] == "DEBUG"
    assert env_vars["REX_DEBUG"] == "true"
    assert env_vars["OPENAI_API_KEY"] == "sk-test123"  # pragma: allowlist secret -- fixture
    assert env_vars["CUSTOM_KEY"] == "custom_value"


def test_read_nonexistent_env(tmp_path):
    """Test reading non-existent .env file returns empty dict."""
    nonexistent = tmp_path / ".env"
    env_vars = read_current_env(nonexistent)

    assert env_vars == {}


def test_create_backup(sample_env, tmp_path):
    """Test creating a backup of .env file."""
    backup_dir = tmp_path / "backups"

    backup_path = create_backup(sample_env, backup_dir)

    assert backup_path is not None
    assert backup_path.exists()
    assert backup_path.parent == backup_dir
    assert ".env.backup." in backup_path.name

    # Verify backup content matches original
    assert backup_path.read_text() == sample_env.read_text()


def test_create_backup_nonexistent_file(tmp_path):
    """Test creating backup of non-existent file returns None."""
    nonexistent = tmp_path / ".env"
    backup_dir = tmp_path / "backups"

    backup_path = create_backup(nonexistent, backup_dir)

    assert backup_path is None


def test_write_env_from_template(sample_env_example, tmp_path):
    """Test writing .env from template with updated values."""
    env_path = tmp_path / ".env"

    values = {
        "REX_ACTIVE_USER": "alice",
        "REX_LOG_LEVEL": "WARNING",
        "REX_DEBUG": "true",
        "OPENAI_API_KEY": "sk-new-key",  # pragma: allowlist secret -- fixture
    }

    write_env_from_template(env_path, sample_env_example, values, create_backup=False)

    assert env_path.exists()

    # Verify content
    content = env_path.read_text()
    assert "REX_ACTIVE_USER=alice" in content
    assert "REX_LOG_LEVEL=WARNING" in content
    assert "REX_DEBUG=true" in content
    assert "OPENAI_API_KEY=sk-new-key" in content

    # Verify structure preserved (sections and comments)
    assert "# ================================" in content
    assert "# Core Settings" in content
    assert "# Active user" in content


def test_write_env_with_custom_overrides(sample_env_example, tmp_path):
    """Test writing .env with custom overrides."""
    env_path = tmp_path / ".env"

    values = {
        "REX_ACTIVE_USER": "bob",
        "REX_LOG_LEVEL": "INFO",
    }

    custom_overrides = {
        "CUSTOM_KEY1": "value1",
        "CUSTOM_KEY2": "value2",
    }

    write_env_from_template(
        env_path, sample_env_example, values, custom_overrides=custom_overrides, create_backup=False
    )

    content = env_path.read_text()

    # Verify custom overrides section
    assert "# Custom Overrides" in content
    assert "CUSTOM_KEY1=value1" in content
    assert "CUSTOM_KEY2=value2" in content


def test_write_env_creates_backup(sample_env, sample_env_example, tmp_path):
    """Test that writing .env creates a backup of existing file."""
    tmp_path / "backups"

    values = {
        "REX_ACTIVE_USER": "new_user",
    }

    backup_path = write_env_from_template(
        sample_env, sample_env_example, values, create_backup=True
    )

    # Should have created a backup
    assert backup_path is not None
    assert backup_path.exists()
    assert backup_path != sample_env


def test_restore_from_backup(sample_env, tmp_path):
    """Test restoring .env from backup."""
    backup_dir = tmp_path / "backups"

    # Create backup
    backup_path = create_backup(sample_env, backup_dir)
    original_content = sample_env.read_text()

    # Modify original
    sample_env.write_text("MODIFIED=true\n")

    # Restore
    restore_from_backup(backup_path, sample_env)

    # Verify restored
    assert sample_env.read_text() == original_content


def test_restore_nonexistent_backup(tmp_path):
    """Test restoring from non-existent backup raises error."""
    nonexistent = tmp_path / "backups" / ".env.backup.nonexistent"
    env_path = tmp_path / ".env"

    with pytest.raises(FileNotFoundError):
        restore_from_backup(nonexistent, env_path)


def test_get_backup_files(tmp_path):
    """Test getting list of backup files."""
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()

    # Create some backup files
    (backup_dir / ".env.backup.20240101_120000").write_text("backup1")
    (backup_dir / ".env.backup.20240102_120000").write_text("backup2")
    (backup_dir / ".env.backup.20240103_120000").write_text("backup3")

    backups = get_backup_files(backup_dir)

    assert len(backups) == 3
    # Should be sorted newest first
    assert "20240103" in backups[0].name
    assert "20240101" in backups[2].name


def test_get_backup_files_empty_dir(tmp_path):
    """Test getting backup files from empty directory."""
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()

    backups = get_backup_files(backup_dir)

    assert backups == []


def test_get_backup_files_nonexistent_dir(tmp_path):
    """Test getting backup files from non-existent directory."""
    nonexistent = tmp_path / "nonexistent"

    backups = get_backup_files(nonexistent)

    assert backups == []


def test_get_extra_keys(sample_env, sample_env_example, tmp_path):
    """Test finding keys in .env that are not in .env.example."""
    schema = parse_env_example(sample_env_example)

    extra = get_extra_keys(sample_env, schema)

    # CUSTOM_KEY is in .env but not in .env.example
    assert "CUSTOM_KEY" in extra
    assert extra["CUSTOM_KEY"] == "custom_value"

    # Standard keys should not be in extra
    assert "REX_ACTIVE_USER" not in extra
    assert "REX_LOG_LEVEL" not in extra


def test_get_extra_keys_no_env(tmp_path, sample_env_example):
    """Test getting extra keys when .env doesn't exist."""
    nonexistent = tmp_path / ".env"
    schema = parse_env_example(sample_env_example)

    extra = get_extra_keys(nonexistent, schema)

    assert extra == {}
