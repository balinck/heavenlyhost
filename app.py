import quart.flask_patch
from flask_wtf import FlaskForm
from wtforms import StringField, SubmitField, TextAreaField, HiddenField
from wtforms.validators import DataRequired
from quart import Quart, render_template, send_file, url_for
import asyncio
from pathlib import Path
import os

from heavenly.host import Host

app = Quart(__name__)

@app.route("/")
async def home():
  host = app.config.get('heavenly_host')
  return await render_template("home.html", games = host.status)

@app.route("/games/<name>")
async def game_status(name):
  host = app.config.get('heavenly_host')
  game_instance = host.find_game_by_name(name)
  if not game_instance: 
    return "No such game."
  file_path = game_instance.path / "status.html"
  if file_path.exists():
    return await send_file(game_instance.path / "status.html")
  else: return "No status page found." 

@app.before_serving
async def startup():
  host = Host(Path("") / "data")
  host.restore_games()
  host.startup()
  app.config.update({"heavenly_host": host})
  host.init_pipe()
  loop = asyncio.get_event_loop()
  loop.create_task(host.listen_pipe())
  loop.create_task(host.ping())

@app.after_serving
async def shutdown():
  app.config['heavenly_host'].shutdown()
