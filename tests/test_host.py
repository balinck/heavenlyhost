import pytest
import asyncio
import os

def test_startup_shutdown(test_host, test_game_settings):
  game = test_host.create_new_game("Test_Game", **test_game_settings)
  test_host.startup()
  assert game.process.poll() is None
  test_host.shutdown()
  assert game.process is None
  test_host.startup()
  test_host.shutdown()
  test_host.startup()
  assert game.process.poll() is None

def test_serialization(test_host, test_game_settings):
  game = test_host.create_new_game("Test_Game", **test_game_settings)
  test_host.startup()
  test_host.shutdown()
  test_host.dump_games()
  test_host.games = []
  test_host.restore_games()
  decoded_game = test_host.games[0]
  assert vars(decoded_game) == vars(game)

def test_filter_games(test_host, test_game_settings):
  game = test_host.create_new_game("Test_Game", **test_game_settings)
  assert test_host.find_game_by_name("Test_Game") is game
  assert test_host.find_game_by_name("Nonexistent_Game") is None
  game.settings.update(port=9999)
  assert list(test_host.filter_games_by(port=9999))[0] is game

def test_postexec(test_host, test_game_settings, test_notifier):
  game = test_host.create_new_game("Test_Game", **test_game_settings)
  game.notifiers = [test_notifier]
  test_host.init_pipe()
  test_msg = "this is a test notification"
  game.on_postexec = lambda: game.notifiers[0].notify(test_msg)
  pipe_path = test_host.root / ".pipe"
  os.system("echo \"postexec {}\" > {}".format(game.name, str(pipe_path)))
  test_host.read_from_pipe()
  assert test_notifier.echo() == test_msg

def test_get_free_port(test_host, test_game_settings):
  assert test_host.get_free_port() == test_host.port_range[0]
  test_game_settings.update(dict(port=test_host.port_range[0]))
  test_host.create_new_game("Test_Game", **test_game_settings)
  assert test_host.get_free_port() == test_host.port_range[0]+1

  