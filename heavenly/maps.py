from PIL import Image
from pathlib import Path
from copy import copy

from .config.maps import MAP_THUMBNAIL_DIR

class Dom5Map:

	def __init__(self, path_to_map):
		self.filename = path_to_map.name
		self.provinces = 0
		self.underwater = 0
		self.wraparound = "No"

		with open(path_to_map, "r") as file:
			line = file.readline()
			eof = not line
			while not eof:
				if line.startswith("--"): pass
				elif line.startswith("#"):
					command, *args = line.split()

					if command == "#dom2title": self.title = " ".join(args)
					elif command == "#imagefile": self.tga = " ".join(args)
					elif command == "#winterimagefile": self.winter_tga = " ".join(args)
					elif command == "#wraparound": self.wraparound = "Full"
					elif command == "#hwraparound": self.wraparound = "Horizontal"
					elif command == "#vwraparound": self.wraparound = "Vertical"

					elif command == "#description":
						self.description = [" ".join(args)]
						while not line.endswith("\"\n"):
							line = file.readline()
							self.description.append(line)
						self.description = "<br>".join(self.description)
						self.description = self.description.replace("\n", "")
						self.description = self.description.replace("\"", "")

					elif command == "#terrain":
						self.provinces += 1
						terrain_mask = format(int(args[1]) + (2**31), "b")
						if any(int(terrain_mask[n]) for n in (-3, -12)): self.underwater += 1

				line = file.readline()
				eof = not bool(line)

		with Image.open(path_to_map.parent / self.tga) as im:
			self.thumbnail = (self.title + ".thumbnail").replace(" ", "")
			im.thumbnail((256, 256))
			im = im.convert("RGB")
			im.save(MAP_THUMBNAIL_DIR / self.thumbnail, "JPEG")

