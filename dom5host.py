import os
import json
import io
import asyncio
from copy import copy
from subprocess import Popen, PIPE, STDOUT

DOM5_PATH = os.environ.get("DOM5_PATH") or "./data/dominions5"
DOM5HOST_DATA_PATH = os.environ.get("DOM5HOST_DATA_PATH") or "./data/"

os.environ["DOM5_CONF"] = DOM5HOST_DATA_PATH + "dominions5"
os.environ["DOM5_SAVE"] = DOM5HOST_DATA_PATH + "savedgames/"
os.environ["DOM5_LOCALMAPS"] = DOM5HOST_DATA_PATH + "maps/"
os.environ["DOM5_MODS"] = DOM5HOST_DATA_PATH + "mods/"

os.makedirs(os.path.dirname(DOM5HOST_DATA_PATH), exist_ok=True)

_CONFIG_DEFAULT = {
  "name": "",                   # name of saved game, no spaces are allowed
  "nosteam": True,              # --nosteam       Do not connect to steam (workshop will be unavailable) 
  "port": 0,                    # --port X        Use this port nbr
  "postexec":                   # --postexec CMD  Execute this command after each new turn
  "echo \"turn complete\" > /tmp/heavenly",
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
  "global_enchantments": 5,     # --globals X     Global Enchantment slots 3-9 (default 5)
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
  "finished": False             # Used by server to track active/archived games
  }

class Server:

  def _load_json_data(self):
    games = []
    for savedgame, _, _ in os.walk(DOM5HOST_DATA_PATH+"savedgames/"):
      json_path = savedgame + "/host_data.json"
      if os.path.isfile(json_path):
        with open(json_path, "r") as file:
          dict_ = json.load(file)
        games.append(Game(**dict_))
    self.games += games

  def _dump_json_gamedata(self, game):
    path = "{}savedgames/{}{}".format(DOM5HOST_DATA_PATH, game.name,"/host_data.json")
    os.makedirs(os.path.dirname("{}savedgames/{}/".format(DOM5HOST_DATA_PATH, game.name)), exist_ok=True)
    dict_ = copy(game.__dict__)
    dict_['process'] = None
    with open(path, "w+") as file:
      json.dump(dict_, file, indent=2)

  def _dump_all(self):
    for game in self.games:
      self._dump_json_gamedata(game)

  async def listen_pipe(self):
    pipe_path = "/tmp/heavenly"
    if not os.path.exists(pipe_path):
      os.mkfifo(pipe_path)
    pipe_fd = os.open(pipe_path, os.O_RDONLY | os.O_NONBLOCK)
    pipe = os.fdopen(pipe_fd)
    while True:
      msg = pipe.readline()
      if msg: print("Received:", msg)
      await asyncio.sleep(5)

  def startup(self):
    for game in self.games:
      if not game.finished:
        if game.process is None: game.setup()
        elif game.process.poll() is not None: game.restart()

  def shutdown(self):
    self._dump_all()
    for game in self.games: game.shutdown()

  def add_game(self, **config):
    active_games = [game for game in self.games if not game.finished]
    if any((port == config['port'] for port in (game.port for game in active_games))): 
      print("Port {} already in use!".format(config['port']))
    elif any((name == config['name'] for name in (game.name for game in active_games))):
      print("Name \"{} already in use!".format(config['name']))
    else: 
      game = Game(**config)
      self.games.append(game)
      self._dump_json_gamedata(game)

  def __init__(self):
    self.games = []

class Game:

  def __init__(self, **config):
    self.__dict__.update(_CONFIG_DEFAULT)
    self.__dict__.update(config)
    self.process = None

  def _setup_command(self):
    return [str(com) for com in [
      DOM5_PATH+"/dom5_amd64", "-S", "-T", "--tcpserver",
      "--nosteam" if self.nosteam else "",
      "--port", self.port,             
      "--postexec" if self.postexec else "", self.postexec or "",       
      "--preexec" if self.preexec else "", self.preexec or "", 
      "--era", self.era,               
      "--teamgame" if self.teamgame else "",                        # TODO: implement team games
      "--clustered" if self.clustered else "",     
      "--closed", [],                                               # TODO: implement closing nations
      "--mapfile" if self.mapfile else "", self.mapfile or "",        
      "--randmap" if self.randmap else "", self.randmap,          
      "--noclientstart" if self.noclientstart else "", 
      "--statuspage" if self.statuspage else "", "./data/savedgames/{}/statuspage.html".format(self.name) if self.statuspage else "",     
      "--scoredump" if self.scoredump else "",      
      "--magicsites", self.magicsites,       
      "--indepstr", self.indepstr,          
      "--richness", self.richness,        
      "--resources", self.resources,       
      "--recruitment", self.recruitment,     
      "--supplies", self.supplies,        
      "--startprov", self.startprov,         
      "--eventrarity", self.eventrarity,       
      "--globals", self.global_enchantments,           
      "--thrones", self.thrones,   
      "--requiredap", self.requiredap,        
      "--conqall" if self.conqall else "",       
      "--cataclysm" if self.cataclysm else "",     
      "--hofsize", self.hofsize,          
      "--noartrest" if self.noartrest else "",     
      "--research", self.research,          
      "--norandres" if self.norandres else "",     
      "--nostoryevents" if self.nostoryevents else "",  
      "--storyevents" if self.storyevents else "",   
      "--allstoryevents" if self.allstoryevents else "",
      "--scoregraphs" if self.scoregraphs else "",   
      "--nonationinfo" if self.nonationinfo else "",   
      "--nocheatdet" if self.nocheatdet else "",    
      "--renaming" if self.renaming else "",      
      "--masterpass" if self.masterpass else "", self.masterpass or ""] if com != ""]

  def _query_command(self):
    return [str(com) for com in [DOM5_PATH+"/dom5.sh", "-T", "--tcpquery", "--ipadr", "localhost", "--port", self.port, "--nosteam"] if com != ""]

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
    with open("{}savedgames/{}/log.txt".format(DOM5HOST_DATA_PATH, self.name), "a") as file:
      for line in io.TextIOWrapper(self.process.stdout, encoding="utf-8"): file.write(line)
    self.process = None

  def restart(self):
    self.shutdown()
    self.setup()