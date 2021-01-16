import pytest
import os
from copy import copy
import asyncio
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))
from heavenly.host import Host, Game, _GAME_DEFAULTS
from app import app
from heavenly.notify import Notifier

@pytest.fixture(name="test_host")
def _test_host(tmpdir):
  host = Host(tmpdir)
  return host

@pytest.fixture(name="test_app")
@pytest.mark.asyncio
async def _test_app(test_host):
  test_host.startup()
  app.config.update({"host_instance": test_host})
  test_host.init_pipe()
  loop = asyncio.get_event_loop()
  loop.create_task(test_host.listen_pipe())
  loop.create_task(test_host.ping())
  yield app
  await app.shutdown()

@pytest.fixture(name="test_game_settings")
def _test_game_settings():
  settings = copy(_GAME_DEFAULTS)
  settings["port"] = 2000
  settings["mapfile"] = "silentseas.map"
  return settings

@pytest.fixture(name="test_notifier")
def _test_notifier():
  @Notifier.register_notifier
  class TestNotifier(Notifier):
    name = "test"

    def notify(self, msg):
      self.last_msg = msg

    def _as_dict(self):
      return {"type": self.name, "attrs": {}}

    def echo(self):
      return self.last_msg

  return TestNotifier()
