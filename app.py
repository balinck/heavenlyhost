import quart.flask_patch
from flask_wtf import FlaskForm
from flask_bootstrap import Bootstrap
from wtforms import StringField, SubmitField, TextAreaField, HiddenField, IntegerField, SelectField, BooleanField, SelectMultipleField
from wtforms.validators import DataRequired, NumberRange
from quart import Quart, render_template, send_file, safe_join, url_for, redirect, flash, request, abort

import asyncio
from pathlib import Path
import os
from hashlib import shake_128
import random

from heavenly.host import Host
from heavenly.notify import DiscordNotifier
from heavenly.config.app import APP_NAME, SERVER_ADDRESS, MOTD, HOST_ROOT_PATH, HOST_PORT_RANGE, SECRET_KEY, SRC_REPO_URL
from heavenly.maps import MAP_THUMBNAIL_DIR
from heavenly.mods import MOD_ICON_DIR

bootstrap = Bootstrap()
app = Quart(__name__)
bootstrap.init_app(app)

app.config.update({
  "APP_NAME": APP_NAME,
  "SERVER_ADDRESS": SERVER_ADDRESS,
  "MOTD": MOTD,
  "map_choices": [("random", "Random")],
  "mod_choices": [], 
  "SECRET_KEY": SECRET_KEY,
  "SRC_REPO_URL": SRC_REPO_URL
})

app.jinja_env.globals.update(app.config)

@app.errorhandler(404)
async def page_not_found(error):
  return "404 - page not found"

@app.route("/")
async def index():
  host = app.config.get('host_instance')
  game_info = []
  for game in host.games:
    game_info.append(dict(
      name = game.name, 
      state = game.state, 
      turn = game.turn, 
      finished = game.finished,
      port = game.settings["port"]
    )
  )
  return await render_template("home.html", game_info = game_info)

@app.route("/new-game", methods = ["GET", "POST"])
async def new_game():
  form = NewGameForm()
  if form.validate_on_submit():
    host = app.config.get("host_instance")
    mapfile = None if form.mapfile.data == "random" else form.mapfile.data
    mods = form.mods.data
    thrones = [form.lvl1_thrones.data, form.lvl2_thrones.data, form.lvl3_thrones.data]

    config = {"era": form.era.data, 
              "port": host.get_free_port(), 
              "mapfile": mapfile,
              "enablemod": mods,
              "magicsites": form.magicsites.data,
              "indepstr": form.indepstr.data,
              "richness": form.richness.data,
              "resources": form.resources.data,
              "recruitment": form.recruitment.data,
              "supplies": form.supplies.data,
              "startprov": form.startprov.data,
              "eventrarity": form.eventrarity.data,
              "thrones": thrones,
              "globals": form.global_enchants.data,
              "requiredap": form.requiredap.data,
              "cataclysm": form.cataclysm.data,
              "hofsize": form.hofsize.data,
              "noartrest": not form.artrest.data,
              "research": form.research.data,
              "norandres": not form.randres.data,
              "nostoryevents": True if form.storyevents.data == "none" else False,
              "storyevents": True if form.storyevents.data == "some" else False,
              "allstoryevents": True if form.storyevents.data == "all" else False,
              "scoregraphs": True if form.scoregraphs.data == "always visible" else False,
              "nonationinfo": True if form.scoregraphs.data == "never visible" else False,
              "teamgame": form.teamgame.data,
              "nocheatdet": not form.cheatdet.data,
              "renaming": form.renaming.data
    }
    notifiers = ([DiscordNotifier(webhook_url = form.notifier.data)]
                if form.notifier.data else None
    )
    name = form.name.data.replace(" ", "_")
    if host.find_game_by_name(name):
      await flash("A game by that name already exists.")
      return redirect(url_for("index")) 
    new_game = host.create_new_game(name, notifiers, **config)
    asyncio.create_task(new_game.run_until_cancelled())
    port = config["port"]
    address = f"{SERVER_ADDRESS}:{port}"
    await flash(
      "The Wheel has turned once again. " 
      f"Your game will be available at {address} soon."
    )
    return redirect(url_for("index"))

  return await render_template("new_game.html", form = form)

@app.route("/games/<name>")
async def game_status(name):
  host = app.config.get('host_instance')
  game_instance = host.find_game_by_name(name)
  if not game_instance: abort(404)

  players = []
  for player in game_instance.players:
    if player["eliminated"]: turn = "eliminated"
    else:
      turn = "unknown"
      connected = False
      for _tuple in game_instance.who_played:
        if _tuple[0] == player["shortname"]:
          turn = _tuple[1]
          connected = _tuple[2]
    players.append((player["name"], turn, connected))

  time = Dom5Time(game_instance.turn)

  return await render_template(
    "game.html", 
    game = game_instance, 
    players = players, 
    time = time
  )

@app.route("/games/<name>/<passcode>")
async def game_admin(name, passcode):
  host = app.config.get("host_instance")
  game_instance = host.find_game_by_name(name)
  if not game_instance: abort(404)
  if passcode == passcode(game_instance):
    return "Access granted!!"
  else:
    abort(404)

@app.route("/maps")
async def map_directory():
  host = app.config.get("host_instance")
  maps = host.maps
  return await render_template("maps.html", maps = maps)

@app.route("/mods")
async def mod_directory():
  host = app.config.get("host_instance")
  mods = host.mods
  return await render_template("mods.html", mods = mods)

@app.route("/thumb/<filename>")
async def get_map_thumb(filename):
  path = safe_join(MAP_THUMBNAIL_DIR, filename)
  return await send_file(path)

@app.route("/icon/<filename>")
async def get_mod_icon(filename):
  path = safe_join(MOD_ICON_DIR, filename)
  return await send_file(path)

@app.route("/rand")
async def random_nation():
  host = app.config.get("host_instance")
  era = int(request.args.get("era", default = 1))
  if not era or era not in (1, 2, 3): era = 1
  era_name = ("early", "middle", "late")[era-1]
  nation = random.choice(list(host.nations[era].values()))
  return await render_template(
    "random_nation.html", 
    nation = nation, 
    era_name = era_name
  )

@app.before_serving
async def startup():
  host = Host(HOST_ROOT_PATH, port_range = HOST_PORT_RANGE)
  host.restore_games()
  asyncio.create_task(host.startup())
  map_choices = app.config.get("map_choices")
  map_choices.extend([(_map.filename, _map.title) for _map in host.maps])
  mod_choices = app.config.get("mod_choices")
  mod_choices.extend([(mod.filename, mod.title) for mod in host.mods])
  app.config.update(host_instance = host, map_choices = map_choices)

@app.after_serving
async def shutdown():
  app.config["host_instance"].shutdown()

class NewGameForm(FlaskForm):
  map_choices = app.config.get("map_choices")
  mod_choices = app.config.get("mod_choices")

  int_style = dict(style = "width: 12%", maxlength = 3)
  select_style = dict(style = "width: 40%")
  string_style = dict(style = "width: 50%", maxlength = 24)

  # ??? why did i write this ???
  within_range = lambda lower, upper: NumberRange(
    min = lower, max = upper
    )

  # Game Settings
  name = StringField("Enter a game name:", 
    render_kw = string_style, validators = [DataRequired()]
  ) 
  era = SelectField("Choose an era:", coerce = int, render_kw = select_style, 
    choices = [
    ("1", "early"), 
    ("2", "middle"), 
    ("3", "late")
    ]
  )
  clustered = None
  closed = None
  
  mapfile = SelectField("Choose a map:", 
    render_kw = select_style, choices = map_choices
    )

  mods = SelectMultipleField("Choose mods to enable:", 
    render_kw = select_style, choices = mod_choices
    )

  randmap = IntegerField(
    "Provinces per player (only applicable to random maps):", 
    default = 15, render_kw = int_style
    )

  # World Settings
  magicsites = IntegerField("Magic site frequency: (0-75)", 
    default = 50, render_kw = int_style, validators = [within_range(0, 75)]
    )
  indepstr = IntegerField("Strength of independents: (0-9)", 
    default = 5, render_kw = int_style, validators = [within_range(0, 9)]
    )
  richness = IntegerField("Province income multiplier: (50-300)", 
    default = 100, render_kw = int_style, validators = [within_range(50, 300)]
    )
  resources = IntegerField("Resource multiplier: (50-300)", 
    default = 100, render_kw = int_style, validators = [within_range(50, 300)]
    )
  recruitment = IntegerField("Recruitment points multiplier: (50-300)", 
    default = 100, render_kw = int_style, validators = [within_range(50, 300)]
    )
  supplies = IntegerField("Supplies multiplier: (50-300)", 
    default = 100, render_kw = int_style, validators = [within_range(50, 300)]
    )
  startprov = IntegerField("Starting provinces per player: (1-9)", 
    default = 1, render_kw = int_style, validators = [within_range(1, 9)]
    )

  # Divine Rules
  eventrarity = SelectField("Event rarity:", render_kw = select_style, 
    choices = [(1, "common"), (2, "rare")]
    )
  global_enchants = IntegerField("Global enchantment slots: (3-9)", 
    default = 5, render_kw = int_style, validators = [within_range(3, 9)]
    )
  lvl1_thrones = IntegerField("Number of level 1 thrones: (0-20)", 
    default = 3, render_kw = int_style, validators = [within_range(0, 20)]
    )
  lvl2_thrones = IntegerField("Number of level 2 thrones: (0-15)", 
    default = 0, render_kw = int_style, validators = [within_range(0, 15)]
    )
  lvl3_thrones = IntegerField("Number of level 3 thrones: (0-10)", 
    default = 0, render_kw = int_style, validators = [within_range(0, 10)]
    )
  requiredap = IntegerField(
    "Ascension points to win:", default = 3, render_kw = int_style
    )
  cataclysm = IntegerField("Turns until Cataclysm: (0-999) (0 disables)", 
    default = 0, render_kw = int_style, validators = [within_range(0, 999)]
    )
  hofsize = IntegerField("Hall of fame size: (5-15)", 
    default = 10, render_kw = int_style, validators = [within_range(5, 15)]
    )
  research = IntegerField("Research difficulty: (0-4)", 
    default = 2, render_kw = int_style, validators = [within_range(0, 4)]
    )
  storyevents = SelectField("Story events:", render_kw = select_style, 
    choices = ["all", "some", "none"]
    )
  scoregraphs = SelectField("Score graphs:", render_kw = select_style, 
    default = "default behavior",
    choices = ["always visible", "default behavior", "never visible"]
    )
  teamgame = BooleanField("Disciple game")
  artrest = BooleanField("Restrict players to one artifact per turn", 
    default = True
    )
  randres = BooleanField("Enable random starting research", 
    default = True
    )
  cheatdet = BooleanField("Enable cheat detection", default=True)
  renaming = BooleanField("Enable renaming commanders")
  masterpass = None

  # Notifications
  notifier = StringField(
    "To enable Discord turn notifications, enter a Discord webhook URL:", 
    render_kw = dict(maxlength = 200)
  )

  submit = SubmitField("Submit new game")

class Dom5Time:

  def __init__(self, turn):
    self.season = (("early", "", "late")[turn % 3], ("spring", "summer", "fall", "winter")[(turn // 3) % 4])
    self.year = turn // 12
    self.month = (turn + 1) % 12
    self.turn = turn

  def __str__(self):
    return f"{self.season[0]} {self.season[1]} in the year {self.year} of the Ascension Wars (turn {self.turn})"


def passcode(game):
  return shake_128((SECRET_KEY + game.name).encode("utf8")).hexdigest(8)



