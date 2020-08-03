import pytest
import asyncio
import os

def test_startup_shutdown(test_server, test_game):
  test_server.add_game(test_game)
  test_server.startup()
  assert test_game.process.poll() is None
  test_server.shutdown()
  assert test_game.process is None
  test_server.startup()
  test_server.shutdown()
  test_server.startup()
  assert test_game.process.poll() is None

def test_encode_decode(test_server, test_game):
  test_server.add_game(test_game)
  test_server.startup()
  test_server.shutdown()
  test_server.games = []
  test_server._load_json_data()
  decoded_game = test_server.games[0]
  assert vars(decoded_game) == vars(test_game)

def test_postexec(test_server, test_game, test_notifier):
  test_game.notifier = test_notifier
  test_server.add_game(test_game)
  test_server.init_pipe()
  test_msg = "this is a test notification"
  test_game.on_postexec = lambda: test_game.notifier.notify(test_msg)
  pipe_path = test_server.data_path / ".pipe"
  os.system("echo \"postexec Test_Game\" > {}".format(str(pipe_path)))
  test_server.read_from_pipe()
  assert test_notifier.echo() == test_msg
  