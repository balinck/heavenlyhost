import os
import json
import io
import asyncio
from copy import copy
from subprocess import Popen, PIPE, STDOUT
from pathlib import Path
import requests

from notify import *

_GAME_DEFAULTS = {
  "nosteam": True,              # --nosteam       Do not connect to steam (workshop will be unavailable) 
  "port": 0,                    # --port X        Use this port nbr
  "postexec": True,             # --postexec CMD  Execute this command after each new turn
  "preexec": None,              # --preexec CMD   Execute this command before each new turn
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

class Server:

  def __init__(self, dom5_path=None, data_path=None):
    self.games = []
    if dom5_path: 
      self.dom5_path = Path(dom5_path).resolve()
    else: 
      self.dom5_path = Path(os.environ.get("DOM5_PATH") or "./data/dominions5/").resolve()
    if data_path: 
      self.data_path = Path(data_path).resolve() 
    else: 
      self.data_path = Path(os.environ.get("DOM5HOST_DATA_PATH") or "./data/").resolve()
    self.conf_path = self.data_path / "dominions5"
    self.savedgame_path = self.data_path / "savedgames"
    self.map_path = self.data_path / "maps"
    self.mod_path = self.data_path / "mods"
    os.environ["DOM5_CONF"] = str(self.conf_path)
    os.environ["DOM5_SAVE"] = str(self.savedgame_path)
    os.environ["DOM5_LOCALMAPS"] = str(self.map_path)
    os.environ["DOM5_MODS"] = str(self.mod_path)
    for path in (self.data_path, self.conf_path, self.savedgame_path, self.map_path, self.mod_path):
      path.mkdir(exist_ok=True)

  def _load_json_data(self):
    for savedgame, _, _ in os.walk(self.savedgame_path):
      json_path = Path(savedgame) / "host_data.json"
      if json_path.exists():
        with open(json_path, "r") as file:
          game = Game._decode_json(file)
        self.add_game(game)

  def _dump_json_gamedata(self, game):
    game.path.mkdir(exist_ok=True)
    file_path = game.path / "host_data.json"
    with open(file_path, "w+") as file:
      game._encode_json(file)

  def _dump_all(self):
    for game in self.games:
      self._dump_json_gamedata(game)

  def _handle_pipe_in(self, msg):
    try:
      cmd, name = msg.split()
    except ValueError:
      print("Invalid command.")
      return
    game = list(filter(lambda g: g.name == name, self.games))[0]
    if cmd == "preexec":
      game.on_preexec()
    if cmd == "postexec":
      game.on_postexec()

  def init_pipe(self):
    pipe_path = self.data_path / ".pipe"
    if pipe_path.exists():
      os.remove(pipe_path)
    os.mkfifo(pipe_path)
    pipe_fd = os.open(pipe_path, os.O_RDONLY | os.O_NONBLOCK)
    self.pipe = os.fdopen(pipe_fd)

  def read_from_pipe(self):
    msg = self.pipe.readline()
    if msg: 
      print("Received:", msg)
      self._handle_pipe_in(msg)

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
    self._dump_all()
    for game in self.games: game.shutdown()

  def add_game(self, game_to_add):
    if (not game_to_add.finished 
        and any(game.settings['port'] == game_to_add.settings['port'] 
        for game in self.games if not game.finished)):

      print("Port {} already in use in active game!".format(game_to_add.settings['port']))

    elif (any(game.name == game_to_add.name
          for game in self.games)):

      print("Name \"{}\" already in use!".format(game_to_add.settings['name']))

    else: 
      game_to_add.path = self.savedgame_path / game_to_add.name
      game_to_add.dom5_path = self.dom5_path
      self.games.append(game_to_add)
      self._dump_json_gamedata(game_to_add)

class Game:

  def __init__(self, name, **game_settings):
    self.name = name
    self.finished = False
    self.process = None
    self.path = ""
    self.dom5_path = ""
    self.notifier = None 

    self.settings = {}
    self.settings.update(_GAME_DEFAULTS)
    self.settings.update(game_settings)

  def _encode_json(self, file):
    game_settings = copy(self.settings)
    metadata = {
    "name": self.name,
    "finished": self.finished,
    "process": None,
    "path": str(self.path),
    "dom5_path": str(self.dom5_path),
    "notifier": self.notifier._as_dict() if self.notifier else None }
    return json.dump({"game_settings": game_settings, "metadata": metadata}, file, indent=2)

  @classmethod
  def _decode_json(cls, file):
    dict_ = json.load(file)
    notifier = dict_["metadata"].get("notifier")
    if notifier: 
      restored_notifier = Notifier._from_dict(notifier)
      dict_["metadata"]["notifier"] = restored_notifier
    name = dict_["metadata"]["name"]
    game = cls(name, **dict_["game_settings"])
    game.__dict__.update(dict_["metadata"])
    game.path, game.dom5_path = Path(game.path), Path(game.dom5_path)
    return game

  def _setup_command(self):
    command = [str(self.dom5_path / "dom5_amd64"), "-S", "-T", "--tcpserver"]
    for key, value in self.settings.items():
      if key == "postexec" and value:
        command += ["--postexec", "echo \"postexec {}\" > {}".format(self.name, str(self.path.parent.parent/".pipe"))]
      elif key == "statuspage" and value:
        command += ["--statuspage", str(self.path / "status.html")]
      elif key == "closed":   # TODO: implement closing nations
        pass
      elif key == "teamgame": # TODO: implement team games
        pass
      elif type(value) == type(True):
        if value is True:
          command += ["--{}".format(key)]
        else:
          pass
      elif value:
        command += ["--{}".format(key), str(value)]
    return command

  def _query_command(self):
    return [str(com) for com in [str(self.dom5_path / "dom5.sh"), "-T", "--tcpquery", "--ipadr", "localhost", "--port", self.settings['port'], "--nosteam"] if com != ""]

  def setup(self):
    proc = Popen(self._setup_command(), stdin=PIPE, stdout=PIPE, stderr=STDOUT)
    proc.stdin.write(bytes(self.name, "utf-8"))
    proc.stdin.close()
    self.process = proc

  def query(self):
    proc = Popen(self._query_command())
    stdout, stderr = proc.communicate(timeout=5)
    return stdout, stderr

  def shutdown(self):
    if self.process and self.process.poll() is None: self.process.kill()
    with open(self.path / "log.txt", "a") as file:
      for line in io.TextIOWrapper(self.process.stdout, encoding="utf-8"): file.write(line)
    self.process = None

  def restart(self):
    self.shutdown()
    self.setup()

  def on_preexec(self):
    pass

  def on_postexec(self):
    if self.notifier:
      self.notifier.notify("{} has just completed a turn.".format(self.name))
