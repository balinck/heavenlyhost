# * tidy up server/game classes
#   * no more json dump on add_game
#   * move ALL game initialization to game.__init__, including paths
#   * cut down on unnecessary methods and especially unnecessary private methods
# * add comments/docstrings
# * game status querying
# * add exceptions
# * readonly properties?

import os
import json
import io
import asyncio
from copy import copy
from subprocess import Popen, PIPE, STDOUT
from pathlib import Path

from .notify import Notifier

_GAME_DEFAULTS = {
  "nosteam": True,              # --nosteam       Do not connect to steam (workshop will be unavailable) 
  "port": 0,                    # --port X        Use this port nbr
  "postexec": True,             # --postexec CMD  Execute this command after each new turn
  "preexec": False,             # --preexec CMD   Execute this command before each new turn
  "era": 1,                     # --era X         New game created in this era (1-3)
  "teamgame": False,            # --teamgame      Disciple game, multiple players on same team
  "clustered": False,           # --clustered     Clustered start positions for team game
  "closed": [],                 # --closed X      Nation closed X=nation number (5-249)
  "mapfile": None,              # --mapfile XXX   Filename of map. E.g. eye.map
  "randmap": 15,                # --randmap X     Make and use a random map with X prov per player (10,15,20)
  "noclientstart": False,       # --noclientstart Clients cannot start the game during Choose Participants
  "statuspage": True,           # --statuspage XX Create html page that shows who needs to play their turn
  "scoredump": True,            # --scoredump     Create a score file after each turn (scores.html)
# World Contents
  "magicsites": 50,             # --magicsites X  Magic site frequency 0-75 (default 40)
  "indepstr": 5,                # --indepstr X    Strength of Independents 0-9 (default 5)
  "richness": 100,              # --richness X    Money multiple 50-300 (default 100)
  "resources": 100,             # --resources X   Resource multiple 50-300 (default 100)
  "recruitment": 100,           # --recruitment X Unit recruitment point multiple 50-300 (default 100)
  "supplies": 100,              # --supplies X    Supply multiple 50-300 (default 100)
  "startprov": 1,               # --startprov X   Number of starting provinces (1-9)
# Divine Rules   
  "eventrarity": 2,             # --eventrarity X Random event rarity 1-2, 1=common 2=rare
  "globals": 5,                 # --globals X     Global Enchantment slots 3-9 (default 5)
  "thrones": [3, 0, 0],         # --thrones X Y Z  Number of thrones of level 1, 2 and 3
  "requiredap": 3,              # --requiredap X   Ascension points required for victory (def total-1)
  "conqall": False,             # --conqall        Win by eliminating all opponents only
  "cataclysm": False,           # --cataclysm X    Cataclysm will occur on turn X (def off)
  "hofsize": 10,                # --hofsize X     Size of Hall of Fame 5-15 (default 10)
  "noartrest": False,           # --noartrest     Players can create more than one artifact per turn
  "research": 2,                # --research X    Research difficulty 0 to 4 (default 2)
  "norandres": False,           # --norandres     No random start research
  "nostoryevents": True,        # --nostoryevents Disable all story events
  "storyevents": False,         # --storyevents   Enable some story events
  "allstoryevents": False,      # --allstoryevents Enable all story events
  "scoregraphs": False,         # --scoregraphs   Enable score graphs during play
  "nonationinfo": False,        # --nonationinfo  No info at all on other nations
  # Advanced    
  "nocheatdet": False,          # --nocheatdet    Turns off cheat detection
  "renaming": False,            # --renaming      Enable commander renaming
  "masterpass": None,           # --masterpass XX Master password. E.g. masterblaster
  }

class Host:

  def __init__(
      self, root_path,
      dom5_path = Path(os.environ.get("DOM5_PATH")).resolve()):
    self.games = []
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

  def validate_game_settings(self, name, **game_settings):
    pass

  def create_new_game(self, name, **game_settings):
    new_game_path = self.savedgame_path / name
    settings_with_defaults = copy(_GAME_DEFAULTS)
    settings_with_defaults.update(game_settings)
    game = Game(name = name, 
                path = new_game_path, 
                dom5_path = self.dom5_path,
                game_settings = settings_with_defaults
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
      game = Game.from_dict(dict_)
      self.games.append(game)
    else:
      print("no json data found in {}".format(json_path.parent))

  def serialize_game(self, game):
    dict_ = game.as_dict()
    json_path = game.path / "host_data.json"
    with open(json_path, "w+") as file:
      json.dump(dict_, file)

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

  def init_pipe(self):
    pipe_path = self.root / ".pipe"
    if pipe_path.exists():
      os.remove(pipe_path)
    os.mkfifo(pipe_path)
    pipe_fd = os.open(pipe_path, os.O_RDONLY | os.O_NONBLOCK)
    self.pipe = os.fdopen(pipe_fd)

  def read_from_pipe(self):
    msg = self.pipe.readline()
    if msg: 
      try:
        cmd, name = msg.split()
      except ValueError:
        print("host instance received invalid message from pipe: \"{}\"".format(msg))
      game = self.find_game_by_name(name)
      if cmd == "postexec":
        game.on_postexec()
      elif cmd == "preexec":
        game.on_preexec()

  async def listen_pipe(self):
    while True:
      self.read_from_pipe()
      await asyncio.sleep(5)

  def startup(self):
    for game in self.games:
      if not game.finished:
        if game.process is None: game.setup()
        elif game.process.poll() is not None: game.restart()

  def shutdown(self):
    self.dump_games()
    for game in self.games: game.shutdown()

class Game:

  def __init__(
      self, name, 
      path = None, dom5_path = Path(os.environ.get("DOM5_PATH")).resolve(), 
      notifier = None, finished = False, 
      game_settings = _GAME_DEFAULTS):
    self.name = name
    self.finished = False
    self.process = None
    self.path = path
    self.dom5_path = dom5_path
    self.notifier = notifier 
    self.settings = copy(game_settings)
    self.path.mkdir(exist_ok = True)

  def as_dict(self):
    game_settings = copy(self.settings)
    metadata = {
      "name": self.name,
      "finished": self.finished,
      "process": None,
      "path": str(self.path) if self.path else None,
      "dom5_path": str(self.dom5_path),
      "notifier": self.notifier._as_dict() if self.notifier else None 
    }
    return {"game_settings": game_settings, "metadata": metadata}

  @classmethod
  def from_dict(cls, dict_):
    game_settings, metadata = dict_["game_settings"], dict_["metadata"]

    name = metadata["name"]

    path = Path(metadata["path"])
    dom5_path = Path(metadata["dom5_path"])

    notifier_dict = metadata.get("notifier")
    if notifier_dict:
      notifier = Notifier._from_dict(notifier_dict)
    else:
      notifier = None

    finished = metadata.get("finished")
    
    game = cls(name = name, 
               path = path, 
               dom5_path = dom5_path, 
               notifier = notifier,
               finished = finished, 
               game_settings = game_settings)
    return game

  def generate_setup_command(self):
    command = [str(self.dom5_path / "dom5_amd64"), "-S", "-T"]

    def _postexec_switch(value):
      if value:
        return [
          "--postexec",  
          "echo \"postexec {}\" > {}".format(
            self.name, 
            self.path.parent.parent / ".pipe"
            )
        ]
      else:
        return []

    def _preexec_switch(value):
      if value:
        return [
          "--preexec",  
          "echo \"preexec {}\" > {}".format(
            self.name, 
            self.path.parent.parent / ".pipe"
            )
        ]
      else:
        return []

    def _closed_switch(value):
      return []

    def _teamgame_switch(value):
      return []

    def _statuspage_switch(value):
      if value:
        return ["--statuspage", self.path / "status.html"]

    special_switches = {"postexec": _postexec_switch,
                        "preexec": _preexec_switch, 
                        "closed": _closed_switch,
                        "teamgame": _teamgame_switch,
                        "statuspage": _statuspage_switch
                        }

    for switch, value in self.settings.items():
      if switch in special_switches:
        command += special_switches[switch](value)
      elif value is True:
        command += ["--{}".format(switch)]
      elif value:
        command += ["--{}".format(switch), str(value)]
    return command

  def generate_query_command(self):
    return [str(com) for com in [str(self.dom5_path / "dom5_amd64"), "-T",
        "--tcpquery", "--ipadr", "localhost",
        "--port", self.settings['port'], 
        "--nosteam"
    ] if com != ""]

  def setup(self):
    proc = Popen(
      self.generate_setup_command(), stdin=PIPE, 
      stdout=PIPE, stderr=STDOUT
    )
    proc.stdin.write(bytes(self.name, "utf-8"))
    proc.stdin.close()
    self.process = proc

  def query(self):
    proc = Popen(self.generate_query_command())
    stdout, stderr = proc.communicate(timeout=5)
    return stdout, stderr

  def shutdown(self):
    if self.process and self.process.poll() is None: self.process.kill()
    with open(self.path / "log.txt", "a") as file:
      for line in io.TextIOWrapper(self.process.stdout, encoding="utf-8"): 
        file.write(line)
    self.process = None

  def restart(self):
    self.shutdown()
    self.setup()

  def on_preexec(self):
    pass

  def on_postexec(self):
    if self.notifier:
      self.notifier.notify("{} has just completed a turn.".format(self.name))
