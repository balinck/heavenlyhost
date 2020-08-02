import quart.flask_patch
from flask_wtf import FlaskForm
from wtforms import StringField, SubmitField, TextAreaField, HiddenField
from wtforms.validators import DataRequired
from quart import Quart, render_template, send_file, url_for
import asyncio
from heavenly import Server
from pathlib import Path

app = Quart(__name__)

@app.route("/")
async def home():
  heavenly = app.config.get('heavenly_server')
  return await render_template("home.html", games=heavenly.games)

@app.route("/games/<name>")
async def game_status(name):
  heavenly = app.config.get('heavenly_server')
  game_instance = list(filter(lambda g: g.name == name, heavenly.games))[0]
  if not game_instance: 
    return "No such game."
  file_path = game_instance.path / "status.html"
  if file_path.exists():
    return await send_file(game_instance.path / "status.html")
  else: return "No status page found." 

@app.before_serving
async def startup():
  heavenly_server = Server()
  heavenly_server._load_json_data()
  heavenly_server.startup()
  app.config.update({"heavenly_server": heavenly_server})
  loop = asyncio.get_event_loop()
  loop.create_task(heavenly_server.listen_pipe())

@app.after_serving
async def shutdown():
  app.config['heavenly_server'].shutdown()
