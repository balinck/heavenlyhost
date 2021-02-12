from PIL import Image
from pathlib import Path

class Dom5Mod:

  def __init__(self, path_to_mod):
    self.filename = path_to_mod.name
    current_nation_id = None
    modded_nations = {}

    with open(path_to_mod, "r") as file:
      line = file.readline()
      eof = not line
      while not eof:
        if line.startswith("--"): pass
        elif line.startswith("#"):
          command, *args = line.split()

          if command == "#modname": self.title = " ".join(args).replace("\"", "")
          elif command == "#icon": self.icon = " ".join(args).replace("\"", "")
          elif command == "#version": self.version = " ".join(args)
          elif command == "#domversion": self.domversion = " ".join(args)
          elif command == "#description":
            self.description = [" ".join(args)]
            while not line.endswith("\"\n"):
              line = file.readline()
              self.description.append(line)
            self.description = "<br>".join(self.description)
            self.description = self.description.replace("\n", "")
            self.description = self.description.replace("\"", "")

          elif command.startswith("#new"): current_nation_id = None

          elif command == "#selectnation":
            current_nation_id = int(args[0]) 
            modded_nations[current_nation_id] = {}
          elif command == "#name" and current_nation_id:
            modded_nations[current_nation_id]["name"] = " ".join(args).replace("\"", "")
          elif command == "#epithet" and current_nation_id: 
            modded_nations[current_nation_id]["epithet"] = " ".join(args).replace("\"", "")

        line = file.readline()
        eof = not bool(line)
    self.nations = {}
    for nid, nation in modded_nations.items():
      if nation.get("name"):
        name = nation["name"]
        if nation.get("epithet"):
          epithet = nation.get("epithet")
          name = f"{name}, {epithet}"
        self.nations[nid] = name

    with Image.open(path_to_mod.parent / self.icon) as im:
      self.thumbnail = self.filename + ".thumbnail"
      im.thumbnail((256, 256))
      im = im.convert("RGB")
      im.save(path_to_mod.parent / self.thumbnail, "JPEG")
