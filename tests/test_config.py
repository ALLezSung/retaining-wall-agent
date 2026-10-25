"""Configuration behavior, project wiring and secret-safe failure messages."""
import tempfile
import unittest
from pathlib import Path

from src.config import AgentConfig, ConfigError, ModelConfig, ProjectConfig, StorageConfig, PROJECT_DIR
from src.design_agent import DesignAgent


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp_root = PROJECT_DIR / "tests/.runtime"
        self.temp_root.mkdir(parents=True, exist_ok=True)
        self.directory = tempfile.TemporaryDirectory(dir=self.temp_root)
        self.path = Path(self.directory.name) / "config.toml"

    def tearDown(self):
        self.assertTrue(Path(self.directory.name).resolve().is_relative_to(self.temp_root.resolve()))
        self.directory.cleanup()

    def write(self, text):
        self.path.write_text(text, encoding="utf-8")

    def test_toml_values_and_application_use_the_same_configuration(self):
        self.write('''[llm]
api_key = "offline-config-secret"
model = "test-model"
base_url = "https://example.invalid/v1/"
timeout_seconds = 12.5
max_tokens = 2048
[agent]
max_rounds = 3
[storage]
cases_database = "data/configured.sqlite3"
''')
        config = ProjectConfig.load(self.path, environ={})
        self.assertEqual(config.llm.model, "test-model")
        self.assertEqual(config.llm.base_url, "https://example.invalid/v1")
        self.assertEqual(config.llm.timeout_seconds, 12.5)
        self.assertEqual(config.llm.max_tokens, 2048)
        self.assertEqual(config.agent.max_rounds, 3)
        self.assertEqual(config.storage.cases_database, PROJECT_DIR / "data/configured.sqlite3")
        self.assertNotIn("offline-config-secret", repr(config))
        app = DesignAgent(config=config)
        self.assertEqual(app.loop.max_rounds, 3)
        self.assertEqual(app.loop.model.config, config.llm)
        self.assertEqual(app.wall.db_path, config.storage.cases_database)
        self.assertNotIn("offline-config-secret", str(app.context_snapshot()))

    def test_environment_overrides_toml_and_defaults(self):
        self.write('[llm]\napi_key="from-file"\nmodel="from-file"\nbase_url="https://file.invalid"\n')
        env = {"DEEPSEEK_API_KEY": "from-env", "DEEPSEEK_MODEL": "env-model",
               "DEEPSEEK_BASE_URL": "https://env.invalid/v1/", "DEEPSEEK_TIMEOUT_SECONDS": "2.5",
               "DEEPSEEK_MAX_TOKENS": "800", "AGENT_MAX_ROUNDS": "2", "CASES_DATABASE": str(self.path.with_suffix(".db"))}
        config = ProjectConfig.load(self.path, environ=env)
        self.assertEqual(config.llm, ModelConfig("from-env", "env-model", "https://env.invalid/v1", 2.5, 800))
        self.assertEqual(config.agent, AgentConfig(2))
        self.assertEqual(config.storage, StorageConfig(self.path.with_suffix(".db")))

    def test_missing_file_and_example_use_defaults(self):
        self.assertEqual(ProjectConfig.load(self.path, environ={}), ProjectConfig())
        # The example may recommend a larger token budget than the compatibility
        # default; its explicit settings still must parse and validate normally.
        example = ProjectConfig.load(PROJECT_DIR / "config.example.toml", environ={})
        self.assertGreaterEqual(example.llm.max_tokens, ProjectConfig().llm.max_tokens)
        self.assertEqual(example.llm.base_url, ProjectConfig().llm.base_url)
        self.assertEqual(example.storage, ProjectConfig().storage)

    def test_invalid_configuration_is_rejected_without_echoing_values(self):
        for text in [
            '[llm]\nmax_tokens = "sensitive-invalid-value"',
            '[llm]\nbase_url = "https://secret:password@example.invalid"',
            '[llm]\nbase_url = "https://example.invalid/chat/completions"',
            '[llm]\nbase_url = "https://example.invalid:bad"',
            '[llm]\ntimeout_seconds = nan',
            '[llm]\ntimeout_seconds = 0',
            '[llm]\nmodel = ""',
            '[llm]\nmax_tokens = true',
            '[agent]\nmax_rounds = 1.5',
            '[storage]\ncases_database = false',
            '[llm]\nmodle = "typo"',
            'unknown = "section"',
            '[llm\napi_key = "secret-value"',
        ]:
            with self.subTest(config=text):
                self.write(text)
                with self.assertRaises(ConfigError) as caught:
                    ProjectConfig.load(self.path, environ={})
                self.assertNotIn("sensitive-invalid-value", str(caught.exception))
                self.assertNotIn("password", str(caught.exception))
                self.assertNotIn("secret-value", str(caught.exception))

    def test_legacy_endpoint_environment_and_base_url_precedence(self):
        env = {"DEEPSEEK_ENDPOINT": "https://example.invalid/v1/chat/completions"}
        self.assertEqual(ProjectConfig.load(self.path, environ=env).llm.base_url, "https://example.invalid/v1")
        env["DEEPSEEK_BASE_URL"] = "https://new.invalid"
        self.assertEqual(ProjectConfig.load(self.path, environ=env).llm.base_url, "https://new.invalid")

    def test_generic_environment_and_reasoning_override_configuration(self):
        self.write('''[llm]
api_key="file"
[llm.reasoning_overrides."custom"]
low="minimal"
high="max"
[storage]
conversations_database="data/chats.sqlite3"
''')
        config = ProjectConfig.load(self.path, environ={"LLM_API_KEY": "generic", "DEEPSEEK_API_KEY": "legacy",
            "LLM_BASE_URL": "https://gateway.invalid/v1", "DEEPSEEK_ENDPOINT": "invalid-ignored",
            "LLM_REASONING_EFFORT": "high"})
        self.assertEqual(config.llm.api_key, "generic")
        self.assertEqual(config.llm.base_url, "https://gateway.invalid/v1")
        self.assertEqual(config.llm.reasoning_effort, "high")
        self.assertEqual(config.llm.reasoning_overrides["custom"]["high"], "max")
        self.assertEqual(config.storage.conversations_database, PROJECT_DIR / "data/chats.sqlite3")
        for invalid in ['[llm]\nreasoning_effort="bad"', '[llm]\nreasoning_overrides=1',
                        '[llm.reasoning_overrides."custom"]\nhigh=7']:
            self.write(invalid)
            with self.assertRaises(ConfigError):
                ProjectConfig.load(self.path, environ={})


if __name__ == "__main__":
    unittest.main()
