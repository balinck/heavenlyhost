import quart.flask_patch
from flask_wtf import FlaskForm
from flask_bootstrap import Bootstrap
from wtforms import StringField, SubmitField, TextAreaField, HiddenField, IntegerField, SelectField, BooleanField
from wtforms.validators import DataRequired, NumberRange
from quart import Quart, render_template, send_file, safe_join, url_for, redirect, flash, request
import asyncio
from pathlib import Path
import os

from heavenly.host import Host
from heavenly.notify import DiscordNotifier
from heavenly.config.app import APP_NAME, SERVER_ADDRESS, MOTD, HOST_ROOT_PATH

bootstrap = Bootstrap()
app = Quart(__name__)
bootstrap.init_app(app)
map_choices = []
app.config.update({
  "APP_NAME": APP_NAME,
  "SERVER_ADDRESS": SERVER_ADDRESS,
  "MOTD": MOTD,
  "map_choices": [("random", "Random")], 
  "SECRET_KEY": "development key"
})

app.jinja_env.globals.update(app.config)

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
    thrones = [form.lvl1_thrones.data, form.lvl2_thrones.data, form.lvl3_thrones.data]

    config = {"era": form.era.data, 
              "port": host.get_free_port(), 
              "mapfile": mapfile,
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
              "nocheatdet": not form.cheatdet.data,
              "renaming": form.renaming.data
    }
    notifiers = ([DiscordNotifier(webhook_url = form.notifier.data)]
                if form.notifier.data else None
    )
    name = form.name.data.replace(" ", "_")
    new_game = host.create_new_game(name, notifiers, **config)
    asyncio.create_task(new_game.run_until_cancelled())
    address = address_str(config["port"])
    await flash("The Wheel has turned once again. " 
        + "Your game will be available at {} soon.".format(address))
    return redirect(url_for("index"))

  return await render_template("new_game.html", form = form)

@app.route("/games/<name>")
async def game_status(name):
  host = app.config.get('host_instance')
  game_instance = host.find_game_by_name(name)
  nations = host.nations
  era = game_instance.settings["era"]
  players = []
  for key, value in game_instance.players.items():
    name = nations[era][value]
    if key in game_instance.eliminated: turn = "eliminated"
    else:
      turn = "unknown"
      connected = False
      for _tuple in game_instance.who_played:
        if _tuple[0] == key:
          turn = _tuple[1]
          connected = _tuple[2]
    players.append((name, turn, connected))
  if not game_instance: 
    return "No such game."
  else:
    return await render_template("game.html", game = game_instance, nations = nations, players = players)

@app.route("/maps")
async def map_directory():
  host = app.config.get("host_instance")
  maps = host.maps
  return await render_template("maps.html", maps = maps)

@app.route("/thumb/<filename>")
async def get_map_thumb(filename):
  host = app.config.get("host_instance")
  path = safe_join(host.map_path, filename)
  return await send_file(path)

@app.before_serving
async def startup():
  host = Host(HOST_ROOT_PATH)
  host.restore_games()
  asyncio.create_task(host.get_nation_dict())
  for game in host.games:
    asyncio.create_task(game.run_until_cancelled())
  map_choices = app.config.get("map_choices")
  map_choices.extend([(_map.filename, _map.title) for _map in host.maps])
  print("MAPCHOICES:", map_choices)
  print(id(map_choices))
  app.config.update(host_instance = host, map_choices = map_choices)

@app.after_serving
async def shutdown():
  app.config["host_instance"].shutdown()

class NewGameForm(FlaskForm):
  map_choices = app.config.get("map_choices")

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
  teamgame = None
  clustered = None
  closed = None
  
  mapfile = SelectField("Choose a map:", 
    render_kw = select_style, choices = map_choices
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

