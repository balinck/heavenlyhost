import os
import json
import io
import asyncio
from copy import copy
from pathlib import Path
from collections import namedtuple
import re

from .notify import Notifier
from .maps import Dom5Map
from .mods import Dom5Mod
from .dom5 import GAME_DEFAULTS, TCPServer, list_nations, STATUS_TURN_GEN, STATUS_ACTIVE, STATUS_INIT, STATUS_SETUP, STATUS_MAPGEN

class Host:

  def __init__(
      self, 
      root_path,
      dom5_path = Path(os.environ.get("DOM5_PATH")).resolve()
      ):
    self.games = []
    self.maps = []
    self.mods = []

    self.status = {}

    self.port_range = (1024, 65535)

    self.root = Path(root_path).resolve()
    self.dom5_path = dom5_path
    self.conf_path = self.root / "dominions5"
    self.savedgame_path = self.root / "savedgames"
    self.map_path = self.root / "maps"
    self.mod_path = self.root / "mods"

    os.environ["DOM5_CONF"] = str(self.conf_path)
    os.environ["DOM5_SAVE"] = str(self.savedgame_path)
    os.environ["DOM5_LOCALMAPS"] = str(self.map_path)
    os.environ["DOM5_MODS"] = str(self.mod_path)

    for path in (self.root, self.conf_path, self.savedgame_path, 
                 self.map_path, self.mod_path):
      path.mkdir(exist_ok=True)

    for file_path in self.map_path.iterdir():
      if file_path.suffix == ".map":
        self.maps.append(Dom5Map(file_path))

    for file_path in self.mod_path.iterdir():
      if file_path.suffix == ".dm":
        self.mods.append(Dom5Mod(file_path))

  def get_free_port(self):
    lower, upper = self.port_range
    for n in range(lower, upper):
      if not [*self.filter_games_by(finished = False, port = n)]:
        return n
    return None

  def create_new_game(self, name, notifiers = None, **game_settings):
    new_game_path = self.savedgame_path / name
    settings_with_defaults = copy(GAME_DEFAULTS)
    settings_with_defaults.update(game_settings)
    game = Game(
      name = name,
      notifiers = notifiers,
      game_settings = settings_with_defaults,
      path = new_game_path,
      dom5_path = self.dom5_path,
      host = self
    )
    self.games.append(game)
    return game

  def filter_games_by(self, **kwargs):
    def filter_func(g):
      matches = []
      for attr, value in kwargs.items():
        if hasattr(g, attr):
          matches.append(g.__dict__[attr] == value)
        elif attr in g.settings:
          matches.append(g.settings[attr] == value)
      return all(matches)
    return filter(filter_func, self.games)

  def find_game_by_name(self, name):
    match = list(self.filter_games_by(name=name))
    if match:
      return match[0]
    else:
      return None

  def deserialize_game(self, path_to_game_files):
    json_path = path_to_game_files / "host_data.json"
    if json_path.exists():
      with open(json_path, "r") as file:
          dict_ = json.load(file)
      game = Game.from_dict(
        dict_, 
        path = path_to_game_files,
        dom5_path = self.dom5_path,
        host = self
      )
      self.games.append(game)
    else:
      print("no json data found in {}".format(json_path.parent))

  def serialize_game(self, game):
    dict_ = game.as_dict()
    json_path = game.path / "host_data.json"
    with open(json_path, "w+") as file:
      json.dump(dict_, file, indent = 2)

  def restore_games(self):
    for dirname, _, _ in os.walk(self.savedgame_path):
      save_path = Path(dirname)
      if save_path.samefile(self.savedgame_path):
        pass
      else:
        self.deserialize_game(save_path)

  def dump_games(self):
    for game in self.games:
      self.serialize_game(game)

  async def startup(self):
    self.nations = await list_nations()
    for game in self.games:
      if not game.finished:
        asyncio.create_task(game.run_until_cancelled())

  def shutdown(self):
    self.dump_games()
    for game in self.games: game.shutdown()

class Game:

  metadata_format = set([
    "name",
    "turn",
    "finished",
    "notifiers",
    "players",
    ]
  )

  def _default_triggers(self):
    
    @self.when_status_change("state")
    def notify_on_turn_advance(prev, new):
      if prev == STATUS_TURN_GEN and new == STATUS_ACTIVE:
        self.turn += 1
        self.notify("{} has advanced to turn {}".format(self.name, self.turn))

    @self.when_status_change("state")
    def request_player_list_on_start(prev, new):
      if prev == STATUS_SETUP and new == STATUS_ACTIVE:
        loop = asyncio.get_running_loop()
        loop.create_task(self.process.request_player_list())

    @self.when_status_change("state")
    def increment_turn_on_start(prev, new):
      if prev == STATUS_SETUP and new == STATUS_ACTIVE:
        self.turn += 1

    @self.when_status_change("players")
    def init_player_roster(prev, new):
      if not prev and new:
        roster = []
        era = self.settings["era"]
        nations = copy(self.host.nations[era])
        for mod in self.mods: nations.update(mod.nations)

        for nid, who_played in zip(new, self.who_played):
          if nid in nations: name = nations[nid]
          else: name = f"{who_played[0]} {nid}"
          roster.append(dict(
            name = name,
            shortname = who_played[0], 
            number = nid, 
            eliminated = False
            )
          )
        print(roster)
        self.players = roster

    @self.when_status_change("who_played")
    def check_for_eliminations(prev, new):
      if not prev or len(new) < len(prev):
        for player in self.players:
          if player["eliminated"]: pass
          elif not any(wp[0] == player["shortname"] for wp in new):
            player["eliminated"] = True

  def __init__(
      self, name, *,
      path, 
      dom5_path = Path(os.environ.get("DOM5_PATH")).resolve(),
      host = None,
      notifiers = None, 
      finished = False, 
      turn = 0,
      players = None,
      game_settings = GAME_DEFAULTS):
    self.name = name
    self.finished = finished

    self.process = None

    self.path = path
    self.dom5_path = dom5_path
    self.path.mkdir(exist_ok = True)

    self.host = host

    if not notifiers:
      self.notifiers = []
    else:
      self.notifiers = notifiers

    self.turn = turn
    self.settings = copy(game_settings)
    if not players:
      players = {}
    self.players = players
    
    if self.settings.get("mapfile"):
      self.map = list(filter(lambda m: m.filename == self.settings["mapfile"], host.maps))[0]

    if self.settings.get("enablemod"):
      self.mods = list(filter(lambda m: any(mod == m.filename for mod in self.settings["enablemod"]), host.mods))


    self.state = STATUS_INIT
    self.status_change_triggers = {}
    self._default_triggers()

  def as_dict(self):
    game_settings = copy(self.settings)
    metadata = {
      key:value for key, value in self.__dict__.items() 
      if key in type(self).metadata_format
    }

    notifiers = ([notifier._as_dict() for notifier in self.notifiers] 
                if self.notifiers else []
    )
    metadata.update(notifiers = notifiers)

    return {"game_settings": game_settings, "metadata": metadata}

  @classmethod
  def from_dict(cls, dict_, **kwargs):
    game_settings, metadata_dict = dict_["game_settings"], dict_["metadata"]
    
    metadata = {
      key:value for key, value in metadata_dict.items()
      if key in cls.metadata_format
    }

    metadata["notifiers"] = [
      Notifier._from_dict(notifier) 
      for notifier in metadata_dict["notifiers"]
    ]

    game = cls(
      game_settings = game_settings,
      **kwargs,
      **metadata
    )
    return game

  async def run_until_cancelled(self):
    self.process = TCPServer(self.name, **self.settings)
    tasks = asyncio.gather(self.process.run(), self.receive_updates())
    try:
      await tasks
    except asyncio.CancelledError:
      tasks.cancel()
    finally:
      self.process = None

  def notify(self, msg):
    for notifier in self.notifiers:
      notifier.notify(msg)

  async def receive_updates(self):
    while True:
      await asyncio.sleep(1)
      while self.process and self.process.has_updates():
        update = self.process.update_queue.popleft()
        for key, value in update.__dict__.items():
          prev = self.__dict__.get(key)
          if prev != value:
            self.__dict__[key] = value
            self.on_status_change(key, prev, value)

  def on_status_change(self, name, prev, new):
    if self.status_change_triggers.get(name):
      for func in self.status_change_triggers.get(name):
        func(prev, new)

  def when_status_change(self, name):
    def interior_decorator(func):
      if self.status_change_triggers.get(name) is None:
        self.status_change_triggers[name] = []
      self.status_change_triggers[name].append(func)
      return func
    return interior_decorator

  def is_started(self):
    # if the turn attribute is unreliable, checking for the presence of
    # a 'ftherland' file instead may be a viable alternative.
    return self.turn > 0

  def shutdown(self):
    if self.process and self.process.process.returncode is None: 
      self.process.die()
    self.process = None

  def restart(self):
    self.shutdown()
    self.setup()

  def domcmd(self, command):
    with open(self.path / "domcmd", "x") as file:
      file.write(command)

  def force_next_turn(self):
    self.domcmd("settimeleft 1")

  def on_preexec(self):
    pass

  def on_postexec(self):
    pass
