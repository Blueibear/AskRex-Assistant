"""Tests for OS automation module."""

import tempfile
from pathlib import Path

import pytest

from rex.os_automation import (
    OSAutomationService,
    get_os_service,
    reset_os_service,
)


class TestOSAutomationService:
    """Tests for OSAutomationService class."""

    def test_service_init_with_defaults(self):
        """Test service initialization with defaults."""
        service = OSAutomationService()
        assert "ls" in service.allowed_commands
        assert "cat" in service.allowed_commands
        assert "echo" in service.allowed_commands

    def test_service_init_with_custom_commands(self):
        """Test service initialization with custom command list."""
        service = OSAutomationService(allowed_commands=["ls", "pwd"])
        assert service.allowed_commands == {"ls", "pwd"}

    def test_run_allowed_command(self):
        """Test running an allowed command."""
        service = OSAutomationService(allowed_commands=["echo"])
        result = service.run_command(["echo", "hello"])

        assert result.success is True
        assert result.returncode == 0
        assert "hello" in result.stdout

    def test_run_disallowed_command(self):
        """Test running a disallowed command raises PermissionError."""
        service = OSAutomationService(allowed_commands=["ls"])

        with pytest.raises(PermissionError):
            service.run_command(["rm", "-rf", "/"])

    def test_run_empty_command(self):
        """Test running empty command raises ValueError."""
        service = OSAutomationService()

        with pytest.raises(ValueError):
            service.run_command([])

    def test_copy_file(self):
        """Test copying a file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)

            # Create source file
            src = tmppath / "source.txt"
            src.write_text("test content")

            # Create service with temp data path
            service = OSAutomationService(data_path=str(tmppath))

            # Copy file
            dst = tmppath / "dest.txt"
            result = service.copy_file(str(src), str(dst))

            assert result["status"] == "success"
            assert dst.exists()
            assert dst.read_text() == "test content"

    def test_copy_nonexistent_file(self):
        """Test copying nonexistent file raises error."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            service = OSAutomationService(data_path=str(tmppath))

            with pytest.raises(RuntimeError):
                service.copy_file(str(tmppath / "nonexistent.txt"), str(tmppath / "dest.txt"))

    def test_move_file(self):
        """Test moving a file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)

            # Create source file
            src = tmppath / "source.txt"
            src.write_text("test content")

            # Create service
            service = OSAutomationService(data_path=str(tmppath))

            # Move file
            dst = tmppath / "dest.txt"
            result = service.move_file(str(src), str(dst))

            assert result["status"] == "success"
            assert not src.exists()
            assert dst.exists()
            assert dst.read_text() == "test content"

    def test_delete_file_with_backup(self):
        """Test deleting a file with backup (move to trash)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)

            # Create file to delete
            file_to_delete = tmppath / "delete_me.txt"
            file_to_delete.write_text("delete this")

            # Create service
            service = OSAutomationService(data_path=str(tmppath))

            # Delete with backup
            result = service.delete_file(str(file_to_delete), backup=True)

            assert result["status"] == "success"
            assert result["action"] == "moved_to_trash"
            assert not file_to_delete.exists()
            assert result["backup_path"] is not None

    def test_list_trash(self):
        """Test listing trash contents."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)

            # Create file and delete it
            file_to_delete = tmppath / "file.txt"
            file_to_delete.write_text("content")

            service = OSAutomationService(data_path=str(tmppath))
            service.delete_file(str(file_to_delete), backup=True)

            # List trash
            trash_files = service.list_trash()

            assert len(trash_files) > 0
            assert any("file" in f["name"] for f in trash_files)

    @pytest.mark.parametrize("filename", ["../outside.txt", "..\\outside.txt", "/outside.txt"])
    def test_restore_from_trash_rejects_traversal_filename(self, filename: str):
        """Restore identifiers cannot escape the managed trash directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            root = tmp_path / "data"
            service = OSAutomationService(data_path=str(root))
            outside = tmp_path / "outside.txt"
            outside.write_text("must remain untouched")

            with pytest.raises(ValueError, match="single file name"):
                service.restore_from_trash(filename, str(root / "restored.txt"))

            assert outside.read_text() == "must remain untouched"

    def test_run_find_rejects_command_execution_actions(self):
        service = OSAutomationService(allowed_commands=["find"])

        with pytest.raises(PermissionError, match="not allowed"):
            service.run_command(["find", ".", "-exec", "echo", "injected", ";"])

    def test_run_command_rejects_cwd_outside_data_path(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            service = OSAutomationService(allowed_commands=["echo"], data_path=str(root))

            with pytest.raises(ValueError, match="outside allowed directory"):
                service.run_command(["echo", "hello"], cwd=str(root.parent))

    def test_path_sanitization_outside_data(self):
        """Test that paths outside data/ directory are rejected."""
        service = OSAutomationService()

        with pytest.raises(ValueError):
            service._sanitize_path("/etc/passwd")

        with pytest.raises(ValueError):
            service._sanitize_path("../../../etc/passwd")


class TestOSServiceSingleton:
    """Tests for OS service singleton."""

    def test_get_os_service(self):
        """Test getting OS service singleton."""
        reset_os_service()
        service1 = get_os_service()
        service2 = get_os_service()
        assert service1 is service2

    def test_reset_os_service(self):
        """Test resetting OS service."""
        service1 = get_os_service()
        reset_os_service()
        service2 = get_os_service()
        assert service1 is not service2
