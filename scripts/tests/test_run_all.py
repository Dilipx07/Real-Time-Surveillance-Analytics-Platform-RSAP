import importlib.util
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RUNNER_PATH = ROOT / "scripts" / "run-all.py"


def load_runner():
    spec = importlib.util.spec_from_file_location("run_all", RUNNER_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class RunAllTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runner = load_runner()

    def test_repo_root_detection(self):
        self.assertEqual(self.runner.find_repo_root(ROOT / "scripts"), ROOT)

    def test_env_generation_uses_local_non_placeholder_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env_path = self.runner.ensure_env(root)
            contents = env_path.read_text(encoding="utf-8")
        self.assertIn("POSTGRES_PASSWORD=postgres123", contents)
        self.assertIn("ADMIN_PASSWORD=admin123", contents)
        self.assertIn("MINIO_SECRET_KEY=miniosecret123", contents)
        self.assertNotIn("minioadmin", contents)
        self.assertNotIn("<required", contents)

    def test_compose_command_uses_root_env_file(self):
        command = self.runner.compose_command(ROOT, "ps")
        self.assertIn("--env-file", command)
        self.assertIn(str(ROOT / ".env"), command)
        self.assertIn(str(ROOT / "infra" / "docker-compose.yml"), command)

    def test_desktop_frontend_url_is_loopback_port_1420(self):
        self.assertEqual(self.runner.DESKTOP_FRONTEND_URL, "http://127.0.0.1:1420")


if __name__ == "__main__":
    unittest.main()
