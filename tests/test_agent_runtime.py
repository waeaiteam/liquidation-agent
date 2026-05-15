import os
import tempfile
import unittest


class AgentRuntimeStateTests(unittest.TestCase):
    def test_tick_lifecycle_is_recorded_in_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            original = os.environ.get("LIQAGENT_DATA_DIR")
            os.environ["LIQAGENT_DATA_DIR"] = tmp
            try:
                import importlib
                import state as state_module

                importlib.reload(state_module)
                agent_state = state_module.AgentState()
                agent_state.record_tick_start()
                agent_state.record_tick_finish(200, {"ok": True})
                agent_state.set_next_tick_due(60)

                status = agent_state.status()
                self.assertEqual(status["tick_count"], 1)
                self.assertEqual(status["last_tick_status"], 200)
                self.assertIsNotNone(status["last_tick_started_at"])
                self.assertIsNotNone(status["last_tick_finished_at"])
                self.assertIsNotNone(status["next_tick_due_at"])
                self.assertEqual(status["worker_error_count"], 0)
            finally:
                if original is None:
                    os.environ.pop("LIQAGENT_DATA_DIR", None)
                else:
                    os.environ["LIQAGENT_DATA_DIR"] = original


if __name__ == "__main__":
    unittest.main()
