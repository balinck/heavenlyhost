from quart import Quart
import asyncio
from heavenly import Server

app = Quart(__name__)

async def test_background_job():
  while True:
    print("Five seconds have passed")
    await asyncio.sleep(5)

@app.route("/")
async def index():
  return "Hello"

@app.before_serving
async def startup():
  print("Starting up.")
  heavenly_server = Server()
  heavenly_server._load_json_data()
  heavenly_server.startup()
  app.config.update({"heavenly_server": heavenly_server})
  loop = asyncio.get_event_loop()
  loop.create_task(heavenly_server.listen_pipe())


@app.after_serving
async def shutdown():
  app.config['heavenly_server'].shutdown()
  print("Shutting down.")