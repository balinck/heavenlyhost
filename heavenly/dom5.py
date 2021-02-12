import shlex
import asyncio
from pathlib import Path
import os
import re
from collections import deque

DOM5_PATH = Path(os.environ.get("DOM5_PATH")).resolve()

STATUS_TIMEOUT = "timed out"
STATUS_MAPGEN = "generating random map"
STATUS_TURN_GEN = "generating next turn"
STATUS_ACTIVE = "active"
STATUS_SETUP = "waiting to start"
STATUS_INIT = "initializing"
STATUS_UNKNOWN = "unknown"

class GameUpdate:
  
  def __init__(self, match):
    self.__dict__.update(match.groupdict())

  @classmethod
  def match(cls, msg):
    match = cls.regex.match(msg)
    if match:
      return cls(match)
    else:
      return None

class Setup(GameUpdate):

  regex = re.compile(
    "^Setup port (?P<port>[0-9]+),?" 
    "(?P<time_until_start>[^()]*?) ?(?:\(.*\))?,?"
    " open: (?P<open>[0-9]+)," 
    " players (?P<player_count>[0-9]+)," 
    " ais (?P<ais>[0-9]+)$"
  )

  def __init__(self, match):
    super().__init__(match)
    self.state = STATUS_SETUP

class Mapgen(GameUpdate):

  regex = re.compile(
    "(?P<mapgen>Random Map Generation), .*$"
  )

  def __init__(self, match):
    super().__init__(match)
    self.state = STATUS_MAPGEN

class Active(GameUpdate):

  regex = re.compile(
    "(?P<name>[^ ,]+), Connections (?P<connections>[0-9]+)," 
    " (?P<time_until_host>[^()]*) (?:\(.*\))$"
  )

  def __init__(self, match):
    super().__init__(match)
    self.state = STATUS_ACTIVE

class TurnAdvance(GameUpdate):

  regex = re.compile("^Generating next turn$")

  def __init__(self, match):
    super().__init__(match)
    self.state = STATUS_TURN_GEN

class WhoPlayed(GameUpdate):

  regex = re.compile(
    "^(?P<who_played>(?:\*?[a-zA-Z]{0,3}(?:\+|-) ?)+)$"
  )

  def __init__(self, match):
    self.who_played = []
    for nation in match.groupdict()["who_played"].split():
      connected = "*" in nation
      turn = "-"
      if "+" in nation: turn = "played"
      if "(" in nation: turn = "AI"
      if "?" in nation: turn = "unfinished"
      nation = nation.translate({ord(c): "" for c in "*+()?-"})
      self.who_played.append((nation, turn, connected))

class PlayerList(GameUpdate):

  def __init__(self, players):
    self.players = players

class GameOver(GameUpdate):

  def __init__(self):
    self.finished = True

class Dom5Process:

  def __init__(
      self, 
      stdin = asyncio.subprocess.PIPE, 
      stdout = asyncio.subprocess.PIPE, 
      stderr = asyncio.subprocess.STDOUT, 
      **kwargs):
    cl_args = ["-T", "-m"]
    for key, value in kwargs.items():
      if key == "statuspage":
        cl_args.extend(["--statuspage", "status.html"])
      elif key == "thrones" and value:
        thrones = ["--thrones"] + [str(n) for n in value]
        cl_args.extend(thrones)
      elif key == "enablemod" and value:
        for mod in value:
          cl_args.extend(["--enablemod", mod])
      elif value:
        cl_args.append("--" + key)
        if type(value) is not bool:
          cl_args.append(str(value))
    cl_args = shlex.split(" ".join(cl_args))
    self.process = asyncio.create_subprocess_exec(str(DOM5_PATH / "dom5_amd64"), 
      *cl_args, stdin = stdin, 
      stdout = stdout, stderr = stderr
    )
    self.tasks = []

  async def get_output(self):
    stdout, stderr = await self.process.communicate()
    self.output = stdout.decode()
    self.error = stderr

  async def run(self):
    self.process = await self.process
    try:
      await asyncio.gather(*self.tasks)
      await self.process.wait()
    except asyncio.CancelledError:
      self.process.terminate()
      await self.process.wait()

  def die(self):
    self.process.terminate()

class TCPServer(Dom5Process):
  update_types = [Setup, Active, Mapgen, WhoPlayed, TurnAdvance]
  
  def __init__(self, name, **game_settings):
    super().__init__(stderr = asyncio.subprocess.STDOUT, tcpserver = True, **game_settings)

    self.port = game_settings["port"]

    async def write_name():
      try:
        self.process.stdin.write(bytes(name + "\n", "utf-8"))
      except BrokenPipeError:
        print("Error writing to process.")
      finally:
        self.process.stdin.close()

    self.tasks.append(write_name())
    self.tasks.append(self.read_from_stdout())
    self.tasks.append(self.check_for_gameover())
    self.update_queue = deque()

  async def read_from_stdout(self):
    while not self.process.stdout.at_eof():
      line = await self.process.stdout.readline()
      if line:
        line = line.decode()
        #print(f"{self.port}: {line}")
        for update_type in type(self).update_types:
          update = update_type.match(line)
          if update:
            self.update_queue.append(update)

  async def check_for_gameover(self):
    await self.process.wait()
    # TODO: handle "address already in use" error, which exits with return code 0.
    if self.process.returncode == 0: 
      self.update_queue.append(GameOver())

  async def query(self):
    query = TCPQuery(self.port)
    await query.run()
    return query.output, query.error

  async def request_player_list(self):
    query_output, _ = await self.query()
    regex = re.compile("^player (?P<nation_number>[0-9]+):.*$")
    players = []
    for line in query_output.split("\n"):
      match = regex.match(line)
      if match:
        nation_number = int(match.groupdict().get("nation_number"))
        players.append(nation_number)
    update = PlayerList(players)
    self.update_queue.append(update)

  def has_updates(self):
    return len(self.update_queue) > 0

  def next_update(self):
    return self.update_queue.popleft()

class TCPQuery(Dom5Process):
  
  def __init__(self, port):
    super().__init__(nosteam = True, tcpquery = True, ipadr = "localhost", port = port)
    self.tasks.append(self.get_output())

async def list_nations():
  process = Dom5Process(nosteam = True, listnations = True)
  process.tasks.append(process.get_output())
  await process.run()
  output = process.output
  nations = {1: {}, 2: {}, 3: {}}
  for line in output.split("\n"):
    substrings = line.split()
    if not substrings:
      pass
    elif substrings[1] == "Era": 
      key = int(substrings[2])
    else:
      nation_number = int(substrings[0])
      nation_name = " ".join(substrings[1:])
      nations[key][nation_number] = nation_name
  return nations

GAME_DEFAULTS = {
  "nosteam": True,              # --nosteam       Do not connect to steam (workshop will be unavailable) 
  "port": 0,                    # --port X        Use this port nbr
  "postexec": False,            # --postexec CMD  Execute this command after each new turn
  "preexec": False,             # --preexec CMD   Execute this command before each new turn
  "era": 1,                     # --era X         New game created in this era (1-3)
  "teamgame": False,            # --teamgame      Disciple game, multiple players on same team
  "clustered": False,           # --clustered     Clustered start positions for team game
  "closed": False,              # --closed X      Nation closed X=nation number (5-249)
  "mapfile": None,              # --mapfile XXX   Filename of map. E.g. eye.map
  "randmap": 15,                # --randmap X     Make and use a random map with X prov per player (10,15,20)
  "noclientstart": False,       # --noclientstart Clients cannot start the game during Choose Participants
  "statuspage": False,          # --statuspage XX Create html page that shows who needs to play their turn
  "scoredump": False,           # --scoredump     Create a score file after each turn (scores.html)
  "enablemod": False,           # --enabledmod    Enable the mod with filename XXX
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
  "thrones": [3, 0, 0],         # --thrones X Y Z Number of thrones of level 1, 2 and 3
  "requiredap": 3,              # --requiredap X  Ascension points required for victory (def total-1)
  "conqall": False,             # --conqall       Win by eliminating all opponents only
  "cataclysm": False,           # --cataclysm X   Cataclysm will occur on turn X (def off)
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