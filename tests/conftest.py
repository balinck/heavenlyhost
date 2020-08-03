import pytest
import os

from ..heavenly import Server, Game
from ..app import app
from ..notify import Notifier

@pytest.fixture(name="test_app")
@pytest.mark.asyncio
async def _test_app():
  await app.startup()
  yield app
  await app.shutdown()

@pytest.fixture(name="test_server")
def _test_server(tmpdir):
  server = Server(dom5_path=os.environ['DOM5_PATH'], data_path=tmpdir)
  return server

@pytest.fixture(name="test_game")
def _test_game():
  game = Game("Test_Game", port=2000, mapfile="silentseas.map")
  return game

@pytest.fixture(name="test_notifier")
def _test_notifier():
  @Notifier.register_notifier
  class TestNotifier(Notifier):
    name = "test"

    def notify(self, msg):
      self.last_msg = msg

    def _as_dict(self):
      return {"type": self.name, "attrs": {}}

    def echo(self)
      return self.last_msg

  return TestNotifier()
