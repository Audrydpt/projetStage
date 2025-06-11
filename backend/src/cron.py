import threading
import uuid

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

class Singleton(type):
  _instances = {}
  _lock = threading.Lock()

  def __call__(cls, *args, **kwargs):
    if cls not in cls._instances:
      with cls._lock:
        if cls not in cls._instances:
          cls._instances[cls] = super().__call__(*args, **kwargs)
    return cls._instances[cls]

class Cron(metaclass=Singleton):
  def __init__(self):
      self.__scheduler = BackgroundScheduler()
      self.__scheduler.start()
      self.__jobs = {}
  
  def add_job(self, func, cron_string):
    job_id = str(uuid.uuid4())

    job = self.__scheduler.add_job(
      func,
      CronTrigger.from_crontab(cron_string),
      id=job_id
    )
    self.__jobs[job_id] = job

    return job_id
  
  def remove_job(self, job_id):
    self.__scheduler.remove_job(job_id)
    self.__jobs.pop(job_id)

