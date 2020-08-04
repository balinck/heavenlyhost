import json
import requests

class Notifier:

  _notifiers = {}

  @classmethod
  def register_notifier(cls, notifier_cls):
    cls._notifiers[notifier_cls.name] = notifier_cls
    return notifier_cls

  @classmethod
  def _from_dict(cls, dict_):
    for name, notifier in cls._notifiers.items():
      if name == dict_["type"]: return notifier(**dict_["attrs"])

@Notifier.register_notifier
class DiscordNotifier(Notifier):
  name = "discord"

  def __init__(self, **kwargs):
    self.webhook_url = kwargs['webhook_url']

  def _as_dict(self):
    return {"type": "discord", "attrs": {"webhook_url": self.webhook_url}}

  def notify(self, msg):
    data = {"content": msg}
    requests.post(self.webhook_url, 
                  data=json.dumps(data), 
                  headers={"Content-Type": "application/json"}
    )