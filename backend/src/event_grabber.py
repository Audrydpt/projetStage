import logging
import os
import threading
import time

import requests
from acic.metadata import AcMetadataEventReceiverThread

from database import AcicEvent
from database import GenericDAL, AcicUnattendedItem, AcicCounting, AcicNumbering, AcicOccupancy, AcicAllInOneEvent, \
    AcicLicensePlate, AcicOCR

def safe_int(val):
    try:
        return int(val)
    except:
        return None

def safe_float(val):
    try:
        return float(val)
    except:
        return None

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
file_handler = logging.FileHandler(f"/tmp/{__name__}.log")
file_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
logger.addHandler(file_handler)
logger.addHandler(logging.StreamHandler())

class EventGrabber(threading.Thread):
    def __init__(self):
        super().__init__()
        self.grabbers = []
        self.running = False
        self.daemon = True

        self.keep_alive = 60
        self.last_keep_alive = time.time()

    def __del__(self):
        self.stop()

    def add_grabber(self, host, port):
        logger.info(f"Adding grabber {host}:{port}")
        grabber = AcMetadataEventReceiverThread(acichost=host, acichostport=port) # , logger=logger)
        self.grabbers.append(grabber)
        return grabber

    def get_grabbers(self):
        for grabber in self.grabbers:
            yield grabber

    def start(self):
        if not self.running:
            for grabber in self.grabbers:
                grabber.start()

            self.running = True
            super().start()

    def stop(self):
        if self.running:
            self.running = False
            for grabber in self.grabbers:
                grabber.stop()

    def run(self):
        logger.info("EventGrabber started.")

        while self.running:

            keep_alive_check = False
            if time.time() - self.last_keep_alive > self.keep_alive:
                keep_alive_check = True
                self.last_keep_alive = time.time()

            for grabber in self.grabbers:

                if keep_alive_check:
                    if grabber.is_alive() is False or grabber.is_reachable(timeout=1.0) is False:
                        logger.warning(
                            f"Grabber {grabber.acichost}:{grabber.acichostport} is not alive or not reachable. Restarting...")

                        grabber.stop()
                        self.grabbers.remove(grabber)
                        grabber = self.add_grabber(grabber.acichost, grabber.acichostport)
                        grabber.start()
                        continue
                

                q = grabber.get_queue_copy()

                if q.qsize() > 80:
                    logger.warning(f"Grabber {grabber.acichost}:{grabber.acichostport} event queue is full!")

                while q.empty() is False:
                    event = q.get()
                    if self.parse_event(grabber.acichost, event) is False:
                        logger.warning(f"Could not parse event: {event} from {grabber.acichost}:{grabber.acichostport}")

            time.sleep(0.01)

        logger.info("EventGrabber stopped.")

    def __download_thumbnail(self, url, subdir="temp"):
        

        output_dir = os.path.join("assets", "images", subdir)
        if os.path.exists("/backend/assets"):
            output_dir = os.path.join("/backend", "assets", "images", subdir)

        output = os.path.join(output_dir, url.split("/")[-1])

        try:
            os.makedirs(output_dir, exist_ok=True)
            
            link = url
            if os.getenv('ACIC_HTTP_PORT') is not None:
                link = url.replace("/stream", ":" + os.getenv('ACIC_HTTP_PORT') + "/stream")

            # logger.debug("Trying to download " + link + " to " + output)
            #with open(output, "wb") as f:
            #    f.write(requests.get(link).content)

        except Exception as e:
            logger.warning("Error while downloading image:", e)

    def parse_event(self, host, event):
        try:
            event_name = event.event_name
            stream_id = safe_int(event.event_stream)
            timestamp = event.event_time_datetime
            state = event.event_state

            # TODO: Some event might fire for multiple roiIndex or multiple lineIndex. How to handle it ?
            line_index = event.event_kvdata['lineIndex'] if 'lineIndex' in event.event_kvdata else None
            roi_index = event.event_kvdata['roiIndex'] if 'roiIndex' in event.event_kvdata else None
            class_name = event.event_kvdata['class'] if 'class' in event.event_kvdata else None
            rule_type = event.event_kvdata['ruleType'] if 'ruleType' in event.event_kvdata else None
            public_name = event.event_kvdata['externalEventName'] if 'externalEventName' in event.event_kvdata else None

            # Ignore those events for now
            if event_name in ["MvTestIO"]:
                return True

            dal = GenericDAL()

            if state == "UPDATE":
                return True

            # Handle Open/Atomic/Close event
            if event_name in ["AcicOccupancyWarning", "AcicOccupancyCritical", "AcicCriticalOccupancy", "AcicWarningOccupancy", "AcicDensityIncrease", "AcicDensityDecrease"]:
                dal.add(AcicEvent(
                    host=host,
                    stream_id=stream_id,
                    timestamp=timestamp,
                    name=event_name,
                    state=state
                ))
                return True

            # Ignore those events for now
            if state == "CLOSE":
                return True

            # Handle Open/Atomic events

            if event_name == "AcicNumbering" or (event_name == "AcicCounting" and rule_type is None):
                count = None
                if "count" in event.event_kvdata:
                    count = safe_int(event.event_kvdata["count"])
                elif "numbering" in event.event_kvdata:
                    count = safe_int(event.event_kvdata["numbering"])
                else:
                    raise Exception("Missing count or numbering in event.")

                # TODO: Add all subcount for this
                dal.add(AcicNumbering(
                    host=host,
                    stream_id=stream_id,
                    timestamp=timestamp,
                    roi_index=safe_int(roi_index),
                    count=count
                ))
                return True

            if event_name == "AcicOccupancy":
                value = safe_float(event.event_kvdata["value"])  # guess it's a percentage
                count = safe_float(event.event_kvdata["count"])  # guess it's the number of people
                dal.add(AcicOccupancy(
                    host=host,
                    stream_id=stream_id,
                    timestamp=timestamp,
                    count=count,
                    value=value
                ))
                return True

            if event_name == "AcicUnattendedItem":
                url = event.event_kvdata["url"]
                url_guid = url.split("/")[-1].split(".")[0]
                threading.Thread(target=self.__download_thumbnail, args=(url, "detections")).start()

                person_url = event.event_kvdata["personUrl"] if "personUrl" in event.event_kvdata else None
                person_guid = None
                if person_url is not None:
                    person_guid = person_url.split("/")[-1].split(".")[0]
                    threading.Thread(target=self.__download_thumbnail, args=(person_url, "thumbnail")).start()

                dal.add(AcicUnattendedItem(
                    host=host,
                    stream_id=stream_id,
                    timestamp=timestamp,
                    roi_index=safe_int(roi_index),
                    url=url_guid,
                    person_url=person_guid
                ))
                return True

            if (event_name == "AcicCountingPositive" or event_name == "AcicCountingNegative" or
                    (event_name == "AcicCounting" and rule_type == "Line counting") or
                    (event_name == "AcicCounting" and rule_type == "Multiple lines counting")):

                if class_name is None or line_index is None:
                    raise Exception("Missing class_name or line_index in event.")

                direction = None
                if event.event_name == "AcicCountingPositive":
                    direction = "positive"
                elif event.event_name == "AcicCountingNegative":
                    direction = "negative"
                elif event.event_name == "AcicCounting":
                    if "way" in event.event_kvdata:
                        direction = event.event_kvdata["way"]
                    elif "direction" in event.event_kvdata:
                        direction = event.event_kvdata["direction"]

                if direction is None:
                    raise Exception("Missing direction in event.")

                direction = direction.lower()
                dal.add(AcicCounting(
                    host=host,
                    stream_id=stream_id,
                    timestamp=timestamp,
                    line_index=line_index,
                    direction=direction,
                    class_name=class_name
                ))

                return True

            if event_name == "AcicLPR" or event_name == "AcicLicensePlate":
                plate = event.event_kvdata["plate_utf8"] if "plate_utf8" in event.event_kvdata else event.event_kvdata[
                    "plate"]

                country = event.event_kvdata["country"]
                text_color = event.event_kvdata["textColor"]
                background_clor = event.event_kvdata["backgroundColor"]
                foreground_color = event.event_kvdata["foregroundColor"]
                vehicle_brand = event.event_kvdata["vehicleBrand"] if "vehicleBrand" in event.event_kvdata else None
                vehicle_type = event.event_kvdata["vehicleType"] if "vehicleType" in event.event_kvdata else None
                vehicle_color = event.event_kvdata["vehicleColor"] if "vehicleColor" in event.event_kvdata else None

                url_guid, url_thumbnail_guid, url_plate_guid = None, None, None
                if "url" in event.event_kvdata:
                    url = event.event_kvdata["url"]
                    url_guid = url.split("/")[-1].split(".")[0]
                    threading.Thread(target=self.__download_thumbnail, args=(url, "detections")).start()
                if "url_thumbnail" in event.event_kvdata:
                    url_thumbnail = event.event_kvdata["url_thumbnail"]
                    url_thumbnail_guid = url_thumbnail.split("/")[-1].split(".")[0]
                    threading.Thread(target=self.__download_thumbnail, args=(url_thumbnail, "thumbnail")).start()
                if "url_plate" in event.event_kvdata:
                    url_plate = event.event_kvdata["url_plate"]
                    url_plate_guid = url_plate.split("/")[-1].split(".")[0]
                    threading.Thread(target=self.__download_thumbnail, args=(url_plate, "text")).start()

                dal.add(AcicLicensePlate(
                    host=host,
                    stream_id=stream_id,
                    timestamp=timestamp,
                    roi_index=safe_int(roi_index),
                    plate=plate,
                    country=country,
                    text_color=text_color,
                    background_color=background_clor,
                    foreground_color=foreground_color,
                    vehicle_brand=vehicle_brand,
                    vehicle_type=vehicle_type,
                    vehicle_color=vehicle_color,
                    url=url_guid,
                    url_thumbnail=url_thumbnail_guid,
                    url_plate=url_plate_guid
                ))
                return True

            if event_name == "AcicOCR":
                text = event.event_kvdata["text"]
                type_of = event.event_kvdata["type"]
                checksum = event.event_kvdata["checksum"]

                url_guid, url_thumbnail_guid, url_plate_guid = None, None, None
                if "url" in event.event_kvdata:
                    url = event.event_kvdata["url"]
                    url_guid = url.split("/")[-1].split(".")[0]
                    threading.Thread(target=self.__download_thumbnail, args=(url, "detections")).start()
                if "url_thumbnail" in event.event_kvdata:
                    url_thumbnail = event.event_kvdata["url_thumbnail"]
                    url_thumbnail_guid = url_thumbnail.split("/")[-1].split(".")[0]
                    threading.Thread(target=self.__download_thumbnail, args=(url_thumbnail, "thumbnail")).start()
                if "url_plate" in event.event_kvdata:
                    url_plate = event.event_kvdata["url_plate"]
                    url_plate_guid = url_plate.split("/")[-1].split(".")[0]
                    threading.Thread(target=self.__download_thumbnail, args=(url_plate, "text")).start()

                dal.add(AcicOCR(
                    host=host,
                    stream_id=stream_id,
                    timestamp=timestamp,
                    roi_index=safe_int(roi_index),
                    text=text,
                    type=type_of,
                    checksum=checksum,
                    url=url_guid,
                    url_thumbnail=url_thumbnail_guid,
                    url_plate=url_plate_guid
                ))
                return True

            # Generic AllInOne event:
            if rule_type is not None:
                dal.add(AcicAllInOneEvent(
                    host=host,
                    stream_id=stream_id,
                    timestamp=timestamp,
                    class_name=class_name,
                    event_name=event_name,
                    public_name=public_name,
                    event_type=rule_type,
                    rois=roi_index,
                    lines=line_index
                ))
                return True

            # I couldn't guess what kind of event it is
            dal.add(AcicEvent(
                host=host,
                stream_id=stream_id,
                timestamp=timestamp,
                name=event_name,
                state=state
            ))
            return True
            #return False

        except Exception as e:
            logger.error(f"Error parsing event: {event}:")
            logger.error(e)

        return False
