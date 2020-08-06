import os
import json
import io
import asyncio
from copy import copy
from subprocess import Popen, PIPE, STDOUT, TimeoutExpired
from pathlib import Path
from collections import namedtuple

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

# TODO: don't include the whole game object in TCPQueryResponse
# TODO: add a dict representing who's played their turn
TCPQueryResponse = namedtuple("TCPQueryResponse", ["game_obj", "game_name", "status", "turn"])
STATUS_TIMEOUT = "Timed out"
STATUS_MAPGEN = "Generating random map"
STATUS_ACTIVE = "Game is active"
STATUS_WAITING = "Game is being setup"  

class Host:

  def __init__(
      self, 
      root_path,
      dom5_path = Path(os.environ.get("DOM5_PATH")).resolve(),
      ping_interval = 20
      ):
    self.games = []
    self.maps = []

    self.ping_interval = ping_interval
    self.status = None

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
        self.maps.append(file_path.name)

  def validate_game_settings(self, name, **game_settings):
    pass

  def get_free_port(self):
    lower, upper = self.port_range
    for n in range(lower, upper):
      if not [*self.filter_games_by(finished = False, port = n)]:
        return n
    return None

  def create_new_game(self, name, notifiers = None, **game_settings):
    new_game_path = self.savedgame_path / name
    settings_with_defaults = copy(_GAME_DEFAULTS)
    settings_with_defaults.update(game_settings)
    if not notifiers:
      notifiers = []
    game = Game(name = name,
                notifiers = notifiers, 
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

  def query_games(self):
    responses = [game.query() for game in self.games]
    return responses

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

  async def ping(self):
    while True:
      await asyncio.sleep(self.ping_interval)
      responses = self.query_games()
      timed_out = [response.game_obj 
                   for response in responses 
                   if response.status == STATUS_TIMEOUT]
      for game in timed_out:
        game.restart()
      self.status = responses

  async def listen_pipe(self):
    while True:
      self.read_from_pipe()
      await asyncio.sleep(1)

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
      self, name, *,
      path = None, dom5_path = Path(os.environ.get("DOM5_PATH")).resolve(), 
      notifiers = None, finished = False, turn = 0,
      game_settings = _GAME_DEFAULTS):
    self.name = name
    self.finished = False
    self.process = None
    self.path = path
    self.dom5_path = dom5_path
    if not notifiers:
      self.notifiers = []
    else:
      self.notifiers = notifiers
    self.turn = turn
    self.status = "Unknown"
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
      "notifiers": ([notifier._as_dict() for notifier in self.notifiers] 
                    if self.notifiers else None
      ),
      "turn": self.turn 
    }
    return {"game_settings": game_settings, "metadata": metadata}

  @classmethod
  def from_dict(cls, dict_):
    game_settings, metadata = dict_["game_settings"], dict_["metadata"]

    name = metadata["name"]

    path = Path(metadata["path"])
    dom5_path = Path(metadata["dom5_path"])

    notifier_list = metadata.get("notifiers")
    if notifier_list:
      notifiers = [Notifier._from_dict(notifier) for notifier in notifier_list]
    else:
      notifiers = None

    finished = metadata.get("finished")

    turn = metadata.get("turn")
    
    game = cls(name = name, 
               path = path, 
               dom5_path = dom5_path, 
               notifiers = notifiers,
               finished = finished,
               turn = turn, 
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

    if self.is_started():
      ignored_switches = (set(_GAME_DEFAULTS.keys()) 
                        - set(["postexec", "preexec", "port", 
                              "nosteam", "statuspage", "scoredump",
                              "nocheatdet"
                              ])
                          )
    else:
      ignored_switches = set()

    for switch, value in self.settings.items():
      if switch in ignored_switches:
        pass
      elif switch in special_switches:
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
      self.generate_setup_command(), 
      stdin=PIPE, stdout=PIPE, stderr=STDOUT
    )
    proc.stdin.write(bytes(self.name, "utf-8"))
    proc.stdin.close()
    self.process = proc

  def is_started(self):
    # if the turn attribute is unreliable, checking for the presence of
    # a 'ftherland' file instead may be a viable alternative.
    return self.turn > 0

  def query(self):
    if self.finished: 
      return TCPQueryResponse(
        game_obj = self, game_name = self.name, 
        status = "Finished", turn = self.turn
        )
    proc = Popen(
      self.generate_query_command(), 
      stdout=PIPE, stderr=PIPE
      )
    turn = self.turn
    status = "Unknown"
    try:
      stdout, stderr = proc.communicate(timeout=5)
    except TimeoutExpired:
      if not self.settings["mapfile"] and turn == 0:
        # Unfortunately, --tcpquery always times out while the server is 
        # generating a random map. This hack will keep Host.ping from 
        # trying to restart us and breaking mapgen until I implement a better
        # fix.
        self.status = STATUS_MAPGEN
        return TCPQueryResponse(
          game_obj = self, game_name = self.name, 
          status = STATUS_MAPGEN, turn = turn
        )
      else:
        status = STATUS_TIMEOUT
    else:
      for line in stdout.decode().split("\n"):
        if line.startswith("Status: "):
          status = line[8:]
        elif line.startswith("Turn: "):
          turn = int(line[6:])
    self.turn = turn
    self.status = status
    return TCPQueryResponse(
      game_obj = self, game_name = self.name, 
      status = status, turn = turn
      )

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
    self.turn += 1
    for notifier in self.notifiers:
      notifier.notify(
        "{} has advanced to turn {}.".format(
          self.name, self.turn)
    )
