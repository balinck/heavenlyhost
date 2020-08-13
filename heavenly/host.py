import os
import json
import io
import asyncio
from copy import copy
from subprocess import Popen, PIPE, STDOUT, TimeoutExpired
from pathlib import Path
from collections import namedtuple
from datetime import datetime, timedelta
import re

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
STATUS_SETUP = "Game is being setup"
STATUS_INIT = "Game is initializing"
STATUS_UNKNOWN = "Unknown"

re_setup = re.compile(
    "^Setup port (?P<port>[0-9]+),?" 
  + "(?P<time_until_start>[^()]*?) ?(?:\(.*\))?,?"
  + " open: (?P<open>[0-9]+)," 
  + " players (?P<players>[0-9]+)," 
  + " ais (?P<ais>[0-9]+)$"
)
re_mapgen = re.compile(
  "(?P<mapgen>Random Map Generation), .*$"
)
re_active = re.compile(
    "(?P<name>[^ ,]+), Connections (?P<connections>[0-9]+)," 
  + " (?P<time_until_host>[^()]*) (?:\(.*\))$"
)
re_whos_played = re.compile(
  "^(?P<whos_played>(?:\*?[a-zA-Z]{2,3}(?:\+|-) ?)+)$"
)
re_next_turn = re.compile("^Generating next turn$")


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
    self.shutting_down = False
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
        self.maps.append(file_path.name)

    loop = asyncio.get_event_loop()
    loop.create_task(self.ping())

  def validate_game_settings(self, name, **game_settings):
    pass

  def get_nation_dict(self):
    command = [str(self.dom5_path / "dom5_amd64"), 
      "-S", "-T", "--nosteam", "--listnations"
    ]
    proc = Popen(command, stdout=PIPE, stderr=PIPE)
    stdout, stderr = proc.communicate(timeout=5)

    nations = {1: {}, 2: {}, 3: {}}
    for line in stdout.decode().split("\n"):
      substrings = line.split()
      if not substrings:
        pass
      elif substrings[1] == "Era": 
        key = int(substrings[2])
      else:
        nation_number = int(substrings[0])
        nation_name = " ".join(substrings[1:])
        nations[key][nation_number] = nation_name
    self.nations = nations 

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
    game = Game(name = name,
                notifiers = notifiers,
                game_settings = settings_with_defaults,
                path = new_game_path,
                dom5_path = self.dom5_path,
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

  async def ping(self):
    await asyncio.sleep(self.ping_interval)
    while not self.shutting_down:
      timed_out = [
        game
        for game in self.games
        if game.status["summary"] == STATUS_TIMEOUT
      ]
      for game in timed_out:
        print(game.name, "timed out.")
        game.restart()
      self.status = [copy(game.status) for game in self.games]
      await asyncio.sleep(self.ping_interval)

  def startup(self):
    for game in self.games:
      if not game.finished:
        if game.process is None: game.setup()
        elif game.process.poll() is not None: game.restart()

  def shutdown(self):
    self.shutting_down = True
    self.dump_games()
    for game in self.games: game.shutdown()

class Game:

  metadata_format = set([
    "name",
    "turn"
    "finished",
    "notifiers",
    "players"
    ]
  )

  def __init__(
      self, name, *,
      path, 
      dom5_path = Path(os.environ.get("DOM5_PATH")).resolve(),
      notifiers = None, 
      finished = False, 
      turn = 0,
      players = None,
      game_settings = _GAME_DEFAULTS):
    self.name = name
    self.finished = False

    self.process = None
    self.buffer = []

    self.path = path
    self.dom5_path = dom5_path
    self.path.mkdir(exist_ok = True)

    if not notifiers:
      self.notifiers = []
    else:
      self.notifiers = notifiers

    self.turn = turn
    self.timeout = timedelta(seconds = 90)
    self.settings = copy(game_settings)
    if not players:
      players = []
    self.players = players

    self.status = {
      "name": self.name,
      "summary": STATUS_INIT,
      "port": self.settings["port"],
      "connections": None,
      "time_until_start": None,
      "time_until_host": None,
      "open_slots": None,
      "ready_players": None,
      "ready_ais": None,
      "turn": self.turn,
      "whos_played": None
    }

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

  def generate_setup_command(self):
    command = [str(self.dom5_path / "dom5_amd64"), "-S", "-T"]

    def _postexec_switch(value):
      if value:
        return [
          "--postexec",  
          "echo \"postexec {}\" > {}".format(
            self.name, 
            self.path / ".pipe"
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
            self.path / ".pipe"
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

    def _thrones_switch(value):
      if value:
        thrones = ["--thrones"]
        for n in value: thrones.append(str(n))
        return thrones
      else:
        return []

    special_switches = {"postexec": _postexec_switch,
                        "preexec": _preexec_switch, 
                        "closed": _closed_switch,
                        "teamgame": _teamgame_switch,
                        "statuspage": _statuspage_switch,
                        "thrones": _thrones_switch
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
    pipe_path = self.path / ".pipe"
    if pipe_path.exists():
      os.remove(pipe_path)
    os.mkfifo(pipe_path)
    read_pipe_fd = os.open(pipe_path, os.O_RDONLY | os.O_NONBLOCK)
    write_pipe_fd = os.open(pipe_path, os.O_WRONLY | os.O_NONBLOCK)
    self.pipe = os.fdopen(read_pipe_fd)
    proc = Popen(
      self.generate_setup_command(), 
      stdin=PIPE, stdout=write_pipe_fd, stderr=STDOUT
    )
    proc.stdin.write(bytes(self.name, "utf-8"))
    proc.stdin.close()
    self.process = proc
    self.status["summary"] = STATUS_INIT
    loop = asyncio.get_event_loop()
    loop.create_task(self.listen_stdout())

  # status = {"name": "",
  #           "port": 0
  #           "summary": "",
  #           "connections": 0,
  #           "time_to_start": "",
  #           "time_to_host": "",
  #           "open_slots": 0,
  #           "ready_players": 0,
  #           "ready_ais": 0,
  #           "whos_played": {} }

  def update_status(self, msg):
    setup = re_setup.match(msg)
    if setup:
      status = {
        "summary": STATUS_SETUP,
        "time_until_start": setup.groupdict()["time_until_start"],
        "open_slots": setup.groupdict()["open"],
        "ready_players": setup.groupdict()["players"],
        "ready_ais": setup.groupdict()["ais"]
      }
      self.status.update(status)
      return

    active = re_active.match(msg)
    if active:
      if self.turn == 0:
        self.turn = 1
      status = {
        "summary": STATUS_ACTIVE,
        "connections": active.groupdict()["connections"],
        "time_until_host": active.groupdict()["time_until_host"],
        "turn": self.turn
      }
      self.status.update(status)
      return

    mapgen = re_mapgen.match(msg)
    if mapgen:
      status = {
        "summary": STATUS_MAPGEN
      }
      self.status.update(status)
      return

    whos_played = re_whos_played.match(msg)
    if whos_played and self.players:
      who = {}
      for nation, status in zip(self.players, msg.split()):
        who[nation] = {}
        who[nation]["connected"] = "*" in status
        who[nation]["played"] = "+" in status
      self.status["whos_played"] = who
      return

    next_turn = re_next_turn.match(msg)
    if next_turn:
      self.turn += 1
      self.status.update(dict(turn = self.turn))

  async def listen_stdout(self):
    print(self.name, "now listening to STDOUT!")
    last = datetime.now()
    while not self.pipe.closed:
      for line in iter(self.pipe.readline, ""):
        last = datetime.now()
        #print("({}) Received: {}".format(self.name, line))
        log = "[{}] {}".format(datetime.now().ctime(), line)
        self.buffer.append(log)
        self.update_status(line)
        #print(self.status)
      if (datetime.now() - last) > self.timeout:
        print("Setting status to TIMEOUT.")
        self.status["summary"] = STATUS_TIMEOUT
        self.pipe.close()
        return
      await asyncio.sleep(1)
    print("Pipe must have closed. Stopped listening to STDOUT.")
    return

  def is_started(self):
    # if the turn attribute is unreliable, checking for the presence of
    # a 'ftherland' file instead may be a viable alternative.
    return self.turn > 0

  def get_players(self):
    players = []
    regex = re.compile("^player (?P<nation_number>[0-9]+):.*$")
    for line in self.query().split("\n"):
      match = regex.match(line)
      if match:
        nation_number = match.groupdict().get("nation_number")
        players.append(nation_number)
    self.players = copy(players)
    print("PLAYERS:", self.players)

  def query(self):
    proc = Popen(
      self.generate_query_command(), 
      stdout=PIPE, stderr=PIPE
      )
    try:
      stdout, stderr = proc.communicate(timeout = 10)
    except TimeoutExpired:
      print("tcpquery on {} timed out.".format(self.name))
    else:
      return stdout.decode()

  def shutdown(self):
    if self.process and self.process.poll() is None: self.process.kill()
    self.pipe.close()
    with open(self.path / "log.txt", "a") as file:
      for line in self.buffer: 
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
